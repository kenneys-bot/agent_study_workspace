#!/usr/bin/env python
"""幂等导入合成演示规则集（M4-01，契约 §2/§3）。

前置：两个演示账号已由显式脚本创建（``make seed-demo-accounts``，``make
seed-demo`` 会先执行它）。本脚本**不创建用户**，账号缺失时非零退出。

导入走真实生命周期：作者提交 → 另一审核人发布；演示规则全部
``demo_only=true``，说明注明「虚构演示制度」，不对应任何真实银行规则。
重复执行按 ``(scope_id, rule_code) + version_no`` 去重；内容一致则跳过，
内容变化必须递增 version_no（不能覆盖已发布版本）。
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
)
from bank_assistant.rules.demo_seed import (
    DemoRuleSeedError,
    import_demo_rules,
    load_demo_rules,
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
        entries = load_demo_rules()
    except DemoRuleSeedError as exc:
        print(f"FAIL: 演示规则种子不合法：{exc}", file=sys.stderr)
        return 2

    with session_scope() as session:
        author = _identity(DEMO_AUTHOR_USERNAME, session)
        reviewer = _identity(DEMO_REVIEWER_USERNAME, session)
        if author is None or reviewer is None:
            print(
                "FAIL: 未找到演示账号（demo-author / demo-reviewer）。"
                "请先运行 `make seed-demo-accounts`；规则导入不会创建用户。",
                file=sys.stderr,
            )
            return 2
        try:
            report = import_demo_rules(
                session,
                entries,
                author=author,
                reviewer=reviewer,
                scope_id=scope_id,
            )
        except DemoRuleSeedError as exc:
            print(f"FAIL: 演示规则导入失败：{exc}", file=sys.stderr)
            return 2

    print(f"seed-demo-rules: scope={scope_id}，种子规则 {report.total_rules} 条")
    print(f"  新建规则：{report.created_rules}")
    print(f"  本次发布版本：{report.published_versions}")
    print(f"  已存在跳过（内容哈希一致）：{report.skipped_versions}")
    if report.published_versions == 0:
        print("  结果：幂等重跑，数据库未新增任何规则版本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
