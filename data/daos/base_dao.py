import asyncio
import logging
import time

import numpy as np
import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class BaseDao:
    # Maintenance gate: cleared during DDL (clear_cache), set otherwise.
    # All _read_db/_write_db calls await this before executing SQL.
    _maintenance_event = None  # Lazy init per event loop

    @classmethod
    def _get_maintenance_event(cls):
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            evt = asyncio.Event()
            evt.set()
            return evt
        if not hasattr(loop, "_basedao_maint_event"):
            evt = asyncio.Event()
            evt.set()
            loop._basedao_maint_event = evt
        return loop._basedao_maint_event

    def __init__(self, engine):
        self.engine = engine

    @staticmethod
    def _prepare_data_params(df, cols, table_name=None):
        if df is None or df.empty:
            return None

        df = df.copy()

        # Ensure cols exist
        for col in cols:
            if col not in df.columns:
                df[col] = None

        if table_name:
            from data.models import DATE_COLUMNS, DATETIME_COLUMNS

            target_date_cols = DATE_COLUMNS.get(table_name, [])
            target_datetime_cols = DATETIME_COLUMNS.get(table_name, [])
            for col in target_date_cols:
                if col in df.columns:
                    try:
                        # Attempt to parse strictly with coerce, avoiding setting slice on copy
                        df[col] = pd.to_datetime(df[col], format='mixed', errors='coerce').dt.date
                    except Exception:
                        pass
            for col in target_datetime_cols:
                if col in df.columns:
                    try:
                        df[col] = pd.to_datetime(df[col], format='mixed', errors='coerce')
                    except Exception:
                        pass

        df_clean = df[cols]

        # Helper to convert numpy types to native Python types for asyncpg
        def _to_native(val):
            if val is None:
                return None

            # Catch all variants of NaNs/NaTs safely before anything is coerced to float('nan')
            try:
                if pd.isna(val):
                    return None
            except (ValueError, TypeError):
                # multi-dimensional np arrays or un-hashable types that 'isna' dislikes
                pass

            if isinstance(val, (np.int64, np.int32, np.int16, np.int8)):
                return int(val)
            if isinstance(val, (np.float64, np.float32)):
                return float(val)
            if isinstance(val, (np.bool_)):
                return bool(val)
            if isinstance(val, pd.Timestamp):
                return val.to_pydatetime().replace(tzinfo=None)
            return val

        return [
            tuple(_to_native(v) for v in row)
            for row in df_clean.itertuples(index=False, name=None)
        ]

    async def _write_db(self, sql, params=None, is_many=False, suppress_errors=True):
        """Generic Write using Driver SQL for '?' support"""
        # For executemany, empty params list means nothing to do
        if is_many and not params:
            return 0

        await self._get_maintenance_event().wait()

        # Check if engine is disposed/closed
        try:
            # SQLAlchemy 1.4/2.0+ async engine check
            if hasattr(self.engine, "sync_engine") and self.engine.sync_engine is None:
                logger.warning(
                    f"[{self.__class__.__name__}] Engine disposed, skipping write.",
                )
                return 0
        except Exception:
            pass  # Safety check

        start_time = time.perf_counter()
        try:
            async with self.engine.begin() as conn:
                await conn.exec_driver_sql(sql, params)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > 2000:
                logger.warning(
                    f"[{self.__class__.__name__}] Slow Write ({elapsed:.1f}ms): {sql[:200]}...",
                )
            else:
                logger.debug(
                    f"[{self.__class__.__name__}] Write ({elapsed:.1f}ms): {sql[:200]}...",
                )

            return len(params) if is_many and params else 1
        except asyncio.CancelledError:
            logger.warning(
                f"[{self.__class__.__name__}] Write cancelled during shutdown.",
            )
            if not suppress_errors:
                raise
            return 0
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            # Suppress "no active connection" errors during shutdown
            err_str = str(e)
            if any(
                msg in err_str
                for msg in [
                    "no active connection",
                    "database is closed",
                    "ConnectionDoesNotExistError",
                ]
            ):
                logger.warning(
                    f"[{self.__class__.__name__}] DB Closed during write (Shutdown): {e}",
                )
                return 0

            logger.error(
                f"[{self.__class__.__name__}] Write Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...",
                exc_info=True,
            )

            if not suppress_errors:
                raise e
            return 0

    @staticmethod
    def _quote_columns(columns):
        """Quote column names for safe use in SQL (handles reserved words like 'date', 'on')."""
        return ",".join(['"' + c + '"' for c in columns])

    async def _save_upsert(
        self, df, table_name, columns, pk_columns, suppress_errors=True,
    ):
        """
        Generic helper for bulk UPSERT using PostgreSQL ON CONFLICT syntax.
        Leverages SQLAlchemy Core for robust type coercion from Pandas to asyncpg natively.
        """
        if df is None or df.empty:
            return 0

        import asyncio

        from data.models import Base

        await self._get_maintenance_event().wait()

        table = Base.metadata.tables.get(table_name)
        if table is None:
            logger.error(
                f"[{self.__class__.__name__}] Table {table_name} not found in SQLAlchemy metadata.",
            )
            return 0

        # Auto-inject updated_at if the table supports it
        # ⚠️ WARNING: Do NOT add similar auto-inject logic for `created_at`.
        # `created_at` relies on DB-level `server_default` and must NEVER
        # appear in the INSERT/UPDATE column list to preserve first-write semantics.
        has_updated_at = "updated_at" in table.columns.keys()

        # We must avoid modifying the passed 'df' in-place.
        # Ensure all required columns exist to prevent Pandas KeyError during slicing
        missing_cols = [col for col in columns if col not in df.columns]
        if missing_cols:
            logger.warning(
                f"[{self.__class__.__name__}] Insert '{table_name}': Missing columns in dataframe, filling with None: {missing_cols}",
            )
            # Use assign with dict comprehension to add missing columns safely as None
            df = df.assign(**{col: None for col in missing_cols})

        if has_updated_at and "updated_at" not in columns:
            columns = list(columns) + ["updated_at"]
            from datetime import datetime

            now = datetime.now().replace(tzinfo=None)
            # Use .assign to safely create a copy with the new column for the slice we need
            df_slice = df.assign(updated_at=now)[columns]
        else:
            # If the dataframe already has updated_at or the table doesn't need it, just slice
            df_slice = df[columns]

        from data.models import DATE_COLUMNS, DATETIME_COLUMNS
        target_date_cols = DATE_COLUMNS.get(table_name, [])
        target_datetime_cols = DATETIME_COLUMNS.get(table_name, [])

        # Extracting out the CPU intensive conversion to allow async offloading
        def _prepare_records(df_slice):
            df_clean = df_slice.copy()
            
            # Vectorized conversion to native date/datetime objects before dict generation
            for col in df_clean.columns:
                if col in target_date_cols:
                    df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce').dt.date
                elif col in target_datetime_cols:
                    df_clean[col] = pd.to_datetime(df_clean[col], format='mixed', errors='coerce')
                    
            records = df_clean.to_dict(orient="records")

            # Convert numpy types in dicts to python native types
            for record in records:
                for k, v in record.items():
                    # 1. Defend against NaN/NaT bypassing type checks
                    try:
                        if pd.isna(v):
                            record[k] = None
                            continue
                    except (ValueError, TypeError):
                        pass

                    # 2. Safely cast numerics and booleans
                    if isinstance(v, (np.int64, np.int32, np.int16, np.int8)):
                        record[k] = int(v)
                    elif isinstance(v, (np.float64, np.float32)):
                        record[k] = float(v)
                    elif isinstance(v, np.bool_):
                        record[k] = bool(v)
                    elif isinstance(v, pd.Timestamp):
                        record[k] = v.to_pydatetime().replace(tzinfo=None)
            return records

        records = await ThreadPoolManager().run_async(TaskType.CPU, _prepare_records, df_slice)

        stmt = pg_insert(table)
        update_cols = [c for c in columns if c not in pk_columns]

        if not update_cols:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_columns)
        else:
            update_dict = {c: getattr(stmt.excluded, c) for c in update_cols}
            stmt = stmt.on_conflict_do_update(
                index_elements=pk_columns, set_=update_dict,
            )

        start_time = time.perf_counter()
        try:
            async with self.engine.begin() as conn:
                await conn.execute(stmt, records)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > 2000:
                logger.warning(
                    f"[{self.__class__.__name__}] Slow UPSERT ({elapsed:.1f}ms, {len(records)} rows): {table_name}",
                )
            else:
                logger.debug(
                    f"[{self.__class__.__name__}] UPSERT ({elapsed:.1f}ms, {len(records)} rows): {table_name}",
                )

            return len(records)
        except asyncio.CancelledError:
            logger.warning(
                f"[{self.__class__.__name__}] UPSERT cancelled during shutdown: {table_name}",
            )
            # CancelledError is a control flow signal, MUST strictly propagate it
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            err_str = str(e)
            if any(
                msg in err_str
                for msg in [
                    "no active connection",
                    "database is closed",
                    "ConnectionDoesNotExistError",
                ]
            ):
                logger.warning(
                    f"[{self.__class__.__name__}] DB Closed during upsert (Shutdown): {e}",
                )
                return 0

            logger.error(
                f"[{self.__class__.__name__}] UPSERT Error ({elapsed:.1f}ms) on {table_name}: {e}",
                exc_info=True,
            )
            if not suppress_errors:
                raise e
            return 0

    async def _read_db(self, sql, params=None):
        """Generic Read returning DataFrame (Offloaded CSV conversion)"""
        # Ensure params is a tuple (not list) to avoid being interpreted as executemany
        if params is not None and isinstance(params, list):
            params = tuple(params)

        await self._get_maintenance_event().wait()

        start_time = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                # Execute raw SQL directly via driver to support native $1, $2 placeholders
                result = await conn.exec_driver_sql(sql, params or ())
                # Fetch all rows
                rows = result.fetchall()
                cols = list(result.keys())

                # Offload DF creation
                df = await ThreadPoolManager().run_async(
                    TaskType.CPU, pd.DataFrame, rows, columns=cols,
                )

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > 500:
                    logger.warning(
                        f"[{self.__class__.__name__}] Slow Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...",
                    )
                else:
                    logger.debug(
                        f"[{self.__class__.__name__}] Read ({elapsed:.1f}ms, {len(df)} rows): {sql[:200]}...",
                    )

                return df
        except asyncio.CancelledError:
            logger.warning(
                f"[{self.__class__.__name__}] Read cancelled during shutdown.",
            )
            return pd.DataFrame()
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000

            # Suppress connection-related errors during shutdown
            err_str = str(e)
            if any(
                msg in err_str
                for msg in [
                    "no active connection",
                    "database is closed",
                    "ConnectionDoesNotExistError",
                ]
            ):
                logger.warning(
                    f"[{self.__class__.__name__}] DB Closed during read (Shutdown): {e}",
                )
                return pd.DataFrame()

            logger.error(
                f"[{self.__class__.__name__}] Read Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...",
                exc_info=True,
            )
            return pd.DataFrame()
