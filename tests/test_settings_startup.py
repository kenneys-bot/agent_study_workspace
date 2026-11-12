"""T08：启动配置不应要求未使用的凭据。

- `DASHSCOPE_API_KEY` 可选：未配置只代表对应能力不可用，不影响应用启动。
- `SECRET_KEY` 只在需要认证/签名的环境必填；development/testing 使用明确
  标注的 dev-only 默认值并打警告；production 缺失必须启动失败。
"""

import logging

import pytest
from pydantic import ValidationError

from bank_assistant.config.settings import DEV_ONLY_SECRET_KEY, Settings

MANAGED_ENV = (
    "APP_ENV",
    "SECRET_KEY",
    "DASHSCOPE_API_KEY",
    "DATABASE_URL",
    "DEBUG",
    "DEEPSEEK_API_KEY",
)


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch):
    """隔离宿主/仓库 .env：每个用例从空白环境开始。"""
    for name in MANAGED_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")


def build_settings():
    """显式忽略 .env 文件，只验证环境变量行为。"""
    return Settings(_env_file=None)


def test_dashscope_key_is_optional_for_startup(monkeypatch):
    monkeypatch.setenv("APP_ENV", "testing")
    monkeypatch.setenv("SECRET_KEY", "test-dummy")

    settings = build_settings()

    assert settings.DASHSCOPE_API_KEY is None


@pytest.mark.parametrize("app_env", ["development", "testing"])
def test_dev_and_test_get_explicit_dev_only_secret_with_warning(
    monkeypatch, caplog, app_env
):
    monkeypatch.setenv("APP_ENV", app_env)

    with caplog.at_level(
        logging.WARNING, logger="bank_assistant.config.settings"
    ):
        settings = build_settings()

    assert settings.SECRET_KEY == DEV_ONLY_SECRET_KEY
    assert "dev-only" in settings.SECRET_KEY
    warnings = [record.getMessage() for record in caplog.records]
    assert any("SECRET_KEY" in message and "dev-only" in message for message in warnings)


@pytest.mark.parametrize("app_env", ["production", "staging"])
def test_missing_secret_key_fails_startup_outside_dev(monkeypatch, app_env):
    monkeypatch.setenv("APP_ENV", app_env)

    with pytest.raises(ValidationError) as exc_info:
        build_settings()

    message = str(exc_info.value)
    assert "SECRET_KEY" in message
    assert app_env in message


def test_production_starts_when_secret_key_is_configured(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "prod-only-real-secret")

    settings = build_settings()

    assert settings.SECRET_KEY == "prod-only-real-secret"


def test_explicit_secret_key_is_not_replaced_in_development(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "explicit-dev-secret")

    settings = build_settings()

    assert settings.SECRET_KEY == "explicit-dev-secret"
    assert settings.SECRET_KEY != DEV_ONLY_SECRET_KEY
