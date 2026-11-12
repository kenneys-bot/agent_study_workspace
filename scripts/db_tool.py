#!/usr/bin/env python
"""数据库小工具（ADR-0006）：``ping`` 与 ``reset-test``。

只读取环境变量 ``DATABASE_URL``（由调用脚本从 .env 或显式覆盖注入），
不读其它配置、不打印任何含密码的连接串；输出只到 ``host:port/db`` 粒度。

安全约束：``reset-test`` 只允许清空以 ``_test`` 结尾的库，且显式拒绝 dev/demo/restore。
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import urlsplit

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError


class DbToolError(RuntimeError):
    """目标或连接不满足约束；调用方必须非零退出。"""


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        raise DbToolError("缺少 DATABASE_URL；不会猜测目标数据库")
    return url


def _target(url: str) -> tuple[str, int | None, str]:
    parsed = urlsplit(url)
    if not parsed.scheme.startswith("postgresql"):
        raise DbToolError(f"只支持 PostgreSQL，拒绝 scheme={parsed.scheme or '<empty>'}")
    database = (parsed.path or "").lstrip("/")
    host = parsed.hostname or ""
    if not database or not host:
        raise DbToolError("DATABASE_URL 缺少 host 或 database")
    try:
        port = parsed.port
    except ValueError as exc:
        raise DbToolError(f"DATABASE_URL 端口非法：{exc}") from exc
    return host, port, database


def _display(host: str, port: int | None, database: str) -> str:
    return f"{host}{f':{port}' if port else ''}/{database}"


def ping() -> int:
    url = _database_url()
    host, port, database = _target(url)
    try:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5},
        )
        try:
            with engine.connect() as connection:
                if connection.execute(text("SELECT 1")).scalar_one() != 1:
                    raise DbToolError("SELECT 1 返回了非 1 的结果")
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        # 只输出异常类型与目标，绝不输出连接串（可能含密码）。
        raise DbToolError(
            f"无法连接 {_display(host, port, database)}（{type(exc).__name__}）"
        ) from None
    print(f"DB 可达：{_display(host, port, database)}")
    return 0


def reset_test() -> int:
    url = _database_url()
    host, port, database = _target(url)
    if not database.endswith("_test"):
        raise DbToolError(f"reset-test 只允许 *_test 库，拒绝 {database!r}")
    if any(marker in database for marker in ("dev", "demo", "restore")):
        raise DbToolError(f"reset-test 拒绝疑似非测试库：{database!r}")

    try:
        # DDL 需要能跨语句执行；AUTOCOMMIT 避免 DROP/CREATE 被包在未提交事务里。
        engine = create_engine(url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
                connection.execute(text("CREATE SCHEMA public"))
        finally:
            engine.dispose()
    except SQLAlchemyError as exc:
        raise DbToolError(
            f"重置 {_display(host, port, database)} 的 public schema 失败"
            f"（{type(exc).__name__}）"
        ) from None
    print(f"已重置 {_display(host, port, database)} 的 public schema（只影响 *_test）")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-0006 数据库小工具")
    parser.add_argument("command", choices=("ping", "reset-test"))
    args = parser.parse_args(argv)
    try:
        return ping() if args.command == "ping" else reset_test()
    except DbToolError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
