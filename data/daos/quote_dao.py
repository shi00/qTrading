import logging

import pandas as pd

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class QuoteDao(BaseDao):

    # --- Daily Quotes ---
    async def save_daily_quotes(self, df, priority=None, suppress_errors=True):
        cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'change', 'pct_chg', 'vol',
                'amount', 'adj_factor']
        return await self._save_upsert(df, "daily_quotes", cols, pk_columns=['ts_code', 'trade_date'],
                                       suppress_errors=suppress_errors)

    async def check_data_exists(self, trade_date: str) -> bool:
        try:
            async with self.engine.connect() as conn:
                res = await conn.exec_driver_sql("SELECT 1 FROM daily_quotes WHERE trade_date=? LIMIT 1", (trade_date,))
                return res.fetchone() is not None
        except Exception:
            return False

    async def get_daily_quotes(self, ts_code=None, start_date=None, end_date=None, ts_code_list=None):
        sql = "SELECT ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount, adj_factor FROM daily_quotes WHERE 1=1"
        params = []

        if ts_code:
            sql += " AND ts_code = ?"
            params.append(ts_code)
        if start_date:
            sql += " AND trade_date >= ?"
            params.append(start_date)
        if end_date:
            sql += " AND trade_date <= ?"
            params.append(end_date)

        if ts_code_list:
            # Split into chunks of 500 to be safe (SQLite default limit is 999 variables usually)
            chunk_size = 500
            if len(ts_code_list) > chunk_size:
                logger.debug(f"[QuoteDao] Chunking query for {len(ts_code_list)} codes")
                all_results = []
                # Remove ts_code_list from params for safe recursion or loop
                # Actually, easier to loop here.

                # Base SQL without the IN clause
                base_sql = sql
                base_params = params.copy()

                for i in range(0, len(ts_code_list), chunk_size):
                    chunk = ts_code_list[i: i + chunk_size]
                    placeholders = ','.join(['?'] * len(chunk))
                    chunk_sql = base_sql + f" AND ts_code IN ({placeholders})"
                    chunk_params = base_params + chunk

                    df_chunk = await self._read_db(chunk_sql, chunk_params)
                    if not df_chunk.empty:
                        all_results.append(df_chunk)

                if all_results:
                    return pd.concat(all_results, ignore_index=True)
                else:
                    return pd.DataFrame()
            else:
                placeholders = ','.join(['?'] * len(ts_code_list))
                sql += f" AND ts_code IN ({placeholders})"
                params.extend(ts_code_list)

        return await self._read_db(sql, params)

    async def get_latest_trade_date(self):
        async with self.engine.connect() as conn:
            res = await conn.exec_driver_sql("SELECT MAX(trade_date) FROM daily_quotes")
            row = res.fetchone()
            return row[0] if row else None

    async def get_cached_trade_dates(self):
        async with self.engine.connect() as conn:
            res = await conn.exec_driver_sql("SELECT DISTINCT trade_date FROM daily_quotes ORDER BY trade_date")
            rows = res.fetchall()
            return set(row[0] for row in rows)

    # --- Index Data ---
    async def save_index_daily(self, df):
        cols = ['ts_code', 'trade_date', 'close', 'open', 'high', 'low', 'pre_close', 'change', 'pct_chg', 'vol',
                'amount']
        return await self._save_upsert(df, "index_daily", cols, pk_columns=['ts_code', 'trade_date'])

    async def save_index_dailybasic(self, df):
        cols = ['ts_code', 'trade_date', 'total_mv', 'float_mv', 'total_share', 'float_share', 'free_share',
                'turnover_rate', 'turnover_rate_f', 'pe', 'pe_ttm', 'pb']
        return await self._save_upsert(df, "index_dailybasic", cols, pk_columns=['ts_code', 'trade_date'])

    async def get_index_daily(self, ts_code=None, trade_date=None):
        sql = "SELECT * FROM index_daily WHERE 1=1"
        p = []
        if ts_code:
            sql += " AND ts_code=?"
            p.append(ts_code)
        if trade_date:
            sql += " AND trade_date=?"
            p.append(trade_date)
        sql += " ORDER BY trade_date DESC"
        return await self._read_db(sql, p)

    # --- Block Trade ---
    async def save_block_trade(self, df):
        cols = ['trade_date', 'ts_code', 'price', 'volume', 'amount', 'buyer', 'seller']
        if df is not None and 'vol' in df.columns:
            df = df.rename(columns={'vol': 'volume'})
        return await self._save_upsert(df, "block_trade", cols, pk_columns=['ts_code', 'trade_date', 'buyer', 'seller'])

    async def get_block_trade(self, trade_date=None):
        sql = "SELECT * FROM block_trade WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=?"
            p.append(trade_date)
        return await self._read_db(sql, p)

    # --- Limit List ---
    async def save_limit_list(self, df):
        cols = ['trade_date', 'ts_code', 'name', 'close', 'pct_chg', 'amp', 'fc_ratio', 'fl_ratio', 'fd_amount',
                'first_time', 'last_time', 'open_times', 'strth', 'limit_type']
        return await self._save_upsert(df, "limit_list", cols, pk_columns=['trade_date', 'ts_code'])

    # --- Top List ---
    async def save_top_list(self, df):
        cols = ['trade_date', 'ts_code', 'name', 'close', 'pct_chg', 'turnover_rate',
                'amount', 'l_sell', 'l_buy', 'l_amount', 'net_amount',
                'net_rate', 'amount_rate', 'float_values', 'reason']
        return await self._save_upsert(df, "top_list", cols, pk_columns=['trade_date', 'ts_code'])

    async def get_top_list(self, trade_date=None):
        sql = "SELECT * FROM top_list WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=?"
            p.append(trade_date)
        return await self._read_db(sql, p)

    # --- Margin ---
    async def save_margin_daily(self, df):
        cols = ['ts_code', 'trade_date', 'rzye', 'rqye', 'rzmre', 'rqyl', 'rzrqye']
        return await self._save_upsert(df, "margin_daily", cols, pk_columns=['ts_code', 'trade_date'])

    # --- Suspend ---
    async def save_suspend_d(self, df):
        cols = ['ts_code', 'trade_date', 'suspend_timing', 'suspend_type_name']
        return await self._save_upsert(df, "suspend_d", cols, pk_columns=['ts_code', 'trade_date'])

    # --- Moneyflow ---
    async def save_moneyflow(self, df):
        cols = ['ts_code', 'trade_date', 'buy_sm_vol', 'buy_sm_amount', 'sell_sm_amount', 'buy_md_amount',
                'sell_md_amount', 'buy_lg_amount', 'sell_lg_amount', 'buy_elg_amount', 'sell_elg_amount', 'net_mf_vol',
                'net_mf_amount']
        return await self._save_upsert(df, "moneyflow_daily", cols, pk_columns=['ts_code', 'trade_date'])

    async def get_moneyflow(self, trade_date=None, ts_code=None):
        sql = "SELECT * FROM moneyflow_daily WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=?"
            p.append(trade_date)
        if ts_code:
            sql += " AND ts_code=?"
            p.append(ts_code)
        return await self._read_db(sql, p)

    async def save_moneyflow_hsgt(self, df):
        cols = ['trade_date', 'ggt_ss', 'ggt_sz', 'hgt', 'sgt', 'north_money', 'south_money']
        return await self._save_upsert(df, "moneyflow_hsgt", cols, pk_columns=['trade_date'])

    # --- Northbound ---
    async def save_northbound(self, df):
        cols = ['ts_code', 'trade_date', 'name', 'vol', 'ratio', 'exchange']
        return await self._save_upsert(df, "northbound_holding", cols, pk_columns=['ts_code', 'trade_date'])

    async def get_northbound(self, trade_date=None, ts_code=None):
        sql = "SELECT * FROM northbound_holding WHERE 1=1"
        p = []
        if trade_date:
            sql += " AND trade_date=?"
            p.append(trade_date)
        if ts_code:
            sql += " AND ts_code=?"
            p.append(ts_code)
        return await self._read_db(sql, p)

    async def get_latest_northbound(self):
        async with self.engine.connect() as conn:
            r = await conn.exec_driver_sql("SELECT MAX(trade_date) FROM northbound_holding")
            row = r.fetchone()
            td = row[0] if row else None
            if not td:
                return pd.DataFrame()
        return await self.get_northbound(trade_date=td)
