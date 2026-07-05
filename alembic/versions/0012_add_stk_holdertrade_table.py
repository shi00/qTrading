"""add stk_holdertrade table (Shareholder Trade / 产业资本增减持)

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-04 00:00:00.000000

Phase 3E §3.2：股东增减持。新增 stk_holdertrade 表用于存储
Tushare stk_holdertrade 接口返回的股东增减持数据，供 AI 分析
产业资本信号（高管/公司层面的增减持行为）。

字段对应 Tushare stk_holdertrade API 返回字段：
- ts_code: 股票代码
- ann_date: 公告日期
- holder_name: 股东名称
- holder_type: 股东类型（C公司 / G高管）
- in_de: 变动方向（IN增持 / DE减持）
- change_vol: 变动数量（股）
- change_ratio: 变动比例（%）
- after_share: 变动后持股
- after_ratio: 变动后占比（%）

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stk_holdertrade table."""
    op.create_table(
        "stk_holdertrade",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False),
        sa.Column("holder_name", sa.String(100), nullable=False),
        sa.Column("holder_type", sa.String(2)),
        sa.Column("in_de", sa.String(2), nullable=False),
        sa.Column("change_vol", sa.Numeric(20, 4)),
        sa.Column("change_ratio", sa.Numeric(8, 4)),
        sa.Column("after_share", sa.Numeric(20, 4)),
        sa.Column("after_ratio", sa.Numeric(8, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "ann_date", "holder_name", "in_de"),
    )
    op.create_index("ix_stk_holdertrade_ann_date", "stk_holdertrade", ["ann_date"])


def downgrade() -> None:
    """Drop stk_holdertrade table."""
    op.drop_index("ix_stk_holdertrade_ann_date", table_name="stk_holdertrade")
    op.drop_table("stk_holdertrade")
