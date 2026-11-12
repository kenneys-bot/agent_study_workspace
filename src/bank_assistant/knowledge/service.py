"""知识版本生命周期服务：创建草稿、提交审核、发布/退回、退役与授权读取。

契约 §3/§5（M2-03）：

- 修改已发布内容只能新建版本（``create_draft_version`` 在已有文档下追加版本）；
- 状态转换 ``draft → in_review → published → retired``，审核退回为 draft；
- 发布必须由 reviewer/admin 执行，且审核人不能是作者（发布者与作者分离）；
- 同一文档的冲突有效版本在发布时被检测出来：旧版本被显式退役并记录原因，
  新版本响应里列出 ``superseded_version_ids``，不静默覆盖；
- 状态转换在短事务内完成，``revision`` 做乐观并发控制（不匹配返回 409）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion
from bank_assistant.knowledge.lifecycle import (
    KnowledgeStatus,
    RevisionConflict,
    ensure_reviewer_separation,
    ensure_transition,
    hash_content,
    validate_window,
)
from bank_assistant.security.authorization import ensure_resource_visible
from bank_assistant.security.identity import Identity


@dataclass(frozen=True)
class PublishedVersion:
    """一次发布的结果；``superseded`` 是被显式退役的冲突版本。"""

    version: KnowledgeVersion
    document: KnowledgeDocument
    superseded: list[UUID]
    outcome: Literal["published", "returned_to_draft"]


def _not_found() -> ApiError:
    """跨范围与不存在共用同一 404，不泄露存在性（契约 §1）。"""
    return ApiError(
        status_code=404,
        code="NOT_FOUND",
        message="资源不存在",
    )


def _commit(session: Session) -> None:
    """短事务提交；失败回滚并保留原始异常（含已发布内容不可覆盖）。"""
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def _load_version(
    session: Session,
    identity: Identity,
    version_id: UUID,
    *,
    for_update: bool = False,
) -> tuple[KnowledgeVersion, KnowledgeDocument]:
    """按身份读取版本及其文档；不存在或跨 ``scope_id`` 一律 404。"""
    version = session.get(KnowledgeVersion, version_id, with_for_update=for_update)
    if version is None:
        raise _not_found()
    document = session.get(
        KnowledgeDocument, version.document_id, with_for_update=for_update
    )
    if document is not None:
        ensure_resource_visible(document, identity)
    if document is None:
        raise _not_found()
    return version, document


def _ensure_revision(version: KnowledgeVersion, expected_revision: int) -> None:
    if version.revision != expected_revision:
        raise RevisionConflict(expected=expected_revision, current=version.revision)


def create_draft_version(
    session: Session,
    identity: Identity,
    *,
    title: str | None,
    source_type: str | None,
    source_uri: str | None,
    business_type: str | None,
    content: str,
    valid_from: datetime | None = None,
    valid_to: datetime | None = None,
    document_id: UUID | None = None,
) -> tuple[KnowledgeDocument, KnowledgeVersion]:
    """新建文档或给已有文档追加草稿版本；``scope_id`` 永远来自服务端身份。"""
    start = valid_from or datetime.now(UTC)
    validate_window(start, valid_to)

    if document_id is None:
        if not title or not source_type or not business_type:
            raise ValueError("新建文档必须提供 title、source_type 与 business_type")
        document = KnowledgeDocument(
            document_id=uuid4(),
            title=title,
            source_type=source_type,
            source_uri=source_uri,
            business_type=business_type,
            scope_id=identity.scope_id,
        )
        session.add(document)
        session.flush()
        version_no = 1
    else:
        document = session.get(
            KnowledgeDocument, document_id, with_for_update=True
        )
        if document is not None:
            ensure_resource_visible(document, identity)
        if document is None:
            raise _not_found()
        highest = session.scalar(
            select(func.max(KnowledgeVersion.version_no)).where(
                KnowledgeVersion.document_id == document.document_id
            )
        )
        version_no = (highest or 0) + 1

    version = KnowledgeVersion(
        version_id=uuid4(),
        document_id=document.document_id,
        version_no=version_no,
        content=content,
        content_hash=hash_content(content),
        status=KnowledgeStatus.DRAFT.value,
        valid_from=start,
        valid_to=valid_to,
        author_id=identity.user_id,
        revision=1,
    )
    session.add(version)
    _commit(session)
    return document, version


def submit_version(
    session: Session,
    identity: Identity,
    version_id: UUID,
    *,
    expected_revision: int,
) -> tuple[KnowledgeVersion, KnowledgeDocument]:
    """``draft → in_review``；提交审核（同范围的作者或协作者均可提交）。"""
    version, document = _load_version(
        session, identity, version_id, for_update=True
    )
    _ensure_revision(version, expected_revision)
    ensure_transition(version.status, KnowledgeStatus.IN_REVIEW)
    version.status = KnowledgeStatus.IN_REVIEW.value
    version.revision += 1
    _commit(session)
    return version, document


def _overlapping_published_query(version: KnowledgeVersion):
    """同一文档中与 ``version`` 有效期重叠的已发布版本（SQL 层判定）。"""
    statement = select(KnowledgeVersion).where(
        KnowledgeVersion.document_id == version.document_id,
        KnowledgeVersion.version_id != version.version_id,
        KnowledgeVersion.status == KnowledgeStatus.PUBLISHED.value,
        or_(
            KnowledgeVersion.valid_to.is_(None),
            version.valid_from < KnowledgeVersion.valid_to,
        ),
    )
    if version.valid_to is not None:
        statement = statement.where(KnowledgeVersion.valid_from < version.valid_to)
    return statement.with_for_update()


def publish_version(
    session: Session,
    identity: Identity,
    version_id: UUID,
    *,
    expected_revision: int,
    review_reason: str,
    decision: str = "approve",
) -> PublishedVersion:
    """发布（approve）或退回草稿（reject）；审核人不能是作者本人。

    approve 时若同一文档存在有效期重叠的已发布版本，旧版本被显式退役并记录
    原因，返回的 ``superseded`` 里列出其 ID——冲突不会静默消失。
    """
    if decision not in {"approve", "reject"}:
        raise ValueError("decision 只能是 approve 或 reject")
    version, document = _load_version(
        session, identity, version_id, for_update=True
    )
    ensure_reviewer_separation(version.author_id, identity.user_id)
    _ensure_revision(version, expected_revision)

    if decision == "reject":
        ensure_transition(version.status, KnowledgeStatus.DRAFT)
        version.status = KnowledgeStatus.DRAFT.value
        version.reviewer_id = identity.user_id
        version.review_reason = review_reason
        version.revision += 1
        _commit(session)
        return PublishedVersion(
            version=version,
            document=document,
            superseded=[],
            outcome="returned_to_draft",
        )

    ensure_transition(version.status, KnowledgeStatus.PUBLISHED)
    superseded: list[UUID] = []
    for conflict in session.scalars(_overlapping_published_query(version)):
        ensure_transition(conflict.status, KnowledgeStatus.RETIRED)
        conflict.status = KnowledgeStatus.RETIRED.value
        conflict.retire_reason = (
            f"被版本 {version.version_no} 取代：有效期重叠，发布时显式退役"
        )
        conflict.revision += 1
        superseded.append(conflict.version_id)

    version.status = KnowledgeStatus.PUBLISHED.value
    version.reviewer_id = identity.user_id
    version.review_reason = review_reason
    version.revision += 1
    _commit(session)
    return PublishedVersion(
        version=version,
        document=document,
        superseded=superseded,
        outcome="published",
    )


def retire_version(
    session: Session,
    identity: Identity,
    version_id: UUID,
    *,
    expected_revision: int,
    reason: str,
) -> tuple[KnowledgeVersion, KnowledgeDocument]:
    """``published → retired``；退役后不再进入检索。"""
    version, document = _load_version(
        session, identity, version_id, for_update=True
    )
    _ensure_revision(version, expected_revision)
    ensure_transition(version.status, KnowledgeStatus.RETIRED)
    version.status = KnowledgeStatus.RETIRED.value
    version.retire_reason = reason
    version.revision += 1
    _commit(session)
    return version, document


def get_version(
    session: Session, identity: Identity, version_id: UUID
) -> tuple[KnowledgeVersion, KnowledgeDocument]:
    """授权范围内读取版本原文与来源；跨范围 404。"""
    return _load_version(session, identity, version_id)


__all__ = [
    "PublishedVersion",
    "create_draft_version",
    "get_version",
    "publish_version",
    "retire_version",
    "submit_version",
]
