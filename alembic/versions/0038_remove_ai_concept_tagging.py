"""remove ai_concept tagging (drop failures table + purge AI_LLM_/AI_DOUBAO_ rows)

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-08 00:00:00.000000

「AI 概念打标 / AI概念重建」功能下线：删除错题本 ``ai_concept_failures`` 表，
并清理 ``stock_concepts`` 中由 LLM 打标写入的 ``AI_LLM_`` / 历史 ``AI_DOUBAO_``
前缀行。东财 ``EM_`` / 涨停 ``LIMIT_`` / Tushare ``TS_`` 三类前缀行保留。

数据不可恢复：downgrade 仅重建 ``ai_concept_failures`` 空表结构（列集对齐 0005
的 create_table），既有行与 ``stock_concepts`` 中被清理的 AI 前缀行无法还原。
``LIKE`` 中的下划线以 ``\\_`` 转义并配合 ``ESCAPE '\\'`` 作字面量匹配，避免 ``_``
被当作单字符通配符放大匹配范围。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PURGE_SQL = (
    "DELETE FROM stock_concepts WHERE concept_id LIKE 'AI\\_LLM\\_%' ESCAPE '\\' "
    "OR concept_id LIKE 'AI\\_DOUBAO\\_%' ESCAPE '\\'"
)


def _table_exists(table_name: str) -> bool:
    """Check if a table exists in the bound database."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Purge AI concept rows and drop the ai_concept_failures table."""
    op.execute(_PURGE_SQL)
    if _table_exists("ai_concept_failures"):
        op.drop_table("ai_concept_failures")


def downgrade() -> None:
    """Recreate the empty ai_concept_failures table (data is not recoverable)."""
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
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=True),
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
