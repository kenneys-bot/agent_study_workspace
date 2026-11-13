"""T38 24h 探测脚本的离线纯逻辑测试（不启动常驻进程、不访问网络）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.probe_uptime import (
    append_jsonl,
    parse_metrics,
    rotate_if_needed,
    summarize_health,
)

SAMPLE_METRICS = """\
# HELP bank_db_ready 数据库就绪状态
# TYPE bank_db_ready gauge
bank_db_ready 1
bank_http_inflight_requests 2
bank_http_requests_total{method="GET",route="/health/live",status="200"} 10
bank_http_requests_total{method="GET",route="/health/ready",status="200"} 5
bank_task_queue_depth{task_type="inspection",status="queued"} 0
bank_task_queue_depth{task_type="inspection",status="running"} 1
bank_task_queue_depth{task_type="inspection",status="succeeded"} 3
bank_rate_limit_rejections_total{scope="login"} 2
bank_db_probe_errors_total 4
bank_model_calls_total{provider="deepseek",status="ok"} 7
"""


def test_parse_metrics_extracts_gauges_depths_and_sums_counters() -> None:
    parsed = parse_metrics(SAMPLE_METRICS)
    assert parsed["gauges"] == {"db_ready": 1.0, "http_inflight": 2.0}
    assert parsed["queue_depth"] == {"queued": 0.0, "running": 1.0, "succeeded": 3.0}
    assert parsed["counters"]["bank_http_requests_total"] == 15.0
    assert parsed["counters"]["bank_rate_limit_rejections_total"] == 2.0
    assert parsed["counters"]["bank_db_probe_errors_total"] == 4.0
    assert parsed["counters"]["bank_model_calls_total"] == 7.0
    assert parsed["counters"]["bank_input_rejections_total"] == 0.0


def test_parse_metrics_ignores_garbage_lines() -> None:
    parsed = parse_metrics("not a metric\nbank_db_ready nope\nbank_db_ready 0\n")
    assert parsed["gauges"] == {"db_ready": 0.0}


def test_rotate_if_needed_noop_below_threshold(tmp_path: Path) -> None:
    path = tmp_path / "probe.jsonl"
    path.write_text("x", encoding="utf-8")
    assert rotate_if_needed(path, max_bytes=100, keep=3) is False
    assert path.exists()
    assert not Path(f"{path}.1").exists()


def test_rotate_if_needed_shifts_and_honors_keep(tmp_path: Path) -> None:
    path = tmp_path / "probe.jsonl"
    path.write_text("current", encoding="utf-8")
    Path(f"{path}.1").write_text("older-1", encoding="utf-8")
    Path(f"{path}.2").write_text("older-2", encoding="utf-8")

    assert rotate_if_needed(path, max_bytes=1, keep=2) is True

    assert Path(f"{path}.1").read_text(encoding="utf-8") == "current"
    assert Path(f"{path}.2").read_text(encoding="utf-8") == "older-1"
    # keep=2 时最旧的 .2 被删除，不再出现 .3
    assert not Path(f"{path}.3").exists()
    assert not path.exists()


def test_rotate_rejects_invalid_limits(tmp_path: Path) -> None:
    path = tmp_path / "x"
    path.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        rotate_if_needed(path, max_bytes=0, keep=1)
    with pytest.raises(ValueError):
        rotate_if_needed(path, max_bytes=1, keep=0)


def test_append_jsonl_writes_one_compact_line(tmp_path: Path) -> None:
    path = tmp_path / "probe.jsonl"
    append_jsonl(path, {"b": 2, "a": 1}, max_bytes=1024, keep=3)
    append_jsonl(path, {"kind": "health", "ok": True}, max_bytes=1024, keep=3)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0]) == {"a": 1, "b": 2}
    assert json.loads(lines[1])["ok"] is True


def test_append_jsonl_rotates_when_threshold_reached(tmp_path: Path) -> None:
    path = tmp_path / "probe.jsonl"
    append_jsonl(path, {"n": 1}, max_bytes=1, keep=2)
    append_jsonl(path, {"n": 2}, max_bytes=1, keep=2)

    assert Path(f"{path}.1").exists()
    assert json.loads(path.read_text(encoding="utf-8"))["n"] == 2


def test_summarize_health_reports_uptime_and_failures() -> None:
    records = [
        {"kind": "health", "target": "live", "ok": True, "latency_ms": 10.0},
        {"kind": "health", "target": "live", "ok": True, "latency_ms": 30.0},
        {
            "kind": "health",
            "target": "ready",
            "ok": False,
            "latency_ms": 5.0,
            "status": 503,
            "error": None,
            "ts": "2026-11-13T00:00:00+00:00",
        },
        {"kind": "metrics", "ok": True},
    ]

    summary = summarize_health(records)

    assert summary["live"]["checks"] == 2
    assert summary["live"]["uptime_ratio"] == 1.0
    assert summary["live"]["avg_latency_ms"] == 20.0
    assert summary["live"]["max_latency_ms"] == 30.0
    assert summary["ready"]["fail"] == 1
    assert summary["ready"]["uptime_ratio"] == 0.0
    assert summary["ready"]["failures"][0]["status"] == 503


def test_summarize_health_empty() -> None:
    assert summarize_health([]) == {}
