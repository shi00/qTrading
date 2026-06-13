"""align macro updated at server default

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-13 19:00:00.000000

"""

from collections.abc import Sequence
import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _target_schema() -> str | None:
    """Resolve migration target schema (if configured)."""
    try:
        ctx = op.get_context()
    except Exception:
        return None
    return getattr(ctx, "version_table_schema", None)


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists in the database."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    schema = _target_schema()
    try:
        columns = inspector.get_columns(table_name, schema=schema)
        return any(col["name"] == column_name for col in columns)
    except Exception:
        return False


def upgrade() -> None:
    if _column_exists("macro_economy", "updated_at"):
        op.alter_column(
            "macro_economy",
            "updated_at",
            server_default=sa.text("now()"),
        )


def downgrade() -> None:
    if _column_exists("macro_economy", "updated_at"):
        op.alter_column(
            "macro_economy",
            "updated_at",
            server_default=None,
        )
