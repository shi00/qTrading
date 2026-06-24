"""Unit tests for DataExplorerViewModel — TDD RED phase.

These tests define the expected ViewModel contract.
They will fail until DataExplorerViewModel is implemented.
"""

import asyncio
import functools
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from ui.viewmodels.data_explorer_view_model import DataExplorerViewModel

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
        assert vm.current_table == "stock_basic"
        assert vm.current_page == 1
        assert vm.page_size == 50
        assert vm.total_rows == 0
        assert vm.table_columns == []
        assert vm.numeric_cols == set()
        assert vm.sort_col_index is None
        assert vm.sort_asc is True
        assert vm.filter_col is None
        assert vm.filter_op == "="
        assert vm.filter_val == ""
        assert vm.is_loading is False
        assert vm.tables_list == []
        assert vm.tables_loaded is False
        assert vm.current_data.empty
        assert vm.error_message is None
        assert vm.sql_result is None
        assert vm.sql_is_executing is False


class TestDispose:
    def test_dispose_clears_state(self, vm):
        vm.current_data = pd.DataFrame({"a": [1]})
        vm.tables_list = ["t1"]
        vm.table_columns = ["col1"]
        vm.error_message = "err"
        vm.dispose()
        assert vm.current_data.empty
        assert vm.tables_list == []
        assert vm.table_columns == []
        assert vm.error_message is None

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

    async def test_query_data_after_dispose_returns_current(self, vm):
        vm.dispose()
        vm.current_data = pd.DataFrame({"existing": [1]})
        result = await vm.query_data()
        assert "existing" in result.columns

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
        assert result == ["daily_quotes", "stock_basic"]
        assert vm.tables_list == ["daily_quotes", "stock_basic"]
        assert vm.tables_loaded is True

    async def test_success_selects_stock_basic_default(self, vm, mock_db):
        mock_db.get_all_tables.return_value = ["daily_quotes", "stock_basic"]
        await vm.init_tables()
        assert vm.current_table == "stock_basic"

    async def test_no_stock_basic_selects_first(self, vm, mock_db):
        mock_db.get_all_tables.return_value = ["alpha_table", "beta_table"]
        await vm.init_tables()
        assert vm.current_table == "alpha_table"

    async def test_empty_tables_sets_current_table_empty(self, vm, mock_db):
        mock_db.get_all_tables.return_value = []
        await vm.init_tables()
        assert vm.current_table == ""
        assert vm.tables_list == []


