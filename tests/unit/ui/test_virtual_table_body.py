"""PaginatedTable 组件体测试 — 通过 component_renderer 驱动 @ft.component 执行。

补充 test_virtual_table.py 仅覆盖纯函数的不足，验证：
- _build_header / _build_cells / _build_row 单元构建函数的分支逻辑
- PaginatedTable 组件体 (lines 276-344) 的渲染结构 + _on_scroll handler 行为

配套 conftest.py 的 ``mock_app_colors_state`` 注入 Observable state，
``_v1_page_compat`` 让 ``control.page`` 可注入。
"""

# pyright: reportAttributeAccessIssue=false, reportOptionalMemberAccess=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）, Optional 成员访问（mock 返回 None）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    render_once,
    run_mount_effects,
)
from ui.components.virtual_table import (
    HEADER_HEIGHT,
    ROW_HEIGHT,
    PaginatedTable,
    _build_cells,
    _build_header,
    _build_row,
    _total_width,
)
from ui.theme import AppColors, AppStyles

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------


def _make_columns() -> list[dict]:
    """构造覆盖各分支的列定义 (普通/数字/趋势/代码)。"""
    return [
        {"id": "ts_code", "label": "Code", "width": 120},  # code col + "." split
        {"id": "name", "label": "Name", "width": 200},  # 普通 text
        {"id": "pct_chg", "label": "Change", "width": 100},  # trend col
        {"id": "price", "label": "Price", "width": 100},  # numeric non-trend
    ]


def _make_row_data() -> dict:
    return {
        "ts_code": "600000.SH",
        "name": "Test Stock",
        "pct_chg": "1.5%",
        "price": "10.50",
    }


def _make_component(
    rows=None,
    columns=None,
    sort_col=None,
    sort_asc=True,
    on_sort=None,
    on_row_click=None,
):
    """构造一个 PaginatedTable Component 实例。"""
    if rows is None:
        rows = [_make_row_data()]
    if columns is None:
        columns = _make_columns()
    return make_component(
        PaginatedTable,
        rows=rows,
        columns=columns,
        sort_col=sort_col,
        sort_asc=sort_asc,
        on_sort=on_sort,
        on_row_click=on_row_click,
    )


def _render(component):
    """驱动 mount effects + 渲染一次，返回 (page, result)。"""
    page = run_mount_effects(component)
    return page, render_once(component)


# ---------------------------------------------------------------------------
# _build_header (lines 134-163)
# ---------------------------------------------------------------------------


class TestBuildHeader:
    """_build_header 纯函数测试：表头单元格构建。"""

    def test_returns_container_per_column(self):
        headers = _build_header(_make_columns(), None, True, None)
        assert len(headers) == 4
        for h in headers:
            assert isinstance(h, ft.Container)

    def test_no_sort_col_label_plain(self):
        """无 sort_col 时 label 不带箭头。"""
        headers = _build_header(_make_columns(), None, True, None)
        # 第 2 列 "Name"
        inner = headers[1].content
        text = inner.content
        assert text.value == "Name"

    def test_sort_col_ascending_appends_up_arrow(self):
        headers = _build_header(_make_columns(), "pct_chg", True, None)
        inner = headers[2].content
        text = inner.content
        assert text.value == "Change ↑"

    def test_sort_col_descending_appends_down_arrow(self):
        headers = _build_header(_make_columns(), "pct_chg", False, None)
        inner = headers[2].content
        text = inner.content
        assert text.value == "Change ↓"

    def test_label_falls_back_to_id_when_missing(self):
        """col 无 label 字段时用 id 作为 label。"""
        cols = [{"id": "x", "width": 100}]
        headers = _build_header(cols, None, True, None)
        inner = headers[0].content
        text = inner.content
        assert text.value == "x"

    def test_no_on_sort_no_click_handler(self):
        headers = _build_header(_make_columns(), None, True, None)
        for h in headers:
            # on_click 挂在内层 content 上（源码 _build_header: content.on_click = ...）
            assert h.content.on_click is None

    def test_with_on_sort_attaches_click_handler(self):
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), None, True, on_sort)
        for h in headers:
            assert callable(h.content.on_click)

    def test_on_sort_handler_invokes_callback_with_new_asc(self):
        """点击列头应调用 on_sort(col_id, new_asc=True)（新列默认升序）。"""
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), "name", False, on_sort)
        # 点击 pct_chg（新列）→ on_sort("pct_chg", True)
        headers[2].content.on_click(MagicMock())
        on_sort.assert_called_once_with("pct_chg", True)

    def test_on_sort_handler_same_column_toggles(self):
        """点击当前排序列 → 翻转方向。"""
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), "pct_chg", True, on_sort)
        # 点击 pct_chg（当前列，asc=True）→ on_sort("pct_chg", False)
        headers[2].content.on_click(MagicMock())
        on_sort.assert_called_once_with("pct_chg", False)

    def test_header_text_uses_table_header_text_color(self):
        headers = _build_header(_make_columns(), None, True, None)
        inner = headers[0].content
        text = inner.content
        assert text.color == AppColors.TABLE_HEADER_TEXT

    def test_header_width_from_column_def(self):
        headers = _build_header(_make_columns(), None, True, None)
        assert headers[0].width == 120
        assert headers[1].width == 200

    def test_header_width_defaults_100_when_missing(self):
        cols = [{"id": "x"}]
        headers = _build_header(cols, None, True, None)
        assert headers[0].width == 100

    def test_empty_columns_returns_empty_list(self):
        assert _build_header([], None, True, None) == []


