"""model_runs / suggestions：候选答复端到端（T15，M3）

Revision ID: 0007_suggestions
Revises: 0006_knowledge_chunks
Create Date: 2026-11-12

契约 §2 的 Suggestion 最低字段：suggestion_id、conversation_id、run_id、
query、rewritten_query、business_type、status、answer、citations、
retrieval_snapshot、mode；另加 created_at / updated_at（契约 §1 要求可变业务
记录带时间列）与 scope_id（授权范围，服务端写入）。

run 元数据选择**独立表** ``model_runs``，而不是塞进 ``retrieval_snapshot``：
契约 §1 要求每次模型运行记录模型、提示词、知识快照、索引版本与代码 SHA，
独立表让“一次运行”可被直接查询与关联，也避免把运行身份混进检索快照的
可变结构。没有模型调用的业务短路同样写一行 ``model='none'`` 的运行记录。

本 revision 只扩展当前单一 head（``0006_knowledge_chunks``），不另起分支。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_suggestions"
down_revision: str | None = "0006_knowledge_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_runs",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("knowledge_snapshot", sa.JSON(), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=True),
        sa.Column("code_sha", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("run_id"),
    )

    op.create_table(
        "suggestions",
        sa.Column("suggestion_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("rewritten_query", sa.Text(), nullable=True),
        sa.Column("business_type", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column(
            "answer", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("missing_information", sa.JSON(), nullable=False),
        sa.Column("retrieval_snapshot", sa.JSON(), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
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
            "status IN ('answered', 'needs_clarification', 'needs_human', 'failed')",
            name="ck_suggestions_status",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            name="fk_suggestions_conversation_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["model_runs.run_id"],
            name="fk_suggestions_run_id",
        ),
        sa.PrimaryKeyConstraint("suggestion_id"),
    )
    op.create_index(
        "ix_suggestions_conversation_id",
        "suggestions",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_suggestions_scope_created",
        "suggestions",
        ["scope_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_suggestions_run_id", "suggestions", ["run_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_suggestions_run_id", table_name="suggestions")
    op.drop_index("ix_suggestions_scope_created", table_name="suggestions")
    op.drop_index("ix_suggestions_conversation_id", table_name="suggestions")
    op.drop_table("suggestions")
    op.drop_table("model_runs")
