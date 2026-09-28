"""PaginatedTable 组件体测试 — 通过 component_renderer 驱动 @ft.component 执行。

补充 test_virtual_table.py 仅覆盖纯函数的不足，验证：
- _build_header / _build_cells / _build_row 单元构建函数的分支逻辑
- 方案 D-v3 布局树 (HScroll Row / Inner Column / Header sticky / BodyClip / VScroll)
- TableRow 独立组件行内 hover state (G5)
- 列宽拖拽 handle 契约与拖拽行为 (G4, R13 契约)

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
from tests.unit.ui.mock_flet import MockHoverEvent
from ui.components.virtual_table import (
    DETAIL_COL_ID,
    DETAIL_COL_WIDTH,
    HEADER_HEIGHT,
    ROW_HEIGHT,
    PaginatedTable,
    TableRow,
    _build_cells,
    _build_detail_cell,
    _build_header,
    _build_row,
    _column_alignments,
)
from ui.theme import AppColors, AppStyles
from ui.testing.e2e_ids import AnchorKind

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


def _total_w() -> int:
    """_make_columns 列宽总和 clamp 到 MIN_TABLE_WIDTH=800。"""
    return 800


def _make_component(
    rows=None,
    columns=None,
    sort_col=None,
    sort_asc=True,
    on_sort=None,
    on_row_click=None,
    **extra,
):
    """构造一个 PaginatedTable Component 实例 (extra 透传新增可选参数)。"""
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
        **extra,
    )


def _make_detail_columns() -> list[dict]:
    """含列尾详情动作列的列定义 (MINOR-09 item 4)。"""
    return [
        *_make_columns(),
        {"id": DETAIL_COL_ID, "label": "Details", "width": DETAIL_COL_WIDTH, "action": "detail"},
    ]


def _render(component):
    """驱动 mount effects + 渲染一次，返回 (page, result)。"""
    page = run_mount_effects(component)
    return page, render_once(component)


def _noop(e):
    return None


def _sort_header_text(headers, col_index):
    """可排序表头读屏语义挂 Text.semantics_label (不嵌套 Semantics).

    结构 (col_anchor=None): headers[i].content (GestureDetector) →
    .content (Container) → .content (Text, 承载 semantics_label)。
    返回该 Text，调用方断言 semantics_label。
    """
    gesture = headers[col_index].content
    assert isinstance(gesture, ft.GestureDetector)
    text = gesture.content.content
    assert isinstance(text, ft.Text)
    return text


def _fake_drag_handlers():
    """构造每列 a no-op (start, update, end) handler 的 dict。"""
    return {str(col["id"]): (_noop, _noop, _noop) for col in _make_columns()}


# ---------------------------------------------------------------------------
# 渲染树导航 helper
# ---------------------------------------------------------------------------


def _structure(result):
    """导航 PaginatedTable 渲染树 → (outer, h_scroll, inner, header, body_clip, vscroll)。"""
    outer = result
    h_scroll = outer.controls[0]
    inner = h_scroll.controls[0]
    header = inner.controls[0]
    body_clip = inner.controls[1]
    vscroll = body_clip.content
    return outer, h_scroll, inner, header, body_clip, vscroll


def _header_cell_of(result, col_index):
    """从渲染结果取第 col_index 个 header 单元格 (Cell Container)。"""
    _, _, _, header, _, _ = _structure(result)
    header_row = header.content
    assert isinstance(header_row, ft.Row)
    return header_row.controls[col_index]


def _header_handle_of(result, col_index):
    """从渲染结果取第 col_index 列 header 的拖拽把手 (GestureDetector)。"""
    cell = _header_cell_of(result, col_index)
    row = cell.content
    assert isinstance(row, ft.Row)
    handle = row.controls[1]
    assert isinstance(handle, ft.GestureDetector)
    return handle


def _trigger_callback(cb, event):
    """Safely trigger Flet optional callback in tests.

    Flet stubs declare callbacks as Optional[Callable[[], None]], but runtime passes
    a ControlEvent. Centralize type narrowing + type: ignore here.
    """
    assert cb is not None
    cb(event)  # type: ignore[reportCallIssue, reason: Flet stub declares callbacks as 0-arg, but runtime passes event]


# ---------------------------------------------------------------------------
# _build_header
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
            # on_sort=None 时 content 是 Container（无 GestureDetector 包裹）
            assert h.content.on_click is None

    def test_with_on_sort_attaches_click_handler(self):
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), None, True, on_sort)
        for h in headers:
            # on_sort 非空时 content 是 GestureDetector(on_tap=handler),
            # 其 content 内层是 Container(Text), 读屏语义挂 Text.semantics_label。
            assert isinstance(h.content, ft.GestureDetector)
            assert callable(h.content.on_tap)

    def test_on_sort_handler_invokes_callback_with_new_asc(self):
        """点击列头应调用 on_sort(col_id, new_asc=True)（新列默认升序）。"""
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), "name", False, on_sort)
        # 点击 pct_chg（新列）→ on_sort("pct_chg", True)
        headers[2].content.on_tap(MagicMock())
        on_sort.assert_called_once_with("pct_chg", True)

    def test_on_sort_handler_same_column_toggles(self):
        """点击当前排序列 → 翻转方向。"""
        on_sort = MagicMock()
        headers = _build_header(_make_columns(), "pct_chg", True, on_sort)
        headers[2].content.on_tap(MagicMock())
        on_sort.assert_called_once_with("pct_chg", False)

    def test_header_text_uses_table_header_text_color(self):
        headers = _build_header(_make_columns(), None, True, None)
        inner = headers[0].content
        text = inner.content
        assert text.color == AppColors.TABLE_HEADER_TEXT


class TestBuildHeaderSemantics:
    """排序表头读屏语义三态标注 — 排序方向可被辅助技术感知。

    读屏语义挂 `Text.semantics_label`（而非嵌套 ft.Semantics）：E2E CI 实证嵌套
    Semantics 阻断 anchored COMPLEX 锚点 fold（PR #655），故表头结构为
    GestureDetector→Container→Text，状态文案在 Text.semantics_label。
    断言不依赖中文字面量: 期望值用 I18n.get(key) 同源构造 (mock_i18n_state 不 patch
    I18n.get, 解析真实 strings.json 默认 zh_CN), 翻译变化自动跟随。
    """

    def test_ascending_column_semantics_label(self, mock_i18n_state) -> None:
        """当前排序列升序 → Text.semantics_label 含 table_sort_asc 文案。"""
        from ui.i18n import I18n

        headers = _build_header(_make_columns(), "pct_chg", True, MagicMock())
        text = _sort_header_text(headers, 2)
        assert text.semantics_label is not None  # noqa: weak-assertion pyright 收窄 Optional[str], 下方 in 依赖非空
        assert I18n.get("table_sort_asc") in text.semantics_label

    def test_descending_column_semantics_label(self, mock_i18n_state) -> None:
        """当前排序列降序 → Text.semantics_label 含 table_sort_desc 文案。"""
        from ui.i18n import I18n

        headers = _build_header(_make_columns(), "pct_chg", False, MagicMock())
        text = _sort_header_text(headers, 2)
        assert text.semantics_label is not None
        assert I18n.get("table_sort_desc") in text.semantics_label
        assert I18n.get("table_sort_asc") not in text.semantics_label

    def test_non_sorted_sortable_column_semantics_label(self, mock_i18n_state) -> None:
        """非当前可排序列 → Text.semantics_label 含 table_sort_action, 不含方向词。"""
        from ui.i18n import I18n

        headers = _build_header(_make_columns(), "pct_chg", True, MagicMock())
        text = _sort_header_text(headers, 0)  # ts_code 非当前列
        assert text.semantics_label is not None
        assert I18n.get("table_sort_action") in text.semantics_label
        assert I18n.get("table_sort_asc") not in text.semantics_label
        assert I18n.get("table_sort_desc") not in text.semantics_label

    def test_no_sort_table_header_plain_container(self, mock_i18n_state) -> None:
        """on_sort=None (只读表格) → 表头保持 Container, 不包排序语义。"""
        headers = _build_header(_make_columns(), None, True, None)
        assert all(not isinstance(h.content, ft.Semantics) for h in headers)

    def test_header_width_from_column_def(self):
        headers = _build_header(_make_columns(), None, True, None)
        assert headers[0].width == 120
        assert headers[1].width == 200

    def test_header_width_from_col_widths_override(self):
        """col_widths 覆盖列定义宽度 (拖拽后 header cell 宽度变化)。"""
        headers = _build_header(_make_columns(), None, True, None, col_widths={"ts_code": 250})
        assert headers[0].width == 250

    def test_header_width_defaults_100_when_missing(self):
        cols = [{"id": "x"}]
        headers = _build_header(cols, None, True, None)
        assert headers[0].width == 100

    def test_empty_columns_returns_empty_list(self):
        assert _build_header([], None, True, None) == []

    # --- G4: 列宽拖拽把手契约 (§6.1/§6.3/§11a C5) ---

    def test_header_cell_wraps_row_with_handle_when_drag_handlers(self):
        """drag_handlers 非空时每个 header cell 是 Row[sort_area, handle]。"""
        headers = _build_header(_make_columns(), None, True, None, drag_handlers=_fake_drag_handlers())
        for h in headers:
            row = h.content
            assert isinstance(row, ft.Row)
            assert len(row.controls) == 2
            handle = row.controls[1]
            assert isinstance(handle, ft.GestureDetector)

    def test_drag_handle_contract(self):
        """拖拽把手 GestureDetector 含 exclude_from_semantics + RESIZE_LEFT_RIGHT +
        on_horizontal_drag_start/update/end + drag_interval=16。"""
        headers = _build_header(_make_columns(), None, True, None, drag_handlers=_fake_drag_handlers())
        for h in headers:
            row = h.content
            handle = row.controls[1]
            assert isinstance(handle, ft.GestureDetector)
            assert handle.exclude_from_semantics is True
            assert handle.mouse_cursor == ft.MouseCursor.RESIZE_LEFT_RIGHT
            assert handle.drag_interval == 16
            assert callable(handle.on_horizontal_drag_start)
            assert callable(handle.on_horizontal_drag_update)
            assert callable(handle.on_horizontal_drag_end)

    def test_drag_handle_width_independent_of_column(self):
        """把手宽度独立于列宽: sort_area expand, handle 固定 6px (§6.4)。"""
        headers = _build_header(_make_columns(), None, True, None, drag_handlers=_fake_drag_handlers())
        h = headers[0]
        row = h.content
        sort_area = row.controls[0]
        handle = row.controls[1]
        assert sort_area.expand is True
        handle_content = handle.content
        assert handle_content.width == 6


# ---------------------------------------------------------------------------
# _build_cells
# ---------------------------------------------------------------------------


class TestBuildCells:
    """_build_cells 纯函数测试：行单元格构建（数字/趋势/代码分支）。"""

    def test_returns_container_per_column(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        assert len(cells) == 4
        for c in cells:
            assert isinstance(c, ft.Container)

    def test_numeric_cell_uses_numeric_color_and_right_alignment(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        price_cell = cells[3]
        inner = price_cell.content
        assert inner.alignment == ft.Alignment.CENTER_RIGHT
        text = inner.content
        assert text.color == AppColors.TABLE_CELL_NUMERIC

    def test_non_numeric_cell_uses_text_color_and_left_alignment(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        name_cell = cells[1]
        inner = name_cell.content
        assert inner.alignment == ft.Alignment.CENTER_LEFT
        text = inner.content
        assert text.color == AppColors.TABLE_CELL_TEXT

    def test_trend_positive_uses_up_red(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        trend_cell = cells[2]
        inner = trend_cell.content
        text = inner.content
        assert text.color == AppColors.UP_RED

    def test_trend_negative_uses_down_green(self):
        row = _make_row_data()
        row["pct_chg"] = "-1.5%"
        cells = _build_cells(row, _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.color == AppColors.DOWN_GREEN

    def test_trend_zero_falls_back_to_numeric_color(self):
        row = _make_row_data()
        row["pct_chg"] = "0%"
        cells = _build_cells(row, _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.color == AppColors.TABLE_CELL_NUMERIC

    def test_code_col_with_dot_renders_text_spans(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        code_cell = cells[0]
        text = code_cell.content.content
        assert isinstance(text, ft.Text)
        assert text.spans is not None
        assert len(text.spans) == 2
        assert text.spans[0].text == "600000"
        assert text.spans[0].style.weight == ft.FontWeight.BOLD
        assert text.spans[1].text == ".SH"
        assert text.spans[1].style.size == AppStyles.FONT_SIZE_CAPTION

    # --- P3-15 色盲友好: 涨跌前置 +/- 符号 ---

    def test_trend_positive_prefixes_plus_sign(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        text = cells[2].content.content
        assert text.value.startswith("+")
        assert text.value == "+1.5%"

    def test_trend_positive_existing_plus_not_duplicated(self):
        row = _make_row_data()
        row["pct_chg"] = "+2.3%"
        cells = _build_cells(row, _make_columns())
        text = cells[2].content.content
        assert text.value == "+2.3%"

    def test_trend_negative_existing_minus_not_duplicated(self):
        row = _make_row_data()
        row["pct_chg"] = "-1.5%"
        cells = _build_cells(row, _make_columns())
        text = cells[2].content.content
        assert text.value == "-1.5%"
        assert text.color == AppColors.DOWN_GREEN

    def test_code_col_without_dot_renders_plain_text(self):
        row = _make_row_data()
        row["ts_code"] = "600000"
        cells = _build_cells(row, _make_columns())
        code_cell = cells[0]
        text = code_cell.content.content
        assert not text.spans

    def test_trend_col_numeric_weight_bold(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        trend_cell = cells[2]
        text = trend_cell.content.content
        assert text.weight == ft.FontWeight.BOLD

    def test_numeric_non_trend_uses_mono_font(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        price_cell = cells[3]
        text = price_cell.content.content
        assert text.font_family == "Roboto Mono, monospace"

    def test_cell_width_from_column_def(self):
        cells = _build_cells(_make_row_data(), _make_columns())
        assert cells[0].width == 120
        assert cells[1].width == 200

    def test_cell_width_from_col_widths_override(self):
        """col_widths 覆盖列定义宽度 (拖拽后 body cell 宽度变化, 与 header 对齐)。"""
        cells = _build_cells(_make_row_data(), _make_columns(), {"ts_code": 250})
        assert cells[0].width == 250

    def test_cell_expand_when_no_width(self):
        """col 无 width 字段且无 col_widths 时 cell expand=1。"""
        cols = [{"id": "x"}]
        cells = _build_cells({"x": "val"}, cols)
        assert cells[0].expand == 1

    def test_missing_value_renders_empty_string(self):
        cells = _build_cells({}, _make_columns())
        name_cell = cells[1]
        text = name_cell.content.content
        assert text.value == ""

    def test_comma_separated_numeric_is_numeric(self):
        row = {"price": "1,234.56"}
        cols = [{"id": "price", "width": 100}]
        cells = _build_cells(row, cols)
        inner = cells[0].content
        assert inner.alignment == ft.Alignment.CENTER_RIGHT

    def test_non_numeric_string_falls_back_to_text(self):
        row = {"price": "abc"}
        cols = [{"id": "price", "width": 100}]
        cells = _build_cells(row, cols)
        inner = cells[0].content
        assert inner.alignment == ft.Alignment.CENTER_LEFT

    # --- MINOR-09 item 1: 超宽省略号 + tooltip ---

    def test_text_cell_has_ellipsis_overflow(self):
        """item 1: 单元格 no_wrap + overflow=ELLIPSIS (超宽显示省略号)。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        assert cells[1].content.content.overflow == ft.TextOverflow.ELLIPSIS

    def test_code_span_text_has_ellipsis_overflow(self):
        """item 1: code 列 (TextSpan 分支) 同样加 ELLIPSIS。"""
        cells = _build_cells(_make_row_data(), _make_columns())
        assert cells[0].content.content.overflow == ft.TextOverflow.ELLIPSIS

    def test_cell_container_has_tooltip_with_full_value(self):
        """item 1: tooltip 展示完整值 + exclude_from_semantics=True (不污染行 anchor 语义)。

        tooltip 文案若进入语义树, 会被 Flutter 合并进行 anchor 节点的无障碍名并排在
        EID 之前, 破坏 COMPLEX 行 anchor 的 textContent 前缀契约 (CI E2E 实证)。
        """
        cells = _build_cells(_make_row_data(), _make_columns())
        tooltip = cells[1].content.tooltip
        assert isinstance(tooltip, ft.Tooltip)
        assert tooltip.message == "Test Stock"
        assert tooltip.exclude_from_semantics is True

    def test_empty_cell_container_tooltip_is_none(self):
        """item 1: 空值不挂 tooltip (避免空白气泡)。"""
        cells = _build_cells({}, _make_columns())
        assert cells[1].content.tooltip is None

    # --- MINOR-09 item 2: 按列对齐覆盖逐格推断 ---

    def test_alignments_override_per_cell_inference(self):
        """item 2: 传入 alignments 时按列对齐 (数值列被强制左对齐)。"""
        cols = [{"id": "price", "width": 100}]
        row = {"price": "10.5"}
        cells = _build_cells(row, cols, alignments={"price": ft.Alignment.CENTER_LEFT})
        assert cells[0].content.alignment == ft.Alignment.CENTER_LEFT

    def test_missing_dash_aligns_like_numeric_column(self):
        """item 2 DoD: 缺失值 "-" 与同列数字对齐一致 (同用按列推断结果)。"""
        cols = [{"id": "price", "width": 100}]
        rows = [{"price": "10.5"}, {"price": "-"}]
        alignments = _column_alignments(cols, rows)
        numeric_cell = _build_cells(rows[0], cols, alignments=alignments)[0]
        dash_cell = _build_cells(rows[1], cols, alignments=alignments)[0]
        assert numeric_cell.content.alignment == ft.Alignment.CENTER_RIGHT
        assert dash_cell.content.alignment == numeric_cell.content.alignment

    def test_no_alignments_falls_back_to_per_cell_inference(self):
        """向后兼容: alignments=None 时保留逐格数值性推断。"""
        cols = [{"id": "price", "width": 100}]
        cells = _build_cells({"price": "10.5"}, cols)
        assert cells[0].content.alignment == ft.Alignment.CENTER_RIGHT


