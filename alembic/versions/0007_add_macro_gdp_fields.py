"""add macro_economy GDP fields (gdp/gdp_yoy/pi/pi_yoy/si/si_yoy/ti/ti_yoy)

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-04 00:00:00.000000

Phase 2D §3.2.6：cn_gdp 全链路补全。macro_economy 表扩列 8 个 GDP 字段，
对应 Tushare cn_gdp API 返回字段：
- gdp: 国内生产总值（亿元）
- gdp_yoy: GDP 同比增速（%）
- pi: 第一产业增加值（亿元）
- pi_yoy: 第一产业同比增速（%）
- si: 第二产业增加值（亿元）
- si_yoy: 第二产业同比增速（%）
- ti: 第三产业增加值（亿元）
- ti_yoy: 第三产业同比增速（%）

v1.7.0 S6：新建表/列无需 guard，但 downgrade 路径完整。
R17：pi/si/ti 非 SQL 保留字，无需 name= 映射。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add 8 GDP fields to macro_economy."""
    op.add_column("macro_economy", sa.Column("gdp", sa.Numeric(20, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("gdp_yoy", sa.Numeric(12, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("pi", sa.Numeric(20, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("pi_yoy", sa.Numeric(12, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("si", sa.Numeric(20, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("si_yoy", sa.Numeric(12, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("ti", sa.Numeric(20, 4), nullable=True))
    op.add_column("macro_economy", sa.Column("ti_yoy", sa.Numeric(12, 4), nullable=True))


def downgrade() -> None:
    """Remove 8 GDP fields from macro_economy."""
    op.drop_column("macro_economy", "ti_yoy")
    op.drop_column("macro_economy", "ti")
    op.drop_column("macro_economy", "si_yoy")
    op.drop_column("macro_economy", "si")
    op.drop_column("macro_economy", "pi_yoy")
    op.drop_column("macro_economy", "pi")
    op.drop_column("macro_economy", "gdp_yoy")
    op.drop_column("macro_economy", "gdp")
