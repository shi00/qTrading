import hashlib
import logging

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class MarketDao(BaseDao):
    """DAO for Market News, Adjustment Factors, Index Weights, and HSGT Money Flow."""

    # --- Market News ---
    async def save_market_news(self, news_item, wait=False):
        """Save a single market news item."""
        content = news_item.get("content", "") or ""
        content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

        sql = """
              INSERT INTO market_news ("content","content_hash","tags","publish_time","source","created_at")
              VALUES ($1, $2, $3, $4, $5, CURRENT_TIMESTAMP)
              ON CONFLICT("content_hash","publish_time") DO
              UPDATE SET "tags" = COALESCE(excluded."tags", market_news."tags")
              """
        params = (
            content,
            content_hash,
            news_item.get("tags"),
            news_item.get("publish_time"),
            news_item.get("source", "Sina"),
        )
        return await self._write_db(sql, params, is_many=False)

    async def get_market_news(self, limit=50, offset=0, min_publish_time=None):
        sql = "SELECT * FROM market_news WHERE 1=1"
        params = []
        idx = 1
        if min_publish_time:
            sql += f" AND publish_time >= ${idx}"
            params.append(min_publish_time)
            idx += 1
        sql += f" ORDER BY publish_time DESC LIMIT ${idx} OFFSET ${idx + 1}"
        params.extend([limit, offset])
        return await self._read_db(sql, params)

    # --- Daily Indicators ---
    async def save_daily_indicators(self, df, suppress_errors=True):
        """
        Save Daily Indicators (PE, PB, etc.). Table: daily_indicators
        :param suppress_errors: If True (default), log errors but returns 0. If False, raises Exception.
        """
        if df is None or df.empty:
            return 0
        columns = [
            "ts_code",
            "trade_date",
            "pe",
            "pe_ttm",
            "pb",
            "ps",
            "ps_ttm",
            "dv_ratio",
            "dv_ttm",
            "total_mv",
            "circ_mv",
            "total_share",
            "float_share",
            "free_share",
            "turnover_rate",
            "turnover_rate_f",
            "volume_ratio",
        ]

        return await self._save_upsert(
            df,
            "daily_indicators",
            columns,
            pk_columns=["ts_code", "trade_date"],
            suppress_errors=suppress_errors,
        )

    async def get_daily_indicators(
        self, ts_code=None, start_date=None, end_date=None, limit=None,
    ):
        """Get Daily Indicators."""
        sql = "SELECT * FROM daily_indicators WHERE 1=1"
        params = []
        idx = 1
        if ts_code:
            sql += f" AND ts_code = ${idx}"
            params.append(ts_code)
            idx += 1
        if start_date:
            sql += f" AND trade_date >= ${idx}"
            params.append(start_date)
            idx += 1
        if end_date:
            sql += f" AND trade_date <= ${idx}"
            params.append(end_date)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += f" LIMIT ${idx}"
            params.append(limit)

        return await self._read_db(sql, params)

    # --- Index Weights ---
    async def save_index_weights(self, df):
        """Save Index Component Weights. Table: index_weight"""
        if df is None or df.empty:
            return 0
        columns = ["index_code", "con_code", "trade_date", "weight"]
        return await self._save_upsert(
            df,
            "index_weight",
            columns,
            pk_columns=["index_code", "con_code", "trade_date"],
        )

    async def get_index_weights(self, index_code, trade_date):
        sql = "SELECT * FROM index_weight WHERE index_code = $1 AND trade_date = $2"
        return await self._read_db(sql, (index_code, trade_date))

    async def get_latest_index_weight_date(self):
        """Get latest trade_date in index_weight."""
        df = await self._read_db("SELECT MAX(trade_date) as max_date FROM index_weight")
        if df is not None and not df.empty and df.iloc[0]["max_date"]:
            return df.iloc[0]["max_date"]
        return None

    # --- Northbound Moneyflow ---
    async def save_moneyflow_hsgt(self, df):
        """Save Northbound (HSGT) Moneyflow. Table: moneyflow_hsgt"""
        if df is None or df.empty:
            return 0
        columns = [
            "trade_date",
            "ggt_ss",
            "ggt_sz",
            "hgt",
            "sgt",
            "north_money",
            "south_money",
        ]

        # Tushare returns moneyflow_hsgt numeric fields as strings!
        # Postgres asyncpg is strictly typed (FLOAT). We must forcibly coerce them.
        import pandas as pd

        for col in columns[1:]:  # skip trade_date
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        return await self._save_upsert(
            df, "moneyflow_hsgt", columns, pk_columns=["trade_date"],
        )

    async def get_moneyflow_hsgt(self, trade_date=None, limit=None):
        """Get Northbound Money Flow."""
        sql = "SELECT * FROM moneyflow_hsgt WHERE 1=1"
        params = []
        idx = 1
        if trade_date:
            sql += f" AND trade_date = ${idx}"
            params.append(trade_date)
            idx += 1

        sql += " ORDER BY trade_date DESC"
        if limit:
            sql += f" LIMIT ${idx}"
            params.append(limit)

        return await self._read_db(sql, params)
