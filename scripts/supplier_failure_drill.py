#!/usr/bin/env python
"""真实供应商故障演练（T39 / #39，受控、不触碰线上部署）。

流程（全部在本机回环，使用一次性 ``bank_assistant_test`` 库）：

1. 在进程内启动 :mod:`scripts.fake_supplier`（OpenAI 兼容假供应商）；
2. 启动一个**临时** API 实例（``127.0.0.1:<port>``），``DEEPSEEK_BASE_URL``
   指向假供应商，``LLM_TIMEOUT_SECONDS=2``、``LLM_MAX_RETRIES=0``；
3. 依次制造四类上游故障，并对每类同时验证两条链路：

   - 同步候选答复 ``POST /api/v1/suggestions``：状态码/稳定错误码/retryable/
     trace_id 必须匹配契约（429/504/503/502），且 DB 中建议为 ``failed``、
     answer 为空、citations 为空；
   - 异步质检 worker：任务必须终态 ``failed``、``findings=0``，不得出现
     “零分报告”或假成功；

4. 连接被拒通过**停止假供应商**制造，不伪造异常；演练结束停止临时 API，
   线上容器（``bank-assistant-api``）与 demo/dev 库均未触碰。

预期映射（契约 §1/§3，``api/errors.py::_MODEL_FAILURE_SPECS``）::

    上游 429 限流      → API 429 MODEL_RATE_LIMITED
    上游连接/读超时    → API 504 MODEL_TIMEOUT
    上游连接被拒       → API 503 MODEL_UNAVAILABLE
    上游非法 JSON      → API 502 MODEL_INVALID_STRUCTURE

运行（先建立 db-tunnel，并把 DATABASE_URL 指向 ``bank_assistant_test``）::

    .local-data/t39-artifacts/run/env.sh
    .venv/bin/python scripts/supplier_failure_drill.py \
        --port 18081 --output .local-data/t39-artifacts/supplier-failure-drill.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import create_engine, text

try:  # pytest 以仓库根为 sys.path；直接运行脚本时 sys.path[0] 是 scripts/
    from scripts.fake_supplier import FakeSupplierServer
except ModuleNotFoundError:  # pragma: no cover - 直接运行路径
    from fake_supplier import FakeSupplierServer  # type: ignore[no-redef]

CASES: tuple[dict[str, Any], ...] = (
    {
        "name": "rate_limit",
        "fake_mode": "rate_limit",
        "expected_status": 429,
        "expected_code": "MODEL_RATE_LIMITED",
        "upstream": "HTTP 429（RateLimitError）",
    },
    {
        "name": "timeout",
        "fake_mode": "timeout",
        "expected_status": 504,
        "expected_code": "MODEL_TIMEOUT",
        "upstream": "读超时（APITimeoutError，客户端 2s）",
    },
    {
        "name": "invalid_json",
        "fake_mode": "invalid_json",
        "expected_status": 502,
        "expected_code": "MODEL_INVALID_STRUCTURE",
        "upstream": "HTTP 200 + 非 JSON content",
    },
    {
        "name": "connection_refused",
        "fake_mode": None,
        "expected_status": 503,
        "expected_code": "MODEL_UNAVAILABLE",
        "upstream": "连接被拒（APIConnectionError，假供应商已停止）",
    },
)

SUGGESTION_QUERY = "银行卡挂失后应该走哪些渠道？请只依据有效制度说明。"


# --------------------------------------------------------------------- 纯逻辑


def verify_failure_response(
    status_code: int,
    body: dict[str, Any],
    *,
    expected_status: int,
    expected_code: str,
    expected_trace_id: str | None = None,
) -> list[str]:
    """返回契约不满足项；空列表表示通过。绝不把 2xx 当失败演练成功。"""
    problems: list[str] = []
    if status_code != expected_status:
        problems.append(f"status={status_code}，期望 {expected_status}")
    if status_code < 400:
        problems.append("失败演练返回了 2xx，可能伪造了成功")
    if body.get("code") != expected_code:
        problems.append(f"code={body.get('code')!r}，期望 {expected_code!r}")
    if body.get("retryable") is not True:
        problems.append(f"retryable={body.get('retryable')!r}，期望 true")
    if not body.get("trace_id"):
        problems.append("缺少 trace_id")
    if expected_trace_id and body.get("trace_id") != expected_trace_id:
        problems.append(
            f"trace_id={body.get('trace_id')!r}，期望回显 {expected_trace_id!r}"
        )
    if "answer" in body:
        problems.append("错误响应包含 answer 字段")
    return problems


def has_fabricated_answer(suggestion: dict[str, Any]) -> bool:
    """失败建议不得带答复或引用；空字符串/空列表是明确失败，不是假成功。"""
    return bool(str(suggestion.get("answer") or "").strip()) or bool(
        suggestion.get("citations") or []
    )


# --------------------------------------------------------------------- 运行时


def _wait_ready(client: httpx.Client, *, timeout_seconds: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            response = client.get("/health/ready")
            if response.status_code == 200:
                return
            last_error = f"status={response.status_code}"
        except httpx.HTTPError as exc:
            last_error = type(exc).__name__
        time.sleep(0.5)
    raise RuntimeError(f"临时 API 未就绪：{last_error}")


def _start_api(
    *,
    port: int,
    fake_base_url: str,
    log_path: Path,
    env_overrides: dict[str, str],
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env.update(env_overrides)
    env.update(
        {
            "DEEPSEEK_BASE_URL": fake_base_url,
            "MODEL_MODE": "live",
            "LLM_TIMEOUT_SECONDS": "2",
            "LLM_MAX_RETRIES": "0",
            "APP_ENV": "testing",
            "RATE_LIMIT_ENABLED": "true",
            "RATE_LIMIT_EXPENSIVE_PER_MINUTE": "600",
            "RATE_LIMIT_EXPENSIVE_BURST": "200",
            "RATE_LIMIT_LOGIN_PER_MINUTE": "600",
            "RATE_LIMIT_LOGIN_BURST": "60",
        }
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("w", encoding="utf-8")
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "bank_assistant.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _login(client: httpx.Client) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "demo-author",
            "password": "demo-only-correct-horse-battery",
        },
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _run_worker(env_overrides: dict[str, str], *, timeout_seconds: float = 120.0) -> str:
    env = os.environ.copy()
    env.update(env_overrides)
    completed = subprocess.run(
        [sys.executable, "-m", "bank_assistant.worker", "--once"],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def _suggestion_row(database_url: str, suggestion_id: str) -> dict[str, Any] | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, answer, citations, mode, "
                    "retrieval_snapshot->>'model_status' AS model_status "
                    "FROM suggestions WHERE suggestion_id = CAST(:id AS uuid)"
                ),
                {"id": suggestion_id},
            ).mappings().first()
            return dict(row) if row is not None else None
    finally:
        engine.dispose()


def _inspection_row(database_url: str, inspection_id: str) -> dict[str, Any] | None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT status, error_code, diagnostics, "
                    "(SELECT count(*) FROM findings f "
                    " WHERE f.inspection_id = r.inspection_id) AS findings "
                    "FROM inspection_runs r "
                    "WHERE inspection_id = CAST(:id AS uuid)"
                ),
                {"id": inspection_id},
            ).mappings().first()
            return dict(row) if row is not None else None
    finally:
        engine.dispose()


def run(args: argparse.Namespace) -> dict[str, Any]:
    fake = FakeSupplierServer(host="127.0.0.1", port=args.fake_port)
    fake.start()
    env_overrides = {
        "DATABASE_URL": args.database_url,
        "INDEX_DIR": args.index_dir,
        "DEEPSEEK_API_KEY": os.environ.get("DEEPSEEK_API_KEY", ""),
        "DEEPSEEK_MODEL": os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
    }
    log_path = Path(args.output).parent / "drill-api.log"
    api = _start_api(
        port=args.port,
        fake_base_url=fake.base_url,
        log_path=log_path,
        env_overrides=env_overrides,
    )
    base_url = f"http://127.0.0.1:{args.port}"
    results: list[dict[str, Any]] = []
    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            _wait_ready(client)
            token = _login(client)
            conversations = json.loads(
                Path(args.state).read_text(encoding="utf-8")
            )["conversations"]
            conversation = conversations[args.conversation]
            auth = {"Authorization": f"Bearer {token}"}

            for case in CASES:
                trace_id = f"t39-drill-{args.run_tag}-{case['name']}"
                fake_note = case["fake_mode"]
                if fake_note is None:
                    fake.stop()
                else:
                    fake.set_mode(fake_note)
                headers = {**auth, "x-trace-id": trace_id}
                suggestion_response = client.post(
                    "/api/v1/suggestions",
                    json={"query": SUGGESTION_QUERY},
                    headers=headers,
                )
                try:
                    suggestion_body = suggestion_response.json()
                except ValueError:
                    suggestion_body = {"raw": suggestion_response.text}
                problems = verify_failure_response(
                    suggestion_response.status_code,
                    suggestion_body,
                    expected_status=case["expected_status"],
                    expected_code=case["expected_code"],
                    expected_trace_id=trace_id,
                )
                details = suggestion_body.get("details") or {}
                suggestion_id = details.get("suggestion_id")
                suggestion_row = (
                    _suggestion_row(args.database_url, str(suggestion_id))
                    if suggestion_id
                    else None
                )
                if suggestion_row is None:
                    problems.append("DB 中没有对应的 failed 建议行")
                elif suggestion_row["status"] != "failed":
                    problems.append(
                        f"DB 建议 status={suggestion_row['status']!r}，期望 failed"
                    )
                elif has_fabricated_answer(suggestion_row):
                    problems.append("failed 建议行仍带 answer/citations（假成功）")

                inspection_response = client.post(
                    "/api/v1/inspections",
                    json={
                        "conversation_id": conversation["conversation_id"],
                        "business_type": conversation["business_type"],
                    },
                    headers={
                        **auth,
                        "Idempotency-Key": f"t39-drill-{args.run_tag}-{case['name']}",
                    },
                )
                inspection_id = inspection_response.json().get("inspection_id")
                worker_env = {
                    **env_overrides,
                    "DEEPSEEK_BASE_URL": fake.base_url,
                    "MODEL_MODE": "live",
                    "LLM_TIMEOUT_SECONDS": "2",
                    "LLM_MAX_RETRIES": "0",
                }
                worker_log = _run_worker(worker_env)
                inspection_row = (
                    _inspection_row(args.database_url, str(inspection_id))
                    if inspection_id
                    else None
                )
                if inspection_row is None:
                    problems.append("DB 中没有对应的质检运行")
                else:
                    if inspection_row["status"] != "failed":
                        problems.append(
                            f"质检 status={inspection_row['status']!r}，期望 failed"
                        )
                    if int(inspection_row["findings"] or 0) != 0:
                        problems.append(
                            f"失败质检写入 {inspection_row['findings']} 条 finding（零分报告/假成功）"
                        )

                results.append(
                    {
                        "case": case["name"],
                        "upstream": case["upstream"],
                        "trace_id": trace_id,
                        "expected": {
                            "status": case["expected_status"],
                            "code": case["expected_code"],
                            "retryable": True,
                        },
                        "http_response": {
                            "status": suggestion_response.status_code,
                            "body": suggestion_body,
                        },
                        "suggestion_row": suggestion_row,
                        "inspection": {
                            "inspection_id": inspection_id,
                            "http_status": inspection_response.status_code,
                            "row": _safe_inspection_row(inspection_row),
                            "worker_log_tail": worker_log.splitlines()[-4:],
                        },
                        "problems": problems,
                        "passed": not problems,
                    }
                )
                time.sleep(0.3)
    finally:
        api.terminate()
        try:
            api.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.kill()
            api.wait(timeout=5)
        try:
            fake.stop()
        except Exception:
            pass

    payload = {
        "schema": "t39-supplier-failure-drill-v1",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_tag": args.run_tag,
        "api_base_url": base_url,
        "fake_supplier": {"host": "127.0.0.1", "port": args.fake_port},
        "database": _masked_database(args.database_url),
        "conditions": {
            "llm_timeout_seconds": 2,
            "llm_max_retries": 0,
            "concurrency": 1,
            "deployed_container_touched": False,
        },
        "cases": results,
        "all_passed": all(result["passed"] for result in results),
        "fake_stats": fake.stats(),
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _safe_inspection_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "status": row["status"],
        "error_code": row["error_code"],
        "findings": int(row["findings"] or 0),
        "diagnostics": row["diagnostics"],
    }


def _masked_database(url: str) -> str:
    """只暴露库名，不输出连接串/凭据。"""
    return url.rsplit("/", 1)[-1]


def _print_results(payload: dict[str, Any]) -> None:
    print(f"all_passed={payload['all_passed']}")
    for case in payload["cases"]:
        body = case["http_response"]["body"]
        suggestion = case["suggestion_row"] or {}
        inspection = case["inspection"]["row"] or {}
        print(
            f"{case['case']:20s} HTTP {case['http_response']['status']} "
            f"{body.get('code')} retryable={body.get('retryable')} "
            f"trace={body.get('trace_id')} | suggestion={suggestion.get('status')} "
            f"answer={bool(str(suggestion.get('answer') or '').strip())} | "
            f"inspection={inspection.get('status')}/{inspection.get('error_code')} "
            f"findings={inspection.get('findings')} | passed={case['passed']}"
        )
        for problem in case["problems"]:
            print(f"  PROBLEM: {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T39 供应商故障受控演练")
    parser.add_argument("--port", type=int, default=18081)
    parser.add_argument("--fake-port", type=int, default=19999)
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument(
        "--index-dir", default=os.environ.get("INDEX_DIR", "data/index")
    )
    parser.add_argument(
        "--state", default=".local-data/t39-artifacts/data/demo-seed-state.json"
    )
    parser.add_argument("--conversation", default="s1-lost-card-risk")
    parser.add_argument("--run-tag", default=time.strftime("%H%M%S"))
    parser.add_argument(
        "--output",
        default=".local-data/t39-artifacts/supplier-failure-drill.json",
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        raise SystemExit("需要 --database-url（或 DATABASE_URL 环境变量）")
    if not args.database_url.rsplit("/", 1)[-1].endswith("_test"):
        raise SystemExit("拒绝执行：演练只允许 *_test 库")
    payload = run(args)
    _print_results(payload)
    return 0 if payload["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
