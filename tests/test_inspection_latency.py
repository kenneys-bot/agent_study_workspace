"""T39 质检延迟实测脚本的离线纯逻辑测试（不发真实请求、不读真实库）。"""

from __future__ import annotations

import pytest

from scripts.inspection_latency_measure import (
    ModelCallUsage,
    estimate_cost_cny,
    parse_model_call_lines,
    percentile,
    summarize,
    summarize_findings,
)


def test_percentile_empty_returns_none() -> None:
    assert percentile([], 95) is None


def test_percentile_linear_interpolation() -> None:
    values = [4.0, 1.0, 3.0, 2.0, 5.0]
    assert percentile(values, 0) == pytest.approx(1.0)
    assert percentile(values, 50) == pytest.approx(3.0)
    # (5-1)*0.95 = 3.8 → 4.0 + 0.8*(5.0-4.0)
    assert percentile(values, 95) == pytest.approx(4.8)
    assert percentile(values, 100) == pytest.approx(5.0)


def test_percentile_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_estimate_cost_uses_per_million_rates() -> None:
    assert estimate_cost_cny(1_000_000, 1_000_000) == pytest.approx(10.0)
    assert estimate_cost_cny(500_000, 250_000) == pytest.approx(3.0)
    with pytest.raises(ValueError):
        estimate_cost_cny(-1, 0)


def test_parse_model_call_lines_counts_only_live_calls() -> None:
    lines = [
        "model_call provider=deepseek model=deepseek-chat mode=live status=succeeded "
        "finish_reason=stop attempts=1 fallback_used=False "
        "prompt_tokens=1000 completion_tokens=200",
        "model_call provider=replay model=replay mode=replay status=succeeded "
        "finish_reason=stop attempts=1 fallback_used=False "
        "prompt_tokens=0 completion_tokens=0",
        "model_call provider=deepseek model=deepseek-chat mode=live status=timeout "
        "finish_reason=None attempts=3 fallback_used=False "
        "prompt_tokens=0 completion_tokens=0",
        "无关日志行",
    ]
    usage = parse_model_call_lines(lines)
    assert usage.calls == 2
    assert usage.prompt_tokens == 1000
    assert usage.completion_tokens == 200
    assert usage.statuses == {"succeeded": 1, "timeout": 1}


def _record(
    *,
    post_ms: float | None,
    completion_seconds: float | None,
    status: str,
    error_code: str | None = None,
    http_status: int = 202,
) -> dict[str, object]:
    return {
        "post_ms": post_ms,
        "completion_seconds": completion_seconds,
        "status": status,
        "error_code": error_code,
        "http_status": http_status,
    }


def test_summarize_separates_success_from_failures_and_pending() -> None:
    records = [
        _record(post_ms=100.0, completion_seconds=10.0, status="succeeded"),
        _record(post_ms=200.0, completion_seconds=20.0, status="succeeded"),
        _record(post_ms=300.0, completion_seconds=30.0, status="failed", error_code="EVIDENCE_INVALID"),
        _record(post_ms=None, completion_seconds=None, status="timeout", http_status=None),
    ]
    usage = ModelCallUsage(
        calls=5, prompt_tokens=1000, completion_tokens=100, statuses={"succeeded": 5}
    )
    summary = summarize(records, usage)
    assert summary["n_requested"] == 4
    assert summary["n_accepted"] == 3
    assert summary["n_completed"] == 3
    assert summary["n_succeeded"] == 2
    assert summary["n_failed"] == 1
    assert summary["n_pending"] == 1
    assert summary["failure_rate"] == pytest.approx(1 / 3)
    assert summary["failure_reasons"] == {"EVIDENCE_INVALID": 1}
    assert summary["post_ms"]["p50"] == pytest.approx(200.0)
    assert summary["post_ms"]["p95"] == pytest.approx(290.0)
    assert summary["completion_seconds_succeeded"]["p95"] == pytest.approx(19.5)
    assert summary["completion_seconds_all"]["max"] == pytest.approx(30.0)
    assert summary["tokens"]["model_calls_live"] == 5
    assert summary["cost_cny_estimate"] == pytest.approx(0.0028)


def test_summarize_without_completed_tasks_has_null_distributions() -> None:
    usage = ModelCallUsage(calls=0, prompt_tokens=0, completion_tokens=0, statuses={})
    summary = summarize(
        [_record(post_ms=None, completion_seconds=None, status="timeout", http_status=None)],
        usage,
    )
    assert summary["n_completed"] == 0
    assert summary["failure_rate"] is None
    assert summary["completion_seconds_all"]["p95"] is None
    assert summary["cost_cny_estimate"] == 0.0


def _finding(
    rule_version_id: str,
    verdict: str,
    evidence: list[dict[str, object]],
    *,
    prompt_version: str | None = None,
    downgrades: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "rule_version_id": rule_version_id,
        "machine_verdict": verdict,
        "evidence": evidence,
        "rule_snapshot": {
            "rules": [{"rule_version_id": rule_version_id, "rule_code": "R-1"}]
        },
        "diagnostics": {
            "prompt_version": prompt_version,
            "evidence_downgrades": downgrades or [],
        },
    }


def test_summarize_findings_recomputes_span_validity_and_verdicts() -> None:
    turn_id = "00000000-0000-0000-0000-00000000a001"
    contents = {turn_id: "这笔年费我给您免了"}
    rows = [
        _finding(
            "v1",
            "hit",
            [{"turn_id": turn_id, "start": 4, "end": 9, "quote": "我给您免了"}],
            prompt_version="inspection-v2:abc",
        ),
        _finding("v2", "insufficient_evidence", [], downgrades=[{"reason": "EVIDENCE_NOT_LOCATABLE"}]),
        _finding(
            "v3",
            "hit",
            [{"turn_id": turn_id, "start": 0, "end": 2, "quote": "我给您"}],
        ),
    ]

    summary = summarize_findings(rows, contents)

    assert summary["findings_total"] == 3
    assert summary["verdicts"] == {"hit": 2, "insufficient_evidence": 1}
    assert summary["spans_total"] == 2
    assert summary["spans_valid"] == 1
    assert summary["hit_spans_valid"] == 1
    assert summary["hit_spans_total"] == 2
    assert summary["all_hit_findings_span_valid"] is False
    assert summary["invalid_hit_examples"][0]["reason"] == "span_mismatch"
    assert summary["downgrades"] == {"EVIDENCE_NOT_LOCATABLE": 1}
    assert summary["prompt_versions"] == ["inspection-v2:abc"]


def test_summarize_findings_all_valid_hit_spans_is_true() -> None:
    turn_id = "00000000-0000-0000-0000-00000000a001"
    rows = [
        _finding(
            "v1",
            "hit",
            [{"turn_id": turn_id, "start": 4, "end": 9, "quote": "我给您免了"}],
        )
    ]

    summary = summarize_findings(rows, {turn_id: "这笔年费我给您免了"})

    assert summary["all_hit_findings_span_valid"] is True
    assert summary["span_validation_rate"] == 1.0


def test_summarize_findings_flags_hit_without_evidence() -> None:
    summary = summarize_findings(
        [_finding("v1", "hit", [])], {}
    )

    assert summary["hit_findings_without_span"] == 1
    assert summary["all_hit_findings_span_valid"] is False
    assert summary["invalid_hit_examples"][0]["reason"] == "no_evidence"
