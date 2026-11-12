"""数据库基础设施：SQLAlchemy 2.0 同步引擎、会话工厂与就绪探针。

数据库是业务事实源（ADR-0002）；单元测试不依赖数据库，集成测试通过
``DATABASE_URL`` 指向真实 PostgreSQL。业务实体在 M2 才加入，这里只保留
最小骨架表，用于验证“空库可迁移”。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import DateTime, Integer, create_engine, func, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)

from bank_assistant.config.settings import settings


class Base(DeclarativeBase):
    """所有 ORM 实体的基类；Alembic 通过它取 metadata。"""


class SchemaMeta(Base):
    """最小骨架表：证明空库可迁移，不承载任何业务字段。"""

    __tablename__ = "schema_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _engine_kwargs(database_url: str) -> dict[str, Any]:
    """按方言给出引擎参数；PostgreSQL 就绪检查要快速失败。"""
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if make_url(database_url).get_backend_name() == "postgresql":
        kwargs["connect_args"] = {"connect_timeout": 3}
    return kwargs


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """按 settings.DATABASE_URL 创建进程级引擎；惰性，不在此建立连接。"""
    return create_engine(settings.DATABASE_URL, **_engine_kwargs(settings.DATABASE_URL))


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """同步会话工厂；事务边界由 session_scope 控制。"""
    return sessionmaker(bind=get_engine(), expire_on_commit=False, autoflush=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    """短事务边界：成功提交，异常回滚，最后关闭。"""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database() -> None:
    """执行真实 ``SELECT 1``；失败时抛出 SQLAlchemy 异常，由调用方判定。"""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
