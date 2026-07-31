"""virtual_table 契约守护测试 — Phase B.3 声明式重写 (方案 D: ListView 原生虚拟化)。

覆盖:
- 纯函数: next_sort_state / _total_width
- 组件契约: @ft.component 装饰标记、参数签名、返回类型注解、禁止命令式 API (源码检查)
- ListView 原生虚拟化配置契约 (build_controls_on_demand / item_extent / cache_extent / key)

声明式组件组合 (@ft.component + use_state) 是有状态的, 在无 renderer
环境下会抛 RuntimeError, 由集成测试 (flet_test_page fixture) 覆盖, 不在本单元测试范围
(对齐 test_resizable_splitter.py / test_task_center_view.py 模式)。

变更要点 (方案 D):
- 删除自实现虚拟化 (compute_window / window_capacity / _ScrollCache / DEFAULT_VIEWPORT_ROWS / RERENDER_THRESHOLD)
- 改用 ListView 原生 build_controls_on_demand + item_extent + cache_extent + key
"""

import inspect
from pathlib import Path

import flet as ft
import pytest

from ui.components.virtual_table import (
    MIN_TABLE_WIDTH,
    PaginatedTable,
    _total_width,
    next_sort_state,
)

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
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
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.components.virtual_table as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.virtual_table as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# --- 1. next_sort_state (排序状态转移纯函数) ---


class TestNextSortState:
    def test_new_column_defaults_ascending(self):
        """点击新列默认升序 (消解原 test_paginated_table_new_column_defaults_ascending)。"""
        new_col, new_asc = next_sort_state("A", False, "B")
        assert new_col == "B"
        assert new_asc is True

    def test_same_column_toggles_ascending(self):
        """点击当前列翻转方向。"""
        new_col, new_asc = next_sort_state("A", True, "A")
        assert new_col == "A"
        assert new_asc is False

    def test_same_column_toggles_descending(self):
        new_col, new_asc = next_sort_state("A", False, "A")
        assert new_col == "A"
        assert new_asc is True

    def test_no_sort_col_first_click_ascending(self):
        """初始无排序列, 首次点击默认升序。"""
        new_col, new_asc = next_sort_state(None, True, "price")
        assert new_col == "price"
        assert new_asc is True


# --- 2. _total_width ---


class TestTotalWidth:
    def test_sum_of_column_widths(self):
        """列宽总和 > MIN_TABLE_WIDTH 时返回总和 (不被 clamp)。"""
        cols = [{"id": "a", "width": 500}, {"id": "b", "width": 400}]
        assert _total_width(cols) == 900

    def test_clamped_to_min(self):
        """列宽总和 < MIN_TABLE_WIDTH 时 clamp 到 MIN_TABLE_WIDTH。"""
        cols = [{"id": "a", "width": 100}]
        assert _total_width(cols) == MIN_TABLE_WIDTH

    def test_missing_width_defaults_100(self):
        cols = [{"id": "a"}, {"id": "b"}]
        assert _total_width(cols) == MIN_TABLE_WIDTH  # 200 < 800

    def test_empty_columns_returns_min(self):
        assert _total_width([]) == MIN_TABLE_WIDTH


# --- 3. 组件契约 (声明式标记 + 签名 + 禁止命令式 API) ---


