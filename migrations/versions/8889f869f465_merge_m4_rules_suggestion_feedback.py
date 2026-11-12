"""merge M4: rules + suggestion feedback

Revision ID: 8889f869f465
Revises: 0008_rules, 0008_suggestion_feedback
Create Date: 2026-11-12 17:14:11.055367

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '8889f869f465'
down_revision: str | None = ('0008_rules', '0008_suggestion_feedback')
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
