"""共享 HTTP 错误契约与请求追踪。

契约 §1：错误结构固定为 ``{code, message, trace_id, retryable, details}``。

- 所有端点（含未捕获异常、参数校验失败、404、未实现接口）都返回该结构；
- ``details`` 只放安全诊断信息：不回传堆栈、原始异常文本、SQL、凭据或用户正文；
- 每个请求都有 ``trace_id``：优先沿用 ``x-trace-id``，否则生成，并在响应头回显；
- 4xx/409/422 映射到稳定错误码；依赖失败映射为 502/503/504；
- 模型网关的失败分类（超时/限流/不可用/结构非法/空内容）由
  :func:`model_failure_error` 转成显式 HTTP 失败，而不是 200 带失败状态。

T08 只建立错误与 trace 管道，不实现认证（#11/T10）。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping, Optional
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from bank_assistant.core.errors import CapabilityUnavailableError
from bank_assistant.core.model_gateway import ModelCallStatus, ModelResult

logger = logging.getLogger(__name__)

TRACE_ID_HEADER = "x-trace-id"
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

DEFAULT_CODE_BY_STATUS: Mapping[int, str] = {
    400: "INVALID_INPUT",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    501: "NOT_IMPLEMENTED",
    502: "DEPENDENCY_BAD_RESPONSE",
    503: "DEPENDENCY_UNAVAILABLE",
    504: "DEPENDENCY_TIMEOUT",
}

DEFAULT_MESSAGE_BY_STATUS: Mapping[int, str] = {
    400: "请求无效",
    401: "未认证：请先登录",
    403: "无权限访问该资源",
    404: "资源不存在或不可见",
    409: "资源状态或版本冲突",
    422: "请求参数校验失败",
    429: "请求过于频繁，请稍后重试",
    500: "服务内部错误",
    501: "功能尚未实现",
    502: "依赖服务返回异常",
    503: "依赖服务暂不可用",
    504: "依赖服务超时",
}

# 只有瞬时性的依赖/限流失败才标记为可重试。
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})


class ErrorResponse(BaseModel):
    """契约 §1 规定的错误响应体。"""

    code: str = Field(..., description="稳定错误码")
    message: str = Field(..., description="面向调用方的错误说明")
    trace_id: str = Field(..., description="请求追踪 ID")
    retryable: bool = Field(..., description="是否可重试")
    details: dict[str, Any] = Field(default_factory=dict, description="安全诊断信息")


class ApiError(Exception):
    """显式业务/协议错误；由全局处理器渲染为契约 §1 的错误响应。"""

    def __init__(
        self,
        *,
        status_code: int,
        message: Optional[str] = None,
        code: Optional[str] = None,
        retryable: Optional[bool] = None,
        details: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.status_code = status_code
        self.code = code or default_error_code(status_code)
        self.message = message or default_error_message(status_code)
        self.retryable = (
            status_code in RETRYABLE_STATUSES if retryable is None else retryable
        )
        self.details = dict(details or {})
        # 认证错误需要带 WWW-Authenticate；其余错误通常为空。
        self.headers = dict(headers or {})
        super().__init__(self.message)


def default_error_code(status_code: int) -> str:
    """返回稳定错误码；未知状态归入 INTERNAL_ERROR / HTTP_ERROR。"""
    if status_code in DEFAULT_CODE_BY_STATUS:
        return DEFAULT_CODE_BY_STATUS[status_code]
    return "INTERNAL_ERROR" if status_code >= 500 else "HTTP_ERROR"


def default_error_message(status_code: int) -> str:
    """返回不会泄露内部细节的默认说明。"""
    return DEFAULT_MESSAGE_BY_STATUS.get(status_code, "请求失败")


def _normalize_trace_id(value: Optional[str]) -> Optional[str]:
    """只接受短且字符集安全的 trace id；非法值一律重新生成。"""
    if not value:
        return None
    candidate = value.strip()
    return candidate if _TRACE_ID_PATTERN.match(candidate) else None


def resolve_trace_id(request: Optional[Request] = None) -> str:
    """优先沿用中间件写入的 trace_id，其次请求头，否则生成新的。"""
    if request is not None:
        state_trace_id = getattr(request.state, "trace_id", None)
        if state_trace_id:
            return str(state_trace_id)
        header_trace_id = _normalize_trace_id(request.headers.get(TRACE_ID_HEADER))
        if header_trace_id:
            return header_trace_id
    return uuid4().hex


def error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    request: Optional[Request] = None,
    retryable: bool = False,
    details: Optional[Mapping[str, Any]] = None,
    headers: Optional[Mapping[str, str]] = None,
) -> JSONResponse:
    """构造契约 §1 的错误响应，并在响应头回显 trace_id。"""
    trace_id = resolve_trace_id(request)
    body = ErrorResponse(
        code=code,
        message=message,
        trace_id=trace_id,
        retryable=retryable,
        details=dict(details or {}),
    )
    response_headers = dict(headers or {})
    response_headers[TRACE_ID_HEADER] = trace_id
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=response_headers,
    )


def not_implemented_response(
    request: Optional[Request],
    *,
    operation: str,
    message: str,
) -> JSONResponse:
    """明确报告“尚未实现”，避免用假数据伪装成功。"""
    return error_response(
        status_code=501,
        code="NOT_IMPLEMENTED",
        message=message,
        request=request,
        retryable=False,
        details={"operation": operation},
    )


class TraceIdMiddleware:
    """为每个 HTTP 请求确定 trace_id，并回显到响应头。

    同时兜底未捕获异常：返回契约 §1 的 500 结构（不泄露异常文本），
    避免 FastAPI/Starlette 默认的纯文本 500。
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        raw_trace_id = headers.get(TRACE_ID_HEADER.encode("latin-1"))
        trace_id = _normalize_trace_id(
            raw_trace_id.decode("latin-1") if raw_trace_id else None
        ) or uuid4().hex
        scope.setdefault("state", {})["trace_id"] = trace_id

        response_started = False

        async def send_with_trace_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                raw_headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != TRACE_ID_HEADER.encode("latin-1")
                ]
                raw_headers.append(
                    (TRACE_ID_HEADER.encode("latin-1"), trace_id.encode("latin-1"))
                )
                message = {**message, "headers": raw_headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace_id)
        except Exception:
            logger.exception(
                "unhandled_exception trace_id=%s path=%s",
                trace_id,
                scope.get("path", ""),
            )
            if response_started:
                raise
            response = error_response(
                status_code=500,
                code="INTERNAL_ERROR",
                message=default_error_message(500),
                request=Request(scope),
                retryable=False,
            )
            await response(scope, receive, send)


