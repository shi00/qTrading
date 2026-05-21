"""add backtest_results table

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-05-21

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return sa.dialects.postgresql.JSONB()
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.String(16), nullable=False),
        sa.Column("strategy_name", sa.String(), nullable=False),
        sa.Column("params_snapshot", _json_type(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("initial_capital", sa.Numeric(20, 4), nullable=True),
        sa.Column("total_return", sa.Numeric(12, 6), nullable=True),
        sa.Column("annualized_return", sa.Numeric(12, 6), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(12, 6), nullable=True),
        sa.Column("calmar_ratio", sa.Numeric(12, 6), nullable=True),
        sa.Column("ic_mean", sa.Numeric(12, 6), nullable=True),
        sa.Column("ic_ir", sa.Numeric(12, 6), nullable=True),
        sa.Column("win_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column("profit_factor", sa.Numeric(12, 6), nullable=True),
        sa.Column("total_trades", sa.Integer(), nullable=True),
        sa.Column("nav_curve_json", _json_type(), nullable=True),
        sa.Column("trades_json", _json_type(), nullable=True),
        sa.Column("period_stats_json", _json_type(), nullable=True),
        sa.Column(
            "executed_at",
            sa.DateTime(timezone=False),
            server_default=sa.func.now(),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_backtest_results")),
        sa.UniqueConstraint("run_id", name=op.f("uq_backtest_results_run_id")),
    )
    op.create_index(
        op.f("ix_backtest_results_strategy"),
        "backtest_results",
        ["strategy_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_backtest_results_date"),
        "backtest_results",
        ["executed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_backtest_results_date"), table_name="backtest_results")
    op.drop_index(op.f("ix_backtest_results_strategy"), table_name="backtest_results")
    op.drop_table("backtest_results")
