"""system_tab — 声明式组件 (Phase D.3).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 class → ``@ft.component def SystemTab(show_snack_callback)``
- SystemViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化, 注入 TierApiPanel
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 状态驱动: text/dropdown value 用 ``use_state`` (声明式自动重渲染)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步保存: ``page.run_task`` 调度; R2 CancelledError 不被 ``except Exception`` 捕获
- 移除命令式生命周期回调 / 手动刷新 / page 引用持有 / resize 级联
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.hooks import use_viewmodel
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles, ThemeName
from ui.viewmodels.system_viewmodel import SystemViewModel
from ui.views.settings_tabs.tier_api_panel import TierApiPanel
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _build_language_options() -> list[ft.dropdown.Option]:
    """构建语言选项列表 (locale 变更时由组件重渲染自动刷新)。"""
    return [ft.dropdown.Option(code, name) for code, name in I18n.get_language_options()]


def _build_theme_options() -> list[ft.dropdown.Option]:
    """构建主题选项列表。"""
    return [
        ft.dropdown.Option(ThemeName.DARK, I18n.get("theme_dark")),
        ft.dropdown.Option(ThemeName.LIGHT, I18n.get("theme_light")),
        ft.dropdown.Option(ThemeName.NAVY, I18n.get("theme_navy")),
        ft.dropdown.Option(ThemeName.DRACULA, I18n.get("theme_dracula")),
    ]


def _build_log_level_options() -> list[ft.dropdown.Option]:
    """构建日志级别选项列表。"""
    return [
        ft.dropdown.Option("DEBUG", I18n.get("sys_opt_debug")),
        ft.dropdown.Option("INFO", I18n.get("sys_opt_info")),
        ft.dropdown.Option("WARNING", I18n.get("sys_opt_warn")),
        ft.dropdown.Option("ERROR", I18n.get("sys_opt_error")),
    ]


# ============================================================================
# SystemTab
# ============================================================================


@ft.component
def SystemTab(show_snack_callback: Callable) -> ft.Container:
    """系统核心配置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - SystemViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化, 注入 TierApiPanel
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: text/dropdown value 用 ``use_state``
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, R2 CancelledError 不被 ``except Exception`` 捕获

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(I18n.get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- SystemViewModel for TierApiPanel (internal mode, hook persists VM) ---
    _system_state, system_vm = use_viewmodel(factory=lambda: SystemViewModel())

    # --- Pure UI state (ConfigHandler 读取初始值, use_state 持久化) ---
    language_value, set_language_value = ft.use_state(ConfigHandler.get_locale())
    theme_value, set_theme_value = ft.use_state(ConfigHandler.get_theme_name())
    concurrency_value, set_concurrency_value = ft.use_state(str(ConfigHandler.get_sync_max_concurrent_heavy()))
    log_level_value, set_log_level_value = ft.use_state(ConfigHandler.get_log_level())
    pool_size_value, set_pool_size_value = ft.use_state(str(ConfigHandler.get_db_connection_pool_size()))
    db_overflow_value, set_db_overflow_value = ft.use_state(str(ConfigHandler.get_db_max_overflow()))
    db_timeout_value, set_db_timeout_value = ft.use_state(str(ConfigHandler.get_db_pool_timeout()))
    io_workers_value, set_io_workers_value = ft.use_state(str(ConfigHandler.get_max_io_workers()))
    cpu_workers_value, set_cpu_workers_value = ft.use_state(str(ConfigHandler.get_max_cpu_workers()))
    no_proxy_value, set_no_proxy_value = ft.use_state(",".join(ConfigHandler.get_no_proxy_domains()))
    diagnostics_exporting, set_diagnostics_exporting = ft.use_state(False)

    # --- Async handlers (R2: except Exception 不捕获 CancelledError) ---
    async def _do_language_change(new_locale: str) -> None:
        try:
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_locale, new_locale)
            if not success:
                set_language_value(I18n.current_locale())
                if show_snack_callback:
                    show_snack_callback(I18n.get("settings_language_save_failed"), color=AppColors.ERROR)
                return
            I18n.set_locale(new_locale)
            page = _get_page()
            if page is not None and getattr(page, "locale_configuration", None):
                try:
                    normalized = I18n.current_locale()
                    parts = normalized.split("_")
                    lang = parts[0]
                    country = parts[1] if len(parts) > 1 else None
                    page.locale_configuration.current_locale = ft.Locale(lang, country)
                except Exception as ex:
                    logger.debug(
                        "[SystemTab] Failed to update page locale configuration: %s",
                        ex,
                        exc_info=True,
                    )
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_language_changed"))
        except Exception as ex:
            logger.error("[SystemTab] Language | Change failed: %s", DataSanitizer.sanitize_error(ex))
            logger.debug("[SystemTab] Language | Change failed traceback", exc_info=True)
            if show_snack_callback:
                show_snack_callback(DataSanitizer.sanitize_error(ex), color=AppColors.ERROR)

    async def _do_theme_change(new_theme: str) -> None:
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_theme_name, new_theme)
            page = _get_page()
            if page is not None:
                from ui.theme import apply_page_theme

                apply_page_theme(page, new_theme)  # type: ignore[untyped]
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_theme_updated"))
        except Exception as ex:
            logger.error("[SystemTab] Theme | Change failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_log_level_change(new_level: str) -> None:
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_log_level, new_level)
            from utils.logger import update_log_level

            update_log_level(new_level)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_log_label") + ": " + new_level)
        except Exception as ex:
            logger.error("[SystemTab] LogLevel | Change failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_concurrency(raw_val: str) -> None:
        try:
            val = int(raw_val)
            if val < 1 or val > 32:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_concurrency_range"), color=AppColors.ERROR)
                return
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_sync_max_concurrent_heavy, val)
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("sys_sync_heavy") + " " + I18n.get("common_saved"),
                    color=AppColors.SUCCESS,
                )
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            logger.error("[SystemTab] Concurrency | Save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_db_pool(pool_size_str: str, max_overflow_str: str, timeout_str: str) -> None:
        try:
            pool_size = int(pool_size_str)
            max_overflow = int(max_overflow_str)
            timeout = int(timeout_str)
            if pool_size < 1 or pool_size > 50:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_pool_range"), color=AppColors.ERROR)
                return
            if max_overflow < 0 or max_overflow > 50:
                if show_snack_callback:
                    show_snack_callback(
                        I18n.get("settings_db_overflow") + ": 0-50",
                        color=AppColors.ERROR,
                    )
                return
            if timeout < 5 or timeout > 300:
                if show_snack_callback:
                    show_snack_callback(
                        I18n.get("settings_db_timeout") + ": 5-300",
                        color=AppColors.ERROR,
                    )
                return

            def _save_db_pool_sync() -> None:
                ConfigHandler.set_db_connection_pool_size(pool_size)
                ConfigHandler.set_db_max_overflow(max_overflow)
                ConfigHandler.set_db_pool_timeout(timeout)

            await ThreadPoolManager().run_async(TaskType.IO, _save_db_pool_sync)
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_db_pool_saved"), color=AppColors.SUCCESS)
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            logger.error("[SystemTab] DBPool | Save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_thread_pool(io_str: str, cpu_str: str) -> None:
        try:
            if not io_str or not cpu_str:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_threads_empty"), color=AppColors.ERROR)
                return
            io_val = int(io_str)
            cpu_val = int(cpu_str)
            if io_val < 4 or io_val > 512:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_io_range"), color=AppColors.ERROR)
                return
            if cpu_val < 1 or cpu_val > 64:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_cpu_range"), color=AppColors.ERROR)
                return

            def _save_thread_pool_sync() -> None:
                ConfigHandler.set_max_io_workers(io_val)
                ConfigHandler.set_max_cpu_workers(cpu_val)

            await ThreadPoolManager().run_async(TaskType.IO, _save_thread_pool_sync)
            if show_snack_callback:
                show_snack_callback(I18n.get("common_preparing"))
            await asyncio.to_thread(ThreadPoolManager().reload_config)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_pool_saved"), color=AppColors.SUCCESS)
            logger.info("Updated ThreadPool: IO=%s, CPU=%s", io_val, cpu_val)
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except Exception as ex:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
            logger.error("[SystemTab] ThreadPool | Save failed: %s", ex, exc_info=True)

    async def _do_save_no_proxy(raw_text: str) -> None:
        try:
            if not raw_text:
                domains: list[str] = []
            else:
                domains = [d.strip() for d in raw_text.split(",") if d.strip()]
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_no_proxy_domains, domains)
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_no_proxy_saved"), color=AppColors.SUCCESS)
            logger.info("No-Proxy domains updated: %s", domains)
            from utils.proxy_manager import ProxyManager

            ThreadPoolManager().submit(TaskType.IO, ProxyManager.reapply_proxy_policy)
        except Exception as ex:
            logger.error("[SystemTab] No-proxy domains save failed: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_export_diagnostics() -> None:
        UILogger.log_action("SystemTab", "Click", "export_diagnostics")
        set_diagnostics_exporting(True)
        try:
            from utils.diagnostics import SystemDiagnosticsCollector

            zip_path = await SystemDiagnosticsCollector.export()
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("settings_diagnostics_success").format(path=zip_path),
                    color=AppColors.SUCCESS,
                )
        except Exception as ex:
            logger.error(
                "[SystemTab] Diagnostics | Export failed: %s",
                DataSanitizer.sanitize_error(ex),
            )
            logger.debug("[SystemTab] Diagnostics | Export failed traceback", exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_diagnostics_failed"), color=AppColors.ERROR)
        finally:
            set_diagnostics_exporting(False)

    # --- Event handlers (乐观更新 + 后台保存) ---
    def _on_language_change(e: ft.ControlEvent) -> None:
        new_locale = e.control.value if e and e.control else None
        if not new_locale:
            return
        set_language_value(new_locale)
        UILogger.log_action("SystemTab", "Select", f"language={new_locale}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_language_change, new_locale)

    def _on_theme_change(e: ft.ControlEvent) -> None:
        new_theme = e.control.value if e and e.control else None
        if not new_theme:
            return
        set_theme_value(new_theme)
        UILogger.log_action("SystemTab", "Select", f"theme={new_theme}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_theme_change, new_theme)

    def _on_log_level_change(e: ft.ControlEvent) -> None:
        new_level = e.control.value if e and e.control else None
        if not new_level:
            return
        set_log_level_value(new_level)
        UILogger.log_action("SystemTab", "Select", f"log_level={new_level}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_log_level_change, new_level)

    def _on_save_concurrency(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_concurrency, concurrency_value)

    def _on_save_db_pool(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_db_pool, pool_size_value, db_overflow_value, db_timeout_value)

    def _on_save_thread_pool(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_thread_pool, io_workers_value.strip(), cpu_workers_value.strip())

    def _on_save_no_proxy(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_no_proxy, no_proxy_value)

    def _on_export_diagnostics(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_export_diagnostics)

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    section_header = SectionHeader(I18n.get("sys_core_config"), title_key="sys_core_config")

    language_dropdown = ft.Dropdown(
        label=I18n.get_language_label(),
        value=language_value,
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=14,
        border_radius=8,
        content_padding=10,
        options=_build_language_options(),
        on_select=_on_language_change,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    theme_dropdown = ft.Dropdown(
        label=I18n.get("settings_theme"),
        value=theme_value,
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=14,
        border_radius=8,
        content_padding=10,
        options=_build_theme_options(),
        on_select=_on_theme_change,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    concurrency_input = ft.TextField(
        label=I18n.get("settings_concurrency"),
        value=concurrency_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        border_radius=8,
        on_change=lambda e: set_concurrency_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    log_level_dropdown = ft.Dropdown(
        label=I18n.get("settings_log_level"),
        value=log_level_value,
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=14,
        border_radius=8,
        content_padding=10,
        options=_build_log_level_options(),
        on_select=_on_log_level_change,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    pool_size_input = ft.TextField(
        label=I18n.get("settings_db_pool"),
        value=pool_size_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_items"),
        border_radius=8,
        on_change=lambda e: set_pool_size_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    db_overflow_input = ft.TextField(
        label=I18n.get("settings_db_overflow"),
        value=db_overflow_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_items"),
        border_radius=8,
        on_change=lambda e: set_db_overflow_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    db_timeout_input = ft.TextField(
        label=I18n.get("settings_db_timeout"),
        value=db_timeout_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_seconds"),
        border_radius=8,
        on_change=lambda e: set_db_timeout_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    io_workers_input = ft.TextField(
        label=I18n.get("sys_pool_io"),
        value=io_workers_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        border_radius=8,
        on_change=lambda e: set_io_workers_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    cpu_workers_input = ft.TextField(
        label=I18n.get("sys_pool_cpu"),
        value=cpu_workers_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=14,
        content_padding=10,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        border_radius=8,
        on_change=lambda e: set_cpu_workers_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    no_proxy_input = ft.TextField(
        value=no_proxy_value,
        expand=True,
        text_size=14,
        content_padding=10,
        hint_text=I18n.get("settings_no_proxy_hint"),
        border_radius=8,
        multiline=False,
        on_change=lambda e: set_no_proxy_value(e.control.value),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border_color=AppColors.INPUT_BORDER,
    )

    diagnostics_button = ft.Button(
        content=(
            I18n.get("settings_diagnostics_exporting")
            if diagnostics_exporting
            else I18n.get("settings_diagnostics_btn")
        ),
        icon=ft.Icons.DOWNLOAD_ROUNDED,
        on_click=_on_export_diagnostics,
        disabled=diagnostics_exporting,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    save_config_tip = I18n.get("settings_save_config")

    # --- SettingRows ---
    row_language = SettingRow(
        icon=ft.Icons.LANGUAGE_ROUNDED,
        icon_color=ft.Colors.BLUE,
        title=I18n.get("settings_language"),
        subtitle=I18n.get("settings_language_desc"),
        control=language_dropdown,
        title_key="settings_language",
        subtitle_key="settings_language_desc",
    )

    row_theme = SettingRow(
        icon=ft.Icons.COLOR_LENS_ROUNDED,
        icon_color=ft.Colors.PURPLE,
        title=I18n.get("settings_theme"),
        subtitle=I18n.get("settings_snack_theme_updated"),
        control=theme_dropdown,
        title_key="settings_theme",
        subtitle_key="settings_snack_theme_updated",
    )

    row_log = SettingRow(
        icon=ft.Icons.BUG_REPORT_ROUNDED,
        icon_color=AppColors.PRIMARY,
        title=I18n.get("settings_log_level"),
        subtitle=I18n.get("sys_log_label"),
        control=log_level_dropdown,
        title_key="settings_log_level",
        subtitle_key="sys_log_label",
    )

    save_concurrency_btn = ft.IconButton(
        icon=ft.Icons.SAVE_ROUNDED,
        icon_color=AppColors.PRIMARY,
        tooltip=save_config_tip,
        on_click=_on_save_concurrency,
    )
    row_concurrency = SettingRow(
        icon=ft.Icons.SPEED_ROUNDED,
        icon_color=AppColors.ACCENT,
        title=I18n.get("sys_sync_heavy"),
        subtitle=I18n.get("sys_sync_heavy_hint"),
        control=ft.Row(
            [concurrency_input, save_concurrency_btn],
            spacing=5,
        ),
        title_key="sys_sync_heavy",
        subtitle_key="sys_sync_heavy_hint",
    )

    save_thread_pool_btn = ft.IconButton(
        icon=ft.Icons.SAVE_ROUNDED,
        icon_color=AppColors.PRIMARY,
        tooltip=save_config_tip,
        on_click=_on_save_thread_pool,
    )
    row_thread_pool = SettingRow(
        icon=ft.Icons.MEMORY_ROUNDED,
        icon_color=ft.Colors.INDIGO,
        title=I18n.get("sys_thread_pool_title"),
        subtitle=I18n.get("sys_thread_pool_desc"),
        control=ft.Row(
            [io_workers_input, cpu_workers_input, save_thread_pool_btn],
            spacing=5,
            wrap=True,
        ),
        title_key="sys_thread_pool_title",
        subtitle_key="sys_thread_pool_desc",
    )

    save_db_pool_btn = ft.IconButton(
        icon=ft.Icons.SAVE_ROUNDED,
        icon_color=AppColors.PRIMARY,
        tooltip=save_config_tip,
        on_click=_on_save_db_pool,
    )
    row_db_pool = SettingRow(
        icon=ft.Icons.STORAGE_ROUNDED,
        icon_color=ft.Colors.ORANGE,
        title=I18n.get("settings_db_pool"),
        subtitle=I18n.get("settings_pool_desc"),
        control=ft.Row(
            [pool_size_input, db_overflow_input, db_timeout_input, save_db_pool_btn],
            spacing=5,
            wrap=True,
        ),
        title_key="settings_db_pool",
        subtitle_key="settings_pool_desc",
    )

    save_no_proxy_btn = ft.IconButton(
        icon=ft.Icons.SAVE_ROUNDED,
        icon_color=AppColors.PRIMARY,
        tooltip=I18n.get("common_save"),
        on_click=_on_save_no_proxy,
    )
    row_proxy = SettingRow(
        icon=ft.Icons.PUBLIC_OFF_ROUNDED,
        icon_color=ft.Colors.TEAL,
        title=I18n.get("settings_no_proxy_domains"),
        subtitle=I18n.get("settings_no_proxy_desc"),
        control=ft.Row(
            [no_proxy_input, save_no_proxy_btn],
            spacing=5,
            expand=True,
        ),
        title_key="settings_no_proxy_domains",
        subtitle_key="settings_no_proxy_desc",
    )

    row_diagnostics = SettingRow(
        icon=ft.Icons.ANALYTICS_ROUNDED,
        icon_color=ft.Colors.RED,
        title=I18n.get("settings_diagnostics"),
        subtitle=I18n.get("settings_diagnostics_desc"),
        control=diagnostics_button,
        title_key="settings_diagnostics",
        subtitle_key="settings_diagnostics_desc",
    )

    # TierApiPanel 消费 system_vm (props 推送, 函数调用)
    tier_panel = TierApiPanel(system_vm)

    return ft.Container(
        content=ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column(
                        [
                            section_header,
                            ft.Container(height=10),
                            row_language,
                            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                            row_theme,
                            ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                            row_log,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            row_concurrency,
                            ft.Container(height=10),
                            row_thread_pool,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            row_db_pool,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            tier_panel,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            row_proxy,
                            ft.Divider(
                                height=20,
                                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
                            ),
                            row_diagnostics,
                        ],
                        spacing=10,
                    ),
                ),
            ],
            padding=ft.Padding.only(bottom=50),
        ),
    )
