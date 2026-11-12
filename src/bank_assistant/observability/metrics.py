"""轻量指标注册表与 Prometheus 文本渲染（M7-02/T30）。

选择自实现而不是引入 ``prometheus-client``：本项目只有一个 uvicorn worker，
指标量小，且部署机联网安装依赖成本高；自实现让单元测试完全离线、无新增依赖。

约定：

- 指标名与标签值都是**有界**的：HTTP 路由使用路由模板（``/api/v1/reports/{id}``）
  而不是实际路径，避免 UUID 造成标签爆炸；
- 指标只描述聚合状态（计数、耗时、队列深度），不记录用户 id、会话内容、
  trace_id 或任何凭据；
- 抓取失败（数据库不可达时采集队列深度）不会让 ``/metrics`` 失败，而是累加
  ``bank_task_queue_depth_collect_errors_total``；``bank_db_ready`` 如实为 0。

线程安全：uvicorn 的请求处理线程与 /metrics 抓取可能并发，所有更新都在
:class:`threading.Lock` 内完成。
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence

__all__ = [
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "METRICS",
    "collect_task_queue_depth",
    "record_db_probe",
    "reset_metrics",
]

_LabelKey = tuple[str, ...]

HTTP_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    15.0,
    30.0,
)
MODEL_LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 15.0, 30.0, 60.0)


def _escape_label(value: object) -> str:
    text = str(value)
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _label_suffix(names: Sequence[str], values: Mapping[str, object]) -> str:
    if not names:
        return ""
    parts = []
    for name in names:
        if name not in values:
            raise KeyError(f"缺少指标标签 {name!r}")
        parts.append(f'{name}="{_escape_label(values[name])}"')
    return "{" + ",".join(parts) + "}"


class _MetricBase:
    def __init__(
        self,
        registry: "MetricsRegistry",
        name: str,
        help_text: str,
        labelnames: Sequence[str],
        metric_type: str,
    ) -> None:
        self.registry = registry
        self.name = name
        self.help_text = help_text
        self.labelnames = tuple(labelnames)
        self.metric_type = metric_type
        registry._register(self)

    def _key(self, labels: Mapping[str, object] | None) -> _LabelKey:
        supplied = dict(labels or {})
        unexpected = set(supplied) - set(self.labelnames)
        if unexpected:
            raise KeyError(f"未知指标标签 {sorted(unexpected)}")
        return tuple(str(supplied.get(name, "")) for name in self.labelnames)

    def reset(self) -> None:  # pragma: no cover - 由具体子类覆盖
        raise NotImplementedError

    def render(self) -> list[str]:  # pragma: no cover - 由具体子类覆盖
        raise NotImplementedError


class Counter(_MetricBase):
    """单调递增计数器。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, metric_type="counter", **kwargs)
        self._values: dict[_LabelKey, float] = {}

    def inc(self, labels: Mapping[str, object] | None = None, amount: float = 1.0) -> None:
        if amount < 0:
            raise ValueError("counter 只能递增")
        key = self._key(labels)
        with self.registry._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Mapping[str, object] | None = None) -> float:
        with self.registry._lock:
            return self._values.get(self._key(labels), 0.0)

    def reset(self) -> None:
        with self.registry._lock:
            self._values.clear()

    def render(self) -> list[str]:
        with self.registry._lock:
            items = sorted(self._values.items())
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        for key, value in items:
            labels = dict(zip(self.labelnames, key, strict=True))
            lines.append(f"{self.name}{_label_suffix(self.labelnames, labels)} {value:g}")
        return lines


class Gauge(_MetricBase):
    """可增可减的瞬时值。"""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, metric_type="gauge", **kwargs)
        self._values: dict[_LabelKey, float] = {}

    def set(self, value: float, labels: Mapping[str, object] | None = None) -> None:
        key = self._key(labels)
        with self.registry._lock:
            self._values[key] = float(value)

    def inc(self, labels: Mapping[str, object] | None = None, amount: float = 1.0) -> None:
        key = self._key(labels)
        with self.registry._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, labels: Mapping[str, object] | None = None, amount: float = 1.0) -> None:
        self.inc(labels, -amount)

    def value(self, labels: Mapping[str, object] | None = None) -> float:
        with self.registry._lock:
            return self._values.get(self._key(labels), 0.0)

    def reset(self) -> None:
        with self.registry._lock:
            self._values.clear()

    def render(self) -> list[str]:
        with self.registry._lock:
            items = sorted(self._values.items())
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        for key, value in items:
            labels = dict(zip(self.labelnames, key, strict=True))
            lines.append(f"{self.name}{_label_suffix(self.labelnames, labels)} {value:g}")
        return lines


