#!/usr/bin/env python
"""幂等导入合成演示知识（M3，契约 §3）。

前置：两个演示账号已由显式脚本创建（``make seed-demo-accounts``）。本脚本
**不创建用户**，也不在导入时补建账号；账号缺失时非零退出并提示。

导入走真实生命周期：作者提交 → 另一审核人发布。重复执行按
``(scope_id, title) + version_no`` 去重；内容一致则跳过，内容变化则报错，
必须递增 version_no。
"""

from __future__ import annotations

import sys

from sqlalchemy import select

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import User
from bank_assistant.knowledge.demo_seed import (
    DEMO_AUTHOR_USERNAME,
    DEMO_REVIEWER_USERNAME,
    DemoSeedError,
    import_demo_knowledge,
    load_demo_entries,
)
from bank_assistant.security.identity import Identity


def _identity(username: str, session) -> Identity | None:
    user = session.scalar(select(User).where(User.username == username))
    if user is None or not user.active:
        return None
    return Identity(
        user_id=user.user_id,
        username=user.username,
        role=user.role,
        scope_id=user.scope_id,
    )


def main() -> int:
    scope_id = settings.DEMO_SCOPE_ID
    try:
        entries = load_demo_entries()
    except DemoSeedError as exc:
        print(f"FAIL: 演示知识种子不合法：{exc}", file=sys.stderr)
        return 2

    with session_scope() as session:
        author = _identity(DEMO_AUTHOR_USERNAME, session)
        reviewer = _identity(DEMO_REVIEWER_USERNAME, session)
        if author is None or reviewer is None:
            print(
                "FAIL: 未找到演示账号（demo-author / demo-reviewer）。"
                "请先运行 `make seed-demo-accounts`；知识导入不会创建用户。",
                file=sys.stderr,
            )
            return 2
        try:
            report = import_demo_knowledge(
                session,
                entries,
                author=author,
                reviewer=reviewer,
                scope_id=scope_id,
            )
        except DemoSeedError as exc:
            print(f"FAIL: 演示知识导入失败：{exc}", file=sys.stderr)
            return 2

    print(f"seed-demo: scope={scope_id}，种子条目 {report.total_entries} 条")
    print(f"  新建文档：{report.created_documents}")
    print(f"  本次发布版本：{report.published_versions}")
    print(f"  已存在跳过（内容哈希一致）：{report.skipped_versions}")
    if report.published_versions == 0:
        print("  结果：幂等重跑，数据库未新增任何版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
