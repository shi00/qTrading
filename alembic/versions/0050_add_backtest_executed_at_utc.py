"""add backtest_results.executed_at_utc absolute execution instant

Revision ID: 0050
Revises: 0049
Create Date: 2026-11-23 00:00:00.000000

P1-04：``backtest_results.executed_at`` 原为 ``timestamp without time zone``，
由会话时区的 ``now()`` 写入墙钟时间，绝对时刻不可证。严格读（
``services/agent_service/business_sources.read_backtest_snapshot``）拒绝 naive
时间并把来源标为不可用，导致「解读回测」无法建稿。

本迁移新增 ``executed_at_utc``（timestamptz，nullable）承载可验证的绝对执行时点：

- 只加空列，**不回填**存量行：存量值属于时区未声明的会话墙钟，补成 UTC
  会让「可得时点」系统性偏移（R21/R24），故保持 NULL = 未知；
- 先 ``add_column`` 再单独 ``SET DEFAULT now()``：避免 ``ADD COLUMN ... DEFAULT
  now()`` 在 ALTER 时刻用迁移时间回填存量行，把「未知」伪装成「可得」；
  设置默认后仅影响后续 INSERT，新行由 DB 写入绝对时刻（不受客户端会话时区影响）。

downgrade 对称删列（数据丢失，不可恢复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0050"
down_revision: str | Sequence[str] | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable executed_at_utc; legacy rows stay NULL (unknown, no backfill)."""
    op.add_column("backtest_results", sa.Column("executed_at_utc", sa.DateTime(timezone=True), nullable=True))
    op.alter_column("backtest_results", "executed_at_utc", server_default=sa.text("now()"))


def downgrade() -> None:
    """Drop executed_at_utc (data loss, non-recoverable)."""
    op.drop_column("backtest_results", "executed_at_utc")
