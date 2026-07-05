"""add sw_industry_classify + sw_industry_member tables (申万行业分类)

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-04 00:00:00.000000

Phase 3F-1 §4.3.2：申万行业分类建表。新增 2 张表用于存储
Tushare index_classify / index_member_all 接口返回的申万行业
分类与成分股映射数据，供 AI 行业景气度分析（Phase 3F-2 切换
stock_basic.industry 字段至申万二级）。

字段对应 Tushare index_classify API 返回字段：
- index_code: 指数代码（如 801010.SI）
- index_name: 指数名称（如 农林牧渔）
- level: 行业级别（L1/L2/L3）
- industry_code: 行业代码
- industry_name: 行业名称
- parent_code: 父级行业代码
- is_sw: 是否申万（1 是 / 0 否）

字段对应 Tushare index_member_all API 返回字段：
- ts_code: 股票代码
- index_code: 指数代码
- index_name: 指数名称
- sw_l1_code/sw_l1_name: 申万一级行业代码/名称
- sw_l2_code/sw_l2_name: 申万二级行业代码/名称
- sw_l3_code/sw_l3_name: 申万三级行业代码/名称

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
申万行业是全局快照，不加入 TABLE_TO_API_MAP（不参与交易日快照权限裁剪）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create sw_industry_classify + sw_industry_member tables."""
    op.create_table(
        "sw_industry_classify",
        sa.Column("index_code", sa.String(), nullable=False),
        sa.Column("index_name", sa.String()),
        sa.Column("level", sa.String(2), nullable=False),
        sa.Column("industry_code", sa.String()),
        sa.Column("industry_name", sa.String()),
        sa.Column("parent_code", sa.String()),
        sa.Column("is_sw", sa.String(1)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("index_code", "level"),
    )
    op.create_index("ix_sw_industry_classify_level", "sw_industry_classify", ["level"])
    op.create_index("ix_sw_industry_classify_industry_code", "sw_industry_classify", ["industry_code"])

    op.create_table(
        "sw_industry_member",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("index_code", sa.String(), nullable=False),
        sa.Column("index_name", sa.String()),
        sa.Column("sw_l1_code", sa.String()),
        sa.Column("sw_l1_name", sa.String()),
        sa.Column("sw_l2_code", sa.String()),
        sa.Column("sw_l2_name", sa.String()),
        sa.Column("sw_l3_code", sa.String()),
        sa.Column("sw_l3_name", sa.String()),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "index_code"),
    )
    op.create_index("ix_sw_industry_member_ts_code", "sw_industry_member", ["ts_code"])
    op.create_index("ix_sw_industry_member_sw_l2_code", "sw_industry_member", ["sw_l2_code"])


def downgrade() -> None:
    """Drop sw_industry_member + sw_industry_classify tables."""
    op.drop_index("ix_sw_industry_member_sw_l2_code", table_name="sw_industry_member")
    op.drop_index("ix_sw_industry_member_ts_code", table_name="sw_industry_member")
    op.drop_table("sw_industry_member")

    op.drop_index("ix_sw_industry_classify_industry_code", table_name="sw_industry_classify")
    op.drop_index("ix_sw_industry_classify_level", table_name="sw_industry_classify")
    op.drop_table("sw_industry_classify")