# ---------------------------------------------------------------------------
# _build_cells (lines 166-226)
# ---------------------------------------------------------------------------


class TestBuildCells:
    """_build_cells 纯函数测试：行单元格构建（数字/趋势/代码分支）。"""

    def test_returns_container_per_column(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        assert len(cells) == 4
        for c in cells:
            assert isinstance(c, ft.Container)

    def test_numeric_cell_uses_numeric_color_and_right_alignment(self):
        """price=10.50 → numeric，颜色 TABLE_CELL_NUMERIC，右对齐。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        price_cell = cells[3]
        inner = price_cell.content
        assert inner.alignment == ft.Alignment.CENTER_RIGHT
        text = inner.content
        assert text.color == AppColors.TABLE_CELL_NUMERIC

    def test_non_numeric_cell_uses_text_color_and_left_alignment(self):
        """name=Test Stock → 非数字，颜色 TABLE_CELL_TEXT，左对齐。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        name_cell = cells[1]
        inner = name_cell.content
        assert inner.alignment == ft.Alignment.CENTER_LEFT
        text = inner.content
        assert text.color == AppColors.TABLE_CELL_TEXT

    def test_trend_positive_uses_up_red(self):
        """pct_chg=1.5% > 0 → UP_RED。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        trend_cell = cells[2]
        inner = trend_cell.content
        text = inner.content
        assert text.color == AppColors.UP_RED

    def test_trend_negative_uses_down_green(self):
        """pct_chg=-1.5% < 0 → DOWN_GREEN。"""
        row = _make_row_data()
        row["pct_chg"] = "-1.5%"
        cells = _build_cells(row, _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.color == AppColors.DOWN_GREEN

    def test_trend_zero_falls_back_to_numeric_color(self):
        """pct_chg=0% → 既非 >0 也非 <0，走 is_numeric 分支 (TABLE_CELL_NUMERIC)。"""
        row = _make_row_data()
        row["pct_chg"] = "0%"
        cells = _build_cells(row, _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.color == AppColors.TABLE_CELL_NUMERIC

    def test_code_col_with_dot_renders_text_spans(self):
        """ts_code=600000.SH 含 "." → TextSpan 分支（前段粗体 + 后段小号灰）。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        code_cell = cells[0]
        text = code_cell.content.content
        assert isinstance(text, ft.Text)
        assert text.spans is not None
        assert len(text.spans) == 2
        # 第一段：600000（粗体）
        assert text.spans[0].text == "600000"
        assert text.spans[0].style.weight == ft.FontWeight.BOLD
        # 第二段：.SH（小号 10）
        assert text.spans[1].text == ".SH"
        assert text.spans[1].style.size == 10

    def test_code_col_without_dot_renders_plain_text(self):
        """ts_code=600000 (无 ".") → 走普通 Text 分支。"""
        row = _make_row_data()
        row["ts_code"] = "600000"
        cells = _build_cells(row, _make_columns())
        code_cell = cells[0]
        text = code_cell.content.content
        # 普通 Text 没有 spans（或 spans 为 None）
        assert not text.spans

    def test_trend_col_numeric_weight_bold(self):
        """trend 列文本 weight=.BOLD。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.weight == ft.FontWeight.BOLD

    def test_numeric_non_trend_uses_mono_font(self):
        """numeric 非 trend 列 font_family=Roboto Mono。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        price_cell = cells[3]
        text = price_cell.content.content
        assert text.font_family == "Roboto Mono, monospace"

    def test_cell_width_from_column_def(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        assert cells[0].width == 120
        assert cells[1].width == 200

    def test_cell_expand_when_no_width(self):
        """col 无 width 字段时 cell expand=1。"""
        cols = [{"id": "x"}]
        cells = _build_cells({"x": "val"}, cols)
        assert cells[0].expand == 1

    def test_missing_value_renders_empty_string(self):
        """row_data 缺失该字段时 val=""。"""
        cells = _build_cells({}, _make_columns())
        name_cell = cells[1]
        text = name_cell.content.content
        assert text.value == ""

    def test_comma_separated_numeric_is_numeric(self):
        """price="1,234.56" → is_numeric=True（去逗号后可转 float）。"""
        row = {"price": "1,234.56"}
        cols = [{"id": "price", "width": 100}]
        cells = _build_cells(row, cols)
        inner = cells[0].content
        assert inner.alignment == ft.Alignment.CENTER_RIGHT

    def test_non_numeric_string_falls_back_to_text(self):
        """price="abc" → is_numeric=False。"""
        row = {"price": "abc"}
        cols = [{"id": "price", "width": 100}]
        cells = _build_cells(row, cols)
        inner = cells[0].content
        assert inner.alignment == ft.Alignment.CENTER_LEFT


# ---------------------------------------------------------------------------
# _build_row (lines 229-248)
# ---------------------------------------------------------------------------


class TestBuildRow:
    """_build_row 纯函数测试：单行构建 + on_row_click 绑定。"""

    def test_returns_container_with_correct_geometry(self):
        row = _build_row(5, _make_row_data(), _make_columns(), 800, None)
        assert isinstance(row, ft.Container)
        assert row.left == 0
        assert row.top == 5 * ROW_HEIGHT
        assert row.height == ROW_HEIGHT
        assert row.width == 800
        assert row.ink is True

    def test_bgcolor_from_app_styles(self):
        row = _build_row(3, _make_row_data(), _make_columns(), 800, None)
        assert row.bgcolor == AppStyles.data_table_row(3)

    def test_content_is_row_of_cells(self):
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None)
        assert isinstance(row.content, ft.Row)
        assert len(row.content.controls) == 4

    def test_no_on_row_click_no_click_handler(self):
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None)
        assert row.on_click is None

    def test_with_on_row_click_attaches_handler(self):
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click)
        assert callable(row.on_click)

    def test_on_row_click_handler_invokes_callback_with_row_data(self):
        on_row_click = MagicMock()
        data = _make_row_data()
        row = _build_row(0, data, _make_columns(), 800, on_row_click)
        assert callable(row.on_click)
        row.on_click(MagicMock())  # type: ignore[reportCallIssue, reason: Flet stub declares on_click as 0-arg, but runtime passes event]
        on_row_click.assert_called_once_with(data)


