"""add crash-retry columns to task_history (LIFE-01)

Revision ID: 0023
Revises: 0022
Create Date: 2026-09-12 00:00:00.000000

LIFE-01（崩溃后重试中断任务）：factory 是闭包无法直接序列化，改为序列化其注册键与
重建参数。本迁移为 ``task_history`` 新增三列：
- ``unique_key``：去重键，崩溃恢复重推时复用以防止并发重推（与数据源页共享同一键）。
- ``factory_key``：可重建任务工厂的注册键，app 重启后据其从注册表回填 factory。
- ``retry_kwargs``：重建参数的 JSON 序列化，重启后反序列化回填 ``_coroutine_kwargs``。

三列均可空（NULL）表示该任务未登记可重建能力——历史存量行自动降级为不可重试，
行为与现状一致（不显示重试按钮），向后兼容。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add unique_key / factory_key / retry_kwargs columns to task_history."""
    op.add_column("task_history", sa.Column("unique_key", sa.String(), nullable=True))
    op.add_column("task_history", sa.Column("factory_key", sa.String(), nullable=True))
    op.add_column("task_history", sa.Column("retry_kwargs", sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the crash-retry columns. Retry info lost (non-recoverable)."""
    op.drop_column("task_history", "retry_kwargs")
    op.drop_column("task_history", "factory_key")
    op.drop_column("task_history", "unique_key")
