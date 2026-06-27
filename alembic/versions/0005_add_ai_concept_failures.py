"""add ai_concept_failures table for retry queue (错题本)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-27 00:00:00.000000

Adds the ``ai_concept_failures`` table to persist failed AI concept tagging
attempts. The AIConceptTagSyncStrategy reads from this table at the start of
each run (priority retries with retry_count < max_retry and next_retry_at <=
now), upserts failures (retry_count+1, last_attempt_at, next_retry_at), and
deletes entries on successful tagging.

Schema:
  - ts_code (PK, String)
  - name (String)
  - last_error (String)
  - retry_count (Integer, default 0)
  - last_attempt_at (DateTime)
  - next_retry_at (DateTime)
  - created_at / updated_at (DateTime)
  - Indexes: next_retry_at, retry_count
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create ai_concept_failures table."""
    if _table_exists("ai_concept_failures"):
        return

    op.create_table(
        "ai_concept_failures",
        sa.Column("ts_code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("ts_code", name=op.f("pk_ai_concept_failures")),
    )
    op.create_index(
        "ix_ai_concept_failures_next_retry",
        "ai_concept_failures",
        ["next_retry_at"],
        if_not_exists=True,
    )
    op.create_index(
        "ix_ai_concept_failures_retry_count",
        "ai_concept_failures",
        ["retry_count"],
        if_not_exists=True,
    )


def downgrade() -> None:
    """Drop ai_concept_failures table."""
    op.drop_index(
        "ix_ai_concept_failures_retry_count",
        table_name="ai_concept_failures",
        if_exists=True,
    )
    op.drop_index(
        "ix_ai_concept_failures_next_retry",
        table_name="ai_concept_failures",
        if_exists=True,
    )
    if _table_exists("ai_concept_failures"):
        op.drop_table("ai_concept_failures")
