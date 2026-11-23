"""DAO for watchlist (用户关注列表, FR-UX-004, Task 4.2 / RV-05).

支持 add/remove/get/is_in 四个核心操作，以及按 ts_code 读取已有备注
（get_watchlist_note，供重复关注/改备注前的替换影响告知）。upsert by ts_code 保留首次加入日期。
RV-05: add 时固化观察起点（最近交易日 + 复权基准乘积 close × adj_factor），
IS NULL 守卫只写一次。
"""

import datetime
import logging
import math

import pandas as pd
import sqlalchemy as sa

from data.persistence.models import DailyQuotes, Watchlist

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# 仅写入这三列；id (autoincrement) 与 added_at (server_default) 由 DB 生成。
# 冲突时仅更新 stock_name/note，保留原始 id 与 added_at（首次加入日期）。
# RV-05: added_trade_date/added_price 不在此列——经 IS NULL 守卫的独立 UPDATE
# 一次性固化（只读语义），不能进 UPSERT 覆盖集。
_WATCHLIST_COLUMNS: list[str] = ["ts_code", "stock_name", "note"]
_WATCHLIST_PK: list[str] = ["ts_code"]

# RV-05: 观察起点固化 SQL（R4: $N 占位符；IS NULL 守卫 = 只写一次，
# 已固化的起点不被后续重复 add 覆盖）。
_PIN_ORIGIN_SQL = (
    "UPDATE watchlist SET added_trade_date = $2, added_price = $3 WHERE ts_code = $1 AND added_trade_date IS NULL"
)

# R2-I13 / R21：按写入前原备注 CAS（BTRIM 归一化，与 UI 展示口径一致）。
# 影响 0 行且行不存在时方可插入；仍为 0 行即并发变化，拒写且不覆盖原值。
_WATCHLIST_NOTE_CAS_UPDATE_SQL = (
    "UPDATE watchlist SET stock_name = $2, note = $3 WHERE ts_code = $1 AND COALESCE(BTRIM(note), '') = $4"
)
_WATCHLIST_INSERT_IF_ABSENT_SQL = (
    "INSERT INTO watchlist (ts_code, stock_name, note) "
    "SELECT $1, $2, $3 WHERE NOT EXISTS (SELECT 1 FROM watchlist WHERE ts_code = $1)"
)


class WatchlistNoteConflictError(RuntimeError):
    """写入前原备注已变化（并发覆盖）：本次未写入，原备注保持不变（R21 fail-closed）。"""

    def __init__(self, ts_code: str) -> None:
        super().__init__(f"watchlist note changed since read: {ts_code}")
        self.ts_code = ts_code


