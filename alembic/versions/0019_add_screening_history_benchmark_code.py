"""add screening_history.benchmark_code

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-09 00:00:00.000000

D2-5：screening_history 新增 benchmark_code 列，记录每批复盘实际使用的基准指数代码
（Alpha 比较对象），使历史结果具备自解释性。列可为 NULL：存量历史行逐年早于该迁移，
基准未知；新复盘记录经由 update_prediction_result 与 index_pct/alpha 同批写入。
与 data/persistence/models.py 对齐（nullable 列，无需 server_default）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add screening_history.benchmark_code (nullable)."""
    op.add_column(
        "screening_history",
        sa.Column("benchmark_code", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    """Drop screening_history.benchmark_code."""
    op.drop_column("screening_history", "benchmark_code")
