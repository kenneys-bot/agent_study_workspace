"""conversations / turns / audit_events：会话持久化与追加式审计（M2-04）

Revision ID: 0004_conversations
Revises: 0002_users
Create Date: 2026-11-12

契约 §2 的最低字段：

- Conversation：conversation_id、scope_id、owner_id、source_type、raw_text、
  content_hash、data_classification、redaction_applied；
- Turn：turn_id、conversation_id、ordinal、speaker、content、timestamp，
  ``(conversation_id, ordinal)`` 唯一；
- AuditEvent：event_id、actor_id、action、resource_type/id、before/after 摘要、
  trace_id、timestamp。

本 revision 有意直接接在 ``0002_users`` 之后：并行票同样从 0002 分叉，由
Lead 在集成时新增 merge revision，这里不主动合并其他分支的 head。

时间列统一 ``timestamptz``（UTC）。审计只追加，不设 ``updated_at``。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_conversations"
down_revision: str | None = "0002_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("source_ref", sa.String(length=512), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("data_classification", sa.String(length=32), nullable=False),
        sa.Column(
            "redaction_applied",
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
        sa.ForeignKeyConstraint(
            ["owner_id"], ["users.user_id"], name="fk_conversations_owner_id_users"
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversations_scope_id", "conversations", ["scope_id"], unique=False
    )
    op.create_index(
        "ix_conversations_owner_id", "conversations", ["owner_id"], unique=False
    )

    op.create_table(
        "turns",
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("speaker", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            name="fk_turns_conversation_id_conversations",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.UniqueConstraint(
            "conversation_id", "ordinal", name="uq_turns_conversation_ordinal"
        ),
    )
    op.create_index("ix_turns_conversation_id", "turns", ["conversation_id"], unique=False)

    op.create_table(
        "audit_events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=32), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=False),
        sa.Column("before_summary", sa.JSON(), nullable=False),
        sa.Column("after_summary", sa.JSON(), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_audit_events_actor_id", "audit_events", ["actor_id"], unique=False)
    op.create_index("ix_audit_events_trace_id", "audit_events", ["trace_id"], unique=False)
    op.create_index(
        "ix_audit_events_resource",
        "audit_events",
        ["resource_type", "resource_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_events_resource", table_name="audit_events")
    op.drop_index("ix_audit_events_trace_id", table_name="audit_events")
    op.drop_index("ix_audit_events_actor_id", table_name="audit_events")
    op.drop_table("audit_events")

    op.drop_index("ix_turns_conversation_id", table_name="turns")
    op.drop_table("turns")

    op.drop_index("ix_conversations_owner_id", table_name="conversations")
    op.drop_index("ix_conversations_scope_id", table_name="conversations")
    op.drop_table("conversations")
