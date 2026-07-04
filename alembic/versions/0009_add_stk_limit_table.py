"""add stk_limit table (Daily Limit Up/Down Price)

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-04 00:00:00.000000

Phase 2G §3.2：stk_limit 涨跌停价格（仅数据层，不注入 AI）。新增 stk_limit 表
用于存储 Tushare stk_limit 接口返回的每日涨跌停价格数据。

字段对应 Tushare stk_limit API 返回字段：
- ts_code: 股票代码
- trade_date: 交易日期
- pre_close: 昨收价
- up_limit: 涨停价
- down_limit: 跌停价
- limit_type: 涨跌停类型（U涨停 / D跌停）

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：``limit`` 是 SQL 保留字（LIMIT 子句关键字），数据库列名映射为 ``limit_type``。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stk_limit table."""
    op.create_table(
        "stk_limit",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("pre_close", sa.Numeric(12, 4)),
        sa.Column("up_limit", sa.Numeric(12, 4)),
        sa.Column("down_limit", sa.Numeric(12, 4)),
        sa.Column("limit_type", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "trade_date"),
    )
    op.create_index("ix_stk_limit_trade_date", "stk_limit", ["trade_date"])


def downgrade() -> None:
    """Drop stk_limit table."""
    op.drop_index("ix_stk_limit_trade_date", table_name="stk_limit")
    op.drop_table("stk_limit")
