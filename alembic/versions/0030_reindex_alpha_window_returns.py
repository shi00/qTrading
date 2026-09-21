"""reset stale COMPLETED review labels computed under wrong alpha window (RV-01)

Revision ID: 0030
Revises: 0029
Create Date: 2026-09-21 00:00:00.000000

RV-01: 复盘打标签的 Alpha 窗口口径错误——个股收益取 T0→T+5 累计，基准侧却取
T+5 当日单日涨跌幅（``_prefetch_index_cache`` 缓存 ``pct_chg``、``run_review``/
``backfill_horizon_returns`` 直接相减），导致标签系统性偏向市场方向（牛市放大
WIN、熊市放大 LOSS），AI few-shot 学习样本与复盘统计均基于错误标签。修复后
基准侧改为窗口累计收益（``close`` 两点）。

存量 ``COMPLETED`` 且 ``alpha IS NOT NULL`` 的记录其 alpha/prediction_result/
index_pct 均为旧口径、不可信，须重置以新口径重算：

- **60 交易日窗口内**的记录（trade_date 落在最近 60 个交易日内）：alpha/
  prediction_result/index_pct/t5_pct/t5_price 置 NULL、review_status 置
  ``T1_DONE``——进入 ``backfill_horizon_returns`` 通道（只处理 T1_DONE 且
  t5_pct IS NULL），以新口径重算 T+5 数值与标签。t1_pct/t1_price/benchmark_code
  保留（不受 Alpha 窗口口径影响）。
- **超窗**（超过 60 个交易日）的旧 COMPLETED：直接置 ``EXPIRED`` 终态——
  数据过旧、丧失回填价值（与 0026 的 lookback 语义一致），避免一次性把海量
  过期记录塞进 backfill 的 LIMIT 2000 队列、饿死后续待复盘记录（对抗检视
  Blocking-2）。

窗口锚定复用 0026 的 ``trade_cal`` 方式：``OFFSET 59`` 取「第 60 个交易日之前的日期」
（即 trade_date < 第 60 个交易日的记录视为超窗），与 0026 的 OFFSET 表述一致。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 合理回溯窗口：60 个交易日（与 0026 常量一致，分隔词不拆开以免口径漂移）。
_EXPIRE_LOOKBACK_TRADE_DAYS = 60


def upgrade() -> None:
    """Reset stale COMPLETED labels to T1_DONE (window) or EXPIRED (beyond window)."""
    # 超窗：>60 交易日的旧 COMPLETED 置 EXPIRED 终态（不进复盘/回填池）。
    op.execute(
        f"""
        UPDATE screening_history sh
        SET review_status = 'EXPIRED'
        WHERE sh.review_status = 'COMPLETED'
          AND sh.alpha IS NOT NULL
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
    # 窗口内：重置为 T1_DONE + 清空旧口径标签列，触发 backfill 通道以新口径重算。
    op.execute(
        """
        UPDATE screening_history
        SET review_status = 'T1_DONE',
            alpha = NULL,
            prediction_result = NULL,
            index_pct = NULL,
            t5_pct = NULL,
            t5_price = NULL
        WHERE review_status = 'COMPLETED'
          AND alpha IS NOT NULL
        """
    )


def downgrade() -> None:
    """No-op: 旧 alpha 本为错误窗口口径，重置为有意数据修正，无法恢复。

    与 0026（reversible data migration）不同、也偏离「迁移可逆」惯例：
    旧值依赖已被修复的旧代码口径，恢复旧值即恢复错误标签，故 downgrade
    有意不做还原（CI 的 downgrade base → upgrade head 为重建式，不受影响）。
    """
    pass
