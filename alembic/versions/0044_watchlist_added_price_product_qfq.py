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

downgrade 对称但可能不可逆：反算回 ``close / adj_factor`` 后若超出旧列
``Numeric(12, 6)`` 上限（999999.999999，``adj_factor`` 过小即可超限），旧 schema
无法承载。默认 fail-closed：先预检，存在此类行即抛中文异常且零写入。确认可接受
数据丢失并已备份 ``watchlist`` 后，可显式授权有损回滚
``alembic -x allow_lossy_rollback=1 downgrade <目标版本>``：仅把不可表示的行
``added_price`` 置 NULL（``added_trade_date`` 保留，表示「关注起点价格未知」），
其余行正常反算，再窄化回 ``Numeric(12, 6)``。有损路径仅删除「观测起点价格」，
不回填、不伪造，需在备份后执行。
"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

logger = logging.getLogger(__name__)

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
# 反算值能否装入旧列 Numeric(12, 6)（上限 999999.999999）的判据：三处
# abs(round(close / adj_factor, 6)) 比较必须逐字一致（_UNREPRESENTABLE_COUNT_SQL /
# _NULL_UNREPRESENTABLE_SQL / _REBASE_TO_QUOTIENT_SQL 护栏）；可用行情条件在
# _NULL_UNREBASABLE_SQL 等语句中亦须与 JOIN 条件一致。
# 先 round 到 6 位小数再比较，覆盖 PostgreSQL 赋值时的 6 位舍入（舍入进位的
# 999999.9999995 被 round 为 1000000.000000，判为不可表示）。
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
    "AND dq.adj_factor > 0 "
    "AND abs(round(dq.close / dq.adj_factor, 6)) < 1000000"
)
# 预检：反算会超出旧列宽的行数（只读，downgrade 最先执行，未做任何写入）。
_UNREPRESENTABLE_COUNT_SQL = (
    "SELECT count(*) FROM watchlist AS w "
    "JOIN daily_quotes AS dq "
    "ON dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "WHERE w.added_price IS NOT NULL "
    "AND dq.close IS NOT NULL "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0 "
    "AND abs(round(dq.close / dq.adj_factor, 6)) >= 1000000"
)
# 有损路径：仅显式授权后执行，把不可表示的行置 NULL（added_trade_date 保留）。
# 与 _NULL_UNREBASABLE_SQL 互斥（可用行情 vs 无可用行情），二者都在重算前执行。
_NULL_UNREPRESENTABLE_SQL = (
    "UPDATE watchlist AS w SET added_price = NULL "
    "WHERE w.added_price IS NOT NULL "
    "AND EXISTS ("
    "SELECT 1 FROM daily_quotes AS dq "
    "WHERE dq.ts_code = w.ts_code "
    "AND dq.trade_date = w.added_trade_date "
    "AND dq.close IS NOT NULL "
    "AND dq.adj_factor IS NOT NULL "
    "AND dq.adj_factor > 0 "
    "AND abs(round(dq.close / dq.adj_factor, 6)) >= 1000000)"
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


# 显式有损回滚的授权值，大小写与空白不敏感；其余值（含 0/false）一律视为未授权。
_TRUTHY_X_VALUES = frozenset({"1", "true", "yes", "on"})


def _is_lossy_rollback_allowed(x_args: dict[str, str]) -> bool:
    """Return True only when the caller explicitly opted into lossy rollback."""
    return x_args.get("allow_lossy_rollback", "").strip().lower() in _TRUTHY_X_VALUES


def downgrade() -> None:
    """Rewrite values back to close / adj_factor and narrow added_price (fail-closed)."""
    migration_context = op.get_context()
    if migration_context.as_sql:
        raise RuntimeError(
            "0044 downgrade 需要在线执行：必须先连接数据库预检"
            "「反算值是否超出 Numeric(12, 6)」，离线 --sql 模式无法预检，拒绝生成 SQL。"
        )
    config = migration_context.config
    cmd_opts = config.cmd_opts if config is not None else None
    raw_x_args = getattr(cmd_opts, "x", None) or []
    x_args = {key.strip(): value.strip() for key, _, value in (item.partition("=") for item in raw_x_args)}

    count = op.get_bind().execute(sa.text(_UNREPRESENTABLE_COUNT_SQL)).scalar_one()
    if count > 0 and not _is_lossy_rollback_allowed(x_args):
        raise RuntimeError(
            f"0044 downgrade 检测到 {count} 行 added_price 反算为 close/adj_factor 后"
            "超出 Numeric(12, 6) 可表示范围（|round(close/adj_factor, 6)| >= 1000000）。"
            "默认拒绝执行（fail-closed），尚未写入任何数据。"
            "如需继续，请先备份 watchlist 表，再显式授权有损回滚："
            "alembic -x allow_lossy_rollback=1 downgrade <目标版本>；"
            f"授权后这 {count} 行的 added_price 将置 NULL（added_trade_date 保留）。"
        )
    if count > 0:
        logger.warning(
            "0044 downgrade 有损回滚：%d 行 added_price 反算结果超出 Numeric(12, 6) 范围，"
            "已置 NULL（added_trade_date 保留）。",
            count,
        )
        op.execute(_NULL_UNREPRESENTABLE_SQL)
    op.execute(_NULL_UNREBASABLE_SQL)
    op.execute(_REBASE_TO_QUOTIENT_SQL)
    op.alter_column(
        "watchlist",
        "added_price",
        type_=sa.Numeric(12, 6),
        existing_type=sa.Numeric(24, 6),
        existing_nullable=True,
    )
