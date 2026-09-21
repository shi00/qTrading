"""add added_trade_date/added_price columns to watchlist (关注列表观察起点)

Revision ID: 0031
Revises: 0030
Create Date: 2026-09-21 00:00:00.000000

RV-05: 关注列表无加入时价格——「关注以来收益」无法回答（added_at 是自然时间戳，
无对应交易日定义）。新增两列在加入关注时一次性写入（IS NULL 守卫，之后只读）：
- ``added_trade_date``：加入时对应交易日（daily_quotes 该股最新交易日）。
- ``added_price``：该交易日复权收盘价（close/adj_factor，与 _qfq_return_pct 同口径）。

存量记录两列为 NULL（表示观察起点未知，消费方按「无法计算」处理，R21 不伪造）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add observation origin columns to watchlist."""
    op.add_column("watchlist", sa.Column("added_trade_date", sa.Date(), nullable=True))
    op.add_column("watchlist", sa.Column("added_price", sa.Numeric(12, 6), nullable=True))


def downgrade() -> None:
    """Drop watchlist observation origin columns (data loss, non-recoverable)."""
    op.drop_column("watchlist", "added_price")
    op.drop_column("watchlist", "added_trade_date")
