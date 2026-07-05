"""add pledge_detail table (Share Pledge Detail)

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-04 00:00:00.000000

Phase 3B §3.2：股权质押明细。新增 pledge_detail 表用于存储
Tushare pledge_detail 接口返回的股权质押明细数据，与 pledge_stat
（统计）互补，提供更细粒度的质押信息供 AI 分析。

字段对应 Tushare pledge_detail API 返回字段：
- ts_code: 股票代码
- end_date: 截止日期
- pledge_amount: 质押股数
- unlimited_pledge_amount: 无限售条件质押股数
- limited_pledge_amount: 有限售条件质押股数
- total_pledge_amount: 质押总数
- pledge_ratio: 质押比例

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create pledge_detail table."""
    op.create_table(
        "pledge_detail",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("pledge_amount", sa.Numeric(20, 4)),
        sa.Column("unlimited_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("limited_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("total_pledge_amount", sa.Numeric(20, 4)),
        sa.Column("pledge_ratio", sa.Numeric(12, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "end_date"),
    )
    op.create_index("ix_pledge_detail_end_date", "pledge_detail", ["end_date"])


def downgrade() -> None:
    """Drop pledge_detail table."""
    op.drop_index("ix_pledge_detail_end_date", table_name="pledge_detail")
    op.drop_table("pledge_detail")
