"""virtual_table — 声明式分页表格 (方案 D-v3: 水平滚动 + sticky header + 列宽拖拽 + hover 局部化).

从命令式容器子类重写为 ``@ft.component def PaginatedTable(...) -> ft.Column``
(CLAUDE.md §3.2 MVVM, §3.3).

变更要点 (方案 D-v3, 分页表实现方案.md §5/§6):
- 恢复水平滚动: 外层 ``Row(scroll=ScrollMode.AUTO, expand=True, vertical_alignment=STRETCH)`` 承载
  Inner Column (``width=total_w``, 不设 expand=True), 表头与表体同在一个水平滚动 Row 内, 横滑时列对齐 (G1/G3)
- Sticky header: Header Container 抽到 Inner Column 第一行, 不在垂直滚动区 VScroll 内, 垂直滚动时位置不变 (G2)
- 滚动条自适应: 水平/垂直均用 ``ScrollMode.AUTO``, 内容溢出才显示滚动条 (G7)
- 列宽拖拽: 复用 resizable_splitter.py 的 R13 拖拽契约 (on_horizontal_drag_* + primary_delta +
  _ColWidthsCache use_ref 缓存 + 节流), 每列 header 右边缘 6px 拖拽把手 (G4)
- Hover 局部化: hover state 下沉到独立 ``TableRow`` ``@ft.component`` 行内 use_state, 不再触发全表重建 (G5)
- 表体列/行仍用 Column 全量渲染 (不做窗口化), 确保 E2E 语义节点稳定 (方案 D-v2 保留)

保留:
- @ft.component 函数组件形态 (MVVM 强制)
- next_sort_state / _total_width 纯函数
- _build_header / _build_cells / _build_row 单元构建 (theme-dependent)
- 行点击 / trend 色 / code TextSpan
- theme 自动重渲染: ``ft.use_state(AppColors.get_observable_state)`` 订阅 Layer 2 表格色
"""

import logging
import time
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import safe_controls
from ui.i18n import I18n
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import Eid
from ui.theme import AppColors, AppStyles

logger = logging.getLogger(__name__)

# UX-11 (P2-03): 行高 30→32 — WCAG 2.2 24px 最低目标之上, 缓解高频点击与系统缩放
ROW_HEIGHT = 32
HEADER_HEIGHT = 35
MIN_TABLE_WIDTH = 800
MIN_COL_WIDTH = 60
MAX_COL_WIDTH = 600
DRAG_INTERVAL = 16
_TREND_COLS = frozenset({"pct_chg", "change", "chg"})
_CODE_COLS = frozenset({"ts_code", "symbol"})

# NOTE(lazy): UX-11 (P2-03) 键盘契约降级 — Flet 0.86.5 无表格 focus/grid 键盘遍历
# (无 Focus 控件; DataTable 无 on_key; KeyboardListener 无 focus 遍历), 键盘用户无方向键
# 行导航与 Enter 打开详情入口 (行点击详情仅鼠标/触屏可达); 本次仅提供排序表头语义状态朗读
# (Semantics 三态) + 行高 32. ceiling: 排序方向可感知, 完整键盘导航不可用.
# upgrade: Flet 提供表格 focus/grid 能力, 或 KeyboardListener 组合方案经 E2E 验证可行时
# (作为独立任务恢复), 评估控件级实现后再解除.


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


def _col_width(
    col_widths: dict[str, int] | None,
    col: dict[str, Any],
) -> int:
    """取单列有效宽度: 优先 col_widths 拖拽值, 回退列定义默认值 (缺省 100)。"""
    if col_widths:
        col_id = str(col.get("id"))
        if col_id in col_widths:
            return int(col_widths[col_id])
    return int(col.get("width", 100))


def _total_width(
    columns: list[dict[str, Any]],
    col_widths: dict[str, int] | None = None,
) -> int:
    return max(sum(_col_width(col_widths, col) for col in columns), MIN_TABLE_WIDTH)


def _clamp_width(width: float, min_width: int, max_width: int) -> int:
    """将宽度 clamp 到 ``[min_width, max_width]``。"""
    return max(min_width, min(max_width, int(width)))


