"""业务实体：M2 的用户、会话、任务与知识文档/版本。

契约 §2 的最低要求是 User / Role（user_id、role、scope_id、active）。首版用
``users.role`` 列表达角色，不额外建 ``roles`` 表；角色值由服务端写入，客户端
不能提交角色字段。

- ``conversations`` / ``turns`` / ``audit_events``：会话原文、轮次与追加式审计（T12）；
- ``tasks`` / ``idempotency_keys``：任务租约与持久化幂等绑定（T13，M2-05）；
- ``knowledge_documents`` / ``knowledge_versions``：知识来源、业务范围与版本
  生命周期（T11，M2-03）；发布后内容不可覆盖。
- ``knowledge_chunks``：版本化索引的原文区间与 embedding 身份（T14，M3）；
- ``model_runs`` / ``suggestions``：候选答复运行追溯与业务状态（T15，M3）；
- ``suggestion_feedback``：坐席采纳/编辑/拒绝反馈，只追加、不改写（T16，M3）；
  反馈不是正确性标签，不得作为评测的 ground-truth；
- ``rule_sets`` / ``rule_versions``：质检规则的身份、版本内容与生命周期
  （T17，M4-01）；发布后内容不可覆盖。
- ``inspection_runs`` / ``findings``：一次质检运行的规则快照、状态与逐规则
  机器判断（T18，M4-03）；机器判断写入后不可覆盖。

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
from bank_assistant.rules.lifecycle import PublishedRuleImmutable, RuleStatus

TASK_STATUS_CHECK = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"
INSPECTION_STATUS_CHECK = TASK_STATUS_CHECK
FINDING_VERDICT_CHECK = (
    "machine_verdict IN ('hit', 'clear', 'insufficient_evidence', 'not_applicable')"
)


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


FEEDBACK_ACTION_CHECK = "action IN ('adopt', 'edit', 'reject')"
FEEDBACK_EDIT_TEXT_CHECK = (
    "action <> 'edit' OR (edited_text IS NOT NULL AND length(trim(edited_text)) > 0)"
)
FEEDBACK_REJECT_REASON_CHECK = (
    "action <> 'reject' OR (reason IS NOT NULL AND length(trim(reason)) > 0)"
)
FEEDBACK_EDITED_TEXT_ONLY_EDIT_CHECK = "action = 'edit' OR edited_text IS NULL"


class FeedbackAppendOnly(RuntimeError):
    """反馈是追加记录：任何更新尝试都必须失败，历史不能被改写。"""

    def __init__(self, feedback_id: UUID) -> None:
        self.feedback_id = feedback_id
        super().__init__(f"建议反馈不可修改: {feedback_id}")


class SuggestionFeedback(Base):
    """坐席对候选答复的反馈（契约 §2）：追加写入，没有更新路径。

    一条反馈通过 ``suggestion_id`` 绑定到**当时的具体建议记录**；重新生成
    候选答复会产生新的 suggestion_id，历史反馈不会被改挂到新版本。

    反馈不是正确性标签：``adopt`` 只表示坐席决定采用（原样或编辑后），
    不表示答复在语义上正确。它不得被用作评测的 ground-truth 标签，任何
    代码都不得把 adopt 当作“正确”。

    - ``edited_text``：仅 ``edit`` 保存坐席实际改写后的文本（编辑文本留存）；
    - ``reason``：反馈理由，``reject`` 必须给出；
    - ``created_at``：写入时间；本表只追加，不更新。
    """

    __tablename__ = "suggestion_feedback"
    __table_args__ = (
        CheckConstraint(FEEDBACK_ACTION_CHECK, name="ck_suggestion_feedback_action"),
        CheckConstraint(
            FEEDBACK_EDIT_TEXT_CHECK, name="ck_suggestion_feedback_edit_text"
        ),
        CheckConstraint(
            FEEDBACK_REJECT_REASON_CHECK,
            name="ck_suggestion_feedback_reject_reason",
        ),
        CheckConstraint(
            FEEDBACK_EDITED_TEXT_ONLY_EDIT_CHECK,
            name="ck_suggestion_feedback_edited_text_only_edit",
        ),
        Index(
            "ix_suggestion_feedback_suggestion_created",
            "suggestion_id",
            "created_at",
        ),
    )

    feedback_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    suggestion_id: Mapped[UUID] = mapped_column(
        ForeignKey("suggestions.suggestion_id", ondelete="RESTRICT"), nullable=False
    )
    actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.user_id", ondelete="RESTRICT"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    edited_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@event.listens_for(SuggestionFeedback, "before_update")
def _reject_feedback_update(
    mapper: Any, connection: Any, target: SuggestionFeedback
) -> None:
    """ORM 层兜底：反馈只能追加，任何 UPDATE 都显式失败。"""
    raise FeedbackAppendOnly(target.feedback_id)


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


class RuleSet(Base):
    """规则身份：规则编码、类别、访问范围与演示标记（契约 §2 RuleVersion）。

    ``rule_id`` 是稳定身份；规则的正文随版本变化，``rule_code``（如
    ``DEMO-FEE-01``）是业务与证据 JSON 里展示的编号。``demo_only`` 标记该
    规则身份是否来自虚构演示规则集；版本在创建时继承此标记，客户端无法在
    追加版本时翻转它。
    """

    __tablename__ = "rule_sets"
    __table_args__ = (
        UniqueConstraint(
            "scope_id", "rule_code", name="uq_rule_sets_scope_rule_code"
        ),
        Index("ix_rule_sets_scope_id", "scope_id"),
        Index("ix_rule_sets_category", "category"),
    )

    rule_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    rule_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    # M4-02 的默认类别之一；规则身份级字段，不随版本变化。
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # 演示规则必须为 true，且说明需含「虚构演示制度」（GLOSSARY「演示制度」）。
    demo_only: Mapped[bool] = mapped_column(
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


RULE_SEVERITY_CHECK = "severity IN ('low', 'medium', 'high')"


class RuleVersion(Base):
    """规则版本：内容、适用范围、证据示例与生命周期（契约 §2、§3）。

    ``applicability`` 只存非时间部分（业务范围、渠道、前提条件）；有效期窗口
    用 ``valid_from``/``valid_to`` 两列表达，便于用 SQL 判定同一规则的两个
    已发布版本是否重叠。``examples`` 是契约 §4 证据 JSON 的四类示例
    （正例/反例/证据不足/不适用），由 ``bank_assistant.rules.schema`` 校验。

    ``revision`` 是状态转换的乐观并发版本号；``content_hash`` 是规则内容
    （含窗口与示例）的规范化 SHA-256，用于演示种子幂等与不可变校验。
    """

    __tablename__ = "rule_versions"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "version_no", name="uq_rule_versions_rule_version_no"
        ),
        CheckConstraint(
            "status in ('draft', 'in_review', 'published', 'retired')",
            name="ck_rule_versions_status",
        ),
        CheckConstraint(RULE_SEVERITY_CHECK, name="ck_rule_versions_severity"),
        CheckConstraint("version_no >= 1", name="ck_rule_versions_version_no"),
        CheckConstraint("revision >= 1", name="ck_rule_versions_revision"),
        CheckConstraint(
            "valid_to is null or valid_to > valid_from",
            name="ck_rule_versions_valid_window",
        ),
        Index("ix_rule_versions_rule_id", "rule_id"),
        Index("ix_rule_versions_status_valid_from", "status", "valid_from"),
    )

    rule_version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    rule_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_sets.rule_id"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    business_type: Mapped[str] = mapped_column(String(64), nullable=False)
    applicability: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    criteria: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    # 豁免条件：命中的反例边界，必须显式声明。
    exceptions: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    source_refs: Mapped[list[Any]] = mapped_column(JSON, nullable=False, default=list)
    examples: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    demo_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
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


# 规则“内容”列：这些字段一旦进入已发布状态就不再允许改写（契约 §3）。
# 状态、审核人、审核/退役理由与 revision 仍可变化，因为它们记录生命周期。
_RULE_CONTENT_COLUMNS: tuple[str, ...] = (
    "business_type",
    "applicability",
    "criteria",
    "exceptions",
    "severity",
    "source_refs",
    "examples",
    "content_hash",
    "demo_only",
    "valid_from",
    "valid_to",
)


@event.listens_for(RuleVersion, "before_update")
def _reject_published_rule_change(
    mapper: Any, connection: Any, target: RuleVersion
) -> None:
    """已发布规则内容不可覆盖：内容列变更时若旧状态或新状态为 published 则拒绝。

    数据库层兜底，防止任何绕过服务的写入静默改写已发布的规则定义或窗口。
    """
    state = inspect(target)
    if not any(
        state.attrs[column].history.has_changes() for column in _RULE_CONTENT_COLUMNS
    ):
        return
    previous_statuses = {str(value) for value in state.attrs.status.history.deleted}
    if (
        target.status == RuleStatus.PUBLISHED.value
        or RuleStatus.PUBLISHED.value in previous_statuses
    ):
        raise PublishedRuleImmutable(target.rule_version_id)


# ---------------------------------------------------------------------------
# 质检运行与机器判断（T18，M4-03；契约 §2 InspectionRun / Finding）
# ---------------------------------------------------------------------------


class FindingImmutable(RuntimeError):
    """机器判断写入后不可覆盖（契约 §2「机器判断不可被人工覆盖」）。

    人工复核结论由后续票的 ReviewDecision 追加保存，绝不能改写这里。
    """

    def __init__(self, finding_id: UUID) -> None:
        self.finding_id = finding_id
        super().__init__(f"机器判断不可覆盖: {finding_id}")


class InspectionRun(Base):
    """一次质检运行（契约 §2 InspectionRun、§3「质检任务」）。

    - ``rule_snapshot`` 是创建时固化的已发布规则版本集合（JSON），worker 只按
      快照判断；规则之后被退役不会改变本次质检的规则集；
    - ``conversation_hash`` 是创建时会话原文的 SHA-256，worker 执行前复核，
      会话不可变（T12）被破坏时显式失败而不是按新内容判断；
    - ``status`` 只允许 ``queued → running → succeeded/failed/cancelled``；
    - ``task_id`` 关联租约队列里的任务（唯一），``parent_run_id`` 预留重试运行
      记录链（M4-05）；``run_id`` 指向模型运行追溯记录（契约 §1）；
    - ``diagnostics`` 只放安全诊断信息（错误码、规则编码、校验原因），不放
      会话正文、模型原始输出或凭据。
    """

    __tablename__ = "inspection_runs"
    __table_args__ = (
        CheckConstraint(
            INSPECTION_STATUS_CHECK, name="ck_inspection_runs_status"
        ),
        UniqueConstraint("task_id", name="uq_inspection_runs_task_id"),
        Index("ix_inspection_runs_conversation_id", "conversation_id"),
        Index("ix_inspection_runs_scope_status", "scope_id", "status"),
    )

    inspection_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.conversation_id", ondelete="RESTRICT"),
        nullable=False,
    )
    conversation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("model_runs.run_id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="queued",
        server_default=text("'queued'"),
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    parent_run_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("inspection_runs.inspection_id"), nullable=True
    )
    scope_id: Mapped[str] = mapped_column(String(64), nullable=False)
    task_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("tasks.task_id"), nullable=True
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


class Finding(Base):
    """逐规则的机器判断（契约 §2 Finding、§4 证据 JSON）。

    ``machine_verdict`` 是模型 + 确定性校验后的判断，写入后不可修改；人工复核
    只能追加 ReviewDecision，不能覆盖。``(inspection_id, rule_version_id)``
    唯一：同一运行对同一规则版本只会有一条最终判断，重复投递不会产生重复行。
    ``severity`` 来自规则版本，不是模型自报。
    """

    __tablename__ = "findings"
    __table_args__ = (
        CheckConstraint(FINDING_VERDICT_CHECK, name="ck_findings_machine_verdict"),
        CheckConstraint(RULE_SEVERITY_CHECK, name="ck_findings_severity"),
        UniqueConstraint(
            "inspection_id",
            "rule_version_id",
            name="uq_findings_inspection_rule_version",
        ),
        Index("ix_findings_inspection_id", "inspection_id"),
    )

    finding_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    inspection_id: Mapped[UUID] = mapped_column(
        ForeignKey("inspection_runs.inspection_id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("rule_versions.rule_version_id", ondelete="RESTRICT"),
        nullable=False,
    )
    machine_verdict: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    knowledge_refs: Mapped[list[Any]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


@event.listens_for(Finding, "before_update")
def _reject_finding_update(
    mapper: Any, connection: Any, target: Finding
) -> None:
    """ORM 层兜底：机器判断只能追加，任何 UPDATE 都显式失败。"""
    raise FindingImmutable(target.finding_id)
