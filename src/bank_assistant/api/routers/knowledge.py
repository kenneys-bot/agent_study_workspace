"""知识生命周期路由（契约 §5，M2-03）。

| 方法与路径 | 作用 |
|---|---|
| POST /knowledge/documents | 新建文档或追加草稿版本 |
| POST /knowledge/versions/{id}/submit | draft → in_review |
| POST /knowledge/versions/{id}/publish | 审核通过发布 / 退回草稿 |
| POST /knowledge/versions/{id}/retire | published → retired |
| GET /knowledge/versions/{id} | 授权范围内原文与来源 |

``scope_id`` 永远取自服务端身份，跨范围资源与不存在资源返回同一个 404；
发布/退役要求 reviewer 或 admin 角色，且审核人不能是作者本人。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from bank_assistant.api.errors import error_response
from bank_assistant.api.models.knowledge import (
    CreateDocumentRequest,
    CreateDocumentResponse,
    DocumentSummary,
    PublishResponse,
    PublishVersionRequest,
    RetireVersionRequest,
    SubmitVersionRequest,
    VersionResponse,
)
from bank_assistant.infra.db import get_db_session
from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion
from bank_assistant.knowledge.lifecycle import (
    InvalidTransition,
    InvalidWindow,
    KnowledgeError,
    PublishedContentImmutable,
    RevisionConflict,
    SelfReviewForbidden,
)
from bank_assistant.knowledge.service import (
    create_draft_version,
    get_version,
    publish_version,
    retire_version,
    submit_version,
)
from bank_assistant.security.authorization import get_current_identity, require_role
from bank_assistant.security.identity import Identity, Role

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

_REVIEWER_ROLES = (Role.REVIEWER, Role.ADMIN)


def _document_summary(document: KnowledgeDocument) -> DocumentSummary:
    return DocumentSummary.model_validate(document)


def _version_response(
    version: KnowledgeVersion, document: KnowledgeDocument
) -> VersionResponse:
    return VersionResponse(
        version_id=version.version_id,
        document_id=version.document_id,
        version_no=version.version_no,
        content=version.content,
        content_hash=version.content_hash,
        status=version.status,
        valid_from=version.valid_from,
        valid_to=version.valid_to,
        author_id=version.author_id,
        reviewer_id=version.reviewer_id,
        review_reason=version.review_reason,
        retire_reason=version.retire_reason,
        revision=version.revision,
        created_at=version.created_at,
        updated_at=version.updated_at,
        document=_document_summary(document),
    )


@router.post(
    "/documents",
    response_model=CreateDocumentResponse,
    status_code=201,
)
def create_document(
    payload: CreateDocumentRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> CreateDocumentResponse:
    """创建文档与草稿版本；范围取自身份，客户端提交的 scope 被忽略。"""
    document, version = create_draft_version(
        session,
        identity,
        title=payload.title,
        source_type=payload.source_type.value if payload.source_type else None,
        source_uri=payload.source_uri,
        business_type=payload.business_type,
        content=payload.content,
        valid_from=payload.valid_from,
        valid_to=payload.valid_to,
        document_id=payload.document_id,
    )
    return CreateDocumentResponse(
        document_id=document.document_id,
        document=_document_summary(document),
        version_id=version.version_id,
        version_no=version.version_no,
        status=version.status,
        content_hash=version.content_hash,
        revision=version.revision,
    )


@router.post("/versions/{version_id}/submit", response_model=VersionResponse)
def submit_version_endpoint(
    version_id: UUID,
    payload: SubmitVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> VersionResponse:
    """提交审核；只有 draft 可以提交，revision 不匹配返回 409。"""
    version, document = submit_version(
        session, identity, version_id, expected_revision=payload.revision
    )
    return _version_response(version, document)


@router.post("/versions/{version_id}/publish", response_model=PublishResponse)
def publish_version_endpoint(
    version_id: UUID,
    payload: PublishVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(require_role(*_REVIEWER_ROLES)),
) -> PublishResponse:
    """审核通过发布，或退回草稿；审核人必须是 reviewer/admin 且不是作者。"""
    result = publish_version(
        session,
        identity,
        version_id,
        expected_revision=payload.revision,
        review_reason=payload.review_reason,
        decision=payload.decision,
    )
    response = _version_response(result.version, result.document)
    return PublishResponse(
        **response.model_dump(),
        outcome=result.outcome,
        superseded_version_ids=result.superseded,
    )


@router.post("/versions/{version_id}/retire", response_model=VersionResponse)
def retire_version_endpoint(
    version_id: UUID,
    payload: RetireVersionRequest,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(require_role(*_REVIEWER_ROLES)),
) -> VersionResponse:
    """退役已发布版本；理由与 revision 必填，退役后不再进入检索。"""
    version, document = retire_version(
        session,
        identity,
        version_id,
        expected_revision=payload.revision,
        reason=payload.reason,
    )
    return _version_response(version, document)


@router.get("/versions/{version_id}", response_model=VersionResponse)
def read_version_endpoint(
    version_id: UUID,
    session: Session = Depends(get_db_session),
    identity: Identity = Depends(get_current_identity),
) -> VersionResponse:
    """读取授权范围内的原文与来源；跨范围与不存在同为 404。"""
    version, document = get_version(session, identity, version_id)
    return _version_response(version, document)


_ERROR_SPECS = (
    (SelfReviewForbidden, 403, "SELF_REVIEW_FORBIDDEN", "审核人不能是作者本人"),
    (RevisionConflict, 409, "REVISION_CONFLICT", "资源已被修改：revision 已过期"),
    (InvalidTransition, 409, "INVALID_STATE", "知识版本状态不允许该操作"),
    (
        PublishedContentImmutable,
        409,
        "PUBLISHED_CONTENT_IMMUTABLE",
        "已发布的知识版本内容不可修改",
    ),
    (InvalidWindow, 422, "VALIDATION_ERROR", "有效期区间非法"),
)


def knowledge_error_handler(request: Request, exc: KnowledgeError) -> JSONResponse:
    """把领域错误映射成契约 §1 的稳定错误码，不泄露内部堆栈。"""
    details: dict[str, object] = {}
    status_code, code, message = 409, "KNOWLEDGE_CONFLICT", "知识版本状态冲突"
    for error_type, error_status, error_code, error_message in _ERROR_SPECS:
        if isinstance(exc, error_type):
            status_code, code, message = error_status, error_code, error_message
            break
    if isinstance(exc, RevisionConflict):
        details["current_revision"] = exc.current
    return error_response(
        status_code=status_code,
        code=code,
        message=message,
        request=request,
        retryable=False,
        details=details,
    )


def install_knowledge_error_handler(app: FastAPI) -> None:
    """安装领域错误处理器；由 ``main`` 在创建应用时调用。"""
    app.add_exception_handler(KnowledgeError, knowledge_error_handler)


__all__ = [
    "install_knowledge_error_handler",
    "knowledge_error_handler",
    "router",
]
