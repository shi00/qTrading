"""virtual_table 契约守护测试 — Phase B.3 声明式重写。

覆盖:
- 纯函数: next_sort_state / window_capacity / compute_window / _total_width / _ScrollCache
- 组件契约: @ft.component 装饰标记、参数签名、返回类型注解、禁止命令式 API (源码检查)
- 虚拟化逻辑: viewport 窗口计算 (窗口大小、缓冲、末尾 clamp、空表)

声明式组件组合 (@ft.component + use_state/use_effect/use_ref) 是有状态的, 在无 renderer
环境下会抛 RuntimeError, 由集成测试 (flet_test_page fixture) 覆盖, 不在本单元测试范围
(对齐 test_resizable_splitter.py / test_task_center_view.py 模式)。
"""

import inspect
from pathlib import Path

import flet as ft
import pytest

from ui.components.virtual_table import (
    BUFFER_ROWS,
    DEFAULT_VIEWPORT_ROWS,
    MIN_TABLE_WIDTH,
    ROW_HEIGHT,
    PaginatedTable,
    _ScrollCache,
    _total_width,
    compute_window,
    next_sort_state,
    window_capacity,
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


# --- 2. window_capacity (视口容量纯函数) ---


class TestWindowCapacity:
    def test_zero_viewport_uses_default(self):
        """viewport_h=0 时用 DEFAULT_VIEWPORT_ROWS + 2*BUFFER_ROWS。"""
        assert window_capacity(0.0) == DEFAULT_VIEWPORT_ROWS + 2 * BUFFER_ROWS

    def test_negative_viewport_uses_default(self):
        assert window_capacity(-1.0) == DEFAULT_VIEWPORT_ROWS + 2 * BUFFER_ROWS

    def test_explicit_viewport_height(self):
        """viewport_h=20*ROW_HEIGHT → 20 + 2*BUFFER_ROWS。"""
        assert window_capacity(20 * ROW_HEIGHT) == 20 + 2 * BUFFER_ROWS

    def test_taller_viewport_larger_capacity(self):
        assert window_capacity(60 * ROW_HEIGHT) == 60 + 2 * BUFFER_ROWS

    def test_smaller_viewport_smaller_capacity(self):
        assert window_capacity(10 * ROW_HEIGHT) == 10 + 2 * BUFFER_ROWS

    def test_non_exact_multiple_ceils_up(self):
        """非整数倍向上取整。"""
        # 20.5 行 → ceil(20.5)=21
        assert window_capacity(20.5 * ROW_HEIGHT) == 21 + 2 * BUFFER_ROWS

    def test_minimum_one_row(self):
        """极小 viewport (小于一行) 至少返回 1 + 2*BUFFER_ROWS。"""
        assert window_capacity(1.0) == 1 + 2 * BUFFER_ROWS


# --- 3. compute_window (虚拟化窗口纯函数) ---


class TestComputeWindow:
    def test_empty_rows_returns_zero_window(self):
        assert compute_window(0, 0, 0.0) == (0, 0)

    def test_default_window_bounded_by_capacity(self):
        """500 行, 默认视口 → 窗口大小 = 30 + 2*8 = 46。"""
        start, end = compute_window(0, 500, 0.0)
        assert (start, end) == (0, 46)
        assert end - start == 46

    def test_initial_window_starts_at_zero(self):
        start, _ = compute_window(0, 500, 0.0)
        assert start == 0

    def test_scroll_shifts_window_start(self):
        """滚动到 200 行后, 窗口 start > 0 (留 BUFFER_ROWS 缓冲)。"""
        start, end = compute_window(200, 500, 20 * ROW_HEIGHT)
        assert start == 200 - BUFFER_ROWS
        assert start > 0
        # 窗口大小不变 (受视口容量约束)
        assert end - start == 20 + 2 * BUFFER_ROWS

    def test_window_never_exceeds_row_count(self):
        """末尾 clamp: 窗口 end 不超过 row_count。"""
        start, end = compute_window(490, 500, 0.0)
        assert end == 500
        assert end - start <= 46
        assert start == 500 - 46  # clamp 到末尾

    def test_small_row_count_renders_all(self):
        """行数 < 容量时全部渲染。"""
        start, end = compute_window(0, 5, 0.0)
        assert (start, end) == (0, 5)

    def test_viewport_height_changes_capacity(self):
        """taller 视口 → 更大窗口 (虚拟化适配视口尺寸)。"""
        _, end_default = compute_window(0, 500, 0.0)
        _, end_tall = compute_window(0, 500, 60 * ROW_HEIGHT)
        assert end_tall > end_default
        assert end_tall == 60 + 2 * BUFFER_ROWS


# --- 4. _total_width ---


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


# --- 5. _ScrollCache (use_ref 缓存即时数值) ---


class TestScrollCache:
    def test_initial_state(self):
        cache = _ScrollCache()
        assert cache.last_first == -1
        assert cache.last_viewport_h == 0.0

    def test_last_first_assignable_to_int(self):
        cache = _ScrollCache()
        cache.last_first = 42
        assert cache.last_first == 42

    def test_last_viewport_h_assignable_to_float(self):
        cache = _ScrollCache()
        cache.last_viewport_h = 600.0
        assert cache.last_viewport_h == 600.0

    def test_has_slots_no_dict(self):
        """_ScrollCache 用 __slots__ (轻量缓存, 无 __dict__ 开销)。"""
        cache = _ScrollCache()
        assert not hasattr(cache, "__dict__")


# --- 6. 组件契约 (声明式标记 + 签名 + 禁止命令式 API) ---


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

    def test_virtualization_pure_functions_present(self):
        """DoD: 虚拟化逻辑以纯函数形式保留 (compute_window / window_capacity)。"""
        assert "def compute_window" in _raw_source()
        assert "def window_capacity" in _raw_source()

    def test_no_pagerefmixin_import(self):
        """模块不得依赖 PageRefMixin (CLAUDE.md §3.3 技术债消除)。"""
        import ui.components.virtual_table as mod

        assert not hasattr(mod, "PageRefMixin")
        assert "PageRefMixin" not in dir(mod)


# --- 7. 无 renderer 环境下组件实例化抛 RuntimeError (契约验证) ---


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
