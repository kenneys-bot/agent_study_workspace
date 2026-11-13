#!/usr/bin/env python3
"""24 小时稳定性定时探测（T38 / #38，计划 §7「长时间运行」证据）。

在**服务器宿主**上用标准库运行（不装第三方依赖、不进入 API 容器）：

- 每 5 分钟：``GET /health/live`` 与 ``GET /health/ready``，记录状态码、延迟、
  响应体；连接失败也记录，不把“探不到”当成“健康”；
- 每 30 分钟：抓取 ``/metrics`` 原文保存为文件，并解析关键指标（``bank_db_ready``、
  任务队列深度、请求/模型/限流计数），同时用只读 ``docker inspect`` 记录
  ``bank-assistant-api`` / ``bank-assistant-db-db-1`` 与参照容器 ``redis-redis-1``
  的状态（参照容器只为证明未被触碰）；
- JSONL 追加写到 ``<out-dir>/probe.jsonl``，超过阈值自动轮转为 ``.1/.2/...``；
  每轮更新 ``summary.json``，停止时可据此给出已运行时长与可用率；
- 单实例：``probe.pid`` 已存在且进程存活时拒绝重复启动。

起点（服务器）::

    mkdir -p ~/workspace/bank-assistant/artifacts/probe
    scp scripts/probe_uptime.py my-ubuntu-server:~/workspace/bank-assistant/artifacts/probe/
    ssh my-ubuntu-server 'cd ~/workspace/bank-assistant/artifacts/probe && \
      nohup python3 probe_uptime.py --out-dir . \
        --base-url http://127.0.0.1:8000 > probe.log 2>&1 &'

停止::

    ssh my-ubuntu-server 'kill "$(cat ~/workspace/bank-assistant/artifacts/probe/probe.pid)"'

快速验证（不进入常驻循环）::

    python3 probe_uptime.py --out-dir /tmp/probe-check --once

边界：本探测只看进程/依赖是否可用与资源指标，不判断业务正确性，也不替代
真实事故响应；``/metrics`` 仅回环可访问（R-09），跨机查看需 SSH。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

SCHEMA = "t38-probe-v1"
DEFAULT_HEALTH_INTERVAL = 300.0
DEFAULT_METRICS_INTERVAL = 1800.0

_COUNTER_PREFIXES = (
    "bank_http_requests_total",
    "bank_model_calls_total",
    "bank_rate_limit_rejections_total",
    "bank_task_events_total",
    "bank_input_rejections_total",
    "bank_db_probe_errors_total",
    "bank_task_queue_depth_collect_errors_total",
)


# --------------------------------------------------------------------- 纯逻辑


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_metrics(text: str) -> dict[str, Any]:
    """从 Prometheus 文本里提取报告需要的少量序列。

    只做“按名称求和/取最新 gauge”，不实现完整 PromQL；解析失败的行跳过，
    不因为一行脏数据丢掉整次快照。
    """

    gauges: dict[str, float] = {}
    counters: dict[str, float] = {name: 0.0 for name in _COUNTER_PREFIXES}
    queue_depth: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name_part, value_part = line.rsplit(" ", 1)
            value = float(value_part)
        except ValueError:
            continue
        base = name_part.split("{", 1)[0]
        if base == "bank_db_ready":
            gauges["db_ready"] = value
        elif base == "bank_http_inflight_requests":
            gauges["http_inflight"] = value
        elif base == "bank_task_queue_depth":
            match = re.search(r'status="([^"]+)"', name_part)
            if match:
                queue_depth[match.group(1)] = value
        if base in counters:
            counters[base] += value
    return {
        "gauges": gauges,
        "counters": counters,
        "queue_depth": queue_depth,
    }


def rotate_if_needed(path: Path, max_bytes: int, keep: int) -> bool:
    """文件达到阈值时轮转（``x`` → ``x.1`` → ``x.2``…），最多保留 ``keep`` 份。"""

    if keep < 1:
        raise ValueError("keep 必须 >= 1")
    if max_bytes < 1:
        raise ValueError("max_bytes 必须 >= 1")
    if not path.exists() or path.stat().st_size < max_bytes:
        return False
    # 从旧到新依次后移；keep 之外的旧文件删除。
    for index in range(keep, 0, -1):
        source = Path(f"{path}.{index}")
        target = Path(f"{path}.{index + 1}")
        if source.exists():
            if index == keep:
                source.unlink()
            else:
                source.replace(target)
    path.replace(Path(f"{path}.1"))
    return True


def append_jsonl(path: Path, record: dict[str, Any], max_bytes: int, keep: int) -> None:
    """追加一行 JSON；写前按大小轮转，flush+fsync 保证断电后尽量可读。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    rotate_if_needed(path, max_bytes, keep)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def summarize_health(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """按目标汇总健康探测：检查数、失败数、可用率、平均/最大延迟、失败时刻。"""

    summary: dict[str, Any] = {}
    for record in records:
        if record.get("kind") != "health":
            continue
        target = str(record.get("target"))
        bucket = summary.setdefault(
            target,
            {"checks": 0, "ok": 0, "fail": 0, "latency_ms": [], "failures": []},
        )
        bucket["checks"] += 1
        if record.get("ok"):
            bucket["ok"] += 1
        else:
            bucket["fail"] += 1
            if len(bucket["failures"]) < 20:
                bucket["failures"].append(
                    {
                        "ts": record.get("ts"),
                        "status": record.get("status"),
                        "error": record.get("error"),
                    }
                )
        latency = record.get("latency_ms")
        if isinstance(latency, (int, float)):
            bucket["latency_ms"].append(float(latency))
    result: dict[str, Any] = {}
    for target, bucket in summary.items():
        latencies = bucket.pop("latency_ms")
        checks = bucket["checks"]
        bucket["uptime_ratio"] = (bucket["ok"] / checks) if checks else 0.0
        bucket["avg_latency_ms"] = (
            round(sum(latencies) / len(latencies), 3) if latencies else None
        )
        bucket["max_latency_ms"] = round(max(latencies), 3) if latencies else None
        result[target] = bucket
    return result


# --------------------------------------------------------------------- 网络/容器


def http_get(url: str, timeout: float) -> dict[str, Any]:
    """GET 并返回状态/原文/延迟/错误；4xx/5xx 也算拿到了响应，不抛异常。"""

    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "t38-probe/1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(65536).decode("utf-8", "replace")
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return {
                "status": response.status,
                "body": body,
                "latency_ms": round(elapsed_ms, 3),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body = exc.read(65536).decode("utf-8", "replace")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "status": exc.code,
            "body": body,
            "latency_ms": round(elapsed_ms, 3),
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - 连接失败要如实记录
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return {
            "status": None,
            "body": "",
            "latency_ms": round(elapsed_ms, 3),
            "error": f"{type(exc).__name__}: {exc}",
        }


def container_state(name: str, timeout: float = 10.0) -> dict[str, Any]:
    """只读 ``docker inspect``；失败记录 error，不让探测进程退出。"""

    try:
        completed = subprocess.run(
            ["docker", "inspect", "--format", "{{.RestartCount}}|{{json .State}}", name],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode != 0:
            return {"name": name, "error": completed.stderr.strip()[:300]}
        restart_count_text, _, state_text = completed.stdout.partition("|")
        state = json.loads(state_text)
        health = state.get("Health") or {}
        return {
            "name": name,
            "status": state.get("Status"),
            "running": state.get("Running"),
            "restarting": state.get("Restarting"),
            "restart_count": int(restart_count_text) if restart_count_text.isdigit() else None,
            "started_at": state.get("StartedAt"),
            "health": health.get("Status"),
        }
    except Exception as exc:  # noqa: BLE001
        return {"name": name, "error": f"{type(exc).__name__}: {exc}"}


def collect_containers(names: Sequence[str]) -> dict[str, Any]:
    return {name: container_state(name) for name in names}


# --------------------------------------------------------------------- 主循环


class Probe:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.out_dir: Path = args.out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl = self.out_dir / "probe.jsonl"
        self.summary_path = self.out_dir / "summary.json"
        self.metrics_dir = self.out_dir / "metrics"
        self.metrics_dir.mkdir(parents=True, exist_ok=True)
        self.pidfile = self.out_dir / "probe.pid"
        self.started_monotonic = time.monotonic()
        self.started_at = utc_now()
        self.health_records: list[dict[str, Any]] = []
        self.metrics_snapshots = 0
        self.last_error: str | None = None
        self._stop = False

    # -- 记录
    def _append(self, record: dict[str, Any]) -> None:
        append_jsonl(
            self.jsonl, record, self.args.max_jsonl_bytes, self.args.rotate_keep
        )

    def write_summary(self) -> dict[str, Any]:
        summary = {
            "schema": SCHEMA,
            "started_at": self.started_at,
            "updated_at": utc_now(),
            "elapsed_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "pid": os.getpid(),
            "base_url": self.args.base_url,
            "health_interval_seconds": self.args.health_interval,
            "metrics_interval_seconds": self.args.metrics_interval,
            "health": summarize_health(self.health_records),
            "metrics_snapshots": self.metrics_snapshots,
            "jsonl": str(self.jsonl),
            "jsonl_size_bytes": self.jsonl.stat().st_size if self.jsonl.exists() else 0,
            "last_error": self.last_error,
        }
        self.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return summary

    # -- 单次采集
    def probe_health(self) -> None:
        for target, path in (("live", "/health/live"), ("ready", "/health/ready")):
            result = http_get(
                self.args.base_url.rstrip("/") + path, self.args.timeout_seconds
            )
            body = result["body"]
            parsed: Any = None
            try:
                parsed = json.loads(body) if body else None
            except json.JSONDecodeError:
                parsed = None
            ok = result["status"] == 200 and (parsed is None or parsed.get("status") in (None, "alive", "ready"))
            record = {
                "ts": utc_now(),
                "kind": "health",
                "target": target,
                "ok": ok,
                "status": result["status"],
                "latency_ms": result["latency_ms"],
                "body": parsed if parsed is not None else body[:500],
                "error": result["error"],
            }
            self.health_records.append(record)
            if len(self.health_records) > 5000:
                # 24h * 每 5 分钟 * 2 目标 = 576 条；上限只为长期运行不无限增长。
                self.health_records = self.health_records[-5000:]
            if not ok:
                self.last_error = f"{target}: status={result['status']} error={result['error']}"
            self._append(record)

    def probe_metrics(self) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.metrics_dir / f"metrics-{timestamp}.txt"
        result = http_get(
            self.args.base_url.rstrip("/") + "/metrics", self.args.timeout_seconds
        )
        parsed = parse_metrics(result["body"]) if result["body"] else {}
        if result["status"] == 200:
            destination.write_text(result["body"], encoding="utf-8")
            self.metrics_snapshots += 1
        else:
            self.last_error = f"metrics: status={result['status']} error={result['error']}"
        self._prune_metrics_files()
        containers = collect_containers(self.args.containers)
        record = {
            "ts": utc_now(),
            "kind": "metrics",
            "ok": result["status"] == 200,
            "status": result["status"],
            "latency_ms": result["latency_ms"],
            "bytes": len(result["body"].encode("utf-8")),
            "file": str(destination) if result["status"] == 200 else None,
            "metrics": parsed,
            "containers": containers,
            "error": result["error"],
        }
        self._append(record)

    def _prune_metrics_files(self) -> None:
        files = sorted(self.metrics_dir.glob("metrics-*.txt"))
        excess = len(files) - self.args.metrics_keep
        for old in files[: max(0, excess)]:
            try:
                old.unlink()
            except OSError:
                pass

    # -- 生命周期
    def stop(self, *_: object) -> None:
        self._stop = True

    def acquire_pidfile(self) -> None:
        if self.pidfile.exists():
            try:
                existing = int(self.pidfile.read_text().strip())
            except ValueError:
                existing = -1
            if existing > 0 and existing != os.getpid() and _pid_alive(existing):
                raise SystemExit(
                    f"probe 已在运行 pid={existing}（{self.pidfile}）；先停止或改用其它 --out-dir"
                )
        self.pidfile.write_text(f"{os.getpid()}\n", encoding="utf-8")

    def release_pidfile(self) -> None:
        try:
            if self.pidfile.exists() and self.pidfile.read_text().strip() == str(os.getpid()):
                self.pidfile.unlink()
        except OSError:
            pass

    def run_once(self) -> None:
        self.probe_health()
        self.probe_metrics()
        self.write_summary()

    def run(self) -> None:
        self.acquire_pidfile()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self.stop)
        deadline = (
            time.monotonic() + self.args.duration_seconds
            if self.args.duration_seconds > 0
            else None
        )
        next_health = time.monotonic()
        next_metrics = time.monotonic()
        try:
            while not self._stop and (deadline is None or time.monotonic() < deadline):
                now = time.monotonic()
                if now >= next_health:
                    self.probe_health()
                    self.write_summary()
                    next_health = now + self.args.health_interval
                if now >= next_metrics:
                    self.probe_metrics()
                    self.write_summary()
                    next_metrics = now + self.args.metrics_interval
                wake = min(next_health, next_metrics)
                if deadline is not None:
                    wake = min(wake, deadline)
                time.sleep(max(0.2, min(1.0, wake - time.monotonic())))
        finally:
            self.write_summary()
            self.release_pidfile()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="24h 稳定性定时探测（标准库）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out-dir", type=Path, default=Path("probe"))
    parser.add_argument("--health-interval", type=float, default=DEFAULT_HEALTH_INTERVAL)
    parser.add_argument("--metrics-interval", type=float, default=DEFAULT_METRICS_INTERVAL)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--once", action="store_true", help="只采集一轮后退出")
    parser.add_argument("--max-jsonl-bytes", type=int, default=5 * 1024 * 1024)
    parser.add_argument("--rotate-keep", type=int, default=3)
    parser.add_argument("--metrics-keep", type=int, default=96)
    parser.add_argument(
        "--containers",
        default="bank-assistant-api,bank-assistant-db-db-1,redis-redis-1",
        help="逗号分隔；只读 inspect，参照容器用于证明未被触碰",
    )
    args = parser.parse_args(argv)
    args.containers = [name.strip() for name in args.containers.split(",") if name.strip()]
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    probe = Probe(args)
    if args.once:
        probe.run_once()
        summary = probe.write_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    probe.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
