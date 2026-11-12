"""业务实体：``users``（M2-02）、``tasks`` 与 ``idempotency_keys``（T13）。

契约 §2 的 User / Role 用 ``users.role`` 列表达，不额外建 ``roles`` 表；
Task 的最低字段之外增加 ``scope_id``，用于跨范围可见性判定（不泄露存在性）。

``tasks`` 是通用任务队列：本票只实现状态、租约与尝试次数，M4 再挂业务载荷。
``idempotency_keys`` 把 ``(user, endpoint, key)`` 与输入哈希、原响应持久化，
多 worker、重启和故障恢复后仍能复用同一条幂等判定（CT-05/CT-06）。
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
    UniqueConstraint,
    Uuid,
    func,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from bank_assistant.infra.db import Base

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
