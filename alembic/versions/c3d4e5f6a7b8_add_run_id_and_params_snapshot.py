"""add_run_id_and_params_snapshot

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-04-26 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | Sequence[str] | None = "b7c8d9e0f1a2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c["name"] for c in inspector.get_columns(table_name)]
    return column_name in columns


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    constraints = inspector.get_unique_constraints(table_name)
    return any(c["name"] == constraint_name for c in constraints)


def _index_exists(table_name: str, index_name: str) -> bool:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [idx["name"] for idx in inspector.get_indexes(table_name)]
    return index_name in indexes


def upgrade() -> None:
    if not _column_exists("screening_history", "run_id"):
        op.add_column("screening_history", sa.Column("run_id", sa.String(16), nullable=True))

    if not _column_exists("screening_history", "params_snapshot"):
        op.add_column("screening_history", sa.Column("params_snapshot", sa.String(), nullable=True))

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE screening_history
            SET run_id = UPPER(SUBSTR(MD5(trade_date || strategy_name), 1, 12))
            WHERE run_id IS NULL
            """
        )
    )

    nullable_info = {c["name"]: c["nullable"] for c in sa.inspect(conn).get_columns("screening_history")}
    if nullable_info.get("run_id", True):
        op.alter_column("screening_history", "run_id", nullable=False)

    if _constraint_exists("screening_history", "uq_screening_history_date_strategy_code"):
        op.drop_constraint("uq_screening_history_date_strategy_code", "screening_history", type_="unique")

    if not _constraint_exists("screening_history", "uq_screening_history_run_code"):
        op.create_unique_constraint("uq_screening_history_run_code", "screening_history", ["run_id", "ts_code"])

    if not _index_exists("screening_history", "idx_sh_run_id"):
        op.create_index("idx_sh_run_id", "screening_history", ["run_id"])


def downgrade() -> None:
    if _index_exists("screening_history", "idx_sh_run_id"):
        op.drop_index("idx_sh_run_id", table_name="screening_history")

    if _constraint_exists("screening_history", "uq_screening_history_run_code"):
        op.drop_constraint("uq_screening_history_run_code", "screening_history", type_="unique")

    if not _constraint_exists("screening_history", "uq_screening_history_date_strategy_code"):
        op.create_unique_constraint(
            "uq_screening_history_date_strategy_code",
            "screening_history",
            ["trade_date", "strategy_name", "ts_code"],
        )

    if _column_exists("screening_history", "params_snapshot"):
        op.drop_column("screening_history", "params_snapshot")
    if _column_exists("screening_history", "run_id"):
        op.drop_column("screening_history", "run_id")
