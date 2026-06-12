import asyncio
from contextlib import asynccontextmanager
import datetime
import logging
import time
import typing
from decimal import Decimal

import numpy as np
import pandas as pd
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from utils.loop_local import get_loop_local
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class EngineDisposedError(RuntimeError):
    """Raised when a DAO operation is attempted after the database engine has been disposed.

    This is a P0 data-safety guard: silently dropping writes or returning empty
    results on a disposed engine can cause data loss without any notification.
    Callers must catch this explicitly if they want graceful degradation.
    """


_IN_CHUNK_SIZE = 500
_UPSERT_CHUNK_SIZE = 500
_SLOW_WRITE_THRESHOLD_MS = 2000
_SLOW_READ_THRESHOLD_MS = 500
_SLOW_UPSERT_THRESHOLD_MS = 2000


class BaseDao:
    _maintenance_event = None

    def _check_engine(self) -> None:
        """Check if the engine is initialized and not disposed.

        Raises:
            RuntimeError: If engine is not initialized.
            EngineDisposedError: If engine has been disposed.
        """
        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )
        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and getattr(CacheManager._instance, "_disposed", False):
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed. Call CacheManager.init_db() to reinitialize."
            )

    @asynccontextmanager
    async def _guarded_begin(self, conn: typing.Any = None):
        """Unified transaction/connection context manager with engine disposal guard.

        If an existing conn is provided, it yields it and does not start a new transaction.
        Otherwise, it starts an engine.begin() transaction.
        """
        self._check_engine()
        await self._get_maintenance_event().wait()

        if conn is not None:
            yield conn
            return

        try:
            async with self.engine.begin() as tx_conn:
                yield tx_conn
        except asyncio.CancelledError:
            raise
        except Exception as e:
            err_str = str(e)
            if any(
                msg in err_str
                for msg in [
                    "no active connection",
                    "database is closed",
                    "ConnectionDoesNotExistError",
                ]
            ):
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during guarded begin: {e}"
                ) from e
            raise

    @staticmethod
    async def chunked_in_query(
        read_db_fn,
        sql_template,
        values,
        *,
        chunk_size=_IN_CHUNK_SIZE,
        params_fn=None,
        start_idx=1,
        extra_params=None,
        **read_db_kwargs,
    ):
        """
        Execute a SQL query with IN clause in chunks to avoid PostgreSQL parameter limit.

        Args:
            read_db_fn: async _read_db method (or equivalent)
            sql_template: SQL with {placeholders} marker or a callable(placeholders, chunk_len) -> sql_string
            values: list of values for the IN clause
            chunk_size: maximum items per IN clause (default 500)
            params_fn: callable(values_chunk) -> extra params list, appended after values
            start_idx: starting index for placeholders (default 1)
            extra_params: prefix parameters list to prepend to query arguments
            **read_db_kwargs: extra kwargs to pass to read_db_fn (e.g., suppress_errors=True)
        """
        if not values:
            return pd.DataFrame()

        extra_prefix = extra_params or []
        prefix_len = len(extra_prefix)
        actual_start_idx = start_idx if extra_params is None else prefix_len + 1

        if len(values) <= chunk_size:
            placeholders = ",".join([f"${actual_start_idx + i}" for i in range(len(values))])
            extra_suffix = params_fn(values) if params_fn else []
            if callable(sql_template):
                sql = sql_template(placeholders, len(values))
            else:
                sql = sql_template.format(placeholders=placeholders)
            df = await read_db_fn(sql, extra_prefix + values + extra_suffix, **read_db_kwargs)
            return df if df is not None else pd.DataFrame()

        all_results = []
        for i in range(0, len(values), chunk_size):
            chunk = values[i : i + chunk_size]
            placeholders = ",".join([f"${actual_start_idx + j}" for j in range(len(chunk))])
            extra_suffix = params_fn(chunk) if params_fn else []
            if callable(sql_template):
                sql = sql_template(placeholders, len(chunk))
            else:
                sql = sql_template.format(placeholders=placeholders)
            df = await read_db_fn(sql, extra_prefix + chunk + extra_suffix, **read_db_kwargs)
            if df is not None and not df.empty:
                all_results.append(df)

        if all_results:
            return pd.concat(all_results, ignore_index=True)
        return pd.DataFrame()

    @staticmethod
    def _to_date_str(val: datetime.date | str | None) -> str | None:
        if val is None:
            return None
        if isinstance(val, str):
            return val
        return val.strftime("%Y%m%d")

    @classmethod
    def _get_maintenance_event(cls):
        import asyncio

        def _factory():
            evt = asyncio.Event()
            evt.set()
            return evt

        return get_loop_local("basedao_maint_event", _factory)

    def __init__(self, engine: typing.Any):
        self.engine = engine

    @staticmethod
    def _prepare_data_params(df: pd.DataFrame, cols: list, table_name: str | None = None):
        if df is None or df.empty:
            return None

        df = df.copy()

        # Ensure cols exist
        for col in cols:
            if col not in df.columns:
                df[col] = None

        if table_name:
            from data.persistence.models import Base
            from sqlalchemy import Date, DateTime

            table = Base.metadata.tables.get(table_name)
            if table is not None:
                target_date_cols = [c.name for c in table.columns if isinstance(c.type, Date)]
                target_datetime_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]
                for col in target_date_cols:
                    if col in df.columns:
                        try:
                            df[col] = pd.to_datetime(df[col], format="mixed", errors="coerce").dt.date
                        except (ValueError, TypeError) as e:
                            logger.debug("[BaseDao] Date conversion skipped for column '%s': %s", col, e)
                for col in target_datetime_cols:
                    if col in df.columns:
                        try:
                            df[col] = pd.to_datetime(df[col], format="mixed", errors="coerce")
                        except (ValueError, TypeError) as e:
                            logger.debug("[BaseDao] Datetime conversion skipped for column '%s': %s", col, e)

        df_clean = df[cols]

        # Helper to convert numpy types to native Python types for asyncpg
        def _to_native(val: typing.Any):
            if val is None:
                return None

            try:
                if pd.isna(val):
                    return None
            except (ValueError, TypeError):
                pass

            if isinstance(val, (np.int64, np.int32, np.int16, np.int8)):  # type: ignore[union-attr]
                return int(val)
            if isinstance(val, (np.float64, np.float32)):  # type: ignore[union-attr]
                return float(val)
            if isinstance(val, Decimal):
                return val
            if isinstance(val, (np.bool_)):
                return bool(val)
            if isinstance(val, pd.Timestamp):
                return val.to_pydatetime().replace(tzinfo=None)
            return val

        return [tuple(_to_native(v) for v in row) for row in df_clean.itertuples(index=False, name=None)]

    async def _write_db(
        self,
        sql: typing.Any,
        params: typing.Any = None,
        is_many: typing.Any = False,
        suppress_errors: bool = False,
        conn: typing.Any = None,
    ):
        """Execute a single SQL statement. For bulk operations, use _save_upsert.

        Note: is_many=True does NOT use executemany — it passes the full params
        list to exec_driver_sql. Prefer _save_upsert for batch inserts/updates.

        .. deprecated::
            The ``is_many`` parameter is deprecated and will be removed in a
            future version. Use ``_save_upsert`` for batch operations.
        """
        if is_many:
            import warnings

            warnings.warn(
                "_write_db(is_many=True) is deprecated. Use _save_upsert for batch operations.",
                DeprecationWarning,
                stacklevel=2,
            )
        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )

        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and getattr(CacheManager._instance, "_disposed", False):
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed, write rejected. "
                f"Call CacheManager.init_db() to reinitialize."
            )

        if is_many and not params:
            return 0

        if params:
            if is_many:
                params = [tuple(self._convert_param_for_asyncpg(p) for p in row) for row in params]
            else:
                params = (
                    tuple(self._convert_param_for_asyncpg(p) for p in params)
                    if not isinstance(params, tuple)
                    else tuple(self._convert_param_for_asyncpg(p) for p in params)
                )

        await self._get_maintenance_event().wait()

        # Check if engine is disposed/closed
        try:
            if hasattr(self.engine, "sync_engine") and self.engine.sync_engine is None:
                raise EngineDisposedError(f"[{self.__class__.__name__}] Engine sync_engine is None, write rejected.")
        except EngineDisposedError:
            raise
        except Exception as e:
            logger.debug("[BaseDao] Engine sync_engine check skipped: %s", e)

        start_time = time.perf_counter()
        try:
            if conn is not None:
                await conn.exec_driver_sql(sql, params)
            else:
                async with self.engine.begin() as conn:
                    await conn.exec_driver_sql(sql, params)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > _SLOW_WRITE_THRESHOLD_MS:
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
                "[%s] Write cancelled during shutdown.",
                self.__class__.__name__,
            )
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
                    f"[{self.__class__.__name__}] DB Closed during write (Shutdown): {e}",
                )
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during write, data not persisted: {e}"
                ) from e

            if suppress_errors:
                logger.warning(
                    f"[{self.__class__.__name__}] Write Error ({elapsed:.1f}ms, suppressed): {e}",
                )
            else:
                logger.error(
                    f"[{self.__class__.__name__}] Write Error ({elapsed:.1f}ms): {e}\nSQL: {sql[:200]}...",
                    exc_info=True,
                )

            if not suppress_errors:
                raise
            return -1

    @staticmethod
    def _quote_columns(columns: typing.Any):
        """Quote column names for safe use in SQL (handles reserved words like 'date', 'on').

        Doubles any embedded double-quote characters to prevent injection.
        All current callers pass hardcoded schema column names, but this
        defensive measure guards against future misuse.
        """
        return ",".join(['"' + c.replace('"', '""') + '"' for c in columns])

    async def _save_upsert(
        self,
        df: pd.DataFrame,
        table_name: str,
        columns: typing.Any,
        pk_columns: typing.Any,
        suppress_errors: bool = False,
        conn: typing.Any = None,
    ):
        """
        Generic helper for bulk UPSERT using PostgreSQL ON CONFLICT syntax.
        Leverages SQLAlchemy Core for robust type coercion from Pandas to asyncpg natively.
        """
        if df is None or df.empty:
            return 0

        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )

        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and getattr(CacheManager._instance, "_disposed", False):
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed, upsert rejected. "
                f"Call CacheManager.init_db() to reinitialize."
            )

        import asyncio

        from data.persistence.models import Base

        await self._get_maintenance_event().wait()

        table = Base.metadata.tables.get(table_name)
        if table is None:
            logger.error(
                f"[{self.__class__.__name__}] Table {table_name} not found in SQLAlchemy metadata.",
            )
            return 0

        has_updated_at = "updated_at" in table.columns

        missing_cols = [col for col in columns if col not in df.columns]
        if missing_cols:
            pk_missing = [col for col in missing_cols if col in pk_columns]
            if pk_missing:
                logger.error(
                    f"[{self.__class__.__name__}] Insert '{table_name}': PK columns missing in dataframe, aborting: {pk_missing}",
                )
                return 0
            logger.warning(
                f"[{self.__class__.__name__}] Insert '{table_name}': Missing columns in dataframe, filling with None: {missing_cols}",
            )
            df = df.assign(**{col: None for col in missing_cols})

        df_slice = df[columns]

        from sqlalchemy import Date, DateTime

        target_date_cols = [c.name for c in table.columns if isinstance(c.type, Date)]
        target_datetime_cols = [c.name for c in table.columns if isinstance(c.type, DateTime)]

        # Extracting out the CPU intensive conversion to allow async offloading
        def _prepare_records(df_slice: typing.Any):
            df_clean = df_slice.copy()

            for col in df_clean.columns:
                if col in target_date_cols:
                    df_clean[col] = pd.to_datetime(df_clean[col], format="mixed", errors="coerce").dt.date
                elif col in target_datetime_cols:
                    df_clean[col] = pd.to_datetime(df_clean[col], format="mixed", errors="coerce")

            for col in df_clean.columns:
                col_dtype = df_clean[col].dtype
                is_numeric = isinstance(col_dtype, np.dtype) and (
                    col_dtype == "bool" or np.issubdtype(col_dtype, np.integer) or np.issubdtype(col_dtype, np.floating)
                )
                if is_numeric:
                    df_clean[col] = df_clean[col].astype(object).where(df_clean[col].notna(), None)
                elif col_dtype == "datetime64[ns]":
                    df_clean[col] = (
                        df_clean[col]
                        .dt.to_pydatetime()
                        .map(lambda v: v.replace(tzinfo=None) if v is not None else None)
                    )

            df_clean = df_clean.where(df_clean.notna(), None)

            records = df_clean.to_dict(orient="records")

            for record in records:
                for k, v in record.items():
                    if pd.api.types.is_scalar(v) and pd.isna(v):
                        record[k] = None
            return records

        records = await ThreadPoolManager().run_async(TaskType.CPU, _prepare_records, df_slice)

        stmt = pg_insert(table)
        update_cols = [c for c in columns if c not in pk_columns and c != "created_at" and c not in missing_cols]

        if not update_cols:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk_columns)
        else:
            null_protected = {c.name for c in table.columns if c.info.get("null_protected", False)}
            update_dict = {}
            for c in update_cols:
                excluded_val = getattr(stmt.excluded, c)
                if c in null_protected:
                    update_dict[c] = sa.func.coalesce(excluded_val, table.c[c])
                else:
                    update_dict[c] = excluded_val
            if has_updated_at:
                update_dict["updated_at"] = sa.func.now()
            stmt = stmt.on_conflict_do_update(
                index_elements=pk_columns,
                set_=update_dict,
            )

        start_time = time.perf_counter()
        try:
            total_written = 0

            if conn is not None:
                for i in range(0, len(records), _UPSERT_CHUNK_SIZE):
                    chunk = records[i : i + _UPSERT_CHUNK_SIZE]
                    await conn.execute(stmt, chunk)
                    total_written += len(chunk)
            else:
                async with self.engine.begin() as conn:
                    for i in range(0, len(records), _UPSERT_CHUNK_SIZE):
                        chunk = records[i : i + _UPSERT_CHUNK_SIZE]
                        await conn.execute(stmt, chunk)
                        total_written += len(chunk)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > _SLOW_UPSERT_THRESHOLD_MS:
                logger.warning(
                    f"[{self.__class__.__name__}] Slow UPSERT ({elapsed:.1f}ms, {total_written} rows): {table_name}",
                )
            else:
                logger.debug(
                    f"[{self.__class__.__name__}] UPSERT ({elapsed:.1f}ms, {total_written} rows): {table_name}",
                )

            return total_written
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
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during upsert, data not persisted: {e}"
                ) from e

            if suppress_errors:
                logger.warning(
                    f"[{self.__class__.__name__}] UPSERT Error ({elapsed:.1f}ms, suppressed) on {table_name}: {e}",
                )
            else:
                logger.error(
                    f"[{self.__class__.__name__}] UPSERT Error ({elapsed:.1f}ms) on {table_name}: {e}",
                    exc_info=True,
                )
            if not suppress_errors:
                raise
            return -1

    @staticmethod
    def _convert_param_for_asyncpg(val: typing.Any):
        """
        Convert Python values to types compatible with asyncpg.

        asyncpg requires strict type matching for DATE columns:
        - Expects datetime.date objects (with .toordinal() method)
        - String dates like '20260320' will cause DataError


        This method converts:
        - str dates in various formats -> datetime.date
        - Other types passed through unchanged
        """
        if val is None:
            return None

        if isinstance(val, str):
            try:
                clean_val = val.strip()
                if len(clean_val) == 8 and clean_val.isdigit():
                    return datetime.date(int(clean_val[:4]), int(clean_val[4:6]), int(clean_val[6:8]))
                elif (len(clean_val) == 10 and clean_val[4] == "-" and clean_val[7] == "-") or (
                    len(clean_val) == 10 and clean_val[4] == "/" and clean_val[7] == "/"
                ):
                    return datetime.date(int(clean_val[:4]), int(clean_val[5:7]), int(clean_val[8:10]))
                elif "T" in clean_val:
                    try:
                        import pandas as pd

                        return pd.to_datetime(clean_val).date()
                    except (ValueError, TypeError) as e:
                        logger.debug("[BaseDao] Pandas date parse skipped for '%s': %s", clean_val, e)
            except (ValueError, TypeError):
                logger.warning("[BaseDao] Failed to convert date string: %s", val)
                pass

        return val

    async def _read_db(
        self, sql: typing.Any, params: typing.Any = None, *, suppress_errors: bool = True, max_rows: int | None = None
    ):
        """Generic Read returning DataFrame (Offloaded CSV conversion)

        Args:
            sql: SQL query string
            params: Query parameters
            suppress_errors: If True, return empty DataFrame on error
            max_rows: Safety valve - if set, raises ValueError when result
                      exceeds this row count to prevent accidental full-table loads
        """
        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )

        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and getattr(CacheManager._instance, "_disposed", False):
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed, read rejected. "
                f"Call CacheManager.init_db() to reinitialize."
            )

        if params is not None and isinstance(params, list):
            params = tuple(params)

        if params:
            params = tuple(self._convert_param_for_asyncpg(p) for p in params)

        await self._get_maintenance_event().wait()

        start_time = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                # Execute raw SQL directly via driver to support native $1, $2 placeholders
                result = await conn.exec_driver_sql(sql, params or ())
                # Fetch all rows
                rows = result.fetchall()
                cols = list(result.keys())

                if max_rows is not None and len(rows) > max_rows:
                    raise ValueError(
                        f"[{self.__class__.__name__}] Query returned {len(rows)} rows, "
                        f"exceeding max_rows limit of {max_rows}. "
                        "Add WHERE filters or increase max_rows."
                    )

                # Offload DF creation
                df = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    pd.DataFrame,
                    rows,
                    columns=cols,
                )

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > _SLOW_READ_THRESHOLD_MS:
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
            raise
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
                raise EngineDisposedError(f"[{self.__class__.__name__}] Engine disposed during read: {e}") from e

            logger.warning(
                f"[{self.__class__.__name__}] Read Error ({elapsed:.1f}ms): {e}",
            )
            if not suppress_errors:
                raise
            return pd.DataFrame()

    async def _read_db_select(
        self,
        stmt: sa.Select,
        *,
        suppress_errors: bool = True,
    ):
        """Execute a SQLAlchemy Core select statement and return DataFrame.

        This is the preferred way to build dynamic queries — it uses
        SQLAlchemy's identifier quoting and parameter binding, eliminating
        SQL injection risk from f-string interpolation.
        """
        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )

        from data.cache.cache_manager import CacheManager

        if CacheManager._instance is not None and getattr(CacheManager._instance, "_disposed", False):
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed, read rejected. "
                f"Call CacheManager.init_db() to reinitialize."
            )

        await self._get_maintenance_event().wait()

        start_time = time.perf_counter()
        try:
            async with self.engine.connect() as conn:
                result = await conn.execute(stmt)
                rows = result.fetchall()
                cols = list(result.keys())

                df = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    pd.DataFrame,
                    rows,
                    columns=cols,
                )

                elapsed = (time.perf_counter() - start_time) * 1000
                if elapsed > _SLOW_READ_THRESHOLD_MS:
                    logger.warning(
                        f"[{self.__class__.__name__}] Slow Read ({elapsed:.1f}ms, {len(df)} rows): {str(stmt)[:200]}...",
                    )
                else:
                    logger.debug(
                        f"[{self.__class__.__name__}] Read ({elapsed:.1f}ms, {len(df)} rows): {str(stmt)[:200]}...",
                    )

                return df
        except asyncio.CancelledError:
            logger.warning(
                f"[{self.__class__.__name__}] Read cancelled during shutdown.",
            )
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
                    f"[{self.__class__.__name__}] DB Closed during read (Shutdown): {e}",
                )
                raise EngineDisposedError(f"[{self.__class__.__name__}] Engine disposed during read: {e}") from e

            logger.warning(
                f"[{self.__class__.__name__}] Read Error ({elapsed:.1f}ms): {e}",
            )
            if not suppress_errors:
                raise
            return pd.DataFrame()
