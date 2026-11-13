#!/usr/bin/env python3
"""真实 10 并发 / 10 分钟 API 压测（T38 / #38，M7-04 容量证据）。

用途
====

对**已部署**的 API 发起真实 HTTP 压测，统计每个端点的 P50/P95/P99、错误率与
吞吐，并把请求构成、并发、时长、环境与原始快照落盘。默认只压**非模型**端点；
``--phase suggestion`` 才压 ``POST /api/v1/suggestions``，且要求目标实例显式运行
``MODEL_MODE=replay``（脚本会在结果的 ``model_mode`` 中记录实例自报模式）。

设计约束
========

- 脚本不读取数据库、不修改部署；只发 HTTP 请求，登录用公开演示账号。
- 10 个 worker 各自顺序发请求，总并发不超过 ``--concurrency``；不会为了刷高
  吞吐而开无限连接。
- 只对成功响应（HTTP 2xx 且状态码符合预期）统计延迟分位；失败单独计数，
  不能把 5xx 的快速失败混进 P95。
- 原始逐请求记录不落盘（10 分钟可达百万级）；落盘的是 10 秒桶与汇总 JSON，
  足以复算吞吐与错误率。
- 本模块的纯逻辑（``percentile`` / ``summarize_endpoints`` / ``rate``）不依赖
  网络，单元测试在离线环境覆盖。

用法（服务器回环；脚本经已部署镜像运行，不装宿主依赖）::

    ssh my-ubuntu-server 'mkdir -p ~/workspace/bank-assistant/artifacts/loadtest'
    scp scripts/load_test.py my-ubuntu-server:~/workspace/bank-assistant/artifacts/loadtest/
    ssh my-ubuntu-server 'cd ~/workspace/bank-assistant/artifacts/loadtest && \
      docker run --rm --network host --user "$(id -u):$(id -g)" -v "$PWD:/work" \
      -e LOADTEST_PASSWORD=demo-only-correct-horse-battery \
      --entrypoint python bank-assistant:api-3a04952 /work/load_test.py \
      --base-url http://127.0.0.1:8000 --duration-seconds 600 --concurrency 10 \
      --inspection-id <seeded-inspection-uuid> --output-dir /work/nonmodel'

停止：Ctrl-C/SIGTERM 会停止发压并尽量写出已采集部分的汇总。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import signal
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import httpx

# 端点定义：name -> (method, path template, 期望状态码, 是否需要认证)
ENDPOINTS: dict[str, tuple[str, str, int, bool]] = {
    "live": ("GET", "/health/live", 200, False),
    "ready": ("GET", "/health/ready", 200, False),
    "version": ("GET", "/api/v1/system/version", 200, True),
    "inspection": ("GET", "/api/v1/inspections/{inspection_id}", 200, True),
    "suggestion": ("POST", "/api/v1/suggestions", 201, True),
}

DEFAULT_SUGGESTION_QUERY = (
    "银行卡挂失后应该走哪些渠道、需要准备什么？请只依据有效制度说明。"
)

DEMO_USERNAME = os.environ.get("LOADTEST_USERNAME", "demo-author")
DEMO_PASSWORD = os.environ.get("LOADTEST_PASSWORD", "")


# --------------------------------------------------------------------- 纯逻辑


def percentile(values: Sequence[float], p: float) -> float | None:
    """线性插值分位数（与 numpy 默认的 ``linear`` 口径一致）。

    - 空序列返回 ``None``（不是 0，避免把“没有样本”伪装成“很快”）；
    - ``p`` 必须在 ``[0, 100]``；排序后按 ``(n-1)*p/100`` 定位并插值。
    """

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


def rate(count: int, elapsed_seconds: float) -> float:
    """每秒吞吐；``elapsed_seconds`` 非正时返回 0，不制造除零。"""

    if elapsed_seconds <= 0:
        return 0.0
    return count / elapsed_seconds


def summarize_endpoints(
    stats: Iterable["EndpointStat"], elapsed_seconds: float
) -> list[dict[str, Any]]:
    """把各端点计数器转成报告需要的汇总行（分位单位毫秒）。"""

    rows: list[dict[str, Any]] = []
    for stat in stats:
        successes = list(stat.success_latencies)
        errors = stat.total - len(successes)
        rows.append(
            {
                "name": stat.name,
                "method": stat.method,
                "path": stat.path,
                "requests": stat.total,
                "successes": len(successes),
                "errors": errors,
                "error_rate": (errors / stat.total) if stat.total else 0.0,
                "throughput_rps": rate(len(successes), elapsed_seconds),
                "p50_ms": _ms(percentile(successes, 50)),
                "p95_ms": _ms(percentile(successes, 95)),
                "p99_ms": _ms(percentile(successes, 99)),
                "max_ms": _ms(max(successes)) if successes else None,
                "status_counts": dict(stat.status_counts),
                "transport_errors": dict(stat.transport_errors),
            }
        )
    return rows


def _ms(seconds: float | None) -> float | None:
    return None if seconds is None else round(seconds * 1000.0, 3)


def build_summary(
    *,
    rows: Sequence[dict[str, Any]],
    elapsed_seconds: float,
    started_at: datetime,
    finished_at: datetime,
    base_url: str,
    phase: str,
    concurrency: int,
    requested_duration: float,
    model_mode: str | None,
    inspection_id: str | None,
) -> dict[str, Any]:
    """汇总为机器可读 JSON；overall 只用端点行的合计，不重复造数。"""

    total = sum(int(row["requests"]) for row in rows)
    successes = sum(int(row["successes"]) for row in rows)
    errors = total - successes
    return {
        "schema": "t38-loadtest-v1",
        "phase": phase,
        "base_url": base_url,
        "model_mode": model_mode,
        "concurrency": concurrency,
        "requested_duration_seconds": requested_duration,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "inspection_id": inspection_id,
        "endpoints": list(rows),
        "overall": {
            "requests": total,
            "successes": successes,
            "errors": errors,
            "error_rate": (errors / total) if total else 0.0,
            "throughput_rps": rate(successes, elapsed_seconds),
        },
    }


# --------------------------------------------------------------------- 运行时


@dataclass
class EndpointStat:
    """单端点计数器；延迟只保留成功响应。"""

    name: str
    method: str
    path: str
    total: int = 0
    success_latencies: list[float] = field(default_factory=list)
    status_counts: Counter[int] = field(default_factory=Counter)
    transport_errors: Counter[str] = field(default_factory=Counter)

    def record(self, status: int, elapsed: float, expected: int) -> None:
        self.total += 1
        self.status_counts[status] += 1
        if status == expected:
            self.success_latencies.append(elapsed)

    def record_transport_error(self, kind: str) -> None:
        self.total += 1
        self.transport_errors[kind] += 1


class JsonlSink:
    """极简 JSONL 追加器；压测只写 10 秒桶，不写逐请求明细。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")

    def write(self, record: dict[str, Any]) -> None:
        self._handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        self._handle.flush()

    def close(self) -> None:
        self._handle.close()


