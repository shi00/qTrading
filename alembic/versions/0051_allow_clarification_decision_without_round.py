"""allow pre-dispatch clarification decisions without a round

Revision ID: 0051
Revises: 0050
Create Date: 2026-11-25 00:00:00.000000

主回合可在派发前产出澄清决定（``decision_kind = 'user_choice'``）：此时执行
尚未绑定轮次，``agent_decision.round_id`` 必须允许为空，``node_id`` 一并放宽
（决定身份仍含节点）。其余决定种类仍必须同时有轮与节点，故以 CHECK 把放宽
限定在 ``user_choice``，避免放开列约束后被其它种类滥用。

``upgrade`` 顺序：先放宽两列，再加 CHECK。存量行在旧约束下均非空，扫描不
阻断；``round_id`` 保留复合外键（NULL 时 PostgreSQL 按 MATCH SIMPLE 跳过）。

``downgrade`` 的不可逆数据边界：先删除 ``round_id`` 或 ``node_id`` 为空的决定行
（派发前澄清决定），再恢复 NOT NULL。被删行及其答复命令不可恢复；迁移不把无轮
决定改写成带轮决定，也不伪造轮次归属（该改写会让决定指向并非其来源的研究轮）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Allow NULL round/node for clarification decisions and fence the other kinds."""

    op.alter_column(
        "agent_decision",
        "round_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "agent_decision",
        "node_id",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.execute(
        "ALTER TABLE agent_decision ADD CONSTRAINT ck_agent_decision_clarification_round_required "
        "CHECK (decision_kind = 'user_choice' OR (round_id IS NOT NULL AND node_id IS NOT NULL))"
    )


def downgrade() -> None:
    """Drop pre-dispatch decisions, then restore NOT NULL (data loss, non-recoverable)."""

    op.execute("ALTER TABLE agent_decision DROP CONSTRAINT ck_agent_decision_clarification_round_required")
    op.execute("DELETE FROM agent_decision WHERE round_id IS NULL OR node_id IS NULL")
    op.alter_column(
        "agent_decision",
        "node_id",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.alter_column(
        "agent_decision",
        "round_id",
        existing_type=sa.Text(),
        nullable=False,
    )
