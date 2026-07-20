"""virtual_table — 声明式虚拟化表格 (Phase B.3).

从命令式容器子类重写为 ``@ft.component def PaginatedTable(...) -> ft.Column``
(CLAUDE.md §3.2 MVVM, §3.3).

变更要点:
- 命令式容器子类 → ``@ft.component`` 函数组件
- 移除全部命令式 API (行/列/主题/视口的手动 setter 与手动刷新调用全部删除)
- 状态驱动: rows/columns/sort_col/sort_asc/on_sort/on_row_click 由 props 推送
- 内部 ``use_state`` 管滚动位置 (scroll_first) / 视口高度 (viewport_h)
- theme 自动重渲染: ``ft.use_state(AppColors.get_observable_state)`` 订阅 Layer 2 表格色
- 保留虚拟化: viewport 窗口渲染 (只渲染可见行 + 缓冲行) + 滚动同步 (header 固定)
- 行池回收改为声明式 reconcile (Flet diff 行控件); ``use_ref`` 仅缓存滚动位置/视口即时值
  (CLAUDE.md §3.3: use_ref 缓存数据允许, 缓存命令式实例禁止; 参考 resizable_splitter._DragCache)
"""

import logging
import math
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import safe_controls
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)

ROW_HEIGHT = 30
HEADER_HEIGHT = 35
BUFFER_ROWS = 8
RERENDER_THRESHOLD = 4
DEFAULT_VIEWPORT_ROWS = 30
MIN_TABLE_WIDTH = 800
_TREND_COLS = frozenset({"pct_chg", "change", "chg"})
_CODE_COLS = frozenset({"ts_code", "symbol"})


class _ScrollCache:
    """滚动节流缓存 (``use_ref`` 持久化, 避免 ``use_state`` 触发 re-render)。

    缓存即时数值 (last_first/last_viewport_h) 而非命令式实例, 符合声明式红线
    (参考 resizable_splitter._DragCache 模式)。
    """

    __slots__ = ("last_first", "last_viewport_h")

    def __init__(self) -> None:
        self.last_first: int = -1
        self.last_viewport_h: float = 0.0


# --- 纯函数 (虚拟化 + 排序逻辑, 供单元测试覆盖) ---


def next_sort_state(
    sort_col: str | None,
    sort_asc: bool,
    clicked_col: str,
) -> tuple[str | None, bool]:
    """点击列头后的排序状态转移。

    点击当前排序列 → 翻转方向; 点击新列 → 默认升序。
    """
    if sort_col == clicked_col:
        return sort_col, not sort_asc
    return clicked_col, True


def window_capacity(viewport_h: float) -> int:
    """视口可容纳行数 (含上下缓冲)。"""
    if viewport_h > 0:
        viewport_rows = math.ceil(viewport_h / ROW_HEIGHT)
    else:
        viewport_rows = DEFAULT_VIEWPORT_ROWS
    return max(1, viewport_rows + 2 * BUFFER_ROWS)


def compute_window(
    target_first: int,
    row_count: int,
    viewport_h: float,
) -> tuple[int, int]:
    """计算应渲染的行窗口 ``[start, end)``。

    start 在 target_first 前留 BUFFER_ROWS 缓冲, 并 clamp 到末尾不越界。
    """
    if row_count == 0:
        return 0, 0
    capacity = window_capacity(viewport_h)
    start = max(0, min(target_first - BUFFER_ROWS, max(0, row_count - capacity)))
    end = min(row_count, start + capacity)
    return start, end


def _total_width(columns: list[dict[str, Any]]) -> int:
    return max(sum(int(col.get("width", 100)) for col in columns), MIN_TABLE_WIDTH)


# --- 事件 handler 工厂 (避免闭包晚绑定 + 收窄非 None 回调) ---


def _make_sort_handler(
    sort_col: str | None,
    sort_asc: bool,
    col_id: str,
    on_sort: Callable[[str, bool], None],
) -> Callable[..., None]:
    """构建列头点击 handler: 计算新排序状态并回调消费方。"""

    # e 不标注类型: Flet 事件 handler 槽位为协变 ControlEventHandler[T], 无类型 e 兼容
    def _on_click(e) -> None:
        _, new_asc = next_sort_state(sort_col, sort_asc, col_id)
        on_sort(col_id, new_asc)

    return _on_click


