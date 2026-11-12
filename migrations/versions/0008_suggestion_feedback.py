"""suggestion_feedback：坐席采纳/编辑/拒绝反馈（T16，M3-05）

Revision ID: 0008_suggestion_feedback
Revises: 0007_suggestions
Create Date: 2026-11-12

契约 §2 的 SuggestionFeedback 最低字段：feedback_id、suggestion_id、actor_id、
action、edited_text、reason；另加 created_at（追加表没有 updated_at）。

- ``suggestion_id`` 是外键：反馈绑定到**具体的建议记录版本**，重新生成候选
  答复会产生新的 suggestion_id，历史反馈不会被改挂。
- ``action`` 只有 adopt/edit/reject；edit 必须有非空 edited_text（编辑文本
  留存），reject 必须有非空 reason，其他动作不得携带 edited_text。
- 本表只追加，不更新：服务层没有修改入口，ORM 的 before_update 事件兜底。

反馈**不是正确性标签**：adopt 只表示坐席采用，不代表答复语义正确，也不得
作为评测的 ground-truth 标签。

本 revision 只扩展当前单一 head（``0007_suggestions``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_suggestion_feedback"
down_revision: str | None = "0007_suggestions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suggestion_feedback",
        sa.Column("feedback_id", sa.Uuid(), nullable=False),
        sa.Column("suggestion_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("edited_text", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "action IN ('adopt', 'edit', 'reject')",
            name="ck_suggestion_feedback_action",
        ),
        sa.CheckConstraint(
            "action <> 'edit' OR (edited_text IS NOT NULL "
            "AND length(trim(edited_text)) > 0)",
            name="ck_suggestion_feedback_edit_text",
        ),
        sa.CheckConstraint(
            "action <> 'reject' OR (reason IS NOT NULL "
            "AND length(trim(reason)) > 0)",
            name="ck_suggestion_feedback_reject_reason",
        ),
        sa.CheckConstraint(
            "action = 'edit' OR edited_text IS NULL",
            name="ck_suggestion_feedback_edited_text_only_edit",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_id"],
            ["suggestions.suggestion_id"],
            name="fk_suggestion_feedback_suggestion_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.user_id"],
            name="fk_suggestion_feedback_actor_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("feedback_id"),
    )
    op.create_index(
        "ix_suggestion_feedback_suggestion_created",
        "suggestion_feedback",
        ["suggestion_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_suggestion_feedback_suggestion_created",
        table_name="suggestion_feedback",
    )
    op.drop_table("suggestion_feedback")
