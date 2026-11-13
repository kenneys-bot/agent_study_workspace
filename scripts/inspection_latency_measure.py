#!/usr/bin/env python
"""质检任务端到端延迟实测客户端（T39 / #39，T40 / #40；计划 §7）。

对**本地临时 API + 真实 worker + 真实 DeepSeek** 发起 N 次真实质检任务，
记录每次 ``POST /inspections`` 的接收延迟、从 ``created_at`` 到 ``finished_at``
的完成时间、终态与失败原因，并汇总 P50/P95/P99、token 与估算成本。

T40 追加（R-19 复测）：

- 从 ``findings`` 汇总逐规则 verdict 分布；
- 在持久化数据上重新回验 ``quote == content[start:end]``（码点），给出证据
  核验率，尤其只统计 ``hit`` 证据（必须 100%）；
- 从 ``inspection_runs.diagnostics`` 汇总 ``evidence_downgrades`` 原因码与
  ``prompt_version``。

约束（与 T38 压测同一口径）：

- 只对成功提交（HTTP 202）统计接收延迟；
- 完成时间使用服务端时间戳 ``finished_at - created_at``，避免客户端轮询误差；
- 分位用线性插值（与 numpy 默认 ``linear`` 一致），空样本返回 ``None``；
- token 从 worker 日志的 ``model_call`` 行读取，不改写模型结果；
- 未终态的任务单独计数，不用超时值伪造分位。

用法（本机临时实例，凭据来自仓库外 ``.env``；不打印任何秘密）::

    .local-data/t40-artifacts/run/env.sh
    .venv/bin/python scripts/inspection_latency_measure.py \
        --base-url http://127.0.0.1:18080 \
        --state .local-data/t40-artifacts/data/demo-seed-state.json \
        --count 30 --concurrency 5 \
        --output .local-data/t40-artifacts/inspection-latency.json \
        --worker-log .local-data/t40-artifacts/run/worker-1.log ...
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


def _span_is_valid(span: Any, turn_contents: dict[str, str]) -> bool:
    """按契约 §4 回验一条证据：码点区间 + quote 与原文逐字相等。"""
    if not isinstance(span, dict):
        return False
    try:
        turn_id = str(span["turn_id"])
        start = span["start"]
        end = span["end"]
        quote = span["quote"]
    except KeyError:
        return False
    if (
        isinstance(start, bool)
        or isinstance(end, bool)
        or not isinstance(start, int)
        or not isinstance(end, int)
        or not isinstance(quote, str)
    ):
        return False
    content = turn_contents.get(turn_id)
    if content is None or start < 0 or end <= start or end > len(content):
        return False
    return content[start:end] == quote


def _rule_code_lookup(snapshot: Any) -> dict[str, str]:
    if not isinstance(snapshot, dict):
        return {}
    rules = snapshot.get("rules")
    if not isinstance(rules, list):
        return {}
    lookup: dict[str, str] = {}
    for rule in rules:
        if isinstance(rule, dict) and rule.get("rule_version_id"):
            lookup[str(rule["rule_version_id"])] = str(
                rule.get("rule_code") or "UNKNOWN"
            )
    return lookup


def summarize_findings(
    rows: Sequence[dict[str, Any]],
    turn_contents: dict[str, str],
) -> dict[str, Any]:
    """汇总逐规则 verdict、证据核验率与降级原因（纯逻辑，便于离线测试）。"""
    verdicts: dict[str, int] = {}
    per_rule: dict[str, dict[str, int]] = {}
    spans_total = 0
    spans_valid = 0
    hit_findings = 0
    hit_findings_without_span = 0
    hit_spans_total = 0
    hit_spans_valid = 0
    invalid_hit_examples: list[dict[str, Any]] = []
    downgrades: dict[str, int] = {}
    prompt_versions: set[str] = set()

    for row in rows:
        verdict = str(row.get("machine_verdict") or "UNKNOWN")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        lookup = _rule_code_lookup(row.get("rule_snapshot"))
        rule_code = lookup.get(str(row.get("rule_version_id")), "UNKNOWN")
        bucket = per_rule.setdefault(rule_code, {})
        bucket[verdict] = bucket.get(verdict, 0) + 1

        diagnostics = row.get("diagnostics")
        if isinstance(diagnostics, dict):
            version = diagnostics.get("prompt_version")
            if version:
                prompt_versions.add(str(version))
            for item in diagnostics.get("evidence_downgrades") or []:
                if isinstance(item, dict):
                    reason = str(item.get("reason") or "UNKNOWN")
                    downgrades[reason] = downgrades.get(reason, 0) + 1

        evidence = row.get("evidence") or []
        if not isinstance(evidence, list):
            evidence = []
        checks = [_span_is_valid(span, turn_contents) for span in evidence]
        spans_total += len(checks)
        spans_valid += sum(1 for ok in checks if ok)
        if verdict == "hit":
            hit_findings += 1
            hit_spans_total += len(checks)
            hit_spans_valid += sum(1 for ok in checks if ok)
            if not checks:
                hit_findings_without_span += 1
                if len(invalid_hit_examples) < 5:
                    invalid_hit_examples.append(
                        {"rule_code": rule_code, "reason": "no_evidence"}
                    )
            elif not all(checks):
                if len(invalid_hit_examples) < 5:
                    invalid_hit_examples.append(
                        {
                            "rule_code": rule_code,
                            "reason": "span_mismatch",
                            "spans": len(checks),
                            "valid_spans": sum(1 for ok in checks if ok),
                        }
                    )

    def rate(numerator: int, denominator: int) -> float | None:
        return numerator / denominator if denominator else None

    return {
        "findings_total": len(rows),
        "verdicts": verdicts,
        "per_rule": per_rule,
        "spans_total": spans_total,
        "spans_valid": spans_valid,
        "span_validation_rate": rate(spans_valid, spans_total),
        "hit_findings": hit_findings,
        "hit_findings_without_span": hit_findings_without_span,
        "hit_spans_total": hit_spans_total,
        "hit_spans_valid": hit_spans_valid,
        "hit_span_validation_rate": rate(hit_spans_valid, hit_spans_total),
        "all_hit_findings_span_valid": (
            hit_findings > 0
            and hit_findings_without_span == 0
            and hit_spans_valid == hit_spans_total
            and hit_spans_total > 0
        ),
        "invalid_hit_examples": invalid_hit_examples,
        "downgrades": downgrades,
        "prompt_versions": sorted(prompt_versions),
    }


def collect_findings(
    database_url: str, inspection_ids: Sequence[str]
) -> dict[str, Any]:
    """从数据库读取本轮 findings/turns 并回验证据（只读，不改数据）。"""
    ids = sorted({str(item) for item in inspection_ids if item})
    if not ids:
        return summarize_findings([], {})
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            turn_rows = connection.execute(
                text(
                    "SELECT DISTINCT t.turn_id::text AS turn_id, t.content "
                    "FROM turns t JOIN inspection_runs r "
                    "ON r.conversation_id = t.conversation_id "
                    "WHERE r.inspection_id::text = ANY(:ids)"
                ),
                {"ids": ids},
            ).mappings().all()
            finding_rows = connection.execute(
                text(
                    "SELECT f.rule_version_id::text AS rule_version_id, "
                    "f.machine_verdict, f.evidence, "
                    "r.rule_snapshot, r.diagnostics "
                    "FROM findings f JOIN inspection_runs r "
                    "ON r.inspection_id = f.inspection_id "
                    "WHERE f.inspection_id::text = ANY(:ids)"
                ),
                {"ids": ids},
            ).mappings().all()
    finally:
        engine.dispose()
    turn_contents = {
        str(row["turn_id"]): str(row["content"]) for row in turn_rows
    }
    return summarize_findings([dict(row) for row in finding_rows], turn_contents)



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
    submit_retries: int = 8,
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
        record["submit_attempts"] = 0
        record["rate_limited_attempts"] = 0
        for attempt in range(1, max(1, submit_retries) + 1):
            record["submit_attempts"] = attempt
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
                        "Idempotency-Key": f"t40-{run_tag}-{job['index']:03d}",
                    },
                )
            except Exception as exc:  # 传输错误显式记录，不混入延迟分位
                record["post_ms"] = round((time.perf_counter() - started) * 1000, 3)
                record["http_status"] = None
                record["submit_error"] = f"{type(exc).__name__}: {exc}"
                return record
            record["post_ms"] = round((time.perf_counter() - started) * 1000, 3)
            record["http_status"] = response.status_code
            body = response.json()
            if response.status_code == 202:
                record["inspection_id"] = body.get("inspection_id")
                record["rule_count"] = body.get("rule_count")
                record.pop("submit_error", None)
                return record
            # T40：5 并发提交会触发 expensive 限流（429）。按 Retry-After 有界重试，
            # 只统计最终 202 的接收延迟；被限流的尝试次数如实记录。
            if response.status_code == 429 and attempt < submit_retries:
                record["rate_limited_attempts"] += 1
                retry_after = 1.0
                details = body.get("details") if isinstance(body, dict) else None
                if isinstance(details, dict):
                    retry_after = float(details.get("retry_after_seconds") or 1.0)
                time.sleep(max(0.2, min(retry_after, 5.0)))
                continue
            record["inspection_id"] = body.get("inspection_id")
            record["rule_count"] = body.get("rule_count")
            record["submit_error"] = body
            return record
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
            submit_retries=args.submit_retries,
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
    summary["findings"] = collect_findings(
        args.database_url,
        [r["inspection_id"] for r in records if r.get("inspection_id")],
    )
    summary.update(
        {
            "schema": "t40-inspection-latency-v1",
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
    findings = summary.get("findings") or {}
    print(f"verdicts: {findings.get('verdicts')}")
    print(
        "evidence: "
        f"spans_valid={findings.get('spans_valid')}/{findings.get('spans_total')} "
        f"hit_spans_valid={findings.get('hit_spans_valid')}/"
        f"{findings.get('hit_spans_total')} "
        f"all_hit_valid={findings.get('all_hit_findings_span_valid')}"
    )
    print(f"downgrades: {findings.get('downgrades')}")


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
    parser.add_argument(
        "--submit-retries",
        type=int,
        default=8,
        help="遇到 429 时的最大提交尝试次数（按 Retry-After 有界重试）",
    )
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
