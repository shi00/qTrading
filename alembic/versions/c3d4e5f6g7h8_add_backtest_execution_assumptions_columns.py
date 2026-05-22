"""add backtest execution assumptions columns

Revision ID: c3d4e5f6g7h8
Revises: b2c3d4e5f6g7
Create Date: 2026-05-22
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f6g7h8"
down_revision = "b2c3d4e5f6g7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("backtest_results", sa.Column("execution_price", sa.String(20), nullable=True))
    op.add_column("backtest_results", sa.Column("allow_limit_up_buy", sa.Boolean(), nullable=True))
    op.add_column("backtest_results", sa.Column("allow_limit_down_sell", sa.Boolean(), nullable=True))
    op.add_column("backtest_results", sa.Column("slippage_model", sa.String(20), nullable=True))
    op.add_column("backtest_results", sa.Column("app_version", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_results", "app_version")
    op.drop_column("backtest_results", "slippage_model")
    op.drop_column("backtest_results", "allow_limit_down_sell")
    op.drop_column("backtest_results", "allow_limit_up_buy")
    op.drop_column("backtest_results", "execution_price")
