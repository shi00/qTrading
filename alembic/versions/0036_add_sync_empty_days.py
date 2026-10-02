"""add sync_empty_days table (review09-24 dim05 MAJOR-03)

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-02 00:00:00.000000

新增 ``sync_empty_days`` 表：按 ``(table_name, trade_date)`` 精确登记"稀疏表在某个交易日
已成功抓取且结果合法为空"的事实。质量评分据此把"已尝试且合法为空"与"从未尝试的真实缺口"
区分开，避免 dense 表已完整仍因个别稀疏空表反复触发重同步。

原实现用 ``app_state`` 中的单点高水位 ``sync_attempted_upto:<table>``（值形如 YYYYMMDD）
作区间豁免判据：某表某日 ``count == 0`` 且 ``trade_date <= 水位`` 即判"已尝试合法为空"。
单点水位无法表达"区间内某些日为空、某些日为真实缺口"，会把水位之前的真实缺口一并豁免，
故改为按 (表, 日) 的显式登记。

复合主键 ``(table_name, trade_date)``；表内无业务列，写入语义为幂等 upsert（重复冲突键走
``ON CONFLICT DO NOTHING``、不刷新时间戳、不产生重复行），无自增 id。时间戳列 nullable、
首插由 ``server_default=now()`` 填充，与既有表（如 ``0001_initial_schema``）约定一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the sync_empty_days registration table."""
    op.create_table(
        "sync_empty_days",
        sa.Column("table_name", sa.String(), primary_key=True),
        sa.Column("trade_date", sa.Date(), primary_key=True),
        sa.Column("updated_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=False), server_default=sa.text("now()"), nullable=True),
    )


def downgrade() -> None:
    """Drop the sync_empty_days table (data loss, non-recoverable; 可在下次同步重建)."""
    op.drop_table("sync_empty_days")
