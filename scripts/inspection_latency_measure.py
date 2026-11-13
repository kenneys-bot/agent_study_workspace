#!/usr/bin/env python
"""质检任务端到端延迟实测客户端（T39 / #39，计划 §7）。

对**本地临时 API + 真实 worker + 真实 DeepSeek** 发起 N 次真实质检任务，
记录每次 ``POST /inspections`` 的接收延迟、从 ``created_at`` 到 ``finished_at``
的完成时间、终态与失败原因，并汇总 P50/P95/P99、token 与估算成本。

约束（与 T38 压测同一口径）：

- 只对成功提交（HTTP 202）统计接收延迟；
- 完成时间使用服务端时间戳 ``finished_at - created_at``，避免客户端轮询误差；
- 分位用线性插值（与 numpy 默认 ``linear`` 一致），空样本返回 ``None``；
- token 从 worker 日志的 ``model_call`` 行读取，不改写模型结果；
- 未终态的任务单独计数，不用超时值伪造分位。

用法（本机临时实例，凭据来自仓库外 ``.env``；不打印任何秘密）::

    .local-data/t39-artifacts/run/env.sh
    .venv/bin/python scripts/inspection_latency_measure.py \
        --base-url http://127.0.0.1:18080 \
        --state .local-data/t39-artifacts/data/demo-seed-state.json \
        --count 30 --concurrency 5 \
        --output .local-data/t39-artifacts/inspection-latency.json \
        --worker-log .local-data/t39-artifacts/run/worker-1.log ...
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx
from sqlalchemy import create_engine, text

DEFAULT_USERNAME = "demo-author"
DEFAULT_PASSWORD = "demo-only-correct-horse-battery"
# DeepSeek 官方历史价目快照（2025-02-09 生效，仓库 configs/eval/live-subset.yaml）。
# 2026 年实际价目可能不同；报告必须写 estimated，不能当账单。
DEFAULT_INPUT_CNY_PER_MTOK = 2.0
DEFAULT_OUTPUT_CNY_PER_MTOK = 8.0

_MODEL_CALL_RE = re.compile(
    r"model_call provider=(?P<provider>\S+) model=(?P<model>\S+) "
    r"mode=(?P<mode>\S+) status=(?P<status>\S+) .*?"
    r"prompt_tokens=(?P<prompt>\d+) completion_tokens=(?P<completion>\d+)"
)


# --------------------------------------------------------------------- 纯逻辑


def percentile(values: Sequence[float], p: float) -> float | None:
    """线性插值分位数；空样本返回 ``None``，避免把“没有样本”伪装成 0。"""
    if not 0.0 <= p <= 100.0:
        raise ValueError(f"percentile p 必须在 [0, 100]，收到 {p}")
    if not values:
        return None
    data = sorted(float(v) for v in values)
    if len(data) == 1:
        return data[0]
    position = (len(data) - 1) * (p / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return data[lower]
    fraction = position - lower
    return data[lower] + (data[upper] - data[lower]) * fraction


def estimate_cost_cny(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    input_cny_per_mtok: float = DEFAULT_INPUT_CNY_PER_MTOK,
    output_cny_per_mtok: float = DEFAULT_OUTPUT_CNY_PER_MTOK,
) -> float:
    """按每百万 token 单价估算人民币成本（估算值，不是供应商账单）。"""
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("token 数不能为负")
    return (
        prompt_tokens / 1_000_000 * input_cny_per_mtok
        + completion_tokens / 1_000_000 * output_cny_per_mtok
    )


@dataclass(frozen=True, slots=True)
class ModelCallUsage:
    """从 worker 日志解析出的模型调用聚合。"""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    statuses: dict[str, int] | None = None

    def merged(self, other: "ModelCallUsage") -> "ModelCallUsage":
        statuses = dict(self.statuses or {})
        for name, count in (other.statuses or {}).items():
            statuses[name] = statuses.get(name, 0) + count
        return ModelCallUsage(
            calls=self.calls + other.calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            statuses=statuses,
        )


def parse_model_call_lines(lines: Iterable[str]) -> ModelCallUsage:
    """只统计 ``mode=live`` 的模型调用；回放/空行不计入真实成本。"""
    calls = 0
    prompt = 0
    completion = 0
    statuses: dict[str, int] = {}
    for line in lines:
        match = _MODEL_CALL_RE.search(line)
        if match is None or match.group("mode") != "live":
            continue
        calls += 1
        prompt += int(match.group("prompt"))
        completion += int(match.group("completion"))
        status = match.group("status")
        statuses[status] = statuses.get(status, 0) + 1
    return ModelCallUsage(
        calls=calls,
        prompt_tokens=prompt,
        completion_tokens=completion,
        statuses=statuses,
    )


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values) if values else None,
    }


def summarize(
    records: Sequence[dict[str, Any]],
    usage: ModelCallUsage,
    *,
    input_cny_per_mtok: float = DEFAULT_INPUT_CNY_PER_MTOK,
    output_cny_per_mtok: float = DEFAULT_OUTPUT_CNY_PER_MTOK,
) -> dict[str, Any]:
    """把逐任务记录汇总成报告可直接引用的结构（时间单位：ms / s）。"""
    accepted = [r for r in records if r.get("http_status") == 202]
    post_ms = [float(r["post_ms"]) for r in accepted if r.get("post_ms") is not None]
    completed = [
        r
        for r in records
        if r.get("completion_seconds") is not None
        and r.get("status") in {"succeeded", "failed", "cancelled"}
    ]
    succeeded = [r for r in completed if r.get("status") == "succeeded"]
    failed = [r for r in completed if r.get("status") == "failed"]
    pending = [r for r in records if r.get("status") not in {"succeeded", "failed", "cancelled"}]
    reasons: dict[str, int] = {}
    for record in failed:
        code = record.get("error_code") or "UNKNOWN"
        reasons[code] = reasons.get(code, 0) + 1
    return {
        "n_requested": len(records),
        "n_accepted": len(accepted),
        "n_completed": len(completed),
        "n_succeeded": len(succeeded),
        "n_failed": len(failed),
        "n_pending": len(pending),
        "failure_rate": (len(failed) / len(completed)) if completed else None,
        "failure_reasons": reasons,
        "post_ms": _distribution(post_ms),
        "completion_seconds_all": _distribution(
            [float(r["completion_seconds"]) for r in completed]
        ),
        "completion_seconds_succeeded": _distribution(
            [float(r["completion_seconds"]) for r in succeeded]
        ),
        "completion_seconds_failed": _distribution(
            [float(r["completion_seconds"]) for r in failed]
        ),
        "tokens": {
            "model_calls_live": usage.calls,
            "prompt": usage.prompt_tokens,
            "completion": usage.completion_tokens,
            "statuses": usage.statuses or {},
        },
        "cost_cny_estimate": estimate_cost_cny(
            usage.prompt_tokens,
            usage.completion_tokens,
            input_cny_per_mtok=input_cny_per_mtok,
            output_cny_per_mtok=output_cny_per_mtok,
        ),
        "cost_note": "estimated；价目为 2025-02-09 官方快照，不是供应商账单",
    }


# --------------------------------------------------------------------- 运行时


def _login(client: httpx.Client) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"username": DEFAULT_USERNAME, "password": DEFAULT_PASSWORD},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def _load_conversations(state_path: Path) -> list[dict[str, Any]]:
    state = json.loads(state_path.read_text(encoding="utf-8"))
    conversations = state["conversations"]
    if not conversations:
        raise ValueError("演示状态里没有会话")
    return [
        {
            "key": key,
            "conversation_id": value["conversation_id"],
            "business_type": value["business_type"],
        }
        for key, value in conversations.items()
    ]


def _submit_all(
    client: httpx.Client,
    token: str,
    conversations: Sequence[dict[str, Any]],
    *,
    count: int,
    concurrency: int,
    run_tag: str,
) -> list[dict[str, Any]]:
    headers = {"Authorization": f"Bearer {token}"}
    jobs = [
        {
            "index": index,
            "key": conversations[index % len(conversations)]["key"],
            "conversation_id": conversations[index % len(conversations)]["conversation_id"],
            "business_type": conversations[index % len(conversations)]["business_type"],
        }
        for index in range(count)
    ]

    def submit(job: dict[str, Any]) -> dict[str, Any]:
        record = dict(job)
        started = time.perf_counter()
        try:
            response = client.post(
                "/api/v1/inspections",
                json={
                    "conversation_id": job["conversation_id"],
                    "business_type": job["business_type"],
                },
                headers={
                    **headers,
                    "Idempotency-Key": f"t39-{run_tag}-{job['index']:03d}",
                },
            )
            record["post_ms"] = round((time.perf_counter() - started) * 1000, 3)
            record["http_status"] = response.status_code
            body = response.json()
            record["inspection_id"] = body.get("inspection_id")
            record["rule_count"] = body.get("rule_count")
            if response.status_code != 202:
                record["submit_error"] = body
        except Exception as exc:  # 传输错误显式记录，不混入延迟分位
            record["post_ms"] = round((time.perf_counter() - started) * 1000, 3)
            record["http_status"] = None
            record["submit_error"] = f"{type(exc).__name__}: {exc}"
        return record

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as executor:
        return list(executor.map(submit, jobs))


def _poll_until_terminal(
    database_url: str,
    records: Sequence[dict[str, Any]],
    *,
    timeout_seconds: float,
    poll_seconds: float = 0.2,
) -> None:
    engine = create_engine(database_url)
    ids = [r["inspection_id"] for r in records if r.get("inspection_id")]
    remaining = set(ids)
    started = time.perf_counter()
    try:
        while remaining and time.perf_counter() - started < timeout_seconds:
            with engine.connect() as connection:
                rows = connection.execute(
                    text(
                        "SELECT inspection_id::text AS inspection_id, status, error_code, "
                        "EXTRACT(EPOCH FROM (finished_at - created_at)) AS seconds, "
                        "(SELECT count(*) FROM findings f "
                        " WHERE f.inspection_id = inspection_runs.inspection_id) AS findings "
                        "FROM inspection_runs WHERE inspection_id::text = ANY(:ids)"
                    ),
                    {"ids": list(remaining)},
                ).mappings().all()
            for row in rows:
                if row["status"] in {"succeeded", "failed", "cancelled"}:
                    record = next(
                        r
                        for r in records
                        if str(r.get("inspection_id")) == row["inspection_id"]
                    )
                    record["status"] = row["status"]
                    record["error_code"] = row["error_code"]
                    record["completion_seconds"] = (
                        float(row["seconds"]) if row["seconds"] is not None else None
                    )
                    record["findings_count"] = int(row["findings"])
                    remaining.discard(row["inspection_id"])
            if remaining:
                time.sleep(poll_seconds)
    finally:
        engine.dispose()
    for record in records:
        if "status" not in record:
            record["status"] = "timeout"
            record["error_code"] = "CLIENT_TIMEOUT"


def run(args: argparse.Namespace) -> dict[str, Any]:
    conversations = _load_conversations(Path(args.state))
    with httpx.Client(base_url=args.base_url, timeout=args.http_timeout) as client:
        ready = client.get("/health/ready")
        ready.raise_for_status()
        token = _login(client)
        started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        records = _submit_all(
            client,
            token,
            conversations,
            count=args.count,
            concurrency=args.concurrency,
            run_tag=args.run_tag,
        )
        _poll_until_terminal(
            args.database_url,
            records,
            timeout_seconds=args.completion_timeout,
        )

    usage = ModelCallUsage(statuses={})
    for path in args.worker_log:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        usage = usage.merged(parse_model_call_lines(lines))

    summary = summarize(
        records,
        usage,
        input_cny_per_mtok=args.input_cny_per_mtok,
        output_cny_per_mtok=args.output_cny_per_mtok,
    )
    summary.update(
        {
            "schema": "t39-inspection-latency-v1",
            "started_at": started_at,
            "base_url": args.base_url,
            "concurrency": args.concurrency,
            "run_tag": args.run_tag,
            "conversations": sorted({r["key"] for r in records}),
            "completion_definition": "server finished_at - created_at（秒）",
        }
    )
    payload = {"summary": summary, "records": records}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    def fmt(dist: dict[str, float | None], unit: str = "s") -> str:
        parts = []
        for key in ("p50", "p95", "p99", "max"):
            value = dist.get(key)
            parts.append(f"{key}={value:.3f}{unit}" if value is not None else f"{key}=n/a")
        return " ".join(parts)

    print(
        f"n={summary['n_requested']} accepted={summary['n_accepted']} "
        f"completed={summary['n_completed']} succeeded={summary['n_succeeded']} "
        f"failed={summary['n_failed']} pending={summary['n_pending']}"
    )
    print(f"POST ms: {fmt(summary['post_ms'], 'ms')}")
    print(f"completion all: {fmt(summary['completion_seconds_all'])}")
    print(f"completion succeeded: {fmt(summary['completion_seconds_succeeded'])}")
    print(f"failure reasons: {summary['failure_reasons']}")
    print(
        f"tokens: calls={summary['tokens']['model_calls_live']} "
        f"prompt={summary['tokens']['prompt']} completion={summary['tokens']['completion']} "
        f"cost≈¥{summary['cost_cny_estimate']:.4f}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="T39 质检任务端到端延迟实测")
    parser.add_argument("--base-url", default="http://127.0.0.1:18080")
    parser.add_argument(
        "--state",
        default=".local-data/t39-artifacts/data/demo-seed-state.json",
    )
    parser.add_argument(
        "--database-url", default=os.environ.get("DATABASE_URL", "")
    )
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--concurrency", type=int, default=5)
    parser.add_argument("--completion-timeout", type=float, default=300.0)
    parser.add_argument("--http-timeout", type=float, default=30.0)
    parser.add_argument("--run-tag", default="measure")
    parser.add_argument(
        "--output",
        default=".local-data/t39-artifacts/inspection-latency.json",
    )
    parser.add_argument("--worker-log", action="append", default=[])
    parser.add_argument("--input-cny-per-mtok", type=float, default=DEFAULT_INPUT_CNY_PER_MTOK)
    parser.add_argument(
        "--output-cny-per-mtok", type=float, default=DEFAULT_OUTPUT_CNY_PER_MTOK
    )
    args = parser.parse_args(argv)
    if not args.database_url:
        raise SystemExit("需要 --database-url（或 DATABASE_URL 环境变量）")
    summary = run(args)
    _print_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
