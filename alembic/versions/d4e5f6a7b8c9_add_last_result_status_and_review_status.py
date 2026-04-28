"""add_last_result_status_and_review_status

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def upgrade() -> None:
    if not _column_exists("sync_status", "last_result_status"):
        op.add_column(
            "sync_status",
            sa.Column("last_result_status", sa.String(), nullable=True),
        )

    if not _column_exists("screening_history", "review_status"):
        op.add_column(
            "screening_history",
            sa.Column("review_status", sa.String(), nullable=True, server_default="PENDING"),
        )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            "UPDATE sync_status SET last_result_status = CASE "
            "WHEN status = 'success' AND record_count > 0 THEN 'HAS_DATA' "
            "WHEN status = 'success' AND (record_count = 0 OR record_count IS NULL) THEN 'EMPTY' "
            "ELSE 'FETCH_FAILED' END "
            "WHERE last_result_status IS NULL"
        )
    )

    conn.execute(
        sa.text(
            "UPDATE screening_history SET review_status = CASE "
            "WHEN prediction_result IS NOT NULL THEN 'T1_DONE' "
            "ELSE 'PENDING' END "
            "WHERE review_status IS NULL"
        )
    )


def downgrade() -> None:
    if _column_exists("screening_history", "review_status"):
        op.drop_column("screening_history", "review_status")

    if _column_exists("sync_status", "last_result_status"):
        op.drop_column("sync_status", "last_result_status")