class TestInitTablesErrors:
    async def test_db_error_sets_error_message(self, vm, mock_db):
        mock_db.get_all_tables.side_effect = RuntimeError("DB connection failed")
        await vm.init_tables()
        assert vm.error_message is not None
        # get_error_message returns i18n translated message, not raw error string

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
        assert vm.table_columns == ["ts_code", "close"]
        assert "close" in vm.numeric_cols
        assert "ts_code" not in vm.numeric_cols

    async def test_atomic_update_on_partial_schema(self, vm, mock_db):
        """If schema fetch returns partial data, columns and numeric_cols update atomically."""
        vm.table_columns = ["old_col"]
        vm.numeric_cols = {"old_col"}
        schema = [{"name": "new_col", "type": "INTEGER"}]
        mock_db.get_table_schema.return_value = schema
        await vm.load_table_schema("stock_basic")
        assert vm.table_columns == ["new_col"]
        assert vm.numeric_cols == {"new_col"}

    async def test_empty_schema(self, vm, mock_db):
        mock_db.get_table_schema.return_value = []
        await vm.load_table_schema("stock_basic")
        assert vm.table_columns == []
        assert vm.numeric_cols == set()

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
        assert vm.numeric_cols == {
            "c_int",
            "c_real",
            "c_float",
            "c_double",
            "c_numeric",
            "c_decimal",
        }

    async def test_db_error_sets_error_message(self, vm, mock_db):
        mock_db.get_table_schema.side_effect = RuntimeError("Schema error")
        await vm.load_table_schema("stock_basic")
        assert vm.error_message is not None
        # get_error_message returns i18n translated message, not raw error string


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
        result = await vm.query_data()
        assert len(result) == 1
        assert vm.total_rows == 100
        assert vm.is_loading is False

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

    async def test_empty_result(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame()
        mock_db.get_table_count.return_value = 0
        result = await vm.query_data()
        assert result.empty
        assert vm.total_rows == 0

    async def test_total_rows_updated(self, vm, mock_db):
        mock_db.query_table.return_value = pd.DataFrame({"a": [1, 2]})
        mock_db.get_table_count.return_value = 42
        await vm.query_data()
        assert vm.total_rows == 42

    async def test_concurrent_guard_returns_current_data(self, vm):
        vm.is_loading = True
        vm.current_data = pd.DataFrame({"existing": [1]})
        result = await vm.query_data()
        assert "existing" in result.columns

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
        vm.table_columns = ["ts_code", "close"]
        mock_db.query_table.return_value = pd.DataFrame({"ts_code": ["000001.SZ"]})
        mock_db.get_table_count.return_value = 1
        result = await vm.export_data()
        assert len(result) == 1

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.query_table.side_effect = asyncio.CancelledError()
        vm.is_loading = False
        with pytest.raises(asyncio.CancelledError):
            await vm.export_data()


class TestExecuteSQL:
    async def test_select_success(self, vm, mock_db):
        expected = {"success": True, "data": pd.DataFrame({"a": [1]}), "error": None}
        mock_db.execute_sql.return_value = expected
        result = await vm.execute_sql("SELECT * FROM stock_basic LIMIT 1")
        assert result["success"] is True
        assert vm.sql_result == expected
        assert vm.sql_is_executing is False

    async def test_error_result(self, vm, mock_db):
        expected = {"success": False, "data": None, "error": "Only SELECT allowed"}
        mock_db.execute_sql.return_value = expected
        result = await vm.execute_sql("DROP TABLE stock_basic")
        assert result["success"] is False
        assert vm.sql_result == expected

    async def test_exception_handling(self, vm, mock_db):
        mock_db.execute_sql.side_effect = RuntimeError("Connection lost")
        result = await vm.execute_sql("SELECT 1")
        assert result["success"] is False
        assert result["error"] is not None
        # get_error_message returns i18n translated message, not raw error string
        assert vm.sql_is_executing is False

    async def test_empty_sql_returns_error(self, vm):
        result = await vm.execute_sql("")
        assert result["success"] is False
        assert result["error"] is not None

    async def test_cancelled_error_propagates(self, vm, mock_db):
        mock_db.execute_sql.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await vm.execute_sql("SELECT 1")


class TestStateManagement:
    def test_set_filter(self, vm):
        vm.set_filter("ts_code", "LIKE", "000001")
        assert vm.filter_col == "ts_code"
        assert vm.filter_op == "LIKE"
        assert vm.filter_val == "000001"

    def test_set_sort(self, vm):
        vm.table_columns = ["ts_code", "close"]
        vm.set_sort(1, False)
        assert vm.sort_col_index == 1
        assert vm.sort_asc is False

    def test_set_sort_toggle_direction(self, vm):
        vm.table_columns = ["ts_code", "close"]
        vm.set_sort(1, True)
        assert vm.sort_col_index == 1
        assert vm.sort_asc is True
        # Toggle direction on same column
        vm.set_sort(1, False)
        assert vm.sort_asc is False

    def test_set_sort_type_guard_ignores_non_int(self, vm):
        vm.table_columns = ["ts_code", "close"]
        vm.sort_col_index = 0
        vm.sort_asc = True
        vm.set_sort("not_an_int", True)
        # Should not change sort state
        assert vm.sort_col_index == 0
        assert vm.sort_asc is True

    def test_reset_table_state(self, vm):
        vm.current_page = 5
        vm.sort_col_index = 2
        vm.sort_asc = False
        vm.filter_col = "close"
        vm.filter_op = ">"
        vm.filter_val = "10"
        vm.error_message = "some error"
        vm.reset_table_state()
        assert vm.current_page == 1
        assert vm.sort_col_index is None
        assert vm.sort_asc is True
        assert vm.filter_col is None
        assert vm.filter_op == "="
        assert vm.filter_val == ""
        assert vm.error_message is None

    def test_clear_error(self, vm):
        vm.error_message = "Something went wrong"
        vm.clear_error()
        assert vm.error_message is None


class TestResolveSortCol:
    def test_valid_index(self, vm):
        vm.table_columns = ["ts_code", "close", "open"]
        vm.sort_col_index = 1
        result = vm._resolve_sort_col_name()
        assert result == "close"

    def test_none_index(self, vm):
        vm.table_columns = ["ts_code", "close"]
        vm.sort_col_index = None
        result = vm._resolve_sort_col_name()
        assert result is None

    def test_out_of_range_index(self, vm):
        vm.table_columns = ["ts_code", "close"]
        vm.sort_col_index = 5
        result = vm._resolve_sort_col_name()
        assert result is None

    def test_empty_columns(self, vm):
        vm.table_columns = []
        vm.sort_col_index = 0
        result = vm._resolve_sort_col_name()
        assert result is None


class TestBuildFilters:
    def test_no_filter_returns_empty(self, vm):
        result = vm._build_filters()
        assert result == []

    def test_equal_filter(self, vm):
        vm.filter_col = "ts_code"
        vm.filter_op = "="
        vm.filter_val = "000001.SZ"
        result = vm._build_filters()
        assert result == [("ts_code", "=", "000001.SZ")]

    def test_like_filter(self, vm):
        vm.filter_col = "ts_code"
        vm.filter_op = "LIKE"
        vm.filter_val = "000001"
        result = vm._build_filters()
        assert result == [("ts_code", "LIKE", "000001")]

    def test_range_filter_greater(self, vm):
        vm.filter_col = "close"
        vm.filter_op = ">"
        vm.filter_val = "10.5"
        result = vm._build_filters()
        assert result == [("close", ">", "10.5")]

    def test_date_column_converts_format(self, vm):
        vm.filter_col = "trade_date"
        vm.filter_op = "="
        vm.filter_val = "2025-01-15"
        result = vm._build_filters()
        # Date columns should convert format if needed
        assert len(result) == 1
        assert result[0][0] == "trade_date"

    def test_non_date_column_no_conversion(self, vm):
        vm.filter_col = "ts_code"
        vm.filter_op = "="
        vm.filter_val = "000001.SZ"
        result = vm._build_filters()
        assert result == [("ts_code", "=", "000001.SZ")]

    def test_date_column_non_standard_format_no_conversion(self, vm):
        vm.filter_col = "trade_date"
        vm.filter_op = "="
        vm.filter_val = "not-a-date"
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
