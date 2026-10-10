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

import asyncio
import datetime
import logging
from collections.abc import Callable

import flet as ft

from core.i18n import Message
from ui.components.flet_type_helpers import safe_on_click
from ui.components.state_views import EmptyState
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import TOPIC_NAVIGATE
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels.task_center_view_model import (
    PAGE_SIZE,
    TaskCenterViewModel,
    TaskRow,
    TaskStatus,
)
from utils.log_decorators import UILogger
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


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
    TaskStatus.QUEUED: "TEXT_SECONDARY",
    TaskStatus.RUNNING: "INFO",
    TaskStatus.COMPLETED: "SUCCESS",
    TaskStatus.FAILED: "ERROR",
    TaskStatus.CANCELLED: "WARNING",
    TaskStatus.INTERRUPTED: "TEXT_DISABLED",
}


def _format_time(dt_obj):
    if not dt_obj:
        return "--:--"
    return dt_obj.strftime("%H:%M:%S")


def _format_duration(seconds: float) -> str:
    """Format a non-negative duration (seconds) as ``mm:ss`` / ``h:mm:ss`` (MINOR-11)."""
    total = int(max(0, seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _task_time_summary(row: TaskRow, now: datetime.datetime | None) -> str | None:
    """Build the elapsed / estimated-remaining text for a RUNNING task (MINOR-11).

    Returns ``None`` when the task is not running or its ``started_at`` is unknown.

    R21: 当 ``progress <= 0`` 时无法可靠推算剩余时间，显示「预计剩余 计算中」而非
    伪造一个确定数值；``progress >= 1`` 时不再显示剩余。
    """
    if row.status != TaskStatus.RUNNING or row.started_at is None or now is None:
        return None
    elapsed = now.timestamp() - row.started_at.timestamp()
    elapsed_text = I18n.get("task_elapsed_fmt", duration=_format_duration(elapsed))
    if row.progress <= 0:
        return f"{elapsed_text} · {I18n.get('task_remaining_estimating')}"
    if row.progress >= 1:
        return elapsed_text
    remaining = elapsed * (1 - row.progress) / row.progress
    return f"{elapsed_text} · {I18n.get('task_remaining_fmt', duration=_format_duration(remaining))}"


def _get_status_label(status: TaskStatus) -> str:
    return I18n.get(_STATUS_I18N_MAP.get(status, "task_status_queued"), status.value)


def _get_status_color(status: TaskStatus) -> str:
    """任务状态 → 当期主题色 (F04: 渲染时读取 AppColors, 不固化导入期快照)."""
    return getattr(AppColors, _STATUS_COLOR_MAP.get(status, "TEXT_SECONDARY"))


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


def _build_task_card(
    row: TaskRow,
    on_cancel: Callable[[str], None],
    on_retry: Callable[[str], None] | None = None,
    on_view_details: Callable[[str], None] | None = None,
    on_reopen_source: Callable[[str], None] | None = None,
    now: datetime.datetime | None = None,
) -> ft.Control:
    """Build a single task card with status badge, progress, and actions.

    Pure function — no self/state dependency. Receives immutable TaskRow + callbacks.

    Phase 6.2 (FR-UX-006): FAILED tasks show "Retry" + "View Details" buttons
    when ``on_retry``/``on_view_details`` callbacks are provided.

    MINOR-11: RUNNING tasks show elapsed / estimated-remaining time (``now`` 由调用方
    传入以保持纯函数可测性)；不可重试的历史失败任务显示「前往数据源页重新发起」指引。
    """
    status_color = _get_status_color(row.status)
    status_label = _get_status_label(row.status)
    status_icon = _STATUS_ICON_MAP.get(row.status, ft.Icons.HELP_OUTLINE)

    # --- Status badge ---
    status_badge = ft.Container(
        content=ft.Row(
            [
                ft.Icon(status_icon, size=AppStyles.FONT_SIZE_LG, color=status_color),
                ft.Text(
                    status_label,
                    size=AppStyles.FONT_SIZE_BODY_SM,
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
        content=ft.Text(
            _render_task_field(row.task_type), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY
        ),
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
                size=AppStyles.FONT_SIZE_LG,
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
        size=AppStyles.FONT_SIZE_BODY_SM,
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
                    size=AppStyles.FONT_SIZE_BODY_SM,
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
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_HINT,
        italic=True,
    )
    # MINOR-11: 运行中任务追加「已耗时 · 预计剩余」文本 (now 参数保持纯函数可测)。
    time_summary = _task_time_summary(row, now)
    if time_summary is not None:
        time_text = ft.Row(
            [
                time_text,
                ft.Text(
                    time_summary,
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.INFO,
                    italic=True,
                ),
            ],
            spacing=10,
            tight=True,
        )

    # Phase 6.2 (FR-UX-006): Build action buttons based on task status
    action_buttons: list[ft.Control] = []
    if row.status in (TaskStatus.RUNNING, TaskStatus.QUEUED) and row.cancellable:
        action_buttons.append(
            ft.TextButton(
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
        )
    elif row.status in (TaskStatus.FAILED, TaskStatus.INTERRUPTED):
        # Phase 6.2 (FR-UX-006) + D6-3: Retry + View Details buttons for
        # failed/interrupted tasks (INTERRUPTED 与 FAILED 业务等价，均可重新发起续传)
        # D6-7: 重试按钮可见性基于 is_retryable（状态可重试 AND 保有 factory）——历史任务
        # 从 DB 加载无 factory 即使状态为 FAILED/INTERRUPTED 也无法重试，不显示按钮，
        # 避免"按钮存在但无效"的体验问题。
        if on_retry is not None and row.is_retryable:
            action_buttons.append(
                ft.TextButton(
                    I18n.get("task_retry"),
                    icon=ft.Icons.REFRESH,
                    icon_color=AppColors.PRIMARY,
                    style=ft.ButtonStyle(
                        color=AppColors.PRIMARY,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                        shape=ft.RoundedRectangleBorder(radius=6),
                    ),
                    on_click=lambda e, tid=row.id: on_retry(tid),
                )
            )
        if on_view_details is not None:
            action_buttons.append(
                ft.TextButton(
                    I18n.get("task_view_details"),
                    icon=ft.Icons.INFO_OUTLINE,
                    icon_color=AppColors.TEXT_SECONDARY,
                    style=ft.ButtonStyle(
                        color=AppColors.TEXT_SECONDARY,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                        shape=ft.RoundedRectangleBorder(radius=6),
                    ),
                    on_click=lambda e, tid=row.id: on_view_details(tid),
                )
            )
        # MINOR-11: 历史任务从 DB 加载无 factory，无法重试（is_retryable=False）时给出
        # 「前往数据源页重新发起」指引，避免用户面对失败任务却无处重新发起。
        if on_reopen_source is not None and not row.is_retryable:
            action_buttons.append(
                ft.TextButton(
                    I18n.get("task_failed_reopen_source"),
                    icon=ft.Icons.OPEN_IN_NEW,
                    icon_color=AppColors.PRIMARY,
                    style=ft.ButtonStyle(
                        color=AppColors.PRIMARY,
                        padding=ft.Padding.symmetric(horizontal=12, vertical=4),
                        shape=ft.RoundedRectangleBorder(radius=6),
                    ),
                    on_click=lambda e, tid=row.id: on_reopen_source(tid),
                )
            )

    bottom_row = ft.Row(
        [
            ft.Icon(ft.Icons.ACCESS_TIME, size=AppStyles.FONT_SIZE_LG, color=AppColors.TEXT_HINT),
            time_text,
            ft.Container(expand=True),
            *action_buttons,
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=6,
    )

    # --- Card assembly ---
    # Highlight running tasks with left accent border
    left_border_color = status_color if row.status == TaskStatus.RUNNING else AppColors.TRANSPARENT

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
        **AppStyles.card(padding=AppStyles.SPACING_LG, border_radius=8, with_border=False),
        border=ft.Border.only(  # type: ignore[untyped]
            left=ft.BorderSide(3, left_border_color),
            top=ft.BorderSide(1, AppColors.BORDER),
            right=ft.BorderSide(1, AppColors.BORDER),
            bottom=ft.BorderSide(1, AppColors.BORDER),
        ),
        animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
    )
    # PR-4 Task 4.1 P2-3: 任务行卡片整体 anchor 化 (LABEL kind, 卡片无 on_click, EID 落 textContent)
    return anchored(EIDS.TASK_CENTER.task_row(row.id), card)


@ft.component
def TaskCenterView(active: bool = True) -> ft.Container:
    """Task center dashboard (declarative).

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(TaskCenterViewModel)``
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` for auto-rerender
    - No page ref, no lifecycle hooks, no manual refresh

    Args:
        active: 当前 tab 是否激活 (控制副作用执行)。
    """
    state, vm = use_viewmodel(TaskCenterViewModel)
    # Subscribe to i18n + theme changes (triggers auto-rerender on locale/theme switch)
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # Phase 6.2 (FR-UX-006): Details dialog state (task_id being viewed, None = closed)
    details_task_id, set_details_task_id = ft.use_state(None)
    # MINOR-11: 取消确认对话框状态 (待取消的 task_id, None = 关闭)
    pending_cancel_id, set_pending_cancel_id = ft.use_state(None)

    # MINOR-11: 每秒刷新运行中任务的「已耗时 / 预计剩余」文本。
    # F10: ticker 仅在「页面可见(active) 且 存在运行中任务」时启用——生产七页常驻时
    # 切到别页不卸载组件，旧实现只按 running_count>0 启停，隐藏页只要有运行任务就
    # 每秒唤醒重渲染任务中心。deps 同时含 active 与 has_running，任一变化即经统一
    # cleanup 路径取消旧 ticker 并按需重建（重激活只建一个）。耗时文本按墙钟即时计算
    # （_task_time_summary 以渲染时 get_now() 差值计），切回页面因 active 变化触发
    # 重渲染即显示最新时长，不以隐藏期间暂停的 tick 累计假时间。
    _tick_count, set_tick_count = ft.use_state(0)
    tick_counter_ref = ft.use_ref(0)
    ticker_task_ref = ft.use_ref(None)
    has_running = state.running_count > 0
    ticker_enabled = active and has_running

    def _setup_ticker() -> None:
        # F10: use_effect 的 setup 在 deps 变化时必被调用 (与 deps 取值无关)，因此需
        # 显式守卫——仅页面可见且确有运行中任务时启动 1s ticker，否则不创建循环。
        if not ticker_enabled:
            return
        page = _get_page()
        # FakePage（单测）无 run_task 时跳过，避免测试上下文报错
        if page is None or not hasattr(page, "run_task"):
            return

        async def _tick() -> None:
            try:
                while True:
                    await asyncio.sleep(1)
                    tick_counter_ref.current = (tick_counter_ref.current or 0) + 1
                    set_tick_count(tick_counter_ref.current)
            except asyncio.CancelledError:
                raise  # R2: 必须传播，配合优雅停机

        ticker_task_ref.current = page.run_task(_tick)

    def _cleanup_ticker() -> None:
        # F10: 切离页面 (active→False)、任务归零 (has_running→False)、组件卸载均经此
        # 统一清理路径；置 None 保证重复 cleanup 不重复 cancel，重激活只建一个 ticker。
        if ticker_task_ref.current is not None:
            ticker_task_ref.current.cancel()
            ticker_task_ref.current = None

    ft.use_effect(_setup_ticker, dependencies=[active, has_running], cleanup=_cleanup_ticker)

    # --- Handlers ---
    def _on_cancel(task_id: str) -> None:
        UILogger.log_action("TaskCenterView", "Click", f"btn_cancel | task_id={task_id}")
        # MINOR-11: 取消长任务需二次确认，避免误点中断耗时数小时的全量同步。
        set_pending_cancel_id(task_id)

    def _on_confirm_cancel(e: ft.ControlEvent) -> None:
        task_id = pending_cancel_id
        set_pending_cancel_id(None)
        if task_id is not None:
            UILogger.log_action("TaskCenterView", "Click", f"btn_cancel_confirm | task_id={task_id}")
            vm.cancel_task(task_id)

    def _on_dismiss_cancel(e: ft.ControlEvent) -> None:
        set_pending_cancel_id(None)

    def _on_reopen_source(task_id: str) -> None:
        UILogger.log_action("TaskCenterView", "Click", f"btn_reopen_source | task_id={task_id}")
        _navigate_to_data_source()

    def _navigate_to_data_source() -> None:
        """MINOR-11: 深链导航到数据源子页 (UX-01 协议)，供历史失败任务重新发起指引。

        无 page.go() 路由，通过 PubSub TOPIC_NAVIGATE 广播 "settings:data"，
        app_layout 订阅后切换到数据源子页；send_all_on_topic 非阻塞，不打断进行中任务。
        """
        page = _get_page()
        pubsub = getattr(page, "pubsub", None)
        if pubsub is not None:
            pubsub.send_all_on_topic(TOPIC_NAVIGATE, "settings:data")

    def _on_retry(task_id: str) -> None:
        UILogger.log_action("TaskCenterView", "Click", f"btn_retry | task_id={task_id}")
        vm.retry_task(task_id)  # pragma: no cover - retry 仅在 FAILED/INTERRUPTED 任务触发，单测不覆盖完整 retry 流程

    def _on_view_details(task_id: str) -> None:
        UILogger.log_action("TaskCenterView", "Click", f"btn_details | task_id={task_id}")
        set_details_task_id(task_id)  # pragma: no cover - 详情对话框交互仅集成测试覆盖

    def _on_close_details(e: ft.ControlEvent) -> None:
        set_details_task_id(None)  # pragma: no cover - 关闭对话框交互仅集成测试覆盖

    def _on_clear(e: ft.ControlEvent) -> None:
        UILogger.log_action("TaskCenterView", "Click", "btn_clear_finished")
        vm.clear_finished()

    def _on_prev(e: ft.ControlEvent) -> None:
        vm.go_prev()

    def _on_next(e: ft.ControlEvent) -> None:
        vm.go_next()

    # --- Pagination slice ---
    start = (state.current_page - 1) * PAGE_SIZE
    page_rows = state.tasks[start : start + PAGE_SIZE]

    # --- Header ---
    stats_text = ft.Text(
        I18n.get("task_stats_fmt").format(total=state.total_count, running=state.running_count),
        size=AppStyles.FONT_SIZE_BODY,
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
        size=AppStyles.FONT_SIZE_XL,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    header = ft.Row(
        [
            ft.Icon(ft.Icons.TASK_ALT, color=AppColors.PRIMARY, size=AppStyles.FONT_SIZE_DISPLAY),
            header_title,
            ft.Container(expand=True),
            stats_text,
            clear_btn,
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    # --- Empty state (UIX-14: 消除手工复制，统一复用 EmptyState) ---
    empty_view = EmptyState(
        icon=ft.Icons.INBOX_OUTLINED,
        title=I18n.get("task_empty_title"),
        message=I18n.get("task_empty_subtitle"),
    )

    # --- Scrollable area ---
    scroll_controls: list[ft.Control]
    if not state.tasks:
        scroll_controls = [empty_view]
    else:
        scroll_controls = [
            _build_task_card(
                row,
                on_cancel=_on_cancel,
                on_retry=_on_retry,
                on_view_details=_on_view_details,
                on_reopen_source=_on_reopen_source,
                now=get_now(),
            )
            for row in page_rows
        ]

    scroll_area = ft.ListView(
        controls=scroll_controls,
        expand=True,
        spacing=0,
        padding=ft.Padding.only(top=8),
    )
    # PR-4 Task 4.1 P2-3: 任务列表区域 anchor 化 (LABEL kind, E2E 探测任务列表存在性)
    scroll_area = anchored(EIDS.TASK_CENTER.TASK_LIST, scroll_area)

    # --- Pagination footer ---
    btn_prev = ft.IconButton(
        icon=ft.Icons.CHEVRON_LEFT,
        tooltip=I18n.get("common_prev_page"),
        on_click=safe_on_click(_on_prev),
        disabled=state.current_page <= 1,
        icon_size=AppStyles.FONT_SIZE_HEADLINE,
    )
    btn_next = ft.IconButton(
        icon=ft.Icons.CHEVRON_RIGHT,
        tooltip=I18n.get("common_next_page"),
        on_click=safe_on_click(_on_next),
        disabled=state.current_page >= state.total_pages,
        icon_size=AppStyles.FONT_SIZE_HEADLINE,
    )
    page_info_text = ft.Text(
        f"{state.current_page} / {state.total_pages}",
        size=AppStyles.FONT_SIZE_BODY,
        color=AppColors.TEXT_SECONDARY,
    )

    pagination_row = ft.Row(
        [btn_prev, page_info_text, btn_next],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=4,
        visible=state.total_pages > 1,
    )

    # --- Phase 6.2 (FR-UX-006): Task details dialog ---
    details_row: TaskRow | None = None
    if details_task_id is not None:
        for r in state.tasks:  # pragma: no cover - 详情对话框查找逻辑仅集成测试覆盖
            if r.id == details_task_id:  # pragma: no cover - 同上
                details_row = r  # pragma: no cover - 同上
                break  # pragma: no cover - 同上

    if details_row is not None:
        details_dialog = ft.AlertDialog(  # pragma: no cover - 详情对话框构建仅集成测试覆盖
            modal=True,
            title=ft.Text(I18n.get("task_details_title"), size=AppStyles.FONT_SIZE_TITLE, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [
                    ft.Text(
                        f"{I18n.get('task_col_name')}: {_render_task_field(details_row.name)}",
                        size=AppStyles.FONT_SIZE_BODY,
                    ),
                    ft.Text(
                        f"{I18n.get('task_col_type')}: {_render_task_field(details_row.task_type)}",
                        size=AppStyles.FONT_SIZE_BODY,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    ft.Text(
                        f"{I18n.get('task_col_status')}: {_get_status_label(details_row.status)}",
                        size=AppStyles.FONT_SIZE_BODY,
                        color=_get_status_color(details_row.status),
                    ),
                    ft.Container(height=4),
                    ft.Text(
                        f"{I18n.get('task_details_error')}:",
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                    ft.Text(
                        details_row.error or I18n.get("task_details_no_error"),
                        size=AppStyles.FONT_SIZE_BODY,
                        color=AppColors.ERROR if details_row.error else AppColors.TEXT_HINT,
                        selectable=True,
                    ),
                ],
                tight=True,
                spacing=6,
                scroll=ft.ScrollMode.AUTO,
            ),
            actions=[
                ft.TextButton(
                    I18n.get("common_close"),
                    on_click=safe_on_click(_on_close_details),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
    else:
        details_dialog = None
    ft.use_dialog(details_dialog)

    # --- MINOR-11: 取消确认对话框 (use_dialog 无条件调用, 以 None/AlertDialog 切换显隐) ---
    # 长任务（如全量历史同步）误点取消代价高，取消前必须二次确认。
    cancel_dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(
                I18n.get("task_cancel_confirm_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
            ),
            content=ft.Text(I18n.get("task_cancel_confirm_content"), size=AppStyles.FONT_SIZE_BODY),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=safe_on_click(_on_dismiss_cancel)),
                ft.TextButton(
                    I18n.get("task_cancel_confirm_ok"),
                    on_click=safe_on_click(_on_confirm_cancel),
                    style=ft.ButtonStyle(color=AppColors.ERROR),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if pending_cancel_id is not None
        else None
    )
    ft.use_dialog(cancel_dialog)

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
