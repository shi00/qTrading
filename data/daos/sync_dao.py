import datetime
import logging
from .base_dao import BaseDao
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

class SyncDao(BaseDao):

    # --- Sync Stats ---
    async def update_sync_status(self, table_name, last_data_date, record_count, status='success'):
        now = get_now().strftime('%Y-%m-%d %H:%M:%S')
        sql = '''INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","updated_at") 
               VALUES ($1, $2, $3, $4, $5, $6) 
               ON CONFLICT("table_name") DO UPDATE SET 
               "last_sync_date"=excluded."last_sync_date","last_data_date"=excluded."last_data_date", 
               "record_count"=excluded."record_count","status"=excluded."status","updated_at"=excluded."updated_at"'''
        await self._write_db(sql, (table_name, now, last_data_date, record_count, status, now))

    async def get_sync_status(self, table_name=None):
        if table_name:
            df = await self._read_db("SELECT * FROM sync_status WHERE table_name = $1", (table_name,))
            if df is not None and not df.empty:
                return df.iloc[0].to_dict()
            return None
        else:
            return await self._read_db("SELECT * FROM sync_status")

    # --- Step 4 Status ---
    async def get_completed_step4_stocks(self, sync_version=1):
        try:
            df = await self._read_db("SELECT ts_code FROM stock_sync_status WHERE sync_version >= $1",
                                     (sync_version,))
            if df is not None and not df.empty:
                return set(df['ts_code'])
            return set()
        except Exception:
            return set()

    async def mark_stock_step4_completed(self, ts_code, sync_version=1):
        now = get_now().strftime('%Y-%m-%d %H:%M:%S')
        sql = '''INSERT INTO stock_sync_status ("ts_code","step4_completed_at","sync_version") 
               VALUES ($1, $2, $3) 
               ON CONFLICT("ts_code") DO UPDATE SET 
               "step4_completed_at"=excluded."step4_completed_at","sync_version"=excluded."sync_version"'''
        await self._write_db(sql, [(ts_code, now, sync_version)], is_many=True)

    async def clear_step4_sync_status(self):
        await self._write_db("DELETE FROM stock_sync_status")
