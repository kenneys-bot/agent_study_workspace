"""共享 HTTP 错误响应。

契约 §1：错误结构为 ``{code, message, trace_id, retryable, details}``。
M2-02 增加 ``ApiError``：认证/授权代码抛业务错误，由统一处理器映射为契约
结构，避免依赖 FastAPI 默认的 ``{"detail": ...}`` 形状。
"""

from typing import Any, Mapping, Optional
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """契约 §1 规定的错误响应体。"""

    code: str = Field(..., description="稳定错误码")
    message: str = Field(..., description="面向调用方的错误说明")
    trace_id: str = Field(..., description="请求追踪 ID")
    retryable: bool = Field(..., description="是否可重试")
    details: dict[str, Any] = Field(default_factory=dict, description="安全诊断信息")


def resolve_trace_id(request: Optional[Request] = None) -> str:
    """优先沿用调用方或中间件给出的 trace_id，否则生成新的。"""
    if request is not None:
        trace_id = request.headers.get("x-trace-id")
        if trace_id:
            return trace_id
        state_trace_id = getattr(request.state, "trace_id", None)
        if state_trace_id:
            return str(state_trace_id)
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
    """构造契约 §1 的错误响应。"""
    body = ErrorResponse(
        code=code,
        message=message,
        trace_id=resolve_trace_id(request),
        retryable=retryable,
        details=dict(details or {}),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers=dict(headers or {}),
    )


class ApiError(Exception):
    """业务错误：状态码、稳定错误码与安全诊断信息。

    ``details`` 只放安全信息，不能包含密码、令牌、SQL 或堆栈。
    """

    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: Optional[Mapping[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = dict(details or {})
        self.headers = dict(headers or {})


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    """把 ``ApiError`` 渲染成契约 §1 的错误结构。"""
    return error_response(
        status_code=exc.status_code,
        code=exc.code,
        message=exc.message,
        request=request,
        retryable=exc.retryable,
        details=exc.details,
        headers=exc.headers,
    )


def install_error_handlers(app: FastAPI) -> None:
    """在任意 FastAPI 应用上安装统一错误处理器（主应用与测试探针共用）。"""
    app.add_exception_handler(ApiError, api_error_handler)


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
