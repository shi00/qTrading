"""add quality_json/config_json columns to backtest_results (回测可信度元数据与完整配置落库)

Revision ID: 0029
Revises: 0028
Create Date: 2026-09-20 00:00:00.000000

BT-03: 修复回测结果落库单向蒸发 —— 数据质量告警、失败信号日、完整回测配置
在持久化时被丢弃，历史记录不可解释、不可复现。新增两个 JSONB 列一次性容纳，
避免每加一种告警就迁移：
- ``quality_json``：data_warnings / failed_signal_dates / skipped_order_count /
  delist_liquidation_count / delist_loss_amount / has_real_score。
- ``config_json``：完整 BacktestConfig（dataclasses.asdict），作为完整配置来源。

存量记录两列为 NULL（list_results 层对其 has_warnings 视为无警告）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add quality_json and config_json columns to backtest_results."""
    op.add_column("backtest_results", sa.Column("quality_json", JSONB(), nullable=True))
    op.add_column("backtest_results", sa.Column("config_json", JSONB(), nullable=True))


def downgrade() -> None:
    """Drop backtest_results quality/config JSONB columns (data loss, non-recoverable)."""
    op.drop_column("backtest_results", "config_json")
    op.drop_column("backtest_results", "quality_json")
