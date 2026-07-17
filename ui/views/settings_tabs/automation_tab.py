"""automation_tab — 声明式组件 (Phase D.4 + Task 5.2 Settings VM 下沉).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 2 个命令式容器子类 → 2 个 ``@ft.component`` 函数组件
  (AutomationTab / NotificationsTab)
- AutomationSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2)
  收敛 ConfigHandler/ThreadPoolManager 业务编排 (计划任务/AI 概念任务/新闻提醒)
- 移除命令式生命周期回调 / 手动刷新 / 手动重渲染 / page 引用持有
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 状态驱动: switch/dropdown value 用 ``use_state`` (声明式自动重渲染)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步任务: ``page.run_task`` 调度; R2 CancelledError 显式 raise
- View 不再直接写 ConfigHandler / 编排 ThreadPoolManager (Task 5.2 DoD)
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from ui.components.settings_widgets import DashboardCard, SettingRow
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.automation_settings_view_model import AutomationSettingsViewModel

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_FONT_SIZE_TITLE = 16
_FONT_SIZE_BODY = 14
_FONT_SIZE_SMALL = 12
_FONT_SIZE_HINT = 11
_ICON_SIZE_SMALL = 16
_DROPDOWN_WIDTH = AppStyles.CONTROL_WIDTH_MD
_SPACING_DEFAULT = 20
_SPACING_SMALL = 10


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _build_time_options() -> list[ft.dropdown.Option]:
    """构建时间选项列表"""
    return [
        ft.dropdown.Option("15:30", I18n.get("settings_opt_1530")),
        ft.dropdown.Option("16:00", I18n.get("settings_opt_1600")),
        ft.dropdown.Option("16:30", I18n.get("settings_opt_1630")),
        ft.dropdown.Option("17:00", I18n.get("settings_opt_1700")),
        ft.dropdown.Option("18:00", I18n.get("settings_opt_1800")),
        ft.dropdown.Option("20:00", I18n.get("settings_opt_2000")),
    ]


def _build_search_engine_options() -> list[ft.dropdown.Option]:
    """构建搜索引擎选项列表"""
    return [
        ft.dropdown.Option("search_std", I18n.get("settings_ai_concept_search_std")),
        ft.dropdown.Option("search_pro", I18n.get("settings_ai_concept_search_pro")),
    ]


def _build_interval_options() -> list[ft.dropdown.Option]:
    """构建新闻拉取间隔选项列表"""
    return [
        ft.dropdown.Option("30", I18n.get("settings_news_interval_30s")),
        ft.dropdown.Option("60", I18n.get("settings_news_interval_60s")),
        ft.dropdown.Option("300", I18n.get("settings_news_interval_5m")),
        ft.dropdown.Option("900", I18n.get("settings_news_interval_15m")),
    ]


def _get_schedule_status_text(enabled: bool) -> str:
    return I18n.get("settings_status_auto_on") if enabled else I18n.get("settings_status_auto_off")


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


# ============================================================================
# AutomationTab
# ============================================================================


@ft.component
def AutomationTab(show_snack_callback: Callable) -> ft.Container:
    """自动化任务设置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - AutomationSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2),
      收敛 ConfigHandler/ThreadPoolManager 业务编排 (计划任务/AI 概念任务)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: switch/dropdown value 用 ``use_state`` (声明式自动重渲染)
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, 失败时回滚 state
    - View 不再直接写 ConfigHandler / 编排 ThreadPoolManager (Task 5.2 DoD)

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- AutomationSettingsViewModel (内部模式: hook 实例化 + dispose on unmount) ---
    settings_state, settings_vm = use_viewmodel(factory=lambda: AutomationSettingsViewModel())

    # --- 状态直接消费 settings_state.* (VM 单一真值源, set_* 触发 _notify → 重渲染) ---

    # --- Async save handlers (R2: CancelledError 显式 raise; 调用 VM commands) ---
    async def _do_schedule_toggle(new_enabled: bool) -> None:
        try:
            success = await settings_vm.save_auto_update_enabled(new_enabled)
            if not success:
                settings_vm.set_auto_enabled(not new_enabled)
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("settings_snack_auto_on") if new_enabled else I18n.get("settings_snack_auto_off"),
                )
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[AutomationTab] schedule toggle save failed: %s", ex, exc_info=True)
            settings_vm.set_auto_enabled(not new_enabled)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_schedule_time_change(new_time: str) -> None:
        try:
            success = await settings_vm.save_auto_update_time(new_time)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_time_set").format(time=new_time))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[AutomationTab] schedule time save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_ai_concept_toggle(new_enabled: bool) -> None:
        try:
            success = await settings_vm.save_ai_concept_enabled(new_enabled)
            if not success:
                settings_vm.set_ai_enabled(not new_enabled)
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("settings_snack_auto_on") if new_enabled else I18n.get("settings_snack_auto_off"),
                )
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[AutomationTab] ai concept toggle save failed: %s", ex, exc_info=True)
            settings_vm.set_ai_enabled(not new_enabled)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_ai_concept_time_change(new_time: str) -> None:
        try:
            success = await settings_vm.save_ai_concept_time(new_time)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_time_set").format(time=new_time))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[AutomationTab] ai concept time save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_ai_concept_engine_change(new_engine: str) -> None:
        try:
            success = await settings_vm.save_ai_concept_engine(new_engine)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("common_saved"))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[AutomationTab] ai concept search engine save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    # --- Event handlers (乐观更新 + 后台保存) ---
    def _on_schedule_toggle(e: ft.ControlEvent) -> None:
        new_enabled = e.control.value
        settings_vm.set_auto_enabled(new_enabled)
        page = _get_page()
        if page is not None:
            page.run_task(_do_schedule_toggle, new_enabled)

    def _on_schedule_time_change(e: ft.ControlEvent) -> None:
        new_time = e.control.value
        settings_vm.set_auto_time(new_time)
        page = _get_page()
        if page is not None:
            page.run_task(_do_schedule_time_change, new_time)

    def _on_ai_concept_toggle(e: ft.ControlEvent) -> None:
        new_enabled = e.control.value
        settings_vm.set_ai_enabled(new_enabled)
        page = _get_page()
        if page is not None:
            page.run_task(_do_ai_concept_toggle, new_enabled)

    def _on_ai_concept_time_change(e: ft.ControlEvent) -> None:
        new_time = e.control.value
        settings_vm.set_ai_time(new_time)
        page = _get_page()
        if page is not None:
            page.run_task(_do_ai_concept_time_change, new_time)

    def _on_ai_concept_engine_change(e: ft.ControlEvent) -> None:
        new_engine = e.control.value
        settings_vm.set_ai_engine(new_engine)
        page = _get_page()
        if page is not None:
            page.run_task(_do_ai_concept_engine_change, new_engine)

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    schedule_status_color = AppColors.SUCCESS if settings_state.auto_enabled else AppColors.TEXT_HINT
    ai_status_color = AppColors.SUCCESS if settings_state.ai_enabled else AppColors.TEXT_HINT

    schedule_enabled_switch = ft.Switch(
        label=I18n.get("settings_auto_update"),
        value=settings_state.auto_enabled,
        on_change=_on_schedule_toggle,
    )
    schedule_time_dropdown = ft.Dropdown(
        label=I18n.get("settings_update_time"),
        width=_DROPDOWN_WIDTH,
        value=settings_state.auto_time,
        options=_build_time_options(),
        on_select=_on_schedule_time_change,
        disabled=not settings_state.auto_enabled,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    schedule_status = ft.Text(
        _get_schedule_status_text(settings_state.auto_enabled),
        size=_FONT_SIZE_SMALL,
        color=schedule_status_color,
    )

    ai_concept_enabled_switch = ft.Switch(
        label=I18n.get("settings_ai_concept_update"),
        value=settings_state.ai_enabled,
        on_change=_on_ai_concept_toggle,
    )
    ai_concept_time_dropdown = ft.Dropdown(
        label=I18n.get("settings_update_time"),
        width=_DROPDOWN_WIDTH,
        value=settings_state.ai_time,
        options=_build_time_options(),
        on_select=_on_ai_concept_time_change,
        disabled=not settings_state.ai_enabled,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )
    ai_concept_status = ft.Text(
        _get_schedule_status_text(settings_state.ai_enabled),
        size=_FONT_SIZE_SMALL,
        color=ai_status_color,
    )
    ai_concept_engine_dropdown = ft.Dropdown(
        label=I18n.get("settings_ai_concept_search_engine"),
        width=_DROPDOWN_WIDTH,
        value=settings_state.ai_engine,
        options=_build_search_engine_options(),
        on_select=_on_ai_concept_engine_change,
        disabled=not settings_state.ai_enabled,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    # --- SettingRows ---
    row_schedule = SettingRow(
        icon=ft.Icons.SCHEDULE,
        title=I18n.get("settings_auto_update"),
        subtitle=I18n.get("settings_auto_desc"),
        control=schedule_enabled_switch,
        icon_color=AppColors.PRIMARY,
        title_key="settings_auto_update",
        subtitle_key="settings_auto_desc",
    )
    row_time = SettingRow(
        icon=ft.Icons.ACCESS_TIME,
        title=I18n.get("settings_update_time"),
        subtitle=I18n.get("settings_trading_days"),
        control=schedule_time_dropdown,
        icon_color=AppColors.ACCENT,
        title_key="settings_update_time",
        subtitle_key="settings_trading_days",
    )
    card_main = DashboardCard(
        content=ft.Column(
            [
                row_schedule,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                row_time,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.INFO_OUTLINE,
                            size=_ICON_SIZE_SMALL,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        schedule_status,
                    ],
                ),
            ],
        ),
    )

    row_ai_schedule = SettingRow(
        icon=ft.Icons.AUTO_AWESOME,
        title=I18n.get("settings_ai_concept_update"),
        subtitle=I18n.get("settings_ai_concept_desc"),
        control=ai_concept_enabled_switch,
        icon_color=AppColors.PRIMARY,
        title_key="settings_ai_concept_update",
        subtitle_key="settings_ai_concept_desc",
    )
    row_ai_time = SettingRow(
        icon=ft.Icons.ACCESS_TIME,
        title=I18n.get("settings_update_time"),
        subtitle=I18n.get("settings_saturdays"),
        control=ai_concept_time_dropdown,
        icon_color=AppColors.ACCENT,
        title_key="settings_update_time",
        subtitle_key="settings_saturdays",
    )
    row_ai_engine = SettingRow(
        icon=ft.Icons.MANAGE_SEARCH,
        title=I18n.get("settings_ai_concept_search_engine"),
        subtitle=I18n.get("settings_ai_concept_search_engine_desc"),
        control=ai_concept_engine_dropdown,
        icon_color=AppColors.ACCENT,
        title_key="settings_ai_concept_search_engine",
        subtitle_key="settings_ai_concept_search_engine_desc",
    )
    card_ai = DashboardCard(
        content=ft.Column(
            [
                row_ai_schedule,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                row_ai_time,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                row_ai_engine,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.INFO_OUTLINE,
                            size=_ICON_SIZE_SMALL,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ai_concept_status,
                    ],
                ),
            ],
        ),
    )

    txt_title = ft.Text(
        I18n.get("settings_auto_update"),
        size=_FONT_SIZE_TITLE,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )
    txt_desc = ft.Text(
        I18n.get("settings_auto_desc"),
        size=_FONT_SIZE_BODY,
        color=AppColors.TEXT_SECONDARY,
    )
    txt_hint = ft.Text(
        I18n.get("settings_hint_bg_run"),
        size=_FONT_SIZE_HINT,
        color=AppColors.TEXT_HINT,
    )

    return ft.Container(
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            controls=[
                txt_title,
                txt_desc,
                ft.Container(height=_SPACING_SMALL),
                card_main,
                card_ai,
                txt_hint,
            ],
            spacing=_SPACING_DEFAULT,
        ),
        **AppStyles.card(),
    )


