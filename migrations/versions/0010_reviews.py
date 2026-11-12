"""review_cases / review_decisions：人工复核闭环（T20，M4-04；契约 §2/§3）

Revision ID: 0010_reviews
Revises: 0009_inspections
Create Date: 2026-11-12

契约 §2 的 ReviewCase 最低字段：review_id、inspection_id、assignee_id、status、
revision；``inspection_id`` 在同一有效复核流内唯一——这里用部分唯一索引表达
（``status <> 'completed'``），完成后允许另起新的复核流，历史行保留。

契约 §2 的 ReviewDecision 最低字段：decision_id、review_id、finding_id、action、
final_verdict、reason、actor_id、expected_revision、created_at。追加保存：
服务层与 ORM 事件都拒绝 UPDATE/DELETE；``accepted_revision`` 记录接受后的
复核 revision（同一 review 内唯一），使决定历史可按 revision 稳定排序。

本 revision 只扩展当前单一 head（``0009_inspections``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_reviews"
down_revision: str | None = "0009_inspections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_cases",
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("last_reopen_reason", sa.Text(), nullable=True),
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
            "status IN ('pending', 'in_review', 'completed')",
            name="ck_review_cases_status",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_review_cases_revision"),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            ["inspection_runs.inspection_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("review_id"),
    )
    op.create_index(
        "ix_review_cases_scope_status",
        "review_cases",
        ["scope_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_review_cases_inspection_id",
        "review_cases",
        ["inspection_id"],
        unique=False,
    )
    # 同一 inspection 同时只能有一个未完成复核（部分唯一索引，CT-07 之外的
    # 创建去重也靠它兜底）；完成后允许新复核流。
    op.create_index(
        "uq_review_cases_active_inspection",
        "review_cases",
        ["inspection_id"],
        unique=True,
        postgresql_where=sa.text("status <> 'completed'"),
    )

    op.create_table(
        "review_decisions",
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("review_id", sa.Uuid(), nullable=False),
        sa.Column("finding_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("final_verdict", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("expected_revision", sa.Integer(), nullable=False),
        sa.Column("accepted_revision", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('confirm', 'reject', 'modify')",
            name="ck_review_decisions_action",
        ),
        sa.CheckConstraint(
            "final_verdict IN "
            "('hit', 'clear', 'insufficient_evidence', 'not_applicable')",
            name="ck_review_decisions_final_verdict",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0", name="ck_review_decisions_reason"
        ),
        sa.CheckConstraint(
            "expected_revision >= 1",
            name="ck_review_decisions_expected_revision",
        ),
        sa.CheckConstraint(
            "accepted_revision >= 2",
            name="ck_review_decisions_accepted_revision",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"], ["review_cases.review_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["finding_id"], ["findings.finding_id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("decision_id"),
        sa.UniqueConstraint(
            "review_id",
            "accepted_revision",
            name="uq_review_decisions_review_revision",
        ),
    )
    op.create_index(
        "ix_review_decisions_review_id",
        "review_decisions",
        ["review_id"],
        unique=False,
    )
    op.create_index(
        "ix_review_decisions_finding_id",
        "review_decisions",
        ["finding_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_review_decisions_finding_id", table_name="review_decisions"
    )
    op.drop_index(
        "ix_review_decisions_review_id", table_name="review_decisions"
    )
    op.drop_table("review_decisions")
    op.drop_index(
        "uq_review_cases_active_inspection", table_name="review_cases"
    )
    op.drop_index("ix_review_cases_inspection_id", table_name="review_cases")
    op.drop_index("ix_review_cases_scope_status", table_name="review_cases")
    op.drop_table("review_cases")
