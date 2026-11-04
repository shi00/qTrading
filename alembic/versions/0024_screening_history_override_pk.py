"""replace screening_history unique constraint (run_id, ts_code) -> (trade_date, strategy_name, ts_code)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-12 00:00:00.000000

LIFE-03: screening_history 唯一键由 (run_id, ts_code) 改为 (trade_date, strategy_name, ts_code)，
新运行以覆盖语义落库（保留最新快照）。修复复盘统计（UN-04）将同一天同一策略同一股票的
多次运行重复计入样本的问题。run_id 降级为普通列，仅作展示。

存量数据去重：对 (trade_date, strategy_name, ts_code) 重复组，用单次确定性语句
``row_number() OVER (PARTITION BY trade_date, strategy_name, ts_code
ORDER BY created_at DESC NULLS LAST, id DESC)`` 标记 rn>1 的行删除，保留每组 1 行；
screening_thinking 经 history_id 外键 ondelete=CASCADE 级联清理。

NULL 排序政策：``created_at IS NULL`` 视为最旧、排在最后（``NULLS LAST``），不伪造
created_at。因此组内保留最新非 NULL 行；全 NULL 组与同 created_at 组按 ``id DESC``
保留最大 id。注意：该 tie-break 与旧 0024 草稿「相同 created_at 保留最小 id」的兜底
语义不同，本迁移只保留这一条确定性规则，避免两种规则并存造成歧义。去重为有损清理
（重复行不归档），属既有 0024 设计。

down_upgrade 反向：删除新约束、重建旧约束 uq(run_id, ts_code)。run_id 为每策略独立 uuid，
同 run 多 ts_code 行仍满足旧约束，(run_id, ts_code) 跨策略不冲突，重建安全。
"""

from collections.abc import Sequence


from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "uq_screening_history_run_code"
_NEW_CONSTRAINT = "uq_screening_history_dat_strategy_code"

# 已登记的 INDEX 名（models.py 定义，迁移不新增）
# idx_sh_date_strategy(trade_date, strategy_name) 已覆盖新唯一键前缀


def upgrade() -> None:
    """Replace unique constraint and dedup legacy duplicate rows."""
    # 单次确定性清理，每组只保留 row_number = 1 的行。
    # 排序政策：created_at DESC NULLS LAST（NULL 视为最旧、排在最后，不伪造时间戳），
    # 并列时 id DESC（保留最大 id）。单一语句覆盖全 NULL 组、NULL 与非 NULL 混合组、
    # 同 created_at 组等旧 schema 合法行组合；原两条 < / = 自连接在 NULL 参与比较时
    # 结果为 UNKNOWN，会残留重复行导致后续三键唯一约束创建失败。
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY trade_date, strategy_name, ts_code
                       ORDER BY created_at DESC NULLS LAST, id DESC
                   ) AS rn
            FROM screening_history
        )
        DELETE FROM screening_history
        WHERE id IN (SELECT id FROM ranked WHERE rn > 1)
        """
    )
    op.drop_constraint(_OLD_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(_NEW_CONSTRAINT, "screening_history", ["trade_date", "strategy_name", "ts_code"])


def downgrade() -> None:
    """Restore the old unique constraint."""
    op.drop_constraint(_NEW_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(_OLD_CONSTRAINT, "screening_history", ["run_id", "ts_code"])
