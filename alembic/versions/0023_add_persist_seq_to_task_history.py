"""add persist_seq to task_history (LIFE-02)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-12 00:00:00.000000

LIFE-02：任务状态持久化无顺序保证，多次 fire-and-forget 写入（`_persist_task` 每次
创建独立 asyncio task）间无顺序保证，且 SQL 是无条件覆盖。停机路径上更早调度、更晚
完成的 RUNNING 快照可能落在 CANCELLED/COMPLETED 之后，覆盖终态，导致 DB 残留
RUNNING，重启后 init_db 误判为 INTERRUPTED。

本迁移为 ``task_history`` 新增 ``persist_seq`` 单调序号（默认 0，兼容存量行），配合
``INSERT ... ON CONFLICT ... WHERE task_history.persist_seq < EXCLUDED.persist_seq``
守卫，使乱序到达的旧快照不会覆盖更新的终态写入。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table_name: str, column_name: str) -> bool:
    """Check if a column exists on a table in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns(table_name)}
    return column_name in columns


def upgrade() -> None:
    """Add persist_seq column (monotonic ordering guard) to task_history."""
    if not _column_exists("task_history", "persist_seq"):
        op.add_column(
            "task_history",
            sa.Column(
                "persist_seq",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    """Drop persist_seq column."""
    if _column_exists("task_history", "persist_seq"):
        op.drop_column("task_history", "persist_seq")
