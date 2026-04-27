"""add_missing_indexes_v2

Revision ID: b7c8d9e0f1a2
Revises: f6586a3fccba
Create Date: 2026-04-26 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_exists(table_name: str, index_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    if not _index_exists("screening_history", "idx_sh_date_code"):
        op.create_index(
            "idx_sh_date_code",
            "screening_history",
            ["trade_date", "ts_code"],
        )

    if not _index_exists("financial_reports", "ix_financial_reports_ann_date"):
        op.create_index(
            "ix_financial_reports_ann_date",
            "financial_reports",
            ["ann_date"],
        )


def downgrade() -> None:
    if _index_exists("screening_history", "idx_sh_date_code"):
        op.drop_index("idx_sh_date_code", table_name="screening_history")

    if _index_exists("financial_reports", "ix_financial_reports_ann_date"):
        op.drop_index("ix_financial_reports_ann_date", table_name="financial_reports")
