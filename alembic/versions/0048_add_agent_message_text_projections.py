"""add agent message text projections (I76)

Revision ID: 0048
Revises: 0047
Create Date: 2026-11-19 00:00:00.000000

外部资料三表示分列（``agent_message``）：

1. ``body_text`` 保持原值（精确回读/摘要与重放比较的身份素材，不改写）；
2. ``model_text`` 新增可空列：模型可见投影（外部文本经中和 + PII 脱敏后截断；
   用户自述保持原值）；
3. ``search_text``（既有列）语义收敛为公开导出/索引投影；
4. ``body_untrusted`` 新增可空列：来源标记（True=外部不可信，False=用户自述）。

存量行策略：两列均以 NULL 补建，**不回填**——历史行的投影与来源无法在迁移内
可靠重建，回填会把「未记录」伪装成「已中和/可信」（R21）；读取侧按 NULL 视为
未记录，不做等值假设。

``downgrade`` 仅删除两列（不改数据，可逆）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0048"
down_revision: str | Sequence[str] | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add model-visible projection and external-origin marker columns."""

    op.add_column("agent_message", sa.Column("model_text", sa.Text(), nullable=True))
    op.add_column("agent_message", sa.Column("body_untrusted", sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Drop the two projection columns (no data rewritten on upgrade)."""

    op.drop_column("agent_message", "body_untrusted")
    op.drop_column("agent_message", "model_text")
