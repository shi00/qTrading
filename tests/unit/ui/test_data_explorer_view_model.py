"""Unit tests for DataExplorerViewModel — 声明式形态(frozen state + tuple[Row, ...])。

VM 改造后(CLAUDE.md §3.2 + CONTRIBUTING.md L771):
- 全部业务状态封装为 frozen `DataExplorerState`(tuple/frozenset 替代 list/set);
- 大体积数据(DataFrame/dict 派生)转换为 tuple[TableRow, ...]/tuple[SqlResultRow, ...]
  直接放入 state, 无 dual-track property 拉取/version 通知.
"""

import asyncio
import functools
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from ui.viewmodels import Message
from ui.viewmodels.data_explorer_view_model import (
    DataExplorerState,
    DataExplorerViewModel,
    SqlResultRow,
    TableRow,
)
from utils.thread_pool import TaskType

pytestmark = pytest.mark.unit

# --- Fixtures ---


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.get_all_tables = MagicMock(return_value=["daily_quotes", "stock_basic"])
    db.get_table_schema = MagicMock(return_value=[])
    db.get_table_count = MagicMock(return_value=0)
    db.query_table = MagicMock(return_value=pd.DataFrame())
    db.execute_sql = MagicMock(return_value={"success": True, "data": pd.DataFrame(), "error": None})
    db.close = MagicMock()
    return db


@pytest.fixture
def mock_tp(mock_db):
    """ThreadPoolManager mock that actually delegates to mock_db methods."""
    tp = MagicMock()

    async def _run_async(task_type, func, *args, **kwargs):
        # Handle functools.partial by calling it directly
        if isinstance(func, functools.partial):
            return func()
        # Handle direct method calls with args
        if asyncio.iscoroutinefunction(func):
            return await func(*args, **kwargs)
        return func(*args, **kwargs)

    tp.run_async = AsyncMock(side_effect=_run_async)
    return tp


@pytest.fixture
def vm(mock_db, mock_tp):
    return DataExplorerViewModel(db_manager=mock_db, thread_pool=mock_tp)


# --- Test Classes ---


class TestInit:
    def test_default_dependencies_created(self):
        """When no args passed, ViewModel creates default DataExplorerQueryClient and ThreadPoolManager."""
        vm = DataExplorerViewModel()
        assert vm._db is not None
        assert vm._tp is not None

    def test_constructor_injection(self, mock_db, mock_tp):
        vm = DataExplorerViewModel(db_manager=mock_db, thread_pool=mock_tp)
        assert vm._db is mock_db
        assert vm._tp is mock_tp

    def test_initial_state(self, vm):
        """frozen state 初始快照断言。"""
        s = vm.state
        assert isinstance(s, DataExplorerState)
        assert s.current_table == "stock_basic"
        assert s.current_page == 1
        assert s.page_size == 50
        assert s.total_rows == 0
        assert s.table_columns == ()
        assert s.numeric_cols == frozenset()
        assert s.sort_col_index is None
        assert s.sort_asc is True
        assert s.filter_col is None
        assert s.filter_op == "="
        assert s.filter_val == ""
        assert s.is_loading is False
        assert s.tables_list == ()
        assert s.tables_loaded is False
        assert s.error_message is None
        assert s.sql_is_executing is False
        # 声明式: 业务数据直接放入 state (tuple[Row, ...])
        assert s.table_rows == ()
        assert s.sql_success is False
        assert s.sql_result_columns == ()
        assert s.sql_result_rows == ()
        assert s.sql_error is None


class TestSubscribe:
    def test_subscribe_returns_unsubscribe_and_removes_callback(self, vm):
        """subscribe 返回 unsubscribe 函数,调用后移除回调。"""
        received: list[DataExplorerState] = []
        unsubscribe = vm.subscribe(lambda s: received.append(s))
        assert callable(unsubscribe)

        vm._set_state(current_page=3)
        assert len(received) == 1
        assert received[0].current_page == 3

        unsubscribe()
        vm._set_state(current_page=5)
        assert len(received) == 1  # 不再接收通知

    def test_notify_swallows_subscriber_exceptions(self, vm):
        """_notify 捕获订阅者异常,不影响其他订阅者。"""
        received: list[DataExplorerState] = []
        vm.subscribe(lambda s: (_ for _ in ()).throw(RuntimeError("boom")))  # type: ignore[func-returns-value]
        vm.subscribe(lambda s: received.append(s))
        vm._set_state(current_page=2)
        assert len(received) == 1


