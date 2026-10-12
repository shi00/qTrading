"""add market_news sentiment score columns (新闻达标模型 M5 Phase 5)

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-12 00:00:00.000000

新闻达标模型落地（Plans.md N5-1）：为 ``market_news`` 新增三个可空的新闻情绪打分列，
承接 N5-2 日度情绪指数聚合。语义边界：

- sentiment_score：情绪分，值域 −1~+1（Negative / Neutral / Positive 的连续打分）。
- sentiment_conf：该条打分的置信度（0~1）。
- sentiment_model：产生该打分的模型标识（本地 GGUF 文件名或 LLM 回退标识），
  N5-2 聚合以 ``(id, sentiment_model)`` 判断是否重算。

三列均可空且不设 server_default：未打分历史行保持 ``NULL``（区别于业务上合法的
具体值，R21），不做批量回填；打分由 N3 系列新闻情绪服务在处理路径按需产出。
不新增索引（重算判断以主键 ``id`` 为锚），``data_dictionary.py`` 的
``market_news`` 条目（约束/索引登记）无需变更。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0039"
down_revision: str | Sequence[str] | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable sentiment score columns to market_news (no backfill)."""
    # Double（而非无精度 Float）：PG 反射 float8 统一报告 DOUBLE PRECISION，
    # ORM↔迁移一致性门禁（test_orm_migration_consistency.py）按 PG dialect 编译字符串
    # 严格比较，sa.Float() 编译为 FLOAT 会与之不匹配；两者底层同为 float8，语义不变。
    op.add_column("market_news", sa.Column("sentiment_score", sa.Double(), nullable=True))
    op.add_column("market_news", sa.Column("sentiment_conf", sa.Double(), nullable=True))
    op.add_column("market_news", sa.Column("sentiment_model", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Drop the sentiment score columns (data loss, non-recoverable)."""
    op.drop_column("market_news", "sentiment_model")
    op.drop_column("market_news", "sentiment_conf")
    op.drop_column("market_news", "sentiment_score")
