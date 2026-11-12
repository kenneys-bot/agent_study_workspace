"""``GET /api/v1/system/version``（M5-01/T22）：真实版本信息，无凭据泄露。

单元测试不联网、不连数据库：认证依赖用 FastAPI ``dependency_overrides`` 替换，
git 回退用打桩验证，索引指针写到 ``tmp_path``。响应只包含契约允许的字段，
API key、数据库 URL 与其它配置绝不回传。
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import bank_assistant.api.errors  # noqa: F401  # 先初始化 api 包，避免授权循环导入
from bank_assistant.config.settings import settings
from bank_assistant.main import app
from bank_assistant.security.authorization import get_current_identity
from bank_assistant.security.identity import Identity
from bank_assistant.services import system_info

IDENTITY = Identity(
    user_id=uuid4(),
    username="agent-a",
    role="agent",
    scope_id="demo-bank",
)

CONTRACT_KEYS = {
    "code_sha",
    "git_branch",
    "model_provider",
    "model_chat",
    "embedding_model",
    "index_version",
    "index_calibration_sha256",
    "index_embedding_model",
    "mode",
    "app_version",
}


@pytest.fixture
def client():
    """已认证客户端：只替换身份依赖，不触发数据库读写。"""
    app.dependency_overrides[get_current_identity] = lambda: IDENTITY
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def write_active_pointer(index_dir: Path, index_version: str = "idx-test") -> None:
    index_dir.mkdir(parents=True, exist_ok=True)
    (index_dir / "active.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "index_version": index_version,
                "collection": f"knowledge_{index_version}",
                "embedding_model_version": "BAAI/bge-small-zh-v1.5@512",
                "calibration_vector_sha256": "cal-" + index_version,
            }
        ),
        encoding="utf-8",
    )


def test_system_version_requires_authentication():
    """未带令牌必须 401，且沿用契约 §1 的错误结构。"""
    with TestClient(app) as unauthenticated:
        response = unauthenticated.get("/api/v1/system/version")

    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "UNAUTHENTICATED"
    assert set(body) == {"code", "message", "trace_id", "retryable", "details"}
    assert response.headers["x-trace-id"] == body["trace_id"]


def test_system_version_returns_real_settings_and_index_pointer(
    client, tmp_path, monkeypatch
):
    """版本页展示的每个字段都来自环境/配置/活动索引指针，不是静态值。"""
    monkeypatch.setenv("GIT_SHA", "0123456789abcdef0123456789abcdef01234567")
    monkeypatch.setenv("GIT_BRANCH", "ticket/T22")
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "deepseek")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(settings, "LLM_MODEL_CHAT", None)
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_NAME", "BAAI/bge-small-zh-v1.5")
    monkeypatch.setattr(settings, "MODEL_MODE", "live")
    monkeypatch.setattr(settings, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path))
    write_active_pointer(tmp_path, "idx-20261112T000000Z-abcdef12")

    response = client.get("/api/v1/system/version")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == CONTRACT_KEYS
    assert payload == {
        "code_sha": "0123456789abcdef0123456789abcdef01234567",
        "git_branch": "ticket/T22",
        "model_provider": "deepseek",
        "model_chat": "deepseek-chat",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "index_version": "idx-20261112T000000Z-abcdef12",
        "index_calibration_sha256": "cal-idx-20261112T000000Z-abcdef12",
        "index_embedding_model": "BAAI/bge-small-zh-v1.5@512",
        "mode": "live",
        "app_version": "1.0.0",
    }


def test_relay_provider_reports_relay_model(client, tmp_path, monkeypatch):
    """模型标识跟随实际供应商，避免把 DeepSeek 模型记到 relay 头上。"""
    monkeypatch.setenv("GIT_SHA", "sha")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setattr(settings, "MODEL_PROVIDER", "relay")
    monkeypatch.setattr(settings, "LLM_MODEL_CHAT", "relay-chat-v1")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-chat")
    monkeypatch.setattr(settings, "EMBEDDING_MODEL_NAME", "embedding-model")
    monkeypatch.setattr(settings, "MODEL_MODE", "replay")
    monkeypatch.setattr(settings, "APP_VERSION", "1.0.0")
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path))

    payload = client.get("/api/v1/system/version").json()

    assert payload["model_provider"] == "relay"
    assert payload["model_chat"] == "relay-chat-v1"
    assert payload["mode"] == "replay"


def test_code_sha_falls_back_to_git_when_env_missing(monkeypatch):
    """没有 GIT_SHA 时读取真实 ``git rev-parse HEAD``，而不是编造值。"""
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GIT_BRANCH", raising=False)

    def fake_git(args: list[str]) -> str | None:
        if args == ["rev-parse", "HEAD"]:
            return "abcdef1234567890"
        if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
            return "feat/bank-assistant"
        return None

    monkeypatch.setattr(system_info, "_run_git", fake_git)

    assert system_info.resolve_code_sha() == "abcdef1234567890"
    assert system_info.resolve_git_branch() == "feat/bank-assistant"


def test_git_failure_is_reported_as_unknown(monkeypatch):
    """git 不可用（打包/容器无 .git）时明确返回 unknown，不让端点 500。"""
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.delenv("GIT_BRANCH", raising=False)
    monkeypatch.setattr(system_info, "_run_git", lambda args: None)

    assert system_info.resolve_code_sha() == "unknown"
    assert system_info.resolve_git_branch() == "unknown"


def test_missing_index_pointer_is_null(tmp_path, monkeypatch):
    """还没来得及建索引时返回 null，前端显示“未构建”，不伪造版本号。"""
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path))

    assert system_info.resolve_index_version() is None
    active = system_info.resolve_active_index()
    assert active.index_version is None
    assert active.calibration_sha256 is None
    assert active.embedding_model is None


def test_corrupt_index_pointer_is_null(tmp_path, monkeypatch):
    """损坏的 active.json 不能被当成可用索引。"""
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path))
    (tmp_path / "active.json").write_text("{not-json", encoding="utf-8")

    assert system_info.resolve_index_version() is None


def test_response_never_contains_credentials_or_database_url(
    client, tmp_path, monkeypatch
):
    """回归保护：版本响应绝不泄露 API key / 数据库 URL。"""
    monkeypatch.setenv("GIT_SHA", "sha")
    monkeypatch.setenv("GIT_BRANCH", "main")
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "sk-deepseek-should-not-leak")
    monkeypatch.setattr(settings, "LLM_API_KEY", "sk-relay-should-not-leak")
    monkeypatch.setattr(
        settings,
        "DATABASE_URL",
        "postgresql+psycopg://bank:supersecret@db:5432/bank_assistant",
    )
    monkeypatch.setattr(settings, "INDEX_DIR", str(tmp_path))

    response = client.get("/api/v1/system/version")
    raw = response.text.lower()

    assert response.status_code == 200
    for forbidden in (
        "sk-deepseek-should-not-leak",
        "sk-relay-should-not-leak",
        "supersecret",
        "postgresql",
        "database_url",
        "api_key",
    ):
        assert forbidden.lower() not in raw
