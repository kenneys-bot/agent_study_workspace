"""inspection_runs / findings：质检运行、规则快照与逐规则机器判断（T18，M4-03）

Revision ID: 0009_inspections
Revises: 8889f869f465
Create Date: 2026-11-12

契约 §2 的 InspectionRun 最低字段：inspection_id、conversation_id、
conversation_hash、rule_snapshot、run_id、status、error_code、started_at、
finished_at、parent_run_id；另加 scope_id（访问范围）、task_id（租约队列任务，
唯一）、diagnostics（安全诊断信息）与 created_at/updated_at。

契约 §2 的 Finding 最低字段：finding_id、inspection_id、rule_version_id、
machine_verdict、severity、rationale、evidence、knowledge_refs。
``machine_verdict`` 只能是 hit / clear / insufficient_evidence / not_applicable；
机器判断写入后不可覆盖（服务层 + ORM 事件双重兜底）。

幂等约束：``inspection_runs.task_id`` 唯一（一个任务只对应一次运行），
``findings (inspection_id, rule_version_id)`` 唯一（同一运行对同一规则版本
不会出现第二行）。这两条与 serve 层的条件更新一起保证「同一质检不会产生
两个最终结果」（CT-11）。

本 revision 只扩展当前单一 head（``8889f869f465``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_inspections"
down_revision: str | None = "8889f869f465"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inspection_runs",
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_hash", sa.String(length=64), nullable=False),
        sa.Column("rule_snapshot", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("diagnostics", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_inspection_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"], ["conversations.conversation_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["inspection_runs.inspection_id"]
        ),
        sa.ForeignKeyConstraint(["run_id"], ["model_runs.run_id"]),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.task_id"]),
        sa.PrimaryKeyConstraint("inspection_id"),
        sa.UniqueConstraint("task_id", name="uq_inspection_runs_task_id"),
    )
    op.create_index(
        "ix_inspection_runs_conversation_id",
        "inspection_runs",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_inspection_runs_scope_status",
        "inspection_runs",
        ["scope_id", "status"],
        unique=False,
    )

    op.create_table(
        "findings",
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("machine_verdict", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("knowledge_refs", sa.JSON(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "machine_verdict IN "
            "('hit', 'clear', 'insufficient_evidence', 'not_applicable')",
            name="ck_findings_machine_verdict",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high')", name="ck_findings_severity"
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            ["inspection_runs.inspection_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rule_version_id"], ["rule_versions.rule_version_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("finding_id"),
        sa.UniqueConstraint(
            "inspection_id",
            "rule_version_id",
            name="uq_findings_inspection_rule_version",
        ),
    )
    op.create_index(
        "ix_findings_inspection_id", "findings", ["inspection_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_findings_inspection_id", table_name="findings")
    op.drop_table("findings")
    op.drop_index("ix_inspection_runs_scope_status", table_name="inspection_runs")
    op.drop_index(
        "ix_inspection_runs_conversation_id", table_name="inspection_runs"
    )
    op.drop_table("inspection_runs")
