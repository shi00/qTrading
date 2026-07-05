"""add top_inst table (LHB Institutional Seat Transaction Detail)

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-04 00:00:00.000000

Phase 2E §3.2.7：top_inst 已封装 API 激活。新增 top_inst 表用于存储
Tushare top_inst 接口返回的龙虎榜机构席位交易明细数据。

字段对应 Tushare top_inst API 返回字段：
- ts_code: 股票代码
- trade_date: 交易日期
- name: 股票名称
- close: 收盘价
- pct_change: 涨跌幅
- amount: 总成交额
- net_amount: 净成交额
- buy_amount: 买入成交额
- buy_value: 买入金额
- sell_amount: 卖出成交额
- sell_value: 卖出金额

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create top_inst table."""
    op.create_table(
        "top_inst",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("close", sa.Numeric(12, 4)),
        sa.Column("pct_change", sa.Numeric(8, 4)),
        sa.Column("amount", sa.Numeric(20, 4)),
        sa.Column("net_amount", sa.Numeric(20, 4)),
        sa.Column("buy_amount", sa.Numeric(20, 4)),
        sa.Column("buy_value", sa.Numeric(20, 4)),
        sa.Column("sell_amount", sa.Numeric(20, 4)),
        sa.Column("sell_value", sa.Numeric(20, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("ix_top_inst_trade_date", "top_inst", ["trade_date"])


def downgrade() -> None:
    """Drop top_inst table."""
    op.drop_index("ix_top_inst_trade_date", table_name="top_inst")
    op.drop_table("top_inst")