class TestComponentContract:
    """验证 PaginatedTable 是 @ft.component 声明式函数组件。"""

    def test_is_callable(self):
        assert callable(PaginatedTable)

    def test_has_wrapped_attribute(self):
        """@ft.component 装饰后保留 __wrapped__ 指向原函数。"""
        assert hasattr(PaginatedTable, "__wrapped__")

    def test_no_class_inheritance(self):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert "class PaginatedTable(" not in _code_source()

    def test_return_annotation_is_column(self):
        """返回类型注解为 ft.Column (声明式组件返回控件)。"""
        sig = inspect.signature(PaginatedTable)
        assert sig.return_annotation is ft.Column

    def test_signature_defaults(self):
        """参数默认值契约。"""
        sig = inspect.signature(PaginatedTable)
        params = sig.parameters
        assert params["rows"].default is None
        assert params["columns"].default is None
        assert params["sort_col"].default is None
        assert params["sort_asc"].default is True
        assert params["on_sort"].default is None
        assert params["on_row_click"].default is None

    def test_no_set_rows(self):
        assert "set_rows" not in _code_source()

    def test_no_set_columns(self):
        assert "set_columns" not in _code_source()

    def test_no_update_theme(self):
        assert "update_theme" not in _code_source()

    def test_no_refresh_viewport(self):
        assert "refresh_viewport" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_did_mount(self):
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        assert "will_unmount" not in _code_source()

    def test_no_handle_sort_click_method(self):
        """排序逻辑已抽为纯函数 next_sort_state, 不再保留命令式 _handle_sort_click。"""
        assert "_handle_sort_click" not in _code_source()

    def test_subscribes_app_colors(self):
        """DoD: 必须订阅 AppColors.get_observable_state (Layer 2 表格色自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source()

    def test_uses_ft_component_decorator(self):
        """DoD: 必须用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source()

    def test_no_self_implemented_virtualization(self):
        """DoD (方案 D): 自实现虚拟化已删除 (compute_window / window_capacity / _ScrollCache)。"""
        assert "def compute_window" not in _raw_source()
        assert "def window_capacity" not in _raw_source()
        assert "class _ScrollCache" not in _raw_source()
        assert "_ScrollCache" not in _code_source()

    def test_no_scroll_state_or_ref(self):
        """DoD (方案 D): 不再使用 scroll_first/viewport_h state 与 scroll_ref。"""
        src = _code_source()
        assert "scroll_first" not in src
        assert "viewport_h" not in src
        assert "scroll_ref" not in src
        assert "set_scroll_first" not in src
        assert "set_viewport_h" not in src

    def test_no_rerender_threshold_constant(self):
        """DoD (方案 D): RERENDER_THRESHOLD / DEFAULT_VIEWPORT_ROWS 常量已删除。

        用 _code_source() (去除 docstring) 检查, 因模块 docstring 会提及已删除的符号名作为变更说明。
        """
        src = _code_source()
        assert "RERENDER_THRESHOLD" not in src
        assert "DEFAULT_VIEWPORT_ROWS" not in src

    def test_no_pagerefmixin_import(self):
        """模块不得依赖 PageRefMixin (CLAUDE.md §3.3 技术债消除)。"""
        import ui.components.virtual_table as mod

        assert not hasattr(mod, "PageRefMixin")
        assert "PageRefMixin" not in dir(mod)


# --- 4. ListView 原生虚拟化配置契约 (方案 D) ---


class TestListViewNativeVirtualization:
    """验证 PaginatedTable 使用 ListView 原生虚拟化能力 (build_controls_on_demand + item_extent + cache_extent + key)。

    这些属性是方案 D 的核心契约: 删除自实现虚拟化后, 由 Flet 引擎层按需构建行控件。
    显式声明 (而非依赖默认值) 以便契约测试可断言源码标记。
    """

    def test_list_view_build_controls_on_demand_declared(self):
        """DoD: ListView 显式声明 build_controls_on_demand=True (原生按需构建)。"""
        assert "build_controls_on_demand=True" in _code_source()

    def test_list_view_item_extent_is_row_height(self):
        """DoD: ListView.item_extent=ROW_HEIGHT (固定行高, Flutter 跳过测量)。"""
        assert "item_extent=ROW_HEIGHT" in _code_source()

    def test_list_view_cache_extent_declared(self):
        """DoD: ListView.cache_extent 显式设置 (上下缓冲, 替代自实现 BUFFER_ROWS)。"""
        assert "cache_extent=" in _code_source()
        assert "BUFFER_ROWS" in _code_source()

    def test_list_view_key_declared(self):
        """DoD: ListView.key 显式设置 (rows 变化时重建以重置滚动位置)。"""
        assert "key=" in _code_source()

    def test_no_stack_canvas_layer(self):
        """DoD (方案 D): 不再使用 ft.Stack 作为虚拟化画布层。"""
        assert "ft.Stack" not in _code_source()

    def test_no_on_scroll_handler(self):
        """DoD (方案 D): 不再挂 _on_scroll handler (虚拟化由引擎层接管)。"""
        src = _code_source()
        assert "on_scroll=" not in src
        assert "def _on_scroll" not in src


# --- 5. 无 renderer 环境下组件实例化抛 RuntimeError (契约验证) ---


class TestRendererRequirement:
    """有状态 @ft.component 在无 renderer 下抛 RuntimeError (由集成测试覆盖渲染)。"""

    def test_calling_without_renderer_raises(self):
        """无 renderer 环境下调用 PaginatedTable 抛 RuntimeError。

        这是有状态声明式组件的预期行为 (含 use_state/use_effect/use_ref), 验证组件确实
        依赖 renderer 上下文, 而非静默返回错误结果。集成测试用 flet_test_page 覆盖。
        """
        with pytest.raises(RuntimeError):
            PaginatedTable(
                rows=[{"name": "S0"}],
                columns=[{"id": "name", "label": "Name", "width": 100}],
            )