# ---------------------------------------------------------------------------
# _build_detail_cell / 详情动作列 (MINOR-09 item 4)
# ---------------------------------------------------------------------------


class TestDetailCell:
    """详情动作列: 可聚焦 TextButton 承载键盘可达入口 (item 4)。"""

    def _detail_col(self) -> dict:
        return {"id": DETAIL_COL_ID, "label": "Details", "width": DETAIL_COL_WIDTH, "action": "detail"}

    def test_none_callback_renders_empty_container(self):
        cell = _build_detail_cell({}, self._detail_col(), None, None)
        assert isinstance(cell, ft.Container)
        assert cell.content is None
        assert cell.width == DETAIL_COL_WIDTH

    def test_renders_focusable_text_button(self):
        """on_row_detail 非空时渲染 TextButton (ActionControl, 可 Tab 聚焦)。"""
        cell = _build_detail_cell(_make_row_data(), self._detail_col(), None, MagicMock())
        assert isinstance(cell.content, ft.TextButton)
        assert callable(cell.content.on_click)
        assert cell.alignment == ft.Alignment.CENTER

    def test_button_click_invokes_callback_with_row_data(self):
        on_detail = MagicMock()
        data = _make_row_data()
        cell = _build_detail_cell(data, self._detail_col(), None, on_detail)
        cell.content.on_click(MagicMock())  # type: ignore[reportCallIssue, reason: Flet stub declares on_click as 0-arg, but runtime passes event]
        on_detail.assert_called_once_with(data)

    def test_width_follows_col_widths_override(self):
        cell = _build_detail_cell({}, self._detail_col(), {DETAIL_COL_ID: 120}, MagicMock())
        assert cell.width == 120

    def test_cells_ignore_action_column(self):
        """_build_cells 只构建数据列; 动作列由 _build_row 单独渲染为兄弟节点 (item 4)。"""
        cells = _build_cells(_make_row_data(), _make_columns(), None, None)
        assert len(cells) == 4  # 4 数据列, 不含动作列

    def test_detail_anchor_wraps_button(self, monkeypatch):
        """detail_anchor 非空时经 anchored() 包裹 TextButton (与行 anchor 语义分离)。"""
        eid = ("e2e.screener.detail_button.600000.SH", AnchorKind.INTERACTIVE)
        captured: list = []

        def _fake_anchored(_eid, control):
            captured.append((_eid, control))
            return ft.Semantics(container=True, label=_eid[0], content=control, button=True)

        monkeypatch.setattr("ui.components.virtual_table.anchored", _fake_anchored)
        cell = _build_detail_cell(_make_row_data(), self._detail_col(), None, MagicMock(), lambda row: eid)
        assert len(captured) == 1
        assert captured[0][0] == eid
        assert isinstance(cell.content, ft.Semantics)
        assert cell.content.label == eid[0]
        assert cell.content.content is captured[0][1]

    def test_detail_anchor_none_keeps_plain_button(self):
        """detail_anchor 未传/返回 None 时不包裹 (向后兼容)。"""
        assert isinstance(
            _build_detail_cell(_make_row_data(), self._detail_col(), None, MagicMock(), None).content, ft.TextButton
        )
        assert isinstance(
            _build_detail_cell(_make_row_data(), self._detail_col(), None, MagicMock(), lambda row: None).content,
            ft.TextButton,
        )


