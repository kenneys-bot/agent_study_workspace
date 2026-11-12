"""knowledge：知识文档与知识版本的完整生命周期（M2-03）

Revision ID: 0003_knowledge
Revises: 0002_users
Create Date: 2026-11-12

契约 §2：KnowledgeDocument（document_id、title、source_type、source_uri、
business_type、scope_id）与 KnowledgeVersion（version_id、document_id、
version_no、content、content_hash、status、valid_from、valid_to、author_id、
reviewer_id、review_reason）。契约 §3：draft → in_review → published →
retired，审核退回为 draft；发布后内容不可覆盖；同一文档可多版本。

``revision`` 是状态转换的乐观并发版本号（契约 §5 的 request revision）。
知识分块（KnowledgeChunk）属于 M3 检索索引，不在本迁移内。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_knowledge"
down_revision: str | None = "0002_users"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_uri", sa.String(length=512), nullable=True),
        sa.Column("business_type", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint("document_id"),
    )
    op.create_index(
        "ix_knowledge_documents_business_type",
        "knowledge_documents",
        ["business_type"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_documents_scope_id",
        "knowledge_documents",
        ["scope_id"],
        unique=False,
    )

    op.create_table(
        "knowledge_versions",
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
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
            name="ck_knowledge_versions_status",
        ),
        sa.CheckConstraint(
            "valid_to is null or valid_to > valid_from",
            name="ck_knowledge_versions_valid_window",
        ),
        sa.CheckConstraint("revision >= 1", name="ck_knowledge_versions_revision"),
        sa.CheckConstraint(
            "version_no >= 1", name="ck_knowledge_versions_version_no"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.user_id"], name="fk_knowledge_versions_author_id"
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.document_id"],
            name="fk_knowledge_versions_document_id",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.user_id"],
            name="fk_knowledge_versions_reviewer_id",
        ),
        sa.PrimaryKeyConstraint("version_id"),
        sa.UniqueConstraint(
            "document_id",
            "version_no",
            name="uq_knowledge_versions_document_version_no",
        ),
    )
    op.create_index(
        "ix_knowledge_versions_author_id",
        "knowledge_versions",
        ["author_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_versions_document_id",
        "knowledge_versions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_versions_status",
        "knowledge_versions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_versions_status_valid_from",
        "knowledge_versions",
        ["status", "valid_from"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_knowledge_versions_status_valid_from", table_name="knowledge_versions"
    )
    op.drop_index("ix_knowledge_versions_status", table_name="knowledge_versions")
    op.drop_index(
        "ix_knowledge_versions_document_id", table_name="knowledge_versions"
    )
    op.drop_index(
        "ix_knowledge_versions_author_id", table_name="knowledge_versions"
    )
    op.drop_table("knowledge_versions")
    op.drop_index("ix_knowledge_documents_scope_id", table_name="knowledge_documents")
    op.drop_index(
        "ix_knowledge_documents_business_type", table_name="knowledge_documents"
    )
    op.drop_table("knowledge_documents")
