"""baseline: 最小骨架表 schema_meta

Revision ID: 0001_schema_meta
Revises:
Create Date: 2026-11-12

只建立最小骨架，用来验证“空库可迁移”；M2 的业务实体各自新增 revision，
不要修改本文件。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001_schema_meta"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "schema_meta",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("schema_meta")
