"""系统版本路由（契约 §5，M5-01/T22）。

``GET /system/version`` 需要登录，返回前端版本页展示的真实运行信息；读取
失败的信息用 ``null``/``unknown`` 如实表达，不编造 SHA、模型或索引版本。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from bank_assistant.api.models.system import SystemVersionResponse
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity
from bank_assistant.services.system_info import build_system_version

router = APIRouter(tags=["system"])


@router.get("/system/version", response_model=SystemVersionResponse)
def read_system_version(
    _identity: Identity = Depends(get_current_identity),
) -> SystemVersionResponse:
    """返回代码/模型/索引/模式版本快照；仅登录用户可见。"""
    return SystemVersionResponse(**asdict(build_system_version()))
