"""fix_thinking_fk_cascade

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-14 10:00:00.000000

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use raw SQL to guarantee CASCADE is applied
    op.execute(
        "ALTER TABLE screening_thinking DROP CONSTRAINT IF EXISTS fk_screening_thinking_history_id_screening_history;"
    )
    op.execute(
        "ALTER TABLE screening_thinking ADD CONSTRAINT fk_screening_thinking_history_id_screening_history "
        "FOREIGN KEY (history_id) REFERENCES screening_history(id) ON DELETE CASCADE;"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE screening_thinking DROP CONSTRAINT IF EXISTS fk_screening_thinking_history_id_screening_history;"
    )
    op.execute(
        "ALTER TABLE screening_thinking ADD CONSTRAINT fk_screening_thinking_history_id_screening_history "
        "FOREIGN KEY (history_id) REFERENCES screening_history(id) ON DELETE NO ACTION;"
    )
