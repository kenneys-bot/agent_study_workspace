"""共享 HTTP 错误响应。

契约 §1：错误结构为 ``{code, message, trace_id, retryable, details}``。
T06 只提供最小实现；T08 在此基础上扩展错误码目录与异常映射。
"""

from typing import Any, Mapping, Optional
from uuid import uuid4

from fastapi import Request
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
) -> JSONResponse:
    """构造契约 §1 的错误响应。"""
    body = ErrorResponse(
        code=code,
        message=message,
        trace_id=resolve_trace_id(request),
        retryable=retryable,
        details=dict(details or {}),
    )
    return JSONResponse(status_code=status_code, content=body.model_dump())


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