def _assert_table_positive_size(total_w: int, header_h: int, row_h: int) -> None:
    """零尺寸护栏 (UIX-13 C5): 表格结构性尺寸不得为 0 (PR373 视口塌陷防护).

    背景: flet 0.86.5 无控件级布局后尺寸回调 (无 Container.on_resize),
    渲染后容器实际可用宽/高由父链 expand 分配、组件层不可测, 故本护栏在
    结构层锁定不变量 — total_w 不得低于 MIN_TABLE_WIDTH, 表头/行高必须为正,
    防止常量误改 (如 MIN_TABLE_WIDTH 归零) 或宽度计算退化引入视口塌陷。

    :raises ValueError: 任一结构尺寸违反不变量 (显式异常而非 assert,
        避免 -O 优化跳过护栏).
    """
    if total_w < MIN_TABLE_WIDTH:
        raise ValueError(f"PaginatedTable 结构宽度非法: total_w={total_w} < MIN_TABLE_WIDTH={MIN_TABLE_WIDTH}")
    if header_h <= 0 or row_h <= 0:
        raise ValueError(f"PaginatedTable 结构高度非法: header_h={header_h}, row_h={row_h}")


class _ColWidthsCache:
    """列宽拖拽状态缓存 (use_ref 承载, 避免 use_state 触发全表 re-render)。

    与 resizable_splitter.py 的 _DragCache 结构对齐 (R13 契约):
    - 拖拽中的即时宽度记录在 widths[col_id]
    - active_col 记录当前拖拽列
    - last_time 用于 Python 级节流兜底
    """

    __slots__ = ("widths", "active_col", "last_time")

    def __init__(self) -> None:
        self.widths: dict[str, int] = {}
        self.active_col: str | None = None
        self.last_time: float = 0.0


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


def _build_drag_handle(
    on_start: Callable[..., None],
    on_update: Callable[..., None],
    on_end: Callable[..., None],
) -> ft.GestureDetector:
    """构建列宽拖拽把手 (R13 契约: on_horizontal_drag_* + RESIZE_LEFT_RIGHT + drag_interval=16).

    exclude_from_semantics=True 强制 (方案 §6.1/§11a C5): 防止 15 个拖拽把手生成额外
    semantic node 污染 E2E ``flt-tappable`` selector。
    """
    return ft.GestureDetector(
        content=ft.Container(width=6, height=HEADER_HEIGHT),
        mouse_cursor=ft.MouseCursor.RESIZE_LEFT_RIGHT,
        on_horizontal_drag_start=on_start,
        on_horizontal_drag_update=on_update,
        on_horizontal_drag_end=on_end,
        drag_interval=DRAG_INTERVAL,
        exclude_from_semantics=True,
    )


