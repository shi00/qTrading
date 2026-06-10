"""add publish_date to macro_economy

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-10 00:00:00.000000

Adds publish_date column to macro_economy for point-in-time queries.
publish_date represents the conservative estimated publication date:
  period month + 1 month, day 16 (e.g., period=2024-03-01 → publish_date=2024-04-16).

This ensures macro data is only visible in as_of queries after its
estimated publication date, preventing lookahead bias.

Also adds updated_at column for UPSERT timestamp tracking.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    """Add publish_date and updated_at columns to macro_economy."""
    if not _column_exists("macro_economy", "publish_date"):
        op.add_column(
            "macro_economy",
            sa.Column("publish_date", sa.Date(), nullable=True),
        )
    if not _column_exists("macro_economy", "updated_at"):
        op.add_column(
            "macro_economy",
            sa.Column("updated_at", sa.DateTime(), nullable=True),
        )

    # Backfill publish_date: period month + 1, day 16
    # Uses PostgreSQL MAKE_DATE for efficient server-side computation
    op.execute("""
        UPDATE macro_economy
        SET publish_date = CASE
            WHEN EXTRACT(MONTH FROM period) = 12 THEN
                MAKE_DATE(EXTRACT(YEAR FROM period)::int + 1, 1, 16)
            ELSE
                MAKE_DATE(EXTRACT(YEAR FROM period)::int, EXTRACT(MONTH FROM period)::int + 1, 16)
        END
        WHERE publish_date IS NULL
    """)

    # Create index on publish_date for as_of queries
    op.create_index(
        "ix_macro_economy_publish_date",
        "macro_economy",
        ["publish_date"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Remove publish_date and updated_at columns from macro_economy."""
    op.drop_index("ix_macro_economy_publish_date", table_name="macro_economy", if_exists=True)
    if _column_exists("macro_economy", "updated_at"):
        op.drop_column("macro_economy", "updated_at")
    if _column_exists("macro_economy", "publish_date"):
        op.drop_column("macro_economy", "publish_date")
