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
        return await self._save_upsert(df, "stock_basic", cols, pk_columns=['ts_code'])

    async def get_stock_basic(self):
        return await self._read_db("SELECT * FROM stock_basic")

    async def get_active_stock_count(self):
        """Count stocks with list_status='L'"""
        async with self.engine.connect() as conn:
            r = await conn.exec_driver_sql("SELECT count(*) FROM stock_basic WHERE list_status='L'")
            return r.fetchone()[0] or 0

    # --- Trade Calendar ---
    async def save_trade_cal(self, df):
        cols = ['cal_date', 'exchange', 'is_open', 'pretrade_date']
        # Fix is_open type
        df = df.copy()
        if 'is_open' in df.columns:
            df['is_open'] = df['is_open'].astype(int)
        
        return await self._save_upsert(df, "trade_cal", cols, pk_columns=['cal_date'])

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

    # --- Concepts ---
    async def save_concepts(self, df):
        if df is None or df.empty: return 0
        cols = ['ts_code', 'concept_name', 'concept_id', 'updated_at']
        
        df = df.copy()
        df['updated_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        return await self._save_upsert(df, "stock_concepts", cols, pk_columns=['ts_code', 'concept_id'])

    async def overwrite_concepts(self, df):
        """
        Transactional overwrite of concepts.
        Clears table and inserts new data in a single transaction.
        """
        if df is None or df.empty: return 0
        
        cols = ['ts_code', 'concept_name', 'concept_id', 'updated_at']
        df = df.copy()
        df['updated_at'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Prepare params outside transaction
        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, cols)
        
        col_str = self._quote_columns(cols)
        sql_insert = f"INSERT INTO stock_concepts ({col_str}) VALUES ({','.join(['?']*len(cols))})"

        try:
            async with self.engine.begin() as conn:
                # 1. Clear old data
                await conn.exec_driver_sql("DELETE FROM stock_concepts")
                
                # 2. Insert new data
                if params:
                    await conn.exec_driver_sql(sql_insert, params)
                    
            return len(params)
        except Exception as e:
            logger.error(f"[StockDao] overwrite_concepts failed: {e}")
            raise e

    async def clear_concepts(self):
        """
        Clear all concept data.
        NOTE: Prefer overwrite_concepts for full refresh to ensure atomicity.
        """
        async with self.engine.begin() as conn:
            await conn.exec_driver_sql("DELETE FROM stock_concepts")

    async def get_concepts(self, ts_codes: list = None):
        """
        Get concepts for given stock codes.
        Returns: Dict[ts_code, List[concept_name]]
        """
        sql = "SELECT ts_code, concept_name FROM stock_concepts"
        params = []
        
        if ts_codes:
            # Handle large list of codes
            if len(ts_codes) == 1:
                sql += " WHERE ts_code=?"
                params.append(ts_codes[0])
            else:
                placeholders = ",".join(["?"] * len(ts_codes))
                sql += f" WHERE ts_code IN ({placeholders})"
                params.extend(ts_codes)
                
        rows = await self._read_db(sql, params)
        
        # Transform to dict
        result = {}
        # rows is a DataFrame
        if rows is None or rows.empty:
            return result
            
        for _, r in rows.iterrows():
            code = r['ts_code']
            concept = r['concept_name']
            if code not in result:
                result[code] = []
            result[code].append(concept)
            
        return result
