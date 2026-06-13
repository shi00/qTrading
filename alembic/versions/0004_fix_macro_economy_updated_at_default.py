"""fix macro_economy updated_at default

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-13 00:00:00.000000

Adds missing server_default to macro_economy.updated_at to match ORM.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Set server_default for macro_economy.updated_at."""
    with op.batch_alter_table("macro_economy") as batch_op:
        batch_op.alter_column("updated_at", server_default=sa.text("now()"))


def downgrade() -> None:
    """Remove server_default from macro_economy.updated_at."""
    with op.batch_alter_table("macro_economy") as batch_op:
        batch_op.alter_column("updated_at", server_default=None)
