"""expand content_hash to sha256

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-09 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "market_news",
        "content_hash",
        existing_type=sa.String(32),
        type_=sa.String(64),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "market_news",
        "content_hash",
        existing_type=sa.String(64),
        type_=sa.String(32),
        existing_nullable=False,
    )
