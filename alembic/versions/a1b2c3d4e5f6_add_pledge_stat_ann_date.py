"""add pledge_stat ann_date column

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-05-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("pledge_stat", sa.Column("ann_date", sa.Date(), nullable=True))
    op.execute("UPDATE pledge_stat SET ann_date = end_date + INTERVAL '3 days' WHERE ann_date IS NULL")


def downgrade() -> None:
    op.drop_column("pledge_stat", "ann_date")