# ---------------------------------------------------------------------------
# _build_row
# ---------------------------------------------------------------------------


class TestBuildRow:
    """_build_row 纯函数测试：单行构建 + on_row_click 绑定 (方案 D: on_row_click 非空时 GestureDetector 包裹).

    返回类型分支 (PR #392 回归修复):
    - on_row_click=None → 直接返回 Container (避免 GestureDetector 无事件处理器警告覆盖语义节点)
    - on_row_click 非 None → 返回 GestureDetector 包裹 Container (生成 flt-tappable 语义属性)
    """

    def test_returns_container_when_no_click_handler(self):
        row = _build_row(5, _make_row_data(), _make_columns(), 800, None)
        assert isinstance(row, ft.Container)
        assert row.height == ROW_HEIGHT
        assert row.width == 800
        assert row.ink is True

    def test_returns_gesture_detector_with_inner_container(self):
        on_row_click = MagicMock()
        row = _build_row(5, _make_row_data(), _make_columns(), 800, on_row_click)
        assert isinstance(row, ft.GestureDetector)
        inner = row.content
        assert isinstance(inner, ft.Container)
        assert inner.height == ROW_HEIGHT
        assert inner.width == 800
        assert inner.ink is True

    def test_bgcolor_from_app_styles(self):
        on_row_click = MagicMock()
        row = _build_row(3, _make_row_data(), _make_columns(), 800, on_row_click)
        inner = row.content
        assert inner.bgcolor == AppStyles.data_table_row(3)

    def test_content_is_row_of_cells(self):
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click)
        inner = row.content
        assert isinstance(inner.content, ft.Row)
        assert len(inner.content.controls) == 4

    def test_with_on_row_click_attaches_tap_handler(self):
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click)
        assert callable(row.on_tap)

    def test_on_row_click_handler_invokes_callback_with_row_data(self):
        on_row_click = MagicMock()
        data = _make_row_data()
        row = _build_row(0, data, _make_columns(), 800, on_row_click)
        assert callable(row.on_tap)
        row.on_tap(MagicMock())  # type: ignore[reportCallIssue, reason: Flet stub declares on_tap as 0-arg, but runtime passes event]
        on_row_click.assert_called_once_with(data)

    def test_is_hovered_false_uses_odd_even_color(self):
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click)
        inner = row.content
        assert inner.bgcolor == AppStyles.data_table_row(0, is_hovered=False)
        assert inner.bgcolor != AppColors.TABLE_ROW_HOVER

    def test_is_hovered_true_uses_hover_color(self):
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click, is_hovered=True)
        inner = row.content
        assert inner.bgcolor == AppColors.TABLE_ROW_HOVER

    def test_on_hover_attached_to_inner_container_when_provided(self):
        on_hover = MagicMock()
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click, on_hover=on_hover)
        inner = row.content
        assert callable(inner.on_hover)

    def test_cells_use_col_widths_when_provided(self):
        """col_widths 传入时 body cell 宽度跟随拖拽值 (与 header 对齐)。"""
        on_row_click = MagicMock()
        row = _build_row(0, _make_row_data(), _make_columns(), 800, on_row_click, col_widths={"ts_code": 250})
        inner = row.content
        cells = inner.content.controls
        assert cells[0].width == 250

    def test_action_column_is_sibling_of_anchor_subtree(self):
        """item 4 契约: 动作列与行 anchor 子树互为兄弟 (不嵌套), 行 anchor 子树内只含数据列。

        嵌套交互控件会让 Flutter 语义合并节点由 role=button 退化为 group + aria-label,
        破坏 COMPLEX 行 anchor 的 textContent 前缀契约 (CI E2E 实证)。
        """
        row = _build_row(
            0,
            _make_row_data(),
            _make_detail_columns(),
            800,
            MagicMock(),
            on_row_detail=MagicMock(),
        )
        assert isinstance(row, ft.Container)
        assert isinstance(row.content, ft.Row)
        anchor_subtree, detail_cell = row.content.controls
        # 行 anchor 子树 = GestureDetector(row_anchor=None), 内部只含 4 个数据单元格
        assert isinstance(anchor_subtree, ft.GestureDetector)
        assert len(anchor_subtree.content.content.controls) == 4
        # 动作列是独立兄弟节点, 内含 TextButton
        assert isinstance(detail_cell.content, ft.TextButton)

    def test_data_area_width_excludes_action_column(self):
        """有动作列时数据区宽度 = 数据列宽度和 (不含动作列), 外层行容器为 total_w。"""
        row = _build_row(0, _make_row_data(), _make_detail_columns(), 800, MagicMock(), on_row_detail=MagicMock())
        anchor_subtree = row.content.controls[0]
        assert anchor_subtree.content.width == 520  # 120 + 200 + 100 + 100
        assert row.width == 800

    def test_action_column_row_bgcolor_and_hover_on_outer_container(self):
        """有动作列时底色/hover 上提到外层行容器, 数据区不再着色/挂 hover (防跨列误触发)。"""
        on_hover = MagicMock()
        row = _build_row(
            0,
            _make_row_data(),
            _make_detail_columns(),
            800,
            MagicMock(),
            on_hover=on_hover,
            on_row_detail=MagicMock(),
        )
        assert row.bgcolor == AppStyles.data_table_row(0, is_hovered=False)
        assert callable(row.on_hover)
        inner = row.content.controls[0].content
        assert inner.bgcolor is None
        assert inner.on_hover is None

    def test_row_anchor_semantics_wraps_only_data_subtree_in_e2e(self, monkeypatch):
        """E2E 下 row_anchor 的 Semantics 仅包裹数据区 GestureDetector, 动作列在其外 (兄弟)。"""
        from ui.testing import anchor as anchor_mod

        monkeypatch.setenv("E2E_TESTING", "true")
        anchor_mod._e2e_enabled.cache_clear()
        try:
            eid = ("e2e.screener.result_row.600000.SH", AnchorKind.COMPLEX)
            row = _build_row(
                0,
                _make_row_data(),
                _make_detail_columns(),
                800,
                MagicMock(),
                row_anchor=lambda r: eid,
                on_row_detail=MagicMock(),
            )
            anchor_subtree, detail_cell = row.content.controls
            assert isinstance(anchor_subtree, ft.Semantics)
            assert anchor_subtree.label == eid[0]
            assert anchor_subtree.button is None or anchor_subtree.button is False  # COMPLEX kind
            assert isinstance(anchor_subtree.content, ft.GestureDetector)
            assert isinstance(detail_cell.content, ft.TextButton)
        finally:
            anchor_mod._e2e_enabled.cache_clear()