class WatchlistDao(BaseDao):
    """DAO for watchlist table (用户关注列表)."""

    async def add_to_watchlist(
        self,
        ts_code: str,
        stock_name: str,
        note: str | None = None,
    ) -> int:
        """加入关注（upsert by ts_code）+ 固化观察起点（RV-05）。

        基础三列语义不变：已存在时更新 stock_name/note，保留首次加入日期
        (added_at)。R8: 使用 _save_upsert 而非 _write_db(is_many=True)。

        RV-05: 额外解析观察起点（该股在 daily_quotes 的最新交易日 + 复权基准乘积
        close × adj_factor），经 IS NULL 守卫一次性写入 added_trade_date/added_price。
        乘积口径与 review_manager._qfq_return_pct 一致（复权因子为乘子，见
        utils/qfq.py:qfq_ratio_expr）：消费方按
        ``(close_t × adj_t) / added_price − 1`` 计算复权持仓期收益，分母即观察起点
        乘积，base 因子自然抵消。两步非原子：中断后起点停留 NULL，由下次 add 的
        同守卫自愈；并发 add 时先者的起点胜出（等价）。行情缺失（新上市未同步/
        已退市）时保持 NULL，不伪造（R21）。
        """
        df = pd.DataFrame([{"ts_code": ts_code, "stock_name": stock_name, "note": note}])
        written = await self._save_upsert(
            df,
            "watchlist",
            _WATCHLIST_COLUMNS,
            pk_columns=_WATCHLIST_PK,
        )
        origin = await self._latest_quote(ts_code)
        if origin is not None:
            await self._write_db(_PIN_ORIGIN_SQL, (ts_code, origin[0], origin[1]))
        return written

    async def add_to_watchlist_checked(
        self,
        ts_code: str,
        stock_name: str,
        note: str | None = None,
        *,
        expected_note: str,
    ) -> int:
        """按写入前原备注 CAS 写入关注（R2-I13 / R21：不匹配即拒写）。

        ``expected_note`` 为调用方写入前读到的原备注（未关注 / 无备注传空串）。
        先在 BTRIM 归一化口径下条件 UPDATE；影响 0 行时仅在该股票不存在时
        INSERT，仍为 0 行即视为并发变化 → 抛 ``WatchlistNoteConflictError``，
        本次不写入、原备注保持不变，绝不以成功收尾。
        """

        expected = (expected_note or "").strip()
        async with self._guarded_begin() as conn:
            updated = await conn.exec_driver_sql(
                _WATCHLIST_NOTE_CAS_UPDATE_SQL,
                (ts_code, stock_name, note, expected),
            )
            written = int(updated.rowcount or 0)
            if written == 0:
                inserted = await conn.exec_driver_sql(
                    _WATCHLIST_INSERT_IF_ABSENT_SQL,
                    (ts_code, stock_name, note),
                )
                written = int(inserted.rowcount or 0)
        if written != 1:
            raise WatchlistNoteConflictError(ts_code)
        origin = await self._latest_quote(ts_code)
        if origin is not None:
            await self._write_db(_PIN_ORIGIN_SQL, (ts_code, origin[0], origin[1]))
        return written

    async def _latest_quote(self, ts_code: str) -> tuple[datetime.date, float | None] | None:
        """解析该股最近行情作为观察起点（RV-05）。

        返回 (trade_date, 复权基准乘积)：价格列存 ``close × adj_factor``——复权因子
        是乘子（``utils/qfq.py:qfq_ratio_expr`` 的 ``复权价 = close × adj / base``），
        故乘积之比即复权收益率之比，消费方
        ``(close_t × adj_t) / added_price − 1`` 与 ``review_manager._qfq_return_pct``
        完全同口径。adj_factor 缺失/为 0/非正/非有限时价格为 None（交易日仍有效；
        未复权价与未来复权口径不可比，宁缺毋错，R21）；close 缺失/非正/非有限同样
        不写价格（0 或负值会被下游当作合法分母）。无行情记录时整体返回 None。
        查询走 daily_quotes 主键 (ts_code, trade_date) 前缀 + trade_date desc
        limit 1，单行开销。
        """
        stmt = (
            sa.select(DailyQuotes.trade_date, DailyQuotes.close, DailyQuotes.adj_factor)
            .where(DailyQuotes.ts_code == ts_code)
            .order_by(DailyQuotes.trade_date.desc())
            .limit(1)
        )
        df = await self._read_db_select(stmt)
        if df is None or df.empty:
            return None
        row = df.iloc[0]
        trade_date = row["trade_date"]
        close_raw = row["close"]
        adj_raw = row["adj_factor"]
        price: float | None = None
        if close_raw is not None and bool(pd.notna(close_raw)):
            close = float(close_raw)
            if math.isfinite(close) and close > 0 and adj_raw is not None and bool(pd.notna(adj_raw)):
                adj = float(adj_raw)
                if math.isfinite(adj) and adj > 0:
                    product = close * adj
                    if math.isfinite(product):
                        price = product
        return (trade_date, price)

    async def remove_from_watchlist(self, ts_code: str) -> int:
        """移除关注。

        R4: asyncpg 原生查询用 $1 占位符（非 %s）。
        """
        sql = "DELETE FROM watchlist WHERE ts_code = $1"
        return await self._write_db(sql, (ts_code,))

    async def get_watchlist(self) -> pd.DataFrame:
        """查询全部关注列表（按 added_at desc）。

        ``suppress_errors=False``（R21）：读取失败必须传播，不得伪装为「无关注记录」
        （与 ``StockDao.get_ts_code_map`` 同一口径）——否则 DB 故障在 UI 上表现为
        空列表，用户误判关注数据丢失。
        """
        stmt = sa.select(Watchlist).order_by(Watchlist.added_at.desc())
        return await self._read_db_select(stmt, suppress_errors=False)

    async def is_in_watchlist(self, ts_code: str) -> bool:
        """检查是否已关注。

        ``suppress_errors=False``（R21）：读取失败传播，不伪装为「未关注」。
        """
        stmt = sa.select(Watchlist.ts_code).where(Watchlist.ts_code == ts_code)
        df = await self._read_db_select(stmt, suppress_errors=False)
        return not df.empty

    async def get_watchlist_note(self, ts_code: str) -> str | None:
        """按 ts_code 读取已有关注备注（R2-I13：重复关注/改备注前的影响告知）。

        返回 ``None`` 表示未关注；返回字符串（可能为 ``""``）表示已关注及其备注，
        二者语义不同——空串不是「无记录」，调用方不得据此判断未关注。

        ``suppress_errors=False``（R21）：读取失败必须传播，不得伪装为「无旧备注」，
        否则重复关注会静默覆盖用户原有备注。
        """
        stmt = sa.select(Watchlist.note).where(Watchlist.ts_code == ts_code).limit(1)
        df = await self._read_db_select(stmt, suppress_errors=False)
        if df is None or df.empty:
            return None
        note = df.iloc[0]["note"]
        if note is None or not bool(pd.notna(note)):
            return ""
        return str(note)
