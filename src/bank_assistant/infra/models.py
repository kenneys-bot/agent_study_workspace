"""业务实体：M2 的用户、会话、任务与知识文档/版本。

契约 §2 的最低要求是 User / Role（user_id、role、scope_id、active）。首版用
``users.role`` 列表达角色，不额外建 ``roles`` 表；角色值由服务端写入，客户端
不能提交角色字段。

- ``conversations`` / ``turns`` / ``audit_events``：会话原文、轮次与追加式审计（T12）；
- ``tasks`` / ``idempotency_keys``：任务租约与持久化幂等绑定（T13，M2-05）；
- ``knowledge_documents`` / ``knowledge_versions``：知识来源、业务范围与版本
  生命周期（T11，M2-03）；发布后内容不可覆盖。
- ``knowledge_chunks``：版本化索引的原文区间与 embedding 身份（T14，M3）；
- ``model_runs`` / ``suggestions``：候选答复运行追溯与业务状态（T15，M3）。

所有时间列为 ``timestamptz``，写入前统一为 UTC（契约 §1）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
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
    false,
    func,
    inspect,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from bank_assistant.infra.db import Base
from bank_assistant.knowledge.lifecycle import KnowledgeStatus, PublishedContentImmutable

TASK_STATUS_CHECK = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"


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


class Conversation(Base):
    """源会话（契约 §2）：原文只读，业务事实源在数据库（ADR-0002）。

    ``raw_text`` 是不可变原文；``content_hash`` 是它的 SHA-256，供审计与
    备份恢复核对。``redaction_applied`` 记录创建时是否检测到敏感片段，
    读取响应据此显式报告脱敏状态，但遮蔽永远只作用于展示文本。
    """

    __tablename__ = "conversations"

    conversation_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    owner_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id"), nullable=False, index=True
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 原文不可变：服务层没有改写入口，只允许新建会话。
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    # 可选的源引用（如上传文件名/外部记录 ID），不存真实文件内容。
    source_ref: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    # 数据分级：首版 synthetic_demo / internal；云模型调用资格由后续票实现。
    data_classification: Mapped[str] = mapped_column(String(32), nullable=False)
    redaction_applied: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
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


class Turn(Base):
    """会话轮次（契约 §2）：``ordinal`` 会话内唯一，``content`` 原文不可变。"""

    __tablename__ = "turns"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "ordinal", name="uq_turns_conversation_ordinal"
        ),
        Index("ix_turns_conversation_id", "conversation_id"),
    )

    turn_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.conversation_id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(64), nullable=False)
    # 原文保持不可变；证据坐标按这里的 Unicode 码点计数（契约 §4）。
    content: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    """追加式审计（契约 §2）：只写不改；摘要禁止包含敏感正文。

    ``before_summary`` / ``after_summary`` 只放短元数据（计数、哈希、状态、
    时间），由 :mod:`bank_assistant.services.audit` 统一构造并校验。
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )

    event_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    actor_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    after_summary: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Task(Base):
    """契约 §2 的 Task：可重试、带租约的后台运行单元。

    ``attempts`` 在每次领取时递增；达到上限的过期租约由队列 sweep 终结为
    ``failed``，不会无限重试。``lease_*`` 在终态不再有效。
    """

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(TASK_STATUS_CHECK, name="ck_tasks_status"),
        Index("ix_tasks_claim", "type", "status", "lease_expires_at"),
        Index("ix_tasks_scope_id", "scope_id"),
    )

    task_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default=text("'queued'")
    )
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    # 业务载荷的引用（例如会话），不在此表内联正文。
    payload_ref: Mapped[str | None] = mapped_column(String(512))
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    result_ref: Mapped[str | None] = mapped_column(String(512))
    error_code: Mapped[str | None] = mapped_column(String(64))
    # 任务可见范围；跨范围读取统一 404，不泄露存在性。
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class IdempotencyKey(Base):
    """持久化的幂等绑定：``(user, endpoint, key)`` → 输入哈希与原响应。

    ``resource_id`` 指向第一次成功创建的业务资源（当前是 task），
    ``response_snapshot`` 保存原始响应体，重放时不重新执行业务逻辑。
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_keys_binding",
        ),
    )

    idempotency_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[UUID | None] = mapped_column(Uuid)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
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


class KnowledgeChunk(Base):
    """知识分块：版本化索引里可引用的原文区间（契约 §2，M3）。

    区间按不可变 ``knowledge_versions.content`` 的 Unicode 码点计数，
    ``start_offset`` 含、``end_offset`` 不含（契约 §4）；``text`` 必须等于
    ``content[start_offset:end_offset]``，索引才能回验原文。

    ``index_version`` 与 ``embedding_model_version`` 一起标识这一行属于哪次
    索引构建：同一知识版本可以在多个索引版本里各有一份分块，重建不能覆盖
    旧索引（失败要能保留旧版本）。chunk_id 由 ``(version_id, index_version,
    区间)`` 确定性派生，同一构建重复执行不会产生重复行。
    """

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "version_id",
            "index_version",
            "start_offset",
            name="uq_knowledge_chunks_version_index_start",
        ),
        CheckConstraint(
            "start_offset >= 0", name="ck_knowledge_chunks_start_offset"
        ),
        CheckConstraint(
            "end_offset > start_offset", name="ck_knowledge_chunks_offsets"
        ),
        Index("ix_knowledge_chunks_index_version", "index_version"),
    )

    chunk_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_versions.version_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # 码点偏移：start 含、end 不含（契约 §4）。
    start_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    end_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    index_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


SUGGESTION_STATUS_CHECK = (
    "status IN ('answered', 'needs_clarification', 'needs_human', 'failed')"
)


class ModelRun(Base):
    """一次候选答复运行的追溯记录（契约 §1、§2 的 run_id 最低字段）。

    契约 §1 要求“每次模型运行有 run_id，记录模型、提示词、规则、知识快照、
    索引版本和代码 SHA”。本表只保存这些元数据；会话正文与候选答复在
    ``suggestions``，两者用 ``run_id`` 关联。

    没有发生模型调用的业务短路（无证据、业务类型不明确、知识冲突）也写一行
    ``model='none'`` 的运行记录：一次请求仍是一次可追溯的运行，run_id 不为空。
    """

    __tablename__ = "model_runs"

    run_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="none")
    model: Mapped[str] = mapped_column(String(128), nullable=False, default="none")
    model_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="none"
    )
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    # 索引清单里的知识快照（version_id + content_hash）；无索引时为空列表。
    knowledge_snapshot: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    index_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    code_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    # live / replay；回放必须显式标记，不能混入真实模型结论。
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Suggestion(Base):
    """候选答复（契约 §2）：供坐席检查/编辑，不表示已发送或已办理。

    ``citations`` 只保存后端校验通过的引用：必须来自本次召回，且版本仍为
    published、在有效期内、属于当前访问范围。``retrieval_snapshot`` 保存
    本次检索的索引版本、校准哈希、召回 chunk/version 与分数，以及被拒绝的
    模型引用；``mode`` 记录 live / replay。
    """

    __tablename__ = "suggestions"
    __table_args__ = (
        CheckConstraint(SUGGESTION_STATUS_CHECK, name="ck_suggestions_status"),
        Index("ix_suggestions_conversation_id", "conversation_id"),
        Index("ix_suggestions_scope_created", "scope_id", "created_at"),
        Index("ix_suggestions_run_id", "run_id"),
    )

    suggestion_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="SET NULL"),
        nullable=True,
    )
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("model_runs.run_id"), nullable=False
    )
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    rewritten_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    answer: Mapped[str] = mapped_column(
        Text, nullable=False, default="", server_default=text("''")
    )
    citations: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    missing_information: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    retrieval_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
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