# ---------------------------------------------------------------------------
# TableRow (独立组件, 行内 hover state)
# ---------------------------------------------------------------------------


class TestTableRow:
    """验证 TableRow 独立组件: GestureDetector(on_tap) 包裹 + 行内 hover state (G5)。"""

    def test_returns_gesture_detector_when_click_handler(self, mock_i18n_state, mock_app_colors_state):
        component = make_component(
            TableRow,
            abs_idx=0,
            row_data=_make_row_data(),
            columns=_make_columns(),
            col_widths={},
            on_row_click=MagicMock(),
        )
        _, result = _render(component)
        assert isinstance(result, ft.GestureDetector)
        inner = result.content
        assert isinstance(inner, ft.Container)
        assert inner.width == _total_w()
        assert callable(inner.on_hover)

    def test_returns_container_when_no_click_handler(self, mock_i18n_state, mock_app_colors_state):
        component = make_component(
            TableRow,
            abs_idx=0,
            row_data=_make_row_data(),
            columns=_make_columns(),
            col_widths={},
            on_row_click=None,
        )
        _, result = _render(component)
        assert isinstance(result, ft.Container)

    def test_row_hover_toggles_bgcolor(self, mock_i18n_state, mock_app_colors_state):
        """AC-11: hover state 行内切换, 触发当前行重渲染 (bgcolor 变 TABLE_ROW_HOVER)。"""
        component = make_component(
            TableRow,
            abs_idx=0,
            row_data=_make_row_data(),
            columns=_make_columns(),
            col_widths={},
            on_row_click=MagicMock(),
        )
        _, result = _render(component)
        inner = result.content
        assert inner.bgcolor == AppStyles.data_table_row(0, is_hovered=False)

        # 触发 hover 进入
        inner.on_hover(MockHoverEvent(data="true"))
        result2 = render_once(component)
        inner2 = result2.content
        assert inner2.bgcolor == AppColors.TABLE_ROW_HOVER

        # 触发 hover 离开 → 恢复
        inner2.on_hover(MockHoverEvent(data="false"))
        result3 = render_once(component)
        inner3 = result3.content
        assert inner3.bgcolor == AppStyles.data_table_row(0, is_hovered=False)

    def test_row_width_follows_col_widths(self, mock_i18n_state, mock_app_colors_state):
        """列宽变化传给 TableRow 时行宽与单元格宽同步 (R3 目标: hover 不丢失 + 宽度 prop diff)。"""
        component = make_component(
            TableRow,
            abs_idx=0,
            row_data=_make_row_data(),
            columns=_make_columns(),
            col_widths={"ts_code": 250},
            on_row_click=MagicMock(),
        )
        _, result = _render(component)
        inner = result.content
        # ts_code 250 + name 200 + pct_chg 100 + price 100 = 650 → clamp MIN 800
        assert inner.width == 800
        cells = inner.content.controls
        assert cells[0].width == 250

    def test_detail_anchor_wraps_detail_button(self, monkeypatch, mock_i18n_state, mock_app_colors_state):
        """detail_anchor 透传到行内「详情」按钮 (经 anchored() 包裹, 独立 INTERACTIVE EID)。

        未传 row_anchor → 仅动作列触发 anchored, captured 仅含 detail_button EID。
        """
        captured: list = []

        def _fake_anchored(_eid, control):
            captured.append(_eid)
            return control

        monkeypatch.setattr("ui.components.virtual_table.anchored", _fake_anchored)
        eid = ("e2e.screener.detail_button.600000.SH", AnchorKind.INTERACTIVE)
        component = make_component(
            TableRow,
            abs_idx=0,
            row_data=_make_row_data(),
            columns=_make_detail_columns(),
            col_widths={},
            on_row_click=MagicMock(),
            on_row_detail=MagicMock(),
            detail_anchor=lambda row: eid,
        )
        _, result = _render(component)
        assert captured, "detail_anchor 未被调用"
        assert all(c == eid for c in captured)


