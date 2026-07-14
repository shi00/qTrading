import asyncio
import functools
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from utils.correlation import ensure_correlation_id
from utils.error_classifier import classify_error, classify_severity, get_error_message
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.thread_pool import TaskType, ThreadPoolManager

from data.persistence.data_explorer_query_client import DataExplorerQueryClient
from ui.viewmodels import Message

logger = logging.getLogger(__name__)

_NUMERIC_TYPE_PATTERN = re.compile(
    r"(INT|REAL|FLOAT|DOUBLE|NUMERIC|DECIMAL)",
    re.IGNORECASE,
)

_DATE_VALUE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class TableRow:
    """表数据行 (L771: frozen dataclass). values 与 state.table_columns 按索引对齐."""

    values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class SqlResultRow:
    """SQL 结果行 (L771: frozen dataclass). values 与 state.sql_result_columns 按索引对齐."""

    values: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DataExplorerState:
    """DataExplorerViewModel 的不可变状态快照 (L771 合规).

    所有业务数据直接放入 state, 用 tuple[Row, ...] 替代 DataFrame/dict.
    View = f(state), 无 dual-track.
    """

    # Table Explorer State
    current_table: str = "stock_basic"
    current_page: int = 1
    page_size: int = 50
    total_rows: int = 0
    sort_col_index: int | None = None
    sort_asc: bool = True
    filter_col: str | None = None
    filter_op: str = "="
    filter_val: str = ""
    is_loading: bool = False
    tables_loaded: bool = False
    error_message: Message | None = None
    # 轻量集合状态(tuple/frozenset 替代 list/set)
    tables_list: tuple[str, ...] = ()
    table_columns: tuple[str, ...] = ()
    numeric_cols: frozenset[str] = frozenset()
    # 业务数据 (L771 合规: tuple[Row, ...], 直接暴露)
    table_rows: tuple[TableRow, ...] = ()
    # SQL Console State
    sql_is_executing: bool = False
    sql_success: bool = False
    sql_result_columns: tuple[str, ...] = ()
    sql_result_rows: tuple[SqlResultRow, ...] = ()
    # NOTE(lazy): sql_error 为已翻译字符串(VM 间接感知 locale). ceiling: Phase 2 locale 修复仅覆盖 state 字段. upgrade: sql_error 改为 Message 或 i18n key + format_args 透传待 Phase R.2.3 执行.
    sql_error: str | None = None


