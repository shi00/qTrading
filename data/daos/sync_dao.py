import datetime
import logging
from .base_dao import BaseDao

logger = logging.getLogger(__name__)

class SyncDao(BaseDao):

    # --- Sync Stats ---
    async def update_sync_status(self, table_name, last_data_date, record_count, status='success'):
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sql = "INSERT OR REPLACE INTO sync_status (table_name, last_sync_date, last_data_date, record_count, status, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
        await self._write_db(sql, (table_name, now, last_data_date, record_count, status, now))

    async def get_sync_status(self, table_name=None):
        if table_name:
            async with self.engine.connect() as conn:
                r = await conn.exec_driver_sql("SELECT * FROM sync_status WHERE table_name = ?", (table_name,))
                row = r.fetchone()
                if row:
                    return dict(zip(list(r.keys()), row))
                return None
        else:
            return await self._read_db("SELECT * FROM sync_status")

    # --- Step 4 Status ---
    async def get_completed_step4_stocks(self, sync_version=1):
        async with self.engine.connect() as conn:
            try:
                r = await conn.exec_driver_sql("SELECT ts_code FROM stock_sync_status WHERE sync_version >= ?",
                                               (sync_version,))
                return set(row[0] for row in r.fetchall())
            except Exception:
                return set()

    async def mark_stock_step4_completed(self, ts_code, sync_version=1):
        now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        sql = "INSERT OR REPLACE INTO stock_sync_status (ts_code, step4_completed_at, sync_version) VALUES (?, ?, ?)"
        await self._write_db(sql, [(ts_code, now, sync_version)], is_many=True)

    async def clear_step4_sync_status(self):
        await self._write_db("DELETE FROM stock_sync_status")
