"""认证：从数据库记录解析身份，绝不接受客户端自报的角色/范围。

令牌只提供 ``user_id``；这里按 ID 重读 ``users`` 行并检查 ``active``。停用
用户或已删除用户与无令牌一样按 401 处理。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import User
from bank_assistant.security.identity import Identity
from bank_assistant.security.passwords import verify_password


def _identity_from_user(user: User) -> Identity:
    return Identity(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        scope_id=user.scope_id,
    )


def load_identity(session: Session, user_id: UUID) -> Identity | None:
    """按服务端用户 ID 解析身份；不存在或已停用返回 ``None``。"""
    user = session.get(User, user_id)
    if user is None or not user.active:
        return None
    return _identity_from_user(user)


def authenticate(session: Session, username: str, password: str) -> Identity | None:
    """校验用户名/密码；未知用户、错误密码、停用用户统一返回 ``None``。"""
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return _identity_from_user(user)
