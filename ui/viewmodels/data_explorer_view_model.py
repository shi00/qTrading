import asyncio
import functools
import logging
import re

import pandas as pd

from utils.correlation import ensure_correlation_id
from utils.error_classifier import classify_error, classify_severity, get_error_message
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.thread_pool import TaskType, ThreadPoolManager

from data.persistence.database_manager import DatabaseManager

logger = logging.getLogger(__name__)

_NUMERIC_TYPE_PATTERN = re.compile(
    r"(INT|REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL)",
    re.IGNORECASE,
)

_DATE_VALUE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class DataExplorerViewModel:
    """ViewModel for DataExplorerView (MVVM-001 fix).

    Holds all business state for both TableViewerTab and SQLConsoleTab.
    No Flet dependencies. All DB access goes through DatabaseManager
    dispatched to ThreadPoolManager.
    """

    def __init__(
        self,
        db_manager: DatabaseManager | None = None,
        thread_pool: ThreadPoolManager | None = None,
    ):
        self._db = db_manager or DatabaseManager()
        self._tp = thread_pool or ThreadPoolManager()

        # Table Explorer State
        self.current_table: str = "stock_basic"
        self.current_page: int = 1
        self.page_size: int = 50
        self.total_rows: int = 0
        self.table_columns: list[str] = []
        self.numeric_cols: set[str] = set()
        self.sort_col_index: int | None = None
        self.sort_asc: bool = True
        self.filter_col: str | None = None
        self.filter_op: str = "="
        self.filter_val: str = ""
        self.is_loading: bool = False
        self.tables_list: list[str] = []
        self.tables_loaded: bool = False
        self.current_data: pd.DataFrame = pd.DataFrame()
        self.error_message: str | None = None

        self._disposed = False

        # SQL Console State
        self.sql_result: dict | None = None
        self.sql_is_executing: bool = False

    def dispose(self):
        """Release resources held by this ViewModel."""
        self._disposed = True
        self.current_data = pd.DataFrame()
        self.sql_result = None
        self.error_message = None
        self.tables_list = []
        self.table_columns = []
        self.numeric_cols = set()
        if self._db is not None:
            self._db.close()
            self._db = None  # type: ignore[assignment]

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def init_tables(self):
        """Load the list of all database tables."""
        ensure_correlation_id()
        try:
            tables = await self._tp.run_async(TaskType.CPU, self._db.get_all_tables)
            self.tables_list = tables
            self.tables_loaded = True
            if tables:
                self.current_table = "stock_basic" if "stock_basic" in tables else tables[0]
            else:
                self.current_table = ""
            return self.tables_list
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during init_tables.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in init_tables: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in init_tables: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in init_tables: {e}", exc_info=True)
            self.error_message = get_error_message(error_info)
            return []

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def load_table_schema(self, table_name: str):
        """Load column schema for a given table."""
        ensure_correlation_id()
        try:
            schema = await self._tp.run_async(TaskType.CPU, self._db.get_table_schema, table_name)
            new_columns = [col["name"] for col in schema]
            new_numeric = self._detect_numeric_cols(schema)
            self.table_columns = new_columns
            self.numeric_cols = new_numeric
            return schema
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during load_table_schema.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in load_table_schema: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in load_table_schema: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in load_table_schema: {e}", exc_info=True)
            self.error_message = get_error_message(error_info)
            return []

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def query_data(
        self,
        table_name: str | None = None,
        page: int | None = None,
        filters: list | None = None,
        sort_col_name: str | None = None,
        sort_ascending: bool | None = None,
    ):
        """Query table data with pagination, filters, and sorting."""
        ensure_correlation_id()
        if self._disposed:
            return self.current_data
        if self.is_loading:
            return self.current_data

        self.is_loading = True
        try:
            tbl = table_name or self.current_table
            pg = page if page is not None else self.current_page
            flt = filters if filters is not None else self._build_filters()
            sort = sort_col_name if sort_col_name is not None else self._resolve_sort_col_name()
            asc = sort_ascending if sort_ascending is not None else self.sort_asc

            count = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.get_table_count, tbl, flt),
            )
            self.total_rows = count

            df = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.query_table, tbl, pg, self.page_size, flt, sort, asc),
            )
            self.current_data = df
            if page is not None:
                self.current_page = page
            return self.current_data
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during query_data.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in query_data: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in query_data: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in query_data: {e}", exc_info=True)
            self.error_message = get_error_message(error_info)
            return self.current_data
        finally:
            self.is_loading = False

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def query_count(
        self,
        table_name: str | None = None,
        filters: list | None = None,
    ):
        """Query total row count for a table."""
        ensure_correlation_id()
        try:
            tbl = table_name or self.current_table
            flt = filters if filters is not None else self._build_filters()
            count = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.get_table_count, tbl, flt),
            )
            self.total_rows = count
            return count
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during query_count.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in query_count: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in query_count: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in query_count: {e}", exc_info=True)
            self.error_message = get_error_message(error_info)
            return 0

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def export_data(self, current_page_only: bool = True):
        """Export table data for CSV download."""
        ensure_correlation_id()
        if self._disposed:
            return pd.DataFrame()
        try:
            tbl = self.current_table
            flt = self._build_filters()
            sort = self._resolve_sort_col_name()
            asc = self.sort_asc
            pg = self.current_page
            ps = self.page_size

            if not current_page_only:
                pg = 1
                ps = 50000

            df = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.query_table, tbl, pg, ps, flt, sort, asc),
            )
            return df
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during export_data.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in export_data: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in export_data: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in export_data: {e}", exc_info=True)
            self.error_message = get_error_message(error_info)
            return pd.DataFrame()

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def execute_sql(self, sql: str):
        """Execute a read-only SQL query from the SQL Console."""
        ensure_correlation_id()
        if self._disposed:
            return {"success": False, "data": None, "error": "ViewModel disposed"}
        if not sql or not sql.strip():
            return {"success": False, "data": None, "error": "Empty query"}

        self.sql_is_executing = True
        try:
            result = await self._tp.run_async(TaskType.CPU, self._db.execute_sql, sql)
            self.sql_result = result
            return result
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during execute_sql.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical(f"[DataExplorerVM] SYSTEM-LEVEL failure in execute_sql: {e}", exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(f"[DataExplorerVM] Recoverable error ({error_info['code']}) in execute_sql: {e}")
            else:
                logger.error(f"[DataExplorerVM] Operational error in execute_sql: {e}", exc_info=True)
            self.sql_result = {"success": False, "data": None, "error": get_error_message(error_info)}
            return self.sql_result
        finally:
            self.sql_is_executing = False

    def set_filter(self, col: str, op: str, val: str):
        """Set the current filter parameters."""
        self.filter_col = col
        self.filter_op = op
        self.filter_val = val

    def set_sort(self, col_index: int | None, ascending: bool):
        """Set the current sort column index and direction."""
        if col_index is not None and not isinstance(col_index, int):
            logger.warning(
                f"[DataExplorerVM] set_sort received non-int col_index: {col_index!r}, ignoring.",
            )
            return
        self.sort_col_index = col_index
        self.sort_asc = ascending

    def reset_table_state(self):
        """Reset pagination, sort, and filter state for a table switch."""
        self.current_page = 1
        self.sort_col_index = None
        self.sort_asc = True
        self.filter_col = None
        self.filter_op = "="
        self.filter_val = ""
        self.error_message = None

    def clear_error(self):
        """Clear the current error message."""
        self.error_message = None

    def _resolve_sort_col_name(self) -> str | None:
        """Resolve sort column index to column name."""
        if isinstance(self.sort_col_index, int) and 0 <= self.sort_col_index < len(self.table_columns):
            return self.table_columns[self.sort_col_index]
        return None

    def _build_filters(self) -> list[tuple[str, str, str]]:
        """Build filter tuples from current filter state."""
        if self.filter_val and self.filter_col:
            val = self.filter_val
            if "date" in self.filter_col and _DATE_VALUE_PATTERN.match(val):
                val = val.replace("-", "")
            return [(self.filter_col, self.filter_op, val)]
        return []

    @staticmethod
    def _detect_numeric_cols(schema: list[dict]) -> set[str]:
        """Detect numeric columns from schema info."""
        result: set[str] = set()
        for col_info in schema:
            col_type = col_info.get("type", "")
            if _NUMERIC_TYPE_PATTERN.search(col_type):
                result.add(col_info["name"])
        return result
