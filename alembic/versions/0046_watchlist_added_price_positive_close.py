"""watchlist.added_price 回填仅接受有限正行情 (D06-002)

Revision ID: 0046
Revises: 0045
Create Date: 2026-11-15 00:00:00.000000

D06-002：0044 的「可用行情」判据为 ``close IS NOT NULL AND adj_factor IS NOT NULL
AND adj_factor > 0``，而消费方 ``WatchlistDao._latest_quote`` 要求 close / adj_factor
同为「有限正数」。二者不一致时，同交易日的旧行情 ``close = 0 / -1``（adj = 2）会被
0044 回填成 ``added_price = 0 / -2``——业务上非法的分母，且与「观察起点未知」（NULL）
语义冲突（R21）。0044 已发布，已迁移库不会重跑，故本前向 revision 用同一判据重放
一次归一回填：

1. 同交易日行情不可用（缺行，或 close / adj_factor 为空、非正、NaN、±Infinity）的行
   置 ``added_price = NULL``，``added_trade_date`` 与备注保留——只把「已知错误的值」
   降级为「未知」，不删除观察起点本身。
2. 行情可用的行重算 ``added_price = close × adj_factor``（幂等；顺带修正历史上被写成
   0 / 负值的行）。
3. 原本 ``added_price IS NULL`` 的行不回填，保持「未知」。

本迁移无 schema 变更，只做数据归一。数据纠错不可逆：``downgrade`` 不恢复已知错误值
（恢复 0 / -2 等于重新制造 R21 缺陷），而是重放同一归一回填，保证 upgrade/downgrade
后状态一致（幂等）。
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0046"
down_revision: str | Sequence[str] | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 可用行情判据：与 0044 的 _USABLE_QUOTE_SQL 及 WatchlistDao._latest_quote 同口径。
# PostgreSQL numeric 的 NaN 比较时被视为大于一切值（NaN > 0 为真），Infinity 亦
# 满足 > 0，故须显式排除；-Infinity 由 > 0 排除。
# R4：常量 SQL，无参数拼接；仅 UPDATE watchlist 单表 + daily_quotes 主键等值关联。
_USABLE_QUOTE_SQL = (
    "dq.close IS NOT NULL "
    "AND dq.close > 0 "
    "AND dq.close <> 'NaN'::numeric "
    "AND dq.close <> 'Infinity'::numeric "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0 "
    "AND dq.adj_factor <> 'NaN'::numeric "
    "AND dq.adj_factor <> 'Infinity'::numeric"
)
# 行情不可用的旧值不得保留（0 / -2 / NaN 都是已知错误的观察起点），置 NULL。
# 必须先于重算执行：NOT EXISTS 只筛「无可用行情」的行，避免清空刚重算成功的行。
_NULL_UNUSABLE_SQL = (
    "UPDATE watchlist AS w SET added_price = NULL "
    "WHERE w.added_price IS NOT NULL "
    "AND NOT EXISTS ("
    "SELECT 1 FROM daily_quotes AS dq "
    "WHERE dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND " + _USABLE_QUOTE_SQL + ")"
)
# 行情可用的行按乘积口径重算（幂等；修正被写成 0 / 负值的行）。
_REBASE_TO_PRODUCT_SQL = (
    "UPDATE watchlist AS w "
    "SET added_price = dq.close * dq.adj_factor "
    "FROM daily_quotes AS dq "
    "WHERE w.added_price IS NOT NULL "
    "AND dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND " + _USABLE_QUOTE_SQL
)


def _normalize_added_price() -> None:
    """Repoint added_price at usable quotes; mark the rest unknown (idempotent)."""
    op.execute(_NULL_UNUSABLE_SQL)
    op.execute(_REBASE_TO_PRODUCT_SQL)


def upgrade() -> None:
    """Re-apply the 0044 backfill with the finite-positive quote predicate."""
    _normalize_added_price()


def downgrade() -> None:
    """Replay the same normalization; known-wrong values are never restored (R21)."""
    _normalize_added_price()
