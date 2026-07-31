"""PaginatedTable 组件体测试 — 通过 component_renderer 驱动 @ft.component 执行。

补充 test_virtual_table.py 仅覆盖纯函数的不足，验证：
- _build_header / _build_cells / _build_row 单元构建函数的分支逻辑
- PaginatedTable 组件体 的渲染结构 + Column(scroll=ALWAYS) 配置 (方案 D-v2, E2E 修复)

配套 conftest.py 的 ``mock_app_colors_state`` 注入 Observable state，
``_v1_page_compat`` 让 ``control.page`` 可注入。

变更要点 (方案 D-v2):
- 删除 TestPaginatedTableScrollHandler (不再有 _on_scroll handler)
- 改写 TestPaginatedTableRenderStructure (Column 直接含行, 无 ListView/Stack 中间层)
- 改写 TestPaginatedTableRowsChangeEffect (key 重建替代 use_effect 重置)
- 新增 TestPaginatedTableRowsColumnConfig (验证 Column 配置, 替代 ListView 虚拟化配置)
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
        # 第二段：.SH（小号 caption）
        assert text.spans[1].text == ".SH"
        assert text.spans[1].style.size == AppStyles.FONT_SIZE_CAPTION

    # --- P3-15 色盲友好: 涨跌前置 +/- 符号 ---

    def test_trend_positive_prefixes_plus_sign(self):
        """P3-15: pct_chg=1.5% > 0 → 前置 "+" (不依赖颜色区分涨跌)。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        text = cells[2].content.content
        assert text.value.startswith("+")
        assert text.value == "+1.5%"

    def test_trend_positive_existing_plus_not_duplicated(self):
        """P3-15: 已带 "+" 前缀不重复添加。"""
        row = _make_row_data()
        row["pct_chg"] = "+2.3%"
        cells = _build_cells(row, _make_columns())
        text = cells[2].content.content
        assert text.value == "+2.3%"

    def test_trend_negative_existing_minus_not_duplicated(self):
        """P3-15: 已带 "-" (U+002D) 前缀负值不重复添加。

        注: U+2212 (数学负号) 输入无法被 float() 解析, trend 分支不可达,
        代码中的 ``val.replace("−", "-")`` 为防御性 normalize, 无法经数值路径触达。
        """
        row = _make_row_data()
        row["pct_chg"] = "-1.5%"
        cells = _build_cells(row, _make_columns())
        text = cells[2].content.content
        assert text.value == "-1.5%"
        assert text.color == AppColors.DOWN_GREEN

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
    """_build_row 纯函数测试：单行构建 + on_row_click 绑定 (方案 D: GestureDetector 包裹)."""

    def test_returns_gesture_detector_with_inner_container(self):
        """方案 D: 行返回 GestureDetector, 内部包裹 Container (生成 flt-tappable 语义属性)."""
        row = _build_row(5, _make_row_data(), _make_columns(), 800, None)
        assert isinstance(row, ft.GestureDetector)
        inner = row.content
        assert isinstance(inner, ft.Container)
        # 方案 D: 不再设置 left/top (由 Column 线性布局)
        assert inner.height == ROW_HEIGHT
        assert inner.width == 800
        assert inner.ink is True

    def test_bgcolor_from_app_styles(self):
        row = _build_row(3, _make_row_data(), _make_columns(), 800, None)
        inner = row.content
        assert inner.bgcolor == AppStyles.data_table_row(3)

    def test_content_is_row_of_cells(self):
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None)
        inner = row.content
        assert isinstance(inner.content, ft.Row)
        assert len(inner.content.controls) == 4

    def test_no_on_row_click_no_tap_handler(self):
        """方案 D: on_row_click=None 时 GestureDetector.on_tap=None."""
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None)
        assert row.on_tap is None

    def test_with_on_row_click_attaches_tap_handler(self):
        """方案 D: on_row_click 非空时 GestureDetector.on_tap 为 callable."""
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click)
        assert callable(row.on_tap)

    def test_on_row_click_handler_invokes_callback_with_row_data(self):
        """方案 D: GestureDetector.on_tap 触发时调用 on_row_click(row_data)."""
        on_row_click = MagicMock()
        data = _make_row_data()
        row = _build_row(0, data, _make_columns(), 800, on_row_click)
        assert callable(row.on_tap)
        row.on_tap(MagicMock())  # type: ignore[reportCallIssue, reason: Flet stub declares on_tap as 0-arg, but runtime passes event]
        on_row_click.assert_called_once_with(data)

    def test_is_hovered_false_uses_odd_even_color(self):
        """P2-8 MAJ-2: is_hovered=False (默认) → bgcolor 为 ODD/EVEN 色 (非 TABLE_ROW_HOVER)."""
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None)
        inner = row.content
        assert inner.bgcolor == AppStyles.data_table_row(0, is_hovered=False)
        assert inner.bgcolor != AppColors.TABLE_ROW_HOVER

    def test_is_hovered_true_uses_hover_color(self):
        """P2-8 MAJ-2: is_hovered=True → bgcolor 为 TABLE_ROW_HOVER."""
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None, is_hovered=True)
        inner = row.content
        assert inner.bgcolor == AppColors.TABLE_ROW_HOVER

    def test_on_hover_attached_to_inner_container_when_provided(self):
        """P2-8 MAJ-2: 传入 on_hover 回调时, 内部 Container.on_hover 非空 (GestureDetector 无 on_hover 用于 bgcolor 切换)."""
        on_hover = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, None, on_hover=on_hover)
        inner = row.content
        assert callable(inner.on_hover)