class AuthState:
    """共享令牌；过期/401 时串行刷新一次，避免 10 个 worker 同时登录。"""

    def __init__(self, client: httpx.AsyncClient, username: str, password: str) -> None:
        self._client = client
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

    async def token(self, force_refresh: bool = False) -> str:
        if self._token is not None and not force_refresh:
            return self._token
        async with self._lock:
            if self._token is not None and not force_refresh:
                return self._token
            response = await self._client.post(
                "/api/v1/auth/login",
                json={"username": self._username, "password": self._password},
            )
            response.raise_for_status()
            token = str(response.json()["access_token"])
            self._token = token
            return token


async def _fetch_model_mode(client: httpx.AsyncClient, auth: AuthState | None) -> str | None:
    """读 /system/version 的 mode；失败返回 None，不阻塞压测。"""

    try:
        headers = {}
        if auth is not None:
            headers["Authorization"] = f"Bearer {await auth.token()}"
        response = await client.get("/api/v1/system/version", headers=headers)
        if response.status_code == 200:
            return response.json().get("mode")
    except Exception:  # noqa: BLE001 - 版本页失败不应中止容量测试
        return None
    return None


async def _fetch_metrics(client: httpx.AsyncClient, destination: Path) -> dict[str, Any]:
    """抓取一次 /metrics 原文快照；返回元信息（是否 200、字节数）。"""

    meta: dict[str, Any] = {"path": str(destination), "ok": False}
    try:
        response = await client.get("/metrics")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(response.text, encoding="utf-8")
        meta.update(
            {
                "ok": response.status_code == 200,
                "status": response.status_code,
                "bytes": len(response.content),
            }
        )
    except Exception as exc:  # noqa: BLE001 - 快照失败要记录，但不影响发压
        meta["error"] = f"{type(exc).__name__}: {exc}"
    return meta


