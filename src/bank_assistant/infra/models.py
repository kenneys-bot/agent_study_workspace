"""业务实体：``users`` 与知识文档/版本（M2）。

契约 §2 的最低要求是 User / Role（user_id、role、scope_id、active）。首版用
``users.role`` 列表达角色，不额外建 ``roles`` 表；角色值由服务端写入，客户端
不能提交角色字段。M2 后续票（知识、会话、审计）在这里追加各自实体。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    inspect,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from bank_assistant.infra.db import Base
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, PublishedContentImmutable


class User(Base):
    """登录主体；``scope_id`` 是内部业务访问范围（首版单银行、单部署）。"""

    __tablename__ = "users"

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    # 只存 Argon2 哈希；禁止明文（M2-02）。
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=true()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeDocument(Base):
    """知识文档：标题、来源、业务与访问范围（契约 §2）。

    ``scope_id`` 由服务端按身份写入，客户端不能自报；``business_type`` 是
    检索时的业务过滤条件。演示制度（``demo_policy``）必须与公开监管条文
    （``public_regulation``）在同一字段上可区分（GLOSSARY「演示制度」）。
    """

    __tablename__ = "knowledge_documents"

    document_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(512), nullable=True)
    business_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class KnowledgeVersion(Base):
    """知识版本：一个文档可有多版本，发布后内容不可覆盖（契约 §2、§3）。

    ``revision`` 是状态转换的乐观并发版本号：客户端提交 ``expected_revision``，
    不匹配返回 409，不能靠进程内锁解决多实例并发。
    """

    __tablename__ = "knowledge_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_no",
            name="uq_knowledge_versions_document_version_no",
        ),
        CheckConstraint(
            "status in ('draft', 'in_review', 'published', 'retired')",
            name="ck_knowledge_versions_status",
        ),
        CheckConstraint(
            "version_no >= 1", name="ck_knowledge_versions_version_no"
        ),
        CheckConstraint("revision >= 1", name="ck_knowledge_versions_revision"),
        CheckConstraint(
            "valid_to is null or valid_to > valid_from",
            name="ck_knowledge_versions_valid_window",
        ),
        Index("ix_knowledge_versions_status_valid_from", "status", "valid_from"),
    )

    version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_documents.document_id"), nullable=False, index=True
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    author_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id"), nullable=False, index=True
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.user_id"), nullable=True
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


@event.listens_for(KnowledgeVersion, "before_update")
def _reject_published_content_change(
    mapper: Any, connection: Any, target: KnowledgeVersion
) -> None:
    """已发布内容不可覆盖：内容/哈希变更时，若旧状态或新状态为 published 则拒绝。

    这是数据库层的兜底，防止任何绕过服务层的写入静默改写已发布原文。
    """
    state = inspect(target)
    content_changed = (
        state.attrs.content.history.has_changes()
        or state.attrs.content_hash.history.has_changes()
    )
    if not content_changed:
        return
    previous_statuses = {str(value) for value in state.attrs.status.history.deleted}
    if (
        target.status == KnowledgeStatus.PUBLISHED.value
        or KnowledgeStatus.PUBLISHED.value in previous_statuses
    ):
        raise PublishedContentImmutable(target.version_id)
