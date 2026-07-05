"""add share_float table (Share Float / Unlock)

Revision ID: 0011
Revises: 0010
Create Date: 2026-07-04 00:00:00.000000

Phase 3D §3.2：限售解禁。新增 share_float 表用于存储
Tushare share_float 接口返回的限售解禁数据，供 AI 分析
解禁压力与减持风险。

字段对应 Tushare share_float API 返回字段（float_type 经
_COLUMN_RENAMES 重命名为 share_type）：
- ts_code: 股票代码
- ann_date: 公告日期
- float_date: 解禁日期
- float_share: 解禁数量（万股）
- float_ratio: 解禁比例（%）
- holder_name: 股东名称（API 当前不返回，预留列）
- share_type: 解禁类型（如"定向增发机构配售股份"）

v1.7.0 S6：新建表无需 guard，但 downgrade 路径完整。
R17：所有列名均非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create share_float table."""
    op.create_table(
        "share_float",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("ann_date", sa.Date()),
        sa.Column("float_date", sa.Date(), nullable=False),
        sa.Column("float_share", sa.Numeric(20, 4)),
        sa.Column("float_ratio", sa.Numeric(8, 4)),
        sa.Column("holder_name", sa.String(100)),
        sa.Column("share_type", sa.String(50)),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("ts_code", "float_date"),
    )
    op.create_index("ix_share_float_ann_date", "share_float", ["ann_date"])
    op.create_index("ix_share_float_float_date", "share_float", ["float_date"])


def downgrade() -> None:
    """Drop share_float table."""
    op.drop_index("ix_share_float_float_date", table_name="share_float")
    op.drop_index("ix_share_float_ann_date", table_name="share_float")
    op.drop_table("share_float")
