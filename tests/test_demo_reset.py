"""demo-reset 安全保护逻辑的单元测试（T25 M5-04）。"""

from __future__ import annotations

import pytest

from bank_assistant.demo.reset import (
    DEMO_DATABASE_NAME,
    DemoResetError,
    validate_reset_target,
)

DEMO_URL = (
    "postgresql+psycopg://bank:local_only@127.0.0.1:15432/bank_assistant_demo"
)


def _validate(**overrides):
    values = {
        "database_url": DEMO_URL,
        "expected_db": DEMO_DATABASE_NAME,
        "confirm": "yes",
        "app_env": "development",
        "dry_run": False,
    }
    values.update(overrides)
    return validate_reset_target(**values)


def test_valid_target_requires_exact_demo_database_and_confirm():
    plan = _validate()
    assert plan.database == DEMO_DATABASE_NAME
    assert plan.display_target == "127.0.0.1:15432/bank_assistant_demo"
    assert "users" in plan.table_names
    assert "conversations" in plan.table_names
    assert "alembic_version" not in plan.table_names


def test_dry_run_does_not_require_confirm_but_still_requires_demo_target():
    plan = _validate(confirm="", dry_run=True)
    assert plan.database == DEMO_DATABASE_NAME
    with pytest.raises(DemoResetError, match="DB=bank_assistant_demo"):
        _validate(expected_db="", confirm="", dry_run=True)


def test_refuses_missing_or_wrong_confirm():
    with pytest.raises(DemoResetError, match="CONFIRM=yes"):
        _validate(confirm="no")
    with pytest.raises(DemoResetError, match="CONFIRM=yes"):
        _validate(confirm="")


@pytest.mark.parametrize(
    "expected_db",
    ["", "bank_assistant_dev", "bank_assistant_test", "bank_assistant_restore_check"],
)
def test_refuses_non_demo_expected_targets(expected_db):
    with pytest.raises(DemoResetError):
        _validate(expected_db=expected_db)


@pytest.mark.parametrize(
    "database",
    [
        "bank_assistant_dev",
        "bank_assistant_test",
        "bank_assistant_restore_check",
        "postgres",
        "bank_assistant_demo_backup",
    ],
)
def test_refuses_url_pointing_at_other_databases(database):
    url = f"postgresql+psycopg://bank:local_only@127.0.0.1:15432/{database}"
    with pytest.raises(DemoResetError, match="拒绝"):
        _validate(database_url=url)


@pytest.mark.parametrize("app_env", ["staging", "production"])
def test_refuses_staging_or_production(app_env):
    with pytest.raises(DemoResetError, match="拒绝执行 demo-reset"):
        _validate(app_env=app_env)


def test_refuses_non_postgresql_url():
    with pytest.raises(DemoResetError, match="PostgreSQL"):
        _validate(database_url="sqlite:///demo.db")


def test_refuses_missing_url():
    with pytest.raises(DemoResetError, match="缺少 DATABASE_URL"):
        _validate(database_url="")
