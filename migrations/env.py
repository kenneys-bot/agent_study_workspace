"""Alembic 环境：连接串来自 settings.DATABASE_URL，metadata 来自 infra.db.Base。

单元测试不经过这里；``make test-integration`` 在空库上执行
``alembic upgrade head``，验证“空库可迁移”。
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "src"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from bank_assistant.config.settings import settings  # noqa: E402
from bank_assistant.infra import models  # noqa: E402,F401  注册 ORM 实体到 metadata
from bank_assistant.infra.db import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """离线模式：输出 SQL，不建立连接。"""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：用 settings.DATABASE_URL 建连并执行迁移。"""
    connectable = create_engine(settings.DATABASE_URL, poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
