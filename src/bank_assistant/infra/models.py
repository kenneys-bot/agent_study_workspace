"""业务实体：M2 起步的 ``users``。

契约 §2 的最低要求是 User / Role（user_id、role、scope_id、active）。首版用
``users.role`` 列表达角色，不额外建 ``roles`` 表；角色值由服务端写入，客户端
不能提交角色字段。M2 后续票（知识、会话、审计）在这里追加各自实体。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, String, Uuid, func, true
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
