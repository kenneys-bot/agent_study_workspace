#!/usr/bin/env python
"""幂等导入六段合成演示会话，并用显式回放生成待复核质检任务（T25）。

前置：``make seed-demo-accounts`` / 知识 / 规则已导入。本脚本不创建用户，
只使用 ``demo-author`` 与 ``demo-reviewer``；缺少账号时非零退出。

演示状态清单写到 ``DEMO_SEED_STATE``（默认 ``$DATA_ROOT/demo-seed-state.json``，
已 gitignore），供演示脚本与浏览器 e2e 定位会话/复核 ID。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from sqlalchemy import select

from bank_assistant.config.settings import settings
from bank_assistant.demo.conversations import (
    DEMO_AUTHOR_USERNAME,
    DEMO_REVIEWER_USERNAME,
    DemoConversationError,
    import_demo_conversations,
    load_demo_conversations,
    write_seed_state,
)
from bank_assistant.infra.db import session_scope
from bank_assistant.infra.models import User
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


def _state_path() -> Path:
    configured = os.environ.get("DEMO_SEED_STATE", "").strip()
    if configured:
        return Path(configured)
    data_root = os.environ.get("DATA_ROOT", ".local-data").strip() or ".local-data"
    return Path(data_root) / "demo-seed-state.json"


def main() -> int:
    try:
        entries = load_demo_conversations()
    except DemoConversationError as exc:
        print(f"FAIL: 演示会话种子不合法：{exc}", file=sys.stderr)
        return 2

    with session_scope() as session:
        author = _identity(DEMO_AUTHOR_USERNAME, session)
        reviewer = _identity(DEMO_REVIEWER_USERNAME, session)
        if author is None or reviewer is None:
            print(
                "FAIL: 未找到演示账号（demo-author / demo-reviewer）。"
                "请先运行 `make seed-demo-accounts`；会话导入不会创建用户。",
                file=sys.stderr,
            )
            return 2
        try:
            report = import_demo_conversations(
                session,
                entries,
                author=author,
                reviewer=reviewer,
                scope_id=settings.DEMO_SCOPE_ID,
            )
        except DemoConversationError as exc:
            print(f"FAIL: 演示会话导入失败：{exc}", file=sys.stderr)
            return 2

    state_path = _state_path()
    try:
        write_seed_state(state_path, entries, scope_id=settings.DEMO_SCOPE_ID)
    except OSError as exc:
        print(f"FAIL: 无法写演示状态清单 {state_path}：{exc}", file=sys.stderr)
        return 2

    print(f"seed-demo-conversations: scope={settings.DEMO_SCOPE_ID}")
    print(f"  会话种子：{report.total_entries} 段（S1/S2/S3 × 正常/风险）")
    print(f"  新建会话：{report.created_conversations}")
    print(f"  已存在跳过（内容哈希一致）：{report.skipped_conversations}")
    print(f"  新建质检任务：{report.created_inspections}")
    print(f"  新建待复核任务：{report.created_reviews}")
    if report.review_ids:
        print("  复核 ID（demo-reviewer 可处理）：")
        for review_id in report.review_ids:
            print(f"    {review_id}")
    print("  回放：质检由显式 replay worker 执行，未调用任何云模型")
    print(f"  状态清单：{state_path}（已 gitignore）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