class Histogram(_MetricBase):
    """固定桶直方图；渲染为 Prometheus 的 ``_bucket/_sum/_count``。"""

    def __init__(
        self,
        registry: "MetricsRegistry",
        name: str,
        help_text: str,
        labelnames: Sequence[str],
        buckets: Sequence[float],
    ) -> None:
        if not buckets or list(buckets) != sorted(buckets):
            raise ValueError("buckets 必须非空且升序")
        super().__init__(
            registry,
            name,
            help_text,
            labelnames,
            metric_type="histogram",
        )
        self.buckets = tuple(float(item) for item in buckets)
        # key -> [每个桶的累计计数, sum, count]
        self._values: dict[_LabelKey, list[float]] = {}

    def observe(
        self, value: float, labels: Mapping[str, object] | None = None
    ) -> None:
        if value < 0:
            raise ValueError("histogram 观测值不能为负")
        key = self._key(labels)
        with self.registry._lock:
            state = self._values.setdefault(key, [0.0] * len(self.buckets) + [0.0, 0.0])
            for index, bucket in enumerate(self.buckets):
                if value <= bucket:
                    state[index] += 1
            state[-2] += float(value)
            state[-1] += 1

    def reset(self) -> None:
        with self.registry._lock:
            self._values.clear()

    def render(self) -> list[str]:
        with self.registry._lock:
            items = sorted(self._values.items())
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        for key, state in items:
            labels = dict(zip(self.labelnames, key, strict=True))
            for index, bucket in enumerate(self.buckets):
                bucket_labels = dict(labels)
                bucket_labels["le"] = _format_bucket(bucket)
                lines.append(
                    f"{self.name}_bucket"
                    f"{_label_suffix(self.labelnames + ('le',), bucket_labels)} "
                    f"{state[index]:g}"
                )
            inf_labels = dict(labels)
            inf_labels["le"] = "+Inf"
            lines.append(
                f"{self.name}_bucket"
                f"{_label_suffix(self.labelnames + ('le',), inf_labels)} {state[-1]:g}"
            )
            lines.append(
                f"{self.name}_sum{_label_suffix(self.labelnames, labels)} {state[-2]:g}"
            )
            lines.append(
                f"{self.name}_count{_label_suffix(self.labelnames, labels)} {state[-1]:g}"
            )
        return lines


def _format_bucket(value: float) -> str:
    return f"{value:g}"


class MetricsRegistry:
    """所有指标定义的容器；``render()`` 输出 Prometheus 文本格式。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: list[_MetricBase] = []

    def _register(self, metric: _MetricBase) -> None:
        self._metrics.append(metric)

    def render(self) -> str:
        lines: list[str] = []
        for metric in sorted(self._metrics, key=lambda item: item.name):
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        for metric in self._metrics:
            metric.reset()


METRICS = MetricsRegistry()

HTTP_REQUESTS = Counter(
    METRICS,
    "bank_http_requests_total",
    "HTTP 请求数（按方法、路由模板、状态码）。",
    ("method", "route", "status"),
)
HTTP_LATENCY = Histogram(
    METRICS,
    "bank_http_request_duration_seconds",
    "HTTP 请求耗时（按方法、路由模板）。",
    ("method", "route"),
    HTTP_LATENCY_BUCKETS,
)
HTTP_INFLIGHT = Gauge(
    METRICS,
    "bank_http_inflight_requests",
    "正在处理的 HTTP 请求数。",
    (),
)
RATE_LIMIT_REJECTIONS = Counter(
    METRICS,
    "bank_rate_limit_rejections_total",
    "限流拒绝次数（按 login/expensive 作用域）。",
    ("scope",),
)
INPUT_REJECTIONS = Counter(
    METRICS,
    "bank_input_rejections_total",
    "输入上限拒绝次数（按请求体/会话/提示词）。",
    ("kind",),
)
MODEL_CALLS = Counter(
    METRICS,
    "bank_model_calls_total",
    "模型调用结果（按供应商、模型、状态、运行模式）。",
    ("provider", "model", "status", "mode"),
)
MODEL_TOKENS = Counter(
    METRICS,
    "bank_model_tokens_total",
    "模型 token 用量（kind=prompt/completion）。",
    ("provider", "model", "kind"),
)
MODEL_LATENCY = Histogram(
    METRICS,
    "bank_model_request_duration_seconds",
    "模型调用耗时（按供应商、运行模式）。",
    ("provider", "mode"),
    MODEL_LATENCY_BUCKETS,
)
TASK_EVENTS = Counter(
    METRICS,
    "bank_task_events_total",
    "任务租约事件（claim/reclaim/success/retry/fail/renew/lease_lost/sweep）。",
    ("event", "task_type"),
)
TASK_QUEUE_DEPTH = Gauge(
    METRICS,
    "bank_task_queue_depth",
    "任务队列深度（按任务类型与状态）。",
    ("task_type", "status"),
)
TASK_DEPTH_ERRORS = Counter(
    METRICS,
    "bank_task_queue_depth_collect_errors_total",
    "队列深度采集失败次数（数据库不可达时不伪造深度）。",
    (),
)
DB_READY = Gauge(
    METRICS,
    "bank_db_ready",
    "数据库就绪状态（1=真实 SELECT 1 成功，0=不可用）。",
    (),
)
DB_PROBE_ERRORS = Counter(
    METRICS,
    "bank_db_probe_errors_total",
    "数据库探测失败次数。",
    (),
)


def record_db_probe(ok: bool) -> None:
    """记录一次真实数据库探测；供 /health/ready 与 /metrics 共用。"""
    DB_READY.set(1 if ok else 0)
    if not ok:
        DB_PROBE_ERRORS.inc()


def collect_task_queue_depth(task_type: str = "inspection") -> dict[str, int]:
    """从数据库读取任务队列深度；失败时如实记错误并返回空字典。

    不在这里抛异常：``/metrics`` 必须在数据库故障时仍可抓取，用
    ``bank_db_ready=0`` 与 ``bank_task_queue_depth_collect_errors_total``
    表达依赖故障，而不是伪造 ``depth=0``。
    """
    try:
        from sqlalchemy import func, select

        from bank_assistant.infra.db import get_engine
        from bank_assistant.infra.models import Task

        with get_engine().connect() as connection:
            rows = connection.execute(
                select(Task.status, func.count())
                .where(Task.type == task_type)
                .group_by(Task.status)
            ).all()
        return {str(status): int(count) for status, count in rows}
    except Exception:
        TASK_DEPTH_ERRORS.inc()
        return {}


def reset_metrics() -> None:
    """测试专用：清空所有样本（不删除指标定义）。"""
    METRICS.reset()
