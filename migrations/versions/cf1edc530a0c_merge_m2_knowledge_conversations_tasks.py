"""merge M2: knowledge + conversations/tasks

Revision ID: cf1edc530a0c
Revises: 0003_knowledge, 55554ee917e7
Create Date: 2026-11-12 16:36:34.049184

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = 'cf1edc530a0c'
down_revision: str | None = ('0003_knowledge', '55554ee917e7')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
