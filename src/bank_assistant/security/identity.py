"""服务端身份模型。

身份只由数据库记录解析：令牌里只带 ``user_id``，角色与访问范围每次请求都
从 ``users`` 表读取，客户端无法通过请求体/令牌声明抬高权限（M2-02）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class Role(StrEnum):
    """首版角色；后续里程碑可按需扩展，但必须是服务端写入的受控值。"""

    AGENT = "agent"
    REVIEWER = "reviewer"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class Identity:
    """一次请求的已认证身份（来自服务端存储，不来自客户端输入）。"""

    user_id: UUID
    username: str
    role: str
    scope_id: str
