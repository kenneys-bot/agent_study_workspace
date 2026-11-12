"""knowledge_chunks：版本化索引的原文区间与 embedding 身份（T14，M3）

Revision ID: 0006_knowledge_chunks
Revises: cf1edc530a0c
Create Date: 2026-11-12

契约 §2 的 KnowledgeChunk：chunk_id、version_id、text、原文区间、
embedding_model_version、index_version。区间以 ``knowledge_versions.content``
的 Unicode 码点计数，``start_offset`` 含、``end_offset`` 不含（契约 §4）。

本 revision 只扩展当前单一 head（``cf1edc530a0c`` 合并后的 M2 头），不另起
分支：M3 的知识分块依赖 M2 的知识版本表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_knowledge_chunks"
down_revision: str | None = "cf1edc530a0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("embedding_model_version", sa.String(length=128), nullable=False),
        sa.Column("index_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "start_offset >= 0", name="ck_knowledge_chunks_start_offset"
        ),
        sa.CheckConstraint(
            "end_offset > start_offset", name="ck_knowledge_chunks_offsets"
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["knowledge_versions.version_id"],
            name="fk_knowledge_chunks_version_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chunk_id"),
        sa.UniqueConstraint(
            "version_id",
            "index_version",
            "start_offset",
            name="uq_knowledge_chunks_version_index_start",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_index_version",
        "knowledge_chunks",
        ["index_version"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_chunks_version_id",
        "knowledge_chunks",
        ["version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_version_id", table_name="knowledge_chunks")
    op.drop_index("ix_knowledge_chunks_index_version", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