def _request_for(
    name: str,
    inspection_id: str | None,
    suggestion_query: str,
) -> tuple[str, str, dict[str, Any] | None]:
    method, template, expected, needs_auth = ENDPOINTS[name]
    path = template.format(inspection_id=inspection_id)
    body: dict[str, Any] | None = None
    if name == "suggestion":
        # 同一问题反复回放，避免把评测样本差异混进延迟统计；请求内容不写日志。
        body = {"query": suggestion_query}
    return method, path, body


async def _worker(
    worker_id: int,
    args: argparse.Namespace,
    stats: dict[str, EndpointStat],
    auth: AuthState,
    stop: asyncio.Event,
    bucket: dict[str, Any],
    bucket_lock: asyncio.Lock,
) -> None:
    """一个 worker：按轮转顺序发请求，记录成功延迟/状态码/传输错误。"""

    names = args.endpoint_names
    index = worker_id
    client = httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.timeout_seconds),
        limits=httpx.Limits(
            max_connections=args.concurrency + 5,
            max_keepalive_connections=args.concurrency,
        ),
    )
    try:
        while not stop.is_set():
            name = names[index % len(names)]
            index += 1
            method, path, body = _request_for(
                name, args.inspection_id, args.suggestion_query
            )
            headers: dict[str, str] = {}
            if ENDPOINTS[name][3]:
                headers["Authorization"] = f"Bearer {await auth.token()}"
            started = time.perf_counter()
            try:
                response = await client.request(
                    method,
                    path,
                    json=body,
                    headers=headers,
                )
                elapsed = time.perf_counter() - started
                if response.status_code == 401 and ENDPOINTS[name][3]:
                    # 令牌过期：刷新后重试一次，重试请求单独计时。
                    headers["Authorization"] = f"Bearer {await auth.token(force_refresh=True)}"
                    retried = time.perf_counter()
                    response = await client.request(
                        method, path, json=body, headers=headers
                    )
                    elapsed = time.perf_counter() - retried
                stats[name].record(response.status_code, elapsed, ENDPOINTS[name][2])
                async with bucket_lock:
                    entry = bucket.setdefault(
                        name, {"requests": 0, "errors": 0, "latency_sum": 0.0}
                    )
                    entry["requests"] += 1
                    if response.status_code != ENDPOINTS[name][2]:
                        entry["errors"] += 1
                    else:
                        entry["latency_sum"] += elapsed
            except (httpx.HTTPError, OSError) as exc:
                stats[name].record_transport_error(type(exc).__name__)
                async with bucket_lock:
                    entry = bucket.setdefault(
                        name, {"requests": 0, "errors": 0, "latency_sum": 0.0}
                    )
                    entry["requests"] += 1
                    entry["errors"] += 1
    finally:
        await client.aclose()


