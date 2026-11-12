"""人工复核路由（契约 §5 ``/reviews``，M4-04/T20，#21）。

| 方法与路径 | 作用 |
|---|---|
| POST /reviews | 为已完成的质检创建复核（同一质检已有未完成复核 → 409） |
| GET /reviews | 按状态/指派/质检过滤的分页列表 |
| GET /reviews/{id} | 原机器判断 + 人工决定历史 + 完成门禁事实 + revision |
| POST /reviews/{id}/decisions | 追加逐项 confirm/reject/modify（理由必填） |
| POST /reviews/{id}/complete | 完成复核（适用规则必须都有结论） |
| POST /reviews/{id}/reopen | 显式重新打开（新 revision + 审计） |

并发语义：决定/完成/重开都要求 ``expected_revision``；过期返回 409 并在
``details.current_revision`` 带上当前版本。互斥由 PostgreSQL 行锁保证，
不依赖进程内锁（CT-07）。跨范围资源与不存在资源统一 404，不泄露存在性。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError, resolve_trace_id
from bank_assistant.api.models.reviews import (
    ReviewCompleteRequest,
    ReviewCreateRequest,
    ReviewDecisionAccepted,
    ReviewDecisionRequest,
    ReviewDetail,
    ReviewList,
    ReviewReopenRequest,
    ReviewStatusLiteral,
    to_accepted,
    to_detail,
    to_list,
)
from bank_assistant.infra.db import get_db_session, session_scope
from bank_assistant.inspections.service import InspectionNotFound
from bank_assistant.reviews.schema import ReviewInputError, ReviewStateError
from bank_assistant.reviews.service import (
    ReviewConflict,
    ReviewNotFound,
    build_review_view,
    complete_review,
    create_review,
    get_review,
    list_reviews,
    reopen_review,
    submit_decision,
)
from bank_assistant.security.authorization import get_current_identity, require_role
from bank_assistant.security.identity import Role
from bank_assistant.security.identity import Identity

router = APIRouter(prefix="/reviews", tags=["人工复核"])

# 审核分离：写入复核（创建/决定/完成/重开）只允许复核员与管理员；
# 读取列表/详情仍按 scope 授权，便于坐席查看与自己相关的复核。
_REVIEWER_ROLES = (Role.REVIEWER, Role.ADMIN)


def _not_found() -> ApiError:
    return ApiError(status_code=404, code="NOT_FOUND", message="复核不存在")


def _input_error(exc: ReviewInputError) -> ApiError:
    return ApiError(
        status_code=422,
        code=exc.code,
        message=str(exc),
        retryable=False,
    )


def _conflict(exc: ReviewConflict) -> ApiError:
    details: dict[str, object] = dict(exc.details)
    if exc.current_revision is not None:
        details["current_revision"] = exc.current_revision
    return ApiError(
        status_code=409,
        code=exc.code,
        message=exc.message,
        retryable=False,
        details=details,
    )


@router.post(
    "",
    response_model=ReviewDetail,
    status_code=201,
    dependencies=[Depends(require_role(*_REVIEWER_ROLES))],
)
def create_review_endpoint(
    payload: ReviewCreateRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> ReviewDetail:
    """创建复核；同一质检已有未完成复核时 409。"""
    trace_id = resolve_trace_id(request)
    try:
        with session_scope() as session:
            review = create_review(
                session,
                identity,
                inspection_id=payload.inspection_id,
                assignee_id=payload.assignee_id,
                trace_id=trace_id,
            )
            return to_detail(build_review_view(session, identity, review))
    except (ReviewNotFound, InspectionNotFound):
        raise _not_found() from None
    except ReviewConflict as exc:
        raise _conflict(exc) from None
    except ReviewInputError as exc:
        raise _input_error(exc) from None


@router.get("", response_model=ReviewList)
def list_reviews_endpoint(
    status: ReviewStatusLiteral | None = Query(default=None),
    assignee_id: UUID | None = Query(default=None),
    inspection_id: UUID | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> ReviewList:
    """按状态/指派/质检过滤；只返回当前业务范围内的复核。"""
    page = list_reviews(
        session,
        identity,
        status=status,
        assignee_id=assignee_id,
        inspection_id=inspection_id,
        limit=limit,
        offset=offset,
    )
    return to_list(page)


@router.get("/{review_id}", response_model=ReviewDetail)
def read_review_endpoint(
    review_id: UUID,
    identity: Identity = Depends(get_current_identity),
    session: Session = Depends(get_db_session),
) -> ReviewDetail:
    """返回原机器判断、人工决定历史与 revision；跨范围与不存在都是 404。"""
    try:
        view = get_review(session, identity, review_id)
    except (ReviewNotFound, InspectionNotFound):
        raise _not_found() from None
    return to_detail(view)


@router.post(
    "/{review_id}/decisions",
    response_model=ReviewDecisionAccepted,
    status_code=201,
    dependencies=[Depends(require_role(*_REVIEWER_ROLES))],
)
def submit_decision_endpoint(
    review_id: UUID,
    payload: ReviewDecisionRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> ReviewDecisionAccepted:
    """追加一条逐项结论；成功返回加一后的 revision（409 表示版本/状态冲突）。"""
    trace_id = resolve_trace_id(request)
    try:
        with session_scope() as session:
            review, decision = submit_decision(
                session,
                identity,
                review_id,
                finding_id=payload.finding_id,
                action=payload.action,
                final_verdict=payload.final_verdict,
                reason=payload.reason,
                expected_revision=payload.expected_revision,
                trace_id=trace_id,
            )
            return to_accepted(review, decision)
    except (ReviewNotFound, InspectionNotFound):
        raise _not_found() from None
    except ReviewConflict as exc:
        raise _conflict(exc) from None
    except ReviewInputError as exc:
        raise _input_error(exc) from None


@router.post(
    "/{review_id}/complete",
    response_model=ReviewDetail,
    dependencies=[Depends(require_role(*_REVIEWER_ROLES))],
)
def complete_review_endpoint(
    review_id: UUID,
    payload: ReviewCompleteRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> ReviewDetail:
    """完成复核；缺结论或未确认的「待业务确认」返回 409。"""
    trace_id = resolve_trace_id(request)
    try:
        with session_scope() as session:
            view = complete_review(
                session,
                identity,
                review_id,
                expected_revision=payload.expected_revision,
                pending_business_confirmation=payload.pending_business_confirmation,
                trace_id=trace_id,
            )
            return to_detail(view)
    except (ReviewNotFound, InspectionNotFound):
        raise _not_found() from None
    except ReviewConflict as exc:
        raise _conflict(exc) from None
    except ReviewInputError as exc:
        raise _input_error(exc) from None


@router.post(
    "/{review_id}/reopen",
    response_model=ReviewDetail,
    dependencies=[Depends(require_role(*_REVIEWER_ROLES))],
)
def reopen_review_endpoint(
    review_id: UUID,
    payload: ReviewReopenRequest,
    request: Request,
    identity: Identity = Depends(get_current_identity),
) -> ReviewDetail:
    """显式重新打开已完成的复核：新 revision + 审计，历史保留。"""
    trace_id = resolve_trace_id(request)
    try:
        with session_scope() as session:
            view = reopen_review(
                session,
                identity,
                review_id,
                expected_revision=payload.expected_revision,
                reason=payload.reason,
                trace_id=trace_id,
            )
            return to_detail(view)
    except (ReviewNotFound, InspectionNotFound):
        raise _not_found() from None
    except ReviewConflict as exc:
        raise _conflict(exc) from None
    except ReviewStateError as exc:  # 服务层已映射；此处是最后一道防线
        raise ApiError(
            status_code=409, code="INVALID_STATE", message=str(exc)
        ) from None
    except ReviewInputError as exc:
        raise _input_error(exc) from None


__all__ = [
    "complete_review_endpoint",
    "create_review_endpoint",
    "list_reviews_endpoint",
    "read_review_endpoint",
    "reopen_review_endpoint",
    "router",
    "submit_decision_endpoint",
]
