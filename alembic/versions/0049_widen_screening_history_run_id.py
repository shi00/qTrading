"""widen screening_history.run_id to hold batch identity

Revision ID: 0049
Revises: 0048
Create Date: 2026-11-23 00:00:00.000000

F34/AC23：实时 AI 选股与夜间预测的批次入口把 ``stock_batch_<32 hex>``（44 字符）
作为 ``run_id`` 落库，并与 ``ai_invocation.run_id``（无长度上限）共用同一身份用于
费用链回查。``screening_history.run_id`` 原为 ``VARCHAR(16)``，PostgreSQL 会以
「value too long for type character varying(16)」拒绝写入：实时路径保存失败（结果
只留内存），夜间路径不产生可复盘记录。

本迁移只扩列宽（16 → 64），不截断/不改写任何存量值：既有 16 字符记录原样保留，
44 字符批次身份完整落库。禁止在保存服务或批量 DAO 侧截断长身份——截断会让不同
批次碰撞到同一 ``run_id``，破坏 append-only 唯一键
``(trade_date, strategy_name, ts_code, run_id)`` 与「历史 ↔ 账本」关联回查。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0049"
down_revision: str | Sequence[str] | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen run_id to 64 chars (PostgreSQL in-place metadata change, no row rewrite)."""
    op.alter_column(
        "screening_history",
        "run_id",
        existing_type=sa.String(16),
        type_=sa.String(64),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Restore 16 chars; fails on rows holding longer identities (widening is lossless)."""
    op.alter_column(
        "screening_history",
        "run_id",
        existing_type=sa.String(64),
        type_=sa.String(16),
        existing_nullable=False,
    )
