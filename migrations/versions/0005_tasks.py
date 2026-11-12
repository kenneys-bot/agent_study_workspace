"""tasks 与 idempotency_keys：任务租约与持久化幂等绑定（T13，M2-05）

Revision ID: 0005_tasks
Revises: 0002_users
Create Date: 2026-11-12

契约 §2 的 Task 最低字段，外加 ``scope_id`` 做跨范围可见性判定；
``idempotency_keys`` 把键绑定到 ``(user_id, endpoint, idempotency_key)``，
保存输入哈希与原响应，保证重启与并发下判定一致（CT-05/CT-06）。

本 revision 只从 ``0002_users`` 分叉；并行票的 head 由 Lead 的合并
revision 统一，不在本文件里 reconcile。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_tasks"
down_revision: str | None = "0002_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STATUS_CHECK = "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')"


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("task_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=True),
        sa.Column("payload_ref", sa.String(length=512), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column(
            "lease_expires_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "attempts",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("result_ref", sa.String(length=512), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(STATUS_CHECK, name="ck_tasks_status"),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index(
        "ix_tasks_claim",
        "tasks",
        ["type", "status", "lease_expires_at"],
        unique=False,
    )
    op.create_index("ix_tasks_scope_id", "tasks", ["scope_id"], unique=False)

    op.create_table(
        "idempotency_keys",
        sa.Column("idempotency_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_snapshot", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("idempotency_id"),
        sa.UniqueConstraint(
            "user_id",
            "endpoint",
            "idempotency_key",
            name="uq_idempotency_keys_binding",
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_tasks_scope_id", table_name="tasks")
    op.drop_index("ix_tasks_claim", table_name="tasks")
    op.drop_table("tasks")
