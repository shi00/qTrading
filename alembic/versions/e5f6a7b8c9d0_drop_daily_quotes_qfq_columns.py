"""drop_daily_quotes_qfq_columns

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-04-28 13:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    for column_name in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
        if _column_exists("daily_quotes", column_name):
            op.drop_column("daily_quotes", column_name)


def downgrade() -> None:
    for column_name in ("qfq_open", "qfq_high", "qfq_low", "qfq_close"):
        if not _column_exists("daily_quotes", column_name):
            op.add_column("daily_quotes", sa.Column(column_name, sa.Float(), nullable=True))
