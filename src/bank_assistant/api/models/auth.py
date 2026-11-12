"""认证 API 的请求/响应模型（契约 §5：POST /auth/login、GET /me）。

响应只包含公开身份字段，绝不回传密码哈希或令牌内部声明。
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    """登录凭据；首版为用户名+密码，生产可替换为 SSO。"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


class UserPublic(BaseModel):
    """当前身份与权限的最小公开视图。"""

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    username: str
    role: str
    scope_id: str


class TokenResponse(BaseModel):
    """登录成功后的 Bearer 令牌与身份。"""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(..., description="有效期（秒）")
    user: UserPublic


class MeResponse(UserPublic):
    """``GET /me``：当前身份与权限（角色 + 业务访问范围）。"""
