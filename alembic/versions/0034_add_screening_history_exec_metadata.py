"""add screening_history exec_warnings / filter_attribution

Revision ID: 0034
Revises: 0033
Create Date: 2026-09-24 00:00:00.000000

CRITICAL-02（R21/BT-03）：screening_history 新增两列 JSONB，承载策略执行期
可信度元数据——``exec_warnings``（执行期 warnings 通道，Message key/params 序列化）
与 ``filter_attribution``（每行筛选归因），避免历史回看把「已知不可信」渲染成
「无信息」。两列均可为 NULL：存量历史行早于该迁移，未记录执行上下文
（UI 据此走「未记录」分支而非「无警告」）。与 data/persistence/models.py 对齐
（nullable 列，无需 server_default）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add screening_history.exec_warnings / filter_attribution (nullable JSONB)."""
    op.add_column("screening_history", sa.Column("exec_warnings", JSONB(), nullable=True))
    op.add_column("screening_history", sa.Column("filter_attribution", JSONB(), nullable=True))


def downgrade() -> None:
    """Drop screening_history.exec_warnings / filter_attribution (data loss, non-recoverable)."""
    op.drop_column("screening_history", "filter_attribution")
    op.drop_column("screening_history", "exec_warnings")
