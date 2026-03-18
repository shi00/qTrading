import logging

from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

from .base_dao import BaseDao

logger = logging.getLogger(__name__)


class StockDao(BaseDao):
    # --- Stock Basic ---
    async def save_stock_basic(self, df, priority=None):
        if df is None or df.empty:
            return 0
        cols = [
            "ts_code",
            "symbol",
            "name",
            "area",
            "industry",
            "market",
            "list_date",
            "list_status",
        ]

        return await self._save_upsert(df, "stock_basic", cols, pk_columns=["ts_code"])

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
        cols = ["cal_date", "exchange", "is_open", "pretrade_date"]
        # Fix is_open type
        df = df.copy()
        if "is_open" in df.columns:
            df["is_open"] = df["is_open"].astype(int)

        return await self._save_upsert(df, "trade_cal", cols, pk_columns=["cal_date"])

    async def get_trade_cal(self, start_date=None, end_date=None, is_open=None):
        sql = "SELECT * FROM trade_cal WHERE 1=1"
        p = []
        idx = 1
        if start_date:
            sql += f" AND cal_date>=${idx}"
            p.append(start_date)
            idx += 1
        if end_date:
            sql += f" AND cal_date<=${idx}"
            p.append(end_date)
            idx += 1
        if is_open is not None:
            sql += f" AND is_open=${idx}"
            p.append(is_open)
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

    # --- Concepts ---
    async def save_concepts(self, df):
        if df is None or df.empty:
            return 0
        cols = ["ts_code", "concept_name", "concept_id"]

        return await self._save_upsert(
            df, "stock_concepts", cols, pk_columns=["ts_code", "concept_id"],
        )

    async def overwrite_concepts(self, df):
        """
        Transactional overwrite of concepts.
        Clears table and inserts new data in a single transaction.
        """
        if df is None or df.empty:
            return 0

        cols = ["ts_code", "concept_name", "concept_id", "updated_at"]
        df = df.copy()
        df["updated_at"] = get_now().replace(tzinfo=None)

        params = await ThreadPoolManager().run_async(
            TaskType.CPU, self._prepare_data_params, df, cols, "stock_concepts"
        )

        col_str = self._quote_columns(cols)
        sql_insert = f"INSERT INTO stock_concepts ({col_str}) VALUES ({','.join([f'${i + 1}' for i in range(len(cols))])})"

        try:
            # Gate: wait for maintenance before direct engine access
            await self._get_maintenance_event().wait()
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

    async def clear_all_doubao_concepts(self) -> int:
        return await self._write_db(
            "DELETE FROM stock_concepts WHERE concept_id LIKE 'AI_DOUBAO_%'",
        )

    async def get_stocks_without_ai_concepts(
        self, batch_size: int, exclude_codes: list = None,
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
        return list(df[["ts_code", "name"]].itertuples(index=False, name=None))[
            :batch_size
        ]

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
                sql += " WHERE ts_code=$1"
                params.append(ts_codes[0])
            else:
                placeholders = ",".join([f"${i + 1}" for i in range(len(ts_codes))])
                sql += f" WHERE ts_code IN ({placeholders})"
                params.extend(ts_codes)

        rows = await self._read_db(sql, params)

        # Transform to dict
        result = {}
        if rows is None or rows.empty:
            return result

        # P3-2: Use groupby instead of iterrows for better performance
        for code, group in rows.groupby("ts_code"):
            # 过滤掉防无限循环的占位符概念名
            concepts = [
                c for c in group["concept_name"].tolist() if c != "已扫描无强概念"
            ]
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
        except Exception:
            return 0

    async def upsert_ai_concepts(self, ai_concept_entries: list):
        """
        AI 专属概念批量入库接口。
        强制为每一个生成的概念附加唯一的 AI_DOUBAO 前缀哈希 ID，以实现物理隔离。
        ai_concept_entries: list of dict, e.g. [{"ts_code": "000001.SZ", "concepts": ["概念1", "概念2"]}]
        """
        import hashlib

        import pandas as pd

        if not ai_concept_entries:
            return 0

        records = []

        for item in ai_concept_entries:
            ts_code = item.get("ts_code")
            if not ts_code:
                continue

            concepts = item.get("concepts", [])
            if not concepts:
                dummy_id = f"AI_DOUBAO_{hashlib.md5(b'NONE').hexdigest()}"
                records.append(
                    {
                        "ts_code": ts_code,
                        "concept_id": dummy_id,
                        "concept_name": "已扫描无强概念",
                    },
                )
                continue

            for concept in concepts:
                concept_id = (
                    f"AI_DOUBAO_{hashlib.md5(concept.encode('utf-8')).hexdigest()}"
                )
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
        cols = ["ts_code", "concept_name", "concept_id"]

        return await self._save_upsert(
            df, "stock_concepts", cols, pk_columns=["ts_code", "concept_id"],
        )
