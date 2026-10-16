"""add s4 metadata channels (S4 统一场景链元数据通道)

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-16 00:00:00.000000

S4（s4_plan §3.2/§3.3/§5）三入口新链元数据落库通道：
- ``market_news.analysis_metadata``：新闻分类 analysis_metadata（JSONB 通道，
  含输入摘要 / schema 版本 / invocation_id / 最近尝试状态 / 来源信息等，
  不存 prompt / 完整响应）；
- ``screening_history.execution_metadata``：AI 深度分析 execution_metadata
  （8 键程序字段：invocation_id / route_revision / connection_id /
  connection_revision / actual_model / usage_status / ai_status / config_revision）。

均 nullable JSONB、none_as_null 语义（JSONB 默认 None → 'null'::jsonb，
ORM 侧声明 none_as_null=True 保证 null_protected coalesce 保护生效）；
历史行保持 NULL（未记录 ≠ 无信息，R21）。downgrade 对称删列（数据丢失，不可恢复）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0041"
down_revision: str | Sequence[str] | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add analysis_metadata (market_news) and execution_metadata (screening_history)."""
    op.add_column("market_news", sa.Column("analysis_metadata", JSONB(none_as_null=True), nullable=True))
    op.add_column("screening_history", sa.Column("execution_metadata", JSONB(none_as_null=True), nullable=True))


def downgrade() -> None:
    """Drop the metadata channels (data loss, non-recoverable)."""
    op.drop_column("screening_history", "execution_metadata")
    op.drop_column("market_news", "analysis_metadata")
