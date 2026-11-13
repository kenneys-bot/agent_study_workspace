"""T38 压测脚本的离线纯逻辑测试（不发真实请求）。"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts.load_test import (
    EndpointStat,
    build_summary,
    percentile,
    rate,
    summarize_endpoints,
)


def test_percentile_empty_returns_none() -> None:
    assert percentile([], 95) is None


def test_percentile_single_value_is_itself() -> None:
    assert percentile([0.123], 50) == pytest.approx(0.123)
    assert percentile([0.123], 99) == pytest.approx(0.123)


def test_percentile_linear_interpolation() -> None:
    values = [0.005, 0.004, 0.003, 0.002, 0.001]
    assert percentile(values, 0) == pytest.approx(0.001)
    assert percentile(values, 50) == pytest.approx(0.003)
    # (5-1)*0.95 = 3.8 → data[3] + 0.8*(data[4]-data[3])
    assert percentile(values, 95) == pytest.approx(0.0048)
    assert percentile(values, 100) == pytest.approx(0.005)


def test_percentile_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        percentile([1.0], 101)


def test_rate_handles_zero_elapsed() -> None:
    assert rate(10, 0) == 0.0
    assert rate(10, 2) == pytest.approx(5.0)


def test_summarize_endpoints_splits_errors_and_success_latency() -> None:
    stat = EndpointStat(name="live", method="GET", path="/health/live")
    stat.record(200, 0.001, 200)
    stat.record(200, 0.003, 200)
    stat.record(500, 0.0005, 200)  # 失败不进入分位
    stat.record_transport_error("ConnectError")

    rows = summarize_endpoints([stat], elapsed_seconds=2.0)

    row = rows[0]
    assert row["requests"] == 4
    assert row["successes"] == 2
    assert row["errors"] == 2
    assert row["error_rate"] == pytest.approx(0.5)
    assert row["throughput_rps"] == pytest.approx(1.0)
    assert row["p50_ms"] == pytest.approx(2.0)
    assert row["p95_ms"] == pytest.approx(2.9)
    assert row["status_counts"] == {200: 2, 500: 1}
    assert row["transport_errors"] == {"ConnectError": 1}


def test_summarize_endpoints_without_requests_has_null_percentiles() -> None:
    stat = EndpointStat(name="ready", method="GET", path="/health/ready")
    row = summarize_endpoints([stat], elapsed_seconds=10.0)[0]
    assert row["requests"] == 0
    assert row["error_rate"] == 0.0
    assert row["p50_ms"] is None


def test_build_summary_totals_are_consistent() -> None:
    stats = [
        EndpointStat(name="live", method="GET", path="/health/live"),
        EndpointStat(name="version", method="GET", path="/api/v1/system/version"),
    ]
    stats[0].record(200, 0.001, 200)
    stats[1].record(200, 0.01, 200)
    stats[1].record(503, 0.02, 200)
    rows = summarize_endpoints(stats, elapsed_seconds=5.0)
    summary = build_summary(
        rows=rows,
        elapsed_seconds=5.0,
        started_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        finished_at=datetime(2026, 11, 13, tzinfo=timezone.utc),
        base_url="http://127.0.0.1:8000",
        phase="non-model",
        concurrency=10,
        requested_duration=600.0,
        model_mode="live",
        inspection_id="00000000-0000-0000-0000-000000000000",
    )
    assert summary["overall"]["requests"] == 3
    assert summary["overall"]["successes"] == 2
    assert summary["overall"]["errors"] == 1
    assert summary["overall"]["error_rate"] == pytest.approx(1 / 3)
    assert summary["overall"]["throughput_rps"] == pytest.approx(0.4)
    assert summary["phase"] == "non-model"
    assert summary["concurrency"] == 10
