"""系统版本信息（契约 §5：``GET /system/version``，M5-01/T22）。

版本页必须展示**真实**运行信息：代码 SHA、分支、模型、embedding 模型、活动
索引版本与运行模式。这里只读取环境变量、应用配置和活动索引指针，不调用
外部服务；响应字段是显式白名单，绝不包含 API key、数据库 URL 或其它配置。
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bank_assistant.config.settings import settings
from bank_assistant.knowledge.indexing import KnowledgeIndexError, read_active_pointer

UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SystemVersion:
    """一次版本查询的快照；字段与 API 响应一一对应。"""

    code_sha: str
    git_branch: str
    model_provider: str
    model_chat: str | None
    embedding_model: str
    index_version: str | None
    mode: str
    app_version: str


def _repo_root() -> Path:
    """仓库根目录（``src/bank_assistant/services`` 向上三级）。"""
    return Path(__file__).resolve().parents[3]


def _run_git(args: list[str]) -> str | None:
    """执行只读 git 命令；git 缺失、超时或非零退出都返回 ``None``。"""
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=_repo_root(),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _env_value(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def resolve_code_sha() -> str:
    """代码 SHA：优先 ``GIT_SHA``（容器/CI 注入），否则真实 git HEAD。"""
    return _env_value("GIT_SHA") or _run_git(["rev-parse", "HEAD"]) or UNKNOWN


def resolve_git_branch() -> str:
    """分支名：优先 ``GIT_BRANCH``，否则真实 git 分支；失败返回 unknown。"""
    return (
        _env_value("GIT_BRANCH")
        or _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        or UNKNOWN
    )


def resolve_model_chat() -> str | None:
    """当前供应商实际使用的对话模型标识。"""
    if settings.MODEL_PROVIDER.lower() == "relay":
        return settings.LLM_MODEL_CHAT or None
    return settings.DEEPSEEK_MODEL or None


def resolve_index_version() -> str | None:
    """活动索引版本；尚未构建或指针损坏时返回 ``None``，不伪造版本号。"""
    try:
        pointer = read_active_pointer(settings.INDEX_DIR)
    except KnowledgeIndexError:
        return None
    if not pointer:
        return None
    index_version = pointer.get("index_version")
    return str(index_version) if index_version else None


def build_system_version() -> SystemVersion:
    """组装版本快照；每次调用都读取当前配置与活动指针。"""
    return SystemVersion(
        code_sha=resolve_code_sha(),
        git_branch=resolve_git_branch(),
        model_provider=settings.MODEL_PROVIDER,
        model_chat=resolve_model_chat(),
        embedding_model=settings.EMBEDDING_MODEL_NAME,
        index_version=resolve_index_version(),
        mode=settings.MODEL_MODE,
        app_version=settings.APP_VERSION,
    )


__all__ = [
    "SystemVersion",
    "build_system_version",
    "resolve_code_sha",
    "resolve_git_branch",
    "resolve_index_version",
    "resolve_model_chat",
]
