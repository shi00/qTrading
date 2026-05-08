"""ai_score_integer_to_float_and_indexes

Revision ID: a3b7c9d2e1f0
Revises: f6586a3fccba
Create Date: 2026-05-07 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a3b7c9d2e1f0"
down_revision: str | None = "f6586a3fccba"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "screening_history",
        "ai_score",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=True,
    )

    op.create_index(
        "idx_task_history_status_created",
        "task_history",
        ["status", "created_at"],
    )
    op.create_index(
        "idx_task_history_completed",
        "task_history",
        ["completed_at"],
    )

    op.create_index(
        "idx_screening_history_trade_date",
        "screening_history",
        ["trade_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_screening_history_trade_date", table_name="screening_history")
    op.drop_index("idx_task_history_completed", table_name="task_history")
    op.drop_index("idx_task_history_status_created", table_name="task_history")

    op.execute(
        "UPDATE screening_history SET ai_score = ROUND(ai_score) WHERE ai_score IS NOT NULL AND ai_score != ROUND(ai_score)"
    )
    op.alter_column(
        "screening_history",
        "ai_score",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=True,
    )
