"""add volatility, information_ratio, tracking_error to backtest_results

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c3d4e5f6g7"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("backtest_results", sa.Column("volatility", sa.Numeric(12, 6), nullable=True))
    op.add_column("backtest_results", sa.Column("information_ratio", sa.Numeric(12, 6), nullable=True))
    op.add_column("backtest_results", sa.Column("tracking_error", sa.Numeric(12, 6), nullable=True))


def downgrade() -> None:
    op.drop_column("backtest_results", "tracking_error")
    op.drop_column("backtest_results", "information_ratio")
    op.drop_column("backtest_results", "volatility")
