#!/usr/bin/env python
"""``make demo-reset`` 的安全入口：只清空显式指定的 ``bank_assistant_demo``。

安全规则见 :mod:`bank_assistant.demo.reset`：

- 必须显式 ``--db bank_assistant_demo`` 且 ``CONFIRM=yes``；
- ``DATABASE_URL`` 的库名必须恰好是 ``bank_assistant_demo``；
- 拒绝 dev/test/restore 库与 staging/production 环境；
- ``--dry-run`` 只打印目标和表清单，不连接数据库、不删除任何东西。
"""

from __future__ import annotations

import argparse
import os
import sys

from bank_assistant.demo.reset import (
    DemoResetError,
    truncate_demo_data,
    validate_reset_target,
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重置本地演示数据库（仅 demo）")
    parser.add_argument(
        "--db",
        dest="expected_db",
        default="",
        help="必须显式指定 bank_assistant_demo",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="二次确认短语：yes（dry-run 除外）",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", ""),
        help="目标数据库 URL（默认 DATABASE_URL）",
    )
    parser.add_argument(
        "--app-env",
        default=os.environ.get("APP_ENV", "development"),
        help="应用环境（默认 APP_ENV）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只校验并打印计划，不连接数据库",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        plan = validate_reset_target(
            database_url=args.database_url,
            expected_db=args.expected_db,
            confirm=args.confirm,
            app_env=args.app_env,
            dry_run=args.dry_run,
        )
    except DemoResetError as exc:
        print(f"REFUSE: {exc}", file=sys.stderr)
        return 2

    print(f"demo-reset: 目标 {plan.display_target}")
    print(f"  数据库：{plan.database}（仅此一个库会被清空）")
    print(f"  表：{len(plan.table_names)} 张（保留 alembic_version）")
    if args.dry_run:
        print("  dry-run：未连接数据库，未删除任何数据")
        return 0

    try:
        truncate_demo_data(plan)
    except Exception as exc:  # noqa: BLE001 - 连接/权限失败必须显式失败
        print(
            f"FAIL: 清空演示库失败（{type(exc).__name__}）：{exc}",
            file=sys.stderr,
        )
        return 2
    print("  已清空业务表；请接着运行 make seed-demo 与 make index-rebuild")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