# ---------------------------------------------------------------------------
# PaginatedTable 组件体 (方案 D-v3 布局树)
# ---------------------------------------------------------------------------


class TestPaginatedTableRenderStructure:
    """验证 PaginatedTable 渲染后的控件树结构 (方案 D-v3: Outer > HScroll > Inner > Header+BodyClip)。"""

    def test_renders_column_with_single_hscroll(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert isinstance(result, ft.Column)
        assert result.expand is True
        assert len(result.controls) == 1
        assert isinstance(result.controls[0], ft.Row)

    def test_hscroll_row_config(self, mock_i18n_state, mock_app_colors_state):
        """G1/AC-10/AC-11: HScroll Row scroll=AUTO + expand=True + vertical_alignment=STRETCH。"""
        _, h_scroll, _, _, _, _ = _structure(_render(_make_component())[1])
        assert h_scroll.scroll == ft.ScrollMode.AUTO
        assert h_scroll.expand is True
        assert h_scroll.vertical_alignment == ft.CrossAxisAlignment.STRETCH

    def test_inner_column_config(self, mock_i18n_state, mock_app_colors_state):
        """AC-12: Inner Column width=total_w, 不设 expand=True, spacing=0。"""
        h_scroll = _structure(_render(_make_component())[1])[1]
        inner = h_scroll.controls[0]
        assert isinstance(inner, ft.Column)
        assert inner.width == _total_w()
        assert inner.expand is not True
        assert inner.spacing == 0

    def test_inner_contains_header_and_body_clip(self, mock_i18n_state, mock_app_colors_state):
        _, _, inner, header, body_clip, _ = _structure(_render(_make_component())[1])
        assert len(inner.controls) == 2
        assert isinstance(header, ft.Container)
        assert isinstance(body_clip, ft.Container)
        assert body_clip.expand is True
        assert body_clip.clip_behavior == ft.ClipBehavior.HARD_EDGE

    def test_header_container_height_bgcolor_width(self, mock_i18n_state, mock_app_colors_state):
        header = _structure(_render(_make_component())[1])[3]
        assert header.height == HEADER_HEIGHT
        assert header.bgcolor == AppColors.TABLE_HEADER_BG
        assert header.width == _total_w()

    def test_header_sticky_not_in_vscroll(self, mock_i18n_state, mock_app_colors_state):
        """G2: Header 在 Inner 第一行, 不在 VScroll controls 内 (垂直滚动时 Header 位置不变)。"""
        # 空行场景: VScroll 无 Header, 也无任何行
        _, _, inner, header, body_clip, vscroll = _structure(_render(_make_component(rows=[]))[1])
        assert inner.controls[0] is header
        assert header not in vscroll.controls
        assert vscroll.controls == []  # 无行时 VScroll 无 Header

    def test_vscroll_column_config(self, mock_i18n_state, mock_app_colors_state):
        """AC-10/AC-11: VScroll Column scroll=AUTO + expand=True + spacing=0 + key。"""
        vscroll = _structure(_render(_make_component())[1])[5]
        assert isinstance(vscroll, ft.Column)
        assert vscroll.scroll == ft.ScrollMode.AUTO
        assert vscroll.expand is True
        assert vscroll.spacing == 0
        assert vscroll.key is not None

    def test_vscroll_contains_table_row_components(self, mock_i18n_state, mock_app_colors_state):
        """方案 §5.2: VScroll controls 是 TableRow 组件 (独立 hover state)。"""
        rows = [_make_row_data() for _ in range(3)]
        vscroll = _structure(_render(_make_component(rows=rows))[1])[5]
        assert len(vscroll.controls) == 3
        for row in vscroll.controls:
            assert isinstance(row, ft.Component)

    def test_empty_rows_renders_empty_vscroll(self, mock_i18n_state, mock_app_colors_state):
        vscroll = _structure(_render(_make_component(rows=[]))[1])[5]
        assert vscroll.controls == []

    def test_header_row_contains_one_cell_per_column(self, mock_i18n_state, mock_app_colors_state):
        header = _structure(_render(_make_component())[1])[3]
        header_row = header.content
        assert isinstance(header_row, ft.Row)
        assert len(header_row.controls) == 4

    def test_outer_column_expands(self, mock_i18n_state, mock_app_colors_state):
        _, result = _render(_make_component())
        assert result.expand is True


class TestPaginatedTableRowsColumnConfig:
    """验证 VScroll Column(scroll=AUTO) + BodyClip(clip_behavior) 配置 (方案 D-v3 核心契约)。"""

    def test_vscroll_scroll_auto(self, mock_i18n_state, mock_app_colors_state):
        vscroll = _structure(_render(_make_component())[1])[5]
        assert vscroll.scroll == ft.ScrollMode.AUTO

    def test_vscroll_key_changes_with_rows_token(self, mock_i18n_state, mock_app_colors_state):
        """VScroll key 随 rows 引用变化 (rows 变化时重建以重置滚动位置)。"""
        rows1 = [_make_row_data()]
        rows2 = [_make_row_data()]
        _, result1 = _render(_make_component(rows=rows1))
        _, result2 = _render(_make_component(rows=rows2))
        vscroll1 = _structure(result1)[5]
        vscroll2 = _structure(result2)[5]
        assert vscroll1.key == f"vt_{id(rows1)}"
        assert vscroll2.key == f"vt_{id(rows2)}"
        assert vscroll1.key != vscroll2.key

    def test_body_clip_clip_behavior_hard_edge(self, mock_i18n_state, mock_app_colors_state):
        body_clip = _structure(_render(_make_component())[1])[4]
        assert body_clip.clip_behavior == ft.ClipBehavior.HARD_EDGE

    def test_vscroll_expand_true(self, mock_i18n_state, mock_app_colors_state):
        vscroll = _structure(_render(_make_component())[1])[5]
        assert vscroll.expand is True

    def test_vscroll_spacing_zero(self, mock_i18n_state, mock_app_colors_state):
        vscroll = _structure(_render(_make_component())[1])[5]
        assert vscroll.spacing == 0

    def test_vscroll_no_on_scroll_handler(self, mock_i18n_state, mock_app_colors_state):
        vscroll = _structure(_render(_make_component())[1])[5]
        assert vscroll.on_scroll is None


class TestPaginatedTableRowsChangeEffect:
    """验证 rows 变化时 VScroll Column key 重建行为 (方案 D-v2 保留)。"""

    def test_initial_mount_renders_vscroll(self, mock_i18n_state, mock_app_colors_state):
        component = _make_component()
        run_mount_effects(component)
        result = render_once(component)
        vscroll = _structure(result)[5]
        assert isinstance(vscroll, ft.Column)
        assert vscroll.key is not None  # 首次 mount 后 rows 引用存在，key 应为 vt_<id>
        assert vscroll.key.startswith("vt_")


# ---------------------------------------------------------------------------
# 列宽拖拽行为 (G4, R13 契约)
# ---------------------------------------------------------------------------


class TestColumnDrag:
    """验证 PaginatedTable 内列宽拖拽 handle 行为 (primary_delta 主路径 / clamp / 节流)。"""

    def test_drag_handle_present_in_rendered_header(self, mock_i18n_state, mock_app_colors_state):
        handle = _header_handle_of(_render(_make_component())[1], 0)
        assert handle.exclude_from_semantics is True
        assert handle.mouse_cursor == ft.MouseCursor.RESIZE_LEFT_RIGHT
        assert handle.drag_interval == 16

    def test_drag_update_changes_column_width(self, mock_i18n_state, mock_app_colors_state):
        """拖拽 +50px → header cell 宽度 120 → 170 (set_col_widths 触发重渲)。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 50
        _trigger_callback(handle.on_horizontal_drag_update, e)
        result2 = render_once(component)
        cell0 = _header_cell_of(result2, 0)
        assert cell0.width == 170  # 120 + 50

    def test_drag_update_clamps_to_max(self, mock_i18n_state, mock_app_colors_state):
        """拖拽超出 MAX_COL_WIDTH → clamp 到 600。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 1000
        _trigger_callback(handle.on_horizontal_drag_update, e)
        result2 = render_once(component)
        cell0 = _header_cell_of(result2, 0)
        assert cell0.width == 600

    def test_drag_update_clamps_to_min(self, mock_i18n_state, mock_app_colors_state):
        """拖拽负增量 → clamp 到 MIN_COL_WIDTH=60。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = -1000
        _trigger_callback(handle.on_horizontal_drag_update, e)
        result2 = render_once(component)
        cell0 = _header_cell_of(result2, 0)
        assert cell0.width == 60

    def test_drag_update_primary_delta_none_is_noop(self, mock_i18n_state, mock_app_colors_state):
        """primary_delta=None → 视为 0 增量，宽度不变（V1 无 local_delta 回退，R13）。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = None
        _trigger_callback(handle.on_horizontal_drag_update, e)
        result2 = render_once(component)
        cell0 = _header_cell_of(result2, 0)
        assert cell0.width == 120

    def test_drag_end_commits_final_width(self, mock_i18n_state, mock_app_colors_state):
        """拖拽结束提交最终宽度并清空 active_col。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 40
        _trigger_callback(handle.on_horizontal_drag_update, e)
        _trigger_callback(handle.on_horizontal_drag_end, MagicMock())
        result2 = render_once(component)
        cell0 = _header_cell_of(result2, 0)
        assert cell0.width == 160  # 120 + 40


# ---------------------------------------------------------------------------
# PaginatedTable 详情动作列 (MINOR-09 item 4)
# ---------------------------------------------------------------------------


class TestPaginatedTableDetailColumn:
    """on_row_detail 非空时追加列尾「详情」动作列 (item 4)。"""

    def test_no_detail_column_without_callback(self, mock_i18n_state, mock_app_colors_state):
        """未传 on_row_detail 时不追加动作列 (向后兼容)。"""
        header = _structure(_render(_make_component())[1])[3]
        assert len(header.content.controls) == 4

    def test_appends_detail_column_with_callback(self, mock_i18n_state, mock_app_colors_state):
        """传 on_row_detail 时列尾追加 1 列 (4 → 5)。"""
        header = _structure(_render(_make_component(on_row_detail=MagicMock()))[1])[3]
        assert len(header.content.controls) == 5

    def test_detail_header_cell_has_no_drag_handle(self, mock_i18n_state, mock_app_colors_state):
        """动作列不参与列宽拖拽 (无 Row[sort_area, handle] 结构)。"""
        header = _structure(_render(_make_component(on_row_detail=MagicMock()))[1])[3]
        detail_cell = header.content.controls[-1]
        assert not isinstance(detail_cell.content, ft.Row)

    def test_detail_header_cell_not_sortable(self, mock_i18n_state, mock_app_colors_state):
        """动作列不参与排序 (不包裹 GestureDetector 排序手势)。"""
        _, result = _render(_make_component(sort_col=None, on_sort=MagicMock(), on_row_detail=MagicMock()))
        detail_cell = _structure(result)[3].content.controls[-1]
        assert not isinstance(detail_cell.content, ft.GestureDetector)

    def test_detail_header_label_uses_i18n(self, mock_i18n_state, mock_app_colors_state):
        """动作列表头标签复用既有 i18n 键 col_details。"""
        from ui.i18n import I18n

        _, result = _render(_make_component(on_row_detail=MagicMock()))
        detail_cell = _structure(result)[3].content.controls[-1]
        text = detail_cell.content.content
        assert isinstance(text, ft.Text)
        assert text.value == I18n.get("col_details")

    def test_detail_column_width_from_constant(self, mock_i18n_state, mock_app_colors_state):
        """动作列使用 DETAIL_COL_WIDTH 默认宽度。"""
        _, result = _render(_make_component(on_row_detail=MagicMock()))
        detail_cell = _structure(result)[3].content.controls[-1]
        assert detail_cell.width == DETAIL_COL_WIDTH


# ---------------------------------------------------------------------------
# PaginatedTable 列宽持久化 (MINOR-09 item 3)
# ---------------------------------------------------------------------------


class TestColumnWidthPersistence:
    """列宽持久化: on_load_col_widths / on_persist_col_widths 回调 (item 3, R16 合规)。"""

    def test_load_applies_persisted_widths_on_mount(self, mock_i18n_state, mock_app_colors_state):
        """挂载时经 on_load_col_widths 加载持久化列宽并覆盖列定义默认值。"""
        component = _make_component(
            col_widths_key="vt_key",
            on_load_col_widths=lambda: {"ts_code": 250},
        )
        _, result = _render(component)
        assert _header_cell_of(result, 0).width == 250

    def test_load_none_keeps_column_defaults(self, mock_i18n_state, mock_app_colors_state):
        """on_load_col_widths 返回 None → 保持列定义默认宽度。"""
        component = _make_component(
            col_widths_key="vt_key",
            on_load_col_widths=lambda: None,
        )
        _, result = _render(component)
        assert _header_cell_of(result, 0).width == 120

    def test_load_exception_falls_back_to_defaults(self, mock_i18n_state, mock_app_colors_state):
        """on_load_col_widths 抛异常 → 静默回退列定义默认值, 不阻断渲染。"""

        def _boom():
            raise OSError("config unavailable")

        component = _make_component(col_widths_key="vt_key", on_load_col_widths=_boom)
        _, result = _render(component)
        assert _header_cell_of(result, 0).width == 120

    def test_persist_called_on_drag_end(self, mock_i18n_state, mock_app_colors_state):
        """拖拽结束时经 on_persist_col_widths 上抛最终列宽。"""
        persisted: dict = {}
        component = _make_component(
            col_widths_key="vt_key",
            on_persist_col_widths=lambda w: persisted.update(w),
        )
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 50
        _trigger_callback(handle.on_horizontal_drag_update, e)
        _trigger_callback(handle.on_horizontal_drag_end, MagicMock())
        assert persisted == {"ts_code": 170}

    def test_no_persist_callback_drag_end_is_noop(self, mock_i18n_state, mock_app_colors_state):
        """未接线 on_persist_col_widths 时拖拽结束不抛 (降级为实例级)。"""
        component = _make_component()
        _, result = _render(component)
        handle = _header_handle_of(result, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 30
        _trigger_callback(handle.on_horizontal_drag_update, e)
        _trigger_callback(handle.on_horizontal_drag_end, MagicMock())
        result2 = render_once(component)
        assert _header_cell_of(result2, 0).width == 150

    def test_widths_restored_on_remount_with_same_key(self, mock_i18n_state, mock_app_colors_state):
        """DoD 切页后保持: 拖拽结果经持久化, 重新挂载 (切页/重建) 时按键加载恢复。"""
        persisted: dict = {}
        comp1 = _make_component(on_persist_col_widths=lambda w: persisted.update(w))
        _, result1 = _render(comp1)
        handle = _header_handle_of(result1, 0)
        _trigger_callback(handle.on_horizontal_drag_start, MagicMock())
        e = MagicMock()
        e.primary_delta = 50
        _trigger_callback(handle.on_horizontal_drag_update, e)
        _trigger_callback(handle.on_horizontal_drag_end, MagicMock())
        assert persisted == {"ts_code": 170}

        comp2 = _make_component(col_widths_key="vt_key", on_load_col_widths=lambda: dict(persisted))
        _, result2 = _render(comp2)
        assert _header_cell_of(result2, 0).width == 170
