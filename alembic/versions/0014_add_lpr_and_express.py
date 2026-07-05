"""add lpr_1y/lpr_5y to shibor_daily + create express table (业绩快报)

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-04 00:00:00.000000

Phase 3G §4.3.4：LPR + 业绩快报。
1. shibor_daily 扩列 lpr_1y/lpr_5y：存储 Tushare shibor_lpr API 返回的
   1 年/5 年 LPR 数据，与 shibor 同表（按 date 主键合并）。
2. express 新建表：存储 Tushare express API 返回的业绩快报数据，
   早于正式财报 30-60 天公告，AI 可提前反应。

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add lpr_1y/lpr_5y to shibor_daily + create express table."""
    # 1. shibor_daily 扩列 LPR
    op.add_column("shibor_daily", sa.Column("lpr_1y", sa.Numeric(12, 4)))
    op.add_column("shibor_daily", sa.Column("lpr_5y", sa.Numeric(12, 4)))

    # 2. express 新建表（业绩快报）
    op.create_table(
        "express",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=False, index=True),
        sa.Column("type", sa.String()),
        sa.Column("revenue", sa.Numeric(20, 4)),
        sa.Column("n_income", sa.Numeric(20, 4)),
        sa.Column("total_profit", sa.Numeric(20, 4)),
        sa.Column("yoy_sales", sa.Numeric(12, 4)),
        sa.Column("yoy_profit", sa.Numeric(12, 4)),
        sa.Column("yoy_dedu_np", sa.Numeric(12, 4)),
        sa.Column("deduct_profit", sa.Numeric(20, 4)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "end_date", "ann_date"),
    )
    # 不为 ts_code 单独创建索引。复合主键 (ts_code, end_date, ann_date) 已支持
    # ts_code 单列前缀查询，独立索引冗余。ORM 中 ts_code 也未声明 index=True，
    # 创建索引会导致 alembic check 报告 ORM 与 DB 索引差异。
    # ann_date 索引用于 get_express_batch 的 ann_date <= $N 范围查询优化。


def downgrade() -> None:
    """Drop express table + remove lpr_1y/lpr_5y from shibor_daily."""
    op.drop_table("express")

    op.drop_column("shibor_daily", "lpr_5y")
    op.drop_column("shibor_daily", "lpr_1y")
