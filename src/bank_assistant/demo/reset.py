"""演示库重置的安全闸门与清空逻辑（T25，M5-04；ADR-0006 过渡期）。

要求（计划书 M5-04）：

- 只允许显式 demo 环境：目标库必须**恰好**是 ``bank_assistant_demo``；
- 必须二次确认目标：``CONFIRM=yes``（dry-run 除外），且显式传入 ``--db``；
- **绝不接触** ``bank_assistant_dev`` / ``bank_assistant_test`` /
  ``bank_assistant_restore_check`` 或任何其他库；
- 生产/预发布环境拒绝执行。

这里的安全判断是纯函数，可单测；真正的清空只对已通过校验的 PostgreSQL
连接执行 ``TRUNCATE ... RESTART IDENTITY CASCADE``，保留迁移版本表。
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text

from bank_assistant.infra.db import Base

DEMO_DATABASE_NAME = "bank_assistant_demo"
FORBIDDEN_MARKERS = ("dev", "test", "restore", "prod", "staging")
CONFIRM_PHRASE = "yes"


class DemoResetError(ValueError):
    """目标不满足显式 demo 约束；调用方必须非零退出。"""


@dataclass(frozen=True, slots=True)
class ResetPlan:
    """通过安全校验后的重置目标；展示时只显示 host/port/db，不含凭据。"""

    database_url: str
    host: str
    port: int | None
    database: str
    table_names: tuple[str, ...]

    @property
    def display_target(self) -> str:
        port = f":{self.port}" if self.port is not None else ""
        return f"{self.host}{port}/{self.database}"


def _database_from_url(database_url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(database_url)
    scheme = parsed.scheme.lower()
    if not scheme.startswith("postgresql"):
        raise DemoResetError(
            f"demo-reset 只支持 PostgreSQL，拒绝 scheme={scheme or '<empty>'}"
        )
    database = (parsed.path or "").lstrip("/")
    host = parsed.hostname or ""
    if not database or not host:
        raise DemoResetError("DATABASE_URL 缺少 host 或 database，拒绝执行")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DemoResetError(f"DATABASE_URL 端口非法：{exc}") from exc
    return host, database, port


def validate_reset_target(
    *,
    database_url: str,
    expected_db: str,
    confirm: str,
    app_env: str = "development",
    dry_run: bool = False,
) -> ResetPlan:
    """校验目标；任何不满足都抛 :class:`DemoResetError`，绝不“尽力而为”。"""
    if not database_url or not database_url.strip():
        raise DemoResetError("缺少 DATABASE_URL；demo-reset 不会猜测数据库")
    if expected_db != DEMO_DATABASE_NAME:
        raise DemoResetError(
            "必须显式指定目标库：DB=bank_assistant_demo"
            f"（收到 {expected_db or '<empty>'}）"
        )
    if app_env in {"staging", "production"}:
        raise DemoResetError(
            f"APP_ENV={app_env} 下拒绝执行 demo-reset（只允许本地演示/测试环境）"
        )
    if not dry_run and confirm.strip().lower() != CONFIRM_PHRASE:
        raise DemoResetError("缺少二次确认：CONFIRM=yes")
    if expected_db in FORBIDDEN_MARKERS or any(
        marker in expected_db for marker in ("dev", "test", "restore")
    ):
        raise DemoResetError(f"拒绝清空疑似非演示库：{expected_db}")

    host, database, port = _database_from_url(database_url)
    if database != DEMO_DATABASE_NAME:
        raise DemoResetError(
            f"DATABASE_URL 指向 {database!r}，不是显式演示库 "
            f"{DEMO_DATABASE_NAME!r}；拒绝执行"
        )
    if any(marker in database for marker in ("dev", "test", "restore")):
        raise DemoResetError(f"拒绝清空疑似非演示库：{database}")

    table_names = tuple(
        table.name
        for table in Base.metadata.sorted_tables
        if table.name != "alembic_version"
    )
    return ResetPlan(
        database_url=database_url,
        host=host,
        port=port,
        database=database,
        table_names=table_names,
    )


def truncate_demo_data(plan: ResetPlan) -> None:
    """清空已校验演示库的业务表；保留 ``alembic_version``。"""
    engine = create_engine(plan.database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            # 表名来自 SQLAlchemy metadata，不是用户输入；仍用双引号避免保留字。
            quoted = ", ".join(f'"{name}"' for name in plan.table_names)
            connection.execute(text(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE"))
    finally:
        engine.dispose()


__all__ = [
    "CONFIRM_PHRASE",
    "DEMO_DATABASE_NAME",
    "DemoResetError",
    "ResetPlan",
    "truncate_demo_data",
    "validate_reset_target",
]
