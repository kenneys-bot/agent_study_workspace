"""输入上限：请求体大小、会话原文与提示词长度（M7-03/T30）。

策略是**显式拒绝或显式截断**，不静默丢弃：

- 请求体超过 ``MAX_REQUEST_BYTES`` → 413 ``REQUEST_TOO_LARGE``，在进入路由前
  就拒绝，避免为超大 JSON 分配内存；
- 会话原文超过 ``MAX_CONVERSATION_CHARS`` → 413 ``INPUT_TOO_LARGE``，不能截断
  后当成完整会话（证据坐标与质检结论都会因此失真）；
- 候选答复问题超过 ``MAX_PROMPT_CHARS`` → 422（入口 schema 校验）；
- 内部构造模型提示词时再用 :func:`truncate_text` 做一次防御性截断（带可见
  截断标记），保证任何调用路径都不会把无界文本塞给模型。

所有上限都来自 :mod:`bank_assistant.config.settings`，可经环境变量配置。
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Receive, Scope, Send

from bank_assistant.config.settings import settings
from bank_assistant.observability.metrics import INPUT_REJECTIONS

__all__ = [
    "InputTooLarge",
    "RequestSizeLimitMiddleware",
    "ensure_text_limit",
    "truncate_text",
]

logger = logging.getLogger(__name__)

TRUNCATION_MARKER = "…[已截断]"


class InputTooLarge(ValueError):
    """输入超过配置上限；由路由映射为显式 413。"""

    def __init__(self, *, kind: str, limit: int, actual: int) -> None:
        self.kind = kind
        self.limit = limit
        self.actual = actual
        super().__init__(f"{kind} 超过 {limit} 字符上限（实际 {actual}）")


def truncate_text(text: str, limit: int, *, marker: str = TRUNCATION_MARKER) -> str:
    """把文本截到 ``limit`` 个码点以内，并留下可见标记。"""
    if limit <= 0:
        raise ValueError("limit 必须为正整数")
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(marker))
    return text[:keep] + marker


def ensure_text_limit(text: str | None, *, kind: str, limit: int) -> None:
    """超过上限时抛 :class:`InputTooLarge`；``None`` 视为未提供。"""
    if text is not None and len(text) > limit:
        raise InputTooLarge(kind=kind, limit=limit, actual=len(text))


def _content_length(scope: Scope) -> int:
    for raw_name, raw_value in scope.get("headers") or []:
        if raw_name.lower() == b"content-length":
            try:
                return max(0, int(raw_value.decode("latin-1")))
            except (ValueError, UnicodeDecodeError):
                return 0
    return 0


class RequestSizeLimitMiddleware:
    """按 ``Content-Length`` 拒绝超大请求体；不读取 body。

    没有 ``Content-Length`` 的分块请求不在这里拒绝，交给各字段上限与解析器；
    这避免为了量尺寸而把整个 body 读进内存。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = _content_length(scope)
        limit = settings.MAX_REQUEST_BYTES
        if length > limit:
            INPUT_REJECTIONS.inc({"kind": "request_body"})
            logger.warning(
                "request_too_large trace_id=%s method=%s route=%s bytes=%d limit=%d",
                scope.get("state", {}).get("trace_id", ""),
                scope.get("method", ""),
                getattr(scope.get("route"), "path", "unmatched"),
                length,
                limit,
            )
            # 延迟导入，避免 observability → api.errors → model_gateway 的循环。
            from starlette.requests import Request

            from bank_assistant.api.errors import error_response

            response = error_response(
                status_code=413,
                code="REQUEST_TOO_LARGE",
                message=f"请求体超过 {limit} 字节上限",
                request=Request(scope),
                retryable=False,
                details={"limit_bytes": limit},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