class DataExplorerViewModel:
    """ViewModel for DataExplorerView (MVVM-001 fix, 声明式形态).

    Holds all business state for both TableViewerTab and SQLConsoleTab.
    No Flet dependencies. All DB access goes through DataExplorerQueryClient
    dispatched to ThreadPoolManager.

    形态契约(CLAUDE.md §3.2 + CONTRIBUTING.md L771):
    - 全部业务状态(含 DataFrame/dict 派生数据)封装为 frozen `DataExplorerState`
      的 tuple[Row, ...] 字段;
    - View = f(state), 无 dual-track property 拉取/version 通知.
    """

    def __init__(
        self,
        db_manager: DataExplorerQueryClient | None = None,
        thread_pool: ThreadPoolManager | None = None,
    ):
        self._db = db_manager or DataExplorerQueryClient()
        self._tp = thread_pool or ThreadPoolManager()

        # Internal state (frozen snapshot)
        self._state = DataExplorerState()
        self._subscribers: list[Callable[[DataExplorerState], None]] = []

        self._disposed = False

    @property
    def state(self) -> DataExplorerState:
        return self._state

    def subscribe(self, callback: Callable[[DataExplorerState], None]) -> Callable[[], None]:
        """订阅 state 变更,返回取消订阅函数。"""
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            try:
                cb(self._state)
            except Exception as e:
                logger.warning("[DataExplorerVM] Subscriber error: %s", e, exc_info=True)

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self):
        """Release resources held by this ViewModel."""
        if self._disposed:
            return
        self._disposed = True
        self._set_state(
            tables_list=(),
            table_columns=(),
            numeric_cols=frozenset(),
            table_rows=(),
            tables_loaded=False,
            error_message=None,
            sql_success=False,
            sql_result_columns=(),
            sql_result_rows=(),
            sql_error=None,
        )
        if self._db is not None:
            self._db.close()
            self._db = None  # type: ignore[assignment]
        self._subscribers.clear()

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def init_tables(self):
        """Load the list of all database tables."""
        ensure_correlation_id()
        if self._disposed:
            return []
        try:
            tables = await self._tp.run_async(TaskType.CPU, self._db.get_all_tables)
            if tables:
                current_table = "stock_basic" if "stock_basic" in tables else tables[0]
            else:
                current_table = ""
            self._set_state(
                tables_list=tuple(tables),
                tables_loaded=True,
                current_table=current_table,
            )
            return self._state.tables_list
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during init_tables.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in init_tables: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in init_tables: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in init_tables: %s", e, exc_info=True)
            self._set_state(
                error_message=Message(
                    error_info.get("message_key", "common_err_unknown"),
                    error_info.get("format_args") or {},
                )
            )
            return []

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def load_table_schema(self, table_name: str):
        """Load column schema for a given table."""
        ensure_correlation_id()
        if self._disposed:
            return []
        try:
            schema = await self._tp.run_async(TaskType.CPU, self._db.get_table_schema, table_name)
            new_columns = [col["name"] for col in schema]
            new_numeric = self._detect_numeric_cols(schema)
            self._set_state(
                table_columns=tuple(new_columns),
                numeric_cols=frozenset(new_numeric),
            )
            return schema
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during load_table_schema.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in load_table_schema: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in load_table_schema: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in load_table_schema: %s", e, exc_info=True)
            self._set_state(
                error_message=Message(
                    error_info.get("message_key", "common_err_unknown"),
                    error_info.get("format_args") or {},
                )
            )
            return []

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def query_data(
        self,
        table_name: str | None = None,
        page: int | None = None,
        filters: list | None = None,
        sort_col_name: str | None = None,
        sort_ascending: bool | None = None,
    ) -> pd.DataFrame:
        """Query table data with pagination, filters, and sorting.

        返回原始 DataFrame (供测试/export 场景), 同时将数据转换为
        ``tuple[TableRow, ...]`` 写入 ``state.table_rows`` (供 View 渲染).
        """
        ensure_correlation_id()
        if self._disposed:
            return pd.DataFrame()
        if self._state.is_loading:
            return pd.DataFrame()

        self._set_state(is_loading=True)
        try:
            tbl = table_name or self._state.current_table
            pg = page if page is not None else self._state.current_page
            flt = filters if filters is not None else self._build_filters()
            sort = sort_col_name if sort_col_name is not None else self._resolve_sort_col_name()
            asc = sort_ascending if sort_ascending is not None else self._state.sort_asc

            count = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.get_table_count, tbl, flt),
            )

            df = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.query_table, tbl, pg, self._state.page_size, flt, sort, asc),
            )
            # 声明式: 数据写入 state.table_rows (L771 合规, tuple[Row, ...])
            # 重读 self._state 拿最新 snapshot 避免 await 期间竞态 (race safety)
            columns = self._state.table_columns
            changes: dict[str, Any] = {
                "total_rows": count,
                "table_rows": _df_to_table_rows(df, columns),
            }
            if page is not None:
                changes["current_page"] = page
            self._set_state(**changes)
            return df
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during query_data.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in query_data: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in query_data: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in query_data: %s", e, exc_info=True)
            self._set_state(
                error_message=Message(
                    error_info.get("message_key", "common_err_unknown"),
                    error_info.get("format_args") or {},
                )
            )
            return pd.DataFrame()
        finally:
            self._set_state(is_loading=False)

    @log_async_operation(threshold_ms=PerfThreshold.DB_SINGLE_QUERY)
    async def query_count(
        self,
        table_name: str | None = None,
        filters: list | None = None,
    ):
        """Query total row count for a table."""
        ensure_correlation_id()
        if self._disposed:
            return 0
        try:
            tbl = table_name or self._state.current_table
            flt = filters if filters is not None else self._build_filters()
            count = await self._tp.run_async(
                TaskType.CPU,
                functools.partial(self._db.get_table_count, tbl, flt),
            )
            self._set_state(total_rows=count)
            return count
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during query_count.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in query_count: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in query_count: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in query_count: %s", e, exc_info=True)
            self._set_state(
                error_message=Message(
                    error_info.get("message_key", "common_err_unknown"),
                    error_info.get("format_args") or {},
                )
            )
            return 0

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def export_data(self, current_page_only: bool = True):
        """Export table data for CSV download."""
        ensure_correlation_id()
        if self._disposed:
            return pd.DataFrame()
        try:
            tbl = self._state.current_table
            flt = self._build_filters()
            sort = self._resolve_sort_col_name()
            asc = self._state.sort_asc
            pg = self._state.current_page
            ps = self._state.page_size

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
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in export_data: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in export_data: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in export_data: %s", e, exc_info=True)
            self._set_state(
                error_message=Message(
                    error_info.get("message_key", "common_err_unknown"),
                    error_info.get("format_args") or {},
                )
            )
            return pd.DataFrame()

    @log_async_operation(threshold_ms=PerfThreshold.DB_BULK_IO)
    async def execute_sql(self, sql: str) -> dict:
        """Execute a read-only SQL query from the SQL Console.

        返回原始 dict ``{success, data, error}`` (供测试/状态显示), 同时将结果
        转换为 ``sql_success``/``sql_result_columns``/``sql_result_rows``/``sql_error``
        写入 state (供 View 渲染).
        """
        ensure_correlation_id()
        if self._disposed:
            return {"success": False, "data": None, "error": "ViewModel disposed"}
        if not sql or not sql.strip():
            return {"success": False, "data": None, "error": "Empty query"}

        self._set_state(sql_is_executing=True)
        try:
            result = await self._tp.run_async(TaskType.CPU, self._db.execute_sql, sql)
            # 声明式: 结果写入 state (L771 合规, tuple[Row, ...])
            self._set_state(**_sql_result_to_state_fields(result))
            return result
        except asyncio.CancelledError:
            logger.warning("[DataExplorerVM] Cancelled during execute_sql.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="db")
            severity = classify_severity(e, context="db")
            if severity == "system":
                logger.critical("[DataExplorerVM] SYSTEM-LEVEL failure in execute_sql: %s", e, exc_info=True)
                raise
            elif severity == "recoverable":
                logger.warning(
                    "[DataExplorerVM] Recoverable error (%s) in execute_sql: %s",
                    error_info["code"],
                    e,
                    exc_info=True,
                )
            else:
                logger.error("[DataExplorerVM] Operational error in execute_sql: %s", e, exc_info=True)
            # NOTE(lazy): sql_error 为已翻译字符串(VM 间接感知 locale). ceiling: Phase 2 locale 修复仅覆盖 state 字段. upgrade: sql_error 改为 Message 或 i18n key + format_args 透传待 Phase R.2.3 执行.
            error_msg = get_error_message(error_info)
            error_result = {"success": False, "data": None, "error": error_msg}
            self._set_state(**_sql_result_to_state_fields(error_result))
            return error_result
        finally:
            self._set_state(sql_is_executing=False)

    def set_filter(self, col: str, op: str, val: str):
        """Set the current filter parameters."""
        self._set_state(filter_col=col, filter_op=op, filter_val=val)

    def set_sort(self, col_index: int | None, ascending: bool):
        """Set the current sort column index and direction."""
        if col_index is not None and not isinstance(col_index, int):
            logger.warning(
                "[DataExplorerVM] set_sort received non-int col_index: %r, ignoring.",
                col_index,
            )
            return
        self._set_state(sort_col_index=col_index, sort_asc=ascending)

    def set_table(self, table_name: str) -> None:
        """Set the current table name(View 调用,替代直接属性写入)。"""
        self._set_state(current_table=table_name)

    def mark_tables_stale(self) -> None:
        """标记 tables 为 stale,强制下次 mount 时重新加载(broadcast 消息触发)。"""
        self._set_state(tables_loaded=False)

    def reset_table_state(self):
        """Reset pagination, sort, and filter state for a table switch."""
        self._set_state(
            current_page=1,
            sort_col_index=None,
            sort_asc=True,
            filter_col=None,
            filter_op="=",
            filter_val="",
            error_message=None,
        )

    def clear_error(self):
        """Clear the current error message."""
        self._set_state(error_message=None)

    def _resolve_sort_col_name(self) -> str | None:
        """Resolve sort column index to column name."""
        idx = self._state.sort_col_index
        cols = self._state.table_columns
        if isinstance(idx, int) and 0 <= idx < len(cols):
            return cols[idx]
        return None

    def _build_filters(self) -> list[tuple[str, str, str]]:
        """Build filter tuples from current filter state."""
        if self._state.filter_val and self._state.filter_col:
            val = self._state.filter_val
            if "date" in self._state.filter_col and _DATE_VALUE_PATTERN.match(val):
                val = val.replace("-", "")
            return [(self._state.filter_col, self._state.filter_op, val)]
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


# ============================================================================
# Module-level pure conversion functions (L771 合规: DataFrame/dict → tuple[Row, ...])
# ============================================================================


def _df_to_table_rows(df: pd.DataFrame, columns: tuple[str, ...]) -> tuple[TableRow, ...]:
    """DataFrame → tuple[TableRow, ...] (L771 合规).

    values 与 columns 按索引对齐; DataFrame 缺列时用 None 占位.
    """
    if df is None or df.empty or not columns:
        return ()
    return tuple(TableRow(values=tuple(row.get(col) for col in columns)) for _, row in df.iterrows())


def _sql_result_to_state_fields(result: dict) -> dict[str, Any]:
    """execute_sql 返回的 dict → DataExplorerState 字段 dict (供 _set_state 使用).

    将 ``{success, data, error}`` 转换为 ``sql_success``/``sql_result_columns``/
    ``sql_result_rows``/``sql_error`` 不可变字段.
    """
    success = bool(result.get("success", False))
    data = result.get("data")
    error = result.get("error")

    if data is None or (isinstance(data, pd.DataFrame) and data.empty):
        return {
            "sql_success": success,
            "sql_result_columns": (),
            "sql_result_rows": (),
            "sql_error": error,
        }

    # data 预期为 pd.DataFrame
    columns = tuple(str(col) for col in data.columns)
    rows = tuple(SqlResultRow(values=tuple(row[col] for col in data.columns)) for _, row in data.iterrows())
    return {
        "sql_success": success,
        "sql_result_columns": columns,
        "sql_result_rows": rows,
        "sql_error": error,
    }
