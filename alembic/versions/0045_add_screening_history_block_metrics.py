"""add screening_history block trade metrics

Revision ID: 0045
Revises: 0044
Create Date: 2026-11-04 00:00:00.000000

D06-R02：``screening_history`` 新增大宗交易聚合快照列
``block_amount_wan``（万元）与 ``block_vol_wanshare``（万股），使历史回看不再丢失
大宗额/量。两列均可为 NULL：非大宗策略与未携带该两列的结果写 NULL，不从
``amount``/``vol``（日线快照，千元/手）反推，避免语义混淆（R21）。

按已确认事实「程序未正式发布、无历史数据需迁移」：本迁移仅 add_column，
不做任何数据 UPDATE、不回填、不建备份表、不引入语义版本列。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0045"
down_revision: str | Sequence[str] | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable block trade metric columns to screening_history."""
    op.add_column(
        "screening_history",
        sa.Column("block_amount_wan", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "screening_history",
        sa.Column("block_vol_wanshare", sa.Numeric(20, 4), nullable=True),
    )


def downgrade() -> None:
    """Drop block trade metric columns (data loss, non-recoverable)."""
    op.drop_column("screening_history", "block_vol_wanshare")
    op.drop_column("screening_history", "block_amount_wan")
