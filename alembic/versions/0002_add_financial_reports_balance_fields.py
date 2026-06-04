"""add financial_reports balance sheet fields

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-03 00:00:00.000000

Adds two new nullable columns to financial_reports to support issue #41
prompt-data consistency fix:
- money_cap: 货币资金 (Tushare balancesheet.money_cap)
- accounts_receiv: 应收账款 (Tushare balancesheet.accounts_receiv)

Both fields are exposed by the already-used Tushare balancesheet endpoint
(no extra points / calls required). Columns are nullable so historical
records remain valid until the next financial repair run.

This migration is the upgrade-path companion to the schema changes for
issue #41 — fresh databases get the columns from 0001_initial_schema,
existing production databases get them from this 0002 migration. The
two paths are mutually exclusive: alembic only applies each revision
once per database.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    """Add money_cap and accounts_receiv columns to financial_reports.

    Idempotent against fresh databases: if 0001 already created these
    columns (older dev environments), the existence check skips them.
    """
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
    """Remove money_cap and accounts_receiv columns from financial_reports."""
    if _column_exists("financial_reports", "accounts_receiv"):
        op.drop_column("financial_reports", "accounts_receiv")
    if _column_exists("financial_reports", "money_cap"):
        op.drop_column("financial_reports", "money_cap")
