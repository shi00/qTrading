"""ui/views/data_view.py 声明式契约守护测试 (Phase F.2).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref)
2. 模块级纯函数测试 (_format_cell_value/_build_filter_op_options/_ceil_div 等)

业务逻辑覆盖 (DataExplorerViewModel 读写 + 异步查询 + 表格渲染) 由集成测试
(flet_test_page fixture) 与 ViewModel 单测 (test_data_explorer_view_model.py) 承担,
声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pandas as pd
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
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
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.views.data_view as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.data_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (TableViewerTab / SQLConsoleTab / DataExplorerView)
# ============================================================================


class TestDataViewDeclarativeContract:
    """data_view.py 声明式契约守护测试 (Phase F.2)。"""

    def test_three_components_are_ft_component(self):
        """DoD: 三个组件必须被 @ft.component 装饰。"""
        from ui.views.data_view import DataExplorerView, SQLConsoleTab, TableViewerTab

        assert hasattr(TableViewerTab, "__wrapped__"), "TableViewerTab 必须用 @ft.component 装饰"
        assert hasattr(SQLConsoleTab, "__wrapped__"), "SQLConsoleTab 必须用 @ft.component 装饰"
        assert hasattr(DataExplorerView, "__wrapped__"), "DataExplorerView 必须用 @ft.component 装饰"

    def test_uses_ft_component_decorator(self):
        """DoD: 源码必须包含 3 个 @ft.component 装饰器。"""
        assert _raw_source().count("@ft.component") == 3, "必须包含 3 个 @ft.component 装饰器"

    def test_no_imperative_class_table_viewer(self):
        """DoD: 禁止命令式 class TableViewerTab(ft.Container)。"""
        assert "class TableViewerTab(" not in _code_source(), "TableViewerTab 不应是 class (命令式)"

    def test_no_imperative_class_sql_console(self):
        """DoD: 禁止命令式 class SQLConsoleTab(ft.Container)。"""
        assert "class SQLConsoleTab(" not in _code_source(), "SQLConsoleTab 不应是 class (命令式)"

    def test_no_imperative_class_data_explorer(self):
        """DoD: 禁止命令式 class DataExplorerView(ft.Container)。"""
        assert "class DataExplorerView(" not in _code_source(), "DataExplorerView 不应是 class (命令式)"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme (声明式通过 Observable state 自动重渲染)。"""
        assert "update_theme" not in _code_source(), "不应使用 update_theme (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 I18n.get_observable_state (i18n 自动重渲染)。"""
        assert "I18n.get_observable_state" in _raw_source(), "必须订阅 I18n.get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_viewmodel(self):
        """DoD: 必须通过 use_viewmodel hook 消费 DataExplorerViewModel。"""
        assert "use_viewmodel" in _raw_source(), "必须使用 use_viewmodel hook"
        assert "DataExplorerViewModel" in _raw_source(), "必须消费 DataExplorerViewModel"

    def test_consumes_paginated_table(self):
        """DoD: 必须函数调用消费 PaginatedTable (props 推送)。"""
        assert "PaginatedTable(" in _code_source(), "必须函数调用 PaginatedTable(rows=..., columns=...)"

    def test_filepicker_in_use_effect(self):
        """DoD: FilePicker 必须通过 use_effect 注册到 page.services (非裸代码)。"""
        # page.services.append 必须出现在 use_effect setup 函数内, 非顶层裸调用
        assert "page.services.append" in _code_source(), "FilePicker 必须注册到 page.services"
        assert "ft.use_ref(lambda: ft.FilePicker())" in _code_source(), "FilePicker 必须通过 use_ref 持有实例"
        # 验证 append 在 _setup_file_picker 函数内 (use_effect setup)
        code = _code_source()
        append_idx = code.find("page.services.append")
        setup_idx = code.find("def _setup_file_picker")
        effect_idx = code.find("ft.use_effect(_setup_file_picker")
        assert setup_idx != -1 and append_idx != -1 and effect_idx != -1
        assert setup_idx < append_idx < effect_idx, (
            "page.services.append 必须在 _setup_file_picker (use_effect setup) 内, 在 use_effect 注册之前"
        )

    def test_pubsub_in_use_effect_with_cleanup(self):
        """DoD: PubSub 必须用 use_effect + cleanup 订阅/退订。"""
        code = _code_source()
        assert "page.pubsub.subscribe_topic" in code, "PubSub 必须在 use_effect setup 中用 subscribe_topic 订阅"
        assert "page.pubsub.unsubscribe_topic" in code, "PubSub 必须在 use_effect cleanup 中用 unsubscribe_topic 退订"
        assert "ft.use_effect(_setup_pubsub" in code, "PubSub 必须通过 use_effect 注册"
        assert "cleanup=_cleanup_pubsub" in code, "PubSub 必须注册 cleanup 退订函数"

    def test_r2_cancelled_error_propagation(self):
        """DoD: R2 — asyncio.CancelledError 必须被 raise (不被 except Exception 捕获)。"""
        code = _code_source()
        # 每个异步 handler 都应有 except asyncio.CancelledError: raise
        assert "except asyncio.CancelledError:" in code, "必须有 CancelledError 捕获"
        assert code.count("raise") >= code.count("except asyncio.CancelledError:"), (
            "每个 except asyncio.CancelledError 必须配合 raise"
        )

    def test_no_optional_union_type(self):
        """DoD: R6 — 禁止 Union[X, Y] / Optional[X] (必须用 X | Y / X | None)。"""
        code = _code_source()
        assert "Optional[" not in code, "不应使用 Optional[X] (R6: 用 X | None)"
        assert "Union[" not in code, "不应使用 Union[X, Y] (R6: 用 X | Y)"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestFormatCellValue:
    """_format_cell_value 模块级纯函数测试。"""

    def test_none_returns_dash(self):
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(None, "col") == "-"

    def test_nan_returns_dash(self):
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(float("nan"), "col") == "-"

    def test_string_returns_as_is(self):
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value("hello", "col") == "hello"

    def test_int_returns_str(self):
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value(42, "col") == "42"

    def test_date_string_8digits_formats(self):
        from ui.views.data_view import _format_cell_value

        assert _format_cell_value("20240115", "trade_date") == "2024-01-15"

    def test_date_datetime_formats(self):
        import datetime

        from ui.views.data_view import _format_cell_value

        d = datetime.date(2024, 1, 15)
        assert _format_cell_value(d, "trade_date") == "2024-01-15"

    def test_non_date_string_8digits_not_formatted(self):
        from ui.views.data_view import _format_cell_value

        # 非 date 列的 8 位数字串不格式化
        assert _format_cell_value("20240115", "code") == "20240115"