class TestDispose:
    def test_dispose_clears_state(self, vm):
        vm._set_state(
            tables_list=("t1",),
            table_columns=("col1",),
            error_message=Message("err"),
            table_rows=(TableRow(values=(1,)),),
            sql_success=True,
            sql_result_columns=("a",),
            sql_result_rows=(SqlResultRow(values=(1,)),),
            sql_error="boom",
        )
        vm.dispose()
        assert vm.state.tables_list == ()
        assert vm.state.table_columns == ()
        assert vm.state.error_message is None
        assert vm.state.table_rows == ()
        assert vm.state.sql_success is False
        assert vm.state.sql_result_columns == ()
        assert vm.state.sql_result_rows == ()
        assert vm.state.sql_error is None

    def test_dispose_releases_db_reference(self, vm, mock_db):
        vm.dispose()
        mock_db.close.assert_called_once()
        assert vm._db is None
        assert vm._disposed is True

    def test_dispose_idempotent(self, vm, mock_db):
        vm.dispose()
        mock_db.close.assert_called_once()
        vm.dispose()  # Second call should be a no-op
        mock_db.close.assert_called_once()  # Still only one close call

    async def test_init_tables_after_dispose_returns_empty(self, vm):
        vm.dispose()
        result = await vm.init_tables()
        assert result == []

    async def test_load_table_schema_after_dispose_returns_empty(self, vm):
        vm.dispose()
        result = await vm.load_table_schema("stock_basic")
        assert result == []

    async def test_query_count_after_dispose_returns_zero(self, vm):
        vm.dispose()
        result = await vm.query_count()
        assert result == 0

    async def test_query_data_after_dispose_returns_empty(self, vm):
        vm.dispose()
        result = await vm.query_data()
        assert result.empty

    async def test_execute_sql_after_dispose_returns_error(self, vm):
        vm.dispose()
        result = await vm.execute_sql("SELECT 1")
        assert result["success"] is False

    async def test_export_data_after_dispose_returns_empty_df(self, vm):
        vm.dispose()
        result = await vm.export_data()
        assert result.empty


class TestInitTables:
    async def test_success_populates_tables_list(self, vm, mock_db):
        mock_db.get_all_tables.return_value = ["daily_quotes", "stock_basic"]
        result = await vm.init_tables()
        assert result == ("daily_quotes", "stock_basic")
        assert vm.state.tables_list == ("daily_quotes", "stock_basic")
        assert vm.state.tables_loaded is True

    async def test_success_selects_stock_basic_default(self, vm, mock_db):
        mock_db.get_all_tables.return_value = ["daily_quotes", "stock_basic"]
        await vm.init_tables()
        assert vm.state.current_table == "stock_basic"

    async def test_no_stock_basic_selects_first(self, vm, mock_db):
        mock_db.get_all_tables.return_value = ["alpha_table", "beta_table"]
        await vm.init_tables()
        assert vm.state.current_table == "alpha_table"

    async def test_empty_tables_sets_current_table_empty(self, vm, mock_db):
        mock_db.get_all_tables.return_value = []
        await vm.init_tables()
        assert vm.state.current_table == ""
        assert vm.state.tables_list == ()


class TestInitTablesErrors:
    async def test_db_error_sets_error_message(self, vm, mock_db):
        mock_db.get_all_tables.side_effect = RuntimeError("DB connection failed")
        await vm.init_tables()
        assert vm.state.error_message is not None
        # error_message 为 Message(key, params)，VM 不感知 locale

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.get_all_tables.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.init_tables()


