"""add news_risk_brief execution_metadata (UIR-F05-002)

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-27 00:00:00.000000

新闻风险解读快照新增执行元数据列：
- ``news_risk_brief.execution_metadata``：S4 统一场景链程序构造的 8 键执行元数据
  （invocation_id / route_revision / connection_id / connection_revision /
  actual_model / usage_status / ai_status / config_revision），nullable JSONB；
- 历史行保持 NULL（未记录 ≠ 无信息，R21），不回填；
- 不设 server_default；none_as_null 语义（None → SQL NULL）。

downgrade 对称删列（数据丢失，不可恢复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0043"
down_revision: str | Sequence[str] | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable execution_metadata to news_risk_brief."""
    op.add_column("news_risk_brief", sa.Column("execution_metadata", JSONB(none_as_null=True), nullable=True))


def downgrade() -> None:
    """Drop execution_metadata (data loss, non-recoverable)."""
    op.drop_column("news_risk_brief", "execution_metadata")
