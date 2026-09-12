"""add ann_date to financial_reports primary key (version dimension)

Revision ID: 0022
Revises: 0021
Create Date: 2026-09-12 00:00:00.000000

DATA-05：金融财报主表 ``financial_reports`` 原主键为 ``(ts_code, end_date)``，
同一报告期只保留一行，财报更正/追溯调整会 UPSERT 覆盖原值，导致 PIT
（``ann_date <= as_of``）无法还原历史公告日的可见值：
- 写原始 ann_date + 更正后数值 → 前视偏差（回测在更正前就用上更正数据）
- 写更正 ann_date → 数据丢失（原始公告日到更正日之间查不到该期财报）

本迁移将主键扩展为 ``(ts_code, end_date, ann_date)``，保留同一报告期的多版本。

迁移要点：
- 先清理 ``ann_date IS NULL`` 的行（缺失公告日的记录无法参与 PIT 还原，亦不能作为
  主键列），语义上等效于 DAT-06 告警的脏数据。
- 原主键 ``(ts_code, end_date)`` 全局唯一，故不存在 ``(ts_code, end_date, ann_date)``
  重复行，无需额外去重。
- 主键列 ``ann_date`` 置 NOT NULL，与模型声明（primary_key）一致，保持 alembic check 一致性。
- 索引不变：``ix_financial_reports_end_date`` / ``ix_financial_reports_ts_code_ann_date``
  / ``ix_financial_reports_ann_date`` 在 0001 已创建，模型列级 index=True 与 __table_args__
  声明与其一致。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Extend financial_reports primary key with ann_date."""
    # 1. 清理缺失公告日的脏数据行。
    op.execute("DELETE FROM financial_reports WHERE ann_date IS NULL")
    # 2. 移除原主键约束。
    op.drop_constraint("pk_financial_reports", "financial_reports", type_="primary")
    # 3. ann_date 置 NOT NULL（主键列，与模型声明一致）。
    op.alter_column(
        "financial_reports",
        "ann_date",
        existing_type=sa.Date(),
        existing_nullable=True,
        nullable=False,
    )
    # 4. 重建主键，加入 ann_date 维度。
    op.create_primary_key(
        "pk_financial_reports",
        "financial_reports",
        ["ts_code", "end_date", "ann_date"],
    )


def downgrade() -> None:
    """Restore the primary key to (ts_code, end_date).

    NOTE: ``ann_date IS NULL`` 行在 upgrade 中已被删除，此操作不可逆，downgrade
    仅还原表结构与主键语义，被清理的脏数据无法恢复（项目对脏数据不承诺保留）。
    """
    op.drop_constraint("pk_financial_reports", "financial_reports", type_="primary")
    op.alter_column(
        "financial_reports",
        "ann_date",
        existing_type=sa.Date(),
        existing_nullable=False,
        nullable=True,
    )
    op.create_primary_key(
        "pk_financial_reports",
        "financial_reports",
        ["ts_code", "end_date"],
    )
