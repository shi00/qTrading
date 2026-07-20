"""TaskCenterView — 声明式组件 (Phase 3.1).

从命令式容器子类重写为 @ft.component + use_viewmodel 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现)。

变更要点:
- 旧命令式类基类 → ``@ft.component def TaskCenterView()``
- 生命周期回调 / 手动刷新 / V1 兼容垫片全部移除
- ``self._build_task_card(t)`` → 模块级纯函数 ``_build_task_card(row, on_cancel)``
- 分页/取消/清理状态由 TaskCenterViewModel 管理,View 通过 use_viewmodel 消费
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅,自动重渲染
"""

import logging
from collections.abc import Callable

import flet as ft

from core.i18n import Message
from services.task_manager import TaskStatus
from ui.components.flet_type_helpers import safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.task_center_view_model import (
    PAGE_SIZE,
    TaskCenterViewModel,
    TaskRow,
)
from ui.views.viewport_state import ViewportState
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)

# --- Status display config ---

_STATUS_I18N_MAP = {
    TaskStatus.QUEUED: "task_status_queued",
    TaskStatus.RUNNING: "task_status_running",
    TaskStatus.COMPLETED: "task_status_completed",
    TaskStatus.FAILED: "task_status_failed",
    TaskStatus.CANCELLED: "task_status_cancelled",
    TaskStatus.INTERRUPTED: "task_status_interrupted",
}

_STATUS_ICON_MAP = {
    TaskStatus.QUEUED: ft.Icons.SCHEDULE_OUTLINED,
    TaskStatus.RUNNING: ft.Icons.PLAY_CIRCLE_OUTLINE,
    TaskStatus.COMPLETED: ft.Icons.CHECK_CIRCLE_OUTLINE,
    TaskStatus.FAILED: ft.Icons.ERROR_OUTLINE,
    TaskStatus.CANCELLED: ft.Icons.CANCEL_OUTLINED,
    TaskStatus.INTERRUPTED: ft.Icons.WARNING_AMBER_OUTLINED,
}

_STATUS_COLOR_MAP = {
    TaskStatus.QUEUED: AppColors.TEXT_SECONDARY,
    TaskStatus.RUNNING: AppColors.INFO,
    TaskStatus.COMPLETED: AppColors.SUCCESS,
    TaskStatus.FAILED: AppColors.ERROR,
    TaskStatus.CANCELLED: AppColors.WARNING,
    TaskStatus.INTERRUPTED: AppColors.TEXT_DISABLED,
}


def _format_time(dt_obj):
    if not dt_obj:
        return "--:--"
    return dt_obj.strftime("%H:%M:%S")


def _get_status_label(status: TaskStatus) -> str:
    return I18n.get(_STATUS_I18N_MAP.get(status, "task_status_queued"), status.value)


def _get_status_color(status: TaskStatus) -> str:
    return _STATUS_COLOR_MAP.get(status, AppColors.TEXT_SECONDARY)


def _render_task_field(val: Message | str) -> str:
    """Render ``Message | str`` task field to display string by current locale.

    Task 3.1: View-side i18n rendering. VM 提交 Message (key+params) 给 TaskManager,
    View 渲染时调 ``I18n.get(msg.key, **msg.params)`` 翻译为当前 locale 字符串.
    ``str`` 直接透传 (向后兼容旧持久化字符串, DoD #3).

    嵌套 key 约定 (与 screener_view._render_status_message 一致): params 中以
    ``_key`` 结尾的字符串参数会先翻译再填入主模板 (去掉 ``_key`` 后缀).
    """
    if isinstance(val, str):
        return val
    params = dict(val.params)
    for k in list(params):
        if k.endswith("_key") and isinstance(params[k], str):
            params[k[:-4]] = I18n.get(params[k])
            del params[k]
    return I18n.get(val.key, **params)