# 模型失败分类 → HTTP 状态/稳定错误码。502/503/504 表示依赖失败，
# 429 表示上游限流；都不得以 200 + 失败状态返回。
_MODEL_FAILURE_SPECS: Mapping[ModelCallStatus, tuple[int, str, str]] = {
    ModelCallStatus.TIMEOUT: (504, "MODEL_TIMEOUT", "模型调用超时"),
    ModelCallStatus.RATE_LIMITED: (429, "MODEL_RATE_LIMITED", "模型调用被限流"),
    ModelCallStatus.PROVIDER_UNAVAILABLE: (
        503,
        "MODEL_UNAVAILABLE",
        "模型服务暂不可用",
    ),
    ModelCallStatus.INVALID_STRUCTURE: (
        502,
        "MODEL_INVALID_STRUCTURE",
        "模型返回内容不符合约定结构",
    ),
    ModelCallStatus.EMPTY_CONTENT: (
        502,
        "MODEL_EMPTY_CONTENT",
        "模型返回空内容",
    ),
}


def model_failure_error(result: ModelResult) -> ApiError:
    """把模型网关的失败结果转成显式 HTTP 错误（CT-04）。

    ``details`` 只保留模型状态、供应商与模型标识，不包含上游原始错误文本。
    成功结果不允许转成错误，调用方应直接使用 ``result``。
    """
    if result.ok:
        raise ValueError("model_failure_error 只接受失败结果")
    status_code, code, message = _MODEL_FAILURE_SPECS.get(
        result.status, (503, "MODEL_UNAVAILABLE", "模型服务暂不可用")
    )
    details: dict[str, Any] = {"model_status": result.status.value}
    if result.provider is not None:
        details["provider"] = result.provider.value
    if result.model:
        details["model"] = result.model
    return ApiError(
        status_code=status_code,
        code=code,
        message=message,
        retryable=True,
        details=details,
    )


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request=request,
        retryable=exc.retryable,
        details=exc.details,
        headers=exc.headers,
    )


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """把 FastAPI/Starlette 的 HTTPException 统一进契约结构。

    5xx 的 detail 可能来自 ``str(exception)``，一律丢弃；4xx 的 detail
    视为调用方可见的说明。
    """
    status_code = exc.status_code
    if status_code >= 500:
        logger.warning(
            "http_exception trace_id=%s status=%s detail=%s",
            resolve_trace_id(request),
            status_code,
            exc.detail,
        )
        message = default_error_message(status_code)
        details: dict[str, Any] = {}
    else:
        message = (
            exc.detail
            if isinstance(exc.detail, str) and exc.detail
            else default_error_message(status_code)
        )
        details = {}
    # content-length 由 JSONResponse 重新计算，不能沿用原异常头。
    safe_headers = {
        name: value
        for name, value in (exc.headers or {}).items()
        if name.lower() != "content-length"
    }
    return error_response(
        status_code=status_code,
        code=default_error_code(status_code),
        message=message,
        request=request,
        retryable=status_code in RETRYABLE_STATUSES,
        details=details,
        headers=safe_headers,
    )


