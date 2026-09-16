"""add news_risk_brief snapshot table (新闻风险解读第一期 Phase C)

Revision ID: 0028
Revises: 0027
Create Date: 2026-09-16 00:00:00.000000

新增 ``news_risk_brief`` 表（设计方案 §7.2）：保存一次「股票 + 30 天证据集合」的
结构化风险分析快照。复合主键 ``(ts_code, input_hash)``，无自增 id；结果列可空且
``null_protected``（失败不得覆盖既有成功快照）。

索引 ``idx_news_risk_brief_ts_code_window_created``：§11 子集例外按
(ts_code, window_start, window_end, created_at) 查最近成功快照。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the news_risk_brief snapshot table."""
    op.create_table(
        "news_risk_brief",
        sa.Column("ts_code", sa.String(), primary_key=True),
        sa.Column("input_hash", sa.String(length=64), primary_key=True),
        sa.Column("window_start", sa.DateTime(timezone=False), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=False), nullable=False),
        sa.Column("analysis_status", sa.String(length=24), nullable=False),
        sa.Column("risk_level", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("summary", sa.String(), nullable=True),
        sa.Column("events", JSONB(), nullable=True),
        sa.Column("evidence_news_ids", JSONB(), nullable=True),
        sa.Column("coverage", JSONB(), nullable=True),
        sa.Column("model_id", sa.String(), nullable=True),
        sa.Column("analysis_profile", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "idx_news_risk_brief_ts_code_window_created",
        "news_risk_brief",
        ["ts_code", "window_start", "window_end", "created_at"],
    )


def downgrade() -> None:
    """Drop the news_risk_brief snapshot table (data loss, non-recoverable)."""
    op.drop_index("idx_news_risk_brief_ts_code_window_created", table_name="news_risk_brief")
    op.drop_table("news_risk_brief")