def _build_task_card(row: TaskRow, on_cancel: Callable[[str], None]) -> ft.Container:
    """Build a single task card with status badge, progress, and actions.

    Pure function — no self/state dependency. Receives immutable TaskRow + on_cancel callback.
    """
    status_color = _get_status_color(row.status)
    status_label = _get_status_label(row.status)
    status_icon = _STATUS_ICON_MAP.get(row.status, ft.Icons.HELP_OUTLINE)

    # --- Status badge ---
    status_badge = ft.Container(
        content=ft.Row(
            [
                ft.Icon(status_icon, size=14, color=status_color),
                ft.Text(
                    status_label,
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=status_color,
                ),
            ],
            spacing=4,
            tight=True,
        ),
        border=ft.Border.all(1, status_color),
        border_radius=12,
        padding=ft.Padding.symmetric(horizontal=10, vertical=3),
    )

    # --- Type chip ---
    type_chip = ft.Container(
        content=ft.Text(_render_task_field(row.task_type), size=11, color=AppColors.TEXT_SECONDARY),
        bgcolor=ft.Colors.with_opacity(0.08, AppColors.PRIMARY),
        border_radius=4,
        padding=ft.Padding.symmetric(horizontal=8, vertical=2),
    )

    # --- Top row: name + badges ---
    top_row = ft.Row(
        [
            type_chip,
            ft.Text(
                _render_task_field(row.name),
                weight=ft.FontWeight.W_600,
                size=14,
                color=AppColors.TEXT_PRIMARY,
                expand=True,
                max_lines=1,
                overflow=ft.TextOverflow.ELLIPSIS,
            ),
            status_badge,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=8,
    )

    # --- Description / Error ---
    if row.status == TaskStatus.FAILED and row.error:
        desc_text = I18n.get(row.error)
    else:
        desc_text = _render_task_field(row.description)
    desc_row = ft.Text(
        desc_text or "",
        size=12,
        color=AppColors.TEXT_HINT,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )

    # --- Progress bar ---
    if row.status == TaskStatus.RUNNING:
        pct = row.progress * 100
        progress_row = ft.Row(
            [
                ft.ProgressBar(
                    value=row.progress,
                    expand=True,
                    color=AppColors.INFO,
                    bgcolor=ft.Colors.with_opacity(0.12, AppColors.INFO),
                    bar_height=6,
                    border_radius=3,
                ),
                ft.Text(
                    f"{pct:.1f}%",
                    size=12,
                    weight=ft.FontWeight.W_600,
                    color=AppColors.INFO,
                    width=48,
                    text_align=ft.TextAlign.RIGHT,
                ),
            ],
            spacing=10,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    elif row.status in (
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.INTERRUPTED,
    ):
        val = 1.0 if row.status == TaskStatus.COMPLETED else row.progress
        progress_row = ft.ProgressBar(
            value=val,
            expand=True,
            color=status_color,
            bgcolor=ft.Colors.with_opacity(0.08, status_color),
            bar_height=4,
            border_radius=2,
        )
    else:
        # QUEUED — indeterminate thin bar
        progress_row = ft.ProgressBar(
            expand=True,
            color=status_color,
            bgcolor=ft.Colors.with_opacity(0.08, status_color),
            bar_height=3,
            border_radius=2,
        )

    # --- Bottom: time + actions ---
    time_text = ft.Text(
        _format_time(row.created_at),
        size=11,
        color=AppColors.TEXT_HINT,
        italic=True,
    )

    action_btn: ft.Control = ft.Container()
    if row.status in (TaskStatus.RUNNING, TaskStatus.QUEUED) and row.cancellable:
        action_btn = ft.TextButton(
            I18n.get("task_cancel_tooltip"),
            icon=ft.Icons.STOP_CIRCLE_OUTLINED,
            icon_color=AppColors.ERROR,
            style=ft.ButtonStyle(
                color=AppColors.ERROR,
                padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                shape=ft.RoundedRectangleBorder(radius=6),
            ),
            on_click=lambda e, tid=row.id: on_cancel(tid),
        )

    bottom_row = ft.Row(
        [
            ft.Icon(ft.Icons.ACCESS_TIME, size=14, color=AppColors.TEXT_HINT),
            time_text,
            ft.Container(expand=True),
            action_btn,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=6,
    )

    # --- Card assembly ---
    # Highlight running tasks with left accent border
    left_border_color = status_color if row.status == TaskStatus.RUNNING else ft.Colors.TRANSPARENT

    card = ft.Container(
        content=ft.Column(
            [
                top_row,
                desc_row,
                progress_row,
                bottom_row,
            ],
            spacing=6,
        ),
        **AppStyles.card(padding=14, border_radius=8, with_border=False),
        border=ft.Border.only(  # type: ignore[untyped]
            left=ft.BorderSide(3, left_border_color),
            top=ft.BorderSide(1, AppColors.BORDER),
            right=ft.BorderSide(1, AppColors.BORDER),
            bottom=ft.BorderSide(1, AppColors.BORDER),
        ),
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )
    return card


@ft.component
def TaskCenterView(active: bool = True, viewport: ViewportState | None = None) -> ft.Container:
    """Task center dashboard (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(TaskCenterViewModel)``
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` for auto-rerender
    - No page ref, no lifecycle hooks, no manual refresh

    Args:
        active: 当前 tab 是否激活 (控制副作用执行)。
        viewport: AppLayout 下发的窗口尺寸快照 (Phase 6.2 P2-1);
            当前未使用 (YAGNI, 后续任务改造内部布局时消费)。
    """
    # Phase 6.2 P2-1: 接收 viewport 但当前未使用 (后续任务消费)
    _ = viewport
    state, vm = use_viewmodel(TaskCenterViewModel)
    # Subscribe to i18n + theme changes (triggers auto-rerender on locale/theme switch)
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- Handlers ---
    def _on_cancel(task_id: str) -> None:
        UILogger.log_action("TaskCenterView", "Click", f"btn_cancel | task_id={task_id}")
        vm.cancel_task(task_id)

    def _on_clear(e: ft.ControlEvent) -> None:  # noqa: ARG001
        UILogger.log_action("TaskCenterView", "Click", "btn_clear_finished")
        vm.clear_finished()

    def _on_prev(e: ft.ControlEvent) -> None:  # noqa: ARG001
        vm.go_prev()

    def _on_next(e: ft.ControlEvent) -> None:  # noqa: ARG001
        vm.go_next()

    # --- Pagination slice ---
    start = (state.current_page - 1) * PAGE_SIZE
    page_rows = state.tasks[start : start + PAGE_SIZE]

    # --- Header ---
    stats_text = ft.Text(
        I18n.get("task_stats_fmt").format(total=state.total_count, running=state.running_count),
        size=13,
        color=AppColors.TEXT_SECONDARY,
    )

    clear_btn = ft.OutlinedButton(
        I18n.get("task_clear_finished"),
        icon=ft.Icons.CLEANING_SERVICES_OUTLINED,
        on_click=safe_on_click(_on_clear),
        style=ft.ButtonStyle(
            shape=ft.RoundedRectangleBorder(radius=6),
            side=ft.BorderSide(1, AppColors.BORDER),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
        ),
    )

    header_title = ft.Text(
        I18n.get("nav_tasks"),
        size=22,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    header = ft.Row(
        [
            ft.Icon(ft.Icons.TASK_ALT, color=AppColors.PRIMARY, size=28),
            header_title,
            ft.Container(expand=True),
            stats_text,
            clear_btn,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- Empty state ---
    empty_view = ft.Container(
        content=ft.Column(
            [
                ft.Icon(
                    ft.Icons.INBOX_OUTLINED,
                    size=64,
                    color=AppColors.TEXT_HINT,
                ),
                ft.Text(
                    I18n.get("task_empty_title"),
                    size=18,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Text(
                    I18n.get("task_empty_subtitle"),
                    size=13,
                    color=AppColors.TEXT_HINT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        ),
        alignment=ft.Alignment.CENTER,
        expand=True,
        padding=ft.Padding.only(top=60),
    )

    # --- Scrollable area ---
    scroll_controls: list[ft.Control]
    if not state.tasks:
        scroll_controls = [empty_view]
    else:
        scroll_controls = [_build_task_card(row, on_cancel=_on_cancel) for row in page_rows]

    scroll_area = ft.ListView(
        controls=scroll_controls,
        expand=True,
        spacing=0,
        padding=ft.Padding.only(top=8),
    )

    # --- Pagination footer ---
    btn_prev = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT,
        tooltip=I18n.get("common_prev_page"),
        on_click=safe_on_click(_on_prev),
        disabled=state.current_page <= 1,
        icon_size=20,
    )
    btn_next = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT,
        tooltip=I18n.get("common_next_page"),
        on_click=safe_on_click(_on_next),
        disabled=state.current_page >= state.total_pages,
        icon_size=20,
    )
    page_info_text = ft.Text(
        f"{state.current_page} / {state.total_pages}",
        size=13,
        color=AppColors.TEXT_SECONDARY,
    )

    pagination_row = ft.Row(
        [btn_prev, page_info_text, btn_next],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=4,
        visible=state.total_pages > 1,
    )

    # --- Assembly ---
    return ft.Container(
        content=ft.Column(
            [
                header,
                ft.Divider(height=1, color=AppColors.DIVIDER),
                scroll_area,
                pagination_row,
            ],
            expand=True,
        ),
        expand=True,
        padding=ft.Padding.all(20),
    )
