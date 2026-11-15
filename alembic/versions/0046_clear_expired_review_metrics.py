"""clear stale review metrics on EXPIRED screening_history rows (D06-001)

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-15 00:00:00.000000

D06-001：0030（RV-01）/0032（RV-02）把 60 交易日锚点前的旧 ``COMPLETED`` 置
``EXPIRED`` 终态，但只改状态、未清按旧口径算出的 ``alpha`` / ``t1_pct`` /
``t5_pct`` / ``prediction_result``（旧胜负）。这些行仍落在 180 自然日复盘统计窗内，
旧收益与旧胜负因此继续进入 ``get_strategy_review_stats`` /
``get_ai_attribution_stats`` 的 N/均值/胜率。

本迁移为**前向数据修正**（不改已执行的 0030/0032）：清空 ``EXPIRED`` 行的收益
数值与标签，使「EXPIRED 不携带复盘收益」在存量上成立。查询侧同时在 DISTINCT ON
之后按状态掩码（``_trusted_metric``），二者共同保证不可信旧收益不进入统计：

- 清空 ``t1_pct`` / ``t5_pct`` / ``alpha`` / ``prediction_result`` / ``index_pct``；
- 保留 ``t1_price`` / ``t5_price``（行情事实，非口径派生收益）与 ``benchmark_code``
  （分组标识，统计仍按原基准归组当日入选数）；
- 保留 ``params_snapshot`` / ``ai_score`` / ``ai_reason`` / ``thinking``（历史研究记录）。

人工确认：程序未正式发布、无真实历史数据需迁移；本迁移对空库与已清理库均为空操作
（WHERE 附加「任一列非空」守卫，幂等可重跑）。``review_status`` 仅有 PENDING / T1_DONE
的部分索引，本 UPDATE 对 ``screening_history`` 做一次顺序扫描；本地单机数据量可控，
且仅执行一次。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Clear stale return metrics / WIN-LOSS labels carried by EXPIRED rows."""
    op.execute(
        """
        UPDATE screening_history
        SET t1_pct = NULL,
            t5_pct = NULL,
            alpha = NULL,
            prediction_result = NULL,
            index_pct = NULL
        WHERE review_status = 'EXPIRED'
          AND (t1_pct IS NOT NULL
               OR t5_pct IS NOT NULL
               OR alpha IS NOT NULL
               OR prediction_result IS NOT NULL
               OR index_pct IS NOT NULL)
        """
    )


def downgrade() -> None:
    """No-op: 旧值为已判定不可信的旧口径收益，恢复即恢复错误数据（对齐 0030/0032 先例）。

    CI 的 downgrade base → upgrade head 为重建式，不受影响。
    """
    pass
