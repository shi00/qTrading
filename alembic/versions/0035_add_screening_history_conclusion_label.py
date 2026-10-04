"""add screening_history.conclusion_label

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-04 00:00:00.000000

MAJOR-01（输出契约统一）：screening_history 新增 conclusion_label 列，承载模型给出的
定性结论枚举（strong_buy / watchlist / uncertain / reject），与 ai_score 数值并存，
使「模型明确否决（reject）」等结论在历史回看时不丢失。列可为 NULL：failed/未打分路径
及存量历史行未记录结论（区别于业务上合法的具体标签，R21）。与
data/persistence/models.py 对齐（nullable 列，无需 server_default）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add screening_history.conclusion_label (nullable)."""
    op.add_column(
        "screening_history",
        sa.Column("conclusion_label", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    """Drop screening_history.conclusion_label (data loss, non-recoverable)."""
    op.drop_column("screening_history", "conclusion_label")