class TestLoadSchema:
    async def test_success_updates_columns_and_numeric(self, vm, mock_db):
        schema = [
            {"name": "ts_code", "type": "TEXT"},
            {"name": "close", "type": "FLOAT"},
        ]
        mock_db.get_table_schema.return_value = schema
        result = await vm.load_table_schema("stock_basic")
        assert result == schema
        assert vm.state.table_columns == ("ts_code", "close")
        assert "close" in vm.state.numeric_cols
        assert "ts_code" not in vm.state.numeric_cols

    async def test_atomic_update_on_partial_schema(self, vm, mock_db):
        """If schema fetch returns partial data, columns and numeric_cols update atomically."""
        vm._set_state(table_columns=("old_col",), numeric_cols=frozenset({"old_col"}))
        schema = [{"name": "new_col", "type": "INTEGER"}]
        mock_db.get_table_schema.return_value = schema
        await vm.load_table_schema("stock_basic")
        assert vm.state.table_columns == ("new_col",)
        assert vm.state.numeric_cols == frozenset({"new_col"})

    async def test_empty_schema(self, vm, mock_db):
        mock_db.get_table_schema.return_value = []
        await vm.load_table_schema("stock_basic")
        assert vm.state.table_columns == ()
        assert vm.state.numeric_cols == frozenset()

    async def test_all_numeric_types_detected(self, vm, mock_db):
        schema = [
            {"name": "c_int", "type": "INTEGER"},
            {"name": "c_real", "type": "REAL"},
            {"name": "c_float", "type": "FLOAT"},
            {"name": "c_double", "type": "DOUBLE PRECISION"},
            {"name": "c_numeric", "type": "NUMERIC(10,2)"},
            {"name": "c_decimal", "type": "DECIMAL(10,2)"},
        ]
        mock_db.get_table_schema.return_value = schema
        await vm.load_table_schema("stock_basic")
        assert vm.state.numeric_cols == frozenset(
            {
                "c_int",
                "c_real",
                "c_float",
                "c_double",
                "c_numeric",
                "c_decimal",
            }
        )

    async def test_db_error_sets_error_message(self, vm, mock_db):
        mock_db.get_table_schema.side_effect = RuntimeError("Schema error")
        await vm.load_table_schema("stock_basic")
        assert vm.state.error_message is not None
        # error_message 为 Message(key, params)，VM 不感知 locale


class TestLoadSchemaCancelled:
    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.get_table_schema.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.load_table_schema("stock_basic")


