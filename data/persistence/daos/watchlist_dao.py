"""DAO for watchlist (用户关注列表, FR-UX-004, Task 4.2).

支持 add/remove/get/is_in 四个核心操作。upsert by ts_code 保留首次加入日期。
"""

import logging

import pandas as pd
import sqlalchemy as sa

from data.persistence.models import Watchlist

from .base_dao import BaseDao

logger = logging.getLogger(__name__)

# 仅写入这三列；id (autoincrement) 与 added_at (server_default) 由 DB 生成。
# 冲突时仅更新 stock_name/note，保留原始 id 与 added_at（首次加入日期）。
_WATCHLIST_COLUMNS: list[str] = ["ts_code", "stock_name", "note"]
_WATCHLIST_PK: list[str] = ["ts_code"]


class WatchlistDao(BaseDao):
    """DAO for watchlist table (用户关注列表)."""

    async def add_to_watchlist(
        self,
        ts_code: str,
        stock_name: str,
        note: str | None = None,
    ) -> int:
        """加入关注（upsert by ts_code）。

        已存在时更新 stock_name/note，保留首次加入日期 (added_at)。
        R8: 使用 _save_upsert 而非 _write_db(is_many=True)。
        """
        df = pd.DataFrame([{"ts_code": ts_code, "stock_name": stock_name, "note": note}])
        return await self._save_upsert(
            df,
            "watchlist",
            _WATCHLIST_COLUMNS,
            pk_columns=_WATCHLIST_PK,
        )

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
