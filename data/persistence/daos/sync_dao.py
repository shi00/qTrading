import logging
from datetime import date, datetime

import pandas as pd

from data.constants import (
    SYNC_RESULT_EMPTY,
    SYNC_RESULT_FETCH_FAILED,
    SYNC_RESULT_HAS_DATA,
    SYNC_RESULT_SAVE_FAILED,
)
from utils.time_utils import get_now, parse_date

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class SyncDao(BaseDao):
    async def update_sync_status(
        self,
        table_name: str,
        last_data_date: str | datetime | date,
        record_count: int,
        status: str = "success",
        last_result_status: str | None = None,
    ):
        if isinstance(last_data_date, str):
            parsed_date = parse_date(last_data_date).date()
        elif isinstance(last_data_date, datetime):
            parsed_date = last_data_date.date()
        elif isinstance(last_data_date, date):
            parsed_date = last_data_date
        else:
            raise TypeError(f"last_data_date must be str, datetime, or date, got {type(last_data_date)}")

        if last_result_status is None:
            if status in ("success", "empty") and record_count > 0:
                last_result_status = SYNC_RESULT_HAS_DATA
            elif status in ("success", "empty") and record_count == 0:
                last_result_status = SYNC_RESULT_EMPTY
            elif status == "save_failed":
                last_result_status = SYNC_RESULT_SAVE_FAILED
            else:
                last_result_status = SYNC_RESULT_FETCH_FAILED

        now = get_now().replace(tzinfo=None)
        sql = '''INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","last_result_status","updated_at")
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT("table_name") DO UPDATE SET
               "last_sync_date"=excluded."last_sync_date",
               "last_data_date"=CASE WHEN excluded."status" IN ('success','empty') AND excluded."last_data_date" > COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                                     THEN excluded."last_data_date"
                                     ELSE sync_status."last_data_date" END,
               "record_count"=CASE WHEN excluded."status" IN ('success','empty') AND excluded."last_data_date" >= COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                                   THEN excluded."record_count"
                                   ELSE sync_status."record_count" END,
               "status"=CASE WHEN excluded."last_data_date" >= COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                             THEN excluded."status"
                             ELSE sync_status."status" END,
               "last_result_status"=CASE WHEN excluded."last_data_date" >= COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                                         THEN excluded."last_result_status"
                                         ELSE sync_status."last_result_status" END,
               "updated_at"=excluded."updated_at"'''
        await self._write_db(
            sql,
            (table_name, now, parsed_date, record_count, status, last_result_status, now),
        )

    async def get_sync_status(self, table_name: str | None = None) -> pd.DataFrame | dict | None:
        if table_name:
            df = await self._read_db(
                "SELECT * FROM sync_status WHERE table_name = $1",
                (table_name,),
            )
            if df is not None and not df.empty:
                return df.iloc[0].to_dict()
            return None
        return await self._read_db("SELECT * FROM sync_status")

    async def get_completed_step4_stocks(self, sync_version: int = 1, raise_on_error: bool = False):
        try:
            df = await self._read_db(
                "SELECT ts_code FROM stock_sync_status WHERE sync_version >= $1",
                (sync_version,),
                suppress_errors=not raise_on_error,
            )
            if df is not None and not df.empty:
                return set(df["ts_code"])
            return set()
        except Exception:
            if raise_on_error:
                raise
            return set()

    async def mark_stock_step4_completed(self, ts_code: str | None, sync_version: int = 1, conn=None):
        now = get_now().replace(tzinfo=None)
        sql = '''INSERT INTO stock_sync_status ("ts_code","step4_completed_at","sync_version")
               VALUES ($1, $2, $3)
               ON CONFLICT("ts_code") DO UPDATE SET
               "step4_completed_at"=excluded."step4_completed_at","sync_version"=excluded."sync_version"'''
        await self._write_db(sql, [(ts_code, now, sync_version)], is_many=True, conn=conn)

    async def clear_step4_sync_status(self):
        await self._write_db("DELETE FROM stock_sync_status")
