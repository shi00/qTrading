"""add_screening_history_review_fields

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-04-28 17:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not _column_exists("screening_history", "index_pct"):
        op.add_column("screening_history", sa.Column("index_pct", sa.Float(), nullable=True))
    if not _column_exists("screening_history", "alpha"):
        op.add_column("screening_history", sa.Column("alpha", sa.Float(), nullable=True))


def downgrade() -> None:
    if _column_exists("screening_history", "alpha"):
        op.drop_column("screening_history", "alpha")
    if _column_exists("screening_history", "index_pct"):
        op.drop_column("screening_history", "index_pct")
