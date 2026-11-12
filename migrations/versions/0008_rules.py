"""rule_sets / rule_versions：质检规则身份、版本内容与生命周期（T17，M4-01）

Revision ID: 0008_rules
Revises: 0007_suggestions
Create Date: 2026-11-12

契约 §2 的 RuleVersion 最低字段：rule_version_id、rule_id、version_no、
business_type、applicability、criteria、exceptions、severity、source_refs、
examples、status、demo_only；另加 author_id/reviewer_id/review_reason（发布者
与作者分离）、valid_from/valid_to（适用范围窗口）、content_hash（内容不可变与
演示种子幂等）、retire_reason 与 revision（乐观并发），以及 created_at/
updated_at（契约 §1 要求可变业务记录带时间列）。

``rule_sets`` 是稳定身份（rule_id、rule_code、类别、说明、访问范围、demo_only），
``rule_versions`` 是版本内容。只有 published 且在有效期窗口内的版本能参与质检；
同一规则发布新版本时，窗口重叠的旧已发布版本由服务层显式退役（见
``bank_assistant.rules.service`` 的决策说明），不会留下两个同时生效的版本。

本 revision 只扩展当前单一 head（``0007_suggestions``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_rules"
down_revision: str | None = "0007_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rule_sets",
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("rule_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column(
            "demo_only",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
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
        sa.PrimaryKeyConstraint("rule_id"),
        sa.UniqueConstraint(
            "scope_id", "rule_code", name="uq_rule_sets_scope_rule_code"
        ),
    )
    op.create_index(
        "ix_rule_sets_scope_id", "rule_sets", ["scope_id"], unique=False
    )
    op.create_index(
        "ix_rule_sets_category", "rule_sets", ["category"], unique=False
    )

    op.create_table(
        "rule_versions",
        sa.Column("rule_version_id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("business_type", sa.String(length=64), nullable=False),
        sa.Column("applicability", sa.JSON(), nullable=False),
        sa.Column("criteria", sa.JSON(), nullable=False),
        sa.Column("exceptions", sa.JSON(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("source_refs", sa.JSON(), nullable=False),
        sa.Column("examples", sa.JSON(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "demo_only",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("author_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("retire_reason", sa.Text(), nullable=True),
        sa.Column(
            "revision", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
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
            "status in ('draft', 'in_review', 'published', 'retired')",
            name="ck_rule_versions_status",
        ),
        sa.CheckConstraint(
            "severity IN ('low', 'medium', 'high')",
            name="ck_rule_versions_severity",
        ),
        sa.CheckConstraint(
            "valid_to is null or valid_to > valid_from",
            name="ck_rule_versions_valid_window",
        ),
        sa.CheckConstraint("version_no >= 1", name="ck_rule_versions_version_no"),
        sa.CheckConstraint("revision >= 1", name="ck_rule_versions_revision"),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.user_id"], name="fk_rule_versions_author_id"
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.user_id"], name="fk_rule_versions_reviewer_id"
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["rule_sets.rule_id"],
            name="fk_rule_versions_rule_id",
        ),
        sa.PrimaryKeyConstraint("rule_version_id"),
        sa.UniqueConstraint(
            "rule_id", "version_no", name="uq_rule_versions_rule_version_no"
        ),
    )
    op.create_index(
        "ix_rule_versions_rule_id", "rule_versions", ["rule_id"], unique=False
    )
    op.create_index(
        "ix_rule_versions_status", "rule_versions", ["status"], unique=False
    )
    op.create_index(
        "ix_rule_versions_author_id", "rule_versions", ["author_id"], unique=False
    )
    op.create_index(
        "ix_rule_versions_status_valid_from",
        "rule_versions",
        ["status", "valid_from"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_rule_versions_status_valid_from", table_name="rule_versions"
    )
    op.drop_index("ix_rule_versions_author_id", table_name="rule_versions")
    op.drop_index("ix_rule_versions_status", table_name="rule_versions")
    op.drop_index("ix_rule_versions_rule_id", table_name="rule_versions")
    op.drop_table("rule_versions")
    op.drop_index("ix_rule_sets_category", table_name="rule_sets")
    op.drop_index("ix_rule_sets_scope_id", table_name="rule_sets")
    op.drop_table("rule_sets")
