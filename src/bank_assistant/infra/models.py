"""业务实体：M2 的用户、会话/轮次与审计事件。

契约 §2 的最低要求是 User / Role（user_id、role、scope_id、active）。首版用
``users.role`` 列表达角色，不额外建 ``roles`` 表；角色值由服务端写入，客户端
不能提交角色字段。M2 后续票（知识）在这里继续追加实体。

本票（T12）加入：

- ``conversations``：源会话与不可变原文、内容哈希、数据分级与脱敏标志；
- ``turns``：会话轮次，``ordinal`` 在会话内唯一，原文不可变（服务层禁止改写）；
- ``audit_events``：追加式审计，before/after 只含元数据摘要，不含正文。

所有时间列为 ``timestamptz``，写入前统一为 UTC（契约 §1）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from bank_assistant.infra.db import Base


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
