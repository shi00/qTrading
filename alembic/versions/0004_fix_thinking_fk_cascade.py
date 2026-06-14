"""fix_thinking_fk_cascade

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-14 13:45:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Use batch_alter_table to safely drop and recreate the constraint
    with op.batch_alter_table("screening_thinking") as batch_op:
        batch_op.drop_constraint("fk_screening_thinking_history_id_screening_history", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_screening_thinking_history_id_screening_history",
            "screening_history",
            ["history_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("screening_thinking") as batch_op:
        batch_op.drop_constraint("fk_screening_thinking_history_id_screening_history", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_screening_thinking_history_id_screening_history", "screening_history", ["history_id"], ["id"]
        )
