"""add news_risk_brief generated_at (UIR-F05-003)

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-27 00:00:00.000000

新闻风险解读快照新增程序生成时点列：
- ``news_risk_brief.generated_at``：UTC-naive 的生成时刻（服务侧写入），nullable；
- 历史行保持 NULL（未记录 ≠ 当前时刻，R21），不回填 ``created_at``/``updated_at``；
- 不设 server_default（避免把迁移/写入时刻伪装成分析生成时刻）。

downgrade 对称删列（数据丢失，不可恢复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0042"
down_revision: str | Sequence[str] | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable generated_at to news_risk_brief."""
    op.add_column("news_risk_brief", sa.Column("generated_at", sa.DateTime(timezone=False), nullable=True))


def downgrade() -> None:
    """Drop generated_at (data loss, non-recoverable)."""
    op.drop_column("news_risk_brief", "generated_at")