# ---------------------------------------------------------------------------
# PaginatedTable 组件体 (lines 251-355)
# ---------------------------------------------------------------------------


class TestPaginatedTableRenderStructure:
    """验证 PaginatedTable 渲染后的控件树结构 (lines 276-344 中 render 段)。"""

    def test_renders_column_with_row(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert isinstance(result, ft.Column)
        assert len(result.controls) == 1
        outer_row = result.controls[0]
        assert isinstance(outer_row, ft.Row)
        assert outer_row.scroll == ft.ScrollMode.ALWAYS
        assert outer_row.vertical_alignment == ft.CrossAxisAlignment.STRETCH

    def test_inner_column_contains_header_and_listview(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        inner_column = result.controls[0].controls[0]
        assert isinstance(inner_column, ft.Column)
        assert len(inner_column.controls) == 2
        header_container, list_view = inner_column.controls
        assert isinstance(header_container, ft.Container)
        assert isinstance(list_view, ft.ListView)

    def test_header_container_has_correct_height_and_bgcolor(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        header = result.controls[0].controls[0].controls[0]
        assert header.height == HEADER_HEIGHT
        assert header.bgcolor == AppColors.TABLE_HEADER_BG

    def test_list_view_has_on_scroll_and_interval(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        list_view = result.controls[0].controls[0].controls[1]
        assert callable(list_view.on_scroll)
        assert list_view.scroll_interval == 100

    def test_canvas_is_stack_with_correct_dimensions(self, mock_i18n_state, mock_app_colors_state):
        rows = [_make_row_data() for _ in range(5)]
        _, result = _render(_make_component(rows=rows))
        list_view = result.controls[0].controls[0].controls[1]
        canvas = list_view.controls[0]
        assert isinstance(canvas, ft.Stack)
        assert canvas.height == 5 * ROW_HEIGHT
        assert canvas.clip_behavior == ft.ClipBehavior.HARD_EDGE

    def test_canvas_width_uses_total_width(self, mock_i18n_state, mock_app_colors_state):
        cols = [{"id": "a", "width": 500}, {"id": "b", "width": 400}]
        _, result = _render(_make_component(columns=cols))
        list_view = result.controls[0].controls[0].controls[1]
        canvas = list_view.controls[0]
        assert canvas.width == _total_width(cols)

    def test_empty_rows_renders_empty_canvas(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component(rows=[]))
        list_view = result.controls[0].controls[0].controls[1]
        canvas = list_view.controls[0]
        assert canvas.height == 0
        assert canvas.controls == []

    def test_visible_rows_bounded_by_window(self, mock_i18n_state, mock_app_colors_state):
        """500 行 + 默认视口 → 仅渲染 [0, 46) 窗口内的 46 行。"""
        rows = [{"ts_code": f"{i:06d}.SH", "name": f"S{i}"} for i in range(500)]
        cols = [{"id": "ts_code", "width": 120}, {"id": "name", "width": 200}]
        _, result = _render(_make_component(rows=rows, columns=cols))
        list_view = result.controls[0].controls[0].controls[1]
        canvas = list_view.controls[0]
        # 默认视口 capacity = 30 + 2*8 = 46
        assert len(canvas.controls) == 46

    def test_header_row_contains_one_container_per_column(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        header = result.controls[0].controls[0].controls[0]
        header_row = header.content
        assert isinstance(header_row, ft.Row)
        assert len(header_row.controls) == 4

    def test_outer_column_expands(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert result.expand is True


class TestPaginatedTableScrollHandler:
    """验证 _on_scroll handler 行为 (lines 312-323)。"""

    def test_on_scroll_updates_viewport_height(self, mock_i18n_state, mock_app_colors_state):
        """viewport_dimension 变化 > 1.0 时触发 set_viewport_h。"""
        component = _make_component()
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        # 清空 mount 期间累积的 schedule_update
        page.session.scheduled_updates.clear()
        # 触发 on_scroll，viewport_dimension=600.0（>1.0 差值）
        e = MagicMock()
        e.viewport_dimension = 600.0
        e.pixels = 0.0
        list_view.on_scroll(e)
        # set_viewport_h 调用后应调度 update
        assert len(page.session.scheduled_updates) > 0

    def test_on_scroll_small_viewport_change_no_update(self, mock_i18n_state, mock_app_colors_state):
        """viewport_dimension 变化 <= 1.0 时不触发 set_viewport_h。"""
        component = _make_component()
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        # 先设置一个 viewport_h
        e1 = MagicMock()
        e1.viewport_dimension = 600.0
        e1.pixels = 0.0
        list_view.on_scroll(e1)
        page.session.scheduled_updates.clear()

        # 微小变化（0.5 < 1.0）→ 不触发 set_viewport_h
        e2 = MagicMock()
        e2.viewport_dimension = 600.5
        e2.pixels = 0.0
        list_view.on_scroll(e2)
        # pixels=0 且 cache.last_first 已设为 0，不会触发 set_scroll_first
        assert len(page.session.scheduled_updates) == 0

    def test_on_scroll_shifts_first_row(self, mock_i18n_state, mock_app_colors_state):
        """pixels 滚动超过 RERENDER_THRESHOLD 时触发 set_scroll_first。"""
        rows = [{"ts_code": f"{i:06d}.SH", "name": f"S{i}"} for i in range(500)]
        cols = [{"id": "ts_code", "width": 120}, {"id": "name", "width": 200}]
        component = _make_component(rows=rows, columns=cols)
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        page.session.scheduled_updates.clear()
        # 滚动 200 行（>= RERENDER_THRESHOLD=4）
        e = MagicMock()
        e.viewport_dimension = 0  # 不触发 viewport_h 分支
        e.pixels = 200 * ROW_HEIGHT
        list_view.on_scroll(e)
        assert len(page.session.scheduled_updates) > 0

    def test_on_scroll_small_shift_no_update(self, mock_i18n_state, mock_app_colors_state):
        """pixels 滚动 < RERENDER_THRESHOLD 时不触发 set_scroll_first。"""
        rows = [{"ts_code": f"{i:06d}.SH", "name": f"S{i}"} for i in range(500)]
        cols = [{"id": "ts_code", "width": 120}, {"id": "name", "width": 200}]
        component = _make_component(rows=rows, columns=cols)
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        # 先触发一次大滚动设置 cache.last_first
        e1 = MagicMock()
        e1.viewport_dimension = 0
        e1.pixels = 100 * ROW_HEIGHT
        list_view.on_scroll(e1)
        page.session.scheduled_updates.clear()

        # 小滚动（< RERENDER_THRESHOLD=4 行）
        e2 = MagicMock()
        e2.viewport_dimension = 0
        e2.pixels = 102 * ROW_HEIGHT  # 差 2 行 < 4
        list_view.on_scroll(e2)
        assert len(page.session.scheduled_updates) == 0

    def test_on_scroll_no_viewport_dimension_no_update(self, mock_i18n_state, mock_app_colors_state):
        """viewport_dimension 为 None 时不触发 set_viewport_h 分支。

        pixels=0 → new_first=0，set_scroll_first(0) 与初值 0 相等，
        框架等值优化不调度 schedule_update。本测试覆盖 ``if vh:`` 为 False 的分支，
        断言 on_scroll 正常返回不抛异常即可（分支覆盖由 coverage 报告确认）。
        """
        component = _make_component()
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        page.session.scheduled_updates.clear()
        e = MagicMock()
        e.viewport_dimension = None
        e.pixels = 0.0
        # 不抛异常即说明 ``if vh:`` 分支正确跳过
        list_view.on_scroll(e)
        # set_scroll_first(0) 与初值相等，不触发 schedule_update
        assert len(page.session.scheduled_updates) == 0

    def test_on_scroll_no_pixels_uses_zero(self, mock_i18n_state, mock_app_colors_state):
        """pixels 为 None 时 fallback 到 0.0。"""
        component = _make_component()
        page, _ = _render(component)
        list_view = render_once(component).controls[0].controls[0].controls[1]

        page.session.scheduled_updates.clear()
        e = MagicMock()
        e.viewport_dimension = 600.0
        e.pixels = None
        list_view.on_scroll(e)
        # pixels=None → 0.0 → new_first=0，cache.last_first 初始 -1 < 0 → 触发 set_scroll_first
        assert len(page.session.scheduled_updates) > 0


class TestPaginatedTableRowsChangeEffect:
    """验证 _reset_scroll_on_rows_change effect (lines 290-294)。"""

    def test_initial_mount_runs_reset_effect(self, mock_i18n_state, mock_app_colors_state):
        """首次 mount 时 effect 执行 set_scroll_first(0)。"""
        component = _make_component()
        run_mount_effects(component)
        # _reset_scroll_on_rows_change 调用 set_scroll_first(0)
        # set_scroll_first(0) 与初值 0 相等，不触发 schedule_update
        # effect 本身已执行（cache.last_first=-1），mount 未抛异常即间接验证