def _make_row_click_handler(
    on_row_click: Callable[[dict[str, Any]], None],
    row_data: dict[str, Any],
) -> Callable[..., None]:
    """构建行点击 handler (捕获非 None 回调 + 行数据)。"""

    # e 不标注类型: Flet 事件 handler 槽位为协变 ControlEventHandler[T], 无类型 e 兼容
    def _on_click(e) -> None:
        on_row_click(row_data)

    return _on_click


# --- 单元构建 (theme-dependent, 随 Observable 重渲染) ---


def _build_header(
    columns: list[dict[str, Any]],
    sort_col: str | None,
    sort_asc: bool,
    on_sort: Callable[[str, bool], None] | None,
) -> list[ft.Container]:
    """构建表头单元格 (theme-dependent)。"""
    controls: list[ft.Container] = []
    for col in columns:
        col_id = str(col["id"])
        label = str(col.get("label", col_id))
        if sort_col == col_id:
            label += " ↑" if sort_asc else " ↓"
        text = ft.Text(
            label,
            weight=ft.FontWeight.BOLD,
            size=12,
            color=AppColors.TABLE_HEADER_TEXT,
            no_wrap=True,
        )
        content = ft.Container(
            content=text,
            alignment=ft.Alignment.CENTER_LEFT,
            padding=ft.Padding.only(left=8, right=8),
        )
        if on_sort is not None:
            content.on_click = _make_sort_handler(sort_col, sort_asc, col_id, on_sort)
        width = int(col.get("width", 100))
        controls.append(ft.Container(content, width=width))
    return controls


def _build_cells(row_data: dict[str, Any], columns: list[dict[str, Any]]) -> list[ft.Container]:
    """构建一行单元格 (theme-dependent)。"""
    cells: list[ft.Container] = []
    for col in columns:
        col_id = str(col["id"])
        val = str(row_data.get(col_id, ""))

        numeric_val: float | None = None
        is_numeric = False
        try:
            numeric_val = float(val.replace("%", "").replace(",", ""))
            is_numeric = True
        except ValueError:
            pass

        text_color = AppColors.TABLE_CELL_NUMERIC if is_numeric else AppColors.TABLE_CELL_TEXT
        alignment = ft.Alignment.CENTER_RIGHT if is_numeric else ft.Alignment.CENTER_LEFT

        is_trend = col_id in _TREND_COLS
        if is_trend and numeric_val is not None:
            if numeric_val > 0:
                text_color = AppColors.UP_RED if hasattr(AppColors, "UP_RED") else "#F44336"
            elif numeric_val < 0:
                text_color = AppColors.DOWN_GREEN if hasattr(AppColors, "DOWN_GREEN") else "#4CAF50"

        if col_id in _CODE_COLS and "." in val:
            parts = val.split(".", maxsplit=1)
            text = ft.Text(
                spans=[
                    ft.TextSpan(parts[0], ft.TextStyle(weight=ft.FontWeight.BOLD, color=text_color)),
                    ft.TextSpan(
                        "." + parts[1],
                        ft.TextStyle(
                            size=10,
                            color=AppColors.TEXT_TERTIARY  # type: ignore[untyped]
                            if hasattr(AppColors, "TEXT_TERTIARY")
                            else "#888888",
                        ),
                    ),
                ],
                size=12,
                no_wrap=True,
            )
        else:
            text = ft.Text(
                val,
                size=12,
                no_wrap=True,
                weight=ft.FontWeight.BOLD if is_trend else None,
                color=text_color,
                font_family="Roboto Mono, monospace" if is_numeric else None,
            )

        content = ft.Container(
            content=text,
            alignment=alignment,
            padding=ft.Padding.only(left=8, right=8),
        )
        width = col.get("width")
        cells.append(ft.Container(content, width=int(width)) if width else ft.Container(content, expand=1))
    return cells


def _build_row(
    abs_idx: int,
    row_data: dict[str, Any],
    columns: list[dict[str, Any]],
    total_w: int,
    on_row_click: Callable[[dict[str, Any]], None] | None,
) -> ft.Container:
    """构建单个绝对定位行 (top = abs_idx * ROW_HEIGHT)。"""
    row = ft.Container(
        left=0,
        top=abs_idx * ROW_HEIGHT,
        height=ROW_HEIGHT,
        width=total_w,
        ink=True,
        bgcolor=AppStyles.data_table_row(abs_idx),
        content=ft.Row(safe_controls(_build_cells(row_data, columns)), spacing=0),
    )
    if on_row_click is not None:
        row.on_click = _make_row_click_handler(on_row_click, row_data)
    return row


