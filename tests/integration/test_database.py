"""空库迁移与真实数据库连通性的集成测试。

由 ``make test-integration`` / ``scripts/test-integration.sh`` 提供一次性
PostgreSQL 18 容器；这里不启动、不清理容器，只在已迁移的库上断言事实。
"""

from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.integration


def _alembic_head() -> str:
    script = ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))
    head = script.get_current_head()
    assert head is not None, "migrations 中没有 head revision"
    return head


def test_empty_database_migrates_and_is_reachable():
    from bank_assistant.infra.db import get_engine, ping_database

    engine = get_engine()
    # 防止误把 sqlite 当成验收对象（ADR-0002）
    assert engine.dialect.name == "postgresql"

    ping_database()
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        current = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()

    assert current == _alembic_head()

    inspector = inspect(engine)
    assert {"alembic_version", "schema_meta"} <= set(inspector.get_table_names())
    columns = {column["name"] for column in inspector.get_columns("schema_meta")}
    assert {"id", "created_at"} <= columns
