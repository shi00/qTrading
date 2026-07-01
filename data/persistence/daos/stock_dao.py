import asyncio
import datetime
import logging
import typing

import pandas as pd

from data.persistence.models import (
    StockBasic,
    StockConcepts,
    TradeCal,
    get_model_columns,
    get_model_pk_columns,
)
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now, to_utc_for_db

from .base_dao import BaseDao, EngineDisposedError

logger = logging.getLogger(__name__)


class StockDao(BaseDao):
    AI_CONCEPT_PREFIX = "AI_LLM_"
    EM_CONCEPT_PREFIX = "EM_"
    LIMIT_CONCEPT_PREFIX = "LIMIT_"

    async def save_stock_basic(self, df, priority=None):
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StockBasic)
        pk_columns = get_model_pk_columns(StockBasic)

        return await self._save_upsert(df, "stock_basic", cols, pk_columns=pk_columns)

    async def get_stock_basic(self):
        return await self._read_db("SELECT * FROM stock_basic")

    async def get_active_stock_count(self):
        """Count stocks with list_status='L'"""
        df = await self._read_db(
            "SELECT count(*) as cnt FROM stock_basic WHERE list_status='L'",
        )
        if df is not None and not df.empty:
            return df["cnt"].iloc[0] or 0
        return 0

    # --- Trade Calendar ---
    async def save_trade_cal(self, df):
        cols = get_model_columns(TradeCal)
        pk_columns = get_model_pk_columns(TradeCal)
        # Fix is_open type
        df = df.copy()
        if "is_open" in df.columns:
            df["is_open"] = df["is_open"].astype(int)

        return await self._save_upsert(df, "trade_cal", cols, pk_columns=pk_columns)

    async def get_trade_cal(
        self,
        start_date: datetime.date | str | None = None,
        end_date: datetime.date | str | None = None,
        is_open: int | str | None = None,
    ):
        sql = "SELECT * FROM trade_cal WHERE 1=1"
        p = []
        idx = 1
        sd = self._to_date_str(start_date)
        ed = self._to_date_str(end_date)
        if sd:
            sql += f" AND cal_date>=${idx}"
            p.append(sd)
            idx += 1
        if ed:
            sql += f" AND cal_date<=${idx}"
            p.append(ed)
            idx += 1
        if is_open is not None:
            sql += f" AND is_open=${idx}"
            p.append(int(is_open))
        sql += " ORDER BY cal_date ASC"
        return await self._read_db(sql, p)

    async def get_trade_cal_range(self):
        """Get the min and max calendar dates from DB"""
        df = await self._read_db(
            "SELECT MIN(cal_date) as min_d, MAX(cal_date) as max_d FROM trade_cal",
        )
        if df is not None and not df.empty:
            return (df["min_d"].iloc[0], df["max_d"].iloc[0])
        return (None, None)

    async def count_trade_days(self, start_date: datetime.date | str | None, end_date: datetime.date | str | None):
        """
        Count trading days in the given date range.

        Args:
            start_date: Start date (date object or string)
            end_date: End date (date object or string)

        Returns:
            int: Number of trading days
        """
        sd = self._to_date_str(start_date)
        ed = self._to_date_str(end_date)
        sql = "SELECT COUNT(*) as cnt FROM trade_cal WHERE is_open=1 AND cal_date >= $1 AND cal_date <= $2"
        df = await self._read_db(sql, (sd, ed))
        if df is None or df.empty:
            return 0
        return df["cnt"].iloc[0] or 0

    async def get_start_date_by_trade_days(self, end_date: datetime.date | str | None, trade_days: int):
        """
        Get the start date by looking back N trading days from end_date.

        Args:
            end_date: End date (date object)
            trade_days: Number of trading days to look back

        Returns:
            date: The start date (N trading days before end_date)
        """
        ed = self._to_date_str(end_date)
        sql = """
            SELECT cal_date FROM trade_cal
            WHERE is_open = 1 AND cal_date <= $1
            ORDER BY cal_date DESC
            LIMIT $2
        """
        df = await self._read_db(sql, (ed, trade_days))
        if df is None or df.empty or len(df) < trade_days:
            return None
        return df["cal_date"].iloc[-1]

    async def count_expected_rows(self, start_date, end_date):
        """
        Calculate expected rows: sum of trading days per stock after its list_date.
        Used for data completeness check.

        Args:
            start_date: Start date (date object)
            end_date: End date (date object)

        Returns:
            int: Expected row count (at least 1 to avoid division by zero)
        """
        sql = """
            SELECT SUM(
                (SELECT COUNT(*) FROM trade_cal tc
                 WHERE tc.is_open = 1
                   AND tc.cal_date >= GREATEST(s.list_date, $1)
                   AND tc.cal_date <= $2)
            ) as expected FROM stock_basic s
            WHERE s.list_status = 'L'
        """
        df = await self._read_db(sql, (start_date, end_date))
        if df is None or df.empty:
            return 1
        return df["expected"].iloc[0] or 1

    # --- Concepts ---
    async def save_concepts(self, df):
        if df is None or df.empty:
            return 0
        cols = get_model_columns(StockConcepts)
        pk_columns = get_model_pk_columns(StockConcepts)

        return await self._save_upsert(
            df,
            "stock_concepts",
            cols,
            pk_columns=pk_columns,
        )

    @log_async_operation(
        operation_name="StockDao.overwrite_concepts",
        threshold_ms=PerfThreshold.DB_BULK_IO,
    )
    async def overwrite_concepts(self, df):
        """
        Transactional overwrite of concepts.
        Clears table and inserts new data in a single transaction.
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(StockConcepts, exclude=set())
        df = df.copy()
        # T4 fix: 与 server_default=now() 保持 UTC 一致（S1-6 fix 模式），避免 tz-naive CST 与 DB 比较偏差 8 小时
        df["updated_at"] = typing.cast(datetime.datetime, to_utc_for_db(get_now()))

        params = await ThreadPoolManager().run_async(
            TaskType.CPU, self._prepare_data_params, df, cols, "stock_concepts"
        )

        col_str = self._quote_columns(cols)
        sql_insert = (
            f"INSERT INTO stock_concepts ({col_str}) VALUES ({','.join([f'${i + 1}' for i in range(len(cols))])})"
        )

        try:
            async with self._guarded_begin() as conn:
                # 1. Clear old EM-prefixed concepts only (preserve AI_LLM_ concepts)
                await conn.exec_driver_sql(
                    "DELETE FROM stock_concepts WHERE concept_id LIKE $1",
                    [f"{self.EM_CONCEPT_PREFIX}%"],
                )

                # 2. Insert new data
                if params:
                    await conn.exec_driver_sql(sql_insert, params)

            return len(params)  # type: ignore[untyped]
        except asyncio.CancelledError:
            logger.warning("[StockDao] Cancelled during overwrite_concepts.")
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.error("[StockDao] overwrite_concepts failed: %s", e)
            raise

    async def clear_all_ai_llm_concepts(self) -> int:
        return await self._write_db(
            "DELETE FROM stock_concepts WHERE concept_id LIKE $1",
            [f"{self.AI_CONCEPT_PREFIX}%"],
        )

    async def get_stocks_without_ai_concepts(
        self,
        batch_size: int,
        exclude_codes: list[str] | None = None,
    ) -> list[tuple[str, str]]:
        sql = """
            SELECT ts_code, name FROM stock_basic
            WHERE list_status = 'L'
              AND NOT EXISTS (
                  SELECT 1 FROM stock_concepts sc
                  WHERE sc.ts_code = stock_basic.ts_code AND sc.concept_id LIKE $1
              )
        """
        df = await self._read_db(sql, [f"{self.AI_CONCEPT_PREFIX}%"])
        if df is None or df.empty:
            return []
        if exclude_codes:
            df = df[~df["ts_code"].isin(exclude_codes)]
        return list(
            df[["ts_code", "name"]].itertuples(index=False, name=None)
        )[  # type: ignore[untyped]
            :batch_size
        ]

    async def get_concepts(self, ts_codes: list[str] | None = None) -> dict[str, list[str]]:
        """
        Get concepts for given stock codes.
        Returns: Dict[ts_code, List[concept_name]]
        """
        if ts_codes is None:
            rows = await self._read_db("SELECT ts_code, concept_name FROM stock_concepts")
        elif len(ts_codes) == 0:
            return {}
        elif len(ts_codes) == 1:
            rows = await self._read_db(
                "SELECT ts_code, concept_name FROM stock_concepts WHERE ts_code=$1",
                [ts_codes[0]],
            )
        else:
            rows = await self.chunked_in_query(
                self._read_db,
                "SELECT ts_code, concept_name FROM stock_concepts WHERE ts_code IN ({placeholders})",
                ts_codes,
            )

        result = {}
        if rows is None or rows.empty:
            return result

        for code, group in rows.groupby("ts_code"):
            concepts = [c for c in group["concept_name"].tolist() if c != "已扫描无强概念"]
            if concepts:
                result[code] = concepts

        return result

    async def get_concept_count(self):
        """Get total count of stock concept mappings."""
        try:
            df = await self._read_db("SELECT COUNT(*) as cnt FROM stock_concepts")
            if df is not None and not df.empty:
                return df["cnt"].iloc[0] or 0
            return 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[StockDao] get_concept_count failed: %s", exc)
            return 0

    async def upsert_ai_concepts(self, ai_concept_entries: list):
        """
        AI 专属概念批量入库接口。
        强制为每一个生成的概念附加唯一的 AI_LLM 前缀哈希 ID，以实现物理隔离。
        ai_concept_entries: list of dict, e.g. [{"ts_code": "000001.SZ", "concepts": ["概念1", "概念2"]}]
        """
        import hashlib

        if not ai_concept_entries:
            return 0

        records = []

        for item in ai_concept_entries:
            ts_code = item.get("ts_code")
            if not ts_code:
                continue

            concepts = item.get("concepts", [])
            if not concepts:
                dummy_id = f"{self.AI_CONCEPT_PREFIX}{hashlib.sha256(b'NONE').hexdigest()}"
                records.append(
                    {
                        "ts_code": ts_code,
                        "concept_id": dummy_id,
                        "concept_name": "已扫描无强概念",
                    },
                )
                continue

            for concept in concepts:
                concept_id = f"{self.AI_CONCEPT_PREFIX}{hashlib.sha256(concept.encode('utf-8')).hexdigest()}"
                records.append(
                    {
                        "ts_code": ts_code,
                        "concept_id": concept_id,
                        "concept_name": concept,
                    },
                )

        if not records:
            return 0

        df = pd.DataFrame(records)
        cols = get_model_columns(StockConcepts)
        pk_columns = get_model_pk_columns(StockConcepts)

        return await self._save_upsert(
            df,
            "stock_concepts",
            cols,
            pk_columns=pk_columns,
        )

    async def upsert_em_concepts(self, records: list[dict]) -> int:
        """
        东财概念板块成分股入库接口。
        records: list of dict, e.g. [{"ts_code": "000001.SZ", "concept_id": "EM_C1", "concept_name": "概念1"}]
        """
        if not records:
            return 0

        df = pd.DataFrame(records)
        cols = get_model_columns(StockConcepts)
        pk_columns = get_model_pk_columns(StockConcepts)

        return await self._save_upsert(
            df,
            "stock_concepts",
            cols,
            pk_columns=pk_columns,
        )

    async def upsert_limit_concepts(self, records: list[dict]) -> int:
        """
        涨停原因概念入库接口。
        records: list of dict, e.g. [{"ts_code": "000001.SZ", "concept_id": "LIMIT_C1", "concept_name": "涨停原因1"}]
        """
        if not records:
            return 0

        df = pd.DataFrame(records)
        cols = get_model_columns(StockConcepts)
        pk_columns = get_model_pk_columns(StockConcepts)

        return await self._save_upsert(
            df,
            "stock_concepts",
            cols,
            pk_columns=pk_columns,
        )

    async def clear_today_limit_concepts(self) -> int:
        """清空当日 LIMIT_ 前缀概念（涨停原因概念每日重建）。"""
        return await self._write_db(
            "DELETE FROM stock_concepts WHERE concept_id LIKE $1",
            [f"{self.LIMIT_CONCEPT_PREFIX}%"],
        )

    async def get_concepts_by_prefix(
        self,
        prefix: str,
        ts_codes: list | None = None,
    ) -> list[dict]:
        """
        按 concept_id 前缀查询概念，可选用 ts_codes 过滤。
        Returns: list of dict, e.g. [{"ts_code": "...", "concept_id": "...", "concept_name": "..."}]
        """
        sql = "SELECT ts_code, concept_id, concept_name FROM stock_concepts WHERE concept_id LIKE $1"
        params: list = [f"{prefix}%"]
        if ts_codes:
            placeholders = ",".join([f"${i + 2}" for i in range(len(ts_codes))])
            sql += f" AND ts_code IN ({placeholders})"
            params.extend(ts_codes)

        df = await self._read_db(sql, params)
        if df is None or df.empty:
            return []
        return df.to_dict(orient="records")

    # --- AI Concept Failures (错题本) ---

    # 默认重试上限与冷却期（24h），可由调用方覆盖
    AI_CONCEPT_FAILURE_MAX_RETRY = 3
    AI_CONCEPT_FAILURE_COOLDOWN_SECONDS = 24 * 3600

    @log_async_operation(
        operation_name="StockDao.upsert_ai_concept_failure",
        threshold_ms=PerfThreshold.DB_SINGLE_QUERY,
    )
    async def upsert_ai_concept_failure(
        self,
        ts_code: str,
        name: str,
        error: str,
        *,
        cooldown_seconds: int | None = None,
    ) -> int:
        """记录/更新一次失败：retry_count+1，刷新 last_attempt_at / next_retry_at。

        使用 UPSERT（ON CONFLICT ts_code DO UPDATE）保证幂等。
        """
        cooldown = cooldown_seconds if cooldown_seconds is not None else self.AI_CONCEPT_FAILURE_COOLDOWN_SECONDS
        # T4 fix: 写入 UTC tz-naive（S1-6 fix 模式），与 DB server_default=now() / SQL now() 保持时区一致。
        # 原代码 get_now().replace(tzinfo=None) 写入 tz-naive CST，与 SQL `next_retry_at <= now()` 比较时
        # 在非 CST 服务器上会有 8 小时偏差，导致 24h 冷却实际变成 16h 或 32h。
        # 前提：PostgreSQL 服务器/会话时区为 UTC（生产环境已确认）。
        now = typing.cast(datetime.datetime, to_utc_for_db(get_now()))
        next_retry = now + datetime.timedelta(seconds=cooldown)
        sql = """
            INSERT INTO ai_concept_failures
                (ts_code, name, last_error, retry_count, last_attempt_at, next_retry_at)
            VALUES ($1, $2, $3, 1, $4, $5)
            ON CONFLICT (ts_code) DO UPDATE SET
                name = EXCLUDED.name,
                last_error = EXCLUDED.last_error,
                retry_count = ai_concept_failures.retry_count + 1,
                last_attempt_at = EXCLUDED.last_attempt_at,
                next_retry_at = EXCLUDED.next_retry_at,
                updated_at = now()
        """
        try:
            async with self._guarded_begin() as conn:
                await conn.exec_driver_sql(sql, (ts_code, name, error, now, next_retry))
            return 1
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.error("[StockDao] upsert_ai_concept_failure failed for %s: %s", ts_code, e, exc_info=True)
            raise

    async def get_ai_concept_failures_for_retry(
        self,
        batch_size: int,
        *,
        max_retry: int | None = None,
    ) -> list[tuple[str, str]]:
        """拉取可重试的失败股票：retry_count < max_retry AND next_retry_at <= now。

        Returns: list of (ts_code, name)
        """
        limit = max_retry if max_retry is not None else self.AI_CONCEPT_FAILURE_MAX_RETRY
        sql = """
            SELECT ts_code, name FROM ai_concept_failures
            WHERE retry_count < $1
              AND (next_retry_at IS NULL OR next_retry_at <= now())
            ORDER BY last_attempt_at ASC
            LIMIT $2
        """
        try:
            df = await self._read_db(sql, (limit, batch_size))
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.error("[StockDao] get_ai_concept_failures_for_retry failed: %s", e, exc_info=True)
            return []
        if df is None or df.empty:
            return []
        return list(df[["ts_code", "name"]].itertuples(index=False, name=None))

    async def clear_ai_concept_failure(self, ts_code: str) -> int:
        """成功打标后从错题本删除。"""
        try:
            return await self._write_db(
                "DELETE FROM ai_concept_failures WHERE ts_code = $1",
                (ts_code,),
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.error("[StockDao] clear_ai_concept_failure failed for %s: %s", ts_code, e, exc_info=True)
            raise

    async def count_ai_concept_failures(self) -> int:
        """统计错题本当前条目数（用于诊断/监控）。"""
        try:
            df = await self._read_db("SELECT COUNT(*) AS cnt FROM ai_concept_failures")
            if df is None or df.empty:
                return 0
            return int(df["cnt"].iloc[0] or 0)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[StockDao] count_ai_concept_failures failed: %s", e)
            return 0

    async def delete_expired_failures(self, max_retry: int | None = None) -> int:
        """T5 fix: 清理 retry_count >= max_retry 的错题本记录。

        这些记录已耗尽重试机会，继续保留无意义且会无限累积。
        建议在 AIConceptTagSyncStrategy 每次运行结束时调用。

        Returns: 被删除的记录数
        """
        limit = max_retry if max_retry is not None else self.AI_CONCEPT_FAILURE_MAX_RETRY
        try:
            return await self._write_db(
                "DELETE FROM ai_concept_failures WHERE retry_count >= $1",
                (limit,),
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.error("[StockDao] delete_expired_failures failed: %s", e, exc_info=True)
            raise
