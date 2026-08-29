"""virtual_table 契约守护测试 — Phase B.3 声明式重写 (方案 D: ListView 原生虚拟化)。

覆盖:
- 纯函数: next_sort_state / _total_width / _col_width / _clamp_width / _ColWidthsCache
- 组件契约: @ft.component 装饰标记、参数签名、返回类型注解、禁止命令式 API (源码检查)
- 方案 D-v3 布局契约: 水平滚动 HScroll Row / sticky header / Inner Column 不设 expand /
  VScroll AUTO / 顶层不再持有 hovered_idx / 列宽拖拽常量

声明式组件组合 (@ft.component + use_state) 是有状态的, 在无 renderer
环境下会抛 RuntimeError, 由集成测试 (flet_test_page fixture) 覆盖, 不在本单元测试范围
(对齐 test_resizable_splitter.py / test_task_center_view.py 模式)。

变更要点 (方案 D):
- 删除自实现虚拟化 (compute_window / window_capacity / _ScrollCache / DEFAULT_VIEWPORT_ROWS / RERENDER_THRESHOLD)
- 改用 ListView 原生 build_controls_on_demand + item_extent + cache_extent + key
变更要点 (方案 D-v3):
- 外层 HScroll Row(scroll=AUTO, expand=True, vertical_alignment=STRETCH) 承载水平滚动
- Header 抽到 Inner Column 第一行 (sticky), VScroll 内不含 Header
- Inner Column 不设 expand=True, 仅设 width=total_w
- VScroll Column(scroll=AUTO, expand=True, key) 承载垂直滚动
- 顶层 hovered_idx 下沉到 TableRow 行内 use_state
"""

import inspect
from pathlib import Path

import flet as ft
import pytest

