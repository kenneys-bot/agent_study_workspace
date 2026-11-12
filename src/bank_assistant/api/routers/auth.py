"""登录与当前身份路由（契约 §5，M2-02）。

``POST /auth/login`` 接受真实凭据并返回签名令牌；``GET /me`` 的身份完全由
服务端从令牌 ``user_id`` 对应的数据库行解析。登录限流属于 M7 部署强化，
本票不伪造限流行为。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.api.models.auth import (
    LoginRequest,
    MeResponse,
    TokenResponse,
    UserPublic,
)
from bank_assistant.config.settings import settings
from bank_assistant.infra.db import get_db_session
from bank_assistant.security.authentication import authenticate
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity
from bank_assistant.security.rate_limit import enforce_login_rate_limit
from bank_assistant.security.tokens import create_access_token

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/login",
    response_model=TokenResponse,
    dependencies=[Depends(enforce_login_rate_limit)],
)
def login(
    payload: LoginRequest,
    session: Session = Depends(get_db_session),
) -> TokenResponse:
    """校验凭据并签发访问令牌；未知用户与错误密码返回同一 401。"""
    identity = authenticate(session, payload.username, payload.password)
    if identity is None:
        raise ApiError(
            status_code=401,
            code="AUTHENTICATION_FAILED",
            message="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenResponse(
        access_token=create_access_token(identity),
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserPublic(
            user_id=identity.user_id,
            username=identity.username,
            role=identity.role,
            scope_id=identity.scope_id,
        ),
    )


@router.get("/me", response_model=MeResponse)
def read_me(identity: Identity = Depends(get_current_identity)) -> MeResponse:
    """返回当前身份与权限；令牌无效/用户停用一律 401。"""
    return MeResponse(
        user_id=identity.user_id,
        username=identity.username,
        role=identity.role,
        scope_id=identity.scope_id,
    )
