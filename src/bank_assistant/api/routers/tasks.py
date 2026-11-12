"""通用任务端点：``POST /tasks`` 与 ``GET /tasks/{id}``（契约 §5，M2-05）。

本票只提供任务契约的最小证明，不含任何质检业务逻辑：M4 会在
``TaskQueue`` 之上挂真实检查处理器，并按任务 ``payload_ref`` 取业务载荷。

``POST /tasks`` 要求 ``Idempotency-Key``：

- 首次使用 → 201，创建 queued 任务；
- 相同键 + 相同输入 → 200，返回第一次的响应体，且带
  ``Idempotent-Replayed: true``；
- 相同键 + 不同输入 → 409 ``IDEMPOTENCY_KEY_REUSED``。

任务可见性按 ``scope_id`` 判定：跨范围读取与不存在的任务都返回同一个 404，
不泄露资源是否存在。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.api.models.task import TaskCreate, TaskResponse
from bank_assistant.infra.db import get_db_session, session_scope
from bank_assistant.infra.idempotency import (
    IdempotencyConflict,
    canonical_request_hash,
    execute_idempotent,
)
from bank_assistant.infra.models import Task
from bank_assistant.infra.task_queue import TaskStatus
from bank_assistant.security.authorization import (
    ensure_resource_visible,
    get_current_identity,
)
from bank_assistant.security.identity import Identity

router = APIRouter(tags=["tasks"])

# 幂等绑定维度之一；用路由模板而不是实际前缀，避免 API 版本前缀变化改变绑定。
TASKS_ENDPOINT = "POST /tasks"


def _create_queued_task(
    session: Session,
    payload: TaskCreate,
    identity: Identity,
    idempotency_key: str,
) -> tuple[UUID, dict[str, object]]:
    """创建 queued 任务并返回响应快照；由首次幂等调用执行一次。"""
    task = Task(
        type=payload.type,
        status=TaskStatus.QUEUED.value,
        idempotency_key=idempotency_key,
        payload_ref=payload.payload_ref,
        scope_id=identity.scope_id,
    )
    session.add(task)
    session.flush()
    snapshot = TaskResponse.model_validate(task).model_dump(mode="json")
    return task.task_id, snapshot


@router.post("/tasks", status_code=201)
def create_task(
    payload: TaskCreate,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    identity: Identity = Depends(get_current_identity),
) -> JSONResponse:
    """创建 queued 任务；重复请求复用原任务（CT-05/CT-06）。"""
    request_hash = canonical_request_hash(payload)
    try:
        with session_scope() as session:
            outcome = execute_idempotent(
                session,
                user_id=identity.user_id,
                endpoint=TASKS_ENDPOINT,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                create=lambda: _create_queued_task(
                    session, payload, identity, idempotency_key
                ),
            )
    except IdempotencyConflict:
        raise ApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一幂等键已用于不同输入",
        ) from None

    return JSONResponse(
        status_code=200 if outcome.replayed else 201,
        content=outcome.response_snapshot,
        headers={"Idempotent-Replayed": "true"} if outcome.replayed else None,
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def read_task(
    task_id: UUID,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> TaskResponse:
    """返回任务状态与结果引用；跨范围与不存在统一 404。"""
    task = session.get(Task, task_id)
    ensure_resource_visible(task, identity)
    return TaskResponse.model_validate(task)
