"""任务 API 的请求/响应模型（契约 §2 Task、§5 GET /tasks/{id}）。

任务表是通用队列：本票只证明幂等键与租约契约，不承载质检业务字段。
``payload_ref`` 指向未来 M4 的业务载荷（例如会话），响应只暴露引用与状态，
不回传载荷正文。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# 任务类型由服务端定义；这里只约束格式，具体类型在 M4 注册业务处理器。


class TaskCreate(BaseModel):
    """``POST /tasks`` 输入；不接受客户端指定状态、租约或结果字段。"""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$",
        description="任务类型（由服务端定义）",
    )
    payload_ref: str | None = Field(
        default=None,
        min_length=1,
        max_length=512,
        description="业务载荷的引用，不在此接口内联上传正文",
    )


class TaskResponse(BaseModel):
    """任务的对外表示；不包含 payload 正文。"""

    model_config = ConfigDict(from_attributes=True)

    task_id: UUID
    type: str
    status: str
    idempotency_key: str | None = None
    payload_ref: str | None = None
    attempts: int
    result_ref: str | None = None
    error_code: str | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
