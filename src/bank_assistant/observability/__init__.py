"""监控、结构化日志与安全观测（M7-02/T30）。

模块边界：

- :mod:`bank_assistant.observability.metrics`：进程内轻量指标注册表与
  Prometheus 文本渲染；不引入额外依赖，单进程部署（uvicorn 一个 worker）
  下足够用。
- :mod:`bank_assistant.observability.http`：请求级结构化日志与 HTTP 指标
  中间件；只记录方法、路由模板、状态码、耗时与 trace_id，绝不记录请求体、
  查询串、令牌或会话原文。
- :mod:`bank_assistant.observability.redaction`：日志脱敏过滤器，兜底清除
  连接串密码、API key、Bearer 令牌等已知秘密形态。

本模块只做观测，不改变业务状态；指标失败（例如数据库不可达导致队列深度
采集失败）不能让抓取端点失败。
"""

from bank_assistant.observability.metrics import (
    METRICS,
    collect_task_queue_depth,
    record_db_probe,
    reset_metrics,
)
from bank_assistant.observability.redaction import RedactingFilter, redact_log_text

__all__ = [
    "METRICS",
    "RedactingFilter",
    "collect_task_queue_depth",
    "record_db_probe",
    "redact_log_text",
    "reset_metrics",
]