# ============================================================================
# NotificationsTab
# ============================================================================


@ft.component
def NotificationsTab(show_snack_callback: Callable) -> ft.Container:
    """通知设置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - AutomationSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2),
      收敛 ConfigHandler/ThreadPoolManager 业务编排 (新闻提醒)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: switch/dropdown value 用 ``use_state``
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, 失败时回滚 state
    - View 不再直接写 ConfigHandler / 编排 ThreadPoolManager (Task 5.2 DoD)

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- AutomationSettingsViewModel (内部模式: hook 实例化 + dispose on unmount) ---
    settings_state, settings_vm = use_viewmodel(factory=lambda: AutomationSettingsViewModel())

    # --- 状态直接消费 settings_state.* (VM 单一真值源, set_* 触发 _notify → 重渲染) ---

    # --- Async save handlers (R2: CancelledError 显式 raise; 调用 VM commands) ---
    async def _do_news_toggle(new_enabled: bool) -> None:
        try:
            success = await settings_vm.save_news_enabled(new_enabled)
            if not success:
                settings_vm.set_news_enabled(not new_enabled)
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if new_enabled:
                if show_snack_callback:
                    show_snack_callback(I18n.get("settings_snack_news_on"))
            elif show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_news_off"))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[NotificationsTab] news toggle save failed: %s", ex, exc_info=True)
            settings_vm.set_news_enabled(not new_enabled)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_interval_change(new_val: str) -> None:
        try:
            success = await settings_vm.save_news_interval(new_val)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
                return
            val = int(new_val)
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_interval_set").format(interval=val))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[NotificationsTab] interval save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    # --- Event handlers (乐观更新 + 后台保存) ---
    def _on_news_toggle(e: ft.ControlEvent) -> None:
        new_enabled = e.control.value
        settings_vm.set_news_enabled(new_enabled)
        page = _get_page()
        if page is not None:
            page.run_task(_do_news_toggle, new_enabled)

    def _on_interval_change(e: ft.ControlEvent) -> None:
        new_val = e.control.value
        settings_vm.set_news_interval(new_val)
        page = _get_page()
        if page is not None:
            page.run_task(_do_interval_change, new_val)

    # --- Build controls (状态驱动) ---
    news_switch = ft.Switch(
        label=I18n.get("settings_news_alerts"),
        value=settings_state.news_enabled,
        on_change=_on_news_toggle,
    )
    interval_dropdown = ft.Dropdown(
        label=I18n.get("settings_news_interval"),
        width=AppStyles.CONTROL_WIDTH_MD,
        value=settings_state.news_interval,
        options=_build_interval_options(),
        on_select=_on_interval_change,
        disabled=not settings_state.news_enabled,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    # --- SettingRows ---
    row_alerts = SettingRow(
        icon=ft.Icons.NOTIFICATIONS_ACTIVE,
        title=I18n.get("settings_news_alerts"),
        subtitle=I18n.get("settings_notify_desc"),
        control=news_switch,
        icon_color=AppColors.WARNING,
        title_key="settings_news_alerts",
        subtitle_key="settings_notify_desc",
    )
    row_interval = SettingRow(
        icon=ft.Icons.TIMER,
        title=I18n.get("settings_news_interval"),
        subtitle=I18n.get("settings_news_interval_desc"),
        control=interval_dropdown,
        icon_color=AppColors.INFO,
        title_key="settings_news_interval",
        subtitle_key="settings_news_interval_desc",
    )
    card_notify = DashboardCard(
        content=ft.Column(
            [
                row_alerts,
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                row_interval,
            ],
        ),
    )

    txt_title = ft.Text(
        I18n.get("settings_notify_title"),
        size=_FONT_SIZE_TITLE,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )
    txt_desc = ft.Text(
        I18n.get("settings_notify_desc"),
        size=_FONT_SIZE_BODY,
        color=AppColors.TEXT_SECONDARY,
    )

    return ft.Container(
        content=ft.Column(
            scroll=ft.ScrollMode.AUTO,
            controls=[
                txt_title,
                ft.Container(height=_SPACING_SMALL),
                card_notify,
                txt_desc,
            ],
            spacing=_SPACING_DEFAULT,
        ),
        **AppStyles.card(),
    )
