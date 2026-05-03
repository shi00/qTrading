"""Add composite index on trade_cal for slow query optimization

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-05-03 16:30:00.000000

"""

from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "f6586a3fccba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "idx_trade_cal_date_open_exchange",
        "trade_cal",
        ["cal_date", "is_open", "exchange"],
    )


def downgrade() -> None:
    op.drop_index("idx_trade_cal_date_open_exchange", table_name="trade_cal")