class TestQueryData:
    async def test_basic_query_updates_state(self, vm, mock_db):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "close": [10.5]})
        mock_db.query_table.return_value = df
        mock_db.get_table_count.return_value = 100
        vm._set_state(table_columns=("ts_code", "close"))
        result = await vm.query_data()
        assert len(result) == 1
        assert vm.state.total_rows == 100
        assert vm.state.is_loading is False
        # 声明式: table_rows 已写入 state (tuple[Row, ...])
        assert len(vm.state.table_rows) == 1
        assert vm.state.table_rows[0].values[0] == "000001.SZ"

    async def test_with_filter_override(self, vm, mock_db):
        df = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_db.query_table.return_value = df
        mock_db.get_table_count.return_value = 1
        await vm.query_data(filters=[("ts_code", "=", "000001.SZ")])
        call_args = mock_db.query_table.call_args
        assert call_args[1].get("filters") == [("ts_code", "=", "000001.SZ")] or call_args[0][3] == [
            ("ts_code", "=", "000001.SZ")
        ]

    async def test_with_sort_override(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame()
        mock_db.get_table_count.return_value = 0
        await vm.query_data(sort_col_name="close", sort_ascending=False)
        # query_table is called via functools.partial, verify it was called
        mock_db.query_table.assert_called_once()
        call_args = mock_db.query_table.call_args
        # functools.partial passes args positionally: (table, page, page_size, filters, sort_col, sort_asc)
        assert call_args[0][4] == "close"  # sort_col_name
        assert call_args[0][5] is False  # sort_ascending

    async def test_with_page_override(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame()
        mock_db.get_table_count.return_value = 0
        await vm.query_data(page=3)
        call_args = mock_db.query_table.call_args
        assert call_args[1].get("page") == 3 or call_args[0][1] == 3
        # current_page 应更新为 3
        assert vm.state.current_page == 3

    async def test_empty_result(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame()
        mock_db.get_table_count.return_value = 0
        result = await vm.query_data()
        assert result.empty
        assert vm.state.total_rows == 0
        assert vm.state.table_rows == ()

    async def test_total_rows_updated(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame({"a": [1, 2]})
        mock_db.get_table_count.return_value = 42
        await vm.query_data()
        assert vm.state.total_rows == 42

    async def test_concurrent_guard_preserves_state(self, vm, mock_db):
        """is_loading=True 时 query_data 提前返回空 DataFrame, 不触达 DB, state 保留."""
        preset_rows = (TableRow(values=("existing",)),)
        vm._set_state(is_loading=True, table_rows=preset_rows, table_columns=("existing",))
        result = await vm.query_data()
        assert result.empty
        mock_db.query_table.assert_not_called()
        assert vm.state.table_rows == preset_rows

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.query_table.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.query_data()


class TestQueryCount:
    async def test_basic_count(self, vm, mock_db):
        mock_db.get_table_count.return_value = 500
        result = await vm.query_count()
        assert result == 500

    async def test_with_filter(self, vm, mock_db):
        mock_db.get_table_count.return_value = 10
        result = await vm.query_count(filters=[("ts_code", "=", "000001.SZ")])
        assert result == 10
        call_args = mock_db.get_table_count.call_args
        assert call_args[1].get("filters") == [("ts_code", "=", "000001.SZ")] or call_args[0][1] == [
            ("ts_code", "=", "000001.SZ")
        ]


class TestQueryCountCancelled:
    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.get_table_count.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.query_count()


class TestExportData:
    async def test_current_page_export(self, vm, mock_db):
        df = pd.DataFrame({"a": range(50)})
        mock_db.query_table.return_value = df
        mock_db.get_table_count.return_value = 50
        result = await vm.export_data(current_page_only=True)
        assert len(result) == 50

    async def test_all_data_export_uses_large_page_size(self, vm, mock_db):
        mock_db.get_table_count.return_value = 200
        mock_db.query_table.return_value = pd.DataFrame({"a": range(200)})
        await vm.export_data(current_page_only=False)
        # Should call query_table with a large page_size to get all data
        call_args = mock_db.query_table.call_args
        page_size_used = call_args[1].get("page_size", call_args[0][2] if len(call_args[0]) > 2 else None)
        assert page_size_used is not None and page_size_used > 50

    async def test_with_filter_and_sort(self, vm, mock_db):
        vm.set_filter("ts_code", "=", "000001.SZ")
        vm.set_sort(0, True)
        vm._set_state(table_columns=("ts_code", "close"))
        mock_db.query_table.return_value = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_db.get_table_count.return_value = 1
        result = await vm.export_data()
        assert len(result) == 1

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.query_table.side_effect = asyncio.CancelledError()
        vm._set_state(is_loading=False)
        with pytest.raises(asyncio.CancelledError):
            await vm.export_data()


class TestExecuteSQL:
    async def test_select_success(self, vm, mock_db):
        expected = {"success": True, "data": pd.DataFrame({"a": [1]}), "error": None}
        mock_db.execute_sql.return_value = expected
        result = await vm.execute_sql("SELECT * FROM stock_basic LIMIT 1")
        assert result["success"] is True
        assert vm.state.sql_is_executing is False
        # 声明式: sql_result_* 已写入 state (tuple[Row, ...])
        assert vm.state.sql_success is True
        assert vm.state.sql_result_columns == ("a",)
        assert len(vm.state.sql_result_rows) == 1
        assert vm.state.sql_result_rows[0].values == (1,)
        assert vm.state.sql_error is None

    async def test_error_result(self, vm, mock_db):
        expected = {"success": False, "data": None, "error": "Only SELECT allowed"}
        mock_db.execute_sql.return_value = expected
        result = await vm.execute_sql("DROP TABLE stock_basic")
        assert result["success"] is False
        # 声明式: error 写入 state.sql_error
        assert vm.state.sql_success is False
        assert vm.state.sql_error == "Only SELECT allowed"
        assert vm.state.sql_result_rows == ()

    async def test_exception_handling(self, vm, mock_db):
        mock_db.execute_sql.side_effect = RuntimeError("Connection lost")
        result = await vm.execute_sql("SELECT 1")
        assert result["success"] is False
        assert result["error"] is not None
        # get_error_message returns i18n translated message, not raw error string
        assert vm.state.sql_is_executing is False
        # 声明式: 异常也写入 state (error dict 转换)
        assert vm.state.sql_success is False
        assert vm.state.sql_error is not None

    async def test_empty_sql_returns_error(self, vm):
        result = await vm.execute_sql("")
        assert result["success"] is False
        assert result["error"] is not None
        # 空 SQL 提前返回, 不触达 _set_state (state 保持初始值)
        assert vm.state.sql_success is False
        assert vm.state.sql_result_rows == ()

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.execute_sql.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.execute_sql("SELECT 1")


class TestStateManagement:
    def test_set_filter(self, vm):
        vm.set_filter("ts_code", "LIKE", "000001")
        assert vm.state.filter_col == "ts_code"
        assert vm.state.filter_op == "LIKE"
        assert vm.state.filter_val == "000001"

    def test_set_sort(self, vm):
        vm._set_state(table_columns=("ts_code", "close"))
        vm.set_sort(1, False)
        assert vm.state.sort_col_index == 1
        assert vm.state.sort_asc is False

    def test_set_sort_toggle_direction(self, vm):
        vm._set_state(table_columns=("ts_code", "close"))
        vm.set_sort(1, True)
        assert vm.state.sort_col_index == 1
        assert vm.state.sort_asc is True
        # Toggle direction on same column
        vm.set_sort(1, False)
        assert vm.state.sort_asc is False

    def test_set_sort_type_guard_ignores_non_int(self, vm):
        vm._set_state(table_columns=("ts_code", "close"), sort_col_index=0, sort_asc=True)
        vm.set_sort("not_an_int", True)
        # Should not change sort state
        assert vm.state.sort_col_index == 0
        assert vm.state.sort_asc is True

    def test_reset_table_state(self, vm):
        vm._set_state(
            current_page=5,
            sort_col_index=2,
            sort_asc=False,
            filter_col="close",
            filter_op=">",
            filter_val="10",
            error_message=Message("some error"),
        )
        vm.reset_table_state()
        assert vm.state.current_page == 1
        assert vm.state.sort_col_index is None
        assert vm.state.sort_asc is True
        assert vm.state.filter_col is None
        assert vm.state.filter_op == "="
        assert vm.state.filter_val == ""
        assert vm.state.error_message is None

    def test_clear_error(self, vm):
        vm._set_state(error_message=Message("Something went wrong"))
        vm.clear_error()
        assert vm.state.error_message is None

    def test_set_table(self, vm):
        """set_table 替代直接属性写入(供 View 调用)。"""
        vm.set_table("daily_quotes")
        assert vm.state.current_table == "daily_quotes"

    def test_mark_tables_stale(self, vm):
        """mark_tables_stale 标记 tables_loaded=False(broadcast 消息触发)。"""
        vm._set_state(tables_loaded=True)
        vm.mark_tables_stale()
        assert vm.state.tables_loaded is False


class TestResolveSortCol:
    def test_valid_index(self, vm):
        vm._set_state(table_columns=("ts_code", "close", "open"), sort_col_index=1)
        result = vm._resolve_sort_col_name()
        assert result == "close"

    def test_none_index(self, vm):
        vm._set_state(table_columns=("ts_code", "close"), sort_col_index=None)
        result = vm._resolve_sort_col_name()
        assert result is None

    def test_out_of_range_index(self, vm):
        vm._set_state(table_columns=("ts_code", "close"), sort_col_index=5)
        result = vm._resolve_sort_col_name()
        assert result is None

    def test_empty_columns(self, vm):
        vm._set_state(table_columns=(), sort_col_index=0)
        result = vm._resolve_sort_col_name()
        assert result is None


class TestBuildFilters:
    def test_no_filter_returns_empty(self, vm):
        result = vm._build_filters()
        assert result == []

    def test_equal_filter(self, vm):
        vm._set_state(filter_col="ts_code", filter_op="=", filter_val="000001.SZ")
        result = vm._build_filters()
        assert result == [("ts_code", "=", "000001.SZ")]

    def test_like_filter(self, vm):
        vm._set_state(filter_col="ts_code", filter_op="LIKE", filter_val="000001")
        result = vm._build_filters()
        assert result == [("ts_code", "LIKE", "000001")]

    def test_range_filter_greater(self, vm):
        vm._set_state(filter_col="close", filter_op=">", filter_val="10.5")
        result = vm._build_filters()
        assert result == [("close", ">", "10.5")]

    def test_date_column_converts_format(self, vm):
        vm._set_state(filter_col="trade_date", filter_op="=", filter_val="2025-01-15")
        result = vm._build_filters()
        # Date columns should convert format if needed
        assert len(result) == 1
        assert result[0][0] == "trade_date"

    def test_non_date_column_no_conversion(self, vm):
        vm._set_state(filter_col="ts_code", filter_op="=", filter_val="000001.SZ")
        result = vm._build_filters()
        assert result == [("ts_code", "=", "000001.SZ")]

    def test_date_column_non_standard_format_no_conversion(self, vm):
        vm._set_state(filter_col="trade_date", filter_op="=", filter_val="not-a-date")
        result = vm._build_filters()
        # Non-standard format should still produce a filter (no crash)
        assert len(result) == 1


class TestNumericDetection:
    def test_int_real_float_detected(self, vm):
        schema = [
            {"name": "c_int", "type": "INTEGER"},
            {"name": "c_real", "type": "REAL"},
            {"name": "c_float", "type": "FLOAT"},
        ]
        result = vm._detect_numeric_cols(schema)
        assert result == {"c_int", "c_real", "c_float"}

    def test_double_numeric_decimal_detected(self, vm):
        schema = [
            {"name": "c_double", "type": "DOUBLE PRECISION"},
            {"name": "c_numeric", "type": "NUMERIC(10,2)"},
            {"name": "c_decimal", "type": "DECIMAL(10,2)"},
        ]
        result = vm._detect_numeric_cols(schema)
        assert result == {"c_double", "c_numeric", "c_decimal"}

    def test_text_varchar_excluded(self, vm):
        schema = [
            {"name": "c_text", "type": "TEXT"},
            {"name": "c_varchar", "type": "VARCHAR(50)"},
            {"name": "c_char", "type": "CHAR(10)"},
        ]
        result = vm._detect_numeric_cols(schema)
        assert result == set()

    def test_case_insensitive_type(self, vm):
        schema = [
            {"name": "c1", "type": "integer"},
            {"name": "c2", "type": "Float"},
            {"name": "c3", "type": "real"},
        ]
        result = vm._detect_numeric_cols(schema)
        assert result == {"c1", "c2", "c3"}


class TestUnsubscribeEdgeCase:
    def test_unsubscribe_when_already_removed_is_noop(self, vm):
        """对已移除的回调再次调用 unsubscribe 应安全无操作(分支 114->exit)。"""

        def cb(_s):
            pass

        unsubscribe = vm.subscribe(cb)
        unsubscribe()
        unsubscribe()  # 第二次调用,callback 已不在列表,不应抛异常


class TestDisposeEdgeCase:
    def test_dispose_when_db_already_none(self, vm):
        """dispose 时 _db 为 None 应安全跳过 close(分支 144->147)。"""
        vm._db = None
        vm.dispose()
        assert vm._disposed is True


class TestInitTablesSeverity:
    async def test_operational_error_sets_error_message(self, vm, mock_db):
        """operational 严重度异常(未知错误)设置 error_message,返回空列表。"""
        mock_db.get_all_tables.side_effect = RuntimeError("unknown boom")
        result = await vm.init_tables()
        assert result == []
        assert vm.state.error_message is not None

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出,不被吞没。"""
        mock_db.get_all_tables.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.init_tables()


class TestLoadSchemaSeverity:
    async def test_recoverable_error_sets_error_message(self, vm, mock_db):
        """recoverable 严重度异常(timeout)设置 error_message,返回空列表。"""
        mock_db.get_table_schema.side_effect = RuntimeError("query timeout")
        result = await vm.load_table_schema("stock_basic")
        assert result == []
        assert vm.state.error_message is not None

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出。"""
        mock_db.get_table_schema.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.load_table_schema("stock_basic")


class TestQueryDataSeverity:
    async def test_operational_error_sets_error_message(self, vm, mock_db):
        """operational 严重度异常设置 error_message,返回当前数据,finally 重置 is_loading。"""
        mock_db.get_table_count.side_effect = RuntimeError("unknown boom")
        result = await vm.query_data()
        assert result.empty
        assert vm.state.error_message is not None
        assert vm.state.is_loading is False

    async def test_recoverable_error_sets_error_message(self, vm, mock_db):
        """recoverable 严重度异常(timeout)设置 error_message,返回当前数据。"""
        mock_db.get_table_count.side_effect = RuntimeError("query timeout")
        result = await vm.query_data()
        assert result.empty
        assert vm.state.error_message is not None
        assert vm.state.is_loading is False

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出,finally 仍重置 is_loading。"""
        mock_db.get_table_count.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.query_data()
        assert vm.state.is_loading is False


class TestQueryCountSeverity:
    async def test_operational_error_returns_zero(self, vm, mock_db):
        """operational 严重度异常返回 0 并设置 error_message。"""
        mock_db.get_table_count.side_effect = RuntimeError("unknown boom")
        result = await vm.query_count()
        assert result == 0
        assert vm.state.error_message is not None

    async def test_recoverable_error_returns_zero(self, vm, mock_db):
        """recoverable 严重度异常(timeout)返回 0 并设置 error_message。"""
        mock_db.get_table_count.side_effect = RuntimeError("query timeout")
        result = await vm.query_count()
        assert result == 0
        assert vm.state.error_message is not None

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出。"""
        mock_db.get_table_count.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.query_count()


class TestExportDataSeverity:
    async def test_operational_error_returns_empty_df(self, vm, mock_db):
        """operational 严重度异常返回空 DataFrame 并设置 error_message。"""
        mock_db.query_table.side_effect = RuntimeError("unknown boom")
        result = await vm.export_data()
        assert result.empty
        assert vm.state.error_message is not None

    async def test_recoverable_error_returns_empty_df(self, vm, mock_db):
        """recoverable 严重度异常(timeout)返回空 DataFrame 并设置 error_message。"""
        mock_db.query_table.side_effect = RuntimeError("query timeout")
        result = await vm.export_data()
        assert result.empty
        assert vm.state.error_message is not None

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出。"""
        mock_db.query_table.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.export_data()


class TestExecuteSQLSeverity:
    async def test_operational_error_returns_error_dict(self, vm, mock_db):
        """operational 严重度异常返回 error dict,写入 state.sql_error,finally 重置 sql_is_executing。"""
        mock_db.execute_sql.side_effect = RuntimeError("unknown boom")
        result = await vm.execute_sql("SELECT 1")
        assert result["success"] is False
        assert result["error"] is not None
        assert vm.state.sql_is_executing is False
        # 声明式: error 写入 state (L771 合规)
        assert vm.state.sql_success is False
        assert vm.state.sql_error is not None

    async def test_system_error_propagates(self, vm, mock_db):
        """system 严重度异常(PermissionError)必须抛出,finally 仍重置 sql_is_executing。"""
        mock_db.execute_sql.side_effect = PermissionError("denied")
        with pytest.raises(PermissionError):
            await vm.execute_sql("SELECT 1")
        assert vm.state.sql_is_executing is False


class TestWriteExcel:
    async def test_write_excel_calls_to_excel_with_correct_args(self, vm, mock_tp):
        """write_excel 通过 ThreadPoolManager 调用 df.to_excel(filepath, index=False, engine='openpyxl') (R16)."""
        df = MagicMock()
        filepath = "/tmp/test.xlsx"
        await vm.write_excel(df, filepath)
        df.to_excel.assert_called_once_with(filepath, index=False, engine="openpyxl")
        # R16: 必须 offload 到 CPU 线程池
        call_args = mock_tp.run_async.call_args
        assert call_args[0][0] is TaskType.CPU

    async def test_cancelled_error_propagates(self, vm, mock_tp):
        """R2: CancelledError 必须重新 raise, 不可吞没."""
        mock_tp.run_async = AsyncMock(side_effect=asyncio.CancelledError())
        df = MagicMock()
        with pytest.raises(asyncio.CancelledError):
            await vm.write_excel(df, "/tmp/test.xlsx")

    async def test_success_returns_none(self, vm, mock_tp):
        """成功路径返回 None, 与 write_csv 一致."""
        df = MagicMock()
        result = await vm.write_excel(df, "/tmp/test.xlsx")
        assert result is None
