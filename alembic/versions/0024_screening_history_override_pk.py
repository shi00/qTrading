"""replace screening_history unique constraint (run_id, ts_code) -> (trade_date, strategy_name, ts_code)

Revision ID: 0024
Revises: 0023
Create Date: 2026-09-12 00:00:00.000000

LIFE-03: screening_history 唯一键由 (run_id, ts_code) 改为 (trade_date, strategy_name, ts_code)，
新运行以覆盖语义落库（保留最新快照）。修复复盘统计（UN-04）将同一天同一策略同一股票的
多次运行重复计入样本的问题。run_id 降级为普通列，仅作展示。

存量数据去重：对 (trade_date, strategy_name, ts_code) 重复组，保留 created_at 最新行，
删除其余行；screening_thinking 经 history_id 外键 ondelete=CASCADE 级联清理。

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
    op.execute(
        """
        DELETE FROM screening_history a
        USING screening_history b
        WHERE a.trade_date = b.trade_date
          AND a.strategy_name = b.strategy_name
          AND a.ts_code = b.ts_code
          AND a.created_at < b.created_at
        """
    )
    # 以上自连接删除重复组中非最新 created_at 的行；实际 created_at 为 server_default now()
    # 微秒级几乎不重复，上面的语句已覆盖绝大部分场景，但仍留一行兜底：
    # 若存在完全同 created_at 的重复组，按 id 单向删除——保留组内最小 id 行，删除其余行，
    # 避免对称自连接（a.id<>b.id）把组内行成对互删导致整组清空。
    op.execute(
        """
        DELETE FROM screening_history a
        USING screening_history b
        WHERE a.trade_date = b.trade_date
          AND a.strategy_name = b.strategy_name
          AND a.ts_code = b.ts_code
          AND a.id > b.id
          AND a.created_at = b.created_at
        """
    )
    op.drop_constraint(_OLD_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(_NEW_CONSTRAINT, "screening_history", ["trade_date", "strategy_name", "ts_code"])


def downgrade() -> None:
    """Restore the old unique constraint."""
    op.drop_constraint(_NEW_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(_OLD_CONSTRAINT, "screening_history", ["run_id", "ts_code"])
