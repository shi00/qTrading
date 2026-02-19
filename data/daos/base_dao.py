import logging
import time

import numpy as np
import pandas as pd

from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)


class BaseDao:
    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _prepare_data_params(df, cols, date_cols=None):
        if df is None or df.empty:
            return None

        df = df.copy()

        # Ensure cols exist
        for col in cols:
            if col not in df.columns:
                df[col] = None

        if date_cols:
            for col in date_cols:
                if col in df.columns:
                    df[col] = df[col].astype(str)

        # Convert to list of tuples (required for SQLAlchemy/sqlite3 with some drivers)
        return [tuple(x) for x in df[cols].replace({np.nan: None}).to_numpy()]

    async def _write_db(self, sql, params=None, is_many=False, suppress_errors=True):
        """Generic Write using Driver SQL for '?' support"""
        # For executemany, empty params list means nothing to do
        if is_many and not params:
            return 0

        # Check if engine is disposed/closed
        try:
            # SQLAlchemy 1.4/2.0+ async engine check
            if hasattr(self.engine, 'sync_engine') and self.engine.sync_engine is None:
                logger.warning(f"[{self.__class__.__name__}] Engine disposed, skipping write.")
                return 0
        except Exception:
            pass  # Safety check

        start_time = time.perf_counter()
        try:
            async with self.engine.begin() as conn:
                await conn.exec_driver_sql(sql, params)
            
            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > 2000:
                logger.warning(f"[{self.__class__.__name__}] Slow Write ({elapsed:.1f}ms): {sql[:200]}...")
            else:
                logger.debug(f"[{self.__class__.__name__}] Write ({elapsed:.1f}ms): {sql[:200]}...")
                
            return len(params) if is_many and params else 1
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            # Suppress "no active connection" errors during shutdown
            if "no active connection" in str(e) or "database is closed" in str(e):
                 logger.warning(f"[{self.__class__.__name__}] DB Closed during write (Shutdown): {e}")
                 return 0
            
            logger.error(f"[{self.__class__.__name__}] Write Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...", exc_info=True)
            
            if not suppress_errors:
                raise e
            return 0

    @staticmethod
    def _quote_columns(columns):
        """Quote column names for safe use in SQL (handles reserved words like 'date', 'on')."""
        return ",".join(['"' + c + '"' for c in columns])

    async def _save_upsert(self, df, table_name, columns, pk_columns, suppress_errors=True):
        """
        Generic helper for INSERT INTO ... ON CONFLICT (...) DO UPDATE SET ...
        This optimizes write performance by avoiding DELETE+INSERT of REPLACE.
        """
        if df is None or df.empty: return 0

        placeholders = ",".join(["?"] * len(columns))
        col_str = self._quote_columns(columns)
        pk_str = self._quote_columns(pk_columns)

        update_cols = [c for c in columns if c not in pk_columns]
        if not update_cols:
            sql = f"INSERT INTO {table_name} ({col_str}) VALUES ({placeholders}) ON CONFLICT({pk_str}) DO NOTHING"
        else:
            update_clause = ",".join(['"' + c + '"=excluded."' + c + '"' for c in update_cols])
            sql = f"""
                INSERT INTO {table_name} ({col_str}) 
                VALUES ({placeholders}) 
                ON CONFLICT({pk_str}) DO UPDATE SET {update_clause}
            """

        params = await ThreadPoolManager().run_async(TaskType.CPU, self._prepare_data_params, df, columns)
        return await self._write_db(sql, params, is_many=True, suppress_errors=suppress_errors)

    async def _read_db(self, sql, params=None):
        """Generic Read returning DataFrame (Offloaded CSV conversion)"""
        # Ensure params is a tuple (not list) to avoid being interpreted as executemany
        if params is not None and isinstance(params, list):
            params = tuple(params)

        start_time = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.exec_driver_sql(sql, params or ())
                # Fetch all rows
                rows = result.fetchall()
                cols = list(result.keys())

                # Offload DF creation
                df = await ThreadPoolManager().run_async(TaskType.CPU, pd.DataFrame, rows, columns=cols)
                
                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > 500:
                    logger.warning(f"[{self.__class__.__name__}] Slow Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...")
                else:
                    logger.debug(f"[{self.__class__.__name__}] Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...")
                
                return df
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            
            # Suppress "no active connection" errors during shutdown
            if "no active connection" in str(e) or "database is closed" in str(e):
                 logger.warning(f"[{self.__class__.__name__}] DB Closed during read (Shutdown): {e}")
                 return pd.DataFrame()

            logger.error(f"[{self.__class__.__name__}] Read Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...", exc_info=True)
            return pd.DataFrame()
