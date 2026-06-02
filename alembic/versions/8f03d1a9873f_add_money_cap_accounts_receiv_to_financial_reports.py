"""add money_cap accounts_receiv to financial_reports

Revision ID: 8f03d1a9873f
Revises: f6586a3fccba
Create Date: 2026-06-02 11:44:28.002904

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f03d1a9873f"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the database."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if table_name not in inspector.get_table_names():
        return False
    cols = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in cols


def upgrade() -> None:
    # Only add columns if financial_reports table exists (may not exist in legacy DBs)
    if _table_exists("financial_reports"):
        if not _column_exists("financial_reports", "money_cap"):
            op.add_column(
                "financial_reports",
                sa.Column("money_cap", sa.Numeric(20, 4), nullable=True),
            )
        if not _column_exists("financial_reports", "accounts_receiv"):
            op.add_column(
                "financial_reports",
                sa.Column("accounts_receiv", sa.Numeric(20, 4), nullable=True),
            )


def downgrade() -> None:
    if _table_exists("financial_reports"):
        if _column_exists("financial_reports", "accounts_receiv"):
            op.drop_column("financial_reports", "accounts_receiv")
        if _column_exists("financial_reports", "money_cap"):
            op.drop_column("financial_reports", "money_cap")
