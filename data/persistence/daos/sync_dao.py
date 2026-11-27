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
        """写入同步状态，严格分离「最近尝试日」与「已确认数据水位」（D2-02）。

        - ``last_sync_date`` = 本次尝试对应的数据日（所有状态都推进，仅作遥测/最近尝试日）；
        - ``last_data_date`` = 已确认数据水位，**仅** ``status='success'`` 且日期非空时写入，
          首次插入同样为 NULL；其余状态（failed / fetch_failed / save_failed / partial /
          cancelled / skipped_permission / empty）只代表尝试或未确认空返回，不得冒充水位。
        - 冲突更新时 ``last_data_date`` 经 ``GREATEST`` 单调推进（R22）；
          ``record_count`` 只由成功写入覆盖，且不回退到旧水位（empty / 无权限 / 失败不得
          抹掉已有历史行数）。
        - 合法稀疏表「成功抓取且当日确实无记录」由调用方（``historical.py``）经
          ``mark_empty_days`` 登记到 ``sync_empty_days``，与无权限/收盘前未出数区分。
        """
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
        # D2-02：水位只由成功完成态写入；非成功状态首插/更新均为 NULL，禁止把失败尝试日
        # 当作已确认数据水位。
        watermark_date = parsed_date if status == "success" else None
        sql = '''INSERT INTO sync_status ("table_name","last_sync_date","last_data_date","record_count","status","last_result_status","updated_at")
               VALUES ($1, $2, $3, $4, $5, $6, $7)
               ON CONFLICT("table_name") DO UPDATE SET
               "last_sync_date"=GREATEST(COALESCE(sync_status."last_sync_date", excluded."last_sync_date"), excluded."last_sync_date"),
               "last_data_date"=CASE WHEN excluded."status"='success' AND excluded."last_data_date" IS NOT NULL
                                     THEN GREATEST(COALESCE(sync_status."last_data_date", excluded."last_data_date"), excluded."last_data_date")
                                     ELSE sync_status."last_data_date" END,
               "record_count"=CASE WHEN excluded."status"='success' AND excluded."last_data_date" IS NOT NULL
                                        AND excluded."last_data_date" >= COALESCE(sync_status."last_data_date",'1900-01-01'::date)
                                   THEN excluded."record_count"
                                   ELSE sync_status."record_count" END,
               "status"=CASE WHEN sync_status."last_sync_date" IS NULL OR excluded."last_sync_date" >= sync_status."last_sync_date"
                             THEN excluded."status"
                             ELSE sync_status."status" END,
               "last_result_status"=CASE WHEN sync_status."last_sync_date" IS NULL OR excluded."last_sync_date" >= sync_status."last_sync_date"
                                         THEN excluded."last_result_status"
                                         ELSE sync_status."last_result_status" END,
               "updated_at"=excluded."updated_at"'''
        await self._write_db(
            sql,
            (table_name, parsed_date, watermark_date, record_count, status, last_result_status, now),
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
