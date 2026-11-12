"""质检路由（契约 §5 ``POST /inspections`` / ``GET /inspections/{id}``，T18/M4-03）。

- ``POST /inspections`` 要求认证与 ``Idempotency-Key``：首次创建返回 202 +
  ``inspection_id`` + ``task_id``；相同键 + 相同输入重放 202（带
  ``Idempotent-Replayed: true``）；相同键 + 不同输入 409（CT-05/CT-06）。
  质检只创建 queued 运行与租约任务，不在这里调用模型。
- ``GET /inspections/{id}`` 返回状态、逐规则判断、证据与失败原因；不存在与
  跨范围统一 404，不泄露存在性。
- 权限来自服务端身份；会话与规则的 ``scope_id`` 都在服务层校验。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.api.models.inspections import (
    InspectionAccepted,
    InspectionCreateRequest,
    InspectionDetail,
    to_accepted,
    to_detail,
)
from bank_assistant.infra.db import get_db_session, session_scope
from bank_assistant.infra.idempotency import (
    IdempotencyConflict,
    canonical_request_hash,
    execute_idempotent,
)
from bank_assistant.inspections.service import (
    InspectionInputError,
    InspectionNotFound,
    create_inspection,
    load_inspection,
)
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity

router = APIRouter(tags=["质检"])

# 幂等绑定维度之一；用路由模板而不是实际前缀，避免 API 版本前缀变化改变绑定。
INSPECTIONS_ENDPOINT = "POST /inspections"


def _not_found() -> ApiError:
    return ApiError(status_code=404, code="NOT_FOUND", message="质检不存在")


def _input_error(exc: InspectionInputError) -> ApiError:
    return ApiError(
        status_code=422,
        code=exc.code,
        message=str(exc),
        retryable=False,
    )


@router.post(
    "/inspections",
    response_model=InspectionAccepted,
    status_code=202,
)
def create_inspection_endpoint(
    payload: InspectionCreateRequest,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
    identity: Identity = Depends(get_current_identity),
) -> JSONResponse:
    """创建一次质检（202）；模型调用由独立 worker 完成。"""
    request_hash = canonical_request_hash(payload)
    try:
        with session_scope() as session:
            outcome = execute_idempotent(
                session,
                user_id=identity.user_id,
                endpoint=INSPECTIONS_ENDPOINT,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                create=lambda: _create(session, identity, payload, idempotency_key),
                response_status=202,
            )
    except IdempotencyConflict:
        raise ApiError(
            status_code=409,
            code="IDEMPOTENCY_KEY_REUSED",
            message="同一幂等键已用于不同输入",
        ) from None
    except InspectionNotFound:
        raise _not_found() from None
    except InspectionInputError as exc:
        raise _input_error(exc) from None

    return JSONResponse(
        status_code=202,
        content=outcome.response_snapshot,
        headers={"Idempotent-Replayed": "true"} if outcome.replayed else None,
    )


def _create(
    session: Session,
    identity: Identity,
    payload: InspectionCreateRequest,
    idempotency_key: str,
) -> tuple[UUID, dict[str, object]]:
    """幂等键首次命中时执行：创建运行 + 任务并返回响应快照。"""
    run, task = create_inspection(
        session,
        identity,
        conversation_id=payload.conversation_id,
        business_type=payload.business_type,
        rule_ids=payload.rule_ids,
        idempotency_key=idempotency_key,
    )
    return run.inspection_id, to_accepted(run, task).model_dump(mode="json")


@router.get("/inspections/{inspection_id}", response_model=InspectionDetail)
def read_inspection_endpoint(
    inspection_id: UUID,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> InspectionDetail:
    """返回状态、逐规则判断、证据与失败原因；跨范围与不存在都是 404。"""
    try:
        view = load_inspection(session, identity, inspection_id)
    except InspectionNotFound:
        raise _not_found() from None
    return to_detail(view)


__all__ = [
    "INSPECTIONS_ENDPOINT",
    "create_inspection_endpoint",
    "read_inspection_endpoint",
    "router",
]
