"""访问令牌：HS256 签名，``SECRET_KEY`` 缺失时明确失败（M2-02）。

令牌只携带 ``user_id``（``sub``）与签发/过期时间；角色、``scope_id`` 一律在
请求时从数据库重读，避免客户端伪造身份。这里不提供默认密钥——没有配置就
直接报错，不能悄悄发明一个进程内密钥。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt

from bank_assistant.config.settings import settings
from bank_assistant.security.identity import Identity

ALGORITHM = "HS256"


class TokenError(Exception):
    """令牌无效、被篡改或已过期。调用方应返回 401，不透露具体原因。"""


class SecretKeyMissingError(RuntimeError):
    """``SECRET_KEY`` 未配置；宁可失败也不使用默认/临时密钥。"""


def resolve_signing_secret(secret: str | None = None) -> str:
    """返回签名密钥；显式参数优先，否则读取必填配置 ``SECRET_KEY``。"""
    candidate = secret if secret is not None else settings.SECRET_KEY
    cleaned = (candidate or "").strip()
    if not cleaned:
        raise SecretKeyMissingError(
            "SECRET_KEY 未配置：拒绝签发或校验访问令牌。请在环境变量/.env 中提供"
            "一个足够长的随机值（演示环境也不能使用默认值）。"
        )
    return cleaned


def create_access_token(
    identity: Identity,
    *,
    secret: str | None = None,
    expires_minutes: int | None = None,
    now: datetime | None = None,
) -> str:
    """签发只含服务端用户 ID 与有效期的访问令牌。"""
    signing_secret = resolve_signing_secret(secret)
    issued_at = now or datetime.now(UTC)
    lifetime = (
        settings.ACCESS_TOKEN_EXPIRE_MINUTES
        if expires_minutes is None
        else expires_minutes
    )
    claims = {
        "sub": str(identity.user_id),
        "iat": issued_at,
        "exp": issued_at + timedelta(minutes=lifetime),
    }
    return jwt.encode(claims, signing_secret, algorithm=ALGORITHM)


def decode_access_token(token: str, *, secret: str | None = None) -> UUID:
    """校验签名与有效期，返回服务端用户 ID；任何问题统一抛 ``TokenError``。"""
    signing_secret = resolve_signing_secret(secret)
    try:
        claims = jwt.decode(token, signing_secret, algorithms=[ALGORITHM])
        return UUID(str(claims["sub"]))
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
        raise TokenError("访问令牌无效或已过期") from exc
