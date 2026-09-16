"""add market_news risk/insight fields (新闻风险解读 Phase A)

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-16 00:00:00.000000

新闻风险解读第一期数据层 Phase A：为 ``market_news`` 新增一组可空的新闻风险/快讯字段，
并建立索引以承接首页来源过滤（source_kind = 'telegraph' OR IS NULL）查询与 ts_code 检索。

新增列（均可空，向后兼容，存量行自动降级为 NULL）：
- ts_code：关联个股代码。
- title：标题。
- url：原文链接。
- source_kind：来源类型（announcement / news / telegraph）。
- category_l1 / category_l2：一级/二级分类。
- sentiment：情感标注。

新增索引：
- ``idx_market_news_ts_code``：ts_code 检索。
- ``idx_market_news_source_kind_pub_time``：（source_kind, publish_time），承接首页来源过滤。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add risk/insight columns to market_news and supporting indexes."""
    op.add_column("market_news", sa.Column("ts_code", sa.String(), nullable=True))
    op.add_column("market_news", sa.Column("title", sa.String(), nullable=True))
    op.add_column("market_news", sa.Column("url", sa.String(), nullable=True))
    op.add_column("market_news", sa.Column("source_kind", sa.String(length=32), nullable=True))
    op.add_column("market_news", sa.Column("category_l1", sa.String(length=32), nullable=True))
    op.add_column("market_news", sa.Column("category_l2", sa.String(length=64), nullable=True))
    op.add_column("market_news", sa.Column("sentiment", sa.String(length=16), nullable=True))

    op.create_index("idx_market_news_ts_code", "market_news", ["ts_code"])
    op.create_index("idx_market_news_source_kind_pub_time", "market_news", ["source_kind", "publish_time"])


def downgrade() -> None:
    """Drop the risk/insight columns and indexes (data loss, non-recoverable)."""
    op.drop_index("idx_market_news_source_kind_pub_time", table_name="market_news")
    op.drop_index("idx_market_news_ts_code", table_name="market_news")

    op.drop_column("market_news", "sentiment")
    op.drop_column("market_news", "category_l2")
    op.drop_column("market_news", "category_l1")
    op.drop_column("market_news", "source_kind")
    op.drop_column("market_news", "url")
    op.drop_column("market_news", "title")
    op.drop_column("market_news", "ts_code")
