"""replace single-column uq_market_news_hash with composite uq_market_news_hash_time

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-05-23

"""

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "f6586a3fccba"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_market_news_hash", "market_news", type_="unique")
    op.create_unique_constraint("uq_market_news_hash_time", "market_news", ["content_hash", "publish_time"])


def downgrade() -> None:
    op.drop_constraint("uq_market_news_hash_time", "market_news", type_="unique")
    op.create_unique_constraint("uq_market_news_hash", "market_news", ["content_hash"])
