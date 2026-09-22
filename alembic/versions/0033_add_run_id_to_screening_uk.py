"""make screening_history record key append-only by adding run_id (RV-03)

Revision ID: 0033 (RV-03)
Revises: 0032
Create Date: 2026-09-22 00:00:00.000000

RV-03: 研究记录本质是「某次运行的当时判断」，天然 append-only。当前唯一键
``(trade_date, strategy_name, ts_code)`` 以覆盖语义落库（LIFE-03），同一天多次
参数运行会静默销毁研究记录：params_snapshot/ai_reason/thinking 被覆盖、孤儿
记录残留、跨日重跑产生「状态 PENDING 带定稿标签」矛盾行。

本迁移把唯一键改为 ``(trade_date, strategy_name, ts_code, run_id)``：
- upgrade: 删旧 3 键唯一约束 → 建新 4 键唯一约束。
- downgrade: append-only 后同一 (trade_date, strategy_name, ts_code) 天然多 run 行，
  重建 3 键约束会因重复值失败 → 先按 3 键去重（保留 id 最大者，对齐 0024 先例）
  再建约束；去重即丢弃旧 run 行（数据损失，注释明确）。

编号说明：0032 已被 RV-02（reset_t0_close_basis_metrics）占用并合入 main，本
迁移确认为 0033（down_revision=0032 链）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CONSTRAINT = "uq_screening_history_dat_strategy_code"
_NEW_CONSTRAINT = "uq_screening_history_dat_strategy_code_run"


def upgrade() -> None:
    """Switch unique key to (trade_date, strategy_name, ts_code, run_id) (append-only)."""
    op.drop_constraint(_OLD_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(
        _NEW_CONSTRAINT,
        "screening_history",
        ["trade_date", "strategy_name", "ts_code", "run_id"],
    )


def downgrade() -> None:
    """Restore 3-key unique constraint (align 0024 dedup precedent).

    重要：downgrade 会先把重复行合并（按 3 键保留 id 最大者），丢弃更早 run 的
    数据——append-only 语义的代价，恢复覆盖语义必须如此（否则建约束失败）。
    """
    op.execute(
        """
        DELETE FROM screening_history a
        USING screening_history b
        WHERE a.id < b.id
          AND a.trade_date = b.trade_date
          AND a.strategy_name = b.strategy_name
          AND a.ts_code = b.ts_code
        """
    )
    op.drop_constraint(_NEW_CONSTRAINT, "screening_history", type_="unique")
    op.create_unique_constraint(
        _OLD_CONSTRAINT,
        "screening_history",
        ["trade_date", "strategy_name", "ts_code"],
    )
