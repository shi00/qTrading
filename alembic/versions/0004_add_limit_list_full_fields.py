"""add full fields to limit_list

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-27 00:00:00.000000

补全 limit_list_d 接口（Tushare doc_id=298）的全部 18 个字段。
此前 ORM 仅保存 10 个字段，遗漏 8 个 API 实际返回的字段，导致后续
策略层需要这些字段时无法直接读取，必须重新调 API。

新增字段（均允许 NULL，因历史数据无这些列）：
  - industry        所属行业
  - amount          成交额
  - limit_amount    板上成交金额（仅跌停有）
  - float_mv        流通市值
  - total_mv        总市值
  - turnover_ratio  换手率
  - up_stat         涨停统计（"N/T" 格式）
  - limit_times     连板数

注：amp/fc_ratio/fl_ratio/strth 属于旧 limit_list 接口，limit_list_d
不提供，已在 0001 中保持永久删除状态，本迁移不重新引入。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (列名, 列类型) 元组列表，按 ORM 顺序定义
_NEW_COLUMNS: list[tuple[str, sa.types.TypeEngine]] = [
    ("industry", sa.String()),
    ("amount", sa.Numeric(20, 4)),
    ("limit_amount", sa.Numeric(20, 4)),
    ("float_mv", sa.Numeric(20, 4)),
    ("total_mv", sa.Numeric(20, 4)),
    ("turnover_ratio", sa.Numeric(12, 4)),
    ("up_stat", sa.String()),
    ("limit_times", sa.Integer()),
]


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    """Add 8 missing limit_list_d fields to limit_list table."""
    for col_name, col_type in _NEW_COLUMNS:
        if not _column_exists("limit_list", col_name):
            op.add_column(
                "limit_list",
                sa.Column(col_name, col_type, nullable=True),
            )


def downgrade() -> None:
    """Remove the 8 fields added in upgrade."""
    for col_name, _ in reversed(_NEW_COLUMNS):
        if _column_exists("limit_list", col_name):
            op.drop_column("limit_list", col_name)
