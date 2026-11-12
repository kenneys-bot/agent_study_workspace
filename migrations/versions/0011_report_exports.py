"""report_exports：报告导出快照（T21，M4-05；契约 §2/§3/§7 CT-12）

Revision ID: 0011_report_exports
Revises: 0010_reviews
Create Date: 2026-11-12

契约 §2 的 ReportExport 最低字段：export_id、inspection_id、review_revision、
format、content_hash、artifact_ref、generated_by。本表另外记录 created_at
（契约要求所有可变业务记录含时间戳），并让 ``artifact_ref`` 唯一，保证一个
产物文件只对应一条导出记录。

绑定语义：一次导出固定 ``(inspection_id, review_revision)``；后续人工复核产生
新 revision 时，旧行与旧产物都不再变化（CT-12）。表本身只追加：服务层没有更新/
删除入口，ORM 事件兜底拒绝 UPDATE/DELETE。

本 revision 只扩展当前单一 head（``0010_reviews``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_report_exports"
down_revision: str | None = "0010_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_exports",
        sa.Column("export_id", sa.Uuid(), nullable=False),
        sa.Column("inspection_id", sa.Uuid(), nullable=False),
        sa.Column("review_revision", sa.Integer(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_ref", sa.String(length=512), nullable=False),
        sa.Column("generated_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "format IN ('json', 'html')", name="ck_report_exports_format"
        ),
        sa.CheckConstraint(
            "review_revision >= 0", name="ck_report_exports_review_revision"
        ),
        sa.ForeignKeyConstraint(
            ["inspection_id"],
            ["inspection_runs.inspection_id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["generated_by"], ["users.user_id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("export_id"),
        sa.UniqueConstraint(
            "artifact_ref", name="uq_report_exports_artifact_ref"
        ),
    )
    op.create_index(
        "ix_report_exports_inspection_revision",
        "report_exports",
        ["inspection_id", "review_revision"],
        unique=False,
    )
    op.create_index(
        "ix_report_exports_created_at",
        "report_exports",
        ["created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_report_exports_created_at", table_name="report_exports")
    op.drop_index(
        "ix_report_exports_inspection_revision", table_name="report_exports"
    )
    op.drop_table("report_exports")
