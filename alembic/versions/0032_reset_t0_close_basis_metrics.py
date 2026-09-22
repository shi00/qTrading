"""reset review metrics computed under T0-close basis (RV-02 T+1-open basis)

Revision ID: 0032
Revises: 0031
Create Date: 2026-09-22 00:00:00.000000

RV-02: 复盘收益基准由 T0 收盘价改为 T+1 开盘复权价（对齐回测默认 next_open）。
旧口径把「T0 收盘 → T+1 开盘」的隔夜跳空计入用户收益（对系统性高开策略为单向
正偏差），且回测页与复盘页口径不一致。修复后个股 t1_pct/t5_pct 与指数基准窗口
起点均改为 T+1 开盘。

存量旧口径数值（t1_pct/t5_pct/alpha/prediction_result/index_pct）均不可信，须全部
重算。与 0030（RV-01）不同：0030 只重置 ``COMPLETED AND alpha IS NOT NULL`` 且保留
t1_pct/t1_price（其声明「t1_pct 不受窗口口径影响」在本问题下不成立——RV-02 改变
的是基点，t1_pct 与 t5_pct 的数值本身均由 T0 收盘改 T+1 开盘，必须全部重算）。
因此本迁移覆盖已计算过旧基点数值的 COMPLETED 与 T1_DONE 两态：

- ``COMPLETED``：
  - **超窗**（trade_date < 第 60 个交易日，复用 0026/0030 的 trade_cal 锚定）→
    ``EXPIRED`` 终态：数据过旧失回填价值，不再入学习样本（与 0026/0030 语义一致）。
  - **窗口内** → 置 ``PENDING`` + 清 t1_pct/t5_pct/t1_price/t5_price/alpha/
    prediction_result/index_pct/benchmark_code，进 backfill 通道以新基重算。
- ``T1_DONE AND (t1_pct IS NOT NULL OR t5_pct IS NOT NULL)``：**不论窗口内外**一律置
  ``PENDING`` + 清同样八列。理由（对抗检视闭合）：T1_DONE 行的 t1_pct 是 T0 收盘
  基点旧数值；RV-04 数值-only 解耦落库的 T1_DONE 行（t5_pct 已有）基点也是旧口径；
  backfill 通道无年龄截断（status+列过滤、无 trade_date cutoff），超窗 T1_DONE 若
  不重置会被以新基重算 t5/alpha、而同行 t1_pct 保持旧基 → 一行内 t1(旧)/t5(新)/alpha(新)
  混基，污染学习样本与统计。统一置 PENDING 后经 backfill_t1_returns 以新基重算
  t1 → T1_DONE → backfill_horizon_returns 定稿 t5+标签，状态机完整。

不重置 params_snapshot/ai_score/ai_reason/thinking（历史研究记录保留）。
基准 anchor：OFFSET 59 取「第 60 个交易日之前的日期」（与 0026/0030 表述一致）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 合理回溯窗口：60 个交易日（与 0026/0030 常量一致，分隔词不拆开以免口径漂移）。
_EXPIRE_LOOKBACK_TRADE_DAYS = 60

# RV-02 需清空的旧基点口径列（基准、数值、标签、统计）。
_RESET_COLS = """
    t1_pct = NULL,
    t5_pct = NULL,
    t1_price = NULL,
    t5_price = NULL,
    alpha = NULL,
    prediction_result = NULL,
    index_pct = NULL,
    benchmark_code = NULL
"""


def upgrade() -> None:
    """Reset stale review metrics computed under T0-close basis to PENDING (or EXPIRED)."""
    # 1) 超窗：>60 交易日的旧 COMPLETED 置 EXPIRED 终态（不进复盘/回填池，对标 0026/0030）。
    op.execute(
        f"""
        UPDATE screening_history sh
        SET review_status = 'EXPIRED'
        WHERE sh.review_status = 'COMPLETED'
          AND sh.trade_date < (
              SELECT tc.cal_date
              FROM trade_cal tc
              WHERE tc.is_open = 1
                AND tc.cal_date <= CURRENT_DATE
              ORDER BY tc.cal_date DESC
              OFFSET {_EXPIRE_LOOKBACK_TRADE_DAYS - 1} LIMIT 1
          )
        """
    )
    # 2) 窗口内 COMPLETED：置 PENDING + 清旧基点数值/标签，触发 backfill 以新基重算。
    op.execute(
        f"""
        UPDATE screening_history
        SET review_status = 'PENDING',
            {_RESET_COLS}
        WHERE review_status = 'COMPLETED'
        """
    )
    # 3) T1_DONE 且任一旧基点数值非空（含 RV-04 数值-only 解耦落库行）：不论窗口内外
    #    一律置 PENDING + 清列——消除「一行 t1(旧)/t5(新) 混基」污染（对抗检视闭合）。
    op.execute(
        f"""
        UPDATE screening_history
        SET review_status = 'PENDING',
            {_RESET_COLS}
        WHERE review_status = 'T1_DONE'
          AND (t1_pct IS NOT NULL OR t5_pct IS NOT NULL)
        """
    )


def downgrade() -> None:
    """No-op: 旧值为 T0 收盘口径、与需求正本冲突，无法恢复（对齐 0030 先例）。

    CI 的 downgrade base → upgrade head 为重建式，不受影响。
    """
    pass
