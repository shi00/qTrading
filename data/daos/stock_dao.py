import datetime
import logging
from .base_dao import BaseDao
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

class StockDao(BaseDao):
    
    # --- Stock Basic ---
    async def save_stock_basic(self, df, priority=None):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'symbol', 'name', 'area', 'industry', 'market', 'list_date', 'list_status', 'updated_at']

        df = df.copy()
        df['updated_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return await self._save_standard(df, "stock_basic", cols)

    async def get_stock_basic(self):
        return await self._read_db("SELECT * FROM stock_basic")

    # --- Trade Calendar ---
    async def save_trade_cal(self, df):
        cols = ['cal_date', 'exchange', 'is_open', 'pretrade_date']
        # Fix is_open type
        df = df.copy()
        if 'is_open' in df.columns:
            df['is_open'] = df['is_open'].astype(int)
        
        return await self._save_standard(df, "trade_cal", cols)

    async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
        sql = "SELECT * FROM trade_cal WHERE 1=1"
        p = []
        if start_date: sql += " AND cal_date>=?"; p.append(start_date)
        if end_date: sql += " AND cal_date<=?"; p.append(end_date)
        if is_open is not None: sql += " AND is_open=?"; p.append(is_open)
        sql += " ORDER BY cal_date ASC"
        return await self._read_db(sql, p)

    async def get_trade_cal_range(self):
        """Get the min and max calendar dates from DB"""
        async with self.engine.connect() as conn:
            r = await conn.exec_driver_sql("SELECT MIN(cal_date), MAX(cal_date) FROM trade_cal")
            row = r.fetchone()
            return (row[0], row[1]) if row else (None, None)
