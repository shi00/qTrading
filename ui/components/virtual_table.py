"""virtual_table — 声明式虚拟化表格 (方案 D: ListView 原生虚拟化).

从命令式容器子类重写为 ``@ft.component def PaginatedTable(...) -> ft.Column``
(CLAUDE.md §3.2 MVVM, §3.3).

变更要点 (方案 D):
- 删除自实现虚拟化 (compute_window / window_capacity / _ScrollCache / DEFAULT_VIEWPORT_ROWS / RERENDER_THRESHOLD)
- 改用 ListView 原生虚拟化: build_controls_on_demand + item_extent + cache_extent + key
- 虚拟化由 Flet 引擎层接管, Python 端构建全量行控件 (100 行规模下可忽略)
- rows 变化时通过 key 重建 ListView 重置滚动位置 (scroll_to 对 ListView 无效, 见 flet-mcp 验证)

保留:
- @ft.component 函数组件形态 (MVVM 强制)
- next_sort_state / _total_width 纯函数
- _build_header / _build_cells / _build_row 单元构建 (theme-dependent)
- hover 高亮 / 列头排序 / 行点击 / trend 色 / code TextSpan
- theme 自动重渲染: ``ft.use_state(AppColors.get_observable_state)`` 订阅 Layer 2 表格色
"""

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import safe_controls
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)

ROW_HEIGHT = 30
HEADER_HEIGHT = 35
BUFFER_ROWS = 8
MIN_TABLE_WIDTH = 800
_TREND_COLS = frozenset({"pct_chg", "change", "chg"})
_CODE_COLS = frozenset({"ts_code", "symbol"})


