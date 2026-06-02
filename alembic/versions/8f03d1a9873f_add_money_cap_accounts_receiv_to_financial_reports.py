"""add money_cap accounts_receiv to financial_reports

Revision ID: 8f03d1a9873f
Revises: f6586a3fccba
Create Date: 2026-06-02 11:44:28.002904

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8f03d1a9873f"
down_revision: str | Sequence[str] | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "financial_reports",
        sa.Column("money_cap", sa.Numeric(20, 4), nullable=True),
    )
    op.add_column(
        "financial_reports",
        sa.Column("accounts_receiv", sa.Numeric(20, 4), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("financial_reports", "accounts_receiv")
    op.drop_column("financial_reports", "money_cap")
