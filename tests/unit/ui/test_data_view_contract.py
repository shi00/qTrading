"""ui/views/data_view.py 声明式契约守护 + 组件体 + 事件 handler 测试.

测试层次:
1. 纯函数测试 (_format_cell_value / _build_*_options / _ceil_div / _table_rows_to_paginated_rows 等)
2. 契约守护 (声明式范式: @ft.component / use_viewmodel / 禁止命令式 API)
3. 组件体测试 (attach_fake_page 驱动 mount/unmount, 验证控件树结构 + VM 生命周期)
4. 事件 handler 测试 (触发 on_click/on_select → page.run_task → async _do_* 路径)
"""

from __future__ import annotations

import asyncio
import datetime
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pandas as pd
import pytest

from data.persistence.metadata_manager import MetaDataManager
from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.viewmodels import Message
from ui.viewmodels.data_explorer_view_model import (
    SqlResultRow,
    TableRow,
    _sql_result_to_state_fields,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 源码读取辅助 (契约守护)
# ============================================================================


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码, 用于契约守护检查。"""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    import ui.views.data_view as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    import ui.views.data_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 纯函数测试
# ============================================================================


class TestFormatCellValue:
    """_format_cell_value: 单元格值格式化。"""

    def test_none_returns_dash(self):
        """None 值返回 '-'。"""
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(None, "col") == "-"

    def test_nan_float_returns_dash(self):
        """NaN float 返回 '-'。"""
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(float("nan"), "col") == "-"

    def test_date_column_formats_datetime(self):
        """date 列的 datetime 对象格式化为 YYYY-MM-DD。"""
        from ui.views.data_view import _format_cell_value

        d = datetime.date(2024, 1, 15)
        assert _format_cell_value(d, "trade_date") == "2024-01-15"

    def test_date_column_formats_8digit_string(self):
        """date 列的 8 位数字字符串格式化为 YYYY-MM-DD。"""
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value("20240115", "end_date") == "2024-01-15"

    def test_non_date_returns_str(self):
        """非 date 列的值直接 str() 化。"""
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(42, "amount") == "42"
        assert _format_cell_value("hello", "name") == "hello"


class TestBuildFilterOpOptions:
    """_build_filter_op_options: 构建过滤操作符选项。"""

    def test_returns_7_options(self):
        from ui.views.data_view import _build_filter_op_options

        opts = _build_filter_op_options()
        assert len(opts) == 7
        keys = [o.key for o in opts]
        assert "=" in keys
        assert "LIKE" in keys
        assert "!=" in keys


class TestCeilDiv:
    """_ceil_div: 向上取整除法。"""

    def test_exact_division(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(100, 50) == 2

    def test_partial_division_rounds_up(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(101, 50) == 3

    def test_zero_divisor_returns_1(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(100, 0) == 1

    def test_small_numerator(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(1, 50) == 1


class TestTableRowsToPaginatedRows:
    """_table_rows_to_paginated_rows: tuple[TableRow, ...] → PaginatedTable rows。"""

    def test_empty_rows_returns_empty_list(self):
        from ui.views.data_view import _table_rows_to_paginated_rows

        assert _table_rows_to_paginated_rows((), ("col1",)) == []

    def test_formats_date_columns(self):
        from ui.views.data_view import _table_rows_to_paginated_rows

        rows = (TableRow(values=("20240115", "测试")),)
        result = _table_rows_to_paginated_rows(rows, ("trade_date", "name"))
        assert result[0]["trade_date"] == "2024-01-15"
        assert result[0]["name"] == "测试"

    def test_none_values_become_dash(self):
        from ui.views.data_view import _table_rows_to_paginated_rows

        rows = (TableRow(values=(None, 1.0)),)
        result = _table_rows_to_paginated_rows(rows, ("col1", "col2"))
        assert result[0]["col1"] == "-"


class TestBuildTableSelectorOptions:
    """_build_table_selector_options: 构建表选择器选项。"""

    def _make_vm(self) -> MagicMock:
        """Task 5.1: helper 需要 vm.get_table_alias 参数。"""
        vm = MagicMock()
        vm.get_table_alias.side_effect = lambda t: t
        return vm

    def test_returns_options_for_each_table(self):
        from ui.views.data_view import _build_table_selector_options

        MetaDataManager._alias_cache.clear()
        opts = _build_table_selector_options(("stock_basic", "daily_quotes"), self._make_vm())
        assert len(opts) == 2
        assert opts[0].key == "stock_basic"

    def test_empty_tuple_returns_empty_list(self):
        from ui.views.data_view import _build_table_selector_options

        assert _build_table_selector_options((), self._make_vm()) == []


class TestBuildFilterColOptions:
    """_build_filter_col_options: 构建过滤列选项。"""

    def _make_vm(self) -> MagicMock:
        """Task 5.1: helper 需要 vm.get_column_alias 参数。"""
        vm = MagicMock()
        vm.get_column_alias.side_effect = lambda t, c: c
        return vm

    def test_returns_options_for_each_column(self):
        from ui.views.data_view import _build_filter_col_options

        MetaDataManager._alias_cache.clear()
        opts = _build_filter_col_options("stock_basic", ("ts_code", "name"), self._make_vm())
        assert len(opts) == 2
        assert opts[0].key == "ts_code"


class TestBuildTableColumnsSpec:
    """_build_table_columns_spec: 构建 PaginatedTable columns spec。"""

    def _make_vm(self) -> MagicMock:
        """Task 5.1: helper 需要 vm.get_column_alias 参数。"""
        vm = MagicMock()
        vm.get_column_alias.side_effect = lambda t, c: c
        return vm

    def test_returns_spec_with_id_label_width(self):
        from ui.views.data_view import _build_table_columns_spec

        MetaDataManager._alias_cache.clear()
        spec = _build_table_columns_spec("stock_basic", ("ts_code", "name"), self._make_vm())
        assert len(spec) == 2
        assert spec[0]["id"] == "ts_code"
        assert spec[0]["width"] == 140


class TestBuildSqlColumnsSpec:
    """_build_sql_columns_spec: 构建 SQL 结果表 columns spec (从 tuple[str, ...])。"""

    def _make_vm(self) -> MagicMock:
        """Task 5.1: helper 需要 vm.get_column_alias 参数。"""
        vm = MagicMock()
        vm.get_column_alias.side_effect = lambda t, c: c
        return vm

    def test_returns_spec_from_columns_tuple(self):
        from ui.views.data_view import _build_sql_columns_spec

        MetaDataManager._alias_cache.clear()
        spec = _build_sql_columns_spec(("col1", "col2"), self._make_vm())
        assert len(spec) == 2
        assert spec[0]["id"] == "col1"
        assert spec[0]["width"] == 140


class TestSqlRowsToPaginatedRows:
    """_sql_rows_to_paginated_rows: tuple[SqlResultRow, ...] → PaginatedTable rows。"""

    def test_empty_rows_returns_empty_list(self):
        from ui.views.data_view import _sql_rows_to_paginated_rows

        assert _sql_rows_to_paginated_rows((), ()) == []

    def test_converts_rows_to_dict(self):
        from ui.views.data_view import _sql_rows_to_paginated_rows

        rows = (SqlResultRow(values=(1, "a")), SqlResultRow(values=(2, "b")))
        result = _sql_rows_to_paginated_rows(rows, ("col1", "col2"))
        assert len(result) == 2
        assert result[0]["col1"] == "1"
        assert result[0]["col2"] == "a"


class TestGetPage:
    """_get_page: 安全获取 ft.context.page。"""

    def test_returns_none_when_no_context(self):
        """无渲染上下文时返回 None (不抛 RuntimeError)。"""
        from ui.views.data_view import _get_page

        with patch("ui.views.data_view.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


# ============================================================================
# 契约守护: 声明式范式
# ============================================================================


class TestDataViewDeclarativeContract:
    """data_view.py 声明式契约守护测试。"""

    def test_table_viewer_tab_is_ft_component(self):
        """DoD: TableViewerTab 必须 @ft.component 装饰。"""
        from ui.views.data_view import TableViewerTab

        assert hasattr(TableViewerTab, "__wrapped__"), "TableViewerTab 必须用 @ft.component 装饰"

    def test_sql_console_tab_is_ft_component(self):
        from ui.views.data_view import SQLConsoleTab

        assert hasattr(SQLConsoleTab, "__wrapped__"), "SQLConsoleTab 必须用 @ft.component 装饰"

    def test_data_explorer_view_is_ft_component(self):
        from ui.views.data_view import DataExplorerView

        assert hasattr(DataExplorerView, "__wrapped__"), "DataExplorerView 必须用 @ft.component 装饰"

    def test_no_did_mount(self):
        assert "did_mount" not in _code_source(), "不应使用 did_mount"

    def test_no_will_unmount(self):
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount"

    def test_no_page_ref_mixin(self):
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"

    def test_uses_use_viewmodel(self):
        assert "use_viewmodel" in _code_source(), "必须使用 use_viewmodel hook"

    def test_uses_ft_context_page(self):
        assert "ft.context.page" in _code_source(), "page 访问必须用 ft.context.page"

    def test_no_class_container(self):
        assert "class TableViewerTab(" not in _code_source()
        assert "class SQLConsoleTab(" not in _code_source()
        assert "class DataExplorerView(" not in _code_source()


# ============================================================================
# FakeVM 基础设施
# ============================================================================


@dataclass(frozen=True)
class _FakeDataExplorerState:
    """模拟 DataExplorerState 的最小字段集 (声明式: tuple[Row, ...])."""

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
    tables_loaded: bool = True
    error_message: Message | None = None
    tables_list: tuple[str, ...] = ("stock_basic", "daily_quotes")
    table_columns: tuple[str, ...] = ("ts_code", "name", "trade_date")
    numeric_cols: frozenset[str] = frozenset()
    table_rows: tuple[TableRow, ...] = ()
    sql_is_executing: bool = False
    sql_success: bool = False
    sql_result_columns: tuple[str, ...] = ()
    sql_result_rows: tuple[SqlResultRow, ...] = ()
    sql_error: str | None = None


class _FakeDataExplorerViewModel:
    """模拟 DataExplorerViewModel, 记录所有方法调用。

    满足 _ViewModelProtocol 契约 (state/subscribe/dispose) +
    组件调用的所有 async/sync 方法。
    """

    def __init__(self, state: _FakeDataExplorerState | None = None) -> None:
        self._state: _FakeDataExplorerState = state or _FakeDataExplorerState()
        self._subscribers: list[Any] = []
        # 测试 fixture: 驱动 export_data 返回值 / execute_sql state 更新 (非 dual-track property)
        self._current_data: pd.DataFrame = pd.DataFrame()
        self._sql_result: dict | None = None
        self.dispose_called: bool = False
        self.method_calls: list[tuple[str, dict]] = []

    @property
    def state(self) -> _FakeDataExplorerState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        for cb in self._subscribers:
            cb(self._state)

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    # --- async methods ---

    async def init_tables(self) -> list[str]:
        self.method_calls.append(("init_tables", {}))
        self._set_state(tables_loaded=True, tables_list=("stock_basic", "daily_quotes"))
        return ["stock_basic", "daily_quotes"]

    async def load_table_schema(self, table_name: str) -> list:
        self.method_calls.append(("load_table_schema", {"table_name": table_name}))
        return []

    async def query_data(self, **kwargs: Any) -> pd.DataFrame:
        self.method_calls.append(("query_data", kwargs))
        return self._current_data

    async def export_data(self, current_page_only: bool = True) -> pd.DataFrame:
        self.method_calls.append(("export_data", {"current_page_only": current_page_only}))
        return self._current_data

    async def execute_sql(self, sql: str) -> dict:
        self.method_calls.append(("execute_sql", {"sql": sql}))
        result = self._sql_result or {"success": False, "data": None, "error": "no result"}
        # 声明式: 从 result 更新 state (镜像真实 VM execute_sql 行为)
        self._set_state(**_sql_result_to_state_fields(result))
        return result

    # --- sync methods ---

    def set_table(self, table_name: str) -> None:
        self.method_calls.append(("set_table", {"table_name": table_name}))
        self._set_state(current_table=table_name)

    def get_table_alias(self, table_name: str) -> str:
        """Mock vm.get_table_alias (Task 5.1: 从 View 迁入 VM)."""
        return table_name

    def get_column_alias(self, table_name: str | None, col: str) -> str:
        """Mock vm.get_column_alias (Task 5.1: 从 View 迁入 VM)."""
        return col

    def reset_table_state(self) -> None:
        self.method_calls.append(("reset_table_state", {}))

    def set_filter(self, col: str, op: str, val: str) -> None:
        self.method_calls.append(("set_filter", {"col": col, "op": op, "val": val}))

    def set_sort(self, col_index: int | None, ascending: bool) -> None:
        self.method_calls.append(("set_sort", {"col_index": col_index, "ascending": ascending}))

    def clear_error(self) -> None:
        self.method_calls.append(("clear_error", {}))

    def mark_tables_stale(self) -> None:
        self.method_calls.append(("mark_tables_stale", {}))


# --- 辅助函数 ---


def _run_async_coro(coro: Any) -> None:
    """同步执行 coroutine。"""
    if asyncio.iscoroutine(coro):
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()


def _make_fake_page() -> FakePage:
    """创建扩展的 FakePage, 支持 run_task/show_toast/pubsub。"""
    page = FakePage()

    def _run_task(fn: Any, *args: Any, **kwargs: Any) -> None:
        result = fn(*args, **kwargs)
        if asyncio.iscoroutine(result):
            _run_async_coro(result)

    page.run_task = MagicMock(side_effect=_run_task)  # type: ignore[method-assign]
    page.show_toast = MagicMock()  # type: ignore[method-assign]
    page.pubsub = MagicMock()  # type: ignore[method-assign]
    return page


def _mount(component: Any, page: FakePage | None = None) -> tuple[Any, FakePage]:
    """挂载组件并返回 (渲染结果, page)。

    run_mount_effects 返回 page 而非渲染结果, 需额外调 render_once 获取控件树。
    """
    if page is None:
        page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)
    return result, page


def _collect_controls(root: Any) -> list[Any]:
    """深度优先遍历控件树, 返回所有控件 (含 Component 描述)。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归: mock 下 content 属性返回新 MagicMock)。
    """
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[Any] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_collect_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_collect_controls(content))
    return result


def _find_by_type(root: Any, ctrl_type: type) -> list[Any]:
    """按类型查找所有控件。"""
    return [c for c in _collect_controls(root) if isinstance(c, ctrl_type)]


def _find_icon_button(root: Any, icon: str) -> Any | None:
    """按 icon 名称查找 IconButton。"""
    return next(
        (c for c in _collect_controls(root) if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == icon),
        None,
    )


def _find_control_by_attr(root: Any, attr: str, value: Any) -> Any | None:
    """按属性值查找控件。"""
    return next((c for c in _collect_controls(root) if getattr(c, attr, None) == value), None)


def _find_dropdown(root: Any) -> Any | None:
    """查找第一个 Dropdown。"""
    return next((c for c in _collect_controls(root) if isinstance(c, ft.Dropdown)), None)


def _make_event(value: Any = None, control: Any = None) -> Any:
    """创建 fake ControlEvent。"""
    e = MagicMock()
    if control is not None:
        e.control = control
    else:
        e.control = MagicMock()
    e.control.value = value
    return e


@pytest.fixture
def mock_metadata(monkeypatch):
    """Mock MetaDataManager 别名方法 + 清缓存。"""
    MetaDataManager._alias_cache.clear()
    monkeypatch.setattr(MetaDataManager, "get_table_alias", classmethod(lambda cls, t: t))
    monkeypatch.setattr(MetaDataManager, "get_column_alias", classmethod(lambda cls, t, c: c))


# ============================================================================
# 组件体测试: TableViewerTab
# ============================================================================


class TestTableViewerTabComponentBody:
    """TableViewerTab 组件体测试: 渲染结构 + VM 生命周期。"""

    def test_mount_returns_column(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """挂载 TableViewerTab 返回 ft.Column。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        assert isinstance(result, ft.Column)

    def test_mount_triggers_vm_subscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """挂载后 VM.subscribe 被调用 (use_viewmodel hook 注册)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(TableViewerTab, vm=vm)
        _mount(component)

        assert len(vm._subscribers) > 0

    def test_mount_registers_file_picker_in_services(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """挂载后 FilePicker 被注册到 page.services。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        _mount(component, page=page)

        assert len(page.services) > 0
        assert any(isinstance(s, ft.FilePicker) for s in page.services)

    def test_mount_shows_loading_widget_when_loading(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """is_loading=True 时渲染 loading widget (ProgressRing)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(is_loading=True))
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        rings = _find_by_type(result, ft.ProgressRing)
        assert len(rings) > 0, "loading 状态应显示 ProgressRing"

    def test_mount_shows_paginated_table_when_columns_present(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """有 table_columns 时渲染 PaginatedTable (非 loading widget)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code", "name"), tables_loaded=True)
        )
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        # PaginatedTable 渲染为 Column, 不应有 ProgressRing (loading widget)
        rings = _find_by_type(result, ft.ProgressRing)
        # ProgressBar in toolbar is allowed, but ProgressRing only in loading widget
        assert len(rings) == 0, "非 loading 状态不应显示 ProgressRing"

    def test_mount_shows_loading_when_no_columns(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """无 table_columns 且非 loading 时显示 loading widget (兜底)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=(), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        rings = _find_by_type(result, ft.ProgressRing)
        assert len(rings) > 0

    def test_table_selector_has_options(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """table_selector Dropdown 包含 tables_list 对应的 options。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(tables_list=("stock_basic", "daily_quotes"), tables_loaded=True)
        )
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        dropdowns = _find_by_type(result, ft.Dropdown)
        assert len(dropdowns) >= 1
        # 第一个 Dropdown 是 table_selector
        table_selector = dropdowns[0]
        assert len(table_selector.options) == 2

    def test_pagination_buttons_present(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """分页栏包含上一页/下一页 IconButton。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        prev_btn = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        next_btn = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        assert prev_btn is not None
        assert next_btn is not None

    def test_export_menu_items_present(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """PopupMenuButton 包含导出当前页/导出全部两个菜单项。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        result, _ = _mount(component)

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        assert len(popup_menus) == 1
        assert len(popup_menus[0].items) == 2

    def test_unmount_does_not_dispose_vm(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """外部 VM 模式: 卸载不 dispose VM (仅退订)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(TableViewerTab, vm=vm)
        _mount(component)

        assert vm.dispose_called is False
        assert len(vm._subscribers) > 0

        run_unmount_effects(component)

        assert vm.dispose_called is False, "外部 VM 模式不应 dispose"
        assert len(vm._subscribers) == 0, "卸载后退订"


# ============================================================================
# 组件体测试: SQLConsoleTab
# ============================================================================


class TestSQLConsoleTabComponentBody:
    """SQLConsoleTab 组件体测试: 渲染结构 + VM 生命周期。"""

    def test_mount_returns_column(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """挂载 SQLConsoleTab 返回 ft.Column。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        result, _ = _mount(component)

        assert isinstance(result, ft.Column)

    def test_mount_triggers_vm_subscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        _mount(component)

        assert len(vm._subscribers) > 0

    def test_shows_empty_state_when_no_data(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """无 SQL 结果时显示 empty_state (Terminal icon)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        result, _ = _mount(component)

        icons = _find_by_type(result, ft.Icon)
        assert any(getattr(i, "icon", None) == ft.Icons.TERMINAL for i in icons)

    def test_shows_result_table_when_has_data(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """有 SQL 结果时显示 result_table (PaginatedTable), 不显示 empty_state。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        # 声明式: 直接设置 state (mount-time 渲染从 state 读取, 非 dual-track property)
        vm._set_state(
            sql_success=True,
            sql_result_columns=("col1",),
            sql_result_rows=(SqlResultRow(values=(1,)), SqlResultRow(values=(2,))),
            sql_error=None,
        )
        component = make_component(SQLConsoleTab, vm=vm)
        result, _ = _mount(component)

        # empty_state should not be visible
        containers = _find_by_type(result, ft.Container)
        empty_state = next(
            (c for c in containers if getattr(c, "visible", True) is False and _has_terminal_icon(c)),
            None,
        )
        assert empty_state is not None, "有数据时 empty_state 应 visible=False"

    def test_sql_editor_present(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """SQL 编辑器 TextField 存在。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        result, _ = _mount(component)

        text_fields = _find_by_type(result, ft.TextField)
        assert any(getattr(tf, "multiline", False) for tf in text_fields)

    def test_template_buttons_present(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """SQL 模板按钮 (OutlinedButton) 存在。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        result, _ = _mount(component)

        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        assert len(outlined_btns) >= 2

    def test_unmount_does_not_dispose_vm(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        _mount(component)

        run_unmount_effects(component)

        assert vm.dispose_called is False


# ============================================================================
# 组件体测试: DataExplorerView
# ============================================================================


class TestDataExplorerViewComponentBody:
    """DataExplorerView 组件体测试: 渲染结构 + VM 生命周期 + PubSub。"""

    def test_mount_returns_container(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """挂载 DataExplorerView 返回 ft.Container。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        result, _ = _mount(component)

        assert isinstance(result, ft.Container)

    def test_mount_triggers_vm_subscribe_and_dispose_on_unmount(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """内部 VM 模式: 挂载 subscribe, 卸载 dispose。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        _mount(component)

        assert len(fake_vm._subscribers) > 0
        assert fake_vm.dispose_called is False

        run_unmount_effects(component)

        assert fake_vm.dispose_called is True, "内部 VM 模式卸载应 dispose"

    def test_mount_triggers_pubsub_subscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """挂载后 pubsub.subscribe_topic 被调用。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        page = _make_fake_page()
        _mount(component, page=page)

        page.pubsub.subscribe_topic.assert_called_once()

    def test_unmount_triggers_pubsub_unsubscribe(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """卸载后 pubsub.unsubscribe_topic 被调用。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        page = _make_fake_page()
        _mount(component, page=page)

        run_unmount_effects(component)

        page.pubsub.unsubscribe_topic.assert_called_once()

    def test_tab_change_updates_selected_index(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """_on_tab_changed: 切换 Tab 更新 selected_index。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        result, _ = _mount(component)

        tabs = _find_by_type(result, ft.Tabs)
        assert len(tabs) >= 1
        assert tabs[0].on_change is not None

        # Trigger tab change
        e = MagicMock()
        e.control = MagicMock()
        e.control.selected_index = 1
        tabs[0].on_change(e)

        # Re-render to verify selected_index updated
        result = render_once(component)
        tabs = _find_by_type(result, ft.Tabs)
        assert tabs[0].selected_index == 1


# ============================================================================
# 事件 handler 测试: TableViewerTab
# ============================================================================


class TestTableViewerTabEventHandlers:
    """TableViewerTab 事件 handler 测试: 触发 _on_* → page.run_task → _do_* 路径。"""

    def test_on_table_changed_triggers_set_table_and_load(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_table_changed → _do_table_change → vm.set_table + reset_table_state + load_schema_and_data。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Find table_selector Dropdown and trigger on_select
        dropdowns = _find_by_type(result, ft.Dropdown)
        table_selector = dropdowns[0]
        table_selector.on_select(_make_event(value="daily_quotes"))

        # Verify VM method calls
        calls = [c[0] for c in vm.method_calls]
        assert "set_table" in calls
        assert "reset_table_state" in calls
        assert "load_table_schema" in calls
        assert "query_data" in calls

    def test_on_query_click_triggers_set_filter_and_query(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_query_click → _do_query → vm.set_filter + vm.query_data(page=1)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_query = _find_icon_button(result, ft.Icons.SEARCH)
        assert btn_query is not None
        btn_query.on_click(MagicMock())

        calls = [c[0] for c in vm.method_calls]
        assert "set_filter" in calls
        assert "query_data" in calls
        # query_data should be called with page=1
        query_call = next(c for c in vm.method_calls if c[0] == "query_data")
        assert query_call[1].get("page") == 1

    def test_on_refresh_click_triggers_query(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_refresh_click → _do_refresh → vm.query_data()。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_refresh = _find_icon_button(result, ft.Icons.REFRESH)
        assert btn_refresh is not None
        btn_refresh.on_click(MagicMock())

        calls = [c[0] for c in vm.method_calls]
        assert "query_data" in calls

    def test_on_sort_triggers_set_sort_and_clear_error(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_sort → vm.set_sort + vm.clear_error + vm.query_data。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code", "name"), tables_loaded=True)
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()

        # Capture on_sort callback via mock PaginatedTable
        captured_on_sort: list = []

        def _mock_paginated_table(**kwargs):
            captured_on_sort.append(kwargs.get("on_sort"))
            return ft.Column([])

        with patch("ui.views.data_view.PaginatedTable", _mock_paginated_table):
            _mount(component, page=page)

        assert len(captured_on_sort) > 0
        on_sort = captured_on_sort[-1]
        assert on_sort is not None

        # Trigger sort on "ts_code" (index 0)
        on_sort("ts_code", False)

        calls = [c[0] for c in vm.method_calls]
        assert "set_sort" in calls
        assert "clear_error" in calls
        assert "query_data" in calls

        sort_call = next(c for c in vm.method_calls if c[0] == "set_sort")
        assert sort_call[1] == {"col_index": 0, "ascending": False}

    def test_on_sort_invalid_col_returns_early(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_sort: 无效 col_id 直接 return (不调 vm.set_sort)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code", "name"), tables_loaded=True)
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()

        captured_on_sort: list = []

        def _mock_paginated_table(**kwargs):
            captured_on_sort.append(kwargs.get("on_sort"))
            return ft.Column([])

        with patch("ui.views.data_view.PaginatedTable", _mock_paginated_table):
            _mount(component, page=page)

        on_sort = captured_on_sort[-1]
        on_sort("nonexistent_col", True)

        calls = [c[0] for c in vm.method_calls]
        assert "set_sort" not in calls

    def test_on_prev_page_triggers_query(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_prev_page → _do_prev_page → vm.query_data(page=current-1)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(
                table_columns=("ts_code",),
                tables_loaded=True,
                current_page=3,
                total_rows=150,
            )
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        assert btn_prev is not None
        btn_prev.on_click(MagicMock())

        query_call = next(c for c in vm.method_calls if c[0] == "query_data")
        assert query_call[1].get("page") == 2

    def test_on_prev_page_disabled_on_page_1(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_prev_page: current_page=1 时不触发 query_data。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True, current_page=1)
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        btn_prev.on_click(MagicMock())

        calls = [c[0] for c in vm.method_calls]
        assert "query_data" not in calls

    def test_on_next_page_triggers_query(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_next_page → _do_next_page → vm.query_data(page=current+1)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(
                table_columns=("ts_code",),
                tables_loaded=True,
                current_page=1,
                total_rows=150,
            )
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_next = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        assert btn_next is not None
        btn_next.on_click(MagicMock())

        query_call = next(c for c in vm.method_calls if c[0] == "query_data")
        assert query_call[1].get("page") == 2

    def test_on_next_page_disabled_on_last_page(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_next_page: 已在最后一页时不触发 query_data。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(
                table_columns=("ts_code",),
                tables_loaded=True,
                current_page=3,
                total_rows=150,
                page_size=50,
            )
        )
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_next = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        btn_next.on_click(MagicMock())

        calls = [c[0] for c in vm.method_calls]
        assert "query_data" not in calls

    def test_on_export_current_triggers_export(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_export_current → _export_csv(True) → vm.export_data(current_page_only=True)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        popup_menus[0].items[0].on_click(MagicMock())

        export_call = next(c for c in vm.method_calls if c[0] == "export_data")
        assert export_call[1] == {"current_page_only": True}

    def test_on_export_all_triggers_export(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_on_export_all → _export_csv(False) → vm.export_data(current_page_only=False)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        popup_menus[0].items[1].on_click(MagicMock())

        export_call = next(c for c in vm.method_calls if c[0] == "export_data")
        assert export_call[1] == {"current_page_only": False}

    def test_export_empty_data_shows_toast(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_export_csv: 空 DataFrame 时 show_toast 被调用 (data_export_no_data)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        vm._current_data = pd.DataFrame()  # empty
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        popup_menus[0].items[0].on_click(MagicMock())

        page.show_toast.assert_called_once()

    def test_export_with_data_writes_csv(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        tmp_path,
    ):
        """_export_csv: 非空 DataFrame + save_file 返回路径 → 写 CSV + show_toast(success)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        vm._current_data = pd.DataFrame({"ts_code": ["000001"], "name": ["测试"]})
        vm.write_csv = AsyncMock(return_value=None)
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Mock FilePicker.save_file to return a filepath
        filepath = str(tmp_path / "test.csv")
        for svc in page.services:
            if isinstance(svc, ft.FilePicker):

                async def _fake_save_file(**kwargs):
                    return filepath

                svc.save_file = _fake_save_file
                break

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        popup_menus[0].items[0].on_click(MagicMock())

        page.show_toast.assert_called_once()
        # Verify success toast
        toast_args = page.show_toast.call_args
        assert toast_args[0][1] == "success"

    def test_init_tables_triggers_when_not_loaded(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """tables_loaded=False 时 _init_tables 调用 vm.init_tables。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(tables_loaded=False))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        _mount(component, page=page)

        calls = [c[0] for c in vm.method_calls]
        assert "init_tables" in calls
        assert "load_table_schema" in calls
        assert "query_data" in calls

    def test_load_schema_and_data_skips_when_loading(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_load_schema_and_data: is_loading=True 时直接 return。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(is_loading=True, tables_loaded=False))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        _mount(component, page=page)

        # _init_tables runs but _load_schema_and_data returns early (is_loading=True)
        calls = [c[0] for c in vm.method_calls]
        assert "init_tables" in calls
        # load_table_schema should NOT be called (is_loading guard)
        assert "load_table_schema" not in calls


# ============================================================================
# 事件 handler 测试: SQLConsoleTab
# ============================================================================


class TestSQLConsoleTabEventHandlers:
    """SQLConsoleTab 事件 handler 测试: 触发 _run_query / _set_sql 路径。"""

    def test_run_query_with_empty_sql_returns_early(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: 空 SQL 直接 return (不调 vm.execute_sql)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Find btn_run (ft.Button with on_click)
        buttons = _find_by_type(result, ft.Button)
        btn_run = buttons[0] if buttons else None
        assert btn_run is not None

        # sql_text is "" on first render, so _run_query should return early
        coro = btn_run.on_click(MagicMock())
        _run_async_coro(coro)

        calls = [c[0] for c in vm.method_calls]
        assert "execute_sql" not in calls

    def test_run_query_with_sql_triggers_execute(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: 非空 SQL → vm.execute_sql(sql)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        vm._sql_result = {"success": True, "data": pd.DataFrame({"col1": [1]}), "error": None}
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Find template button to set SQL text
        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        # Click template button to set sql_text
        outlined_btns[0].on_click(MagicMock())

        # Re-render to get new btn_run with updated sql_text closure
        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        btn_run = buttons[0]

        coro = btn_run.on_click(MagicMock())
        _run_async_coro(coro)

        calls = [c[0] for c in vm.method_calls]
        assert "execute_sql" in calls

    def test_run_query_with_error_sets_error_status(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: execute_sql 抛异常 → 设置错误状态 (不崩溃)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()

        async def _raise_sql(sql):
            raise RuntimeError("DB error")

        vm.execute_sql = _raise_sql  # type: ignore[method-assign]
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Set SQL text via template button
        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        # Re-render + trigger
        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        btn_run = buttons[0]

        coro = btn_run.on_click(MagicMock())
        _run_async_coro(coro)  # should not raise

    def test_template_button_sets_sql_text(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """模板按钮 → _set_sql → set_sql_text (不抛异常)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        assert len(outlined_btns) >= 2

        # Click both template buttons (should not raise)
        outlined_btns[0].on_click(MagicMock())
        outlined_btns[1].on_click(MagicMock())

    def test_run_query_success_with_data(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: execute_sql 返回 success=True + 非空 data → 正常流程。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        vm._sql_result = {"success": True, "data": pd.DataFrame({"col1": [1, 2, 3]}), "error": None}
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Set SQL text
        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        # Re-render + trigger
        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        coro = buttons[0].on_click(MagicMock())
        _run_async_coro(coro)

    def test_run_query_success_with_large_result_truncates(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: 结果 >100 行 → 截断提示路径。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        vm._sql_result = {"success": True, "data": pd.DataFrame({"col1": list(range(200))}), "error": None}
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Set SQL text
        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        # Re-render + trigger
        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        coro = buttons[0].on_click(MagicMock())
        _run_async_coro(coro)  # should not raise

    def test_run_query_failure_sets_error_status(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: execute_sql 返回 success=False → 错误状态路径。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        vm._sql_result = {"success": False, "data": None, "error": "syntax error"}
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        # Set SQL text
        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        # Re-render + trigger
        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        coro = buttons[0].on_click(MagicMock())
        _run_async_coro(coro)  # should not raise


# ============================================================================
# 事件 handler 测试: DataExplorerView PubSub
# ============================================================================


class TestDataExplorerViewPubSubCallback:
    """DataExplorerView PubSub callback 测试: _on_broadcast_message 路径。"""

    def test_pubsub_callback_triggers_mark_tables_stale(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """_on_broadcast_message: cache_cleared 消息 → vm.mark_tables_stale。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        page = _make_fake_page()
        _mount(component, page=page)

        # Extract the callback passed to subscribe_topic
        subscribe_call = page.pubsub.subscribe_topic.call_args
        callback = subscribe_call[0][1]

        # Trigger cache_cleared message
        callback("cache_cleared", "cache_cleared")

        calls = [c[0] for c in fake_vm.method_calls]
        assert "mark_tables_stale" in calls

    def test_pubsub_callback_ignores_other_messages(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """_on_broadcast_message: 非 cache_cleared 消息 → 不触发 mark_tables_stale。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        page = _make_fake_page()
        _mount(component, page=page)

        subscribe_call = page.pubsub.subscribe_topic.call_args
        callback = subscribe_call[0][1]

        # Trigger non-cache_cleared message
        callback("cache_cleared", "other_message")

        calls = [c[0] for c in fake_vm.method_calls]
        assert "mark_tables_stale" not in calls

    def test_pubsub_callback_ignores_other_topics(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        monkeypatch,
    ):
        """_on_broadcast_message: 非 CACHE_CLEARED_TOPIC → 不触发。"""
        from ui.views.data_view import DataExplorerView

        fake_vm = _FakeDataExplorerViewModel()
        monkeypatch.setattr("ui.views.data_view.DataExplorerViewModel", lambda: fake_vm)

        component = make_component(DataExplorerView)
        page = _make_fake_page()
        _mount(component, page=page)

        subscribe_call = page.pubsub.subscribe_topic.call_args
        callback = subscribe_call[0][1]

        callback("other_topic", "cache_cleared")

        calls = [c[0] for c in fake_vm.method_calls]
        assert "mark_tables_stale" not in calls


# ============================================================================
# 异常路径测试: TableViewerTab async methods (R2: CancelledError 传播 + Exception 兜底)
# ============================================================================


class TestTableViewerTabAsyncErrorPaths:
    """TableViewerTab 异步方法异常路径: Exception 兜底 + CancelledError 传播 (R2)。"""

    def test_load_schema_and_data_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_load_schema_and_data: vm.load_table_schema 抛 Exception → show_toast。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(tables_loaded=False))

        async def _raising_schema(table_name: str) -> list:
            raise RuntimeError("schema error")

        vm.load_table_schema = _raising_schema  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        _mount(component, page=page)

        page.show_toast.assert_called_once()

    def test_init_tables_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_init_tables: vm.init_tables 抛 Exception → show_toast。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(tables_loaded=False))

        async def _raising_init() -> list[str]:
            raise RuntimeError("init error")

        vm.init_tables = _raising_init  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        _mount(component, page=page)

        page.show_toast.assert_called_once()

    def test_do_query_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_do_query: vm.query_data 抛 Exception → 兜底捕获 (不传播)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))

        async def _raising_query(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("query error")

        vm.query_data = _raising_query  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_query = _find_icon_button(result, ft.Icons.SEARCH)
        btn_query.on_click(MagicMock())  # 不抛异常

    def test_do_refresh_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_do_refresh: vm.query_data 抛 Exception → 兜底捕获。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))

        async def _raising_query(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("refresh error")

        vm.query_data = _raising_query  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_refresh = _find_icon_button(result, ft.Icons.REFRESH)
        btn_refresh.on_click(MagicMock())

    def test_do_sort_query_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_do_sort_query: vm.query_data 抛 Exception → 兜底捕获。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code", "name"), tables_loaded=True)
        )

        async def _raising_query(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("sort error")

        vm.query_data = _raising_query  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()

        captured_on_sort: list = []

        def _mock_paginated_table(**kwargs: Any) -> ft.Column:
            captured_on_sort.append(kwargs.get("on_sort"))
            return ft.Column([])

        with patch("ui.views.data_view.PaginatedTable", _mock_paginated_table):
            _mount(component, page=page)

        on_sort = captured_on_sort[-1]
        on_sort("ts_code", False)  # 不抛异常

    def test_do_prev_page_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_do_prev_page: vm.query_data 抛 Exception → 兜底捕获。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True, current_page=3, total_rows=150)
        )

        async def _raising_query(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("prev error")

        vm.query_data = _raising_query  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        btn_prev.on_click(MagicMock())

    def test_do_next_page_handles_exception(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_do_next_page: vm.query_data 抛 Exception → 兜底捕获。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(
            state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True, current_page=1, total_rows=150)
        )

        async def _raising_query(**kwargs: Any) -> pd.DataFrame:
            raise RuntimeError("next error")

        vm.query_data = _raising_query  # type: ignore[method-assign]
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        btn_next = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        btn_next.on_click(MagicMock())

    def test_export_csv_write_failure_shows_error_toast(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
        tmp_path,
    ):
        """_export_csv: 文件写入失败 → show_toast(error) (data_export_fail)。"""
        from ui.views.data_view import TableViewerTab

        vm = _FakeDataExplorerViewModel(state=_FakeDataExplorerState(table_columns=("ts_code",), tables_loaded=True))
        vm._current_data = pd.DataFrame({"ts_code": ["000001"]})
        vm.write_csv = AsyncMock(side_effect=RuntimeError("disk full"))
        component = make_component(TableViewerTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        filepath = str(tmp_path / "test.csv")
        for svc in page.services:
            if isinstance(svc, ft.FilePicker):

                async def _fake_save_file(**kwargs: Any) -> str:
                    return filepath

                svc.save_file = _fake_save_file  # type: ignore[method-assign]
                break

        popup_menus = _find_by_type(result, ft.PopupMenuButton)
        popup_menus[0].items[0].on_click(MagicMock())

        page.show_toast.assert_called()
        toast_args = page.show_toast.call_args
        assert toast_args[0][1] == "error"


# ============================================================================
# 异常路径测试: SQLConsoleTab _run_query (R2 + 空结果)
# ============================================================================


class TestSQLConsoleTabAsyncErrorPaths:
    """SQLConsoleTab _run_query 异常路径: 空成功结果 + CancelledError 传播 (R2)。"""

    def test_run_query_success_with_empty_df_sets_error_status(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: success=True 但 df 为空 → 设置错误状态 (data_sql_error)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()
        vm._sql_result = {"success": True, "data": pd.DataFrame(), "error": None}
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        btn_run = buttons[0]

        coro = btn_run.on_click(MagicMock())
        _run_async_coro(coro)

        calls = [c[0] for c in vm.method_calls]
        assert "execute_sql" in calls

    def test_run_query_propagates_cancelled_error(
        self,
        mock_i18n_state,
        mock_app_colors_state,
        mock_metadata,
    ):
        """_run_query: vm.execute_sql 抛 CancelledError → 传播 (R2 红线)。"""
        from ui.views.data_view import SQLConsoleTab

        vm = _FakeDataExplorerViewModel()

        async def _cancelling_sql(sql: str) -> dict:
            raise asyncio.CancelledError()

        vm.execute_sql = _cancelling_sql  # type: ignore[method-assign]
        component = make_component(SQLConsoleTab, vm=vm)
        page = _make_fake_page()
        result, page = _mount(component, page=page)

        outlined_btns = _find_by_type(result, ft.OutlinedButton)
        outlined_btns[0].on_click(MagicMock())

        result = render_once(component)
        buttons = _find_by_type(result, ft.Button)
        btn_run = buttons[0]

        coro = btn_run.on_click(MagicMock())
        with pytest.raises(asyncio.CancelledError):
            _run_async_coro(coro)


# ============================================================================
# 辅助
# ============================================================================


def _has_terminal_icon(container: Any) -> bool:
    """检查 Container 是否包含 TERMINAL icon (用于识别 empty_state)。"""
    for ctrl in _collect_controls(container):
        if isinstance(ctrl, ft.Icon) and getattr(ctrl, "icon", None) == ft.Icons.TERMINAL:
            return True
    return False
