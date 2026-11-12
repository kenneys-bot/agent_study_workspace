"""HTTP 观测中间件：结构化请求日志 + 请求计数/耗时（M7-02/T30）。

日志是**单行 JSON**（``event=http_request``），字段固定且不含请求体：

``{"event":"http_request","trace_id":"...","method":"GET","route":"/health/live",
"status":200,"duration_ms":1.23,"request_bytes":0,"user_id":null}``

安全约束：

- 不记录请求体、查询串、Authorization 头、cookie 或会话原文；
- 路由用**路由模板**而不是实际路径，避免 UUID/用户输入进入日志与指标标签；
- 未匹配路由统一记为 ``route=unmatched``，不把任意 404 路径写进指标。

中间件必须放在 :class:`bank_assistant.api.errors.TraceIdMiddleware` 内层，
这样 ``scope["state"]["trace_id"]`` 已存在；异常时先记 500 日志再向上抛，
由 TraceIdMiddleware 输出契约 §1 的 500 响应。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bank_assistant.observability.metrics import (
    HTTP_INFLIGHT,
    HTTP_LATENCY,
    HTTP_REQUESTS,
)

logger = logging.getLogger("bank_assistant.access")

__all__ = ["RequestObservabilityMiddleware", "route_template"]


def _route_template(scope: Scope) -> str:
    """返回路由模板；未匹配路由归入 ``unmatched``，控制标签基数。"""
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    return "unmatched"


def _request_bytes(scope: Scope) -> int:
    """只读取 Content-Length 头；绝不读取/记录 body。"""
    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name.lower() == b"content-length":
            try:
                return max(0, int(raw_value.decode("latin-1")))
            except (ValueError, UnicodeDecodeError):
                return 0
    return 0


class RequestObservabilityMiddleware:
    """记录 HTTP 指标与结构化访问日志。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        state = scope.setdefault("state", {})
        method = str(scope.get("method", "GET"))
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message.get("status", 500))
            await send(message)

        HTTP_INFLIGHT.inc()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # 异常路径：TraceIdMiddleware 会输出 500；这里如实记一次失败请求。
            status_code = 500
            raise
        finally:
            HTTP_INFLIGHT.dec()
            duration = time.perf_counter() - started
            route = _route_template(scope)
            HTTP_REQUESTS.inc(
                {"method": method, "route": route, "status": str(status_code)}
            )
            HTTP_LATENCY.observe(duration, {"method": method, "route": route})
            payload: dict[str, Any] = {
                "event": "http_request",
                "trace_id": str(state.get("trace_id") or ""),
                "method": method,
                "route": route,
                "status": status_code,
                "duration_ms": round(duration * 1000, 2),
                "request_bytes": _request_bytes(scope),
                "user_id": state.get("user_id"),
            }
            logger.info(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