from ui.components.virtual_table import (
    MAX_COL_WIDTH,
    MIN_COL_WIDTH,
    MIN_TABLE_WIDTH,
    PaginatedTable,
    ROW_HEIGHT,
    _ColWidthsCache,
    _clamp_width,
    _col_width,
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


# --- 2. _total_width / _col_width / _clamp_width / _ColWidthsCache ---


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

    def test_col_widths_override(self):
        """col_widths 覆盖列定义宽度 (拖拽后 total_w 随之变化)。"""
        # 默认 400+400=800 (=MIN_TABLE_WIDTH); 覆盖 a→500 后 900 (> MIN_TABLE_WIDTH, 不被 clamp)
        cols = [{"id": "a", "width": 400}, {"id": "b", "width": 400}]
        assert _total_width(cols, {"a": 500}) == 900


class TestColWidth:
    def test_uses_dragged_width_when_present(self):
        assert _col_width({"a": 250}, {"id": "a", "width": 100}) == 250

    def test_falls_back_to_column_default(self):
        assert _col_width(None, {"id": "a", "width": 120}) == 120

    def test_falls_back_to_100_when_missing(self):
        assert _col_width(None, {"id": "a"}) == 100

    def test_empty_col_widths_uses_default(self):
        assert _col_width({}, {"id": "a", "width": 150}) == 150


class TestClampWidth:
    def test_clamps_to_min(self):
        assert _clamp_width(10, MIN_COL_WIDTH, MAX_COL_WIDTH) == MIN_COL_WIDTH

    def test_clamps_to_max(self):
        assert _clamp_width(1000, MIN_COL_WIDTH, MAX_COL_WIDTH) == MAX_COL_WIDTH

    def test_keeps_float_truncated_in_range(self):
        assert _clamp_width(200.7, MIN_COL_WIDTH, MAX_COL_WIDTH) == 200

    def test_constants_values(self):
        assert MIN_COL_WIDTH == 60
        assert MAX_COL_WIDTH == 600

    def test_row_height_meets_minimum(self):
        """UX-11 (P2-03): 行高 30→32, 高于 WCAG 2.2 的 24px 最低目标并预留余量 (Plans 验收 ≥32)。"""
        assert ROW_HEIGHT == 32


class TestColWidthsCache:
    def test_slots_contract(self):
        """_ColWidthsCache 必须用 __slots__ = (widths, active_col, last_time) (对齐 _DragCache)。"""
        assert _ColWidthsCache.__slots__ == ("widths", "active_col", "last_time")

    def test_initial_state(self):
        cache = _ColWidthsCache()
        assert cache.widths == {}
        assert cache.active_col is None
        assert cache.last_time == 0.0


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
        """参数默认值契约 (方案 §8.3: 不新增 props)。"""
        sig = inspect.signature(PaginatedTable)
        params = sig.parameters
        assert params["rows"].default is None
        assert params["columns"].default is None
        assert params["sort_col"].default is None
        assert params["sort_asc"].default is True
        assert params["on_sort"].default is None
        assert params["on_row_click"].default is None
        assert params["col_anchor"].default is None
        assert params["row_anchor"].default is None

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

    def test_table_row_is_component(self):
        """DoD (方案 §5.2): TableRow 是独立 @ft.component。"""
        assert "@ft.component" in _raw_source()
        assert "def TableRow(" in _raw_source()

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
        """DoD (方案 D): RERENDER_THRESHOLD / DEFAULT_VIEWPORT_ROWS 常量已删除。"""
        src = _code_source()
        assert "RERENDER_THRESHOLD" not in src
        assert "DEFAULT_VIEWPORT_ROWS" not in src

    def test_no_pagerefmixin_import(self):
        """模块不得依赖 PageRefMixin (CLAUDE.md §3.3 技术债消除)。"""
        import ui.components.virtual_table as mod

        assert not hasattr(mod, "PageRefMixin")
        assert "PageRefMixin" not in dir(mod)

    def test_no_top_level_hovered_state(self):
        """AC-9: 顶层不再声明 hovered_idx / set_hovered_idx (hover 下沉到 TableRow 行内)。"""
        src = _code_source()
        assert "hovered_idx" not in src
        assert "set_hovered_idx" not in src

    def test_no_scroll_mode_always(self):
        """AC-10: 滚动用 AUTO (内容溢出才显示滚动条), 不再用 ALWAYS。"""
        assert "ft.ScrollMode.ALWAYS" not in _code_source()

    def test_use_state_initializer_is_dict_instance(self):
        """col_widths 初始值必须是 dict 实例 (dict[str, int]()), 禁止传类型 dict[str, int]。

        类型 dict[str, int] 是 types.GenericAlias, 虽因 `in` 返回 False 而"碰巧"不崩,
        但语义错误且脆弱 (任何 col_widths[col_id] / .get 直接访问都会炸)。
        """
        assert "use_state(dict[str, int]())" in _code_source()
        assert "use_state(dict[str, int])" not in _code_source()

    def test_hscroll_row_stretch_declared(self):
        """AC-12: HScroll Row 使用 vertical_alignment=CrossAxisAlignment.STRETCH。"""
        assert "vertical_alignment=ft.CrossAxisAlignment.STRETCH" in _code_source()

    def test_inner_column_no_expand(self):
        """AC-12: Inner Column 不设 expand=True (仅 width=total_w)。源码断言保护 v1.2 expand 主轴回归。

        渲染树级断言 (inner.expand is not True) 由 test_virtual_table_body.py 覆盖;
        此处仅做源码级正向契约 (width=total_w 必现)。
        """
        src = _code_source()
        assert "width=total_w" in src


# --- 4. 方案 D-v3 布局契约 (源码级) ---


class TestScrollConfig:
    """验证 PaginatedTable 使用 HScroll Row(scroll=AUTO) + VScroll Column(scroll=AUTO) (方案 D-v3, G1/G7)。"""

    def test_no_listview_used(self):
        """DoD: 不再使用 ft.ListView (避免 ListView 视口高度为 0 时的语义节点丢失)。"""
        assert "ft.ListView" not in _code_source()
        assert "ListView(" not in _code_source()

    def test_hscroll_row_scroll_auto_declared(self):
        """G1/AC-10: 外层 HScroll Row 声明 scroll=ft.ScrollMode.AUTO。"""
        assert "scroll=ft.ScrollMode.AUTO" in _code_source()

    def test_hscroll_row_expand_declared(self):
        """§5.5c 关键点1: HScroll Row expand=True (撑满视口高度)。"""
        assert "expand=True" in _code_source()

    def test_vscroll_key_declared(self):
        """VScroll Column.key 显式设置 (rows 变化时重建以重置滚动位置)。"""
        assert "key=" in _code_source()

    def test_inner_column_has_width(self):
        """§5.1/§5.4: Inner Column 显式 width=total_w。"""
        assert "width=total_w" in _code_source()

    def test_no_stack_canvas_layer(self):
        """DoD (方案 D-v2): 不再使用 ft.Stack 作为虚拟化画布层。"""
        assert "ft.Stack" not in _code_source()
        assert "Stack(" not in _code_source()

    def test_no_on_scroll_handler(self):
        """DoD (方案 D-v2): 不再挂 _on_scroll handler (无自实现虚拟化滚动节流)。"""
        src = _code_source()
        assert "on_scroll=" not in src
        assert "def _on_scroll" not in src


# --- 5. 无 renderer 环境下组件实例化抛 RuntimeError (契约验证) ---


class TestRendererRequirement:
    """有状态 @ft.component 在无 renderer 下抛 RuntimeError (由集成测试覆盖渲染)。"""

    def test_calling_without_renderer_raises(self):
        """无 renderer 环境下调用 PaginatedTable 抛 RuntimeError。

        这是有状态声明式组件的预期行为 (含 use_state/use_ref), 验证组件确实
        依赖 renderer 上下文, 而非静默返回错误结果。集成测试用 flet_test_page 覆盖。
        """
        with pytest.raises(RuntimeError):
            PaginatedTable(
                rows=[{"name": "S0"}],
                columns=[{"id": "name", "label": "Name", "width": 100}],
            )
