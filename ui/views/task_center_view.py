import logging

import flet as ft

from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)

PAGE_SIZE = 10  # Tasks per page

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
    TaskStatus.INTERRUPTED: "#90A4AE",  # Blue Grey 300
}


def _format_time(dt_obj):
    if not dt_obj:
        return "--:--"
    return dt_obj.strftime("%H:%M:%S")


def _get_status_label(status: TaskStatus) -> str:
    return I18n.get(_STATUS_I18N_MAP.get(status, "task_status_queued"), status.value)


def _get_status_color(status: TaskStatus) -> str:
    return _STATUS_COLOR_MAP.get(status, AppColors.TEXT_SECONDARY)


class TaskCenterView(ft.Container):
    """
    A polished dashboard showing all background operations.
    Card-based layout with status badges, progress bars, and pagination.
    """

    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self.page = page
        self.task_manager = TaskManager()
        self._mounted = False
        self._all_tasks: list[AppTask] = []
        self._locale_subscription_id: object | None = None

        # Pagination state
        self._current_page = 1
        self._total_pages = 1

        self._build_ui()

    def _build_ui(self):
        # --- Header ---
        self.stats_text = ft.Text("", size=13, color=AppColors.TEXT_SECONDARY)

        self.clear_btn = ft.OutlinedButton(
            I18n.get("task_clear_finished"),
            icon=ft.Icons.CLEANING_SERVICES_OUTLINED,
            on_click=self._handle_clear,
            style=ft.ButtonStyle(
                shape=ft.RoundedRectangleBorder(radius=6),
                side=ft.BorderSide(1, AppColors.BORDER),
                padding=ft.padding.symmetric(horizontal=16, vertical=8),
            ),
        )

        self.header_title = ft.Text(
            I18n.get("nav_tasks"),
            size=22,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

        header = ft.Row(
            [
                ft.Icon(ft.Icons.TASK_ALT, color=AppColors.PRIMARY, size=28),
                self.header_title,
                ft.Container(expand=True),
                self.stats_text,
                self.clear_btn,
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # --- Empty state ---
        self.empty_title = ft.Text(
            I18n.get("task_empty_title"),
            size=18,
            weight=ft.FontWeight.W_500,
            color=AppColors.TEXT_SECONDARY,
        )
        self.empty_subtitle = ft.Text(
            I18n.get("task_empty_subtitle"),
            size=13,
            color=AppColors.TEXT_HINT,
            text_align=ft.TextAlign.CENTER,
        )
        self.empty_view = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(
                        ft.Icons.INBOX_OUTLINED,
                        size=64,
                        color=AppColors.TEXT_HINT,
                    ),
                    self.empty_title,
                    self.empty_subtitle,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.alignment.center,
            expand=True,
            padding=ft.padding.only(top=60),
        )

        # --- Scrollable area ---
        self.scroll_area = ft.ListView(
            expand=True,
            spacing=0,
            padding=ft.padding.only(top=8),
        )

        # --- Pagination footer ---
        self.btn_prev = ft.IconButton(
            icon=ft.Icons.CHEVRON_LEFT,
            tooltip=I18n.get("common_prev_page"),
            on_click=self._go_prev,
            disabled=True,
            icon_size=20,
        )
        self.btn_next = ft.IconButton(
            icon=ft.Icons.CHEVRON_RIGHT,
            tooltip=I18n.get("common_next_page"),
            on_click=self._go_next,
            disabled=True,
            icon_size=20,
        )
        self.page_info_text = ft.Text("1 / 1", size=13, color=AppColors.TEXT_SECONDARY)

        self.pagination_row = ft.Row(
            [self.btn_prev, self.page_info_text, self.btn_next],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=4,
        )

        # --- Assembly ---
        self.content = ft.Column(
            [
                header,
                ft.Divider(height=1, color=AppColors.DIVIDER),
                self.scroll_area,
                self.pagination_row,
            ],
            expand=True,
        )
        self.padding = ft.padding.all(20)

    # --- Lifecycle ---

    def did_mount(self):
        self._mounted = True
        self.task_manager.subscribe(self._on_tasks_updated)
        self._refresh_ui(self.task_manager.get_all_tasks())
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale, sync_immediately=False)

    def will_unmount(self):
        self._mounted = False
        self.task_manager.unsubscribe(self._on_tasks_updated)
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作，禁止 IO）。"""
        try:
            self.clear_btn.text = I18n.get("task_clear_finished")
            self.header_title.value = I18n.get("nav_tasks")
            self.empty_title.value = I18n.get("task_empty_title")
            self.empty_subtitle.value = I18n.get("task_empty_subtitle")
            self.btn_prev.tooltip = I18n.get("common_prev_page")
            self.btn_next.tooltip = I18n.get("common_next_page")
            # stats_text 重算
            total = len(self._all_tasks)
            running = sum(1 for t in self._all_tasks if t.status == TaskStatus.RUNNING)
            self.stats_text.value = I18n.get("task_stats_fmt").format(total=total, running=running)
            self._refresh_ui(self._all_tasks)
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[TaskCenterView] refresh_locale error: %s", e, exc_info=True)

    def _on_tasks_updated(self, current_tasks):
        if not self._mounted:
            return
        try:
            if self.page:
                self.page.run_task(self._safe_refresh, current_tasks)
        except Exception as e:
            logger.error(
                "[TaskCenterView] Refresh | ❌ Error scheduling UI update: %s",
                e,
                exc_info=True,
            )

    async def _safe_refresh(self, current_tasks):
        try:
            self._refresh_ui(current_tasks)
        except Exception as e:
            logger.debug("[TaskCenterView] Refresh skipped: %s", e, exc_info=True)

    # --- Pagination ---

    def _compute_pagination(self, total_count: int):
        self._total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
        self._current_page = max(1, min(self._current_page, self._total_pages))

    def _get_page_slice(self, tasks: list[AppTask]) -> list[AppTask]:
        start = (self._current_page - 1) * PAGE_SIZE
        return tasks[start : start + PAGE_SIZE]

    def _update_pagination_controls(self):
        self.btn_prev.disabled = self._current_page <= 1
        self.btn_next.disabled = self._current_page >= self._total_pages
        self.page_info_text.value = f"{self._current_page} / {self._total_pages}"

    def _go_prev(self, e):
        if self._current_page > 1:
            self._current_page -= 1
            self._refresh_ui(self._all_tasks)

    def _go_next(self, e):
        if self._current_page < self._total_pages:
            self._current_page += 1
            self._refresh_ui(self._all_tasks)

    # --- Core rendering ---

    def _refresh_ui(self, tasks: list[AppTask]):
        self._all_tasks = tasks
        total = len(tasks)
        running = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        self.stats_text.value = I18n.get(
            "task_stats_fmt",
        ).format(total=total, running=running)

        # Pagination
        self._compute_pagination(total)
        page_tasks = self._get_page_slice(tasks)
        self._update_pagination_controls()

        # Show/hide pagination
        self.pagination_row.visible = self._total_pages > 1

        # Build task cards or empty state
        if not tasks:
            self.scroll_area.controls = [self.empty_view]
        else:
            cards = [self._build_task_card(t) for t in page_tasks]
            self.scroll_area.controls = cards

        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TaskCenterView] UI update skipped: %s", exc, exc_info=True)

    def _build_task_card(self, t: AppTask) -> ft.Container:
        """Build a single task card with status badge, progress, and actions."""
        status_color = _get_status_color(t.status)
        status_label = _get_status_label(t.status)
        status_icon = _STATUS_ICON_MAP.get(t.status, ft.Icons.HELP_OUTLINE)

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
            border=ft.border.all(1, status_color),
            border_radius=12,
            padding=ft.padding.symmetric(horizontal=10, vertical=3),
        )

        # --- Type chip ---
        type_chip = ft.Container(
            content=ft.Text(t.task_type, size=11, color=AppColors.TEXT_SECONDARY),
            bgcolor=ft.Colors.with_opacity(0.08, AppColors.PRIMARY),
            border_radius=4,
            padding=ft.padding.symmetric(horizontal=8, vertical=2),
        )

        # --- Top row: name + badges ---
        top_row = ft.Row(
            [
                type_chip,
                ft.Text(
                    t.name,
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
        if t.status == TaskStatus.FAILED and t.error:
            desc_text = I18n.get(t.error)
        else:
            desc_text = t.description
        desc_row = ft.Text(
            desc_text or "",
            size=12,
            color=AppColors.TEXT_HINT,
            max_lines=1,
            overflow=ft.TextOverflow.ELLIPSIS,
        )

        # --- Progress bar ---
        if t.status == TaskStatus.RUNNING:
            pct = t.progress * 100
            progress_row = ft.Row(
                [
                    ft.ProgressBar(
                        value=t.progress,
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
        elif t.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ):
            val = 1.0 if t.status == TaskStatus.COMPLETED else t.progress
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
            _format_time(t.created_at),
            size=11,
            color=AppColors.TEXT_HINT,
            italic=True,
        )

        action_btn = ft.Container()
        if t.status in (TaskStatus.RUNNING, TaskStatus.QUEUED) and t.cancellable:
            action_btn = ft.TextButton(
                I18n.get("task_cancel_tooltip"),
                icon=ft.Icons.STOP_CIRCLE_OUTLINED,
                icon_color=AppColors.ERROR,
                style=ft.ButtonStyle(
                    color=AppColors.ERROR,
                    padding=ft.padding.symmetric(horizontal=12, vertical=4),
                    shape=ft.RoundedRectangleBorder(radius=6),
                ),
                on_click=lambda e, tid=t.id: self._handle_cancel(tid),
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
        left_border_color = status_color if t.status == TaskStatus.RUNNING else ft.Colors.TRANSPARENT

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
            border=ft.border.only(  # type: ignore[untyped]
                left=ft.BorderSide(3, left_border_color),
                top=ft.BorderSide(1, AppColors.BORDER),
                right=ft.BorderSide(1, AppColors.BORDER),
                bottom=ft.BorderSide(1, AppColors.BORDER),
            ),
            animate=ft.Animation(200, ft.AnimationCurve.EASE_OUT),
        )
        return card

    # --- Handlers ---

    def _handle_cancel(self, task_id: str):
        UILogger.log_action(
            "TaskCenterView",
            "Click",
            f"btn_cancel | task_id={task_id}",
        )
        self.task_manager.cancel_task(task_id)

    async def _handle_clear(self, e):
        UILogger.log_action("TaskCenterView", "Click", "btn_clear_finished")
        self._current_page = 1
        self.task_manager.clear_finished()
