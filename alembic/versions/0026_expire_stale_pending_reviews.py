"""expire stale pending screening_history records (BIZ-01)

Revision ID: 0026
Revises: 0025
Create Date: 2026-09-15 00:00:00.000000

BIZ-01: 移除 ``get_pending_predictions`` 的 ``ai_score > 0`` 过滤后，历史上以
``ai_score=0`` 落库的纯数学策略 / 无 AI 用户记录将重新进入复盘池。若不处理，
存量超窗 PENDING 记录会一次性全部被 T+1 回填通道（BIZ-03）处理，触发巨量行情拉取。

本迁移把超出合理回溯窗口（60 个交易日，由 trade_cal 锚定）且从未完成 T+1 的
PENDING/NULL 记录置为终态 ``EXPIRED``：

- 不进入复盘池（get_pending_predictions / get_pending_reviews 均不匹配 EXPIRED）；
- 不被 T+1 回填通道处理（数据过旧、回填价值低）；
- 只动 ``t1_pct IS NULL`` 的 PENDING/NULL——已过 T+1 的 T1_DONE 记录不受影响，
  T+5 回填通道继续正常兜底。

``ai_score`` 列自 0001 起即 ``nullable=True``，无需 ALTER，本迁移仅做数据状态整理。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 合理回溯窗口：60 个交易日。超过该窗口的存量 PENDING 视为失去回填价值。
_EXPIRE_LOOKBACK_TRADE_DAYS = 60


def upgrade() -> None:
    """Mark stale PENDING/NULL (t1_pct IS NULL) records older than the lookback window as EXPIRED."""
    op.execute(
        f"""
        UPDATE screening_history sh
        SET review_status = 'EXPIRED'
        WHERE (sh.review_status = 'PENDING' OR sh.review_status IS NULL)
          AND sh.t1_pct IS NULL
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


def downgrade() -> None:
    """Restore EXPIRED records to PENDING (reversible data migration)."""
    op.execute(
        """
        UPDATE screening_history
        SET review_status = 'PENDING'
        WHERE review_status = 'EXPIRED'
        """
    )
