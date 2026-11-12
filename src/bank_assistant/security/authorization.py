"""授权依赖：未来业务路由统一从这里取身份并做 403/404 判定（M2-02）。

规则：
- 未登录 / 令牌无效 / 用户停用 → 401；
- 已登录但请求范围或角色不符 → 403；
- 资源不存在，或资源属于其他 ``scope_id`` → 同一个 404 响应，不泄露存在性。
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from bank_assistant.api.errors import ApiError
from bank_assistant.infra.db import get_db_session
from bank_assistant.security.authentication import load_identity
from bank_assistant.security.identity import Identity
from bank_assistant.security.tokens import TokenError, decode_access_token

# auto_error=False：没有 Authorization 头时由我们返回契约错误结构，而不是
# FastAPI 默认的 {"detail": "Not authenticated"}。
_BEARER = HTTPBearer(auto_error=False)


def unauthorized(message: str = "需要登录后访问") -> ApiError:
    """构造 401；附带标准 ``WWW-Authenticate`` 头。"""
    return ApiError(
        status_code=401,
        code="UNAUTHENTICATED",
        message=message,
        headers={"WWW-Authenticate": "Bearer"},
    )


def ensure_scope(identity: Identity, required_scope: str) -> None:
    """当前身份必须属于目标 ``scope_id``，否则 403。"""
    if identity.scope_id != required_scope:
        raise ApiError(
            status_code=403,
            code="FORBIDDEN",
            message="无权访问该业务范围的资源",
        )


def ensure_role(identity: Identity, *allowed_roles: str) -> None:
    """当前身份角色必须在允许集合内，否则 403。"""
    allowed = {str(role) for role in allowed_roles}
    if identity.role not in allowed:
        raise ApiError(
            status_code=403,
            code="FORBIDDEN",
            message="当前角色无权执行该操作",
        )


def ensure_resource_visible(
    resource: Any | None,
    identity: Identity,
    *,
    scope_attr: str = "scope_id",
) -> None:
    """资源不存在或跨范围时统一 404，调用方不能借此探测资源是否存在。"""
    resource_scope = getattr(resource, scope_attr, None) if resource is not None else None
    if resource is None or resource_scope != identity.scope_id:
        raise ApiError(
            status_code=404,
            code="NOT_FOUND",
            message="资源不存在",
        )


def get_current_identity(
    credentials: HTTPAuthorizationCredentials | None = Depends(_BEARER),
    session: Session = Depends(get_db_session),
) -> Identity:
    """从 Bearer 令牌解析服务端身份；角色/范围每次从数据库读取。"""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    try:
        user_id = decode_access_token(credentials.credentials)
    except TokenError:
        raise unauthorized("访问令牌无效或已过期") from None

    identity = load_identity(session, user_id)
    if identity is None:
        # 用户已删除/停用，与令牌无效同样处理，不区分原因。
        raise unauthorized("访问令牌无效或已过期")
    return identity


def require_scope(required_scope: str):
    """依赖工厂：``Depends(require_scope("scope-a"))``。"""

    def dependency(identity: Identity = Depends(get_current_identity)) -> Identity:
        ensure_scope(identity, required_scope)
        return identity

    return dependency


def require_role(*allowed_roles: str):
    """依赖工厂：``Depends(require_role(Role.REVIEWER))``。"""

    def dependency(identity: Identity = Depends(get_current_identity)) -> Identity:
        ensure_role(identity, *allowed_roles)
        return identity

    return dependency
