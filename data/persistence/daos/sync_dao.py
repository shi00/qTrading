import asyncio
import logging
import typing
from datetime import date, datetime

import pandas as pd

from data.constants import (
    SYNC_RESULT_EMPTY,
    SYNC_RESULT_FETCH_FAILED,
    SYNC_RESULT_HAS_DATA,
    SYNC_RESULT_SAVE_FAILED,
    SYNC_RESULT_SKIPPED_PERMISSION,
)
from data.persistence.models import SyncEmptyDay, StockSyncStatus, get_model_columns, get_model_pk_columns
from utils.time_utils import get_now, parse_date, to_utc_for_db

from .base_dao import BaseDao, EngineDisposedError

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
            if status == "skipped_permission":
                last_result_status = SYNC_RESULT_SKIPPED_PERMISSION
            elif status in ("success", "empty") and record_count > 0:
                last_result_status = SYNC_RESULT_HAS_DATA
            elif status in ("success", "empty") and record_count == 0:
                last_result_status = SYNC_RESULT_EMPTY
            elif status == "save_failed":
                last_result_status = SYNC_RESULT_SAVE_FAILED
            else:
                last_result_status = SYNC_RESULT_FETCH_FAILED

        now = typing.cast(
            datetime, to_utc_for_db(get_now())
        )  # T4 fix: 与 server_default=now() 时区一致（前提：DB 会话时区为 UTC）
        sql = '''INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","last_result_status","updated_at")
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT("table_name") DO UPDATE SET
               "last_sync_date"=excluded."last_sync_date",
               "last_data_date"=CASE WHEN excluded."status" IN ('success','empty','skipped_permission') AND excluded."last_data_date" > COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                                     THEN excluded."last_data_date"
                                     ELSE sync_status."last_data_date" END,
               "record_count"=CASE WHEN excluded."status" IN ('success','empty','skipped_permission') AND excluded."last_data_date" >= COALESCE(sync_status."last_data_date",'1900-01-01'::date)
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

    async def get_completed_step4_stocks(self, sync_version: int = 1, raise_on_error: bool = False) -> set[str]:
        try:
            df = await self._read_db(
                "SELECT ts_code FROM stock_sync_status WHERE sync_version >= $1",
                (sync_version,),
                suppress_errors=not raise_on_error,
            )
            if df is not None and not df.empty:
                return set(df["ts_code"])
            return set()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as exc:
            if raise_on_error:
                raise
            logger.debug("[SyncDao] Step4 stock query failed: %s", exc)
            return set()

    async def mark_stock_step4_completed(self, ts_code: str | None, sync_version: int = 1, conn=None):
        now = typing.cast(
            datetime, to_utc_for_db(get_now())
        )  # T4 fix: 与 server_default=now() 时区一致（前提：DB 会话时区为 UTC）
        df = pd.DataFrame(
            [
                {
                    "ts_code": ts_code,
                    "step4_completed_at": now,
                    "sync_version": sync_version,
                }
            ]
        )
        await self._save_upsert(
            df,
            "stock_sync_status",
            get_model_columns(StockSyncStatus),
            pk_columns=get_model_pk_columns(StockSyncStatus),
            conn=conn,
        )

    async def clear_step4_sync_status(self):
        await self._write_db("DELETE FROM stock_sync_status")

    async def mark_empty_days(self, table_name: str, dates: typing.Iterable[date | datetime | str]) -> int:
        """登记稀疏表在指定交易日"已成功抓取且合法为空"（review09-24 dim05 MAJOR-03）。

        幂等 upsert：``sync_empty_days`` 仅含复合主键 (table_name, trade_date)、无业务列，
        故重复登记同一键走 ``ON CONFLICT DO NOTHING``——不产生重复行，也不刷新时间戳
        （该事实不可变；首插时 ``updated_at`` / ``created_at`` 由 ``server_default=now()`` 填充）。
        调用方（historical.py）仅对 ``result_status == SYNC_RESULT_EMPTY``（成功 fetch 且为空，
        排除抓取/落库失败与无权限跳过）的表调用，故登记即代表"该表该日合法为空"。

        best-effort 元数据登记：普通写库异常经 ``suppress_errors=True`` 吞掉并返回 -1，
        不阻断同步主流程；``asyncio.CancelledError``（R2）与 ``EngineDisposedError``（R5）仍传播。

        Returns:
            实际落库行数（去重后）；无有效日期时返回 0；写库被抑制时返回 -1。
        """
        clean_dates = sorted({d for d in dates if d is not None})
        if not clean_dates:
            return 0
        df = pd.DataFrame([{"table_name": table_name, "trade_date": d} for d in clean_dates])
        return await self._save_upsert(
            df,
            "sync_empty_days",
            get_model_columns(SyncEmptyDay),
            pk_columns=get_model_pk_columns(SyncEmptyDay),
            suppress_errors=True,
        )
