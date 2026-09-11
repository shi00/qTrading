"""add stock_name_history table

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-11 00:00:00.000000

DATA-04 L3：引入 ``stock_name_history`` 表记录股票历次名称变更（Tushare
``namechange`` 接口），使 ST/*ST 状态可按时点（as-of）还原，消除回测引用当前
名称（``stock_basic.name``）引入的前视偏差。

- 主键 (ts_code, start_date)：每股每个名称生效起始日唯一。
- as-of 语义：``start_date <= as_of AND (end_date IS NULL OR end_date > as_of)``。
- 所有列名均非 SQL 保留字（start_date/end_date 以字母开头），无需 name= 映射（R17）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create stock_name_history table."""
    op.create_table(
        "stock_name_history",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("name", sa.String()),
        sa.Column("end_date", sa.Date()),
        sa.Column("ann_date", sa.Date()),
        sa.Column("change_reason", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "start_date", name=op.f("pk_stock_name_history")),
    )
    # ts_code 为主键左前缀列，单列索引冗余（与模型声明一致），不创建以保持 alembic check 通过。


def downgrade() -> None:
    """Drop stock_name_history table."""
    op.drop_table("stock_name_history")
