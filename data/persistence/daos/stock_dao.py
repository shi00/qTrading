import datetime
import logging

import pandas as pd

from data.persistence.models import (
    StockBasic,
    StockConcepts,
    TradeCal,
    get_model_columns,
    get_model_pk_columns,
)
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class StockDao(BaseDao):
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

    async def overwrite_concepts(self, df):
        """
        Transactional overwrite of concepts.
        Clears table and inserts new data in a single transaction.
        """
        if df is None or df.empty:
            return 0

        cols = get_model_columns(StockConcepts, exclude=set())
        df = df.copy()
        df["updated_at"] = get_now().replace(tzinfo=None)

        params = await ThreadPoolManager().run_async(
            TaskType.CPU, self._prepare_data_params, df, cols, "stock_concepts"
        )

        col_str = self._quote_columns(cols)
        sql_insert = (
            f"INSERT INTO stock_concepts ({col_str}) VALUES ({','.join([f'${i + 1}' for i in range(len(cols))])})"
        )

        try:
            # Gate: wait for maintenance before direct engine access
            await self._get_maintenance_event().wait()
            async with self.engine.begin() as conn:
                # 1. Clear old data
                await conn.exec_driver_sql("DELETE FROM stock_concepts")

                # 2. Insert new data
                if params:
                    await conn.exec_driver_sql(sql_insert, params)

            return len(params)  # type: ignore[untyped]
        except Exception as e:
            logger.error(f"[StockDao] overwrite_concepts failed: {e}")
            raise e

    async def clear_all_doubao_concepts(self) -> int:
        return await self._write_db(
            "DELETE FROM stock_concepts WHERE concept_id LIKE 'AI_DOUBAO_%'",
        )

    async def get_stocks_without_ai_concepts(
        self,
        batch_size: int,
        exclude_codes: list = None,  # type: ignore[untyped]
    ) -> list:
        sql = """
            SELECT ts_code, name FROM stock_basic
            WHERE list_status = 'L'
              AND NOT EXISTS (
                  SELECT 1 FROM stock_concepts sc
                  WHERE sc.ts_code = stock_basic.ts_code AND sc.concept_id LIKE 'AI_DOUBAO_%'
              )
        """
        df = await self._read_db(sql)
        if df is None or df.empty:
            return []
        if exclude_codes:
            df = df[~df["ts_code"].isin(exclude_codes)]
        return list(
            df[["ts_code", "name"]].itertuples(index=False, name=None)
        )[  # type: ignore[untyped]
            :batch_size
        ]

    async def get_concepts(self, ts_codes: list = None):  # type: ignore[untyped]
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
            _CHUNK = 500
            all_dfs = []
            for i in range(0, len(ts_codes), _CHUNK):
                chunk = ts_codes[i : i + _CHUNK]
                placeholders = ",".join([f"${j + 1}" for j in range(len(chunk))])
                sql = f"SELECT ts_code, concept_name FROM stock_concepts WHERE ts_code IN ({placeholders})"
                df = await self._read_db(sql, chunk)
                if df is not None and not df.empty:
                    all_dfs.append(df)
            rows = pd.concat(all_dfs, ignore_index=True) if all_dfs else None

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
        except (ValueError, RuntimeError, OSError) as exc:
            logger.debug(f"[StockDao] get_concept_count failed: {exc}")
            return 0

    async def upsert_ai_concepts(self, ai_concept_entries: list):
        """
        AI 专属概念批量入库接口。
        强制为每一个生成的概念附加唯一的 AI_DOUBAO 前缀哈希 ID，以实现物理隔离。
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
                dummy_id = f"AI_DOUBAO_{hashlib.sha256(b'NONE').hexdigest()}"
                records.append(
                    {
                        "ts_code": ts_code,
                        "concept_id": dummy_id,
                        "concept_name": "已扫描无强概念",
                    },
                )
                continue

            for concept in concepts:
                concept_id = f"AI_DOUBAO_{hashlib.sha256(concept.encode('utf-8')).hexdigest()}"
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