@ft.component
def PaginatedTable(
    rows: list[dict[str, Any]] | None = None,
    columns: list[dict[str, Any]] | None = None,
    sort_col: str | None = None,
    sort_asc: bool = True,
    on_sort: Callable[[str, bool], None] | None = None,
    on_row_click: Callable[[dict[str, Any]], None] | None = None,
) -> ft.Column:
    """视口虚拟化表格 (声明式, 保留 viewport 窗口渲染 + 滚动同步)。

    Args:
        rows: 当页全量行数据 (dict 列表); 组件仅渲染 viewport 窗口内行。
        columns: 列定义 (id/label/width)。
        sort_col: 当前排序列 id; 表头显示方向箭头。
        sort_asc: 当前排序方向 (True=升序)。
        on_sort: 列头点击回调 (col_id, new_asc); 由消费方更新 sort_col/sort_asc props。
        on_row_click: 行点击回调 (row_data)。

    虚拟化: 仅渲染 ``[start, end)`` 窗口 (viewport 行 + 2*BUFFER_ROWS 缓冲);
    滚动同步由 ``use_state(scroll_first)`` 触发重渲染, ``use_ref`` 节流避免抖动。
    行池回收改为声明式 reconcile (Flet diff 行控件); ``use_ref`` 仅缓存滚动位置即时值
    (CLAUDE.md §3.3: use_ref 缓存数据允许, 缓存命令式实例禁止)。
    """
    # theme 订阅 (Layer 2 表格色随主题自动重渲染)
    ft.use_state(AppColors.get_observable_state)

    rows_list = rows or []
    cols_list = columns or []

    scroll_first, set_scroll_first = ft.use_state(0)
    viewport_h, set_viewport_h = ft.use_state(0.0)
    scroll_ref = ft.use_ref(_ScrollCache)
    cache = scroll_ref.current
    assert cache is not None

    # rows 变更时重置滚动位置到顶部 (对齐原命令式数据推送行为)
    rows_token = id(rows) if rows is not None else 0

    def _reset_scroll_on_rows_change() -> None:
        cache.last_first = -1
        set_scroll_first(0)

    ft.use_effect(_reset_scroll_on_rows_change, dependencies=[rows_token])

    total_w = _total_width(cols_list)
    row_count = len(rows_list)
    start, end = compute_window(scroll_first, row_count, viewport_h)

    header_controls = _build_header(cols_list, sort_col, sort_asc, on_sort)
    visible_rows = [
        _build_row(abs_idx, rows_list[abs_idx], cols_list, total_w, on_row_click) for abs_idx in range(start, end)
    ]

    canvas = ft.Stack(
        controls=safe_controls(visible_rows),
        height=row_count * ROW_HEIGHT,
        width=total_w,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )

    def _on_scroll(e) -> None:
        vh = getattr(e, "viewport_dimension", None)
        if vh:
            new_vh = float(vh)
            if abs(new_vh - cache.last_viewport_h) > 1.0:
                cache.last_viewport_h = new_vh
                set_viewport_h(new_vh)
        offset = float(getattr(e, "pixels", None) or 0.0)
        new_first = max(0, int(offset // ROW_HEIGHT))
        if cache.last_first < 0 or abs(new_first - cache.last_first) >= RERENDER_THRESHOLD:
            cache.last_first = new_first
            set_scroll_first(new_first)

    list_view = ft.ListView(
        controls=[canvas],
        expand=True,
        spacing=0,
        on_scroll=_on_scroll,
        scroll_interval=100,
    )
    header_container = ft.Container(
        content=ft.Row(safe_controls(header_controls), spacing=0),
        bgcolor=AppColors.TABLE_HEADER_BG,
        height=HEADER_HEIGHT,
        width=total_w,
        border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.TABLE_BORDER)),
    )
    inner_column = ft.Column(
        controls=[header_container, list_view],
        spacing=0,
        width=total_w,
    )
    return ft.Column(
        controls=[
            ft.Row(
                controls=[inner_column],
                expand=True,
                scroll=ft.ScrollMode.ALWAYS,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            )
        ],
        expand=True,
        spacing=0,
    )