async def _flush_buckets(
    sink: JsonlSink,
    bucket: dict[str, Any],
    bucket_lock: asyncio.Lock,
    stop: asyncio.Event,
    bucket_seconds: float,
) -> None:
    """每 ``bucket_seconds`` 写一条按端点的 10 秒桶，便于观察吞吐随时间变化。"""

    window_start = time.time()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=bucket_seconds)
            return
        except asyncio.TimeoutError:
            pass
        async with bucket_lock:
            snapshot = bucket.copy()
            bucket.clear()
        now = time.time()
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "window_seconds": round(now - window_start, 3),
            "endpoints": snapshot,
        }
        window_start = now
        sink.write(record)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    """执行一个压测阶段；返回汇总 dict 并写入磁盘。"""

    args.endpoint_names = [name.strip() for name in args.endpoints.split(",") if name.strip()]
    unknown = [name for name in args.endpoint_names if name not in ENDPOINTS]
    if unknown:
        raise SystemExit(f"未知端点: {unknown}；可选 {sorted(ENDPOINTS)}")
    if "inspection" in args.endpoint_names and not args.inspection_id:
        raise SystemExit("--endpoints 含 inspection 时必须提供 --inspection-id（演示质检 UUID）")
    if "suggestion" in args.endpoint_names and args.phase != "suggestion":
        raise SystemExit("suggestion 端点只能用于 --phase suggestion")
    if args.phase == "suggestion" and args.endpoint_names != ["suggestion"]:
        raise SystemExit("--phase suggestion 只允许 --endpoints suggestion")
    if not DEMO_PASSWORD:
        raise SystemExit("缺少 LOADTEST_PASSWORD 环境变量（公开演示密码，不是秘密）")

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {
        name: EndpointStat(name=name, method=ENDPOINTS[name][0], path=ENDPOINTS[name][1])
        for name in args.endpoint_names
    }
    bucket: dict[str, Any] = {}
    bucket_lock = asyncio.Lock()
    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:  # pragma: no cover - Windows 无 add_signal_handler
            pass

    async with httpx.AsyncClient(
        base_url=args.base_url,
        timeout=httpx.Timeout(args.timeout_seconds),
    ) as control:
        auth = AuthState(control, args.username, DEMO_PASSWORD)
        if any(ENDPOINTS[name][3] for name in args.endpoint_names):
            # 预检登录：凭据错误应立刻失败，而不是让 10 个 worker 各报一次。
            await auth.token()
        model_mode = await _fetch_model_mode(
            control, auth if any(ENDPOINTS[n][3] for n in args.endpoint_names) else None
        )
        before = await _fetch_metrics(control, output_dir / "metrics-before.txt")
        started_at = datetime.now(timezone.utc)
        started = time.perf_counter()

        sink = JsonlSink(output_dir / "buckets.jsonl")
        workers = [
            asyncio.create_task(
                _worker(i, args, stats, auth, stop, bucket, bucket_lock)
            )
            for i in range(args.concurrency)
        ]
        flusher = asyncio.create_task(
            _flush_buckets(sink, bucket, bucket_lock, stop, args.bucket_seconds)
        )

        try:
            await asyncio.wait_for(stop.wait(), timeout=args.duration_seconds)
        except asyncio.TimeoutError:
            stop.set()
        await asyncio.gather(*workers, return_exceptions=True)
        stop.set()
        await flusher
        sink.close()
        elapsed = time.perf_counter() - started
        finished_at = datetime.now(timezone.utc)

        after = await _fetch_metrics(control, output_dir / "metrics-after.txt")
        rows = summarize_endpoints(stats.values(), elapsed)
        summary = build_summary(
            rows=rows,
            elapsed_seconds=elapsed,
            started_at=started_at,
            finished_at=finished_at,
            base_url=args.base_url,
            phase=args.phase,
            concurrency=args.concurrency,
            requested_duration=args.duration_seconds,
            model_mode=model_mode,
            inspection_id=args.inspection_id,
        )
        summary["metrics"] = {"before": before, "after": after}
        summary["username"] = args.username
        summary["endpoints_requested"] = args.endpoint_names
        (output_dir / "summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return summary


def render_table(summary: dict[str, Any]) -> str:
    """控制台表格；和报告里的 Markdown 表用同一份数字。"""

    header = (
        "endpoint        reqs    err   err%    rps     p50ms    p95ms    p99ms"
    )
    lines = [header]
    for row in summary["endpoints"]:
        lines.append(
            "{name:<14} {requests:>6} {errors:>6} {err:>6.2%} {rps:>7.1f} "
            "{p50:>8} {p95:>8} {p99:>8}".format(
                name=row["name"],
                requests=row["requests"],
                errors=row["errors"],
                err=row["error_rate"],
                rps=row["throughput_rps"],
                p50=_fmt(row["p50_ms"]),
                p95=_fmt(row["p95_ms"]),
                p99=_fmt(row["p99_ms"]),
            )
        )
    overall = summary["overall"]
    lines.append(
        "OVERALL        {reqs:>6} {errors:>6} {err:>6.2%} {rps:>7.1f}".format(
            reqs=overall["requests"],
            errors=overall["errors"],
            err=overall["error_rate"],
            rps=overall["throughput_rps"],
        )
    )
    lines.append(
        f"phase={summary['phase']} concurrency={summary['concurrency']} "
        f"elapsed={summary['elapsed_seconds']:.1f}s model_mode={summary['model_mode']}"
    )
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实并发压测（非模型/回放候选答复）")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument(
        "--endpoints",
        default="live,ready,version,inspection",
        help="逗号分隔；live/ready/version/inspection/suggestion",
    )
    parser.add_argument("--inspection-id", default=os.environ.get("LOADTEST_INSPECTION_ID"))
    parser.add_argument(
        "--suggestion-query",
        default=os.environ.get("LOADTEST_SUGGESTION_QUERY", DEFAULT_SUGGESTION_QUERY),
    )
    parser.add_argument(
        "--phase", choices=("non-model", "suggestion"), default="non-model"
    )
    parser.add_argument("--username", default=DEMO_USERNAME)
    parser.add_argument("--bucket-seconds", type=float, default=10.0)
    parser.add_argument("--output-dir", type=Path, default=Path("loadtest-output"))
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover - 交互式中断
        return 130
    print(render_table(summary))
    print(f"summary: {args.output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