def _build_header(
    columns: list[dict[str, Any]],
    sort_col: str | None,
    sort_asc: bool,
    on_sort: Callable[[str, bool], None] | None,
    col_anchor: Callable[[str], Eid] | None = None,
    col_widths: dict[str, int] | None = None,
    drag_handlers: (dict[str, tuple[Callable[..., None], Callable[..., None], Callable[..., None]]] | None) = None,
) -> list[ft.Control]:
    """构建表头单元格 (theme-dependent)。

    on_sort 非空时用 Semantics(GestureDetector(on_tap)) 包裹（UX-11: 排序状态三态语义标注,
    读屏可感知升/降/可排序；与行一致生成 flt-tappable 语义属性）。
    Container.on_click 生成 InkWell 语义合并会吸收子树 Text（PR #373 实证），
    导致列头文本从语义树消失 + anchor 不可定位。

    drag_handlers 非空时, 每个 header 单元格右缘叠加 6px 拖拽把手 (G4), 用
    ``Row([sort_area(expand), handle], spacing=0)`` 布局 (方案 §6.4: 把手宽度独立于列宽,
    避免与排序 on_click 的 hit-testing 冲突)。

    col_anchor 非空时用 anchored() 包裹 GestureDetector，加 E2E anchor。
    """
    controls: list[ft.Control] = []
    for col in columns:
        col_id = str(col["id"])
        base_label = str(col.get("label", col_id))
        label = base_label
        if sort_col == col_id:
            label += " ↑" if sort_asc else " ↓"
        if on_sort is not None:
            # UX-11 (P2-03): 排序表头读屏语义三态标注 — 升序 / 降序 / 可排序.
            if sort_col == col_id and sort_asc:
                state_desc = I18n.get("table_sort_asc")
            elif sort_col == col_id:
                state_desc = I18n.get("table_sort_desc")
            else:
                state_desc = I18n.get("table_sort_action")
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
            # 语义挂 `Text.semantics_label` 而非嵌套 `ft.Semantics`:
            # E2E CI 实证 (PR #655) 在 anchored(COMPLEX, container=True) 与 GestureDetector 之间
            # 插入带 label 的 Semantics 会在 Flutter 语义树生成独立 role=button 节点, 使 EID
            # 前缀与 role=button 归因分离, AnchorPage role_filter="button" 前缀匹配失败
            # (anchor_page.py _wait_for_text_anchor). Text.semantics_label 只改 Text 节点的读屏
            # 标签, 不产生额外语义边界, anchored→GestureDetector 按 PoC A7 正常合并为单
            # role=button 节点, textContent = EID\n语义, EID 保持前缀, 排序状态由该 label 朗读.
            # 分隔符随 locale (zh 全角 / en 半角), 避免英文下跨语言标点混排.
            # label 含 ↑/↓ 箭头: E2E test_screener_sort_by_column 语义断言以
            # "pct_chg (涨跌幅) ↑" 作为朗读锚点 (aria-label* 子串匹配); CanvasKit 无 DOM 文本,
            # 语义节点 label 即读屏文本, 故语义串须保留箭头以兼容既有 E2E 锚点.
            sep = I18n.get("table_sort_sep")
            text.semantics_label = f"{label}{sep}{state_desc}"
            gesture = ft.GestureDetector(
                content=content,
                on_tap=_make_sort_handler(sort_col, sort_asc, col_id, on_sort),
            )
            if col_anchor is not None:
                gesture = anchored(col_anchor(col_id), gesture)
            control: ft.Control = gesture
        else:
            control = content
        width = _col_width(col_widths, col)
        if drag_handlers is not None:
            start, update, end = drag_handlers[col_id]
            controls.append(
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Container(content=control, expand=True),
                            _build_drag_handle(start, update, end),
                        ],
                        spacing=0,
                    ),
                    width=width,
                )
            )
        else:
            controls.append(ft.Container(control, width=width))
    return controls


def _build_cells(
    row_data: dict[str, Any],
    columns: list[dict[str, Any]],
    col_widths: dict[str, int] | None = None,
) -> list[ft.Container]:
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
        if col_widths is not None:
            cells.append(ft.Container(content, width=_col_width(col_widths, col)))
        else:
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
    row_anchor: Callable[[dict[str, Any]], Eid | None] | None = None,
    col_widths: dict[str, int] | None = None,
) -> ft.Control:
    """构建单个行 (方案 D: on_row_click 非空时用 GestureDetector 包裹生成 flt-tappable 语义属性)。

    Args:
        abs_idx: 行绝对索引 (用于 bgcolor 奇偶交替)。
        is_hovered: 当前行是否处于 hover 态 (P2-8: 切换 bgcolor 为 TABLE_ROW_HOVER)。
        on_hover: hover 事件回调 (P2-8: 由 TableRow 传入 set_hovered 触发当前行重渲染)。
        col_widths: 列宽拖拽覆盖 (None 时回退列定义默认宽度)。

    Note:
        on_row_click=None 时直接返回 Container (不包裹 GestureDetector), 避免 Flutter
        "GestureDetector should have at least one event handler defined" 警告覆盖行文本
        的语义节点 (PR #392 回归修复: data_explorer 表格行文本被警告文本覆盖导致 E2E 失败)。

        方案 E/G (Container.on_click + MergeSemantics) 已被证伪并回退 (PR #373):
        Container.on_click 在 Flutter 端生成 InkWell, 其语义合并会将子树所有 Text 语义
        吸收进单个 role=button 节点且 label 为空 (E2E DOM dump: node-111 text='' aria=''),
        导致行文本从语义树中彻底消失, get_by_text 无法匹配. GestureDetector 不合并子树
        语义, Text 节点独立存在, 行文本保持可见.
    """
    inner = ft.Container(
        height=ROW_HEIGHT,
        width=total_w,
        ink=True,
        bgcolor=AppStyles.data_table_row(abs_idx, is_hovered=is_hovered),
        content=ft.Row(safe_controls(_build_cells(row_data, columns, col_widths)), spacing=0),
        on_hover=on_hover,
    )
    if on_row_click is None:
        return inner
    gesture = ft.GestureDetector(
        content=inner,
        on_tap=_make_row_click_handler(on_row_click, row_data),
    )
    if row_anchor is not None:
        eid = row_anchor(row_data)
        if eid is not None:
            return anchored(eid, gesture)
    return gesture


