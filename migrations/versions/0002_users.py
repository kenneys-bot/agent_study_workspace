"""users：登录主体、角色与业务访问范围（M2-02）

Revision ID: 0002_users
Revises: 0001_schema_meta
Create Date: 2026-11-12

契约 §2 的 User/Role 最低集合用一张 ``users`` 表表达：user_id、role、
scope_id、active，加用户名与 Argon2 密码哈希。只保存哈希，禁止明文。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_users"
down_revision: str | None = "0001_schema_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.String(length=64), nullable=False),
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username", name="uq_users_username"),
    )
    op.create_index("ix_users_scope_id", "users", ["scope_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_scope_id", table_name="users")
    op.drop_table("users")
