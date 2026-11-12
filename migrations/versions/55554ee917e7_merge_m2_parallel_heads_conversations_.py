"""merge M2 parallel heads (conversations, tasks)

Revision ID: 55554ee917e7
Revises: 0004_conversations, 0005_tasks
Create Date: 2026-11-12 16:33:04.655226

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '55554ee917e7'
down_revision: str | None = ('0004_conversations', '0005_tasks')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