# ---------------------------------------------------------------------------
# PaginatedTable 组件体 (方案 D-v2: Column(scroll=ALWAYS) 承载行, E2E 修复)
# ---------------------------------------------------------------------------


class TestPaginatedTableRenderStructure:
    """验证 PaginatedTable 渲染后的控件树结构 (方案 D-v3: Column 直接含 header + rows, 无 Row 中间层)。"""

    def test_renders_column_directly(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert isinstance(result, ft.Column)
        assert result.expand is True
        # 直接含 header_container + rows_clip_container, 无外层 Row 嵌套
        assert len(result.controls) == 2

    def test_column_contains_header_and_rows_clip_container(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        header_container, rows_clip_container = result.controls
        assert isinstance(header_container, ft.Container)
        # 方案 D-v2: 用 ft.Container(clip_behavior=HARD_EDGE) 包裹 ft.Column(scroll=ALWAYS)
        assert isinstance(rows_clip_container, ft.Container)
        assert rows_clip_container.clip_behavior == ft.ClipBehavior.HARD_EDGE
        rows_column = rows_clip_container.content
        assert isinstance(rows_column, ft.Column)

    def test_header_container_has_correct_height_and_bgcolor(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        header = result.controls[0]
        assert header.height == HEADER_HEIGHT
        assert header.bgcolor == AppColors.TABLE_HEADER_BG

    def test_rows_column_contains_all_rows_directly(self, mock_i18n_state, mock_app_colors_state):
        """方案 D-v2: Column.controls 直接是行 GestureDetector 列表 (无 ListView/Stack 中间层)。

        100 行规模下 Python 端构建全量行控件, Column(scroll=ALWAYS) 直接渲染全部行.
        """
        rows = [_make_row_data() for _ in range(5)]
        _, result = _render(_make_component(rows=rows))
        rows_clip_container = result.controls[1]
        assert isinstance(rows_clip_container, ft.Container)
        rows_column = rows_clip_container.content
        assert isinstance(rows_column, ft.Column)
        # 直接是行 GestureDetector, 不经过 ListView/Stack
        assert len(rows_column.controls) == 5
        for row in rows_column.controls:
            assert isinstance(row, ft.GestureDetector)

    def test_empty_rows_renders_empty_rows_column(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component(rows=[]))
        rows_clip_container = result.controls[1]
        assert isinstance(rows_clip_container, ft.Container)
        rows_column = rows_clip_container.content
        assert isinstance(rows_column, ft.Column)
        assert rows_column.controls == []

    def test_header_row_contains_one_container_per_column(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        header = result.controls[0]
        header_row = header.content
        assert isinstance(header_row, ft.Row)
        assert len(header_row.controls) == 4

    def test_outer_column_expands(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert result.expand is True


class TestPaginatedTableRowsColumnConfig:
    """验证 Column(scroll=ALWAYS) + Container(clip_behavior) 配置 (方案 D-v2 核心契约, E2E 修复)。"""

    def test_rows_column_scroll_always(self, mock_i18n_state, mock_app_colors_state):
        """rows_column.scroll=ALWAYS (保留纵向滚动能力, 与原 ListView 行为一致)。"""
        _, result = _render(_make_component())
        rows_clip_container = result.controls[1]
        rows_column = rows_clip_container.content
        assert rows_column.scroll == ft.ScrollMode.ALWAYS

    def test_rows_column_key_changes_with_rows_token(self, mock_i18n_state, mock_app_colors_state):
        """rows_column.key 随 rows 引用变化 (rows 变化时重建以重置滚动位置)。"""
        rows1 = [_make_row_data()]
        rows2 = [_make_row_data()]
        _, result1 = _render(_make_component(rows=rows1))
        _, result2 = _render(_make_component(rows=rows2))
        rows_col1 = result1.controls[1].content
        rows_col2 = result2.controls[1].content
        # key 非空且随 rows 引用变化
        assert rows_col1.key is not None
        assert rows_col2.key is not None
        assert rows_col1.key != rows_col2.key

    def test_rows_clip_container_clip_behavior_hard_edge(self, mock_i18n_state, mock_app_colors_state):
        """rows_clip_container.clip_behavior=HARD_EDGE (裁剪溢出行内容, 对应原 ListView clip_behavior)。"""
        _, result = _render(_make_component())
        rows_clip_container = result.controls[1]
        assert rows_clip_container.clip_behavior == ft.ClipBehavior.HARD_EDGE

    def test_rows_column_expand_true(self, mock_i18n_state, mock_app_colors_state):
        """rows_column.expand=True (占满剩余可用高度, 与原 ListView 行为一致)。"""
        _, result = _render(_make_component())
        rows_clip_container = result.controls[1]
        rows_column = rows_clip_container.content
        assert rows_column.expand is True

    def test_rows_column_spacing_zero(self, mock_i18n_state, mock_app_colors_state):
        """rows_column.spacing=0 (行间无空隙, 与原 ListView spacing=0 一致)。"""
        _, result = _render(_make_component())
        rows_clip_container = result.controls[1]
        rows_column = rows_clip_container.content
        assert rows_column.spacing == 0

    def test_rows_column_no_on_scroll_handler(self, mock_i18n_state, mock_app_colors_state):
        """方案 D-v2: Column 不挂 on_scroll handler (无自实现虚拟化滚动节流)。"""
        _, result = _render(_make_component())
        rows_clip_container = result.controls[1]
        rows_column = rows_clip_container.content
        assert rows_column.on_scroll is None


class TestPaginatedTableRowsChangeEffect:
    """验证 rows 变化时 Column key 重建行为 (方案 D-v2: 替代 use_effect 重置滚动)。"""

    def test_initial_mount_renders_rows_column(self, mock_i18n_state, mock_app_colors_state):
        """首次 mount 时 rows_column(Column) 正常渲染 (key 机制不阻塞初始渲染)。"""
        component = _make_component()
        run_mount_effects(component)
        result = render_once(component)
        rows_clip_container = result.controls[1]
        rows_column = rows_clip_container.content
        assert isinstance(rows_column, ft.Column)
        assert rows_column.key is not None
