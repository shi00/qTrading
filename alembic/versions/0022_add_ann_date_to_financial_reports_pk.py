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

downgrade（D04-F01）说明：
- 三键主键允许同一 ``(ts_code, end_date)`` 存在多个合法公告版本；直接重建
  ``(ts_code, end_date)`` 主键会在这些行上冲突。故回退默认 fail-closed：
  在任何破坏性 DDL 之前统计 ``(ts_code, end_date)`` 重复组，存在重复且未显式授权时
  抛 ``RuntimeError``（事务回滚后仍停留 head，不删除任何数据）。
- 显式有损授权路径：``alembic -x allow_lossy_rollback=1 downgrade <目标版本>``。
  **操作者 SHALL 在执行前自行用 ``pg_dump`` 归档 ``financial_reports``**；有损路径会
  删除每组较旧的公告版本，且同一 ``(ts_code, end_date, ann_date)`` 只会保留一行。
  授权后按 ``ann_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST``
  每组保留一行、删除其余，并记录删除行数日志，再执行原有主键还原。
  执行本迁移时 ``ann_date`` 仍为 NOT NULL（upgrade 已置 NOT NULL），三键主键保证组内
  ``ann_date`` 唯一，因此保留规则确定；``updated_at`` / ``created_at`` 为 0001 已存在的
  可空时间列，仅作兜底排序键。
- 离线 ``--sql`` 模式无法执行数据预检，默认拒绝生成可能失败的破坏性脚本。
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

_LOSSY_ROLLBACK_X_ARG = "allow_lossy_rollback"
_LOSSY_ROLLBACK_ENABLED_VALUE = "1"

# 破坏性 DDL 之前的只读预检：统计存在多公告版本的 (ts_code, end_date) 组数。
_DUPLICATE_GROUPS_SQL = sa.text(
    """
    SELECT count(*)
    FROM (
        SELECT 1
        FROM financial_reports
        GROUP BY ts_code, end_date
        HAVING count(*) > 1
    ) AS duplicate_groups
    """
)

# 显式有损授权后的确定性去重：每组仅保留 ann_date 最新版本。
_DELETE_EXTRA_VERSIONS_SQL = sa.text(
    """
    DELETE FROM financial_reports AS target
    USING (
        SELECT
            ts_code,
            end_date,
            ann_date,
            row_number() OVER (
                PARTITION BY ts_code, end_date
                ORDER BY ann_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST
            ) AS version_rank
        FROM financial_reports
    ) AS ranked
    WHERE target.ts_code = ranked.ts_code
      AND target.end_date = ranked.end_date
      AND target.ann_date = ranked.ann_date
      AND ranked.version_rank > 1
    """
)


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


def _lossy_rollback_authorized() -> bool:
    """Return True only when ``-x allow_lossy_rollback=1`` was explicitly passed."""
    x_arguments = context.get_x_argument(as_dictionary=True)
    return x_arguments.get(_LOSSY_ROLLBACK_X_ARG) == _LOSSY_ROLLBACK_ENABLED_VALUE


def _count_duplicate_groups(bind: sa.Connection) -> int:
    """Count ``(ts_code, end_date)`` groups holding more than one announcement version."""
    return int(bind.execute(_DUPLICATE_GROUPS_SQL).scalar_one())


def _delete_extra_versions(bind: sa.Connection) -> int:
    """Keep the latest announcement version per group, delete the rest, and return deleted rows."""
    return int(bind.execute(_DELETE_EXTRA_VERSIONS_SQL).rowcount)


def _restore_old_primary_key() -> None:
    """Restore the original ``(ts_code, end_date)`` primary key (destructive DDL)."""
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


def _handle_multiple_versions(bind: sa.Connection) -> None:
    """Fail closed unless lossy rollback is explicitly authorized; prune when authorized."""
    duplicate_groups = _count_duplicate_groups(bind)
    if duplicate_groups == 0:
        return
    if not _lossy_rollback_authorized():
        raise RuntimeError(
            f"0022 downgrade 默认拒绝：financial_reports 中有 {duplicate_groups} 个 "
            "(ts_code, end_date) 存在多个公告版本（ann_date），重建旧主键 (ts_code, end_date) 会冲突。"
            "本迁移已在任何破坏性 DDL 之前终止，事务回滚后数据库仍停留当前版本且未删除数据。"
            "如确需回退，请先自行用 pg_dump 归档 financial_reports（有损路径会删除每组较旧的公告版本），"
            "再显式授权：alembic -x allow_lossy_rollback=1 downgrade <目标版本>。"
        )
    deleted_rows = _delete_extra_versions(bind)
    logger.info(
        "0022 downgrade 有损回退：%s 个 (ts_code, end_date) 存在多公告版本，按 "
        "ann_date DESC, updated_at DESC NULLS LAST, created_at DESC NULLS LAST 每组保留最新版本，"
        "共删除 %s 行。",
        duplicate_groups,
        deleted_rows,
    )


def downgrade() -> None:
    """Restore the primary key to (ts_code, end_date).

    默认 fail-closed：存在多公告版本时在破坏性 DDL 之前抛错并依赖迁移事务回滚。
    显式有损路径见模块 docstring（``-x allow_lossy_rollback=1``）。

    NOTE: ``ann_date IS NULL`` 行在 upgrade 中已被删除，此操作不可逆，downgrade
    仅还原表结构与主键语义，被清理的脏数据无法恢复（项目对脏数据不承诺保留）。
    """
    if context.is_offline_mode():
        raise RuntimeError(
            "0022 downgrade 需要在线数据库连接以完成 (ts_code, end_date) 多公告版本预检；"
            "离线 --sql 模式默认拒绝生成可能失败的破坏性脚本。"
        )
    bind = op.get_bind()
    _handle_multiple_versions(bind)
    _restore_old_primary_key()