class TestBuildFilterOpOptions:
    """_build_filter_op_options 模块级纯函数测试。"""

    def test_returns_seven_operators(self):
        from ui.views.data_view import _build_filter_op_options

        options = _build_filter_op_options()
        assert len(options) == 7

    def test_options_are_dropdown_option_instances(self):
        from ui.views.data_view import _build_filter_op_options

        options = _build_filter_op_options()
        for opt in options:
            assert isinstance(opt, ft.dropdown.Option)

    def test_includes_like_operator(self):
        from ui.views.data_view import _build_filter_op_options

        options = _build_filter_op_options()
        keys = [opt.key for opt in options]
        assert "LIKE" in keys
        assert "=" in keys


class TestCeilDiv:
    """_ceil_div 模块级纯函数测试。"""

    def test_exact_division(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(100, 50) == 2

    def test_ceil_rounds_up(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(101, 50) == 3

    def test_zero_numerator(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(0, 50) == 0

    def test_zero_denominator_returns_one(self):
        from ui.views.data_view import _ceil_div

        assert _ceil_div(100, 0) == 1


class TestDfToRows:
    """_df_to_rows 模块级纯函数测试。"""

    def test_empty_df_returns_empty_list(self):
        from ui.views.data_view import _df_to_rows

        df = pd.DataFrame()
        assert _df_to_rows(df, ("col1",)) == []

    def test_formats_df_to_dict_rows(self):
        from ui.views.data_view import _df_to_rows

        df = pd.DataFrame({"col1": [1, 2], "col2": ["a", "b"]})
        rows = _df_to_rows(df, ("col1", "col2"))
        assert len(rows) == 2
        assert rows[0]["col1"] == "1"
        assert rows[0]["col2"] == "a"
        assert rows[1]["col1"] == "2"

    def test_none_value_becomes_dash(self):
        from ui.views.data_view import _df_to_rows

        df = pd.DataFrame({"col1": [None]})
        rows = _df_to_rows(df, ("col1",))
        assert rows[0]["col1"] == "-"


class TestBuildTableSelectorOptions:
    """_build_table_selector_options 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.patches = [patch("ui.views.data_view.MetaDataManager")]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_option_per_table(self):
        from ui.views.data_view import _build_table_selector_options

        options = _build_table_selector_options(("stock_basic", "daily_quotes"))
        assert len(options) == 2

    def test_option_keys_match_table_names(self):
        from ui.views.data_view import _build_table_selector_options

        options = _build_table_selector_options(("stock_basic", "daily_quotes"))
        keys = [opt.key for opt in options]
        assert keys == ["stock_basic", "daily_quotes"]

    def test_empty_tables_returns_empty_list(self):
        from ui.views.data_view import _build_table_selector_options

        assert _build_table_selector_options(()) == []


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        from ui.views.data_view import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.data_view.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        from ui.views.data_view import _get_page

        with patch("ui.views.data_view.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None
