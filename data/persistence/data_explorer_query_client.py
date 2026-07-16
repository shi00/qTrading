import logging
import re
import threading
import time
import typing

import pandas as pd
import sqlalchemy as sa
import sqlparse

from utils.config_handler import ConfigHandler
from utils.correlation import ensure_correlation_id
from utils.db_utils import get_db_pool_config
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_OP_MAP = {
    "=": lambda c, v: c == v,
    ">": lambda c, v: c > v,
    "<": lambda c, v: c < v,
    ">=": lambda c, v: c >= v,
    "<=": lambda c, v: c <= v,
    "!=": lambda c, v: c != v,
    "LIKE": lambda c, v: c.like(v),
}

# Task 2.3: SQL Console 重查询硬超时(固定常量,仅影响当前事务)。
# 通过 SET LOCAL 在 read-only 事务内设置,事务结束后自动回滚,不污染连接池。
_STATEMENT_TIMEOUT = "10s"


class DataExplorerQueryClient:
    """
    Manages database interactions for the Data Explorer feature.
    Provides read-only access (with safety checks) to the PostgreSQL database.

    P0-3: All queries are built using SQLAlchemy Core to completely
    eliminate f-string SQL injection vectors.

    Lazy Initialization: Engine is created on first use, not in __init__.
    This allows the application to start without a configured database URL.

    Resource Lifecycle: A shared class-level engine is used across all
    instances. ShutdownCoordinator can close it via close_all() during
    graceful shutdown (View will_unmount is not triggered on app exit).
    """

    # 类级别共享引擎：所有实例共用，受 _engine_lock 保护（双重检查锁定）
    _shared_engine: sa.Engine | None = None
    _engine_lock = threading.Lock()

    def __init__(self):
        pass

    @classmethod
    def close_all(cls) -> None:
        """关闭共享的同步引擎，供 ShutdownCoordinator 调用。

        幂等：多次调用安全。
        """
        with cls._engine_lock:
            if cls._shared_engine is not None:
                try:
                    cls._shared_engine.dispose()
                except Exception as e:  # noqa: BLE001
                    logger.warning("[DataExplorerQueryClient] close_all() failed: %s", e)
                cls._shared_engine = None

    def _ensure_engine(self):
        """
        Lazy initialization of the shared database engine (double-checked locking).
        Raises RuntimeError if database URL is not configured.
        """
        if DataExplorerQueryClient._shared_engine is not None:
            return

        with DataExplorerQueryClient._engine_lock:
            if DataExplorerQueryClient._shared_engine is None:
                db_url_async = ConfigHandler.get_db_url()
                if not db_url_async:
                    raise RuntimeError("Database URL is not configured. Please complete the onboarding wizard first.")

                db_url_sync = db_url_async.replace("+asyncpg", "")
                pool_config = get_db_pool_config()

                DataExplorerQueryClient._shared_engine = sa.create_engine(
                    db_url_sync,
                    echo=False,
                    **pool_config,
                )

    @property
    def _engine(self) -> sa.Engine | None:
        """只读属性，返回类级别共享引擎。"""
        return DataExplorerQueryClient._shared_engine

    def close(self):
        """实例级 close 为空操作。

        共享引擎的生命周期由 close_all() 统一管理。
        此方法仅保留接口兼容性，供 DataExplorerViewModel.dispose() 调用。
        """
        pass

    def get_all_tables(self):
        """
        Get a list of all tables in the database.
        Returns:
            list[str]: List of table names.

        Task 2.2: 异常自然抛出,由 VM 层 classify_error + sanitize_error 处理,
        不再折叠为空列表(区分 error/empty)。
        """
        self._ensure_engine()
        assert self._engine is not None
        insp = sa.inspect(self._engine)
        return sorted(insp.get_table_names())

    def get_table_schema(self, table_name: str):
        """
        Get the schema (column names and types) for a specific table.
        Returns:
            list[dict]: List of column info {'name': 'col_name', 'type': 'TEXT'}

        Task 2.2: 异常自然抛出,由 VM 层 classify_error + sanitize_error 处理,
        不再折叠为空列表(区分 error/empty)。
        """
        self._ensure_engine()
        self._validate_table_name(table_name)
        assert self._engine is not None
        insp = sa.inspect(self._engine)
        columns = []
        for col_info in insp.get_columns(table_name):
            columns.append(
                {"name": col_info["name"], "type": str(col_info["type"])},
            )
        return columns

    def get_table_count(self, table_name: str, filters: list | None = None):
        """
        Get total row count for a table, optionally with filters.

        Args:
            table_name (str): Name of the table.
            filters (list): List of filter tuples (column, operator, value).
                            Example: [('ts_code', '=', '000001.SZ')]

        Task 2.2: 异常自然抛出,由 VM 层 classify_error + sanitize_error 处理,
        不再折叠为 0(区分 error/empty)。
        """
        self._ensure_engine()
        self._validate_table_name(table_name)
        tbl = sa.table(table_name)
        stmt = sa.select(sa.func.count()).select_from(tbl)

        if filters:
            schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
            stmt = self._apply_filters(stmt, filters, schema_cols=schema_cols)

        with self._engine.connect() as conn:  # type: ignore[union-attr]
            result = conn.execute(stmt)
            return result.scalar() or 0

    def _validate_table_name(self, table_name: str):
        """Ensure table name is valid and exists to prevent injection."""
        allowed_tables = self.get_all_tables()
        if table_name not in allowed_tables:
            raise ValueError(f"Invalid table name: {table_name}")

    @staticmethod
    def _apply_filters(stmt: typing.Any, filters: list | None, schema_cols: typing.Any = None):
        """Apply WHERE filters using SQLAlchemy Core operators (zero f-string).
        schema_cols: optional set of valid column names for whitelist validation."""
        if not filters:
            return stmt
        for col_name, op, val in filters:
            # Whitelist validation: reject columns not in schema (mirrors sort_col check)
            if schema_cols and col_name not in schema_cols:
                logger.warning(
                    "[DataExplorerQueryClient] Filter column '%s' not in schema, skipped.",
                    col_name,
                )
                continue
            op_func = _OP_MAP.get(op)
            if op_func is None:
                continue  # Skip unsupported operator
            col_obj = sa.column(col_name)
            # Handle LIKE wildcards
            if op == "LIKE" and "%" not in str(val):
                val = f"%{val}%"
            stmt = stmt.where(op_func(col_obj, val))
        return stmt

    def query_table(
        self,
        table_name: str,
        page: typing.Any = 1,
        page_size: typing.Any = 50,
        filters: list | None = None,
        sort_col: typing.Any = None,
        sort_asc: typing.Any = True,
    ):
        """
        Query data from a table with pagination and filtering.
        All SQL is built via SQLAlchemy Core — zero f-string concatenation.

        Returns:
            pd.DataFrame: DataFrame containing the result rows.

        Task 2.2: 异常自然抛出,由 VM 层 classify_error + sanitize_error 处理,
        不再折叠为空 DataFrame(区分 error/empty)。成功空查询返回空 DataFrame。
        """
        self._ensure_engine()
        self._validate_table_name(table_name)

        offset = (page - 1) * page_size
        tbl = sa.table(table_name)
        stmt = sa.select(sa.text("*")).select_from(tbl)

        # 1. Apply Filters
        schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
        if filters:
            stmt = self._apply_filters(stmt, filters, schema_cols=schema_cols)

        # 2. Apply Sorting
        if sort_col:
            # Validate sort_col exists in the table schema
            schema_cols = {c["name"] for c in self.get_table_schema(table_name)}
            if sort_col in schema_cols:
                col_obj = sa.column(sort_col)
                stmt = stmt.order_by(
                    sa.asc(col_obj) if sort_asc else sa.desc(col_obj),
                )
        elif table_name == "daily_quotes":
            # Default sort for common tables
            stmt = stmt.order_by(
                sa.desc(sa.column("trade_date")),
                sa.asc(sa.column("ts_code")),
            )

        # 3. Apply Pagination
        stmt = stmt.limit(page_size).offset(offset)

        with self._engine.connect() as conn:  # type: ignore[union-attr]
            result = conn.execute(stmt)
            rows = result.fetchall()
            if not rows:
                return pd.DataFrame()
            cols = list(result.keys())
            return pd.DataFrame(rows, columns=cols)

    def execute_sql(self, sql_query: typing.Any):
        """
        Execute a raw SQL query from the SQL Console.
        Includes safety checks to prevent modification queries and memory exhaustion.

        NOTE: This method intentionally accepts raw SQL (user's explicit intent).
        Defense layers: sqlparse SELECT-only check + read-only connection
        + statement_timeout (Task 2.3) + DataSanitizer.sanitize_error (Task 2.3).

        Returns:
            dict: {
                'success': bool,
                'data': pd.DataFrame or None,
                'error': str or None,
                'rows_affected': int (0 for select)
            }
        """
        # 0. Ensure engine is initialized
        try:
            self._ensure_engine()
        except RuntimeError as e:
            return {"success": False, "data": None, "error": str(e)}

        # 1. Security Check with sqlparse
        try:
            parsed = sqlparse.parse(sql_query)
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": f"SQL Parse Error: {e!s}",
            }

        if not parsed:
            return {"success": False, "data": None, "error": "Empty query"}

        for statement in parsed:
            if statement.get_type() != "SELECT":
                return {
                    "success": False,
                    "data": None,
                    "error": f"Security Alert: Only SELECT statements are allowed. Found: {statement.get_type()}",
                }

        # 1.5. Defense-in-depth: Block dangerous keywords even if sqlparse misclassifies
        # P0-2: Use regex word-boundary matching instead of trailing-space strings.
        # Trailing-space matching ("DROP ") can be bypassed with tabs, newlines,
        # or comments: DROP\ttable, DROP\n table, DROP(--comment)table.
        _DANGEROUS_KEYWORD_PATTERN = re.compile(
            r"\b(DROP|DELETE|INSERT|UPDATE|ALTER|CREATE|TRUNCATE|EXECUTE|GRANT|REVOKE)\b",
            re.IGNORECASE,
        )
        match = _DANGEROUS_KEYWORD_PATTERN.search(sql_query)
        if match:
            return {
                "success": False,
                "data": None,
                "error": f"Security Alert: Dangerous keyword '{match.group(1).upper()}' detected in query.",
            }

        # 2. Execute with strictly Read-Only transaction and Memory Protection
        # P0-1: Use REPEATABLE READ + SET TRANSACTION READ ONLY instead of
        # AUTOCOMMIT.  AUTOCOMMIT is NOT read-only — it auto-commits every
        # statement, allowing writes to slip through if keyword/sqlparse checks
        # are bypassed (e.g. SELECT ... INTO, COPY ... TO PROGRAM).
        # Task 2.3: SET LOCAL statement_timeout 仅影响当前事务,事务结束后自动回滚,
        # 为重查询提供数据库侧硬超时,防止卡死连接池。
        cid = ensure_correlation_id()
        start_ts = time.perf_counter()
        truncated = False
        success = False
        conn = None
        try:
            with self._engine.connect() as conn:  # type: ignore[union-attr]
                conn = conn.execution_options(isolation_level="REPEATABLE READ")
                with conn.begin():
                    # 顺序: READ ONLY → statement_timeout → 用户 SQL
                    conn.execute(sa.text("SET TRANSACTION READ ONLY"))
                    conn.execute(sa.text(f"SET LOCAL statement_timeout = '{_STATEMENT_TIMEOUT}'"))
                    result = conn.execute(sa.text(sql_query))

                    # Protection: Fetch at most 2000 rows to prevent memory explosion (DoS)
                    MAX_FETCH = 2000

                    cols = list(result.keys())
                    rows = result.fetchmany(MAX_FETCH)
                    truncated = len(rows) >= MAX_FETCH

                    df = pd.DataFrame(rows, columns=cols)
                    success = True

                    return {
                        "success": True,
                        "data": df,
                        "error": None
                        if not truncated
                        else f"Warning: Result truncated to {MAX_FETCH} rows for performance.",
                    }
        except Exception as e:
            # Task 2.3: 错误消息经 DataSanitizer.sanitize_error 脱敏,避免泄露
            # URL 凭证/路径/已注册 secret(R9)。返回结构化 dict,非 str(e)。
            sanitized = DataSanitizer.sanitize_error(e)
            logger.warning(
                "[DataExplorerQueryClient] execute_sql failed. cid=%s duration_ms=%.1f",
                cid,
                (time.perf_counter() - start_ts) * 1000,
            )
            return {"success": False, "data": None, "error": sanitized}
        finally:
            # Task 2.3: 日志只记录 correlation id/耗时/成功失败/截断状态,
            # 不记录 SQL 内容或错误细节(避免泄露查询语义/敏感信息)。
            if success:
                logger.info(
                    "[DataExplorerQueryClient] execute_sql ok. cid=%s duration_ms=%.1f truncated=%s",
                    cid,
                    (time.perf_counter() - start_ts) * 1000,
                    truncated,
                )
