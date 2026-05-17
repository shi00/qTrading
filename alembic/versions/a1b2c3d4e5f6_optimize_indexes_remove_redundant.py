"""optimize_indexes_remove_redundant

Revision ID: a1b2c3d4e5f6
Revises: f6586a3fccba
Create Date: 2026-05-17 21:49:29.968570

"""

from collections.abc import Sequence

from alembic import op


revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_market_news_publish_time", table_name="market_news")

    op.drop_index("idx_sh_params_gin", table_name="screening_history")
    op.execute("CREATE INDEX idx_sh_params_gin ON screening_history USING gin (params_snapshot jsonb_path_ops)")


def downgrade() -> None:
    op.drop_index("idx_sh_params_gin", table_name="screening_history")
    op.execute("CREATE INDEX idx_sh_params_gin ON screening_history USING gin (params_snapshot)")

    op.create_index("ix_market_news_publish_time", "market_news", ["publish_time"])
