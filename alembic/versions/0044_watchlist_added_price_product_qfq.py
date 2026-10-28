"""watchlist.added_price 改为复权乘积口径并宽化精度 (UIR-F09-005)

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-28 00:00:00.000000

UIR-F09-005：``added_price`` 原写入 ``close / adj_factor``（倒数口径），与项目复权
正本 ``utils/qfq.py:qfq_ratio_expr``（``复权价 = close × adj_factor / base``，因子为
乘子）及 ``data/persistence/review_manager.py:_qfq_return_pct`` 的乘积口径
``(close_tN × adj_tN) / (close_t1 × adj_t1) − 1`` 方向相反。本迁移把语义改为
``close × adj_factor``（复权基准乘积）：消费方按
``(close_t × adj_t) / added_price − 1`` 计算「关注以来收益」，与 ``_qfq_return_pct``
完全同口径，0031 中「close/adj_factor，与 _qfq_return_pct 同口径」的描述随之废止。

两步：
1. ``added_price`` 由 ``Numeric(12, 6)``（上限 999999.999999）宽化为
   ``Numeric(24, 6)``：乘积量级约为复权基准日价格水平（现实 ≤ 1e4，原宽度可控），
   但 ``daily_quotes.adj_factor`` 为 ``Numeric(20, 12)``，脏因子可使乘积超过原上限
   触发 numeric overflow，导致 add_to_watchlist 整条写入失败；24 位可容纳 schema
   允许的最大乘积（close N(12,4) ≤ 1e8 × adj N(20,12) ≤ 1e8）并留余量。
2. 存量旧值按新口径重算：先置空「同交易日 daily_quotes 不可用（缺行/close 或
   adj_factor 无效）」的旧值（R21：宁可「观察起点未知」，不保留已知错误语义的
   倒数乘积），再把其余旧值改写为 ``close × adj_factor``。``added_price`` 目前无
   读取方（只写不读），无消费方回归面。

downgrade 对称：同条件反算回 ``close / adj_factor``，其余置 NULL，再窄化回
``Numeric(12, 6)``（反算值为价格量级，不会溢出）。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0044"
down_revision: str | Sequence[str] | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 旧语义（close / adj_factor）→ 新语义（close × adj_factor）：以同交易日行情重算，
# 不依赖旧值本身（旧值可被反推但乘法重算更直接、且不受旧值精度影响）。
# R4：常量 SQL，无参数拼接；仅 UPDATE watchlist 单表 + daily_quotes 主键等值关联。
_REBASE_TO_PRODUCT_SQL = (
    "UPDATE watchlist AS w "
    "SET added_price = dq.close * dq.adj_factor "
    "FROM daily_quotes AS dq "
    "WHERE w.added_price IS NOT NULL "
    "AND dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND dq.close IS NOT NULL "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0"
)
# 新语义（close × adj_factor）→ 旧语义（close / adj_factor），downgrade 用。
_REBASE_TO_QUOTIENT_SQL = (
    "UPDATE watchlist AS w "
    "SET added_price = dq.close / dq.adj_factor "
    "FROM daily_quotes AS dq "
    "WHERE w.added_price IS NOT NULL "
    "AND dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND dq.close IS NOT NULL "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0"
)
# 无法重算的旧值不得保留（混语义即误导），置 NULL 表示「观察起点价格未知」。
# 必须在重算之前执行：NOT EXISTS 判定与值本身无关，只筛「同交易日行情不可用」的行，
# 避免把刚重算成功的行一并清空。
_NULL_UNREBASABLE_SQL = (
    "UPDATE watchlist AS w SET added_price = NULL "
    "WHERE w.added_price IS NOT NULL "
    "AND NOT EXISTS ("
    "SELECT 1 FROM daily_quotes AS dq "
    "WHERE dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND dq.close IS NOT NULL "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0)"
)


def upgrade() -> None:
    """Widen added_price and rewrite legacy reciprocal values as close × adj_factor."""
    op.alter_column(
        "watchlist",
        "added_price",
        type_=sa.Numeric(24, 6),
        existing_type=sa.Numeric(12, 6),
        existing_nullable=True,
    )
    op.execute(_NULL_UNREBASABLE_SQL)
    op.execute(_REBASE_TO_PRODUCT_SQL)


def downgrade() -> None:
    """Rewrite values back to close / adj_factor and narrow added_price."""
    op.execute(_NULL_UNREBASABLE_SQL)
    op.execute(_REBASE_TO_QUOTIENT_SQL)
    op.alter_column(
        "watchlist",
        "added_price",
        type_=sa.Numeric(12, 6),
        existing_type=sa.Numeric(24, 6),
        existing_nullable=True,
    )
