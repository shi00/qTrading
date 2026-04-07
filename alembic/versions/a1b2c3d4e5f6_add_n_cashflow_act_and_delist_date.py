"""add n_cashflow_act and delist_date fields

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-04-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy import inspect as sa_inspect

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in a table."""
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _index_exists(table_name: str, index_name: str) -> bool:
    """Check if an index exists on a table."""
    conn = op.get_bind()
    inspector = sa_inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    """Upgrade schema: add n_cashflow_act and delist_date fields."""

    if not _column_exists("financial_reports", "n_cashflow_act"):
        op.add_column(
            "financial_reports",
            sa.Column("n_cashflow_act", sa.Float(), nullable=True),
        )

    if not _column_exists("stock_basic", "delist_date"):
        op.add_column(
            "stock_basic",
            sa.Column("delist_date", sa.Date(), nullable=True),
        )

    if not _index_exists("stock_basic", "idx_stock_basic_delist_date"):
        op.create_index(
            "idx_stock_basic_delist_date",
            "stock_basic",
            ["delist_date"],
            unique=False,
        )

    if not _index_exists("stock_basic", "idx_stock_basic_dates"):
        op.create_index(
            "idx_stock_basic_dates",
            "stock_basic",
            ["list_date", "delist_date"],
            unique=False,
        )

    if not _index_exists("stock_basic", "idx_stock_basic_status"):
        op.create_index(
            "idx_stock_basic_status",
            "stock_basic",
            ["list_status", "list_date"],
            unique=False,
        )


def downgrade() -> None:
    """Downgrade schema: remove n_cashflow_act and delist_date fields."""

    if _index_exists("stock_basic", "idx_stock_basic_status"):
        op.drop_index("idx_stock_basic_status", table_name="stock_basic")
    if _index_exists("stock_basic", "idx_stock_basic_dates"):
        op.drop_index("idx_stock_basic_dates", table_name="stock_basic")
    if _index_exists("stock_basic", "idx_stock_basic_delist_date"):
        op.drop_index("idx_stock_basic_delist_date", table_name="stock_basic")

    if _column_exists("stock_basic", "delist_date"):
        op.drop_column("stock_basic", "delist_date")
    if _column_exists("financial_reports", "n_cashflow_act"):
        op.drop_column("financial_reports", "n_cashflow_act")
