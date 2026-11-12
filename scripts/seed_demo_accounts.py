#!/usr/bin/env python
"""显式创建两个演示账号（作者 + 审核人）；知识导入本身不创建任何用户。

M3 的演示知识必须走真实生命周期：作者提交、另一审核人发布。因此需要两个
不同账号。本脚本只负责账号，且幂等：

- 已存在且角色/范围一致 → 跳过；
- 已存在但角色/范围不一致 → 非零退出，不静默改权限；
- ``APP_ENV`` 为 staging/production 时拒绝执行（演示账号只用于本地演示）。

演示密码是公开的演示值（写在 README），不是任何环境的秘密。
"""

from __future__ import annotations

import sys
from uuid import uuid4

from sqlalchemy import select

from bank_assistant.config.settings import settings
from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import User
from bank_assistant.knowledge.demo_seed import (
    DEMO_AUTHOR_USERNAME,
    DEMO_REVIEWER_USERNAME,
)
from bank_assistant.security.passwords import hash_password

# 仅用于本地演示的固定密码；不是秘密，也不能用于任何真实环境。
DEMO_ACCOUNT_PASSWORD = "demo-only-correct-horse-battery"

_SPECS = (
    (DEMO_AUTHOR_USERNAME, "agent"),
    (DEMO_REVIEWER_USERNAME, "reviewer"),
)


def main() -> int:
    if settings.APP_ENV in {"staging", "production"}:
        print(
            "REFUSE: 演示账号只允许在 development/testing 环境创建"
            f"（当前 APP_ENV={settings.APP_ENV}）",
            file=sys.stderr,
        )
        return 2

    scope_id = settings.DEMO_SCOPE_ID
    created: list[str] = []
    existing: list[str] = []
    with session_scope() as session:
        for username, role in _SPECS:
            user = session.scalar(select(User).where(User.username == username))
            if user is None:
                session.add(
                    User(
                        user_id=uuid4(),
                        username=username,
                        password_hash=hash_password(DEMO_ACCOUNT_PASSWORD),
                        role=role,
                        scope_id=scope_id,
                        active=True,
                    )
                )
                created.append(username)
                continue
            if user.role != role or user.scope_id != scope_id or not user.active:
                print(
                    f"FAIL: 账号 {username} 已存在但角色/范围/状态不一致"
                    f"（role={user.role}, scope={user.scope_id}, active={user.active}），"
                    f"期望 role={role}, scope={scope_id}；请人工核对，脚本不静默改权限",
                    file=sys.stderr,
                )
                return 2
            existing.append(username)

    print(f"seed-demo-accounts: scope={scope_id}")
    print(f"  新建账号：{', '.join(created) if created else '无'}")
    print(f"  已存在：{', '.join(existing) if existing else '无'}")
    print("  账号：demo-author（agent）/ demo-reviewer（reviewer）")
    print(f"  演示密码（demo-only，不是秘密）：{DEMO_ACCOUNT_PASSWORD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
