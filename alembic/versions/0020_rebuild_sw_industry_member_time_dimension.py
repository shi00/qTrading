"""rebuild sw_industry_member with index_member_all real fields + time dimension

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-11 00:00:00.000000

DATA-04 L2：`index_member_all`（申万行业成分构成·分级，doc_id=335）的真实输出字段为
`l1_code/l1_name/l2_code/l2_name/l3_code/l3_name/ts_code/name/in_date/out_date/is_new`，
并不存在旧 schema 请求的 `index_code/index_name/sw_l1_code..sw_l3_name` 列（仅
`index_classify` 输出该口径）。旧表因此未正确落库且丢失行业进出时间维度，历史回测
引用"当前成分"引入前视偏差。

本迁移 drop+create 重建 sw_industry_member（旧数据口径错误，无法增量修复，重建后由
同步逻辑重新拉取），新 schema：
- 列：l1/l2/l3_code + l1/l2/l3_name + ts_code + name + in_date/out_date/is_new
- 主键：(ts_code, l3_code, in_date)，容纳同股票同行业多次进出与行业重分类
- as-of 语义：out_date IS NULL 表示当前有效

v1.x S6：drop+create 属数据迁移，downgrade 路径完整恢复旧 schema。
R17：所有列名均非 SQL 保留字（l1_code 以字母开头），无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop and recreate sw_industry_member with index_member_all real fields."""
    # 1. Drop legacy table (data has wrong column grain).
    # DAT-15 (0018) 已删除 ix_sw_industry_member_ts_code，drop 带 if_exists 保持幂等。
    op.drop_index("ix_sw_industry_member_sw_l2_code", table_name="sw_industry_member")
    op.drop_index("ix_sw_industry_member_ts_code", table_name="sw_industry_member", if_exists=True)
    op.drop_table("sw_industry_member")

    # 2. Recreate with corrected schema (match data/persistence/models.py).
    op.create_table(
        "sw_industry_member",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("l3_code", sa.String(), nullable=False),
        sa.Column("in_date", sa.Date(), nullable=False),
        sa.Column("l1_code", sa.String()),
        sa.Column("l1_name", sa.String()),
        sa.Column("l2_code", sa.String()),
        sa.Column("l2_name", sa.String()),
        sa.Column("l3_name", sa.String()),
        sa.Column("name", sa.String()),
        sa.Column("out_date", sa.Date()),
        sa.Column("is_new", sa.String(1)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "l3_code", "in_date", name=op.f("pk_sw_industry_member")),
    )
    # 仅创建模型声明的 l2_code 索引；ts_code 为主键左前缀列，单列索引冗余（DAT-15），
    # 不创建可保持 alembic check 通过。
    op.create_index("ix_sw_industry_member_l2_code", "sw_industry_member", ["l2_code"])


def downgrade() -> None:
    """Restore the legacy sw_industry_member schema."""
    # upgrade 不再创建 ts_code 索引，drop 带 if_exists 保持降级幂等。
    op.drop_index("ix_sw_industry_member_l2_code", table_name="sw_industry_member")
    op.drop_index("ix_sw_industry_member_ts_code", table_name="sw_industry_member", if_exists=True)
    op.drop_table("sw_industry_member")

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
        sa.PrimaryKeyConstraint("ts_code", "index_code", name=op.f("pk_sw_industry_member")),
    )
    # 忠实还原 pre-0020 状态：DAT-15 (0018) 已删除 ts_code 索引，仅恢复 sw_l2_code 索引。
    op.create_index("ix_sw_industry_member_sw_l2_code", "sw_industry_member", ["sw_l2_code"])
