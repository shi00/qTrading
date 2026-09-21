"""DAO for watchlist (用户关注列表, FR-UX-004, Task 4.2 / RV-05).

支持 add/remove/get/is_in 四个核心操作。upsert by ts_code 保留首次加入日期。
RV-05: add 时固化观察起点（最近交易日 + 复权收盘价），IS NULL 守卫只写一次。
"""

import datetime
import logging

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

        RV-05: 额外解析观察起点（该股在 daily_quotes 的最新交易日 + 复权收盘价
        close/adj_factor，与 review_manager._qfq_return_pct 同口径），经 IS NULL
        守卫一次性写入 added_trade_date/added_price。两步非原子：中断后起点
        停留 NULL，由下次 add 的同守卫自愈；并发 add 时先者的起点胜出（等价）。
        行情缺失（新上市未同步/已退市）时保持 NULL，不伪造（R21）。
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

    async def _latest_quote(self, ts_code: str) -> tuple[datetime.date, float | None] | None:
        """解析该股最近行情作为观察起点（RV-05）。

        返回 (trade_date, 复权收盘价)：adj_factor 缺失/为 0 时价格为 None
        （交易日仍有效；未复权价与未来复权口径不可比，宁缺毋错）；无行情
        记录时整体返回 None。查询走 daily_quotes 主键 (ts_code, trade_date)
        前缀 + trade_date desc limit 1，单行开销。
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
            if adj_raw is not None and bool(pd.notna(adj_raw)):
                adj = float(adj_raw)
                if adj != 0:
                    price = close / adj
        return (trade_date, price)

    async def remove_from_watchlist(self, ts_code: str) -> int:
        """移除关注。

        R4: asyncpg 原生查询用 $1 占位符（非 %s）。
        """
        sql = "DELETE FROM watchlist WHERE ts_code = $1"
        return await self._write_db(sql, (ts_code,))

    async def get_watchlist(self) -> pd.DataFrame:
        """查询全部关注列表（按 added_at desc）。"""
        stmt = sa.select(Watchlist).order_by(Watchlist.added_at.desc())
        return await self._read_db_select(stmt)

    async def is_in_watchlist(self, ts_code: str) -> bool:
        """检查是否已关注。"""
        stmt = sa.select(Watchlist.ts_code).where(Watchlist.ts_code == ts_code)
        df = await self._read_db_select(stmt)
        return not df.empty