# --- 纯函数 (排序逻辑, 供单元测试覆盖) ---


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
            size=AppStyles.FONT_SIZE_BODY_SM,
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
            # P3-15 色盲友好: 涨跌前置 +/- 符号 (U+002D 普通连字符), 不依赖颜色区分
            # 批次 3 #128: 移除 hasattr + hex fallback, 直接使用 AppColors.UP_RED/DOWN_GREEN
            # MAJ-1 (review fix): 代码后缀色统一用 AppColors.TEXT_HINT (与 on_surface_variant 同源)
            if numeric_val > 0:
                text_color = AppColors.UP_RED
                if not val.startswith("+"):
                    val = "+" + val
            elif numeric_val < 0:
                text_color = AppColors.DOWN_GREEN
                # 统一为 U+002D 普通连字符 (避免 U+2212 在某些字体下渲染异常)
                val = val.replace("−", "-")
                if not val.startswith("-"):
                    val = "-" + val

        if col_id in _CODE_COLS and "." in val:
            parts = val.split(".", maxsplit=1)
            text = ft.Text(
                spans=[
                    ft.TextSpan(parts[0], ft.TextStyle(weight=ft.FontWeight.BOLD, color=text_color)),
                    ft.TextSpan(
                        "." + parts[1],
                        ft.TextStyle(
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=AppColors.TEXT_HINT,
                        ),
                    ),
                ],
                size=AppStyles.FONT_SIZE_BODY_SM,
                no_wrap=True,
            )
        else:
            text = ft.Text(
                val,
                size=AppStyles.FONT_SIZE_BODY_SM,
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
    is_hovered: bool = False,
    on_hover: Callable[[ft.HoverEvent], None] | None = None,
) -> ft.Container:
    """构建单个行 Container (方案 D: 不再绝对定位, 由 ListView 线性布局)。

    Args:
        abs_idx: 行绝对索引 (用于 bgcolor 奇偶交替)。
        is_hovered: 当前行是否处于 hover 态 (P2-8: 切换 bgcolor 为 TABLE_ROW_HOVER)。
        on_hover: hover 事件回调 (P2-8: 由 PaginatedTable 传入 set_hovered_idx 触发重渲染)。
    """
    row = ft.Container(
        height=ROW_HEIGHT,
        width=total_w,
        ink=True,
        bgcolor=AppStyles.data_table_row(abs_idx, is_hovered=is_hovered),
        content=ft.Row(safe_controls(_build_cells(row_data, columns)), spacing=0),
        on_hover=on_hover,
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
    """声明式虚拟化表格 (方案 D: ListView 原生虚拟化)。

    Args:
        rows: 当页全量行数据 (dict 列表); ListView 按需构建可见行。
        columns: 列定义 (id/label/width)。
        sort_col: 当前排序列 id; 表头显示方向箭头。
        sort_asc: 当前排序方向 (True=升序)。
        on_sort: 列头点击回调 (col_id, new_asc); 由消费方更新 sort_col/sort_asc props。
        on_row_click: 行点击回调 (row_data)。

    虚拟化 (方案 D): 删除自实现 viewport 窗口计算, 改用 ListView 原生能力:
    - ``build_controls_on_demand=True``: Flutter 引擎按需构建可见行
    - ``item_extent=ROW_HEIGHT``: 固定行高, 跳过测量阶段
    - ``cache_extent=BUFFER_ROWS * ROW_HEIGHT``: 上下各 BUFFER_ROWS 行缓冲
    - ``key=f"vt_{rows_token}"``: rows 变化时重建 ListView 重置滚动位置
      (scroll_to 对 ListView "ineffective", 见 flet-mcp 验证, 故用 key 重建)
    """
    # theme 订阅 (Layer 2 表格色随主题自动重渲染)
    ft.use_state(AppColors.get_observable_state)

    rows_list = rows or []
    cols_list = columns or []

    # P2-8 MAJ-2 (review fix): hover 触发链路落地 — hovered_idx=-1 表示无 hover
    hovered_idx, set_hovered_idx = ft.use_state(-1)

    # rows 变化时通过 key 重建 ListView 重置滚动位置 (对齐原命令式数据推送行为)
    # scroll_to 对 ListView "ineffective" (flet-mcp 验证), 故用 key 重建
    rows_token = id(rows) if rows is not None else 0
    list_view_key = f"vt_{rows_token}"

    total_w = _total_width(cols_list)
    row_count = len(rows_list)

    header_controls = _build_header(cols_list, sort_col, sort_asc, on_sort)

    def _make_row_hover(abs_idx: int) -> Callable[[ft.HoverEvent], None]:
        """P2-8 MAJ-2: 构造单行 hover 回调, 切换 hovered_idx 触发重渲染。"""

        def _on_hover(e: ft.HoverEvent) -> None:
            # e.data == "true" 表示进入; "false" 表示离开
            set_hovered_idx(abs_idx if str(e.data) == "true" else -1)

        return _on_hover

    all_rows = [
        _build_row(
            abs_idx,
            rows_list[abs_idx],
            cols_list,
            total_w,
            on_row_click,
            is_hovered=(abs_idx == hovered_idx),
            on_hover=_make_row_hover(abs_idx),
        )
        # NOTE(lazy): Python 端构建全量行控件 (非窗口化), 由 ListView build_controls_on_demand 按需渲染. ceiling: 所有调用点单页 ≤100 行 (screener page_size 最大 100, data_view MAX_ROWS_UI=100). upgrade: 单页行数上限提升至 ≥500 或观察到构建耗时 > 50ms 时, 改回自实现窗口化或分页加载.
        for abs_idx in range(row_count)
    ]

    list_view = ft.ListView(
        controls=safe_controls(all_rows),
        expand=True,
        spacing=0,
        # 方案 D: 原生虚拟化配置 (显式声明用于契约测试)
        build_controls_on_demand=True,  # 原生按需构建 (默认值, 显式声明便于契约测试)
        item_extent=ROW_HEIGHT,  # 固定行高, Flutter 跳过测量
        cache_extent=BUFFER_ROWS * ROW_HEIGHT,  # 上下各 BUFFER_ROWS 行缓冲
        key=list_view_key,  # rows 变化时重建以重置滚动位置
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
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
