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

from data.persistence import engine_provider
from utils.error_classifier import classify_error, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.loop_local import get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


class EngineDisposedError(RuntimeError):
    """Raised when a DAO operation is attempted after the database engine has been disposed.

    This is a P0 data-safety guard: silently dropping writes or returning empty
    results on a disposed engine can cause data loss without any notification.
    Callers must catch this explicitly if they want graceful degradation.
    """


class DatabaseQueryError(RuntimeError):
    """Raised when a database query or write operation fails."""


_IN_CHUNK_SIZE = 500
_UPSERT_CHUNK_SIZE = 500
# review03-C2: 超过该行数时 _save_upsert 改为每块独立事务（UPSERT 幂等，重跑安全）
_LONG_TX_ROW_THRESHOLD = 20_000


class BaseDao:
    _maintenance_event = None

    def _check_engine(self, context: str | None = None) -> None:
        """Check if the engine is initialized and not disposed.

        Args:
            context: 可选操作上下文（如 "read"/"write"/"upsert"），用于生成
                带操作语义的 EngineDisposedError 消息（保持与历史内联检查一致）。

        Raises:
            RuntimeError: If engine is not initialized.
            EngineDisposedError: If engine has been disposed.
        """
        if self.engine is None:
            raise RuntimeError(
                f"[{self.__class__.__name__}] Engine not initialized. Call CacheManager.init_db() first."
            )
        # review03-C11 Step2: disposed 状态查询从 CacheManager._instance 迁移到
        # engine_provider（解除 data/persistence → data/cache 反向运行时查询）。
        # 按引擎身份判定：仅受管引擎受全局 disposed 影响，独立注入引擎不受影响。
        if engine_provider.is_disposed(self.engine):
            suffix = f", {context} rejected." if context else "."
            raise EngineDisposedError(
                f"[{self.__class__.__name__}] Engine disposed{suffix} Call CacheManager.init_db() to reinitialize."
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
            # P3-M5-ClassifyError-System-Gap: 用 classify_error 替代手写字符串匹配
            if classify_error(e, "db")["code"] == "interrupted":
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during guarded begin: {e}"
                ) from e
            raise

    @staticmethod
    async def _chunked_execute(
        db_fn,
        sql_template,
        values,
        *,
        chunk_size=_IN_CHUNK_SIZE,
        params_fn=None,
        start_idx=1,
        extra_params=None,
        conn: typing.Any = None,
        **db_kwargs,
    ) -> list[typing.Any]:
        """IN 子句分块执行的公共逻辑（ARCH-M5 / CQ-M4 代码去重）。

        处理：分块分割、占位符生成、SQL 模板调用，对每个分块调用
        ``db_fn(sql, params, **kwargs)`` 并收集返回值。

        返回类型为 ``list[Any]``（不同调用方 db_fn 返回类型各异，如 int /
        DataFrame）；若不标注，pyright 会把 ``gather(return_exceptions=True)``
        的结果推断为 ``list[Unknown | BaseException]``，污染调用方的类型检查。

        当 ``conn`` 显式传入时（共享事务连接场景），强制串行 for 循环执行分块：
        asyncpg 禁止单连接并发执行语句，并发会触发
        ``InterfaceError: another operation is in progress``
        （与 ``_save_upsert`` conn 分支同型）。

        Args:
            db_fn: async 函数 ``(sql, params, **kwargs) -> result``
            sql_template: 含 ``{placeholders}`` 标记的 SQL 字符串，或
                ``callable(placeholders, chunk_len[, start_idx]) -> sql_string``
            values: IN 子句的值列表
            chunk_size: 每块最大值数（默认 500）
            params_fn: ``callable(values_chunk) -> extra params list``，追加到值之后
            start_idx: 占位符起始索引（默认 1）
            extra_params: 前缀参数列表，前置到查询参数
            conn: 共享事务连接；非 None 时强制串行执行
            **db_kwargs: 透传给 db_fn 的额外关键字参数

        Returns:
            list：每个分块对应 db_fn 的返回值（保持顺序）。values 为空时返回 ``[]``。
        """
        if not values:
            return []

        extra_prefix = extra_params or []
        prefix_len = len(extra_prefix)
        actual_start_idx = start_idx if extra_params is None else prefix_len + 1

        import inspect

        template_takes_start_idx = False
        if callable(sql_template):
            try:
                sig = inspect.signature(sql_template)
                params_list = list(sig.parameters.values())
                pos_count = 0
                has_var_positional = False
                for p in params_list:
                    if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                        pos_count += 1
                    elif p.kind == inspect.Parameter.VAR_POSITIONAL:
                        has_var_positional = True
                template_takes_start_idx = (pos_count >= 3) or has_var_positional
            except (ValueError, TypeError):
                template_takes_start_idx = False

        def _build_sql(chunk, chunk_start_idx):
            placeholders = ",".join([f"${chunk_start_idx + j}" for j in range(len(chunk))])
            extra_suffix = params_fn(chunk) if params_fn else []
            if callable(sql_template):
                if template_takes_start_idx:
                    sql = sql_template(placeholders, len(chunk), chunk_start_idx)
                else:
                    sql = sql_template(placeholders, len(chunk))
            else:
                sql = sql_template.format(placeholders=placeholders)
            return sql, extra_prefix + chunk + extra_suffix

        # Shared transaction connection: asyncpg forbids concurrent ops on a single
        # connection, so chunks must execute serially (mirrors _save_upsert conn branch).
        if conn is not None:
            results = []
            for i in range(0, len(values), chunk_size):
                chunk = values[i : i + chunk_size]
                sql, params = _build_sql(chunk, actual_start_idx)
                result = await db_fn(sql, params, conn=conn, **db_kwargs)
                results.append(result)
            return results

        # No shared conn: spawn chunks concurrently under a semaphore bounded by pool size.
        try:
            from utils.config_handler import ConfigHandler

            pool_size = ConfigHandler.get_db_connection_pool_size()
            max_concurrent = max(1, pool_size - 2)
        except Exception:
            max_concurrent = 8  # 默认并发数
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _execute_chunk(chunk, chunk_start_idx):
            async with semaphore:
                sql, params = _build_sql(chunk, chunk_start_idx)
                return await db_fn(sql, params, **db_kwargs)

        chunk_tasks = []
        for i in range(0, len(values), chunk_size):
            chunk = values[i : i + chunk_size]
            chunk_tasks.append(_execute_chunk(chunk, actual_start_idx))

        # review03-C1: 读路径失败语义显式化（fail-fast）。
        # gather(return_exceptions=True) 使"块级异常"不再依赖隐式默认参数：
        #   1. 结果位置出现 CancelledError → 必须 raise（R2 红线，配合优雅停机）；
        #   2. 结果位置出现其他异常 → 抛 DatabaseQueryError 并丢弃部分结果，
        #      避免调用方拿到"看起来正常但缺块"的残缺数据集。
        results = await asyncio.gather(*chunk_tasks, return_exceptions=True)
        for r in results:
            if isinstance(r, asyncio.CancelledError):
                raise r
            # R5 红线：引擎释放异常必须原样传播，不包装为 DatabaseQueryError，
            # 否则调用方"EngineDisposedError → 降级/重连"路径被类型检查绕过。
            if isinstance(r, EngineDisposedError):
                raise r
            if isinstance(r, BaseException):
                raise DatabaseQueryError(
                    f"[{BaseDao.__name__}] Chunked read failed; partial results discarded "
                    f"to avoid silent data loss: {r}"
                ) from r
        return list(results)

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
            **read_db_kwargs: extra kwargs to pass to read_db_fn
                (review03-C1: suppress_errors 默认强制 False——块级读失败必须显式失败，
                而非被 _read_db 吞成空 DF 后过滤，否则部分块失败会静默返回残缺数据集)
        """
        # review03-C1: 读路径块失败显式化——禁止 _read_db 默认的 suppress_errors=True 吞错
        read_db_kwargs.setdefault("suppress_errors", False)
        results = await BaseDao._chunked_execute(
            read_db_fn,
            sql_template,
            values,
            chunk_size=chunk_size,
            params_fn=params_fn,
            start_idx=start_idx,
            extra_params=extra_params,
            **read_db_kwargs,
        )
        # review03-C1: 只收集 DataFrame 结果；BaseException 已被 _chunked_execute 显式排查
        all_results = [df for df in results if isinstance(df, pd.DataFrame) and not df.empty]
        if all_results:
            return pd.concat(all_results, ignore_index=True)
        return pd.DataFrame()

    @staticmethod
    async def chunked_in_write(
        write_db_fn,
        sql_template,
        values,
        *,
        chunk_size=_IN_CHUNK_SIZE,
        params_fn=None,
        start_idx=1,
        extra_params=None,
        conn: typing.Any = None,
        **write_db_kwargs,
    ):
        """
        Execute a write SQL with IN clause in chunks to avoid PostgreSQL parameter limit.

        Identical chunking logic to chunked_in_query but for write operations.
        Returns the total number of affected rows (sum of write_db_fn return values).

        Args:
            write_db_fn: async _write_db method (or equivalent)
            sql_template: SQL with {placeholders} marker or a callable(placeholders, chunk_len) -> sql_string
            values: list of values for the IN clause
            chunk_size: maximum items per IN clause (default 500)
            params_fn: callable(values_chunk) -> extra params list, appended after values
            start_idx: starting index for placeholders (default 1)
            extra_params: prefix parameters list to prepend to query arguments
            conn: shared transaction connection; when not None, chunks run serially
            **write_db_kwargs: extra kwargs to pass to write_db_fn
        """
        results = await BaseDao._chunked_execute(
            write_db_fn,
            sql_template,
            values,
            chunk_size=chunk_size,
            params_fn=params_fn,
            start_idx=start_idx,
            extra_params=extra_params,
            conn=conn,
            **write_db_kwargs,
        )
        total = 0
        for result in results:
            if isinstance(result, int):
                total += result
        return total

    async def _batch_get_with_as_of_date(
        self,
        sql_fn: typing.Callable[[typing.Any], tuple[typing.Any, typing.Callable[[list], list] | None]],
        ts_codes: list[str],
        as_of_date: typing.Any,
        log_prefix: str,
        *,
        post_process: typing.Callable[[pd.DataFrame], pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        """批量查询模板：消除 13 个 get_*_batch 方法的双分支重复（P3-Duplicate-Batch-Get）。

        封装空入参早返、try/except 异常兜底、post_process 后处理三个共性步骤。
        sql_fn 由调用方提供闭包，按 as_of_date 是否为 None 返回对应的
        (sql_template, params_fn) 元组——双分支选择下沉到调用方闭包，
        模板只负责异常兜底与统一日志。

        异常处理策略（与原 13 方法保持一致）：
          - asyncio.CancelledError：raise（R2 红线）
          - EngineDisposedError：raise（R5 红线）
          - 其他 Exception：logger.warning + 返回空 DataFrame

        Args:
            sql_fn: ``callable(as_of_date) -> (sql_template, params_fn)``
                sql_template 为 str 或 callable(placeholders, chunk_len, start_idx) -> str；
                params_fn 为 ``callable(chunk) -> list`` 或 None。
            ts_codes: 股票代码列表，空时直接返回空 DataFrame。
            as_of_date: 截止日期，原样传给 sql_fn。
            log_prefix: 异常日志前缀（如 "Failed to get express batch"）。
            post_process: 可选后处理回调，对非空 DataFrame 调用并返回处理后的 DataFrame。

        Returns:
            pd.DataFrame，查询返回的 DataFrame；查询异常或返回 None 时返回空 DataFrame。
        """
        if not ts_codes:
            return pd.DataFrame()

        try:
            sql_template, params_fn = sql_fn(as_of_date)
            kwargs: dict[str, typing.Any] = {}
            if params_fn is not None:
                kwargs["params_fn"] = params_fn
            df = await self.chunked_in_query(
                self._read_db,
                sql_template,
                ts_codes,
                **kwargs,
            )
            if post_process is not None and df is not None and not df.empty:
                df = post_process(df)
            return df if df is not None else pd.DataFrame()
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except DatabaseQueryError as e:
            # review03-C1: 分块查询部分失败 → fail-fast 已整体放弃（不返回残缺数据）。
            # 语义升级为 error：调用方可区分"查询失败"与"无数据"（leader 通过日志审计）。
            logger.error(
                "[%s] %s | Chunked read failed, partial results discarded: %s",
                self.__class__.__name__,
                log_prefix,
                DataSanitizer.sanitize_error(e),
            )
            return pd.DataFrame()
        except Exception as e:
            logger.warning(
                "[%s] %s: %s",
                self.__class__.__name__,
                log_prefix,
                DataSanitizer.sanitize_error(e),
            )
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

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _write_db(
        self,
        sql: typing.Any,
        params: typing.Any = None,
        suppress_errors: bool = False,
        conn: typing.Any = None,
    ):
        """Execute a single SQL statement. For bulk operations, use _save_upsert."""
        self._check_engine(context="write")

        if params:
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

        # P1-3: 共享事务连接（conn is not None）时，suppress_errors 必须忽略。
        # PostgreSQL 事务中发生错误后进入 "aborted" 状态，后续语句全部失败；
        # 若吞没异常，调用方无法感知事务损坏，可能 commit 部分写入导致数据不一致。
        # 调用方负责事务生命周期（如 _guarded_begin），异常必须传播以触发回滚。
        # 注意：else 分支使用 tx_conn 变量名，避免重新绑定 conn 导致异常分支误判。
        is_shared_conn = conn is not None
        start_time = time.perf_counter()
        try:
            if is_shared_conn:
                await conn.exec_driver_sql(sql, params)
            else:
                async with self.engine.begin() as tx_conn:
                    await tx_conn.exec_driver_sql(sql, params)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > PerfThreshold.DAO_WRITE_MS:
                logger.warning(
                    "[%s] Slow Write (%.1fms): %s...",
                    self.__class__.__name__,
                    elapsed,
                    sql[:200],
                )
            else:
                logger.debug(
                    "[%s] Write (%.1fms): %s...",
                    self.__class__.__name__,
                    elapsed,
                    sql[:200],
                )

            return 1
        except asyncio.CancelledError:
            logger.warning(
                "[%s] Write cancelled during shutdown.",
                self.__class__.__name__,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            # P3-M5-ClassifyError-System-Gap: 用 classify_error 替代手写字符串匹配
            if classify_error(e, "db")["code"] == "interrupted":
                logger.warning(
                    "[%s] DB Closed during write (Shutdown): %s",
                    self.__class__.__name__,
                    e,
                )
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during write, data not persisted: {e}"
                ) from e

            # P1-3: 共享事务连接（is_shared_conn=True）时，suppress_errors 必须忽略。
            # PostgreSQL 事务中发生错误后进入 "aborted" 状态，后续语句全部失败；
            # 若吞没异常，调用方无法感知事务损坏，可能 commit 部分写入导致数据不一致。
            # 调用方负责事务生命周期（如 _guarded_begin），异常必须传播以触发回滚。
            if is_shared_conn:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] Write Error on shared conn (%s: %s, %.1fms, suppress_errors ignored)\nSQL: %s...",
                    elapsed,
                    sql[:200],
                    exc_info=True,
                )
                raise DatabaseQueryError(
                    f"[{self.__class__.__name__}] Database write failed on shared conn: {e}"
                ) from e

            if suppress_errors:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] Write Error (%s: %s, %.1fms, suppressed)",
                    elapsed,
                )
            else:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] Write Error (%s: %s, %.1fms)\nSQL: %s...",
                    elapsed,
                    sql[:200],
                    exc_info=True,
                )

            if not suppress_errors:
                raise DatabaseQueryError(f"[{self.__class__.__name__}] Database write failed: {e}") from e
            return -1

    @staticmethod
    def _quote_columns(columns: typing.Any):
        """Quote column names for safe use in SQL (handles reserved words like 'date', 'on').

        Doubles any embedded double-quote characters to prevent injection.
        All current callers pass hardcoded schema column names, but this
        defensive measure guards against future misuse.
        """
        return ",".join(['"' + c.replace('"', '""') + '"' for c in columns])

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
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

        self._check_engine(context="upsert")

        import asyncio

        from data.persistence.models import Base

        await self._get_maintenance_event().wait()

        table = Base.metadata.tables.get(table_name)
        if table is None:
            logger.error(
                "[%s] Table %s not found in SQLAlchemy metadata.",
                self.__class__.__name__,
                table_name,
            )
            return 0

        has_updated_at = "updated_at" in table.columns

        missing_cols = [col for col in columns if col not in df.columns]
        if missing_cols:
            pk_missing = [col for col in missing_cols if col in pk_columns]
            if pk_missing:
                logger.error(
                    "[%s] Insert '%s': PK columns missing in dataframe, aborting: %s",
                    self.__class__.__name__,
                    table_name,
                    pk_missing,
                )
                return 0
            logger.warning(
                "[%s] Insert '%s': Missing columns in dataframe, filling with None: %s",
                self.__class__.__name__,
                table_name,
                missing_cols,
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

        # P1-3: 共享事务连接（conn is not None）时，suppress_errors 必须忽略。
        # PostgreSQL 事务中发生错误后进入 "aborted" 状态，后续语句全部失败；
        # 若吞没异常，调用方无法感知事务损坏，可能 commit 部分写入导致数据不一致。
        # 调用方负责事务生命周期（如 _guarded_begin），异常必须传播以触发回滚。
        # 注意：else 分支使用 tx_conn 变量名，避免重新绑定 conn 导致异常分支误判。
        is_shared_conn = conn is not None
        start_time = time.perf_counter()
        try:
            total_written = 0

            if is_shared_conn:
                for i in range(0, len(records), _UPSERT_CHUNK_SIZE):
                    chunk = records[i : i + _UPSERT_CHUNK_SIZE]
                    await conn.execute(stmt, chunk)
                    total_written += len(chunk)
            else:
                if len(records) > _LONG_TX_ROW_THRESHOLD:
                    # review03-C2: 超大批量时每块独立事务（依赖 UPSERT 幂等性保证重跑安全）。
                    # 避免数十秒级长事务阻塞 vacuum/DDL、增大取消时回滚成本；
                    # 中段失败时前 N 块已提交，可在日志看到已提交行数后重跑收敛。
                    for i in range(0, len(records), _UPSERT_CHUNK_SIZE):
                        chunk = records[i : i + _UPSERT_CHUNK_SIZE]
                        async with self.engine.begin() as tx_conn:
                            await tx_conn.execute(stmt, chunk)
                        total_written += len(chunk)
                else:
                    async with self.engine.begin() as tx_conn:
                        for i in range(0, len(records), _UPSERT_CHUNK_SIZE):
                            chunk = records[i : i + _UPSERT_CHUNK_SIZE]
                            await tx_conn.execute(stmt, chunk)
                            total_written += len(chunk)

            elapsed = (time.perf_counter() - start_time) * 1000
            if elapsed > PerfThreshold.DAO_UPSERT_MS:
                logger.warning(
                    "[%s] Slow UPSERT (%.1fms, %s rows): %s",
                    self.__class__.__name__,
                    elapsed,
                    total_written,
                    table_name,
                )
            else:
                logger.debug(
                    "[%s] UPSERT (%.1fms, %s rows): %s",
                    self.__class__.__name__,
                    elapsed,
                    total_written,
                    table_name,
                )

            return total_written
        except asyncio.CancelledError:
            logger.warning(
                "[%s] UPSERT cancelled during shutdown: %s",
                self.__class__.__name__,
                table_name,
            )
            # CancelledError is a control flow signal, MUST strictly propagate it
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000
            # P3-M5-ClassifyError-System-Gap: 用 classify_error 替代手写字符串匹配
            if classify_error(e, "db")["code"] == "interrupted":
                logger.warning(
                    "[%s] DB Closed during upsert (Shutdown): %s",
                    self.__class__.__name__,
                    e,
                )
                raise EngineDisposedError(
                    f"[{self.__class__.__name__}] Engine disposed during upsert, data not persisted: {e}"
                ) from e

            # P1-3: 共享事务连接（is_shared_conn=True）时，suppress_errors 必须忽略。
            # PostgreSQL 事务中发生错误后进入 "aborted" 状态，后续语句全部失败；
            # 若吞没异常，调用方无法感知事务损坏，可能 commit 部分写入导致数据不一致。
            # 调用方负责事务生命周期（如 _guarded_begin），异常必须传播以触发回滚。
            if is_shared_conn:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] UPSERT Error on shared conn (%s: %s, %.1fms, suppress_errors ignored) on %s",
                    elapsed,
                    table_name,
                    exc_info=True,
                )
                raise

            if suppress_errors:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] UPSERT Error (%s: %s, %.1fms, suppressed) on %s",
                    elapsed,
                    table_name,
                )
            else:
                log_classified(
                    logger,
                    e,
                    "db",
                    "[BaseDao] UPSERT Error (%s: %s, %.1fms) on %s",
                    elapsed,
                    table_name,
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

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _read_db(
        self, sql: typing.Any, params: typing.Any = None, *, suppress_errors: bool = True, max_rows: int | None = None
    ) -> pd.DataFrame:
        """Generic Read returning DataFrame (Offloaded CSV conversion)

        Args:
            sql: SQL query string
            params: Query parameters
            suppress_errors: If True (default), return empty DataFrame on error.
                Use suppress_errors=False for critical paths where "query failed"
                must not be confused with "no data".
            max_rows: Safety valve - if set, raises ValueError when result
                      exceeds this row count to prevent accidental full-table loads
        """
        self._check_engine(context="read")

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
        except asyncio.CancelledError:
            logger.warning(
                "[%s] Read cancelled during shutdown.",
                self.__class__.__name__,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000

            # P3-M5-ClassifyError-System-Gap: 用 classify_error 替代手写字符串匹配
            # Suppress connection-related errors during shutdown
            if classify_error(e, "db")["code"] == "interrupted":
                logger.warning(
                    "[%s] DB Closed during read (Shutdown): %s",
                    self.__class__.__name__,
                    e,
                )
                raise EngineDisposedError(f"[{self.__class__.__name__}] Engine disposed during read: {e}") from e

            log_classified(
                logger,
                e,
                "db",
                "[BaseDao] Read Error (%s: %s, %.1fms)",
                elapsed,
            )
            if not suppress_errors:
                raise DatabaseQueryError(f"[{self.__class__.__name__}] Database read failed: {e}") from e
            return pd.DataFrame()

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
        if elapsed > PerfThreshold.DAO_READ_MS:
            logger.warning(
                "[%s] Slow Read (%.1fms, %s rows): %s...",
                self.__class__.__name__,
                elapsed,
                len(df),
                sql[:200],
            )
        else:
            logger.debug(
                "[%s] Read (%.1fms, %s rows): %s...",
                self.__class__.__name__,
                elapsed,
                len(df),
                sql[:200],
            )

        return df

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def _read_db_select(
        self,
        stmt: sa.Select | sa.CompoundSelect,
        *,
        suppress_errors: bool = True,
        max_rows: int | None = None,
    ) -> pd.DataFrame:
        """Execute a SQLAlchemy Core select statement and return DataFrame.

        This is the preferred way to build dynamic queries — it uses
        SQLAlchemy's identifier quoting and parameter binding, eliminating
        SQL injection risk from f-string interpolation.

        Args:
            suppress_errors: 失败时是否吞错返回空 DataFrame（默认吞）。
            max_rows: 安全阀（review03-C4）——结果行数超限时抛 ValueError，
                防止无 WHERE/LIMIT 的查询意外物化全表。检查位于 except 之外，
                不受 suppress_errors=True 影响。
        """
        self._check_engine(context="read")

        await self._get_maintenance_event().wait()

        start_time = time.perf_counter()
        df: pd.DataFrame = pd.DataFrame()
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
                if elapsed > PerfThreshold.DAO_READ_MS:
                    logger.warning(
                        "[%s] Slow Read (%.1fms, %s rows): %s...",
                        self.__class__.__name__,
                        elapsed,
                        len(df),
                        str(stmt)[:200],
                    )
                else:
                    logger.debug(
                        "[%s] Read (%.1fms, %s rows): %s...",
                        self.__class__.__name__,
                        elapsed,
                        len(df),
                        str(stmt)[:200],
                    )
        except asyncio.CancelledError:
            logger.warning(
                "[%s] Read cancelled during shutdown.",
                self.__class__.__name__,
            )
            raise
        except Exception as e:
            elapsed = (time.perf_counter() - start_time) * 1000

            # P3-M5-ClassifyError-System-Gap: 用 classify_error 替代手写字符串匹配
            if classify_error(e, "db")["code"] == "interrupted":
                logger.warning(
                    "[%s] DB Closed during read (Shutdown): %s",
                    self.__class__.__name__,
                    e,
                )
                raise EngineDisposedError(f"[{self.__class__.__name__}] Engine disposed during read: {e}") from e

            log_classified(
                logger,
                e,
                "db",
                "[BaseDao] Read Error (%s: %s, %.1fms)",
                elapsed,
            )
            if not suppress_errors:
                raise DatabaseQueryError(f"[{self.__class__.__name__}] Database read failed: {e}") from e
            return pd.DataFrame()

        # review03-C4: max_rows 检查位于 except 之外——超限是编程/查询错误，
        # 不应被 suppress_errors=True 吞成"静默空结果"。
        if max_rows is not None and len(df) > max_rows:
            raise ValueError(
                f"[{self.__class__.__name__}] Query exceeded max_rows={max_rows} "
                f"(returned {len(df)} rows); refusing unbounded full-table materialization."
            )
        return df