@ft.component
def TableRow(
    abs_idx: int,
    row_data: dict[str, Any],
    columns: list[dict[str, Any]],
    col_widths: dict[str, int],
    on_row_click: Callable[[dict[str, Any]], None] | None,
    row_anchor: Callable[[dict[str, Any]], Eid | None] | None = None,
) -> ft.Control:
    """独立行组件 (方案 §5.2/§5.3): hover state 下沉到行内 use_state (G5, ~100× 性能)。

    不设置显式 key, 让 Flet reconciliation 按列表位置复用实例 (列宽变化/主题切换时
    hovered state 保留)。翻页/排序时 VScroll key 变化 → 整行重建, hover 丢失为预期行为。
    """
    hovered, set_hovered = ft.use_state(False)

    def _on_hover(e) -> None:
        # e.data == "true" 表示进入; "false" 表示离开
        set_hovered(str(e.data) == "true")

    total_w = _total_width(columns, col_widths)
    return _build_row(
        abs_idx,
        row_data,
        columns,
        total_w,
        on_row_click,
        is_hovered=hovered,
        on_hover=_on_hover,
        row_anchor=row_anchor,
        col_widths=col_widths,
    )


@ft.component
def PaginatedTable(
    rows: list[dict[str, Any]] | None = None,
    columns: list[dict[str, Any]] | None = None,
    sort_col: str | None = None,
    sort_asc: bool = True,
    on_sort: Callable[[str, bool], None] | None = None,
    on_row_click: Callable[[dict[str, Any]], None] | None = None,
    col_anchor: Callable[[str], Eid] | None = None,
    row_anchor: Callable[[dict[str, Any]], Eid | None] | None = None,
) -> ft.Column:
    """声明式分页表格 (方案 D-v3: 水平滚动 + sticky header + 列宽拖拽 + hover 局部化).

    本组件不做窗口化 (无虚拟滚动), 依赖调用方分页, 单页 ≤100 行:
    调用方应先分页、仅传当页 rows, 由本组件 Column 全量渲染 (方案 D-v2 保留,
    确保 E2E 语义节点稳定)。类名保留 "PaginatedTable", 以本文档为准确认行为。

    Args:
        rows: 当页全量行数据 (dict 列表); Column 直接渲染全部行.
        columns: 列定义 (id/label/width)。
        sort_col: 当前排序列 id; 表头显示方向箭头。
        sort_asc: 当前排序方向 (True=升序)。
        on_sort: 列头点击回调 (col_id, new_asc); 由消费方更新 sort_col/sort_asc props。
        on_row_click: 行点击回调 (row_data)。

    布局树 (方案 §5.1):
        Outer(Column, expand=True)
          └─ HScroll(Row, scroll=AUTO, expand=True, vertical_alignment=STRETCH)
               └─ Inner(Column, spacing=0, width=total_w)  # 不设 expand=True
                    ├─ Header(Container, width=total_w, height=HEADER_HEIGHT)  # sticky
                    └─ BodyClip(Container, expand=True, clip=HARD_EDGE)
                         └─ VScroll(Column, scroll=AUTO, spacing=0, expand=True, key=vt_<id(rows)>)
                              └─ TableRow × N  # 各自独立 hover state

    E2E 修复 (方案 D-v2 保留): Column 不做窗口化, 所有行立即布局并生成语义节点;
    rows 变化时通过 VScroll key 重建重置滚动位置.
    """
    # theme 订阅 (Layer 2 表格色随主题自动重渲染)
    ft.use_state(AppColors.get_observable_state)

    rows_list = rows or []
    cols_list = columns or []

    # 列宽拖拽: col_widths 顶层 use_state (触发 Header+Body width prop diff),
    # col_widths_ref 用 _ColWidthsCache 缓存拖拽即时值 (不触发 re-render) (§5.3/§6.2)
    # 初始值必须传 dict 实例 (dict[str, int]()) 而非类型 dict[str, int]:
    # 后者是 types.GenericAlias, 虽因 `in` 返回 False 而"碰巧"不崩, 但语义错误且脆弱
    col_widths, set_col_widths = ft.use_state(dict[str, int]())
    col_cache = ft.use_ref(_ColWidthsCache)
    cache = col_cache.current
    assert cache is not None

    # rows 变化时通过 key 重建 Column 重置滚动位置 (对齐原命令式数据推送行为)
    # scroll_to 对 Column "ineffective" (flet-mcp 验证), 故用 key 重建
    rows_token = id(rows) if rows is not None else 0
    list_view_key = f"vt_{rows_token}"

    # --- 列宽拖拽 handler 工厂 (R13 契约, §6.2) ---
    def _make_drag_start_handler(col_id: str, current_width: int) -> Callable[..., None]:
        def _on_drag_start(e) -> None:
            cache.active_col = col_id
            cache.widths.setdefault(col_id, current_width)

        return _on_drag_start

    def _make_drag_update_handler(col_id: str) -> Callable[..., None]:
        def _on_drag_update(e) -> None:
            active_col = cache.active_col
            if active_col is None:
                return
            # R13: V1 DragUpdateEvent 用 primary_delta (水平拖拽 x 增量);
            # local_delta.x 作为回退 (兼容边界场景)
            delta_x = getattr(e, "primary_delta", None)
            if delta_x is None:
                local_delta = getattr(e, "local_delta", None)
                delta_x = getattr(local_delta, "x", 0) if local_delta else 0
            current = cache.widths[active_col]
            new_w = _clamp_width(current + delta_x, MIN_COL_WIDTH, MAX_COL_WIDTH)
            if new_w == current:
                return
            cache.widths[active_col] = new_w

            # Python 级节流兜底: 仅节流 set_col_widths, 防止 reconcile 过频
            current_time = time.time()
            if current_time - cache.last_time < (DRAG_INTERVAL / 1000.0):
                return
            cache.last_time = current_time
            set_col_widths(dict(cache.widths))

        return _on_drag_update

    def _make_drag_end_handler(col_id: str) -> Callable[..., None]:
        def _on_drag_end(e) -> None:
            if cache.active_col is None:
                return
            set_col_widths(dict(cache.widths))
            cache.active_col = None

        return _on_drag_end

    # per-column 拖拽 handler (避免闭包晚绑定, 对齐 _make_sort_handler 模式)
    drag_handlers: dict[str, tuple[Callable[..., None], Callable[..., None], Callable[..., None]]] = {}
    for col in cols_list:
        col_id = str(col["id"])
        w = _col_width(col_widths, col)
        drag_handlers[col_id] = (
            _make_drag_start_handler(col_id, w),
            _make_drag_update_handler(col_id),
            _make_drag_end_handler(col_id),
        )

    total_w = _total_width(cols_list, col_widths)
    row_count = len(rows_list)

    # UIX-13 C5: 零尺寸护栏 (结构不变量, 防 PR373 类视口塌陷; 显式异常见函数 docstring)
    _assert_table_positive_size(total_w, HEADER_HEIGHT, ROW_HEIGHT)

    header_controls = _build_header(cols_list, sort_col, sort_asc, on_sort, col_anchor, col_widths, drag_handlers)

    all_rows = [
        TableRow(
            abs_idx=abs_idx,
            row_data=rows_list[abs_idx],
            columns=cols_list,
            col_widths=col_widths,
            on_row_click=on_row_click,
            row_anchor=row_anchor,
        )
        # NOTE(lazy): Python 端构建全量行控件, VScroll(Column, scroll=AUTO) 直接渲染全部行 (无窗口化). ceiling: 所有调用点单页 ≤100 行 (screener page_size 最大 100, data_view MAX_ROWS_UI=100). upgrade: 单页行数上限提升至 ≥500 或观察到构建耗时 > 50ms 时, 评估切换到 ListView build_controls_on_demand=True 并解决 E2E 视口高度为 0 时的子控件不构建问题.
        for abs_idx in range(row_count)
    ]

    # VScroll: 垂直滚动区 (scroll=AUTO 内容溢出才显示滚动条, G7)
    # NOTE(guard): §5.5c 关键点3 — VScroll 必须同时 scroll=AUTO + expand=True:
    # expand 接受父 BodyClip 分配的 tight height 约束, scroll=AUTO 让 rows 溢出转为
    # 内部滚动而不撑开 VScroll/链, 从而保证 HScroll 高度稳定 = 视口高度 (水平 Scrollbar 位置)。
    rows_column = ft.Column(
        controls=safe_controls(all_rows),
        expand=True,
        spacing=0,
        scroll=ft.ScrollMode.AUTO,
        key=list_view_key,  # rows 变化时重建以重置滚动位置
    )
    # BodyClip: 裁剪溢出行内容 (对应原 ListView HARD_EDGE 行为)
    body_clip = ft.Container(
        content=rows_column,
        expand=True,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    # Header: 抽到 Inner Column 第一行 (sticky header, 不在 VScroll 内, 垂直滚动时位置不变)
    header_container = ft.Container(
        content=ft.Row(safe_controls(header_controls), spacing=0),
        bgcolor=AppColors.TABLE_HEADER_BG,
        height=HEADER_HEIGHT,
        width=total_w,
        border=ft.Border.only(bottom=ft.BorderSide(1, AppColors.TABLE_BORDER)),
    )
    # Inner Column: 不设 expand=True (expand 沿主轴即水平方向 flex 分配, 会覆盖 width=total_w),
    # 高度由 HScroll.vertical_alignment=STRETCH 沿交叉轴撑满 (§5.1/§5.4/§5.5)
    inner_column = ft.Column(
        controls=[header_container, body_clip],
        spacing=0,
        width=total_w,
    )
    # HScroll: 水平滚动容器 (scroll=AUTO 内容溢出才显示滚动条, G7)
    # NOTE(guard): §5.5c 关键点1+2 — HScroll 必须 expand=True (撑满 Outer 分配高度=视口) +
    # vertical_alignment=STRETCH (让 Inner 被拉伸而非撑开 Row, 保持 Row 高度 = 视口高度,
    # 水平 Scrollbar orientation=BOTTOM 位置 = 视口底部)。
    h_scroll_row = ft.Row(
        controls=[inner_column],
        scroll=ft.ScrollMode.AUTO,
        expand=True,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )
    # NOTE(lazy): 列宽仅存活于 PaginatedTable 实例生命周期 (use_state), 关闭 tab/切视图/重启后重置为 columns 定义默认值. ceiling: 适用于用户偶发拖拽 (<5次/会话) 与列数量固定的场景, 每次进入视图重新拖 5 列 = 5 秒操作代价, 用户可容忍. upgrade: 用户反馈"每次都要重拖太烦"、或拖拽频率 profiling 显示 >20 次/会话、或产品新增"保存视图布局"需求时, 升级为 ConfigHandler 持久化 column_widths_by_view.
    return ft.Column(
        controls=[h_scroll_row],
        expand=True,
        spacing=0,
    )