async def _database_unavailable_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """数据库/连接池不可用 → 503，不把连接细节或 SQL 回给调用方（CT-04/可恢复性）。

    只注册连接级异常（OperationalError/InterfaceError/pool Timeout）；完整性约束
    等业务型 DBAPI 错误仍走 500，避免把可以修复的输入问题伪装成依赖故障。
    """
    logger.warning(
        "database_unavailable trace_id=%s error=%s",
        resolve_trace_id(request),
        type(exc).__name__,
    )
    return error_response(
        status_code=503,
        code="DEPENDENCY_UNAVAILABLE",
        message="数据库暂不可用",
        request=request,
        retryable=True,
        details={"dependency": "postgresql"},
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422：只返回字段位置/类型/可读原因，不回传原始输入。"""
    errors = [
        {
            "loc": ".".join(str(part) for part in error.get("loc", ())),
            "type": str(error.get("type", "")),
            "msg": str(error.get("msg", "")),
        }
        for error in exc.errors()
    ]
    return error_response(
        status_code=422,
        code="VALIDATION_ERROR",
        message=default_error_message(422),
        request=request,
        retryable=False,
        details={"errors": errors},
    )


async def _capability_unavailable_handler(
    request: Request, exc: CapabilityUnavailableError
) -> JSONResponse:
    """能力缺失（凭据/SDK/端点）明确失败，但不把配置细节回给调用方。"""
    logger.warning(
        "capability_unavailable trace_id=%s reason=%s",
        resolve_trace_id(request),
        exc,
    )
    return error_response(
        status_code=503,
        code="CAPABILITY_UNAVAILABLE",
        message="所需能力当前不可用：请检查服务配置",
        request=request,
        retryable=False,
        details={},
    )


def install_error_handlers(app: FastAPI) -> None:
    """在应用上安装统一的异常处理器。

    未捕获异常由 :class:`TraceIdMiddleware` 兜底，因此这里不注册
    ``Exception`` 处理器（Starlette 会把它放到中间件栈最外层，响应反而
    绕过 trace 中间件）。
    """
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(
        CapabilityUnavailableError, _capability_unavailable_handler
    )
    # 依赖不可用必须显式 503，不能 200 + 假成功，也不能把连接串/SQL 回给调用方。
    app.add_exception_handler(OperationalError, _database_unavailable_handler)
    app.add_exception_handler(InterfaceError, _database_unavailable_handler)
    app.add_exception_handler(SATimeoutError, _database_unavailable_handler)


__all__ = [
    "ApiError",
    "ErrorResponse",
    "TRACE_ID_HEADER",
    "TraceIdMiddleware",
    "default_error_code",
    "default_error_message",
    "error_response",
    "install_error_handlers",
    "model_failure_error",
    "not_implemented_response",
    "resolve_trace_id",
]
