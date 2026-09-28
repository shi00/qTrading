"""system_tab — 声明式组件 (Phase D.3 + Task 5.2 Settings VM 下沉).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 class → ``@ft.component def SystemTab(show_snack_callback)``
- SystemViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化, 注入 TierApiPanel
- SystemSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2)
  收敛 ConfigHandler/ThreadPoolManager 业务编排 (语言/主题/线程池/DB pool/proxy)
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 状态驱动: text/dropdown value 用 ``use_state`` (声明式自动重渲染)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步保存: ``page.run_task`` 调度; R2 CancelledError 显式 raise
- View 不再直接写 ConfigHandler / 直接编排 ThreadPoolManager (Task 5.2 DoD)
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from config import USER_DATA_ROOT
from ui.components.confirm_dialog import ConfirmDialog
from ui.components.flet_type_helpers import (
    get_control_value,
    safe_icon_str,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.safe_wrap_row import SafeWrapRow
from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles, ThemeName
from ui.viewmodels.system_settings_view_model import (
    CONCURRENCY_MAX,
    CONCURRENCY_MIN,
    CPU_WORKERS_MAX,
    CPU_WORKERS_MIN,
    DB_OVERFLOW_MAX,
    DB_OVERFLOW_MIN,
    DB_POOL_MAX,
    DB_POOL_MIN,
    DB_TIMEOUT_MAX,
    DB_TIMEOUT_MIN,
    IO_WORKERS_MAX,
    IO_WORKERS_MIN,
    SystemSettingsViewModel,
)
from ui.viewmodels.system_viewmodel import SystemViewModel
from ui.views.settings_tabs.tier_api_panel import TierApiPanel
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer

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


def _validate_int_field(raw: str | None, min_val: int, max_val: int) -> str | None:
    """校验整数字段, 返回错误 i18n key; 合法返回 None (MAJOR-08).

    不修改用户输入 —— 空值/非数字/越界分别返回对应错误 key, 由调用方在字段上
    展示 ``error``, 保留原始输入 (UIX 最佳实践: 不静默修正用户输入)。
    """
    stripped = (raw or "").strip()
    if not stripped:
        return "sys_field_required"
    try:
        value = int(stripped)
    except (ValueError, TypeError):
        return "sys_snack_num_fmt"
    if not (min_val <= value <= max_val):
        return "sys_err_out_of_range_fmt"
    return None


def _format_field_error(err_key: str | None, min_val: int, max_val: int) -> str | None:
    """将错误 i18n key 渲染为字段 error 文案 (无错误返回 None)。"""
    if err_key is None:
        return None
    return I18n.get(err_key, min=min_val, max=max_val)


def _build_legacy_key_warning() -> ft.Control:
    """构建 legacy 明文密钥文件过渡期安全告警（F3, 检视 06）。

    当仍在使用 .secret.key 家族明文密钥文件时，在核心配置页顶部展示提示，
    引导用户迁移凭证到 keyring / 环境变量。仅在有 legacy 密钥文件时调用。
    """
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.SECURITY_ROUNDED, color=AppColors.WARNING),
                ft.Column(
                    [
                        ft.Text(
                            I18n.get("sys_legacy_key_warning_title"),
                            size=AppStyles.FONT_SIZE_TITLE,
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.WARNING,
                        ),
                        ft.Text(
                            I18n.get("sys_legacy_key_warning_desc"),
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.INPUT_TEXT,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                ),
            ],
            spacing=8,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, AppColors.WARNING),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.4, AppColors.WARNING)),
        border_radius=8,
        padding=10,
    )


def _build_storage_security_notice() -> ft.Control:
    """构建本地数据存储安全提示卡片（SEC-05, 检视 06）。

    告知用户应用数据存储位置及未加密状态，建议启用操作系统磁盘加密。
    内置 PostgreSQL 存有选股历史/复盘/AI 分析结果，静态未加密是合理的
    工程取舍（本地桌面应用、性能、恢复复杂度），但用户有权知道数据
    处于什么保护状态（检视报告 §1.5）。
    """
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.INFO_ROUNDED, color=AppColors.INFO),
                ft.Column(
                    [
                        ft.Text(
                            I18n.get("sys_storage_security_title"),
                            size=AppStyles.FONT_SIZE_TITLE,
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.INFO,
                        ),
                        ft.Text(
                            I18n.get("sys_storage_security_desc", path=USER_DATA_ROOT),
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.INPUT_TEXT,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                ),
            ],
            spacing=8,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, AppColors.INFO),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.4, AppColors.INFO)),
        border_radius=8,
        padding=10,
    )


def _build_keyring_unavailable_warning() -> ft.Control:
    """构建 keyring 后端不可用安全告警（F4, 检视 06）。

    当系统 keyring 不可用时，凭证会静默降级写入 AES 加密配置文件（较低安全
    级别）。此提示条告知用户该降级已发生，并推荐改用环境变量保存凭证。
    仅当 keyring 不可用时调用。
    """
    return ft.Container(
        content=ft.Row(
            [
                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED, color=AppColors.WARNING),
                ft.Column(
                    [
                        ft.Text(
                            I18n.get("sys_keyring_unavailable_title"),
                            size=AppStyles.FONT_SIZE_TITLE,
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.WARNING,
                        ),
                        ft.Text(
                            I18n.get("sys_keyring_unavailable_desc"),
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.INPUT_TEXT,
                        ),
                    ],
                    spacing=4,
                    expand=True,
                ),
            ],
            spacing=8,
        ),
        bgcolor=ft.Colors.with_opacity(0.12, AppColors.WARNING),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.4, AppColors.WARNING)),
        border_radius=8,
        padding=10,
    )


# ============================================================================
# SystemTab
# ============================================================================


@ft.component
def SystemTab(show_snack_callback: Callable) -> ft.Container:
    """系统核心配置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - SystemViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化, 注入 TierApiPanel
    - SystemSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (Task 5.2),
      收敛 ConfigHandler/ThreadPoolManager 业务编排
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - 状态驱动: text/dropdown value 用 ``use_state``
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, R2 CancelledError 显式 raise
    - View 不再直接写 ConfigHandler / 编排 ThreadPoolManager (Task 5.2 DoD)

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- SystemViewModel for TierApiPanel (internal mode, hook persists VM) ---
    _system_state, system_vm = use_viewmodel(factory=lambda: SystemViewModel())

    # --- SystemSettingsViewModel for ConfigHandler-driven settings (Task 5.2) ---
    settings_state, settings_vm = use_viewmodel(factory=lambda: SystemSettingsViewModel())

    # --- Pure UI state (diagnostics_exporting 为纯 UI state, 不下沉 VM) ---
    # 其余字段直接消费 settings_state.* (VM 单一真值源, set_* 触发 _notify → 重渲染)
    diagnostics_exporting, set_diagnostics_exporting = ft.use_state(False)
    # P3-17: 语言切换 loading 态 (纯 UI state, VM 不感知 locale 故不下沉 VM)
    is_changing_language, set_is_changing_language = ft.use_state(False)
    # MAJOR-08: 字段级校验错误 (key = 字段 label i18n key)。校验失败时在字段下方
    # 显示 error 且保留用户输入 (不清空/不回滚)。
    field_errors, set_field_errors = ft.use_state({})
    # MAJOR-08: 线程池保存前的运行任务确认对话框开关。
    thread_pool_confirm_open, set_thread_pool_confirm_open = ft.use_state(False)
    # MAJOR-08 回归修复: 高级 (开发者) 分组的展开态必须由 use_state 持有。
    # VM 通知 (如切换日志级别) 触发的重渲染会重建 ExpansionTile, 若把展开态写死
    # 则每次重渲染都塌回折叠态; 折叠态下子控件不渲染 (maintain_state=False),
    # 导致组内 anchor 语义节点消失, E2E 定位失败且用户操作被打断。
    advanced_expanded, set_advanced_expanded = ft.use_state(False)

    def _set_field_error(error_key: str, err_key: str | None) -> None:
        """设置/清除某字段的校验错误 (仅在变化时 set_state, 避免多余重渲染)。"""
        if err_key is None:
            if error_key in field_errors:
                set_field_errors({k: v for k, v in field_errors.items() if k != error_key})
        elif field_errors.get(error_key) != err_key:
            set_field_errors({**field_errors, error_key: err_key})

    def _make_change_handler(error_key: str, setter: Callable) -> Callable:
        """构造 on_change 处理器: 写入 VM state 并清除该字段错误标记。"""

        def _handler(e: ft.ControlEvent) -> None:
            setter(get_control_value(e.control, ft.TextField) or "")
            _set_field_error(error_key, None)

        return _handler

    # --- Async handlers (R2: CancelledError 显式 raise; 调用 VM commands) ---
    async def _do_language_change(new_locale: str) -> None:
        set_is_changing_language(True)  # P3-17: 切换期间 Dropdown disabled + ProgressRing
        try:
            success = await settings_vm.save_language(new_locale)
            if not success:
                settings_vm.set_language_value(I18n.current_locale())
                if show_snack_callback:
                    show_snack_callback(I18n.get("settings_language_save_failed"), color=AppColors.ERROR)
                return
            I18n.set_locale(new_locale)
            page = _get_page()
            if page is not None:
                locale_config = page.locale_configuration
                if locale_config is not None:
                    try:
                        normalized = I18n.current_locale()
                        parts = normalized.split("_")
                        lang = parts[0]
                        country = parts[1] if len(parts) > 1 else None
                        locale_config.current_locale = ft.Locale(lang, country)
                    except Exception as ex:
                        logger.debug(
                            "[SystemTab] Failed to update page locale configuration: %s",
                            DataSanitizer.sanitize_error(ex),
                            exc_info=True,
                        )
            if show_snack_callback:
                # P3-17: 文案更新 — 提示部分模块需重启后完全生效
                show_snack_callback(I18n.get("settings_language_changed"))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemTab] Language | Change failed: %s", DataSanitizer.sanitize_error(ex))
            logger.debug("[SystemTab] Language | Change failed traceback", exc_info=True)
            if show_snack_callback:
                show_snack_callback(DataSanitizer.sanitize_error(ex), color=AppColors.ERROR)
        finally:
            set_is_changing_language(False)  # P3-17

    async def _do_theme_change(new_theme: str) -> None:
        try:
            success = await settings_vm.save_theme(new_theme)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            page = _get_page()
            if page is not None:
                from ui.theme import apply_page_theme

                apply_page_theme(page, new_theme)  # type: ignore[untyped]  # [reason: apply_page_theme 为动态挂载函数, 无类型存根]
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_theme_updated"))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemTab] Theme | Change failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_log_level_change(new_level: str) -> None:
        try:
            success = await settings_vm.save_log_level(new_level)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_log_label") + ": " + new_level)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemTab] LogLevel | Change failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_concurrency(raw_val: str) -> None:
        # P2-13: raw_val 来自 VM state (set_*_value 已 clamp), View 不再硬编码范围检查;
        # VM save_* 保留范围检查作为兜底 (场景遗漏 33a).
        try:
            int(raw_val)  # 仅校验数字格式, 范围由 VM clamp + save 兜底
            success = await settings_vm.save_concurrency(raw_val)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("sys_sync_heavy") + " " + I18n.get("common_saved"),
                    color=AppColors.SUCCESS,
                )
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemTab] Concurrency | Save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_db_pool(pool_size_str: str, max_overflow_str: str, timeout_str: str) -> None:
        # P2-13: 参数来自 VM state (set_*_value 已 clamp), View 不再硬编码范围检查;
        # VM save_db_pool 保留范围检查作为兜底 (场景遗漏 33a).
        try:
            int(pool_size_str)
            int(max_overflow_str)
            int(timeout_str)
            success = await settings_vm.save_db_pool(pool_size_str, max_overflow_str, timeout_str)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_db_pool_saved"), color=AppColors.SUCCESS)
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[SystemTab] DBPool | Save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_save_thread_pool(io_str: str, cpu_str: str) -> None:
        # P2-13: 参数来自 VM state (set_*_value 已 clamp), View 不再硬编码范围检查;
        # VM save_thread_pool 保留范围检查作为兜底 (场景遗漏 33a).
        try:
            if not io_str or not cpu_str:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_threads_empty"), color=AppColors.ERROR)
                return
            int(io_str)
            int(cpu_str)
            if show_snack_callback:
                show_snack_callback(I18n.get("common_preparing"))
            success = await settings_vm.save_thread_pool(io_str, cpu_str)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_pool_saved"), color=AppColors.SUCCESS)
        except ValueError:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_num_fmt"), color=AppColors.ERROR)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
            logger.error("[SystemTab] ThreadPool | Save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True)

    async def _do_save_no_proxy(raw_text: str) -> None:
        try:
            success = await settings_vm.save_no_proxy(raw_text)
            if not success:
                if show_snack_callback:
                    show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)
                return
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_snack_no_proxy_saved"), color=AppColors.SUCCESS)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error(
                "[SystemTab] No-proxy domains save failed: %s", DataSanitizer.sanitize_error(ex), exc_info=True
            )
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
        except asyncio.CancelledError:
            raise  # R2: 必须传播
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
        new_locale = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        if not new_locale:
            return
        settings_vm.set_language_value(new_locale)
        UILogger.log_action("SystemTab", "Select", f"language={new_locale}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_language_change, new_locale)

    def _on_theme_change(e: ft.ControlEvent) -> None:
        new_theme = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        if not new_theme:
            return
        settings_vm.set_theme_value(new_theme)
        UILogger.log_action("SystemTab", "Select", f"theme={new_theme}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_theme_change, new_theme)

    def _on_log_level_change(e: ft.ControlEvent) -> None:
        new_level = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        if not new_level:
            return
        settings_vm.set_log_level_value(new_level)
        UILogger.log_action("SystemTab", "Select", f"log_level={new_level}")
        page = _get_page()
        if page is not None:
            page.run_task(_do_log_level_change, new_level)

    # --- Field commit handlers (MAJOR-08: on_blur/on_submit 校验通过即保存) ---
    def _commit_concurrency() -> None:
        raw = settings_state.concurrency_value
        err = _validate_int_field(raw, CONCURRENCY_MIN, CONCURRENCY_MAX)
        _set_field_error("settings_concurrency", err)
        if err is not None:
            return
        clean = raw.strip()
        if not settings_vm.should_dispatch_save("concurrency", clean):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_concurrency, clean)

    def _commit_db_pool() -> None:
        pool_raw = settings_state.pool_size_value
        overflow_raw = settings_state.db_overflow_value
        timeout_raw = settings_state.db_timeout_value
        pool_err = _validate_int_field(pool_raw, DB_POOL_MIN, DB_POOL_MAX)
        overflow_err = _validate_int_field(overflow_raw, DB_OVERFLOW_MIN, DB_OVERFLOW_MAX)
        timeout_err = _validate_int_field(timeout_raw, DB_TIMEOUT_MIN, DB_TIMEOUT_MAX)
        _set_field_error("settings_db_pool", pool_err)
        _set_field_error("settings_db_overflow", overflow_err)
        _set_field_error("settings_db_timeout", timeout_err)
        if pool_err is not None or overflow_err is not None or timeout_err is not None:
            return
        signature = f"{pool_raw.strip()}|{overflow_raw.strip()}|{timeout_raw.strip()}"
        if not settings_vm.should_dispatch_save("db_pool", signature):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_db_pool, pool_raw.strip(), overflow_raw.strip(), timeout_raw.strip())

    def _dispatch_thread_pool_save(io_val: str, cpu_val: str) -> None:
        if not settings_vm.should_dispatch_save("thread_pool", f"{io_val}|{cpu_val}"):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_thread_pool, io_val, cpu_val)

    def _commit_thread_pool() -> None:
        io_raw = settings_state.io_workers_value
        cpu_raw = settings_state.cpu_workers_value
        io_err = _validate_int_field(io_raw, IO_WORKERS_MIN, IO_WORKERS_MAX)
        cpu_err = _validate_int_field(cpu_raw, CPU_WORKERS_MIN, CPU_WORKERS_MAX)
        _set_field_error("sys_pool_io", io_err)
        _set_field_error("sys_pool_cpu", cpu_err)
        if io_err is not None or cpu_err is not None:
            return
        # MAJOR-08: 保存线程池会重建线程池并中断排队任务, 存在运行中任务时先弹确认。
        if settings_vm.has_running_tasks():
            set_thread_pool_confirm_open(True)
            return
        _dispatch_thread_pool_save(io_raw.strip(), cpu_raw.strip())

    def _commit_no_proxy() -> None:
        raw = settings_state.no_proxy_value
        if not settings_vm.should_dispatch_save("no_proxy", raw.strip()):
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_no_proxy, raw)

    def _on_save_concurrency(e: ft.ControlEvent) -> None:
        _commit_concurrency()

    def _on_save_db_pool(e: ft.ControlEvent) -> None:
        _commit_db_pool()

    def _on_save_thread_pool(e: ft.ControlEvent) -> None:
        _commit_thread_pool()

    def _on_save_no_proxy(e: ft.ControlEvent) -> None:
        _commit_no_proxy()

    def _on_confirm_thread_pool_save() -> None:
        """确认对话框确认回调: 关闭对话框并派发线程池保存。"""
        set_thread_pool_confirm_open(False)
        _dispatch_thread_pool_save(
            settings_state.io_workers_value.strip(),
            settings_state.cpu_workers_value.strip(),
        )

    def _on_cancel_thread_pool_save() -> None:
        set_thread_pool_confirm_open(False)

    def _on_export_diagnostics(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_export_diagnostics)

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    section_header = SectionHeader(I18n.get("sys_core_config"), title_key="sys_core_config")

    lang_opts = _build_language_options()
    lang_lbl = I18n.get_language_label()
    language_dropdown = anchored(
        EIDS.SETTINGS.LANGUAGE_DROPDOWN,
        ft.Dropdown(
            label=lang_lbl,
            value=settings_state.language_value,
            width=AppStyles.calc_dropdown_width(lang_opts, label=lang_lbl),
            text_size=AppStyles.FONT_SIZE_LG,
            border_radius=8,
            content_padding=AppStyles.SPACING_SM,
            options=lang_opts,
            on_select=safe_on_select(_on_language_change),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
            disabled=is_changing_language,  # P3-17: 切换期间禁用, 防重复提交
        ),
    )
    # P3-17: 切换期间显示 ProgressRing (复用 backtest loading 范式)
    language_control: ft.Control = ft.Row(
        [
            language_dropdown,
            ft.ProgressRing(
                width=20,
                height=20,
                stroke_width=2,
                color=AppColors.PRIMARY,
                visible=is_changing_language,
            ),
        ],
        spacing=8,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    theme_opts = _build_theme_options()
    theme_lbl = I18n.get("settings_theme")
    theme_dropdown = anchored(
        EIDS.SETTINGS.THEME_DROPDOWN,
        ft.Dropdown(
            label=theme_lbl,
            value=settings_state.theme_value,
            width=AppStyles.calc_dropdown_width(theme_opts, label=theme_lbl),
            text_size=AppStyles.FONT_SIZE_LG,
            border_radius=8,
            content_padding=AppStyles.SPACING_SM,
            options=theme_opts,
            on_select=safe_on_select(_on_theme_change),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
        ),
    )

    concurrency_input = ft.TextField(
        label=I18n.get("settings_concurrency"),
        value=settings_state.concurrency_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        # P2-13: hint_text 提示范围 (VM 公开别名, 消除硬编码双重定义)
        hint_text=I18n.get("sys_hint_range_fmt", min=CONCURRENCY_MIN, max=CONCURRENCY_MAX),
        border_radius=8,
        on_change=_make_change_handler("settings_concurrency", settings_vm.set_concurrency_value),
        # UX-09 (P2-04): 单行主表单 Enter 提交 = 行内保存主动作 (与鼠标点击等价)
        on_submit=safe_on_change(_on_save_concurrency),
        # MAJOR-08: 失焦校验通过即保存 (与行内保存按钮统一为失焦提交语义)
        on_blur=safe_on_change(_on_save_concurrency),
        error=_format_field_error(field_errors.get("settings_concurrency"), CONCURRENCY_MIN, CONCURRENCY_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    log_opts = _build_log_level_options()
    log_lbl = I18n.get("settings_log_level")
    log_level_dropdown = anchored(
        EIDS.SETTINGS.LOG_LEVEL_DROPDOWN,
        ft.Dropdown(
            label=log_lbl,
            value=settings_state.log_level_value,
            width=AppStyles.calc_dropdown_width(log_opts, label=log_lbl),
            text_size=AppStyles.FONT_SIZE_LG,
            border_radius=8,
            content_padding=AppStyles.SPACING_SM,
            options=log_opts,
            on_select=safe_on_select(_on_log_level_change),
            bgcolor=AppColors.INPUT_BG,
            color=AppColors.INPUT_TEXT,
            border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
        ),
    )

    pool_size_input = ft.TextField(
        label=I18n.get("settings_db_pool"),
        value=settings_state.pool_size_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_items"),
        hint_text=I18n.get("sys_hint_range_fmt", min=DB_POOL_MIN, max=DB_POOL_MAX),  # P2-13
        border_radius=8,
        on_change=_make_change_handler("settings_db_pool", settings_vm.set_pool_size_value),
        on_submit=safe_on_change(_on_save_db_pool),  # UX-09: Enter = 组保存主动作
        on_blur=safe_on_change(_on_save_db_pool),  # MAJOR-08: 失焦校验通过即保存
        error=_format_field_error(field_errors.get("settings_db_pool"), DB_POOL_MIN, DB_POOL_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    db_overflow_input = ft.TextField(
        label=I18n.get("settings_db_overflow"),
        value=settings_state.db_overflow_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_items"),
        hint_text=I18n.get("sys_hint_range_fmt", min=DB_OVERFLOW_MIN, max=DB_OVERFLOW_MAX),  # P2-13
        border_radius=8,
        on_change=_make_change_handler("settings_db_overflow", settings_vm.set_db_overflow_value),
        on_submit=safe_on_change(_on_save_db_pool),  # UX-09: Enter = 组保存主动作
        on_blur=safe_on_change(_on_save_db_pool),  # MAJOR-08: 失焦校验通过即保存
        error=_format_field_error(field_errors.get("settings_db_overflow"), DB_OVERFLOW_MIN, DB_OVERFLOW_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    db_timeout_input = ft.TextField(
        label=I18n.get("settings_db_timeout"),
        value=settings_state.db_timeout_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("common_seconds"),
        hint_text=I18n.get("sys_hint_range_fmt", min=DB_TIMEOUT_MIN, max=DB_TIMEOUT_MAX),  # P2-13
        border_radius=8,
        on_change=_make_change_handler("settings_db_timeout", settings_vm.set_db_timeout_value),
        on_submit=safe_on_change(_on_save_db_pool),  # UX-09: Enter = 组保存主动作
        on_blur=safe_on_change(_on_save_db_pool),  # MAJOR-08: 失焦校验通过即保存
        error=_format_field_error(field_errors.get("settings_db_timeout"), DB_TIMEOUT_MIN, DB_TIMEOUT_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    io_workers_input = ft.TextField(
        label=I18n.get("sys_pool_io"),
        value=settings_state.io_workers_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        hint_text=I18n.get("sys_hint_range_fmt", min=IO_WORKERS_MIN, max=IO_WORKERS_MAX),  # P2-13
        border_radius=8,
        on_change=_make_change_handler("sys_pool_io", settings_vm.set_io_workers_value),
        on_submit=safe_on_change(_on_save_thread_pool),  # UX-09: Enter = 组保存主动作
        on_blur=safe_on_change(_on_save_thread_pool),  # MAJOR-08: 失焦校验通过即保存
        error=_format_field_error(field_errors.get("sys_pool_io"), IO_WORKERS_MIN, IO_WORKERS_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    cpu_workers_input = ft.TextField(
        label=I18n.get("sys_pool_cpu"),
        value=settings_state.cpu_workers_value,
        width=AppStyles.CONTROL_WIDTH_SM,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        keyboard_type=ft.KeyboardType.NUMBER,
        input_filter=ft.InputFilter(allow=True, regex_string=r"[0-9]"),
        suffix=I18n.get("sys_suffix_threads"),
        hint_text=I18n.get("sys_hint_range_fmt", min=CPU_WORKERS_MIN, max=CPU_WORKERS_MAX),  # P2-13
        border_radius=8,
        on_change=_make_change_handler("sys_pool_cpu", settings_vm.set_cpu_workers_value),
        on_submit=safe_on_change(_on_save_thread_pool),  # UX-09: Enter = 组保存主动作
        on_blur=safe_on_change(_on_save_thread_pool),  # MAJOR-08: 失焦校验通过即保存
        error=_format_field_error(field_errors.get("sys_pool_cpu"), CPU_WORKERS_MIN, CPU_WORKERS_MAX),
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    no_proxy_input = ft.TextField(
        value=settings_state.no_proxy_value,
        expand=True,
        text_size=AppStyles.FONT_SIZE_LG,
        content_padding=AppStyles.SPACING_SM,
        hint_text=I18n.get("settings_no_proxy_hint"),
        border_radius=8,
        multiline=False,
        on_change=_make_change_handler("settings_no_proxy_domains", settings_vm.set_no_proxy_value),
        on_submit=safe_on_change(_on_save_no_proxy),  # UX-09: Enter = 保存主动作
        on_blur=safe_on_change(_on_save_no_proxy),  # MAJOR-08: 失焦校验通过即保存
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    diagnostics_button = ft.Button(
        content=(
            I18n.get("settings_diagnostics_exporting")
            if diagnostics_exporting
            else I18n.get("settings_diagnostics_btn")
        ),
        icon=ft.Icons.DOWNLOAD_ROUNDED,
        on_click=safe_on_click(_on_export_diagnostics),
        disabled=diagnostics_exporting,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
    )

    # --- SettingRows ---
    # MAJOR-08: 数值/文本字段统一"失焦(on_blur)校验通过即保存", 移除行内保存
    # IconButton (与语言/主题/日志级别下拉的即时保存语义统一)。
    row_language = SettingRow(
        icon=safe_icon_str(ft.Icons.LANGUAGE_ROUNDED),
        icon_color=AppColors.PRIMARY,
        title=I18n.get("settings_language"),
        subtitle=I18n.get("settings_language_desc"),
        control=language_control,  # P3-17: Row(dropdown + ProgressRing)
        title_key="settings_language",
        subtitle_key="settings_language_desc",
    )

    row_theme = SettingRow(
        icon=safe_icon_str(ft.Icons.COLOR_LENS_ROUNDED),
        icon_color=AppColors.TERTIARY,
        title=I18n.get("settings_theme"),
        subtitle=I18n.get("settings_theme_desc"),
        control=theme_dropdown,
        title_key="settings_theme",
        subtitle_key="settings_theme_desc",
    )

    row_log = SettingRow(
        icon=safe_icon_str(ft.Icons.BUG_REPORT_ROUNDED),
        icon_color=AppColors.PRIMARY,
        title=I18n.get("settings_log_level"),
        subtitle=I18n.get("sys_log_label"),
        control=log_level_dropdown,
        title_key="settings_log_level",
        subtitle_key="sys_log_label",
    )

    row_concurrency = SettingRow(
        icon=safe_icon_str(ft.Icons.SPEED_ROUNDED),
        icon_color=AppColors.ACCENT,
        title=I18n.get("sys_sync_heavy"),
        subtitle=I18n.get("sys_sync_heavy_hint"),
        control=ft.Row(
            [concurrency_input],
            spacing=5,
        ),
        title_key="sys_sync_heavy",
        subtitle_key="sys_sync_heavy_hint",
    )

    row_thread_pool = SettingRow(
        icon=safe_icon_str(ft.Icons.MEMORY_ROUNDED),
        icon_color=AppColors.PRIMARY_DARK,
        title=I18n.get("sys_thread_pool_title"),
        subtitle=I18n.get("sys_thread_pool_desc"),
        control=ft.Row(
            [io_workers_input, cpu_workers_input],
            spacing=5,
            wrap=True,
        ),
        title_key="sys_thread_pool_title",
        subtitle_key="sys_thread_pool_desc",
    )

    row_db_pool = SettingRow(
        icon=safe_icon_str(ft.Icons.STORAGE_ROUNDED),
        icon_color=AppColors.WARNING,
        title=I18n.get("settings_db_pool"),
        subtitle=I18n.get("settings_pool_desc"),
        control=SafeWrapRow(
            [pool_size_input, db_overflow_input, db_timeout_input],
            spacing=5,
        ),
        title_key="settings_db_pool",
        subtitle_key="settings_pool_desc",
    )

    row_proxy = SettingRow(
        icon=safe_icon_str(ft.Icons.PUBLIC_OFF_ROUNDED),
        icon_color=AppColors.ACCENT,
        title=I18n.get("settings_no_proxy_domains"),
        subtitle=I18n.get("settings_no_proxy_desc"),
        control=ft.Row(
            [no_proxy_input],
            spacing=5,
            expand=True,
        ),
        title_key="settings_no_proxy_domains",
        subtitle_key="settings_no_proxy_desc",
    )

    row_diagnostics = SettingRow(
        icon=safe_icon_str(ft.Icons.ANALYTICS_ROUNDED),
        icon_color=AppColors.ERROR,
        title=I18n.get("settings_diagnostics"),
        subtitle=I18n.get("settings_diagnostics_desc"),
        control=diagnostics_button,
        title_key="settings_diagnostics",
        subtitle_key="settings_diagnostics_desc",
    )

    # TierApiPanel 消费 system_vm (props 推送, 函数调用)
    tier_panel = TierApiPanel(system_vm)

    # --- MAJOR-08: 高级 (开发者) 分组 — 默认折叠 ---
    # 连接池 / 线程池 / 日志级别 / 代理白名单等技术参数默认折叠, 组内附
    # 「重启或重载生效 / 调整会中断排队任务」说明, 降低普通用户误操作风险。
    advanced_title = ft.Text(
        I18n.get("sys_advanced_group_title"),
        size=AppStyles.FONT_SIZE_LG,
        weight=ft.FontWeight.BOLD,
    )
    advanced_subtitle = ft.Text(
        I18n.get("sys_advanced_group_note"),
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.WARNING,
    )
    advanced_group = ft.ExpansionTile(
        title=advanced_title,
        subtitle=advanced_subtitle,
        controls=[
            ft.Container(height=10),
            row_log,
            ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
            row_concurrency,
            ft.Container(height=10),
            row_thread_pool,
            ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
            row_db_pool,
            ft.Divider(height=20, color=ft.Colors.with_opacity(0.5, AppColors.BORDER)),
            row_proxy,
        ],
        # 受控展开态: 由 use_state 驱动, 重渲染后保持用户展开/折叠选择。
        expanded=advanced_expanded,
        on_change=lambda e: set_advanced_expanded(bool(e.data)),
    )

    # F3（检视 06）：过渡期安全告警——仍在使用 legacy 明文密钥文件时，
    # 在核心配置页顶部插入提示条。
    core_config_controls: list[ft.Control] = [section_header]
    if settings_state.using_legacy_key:
        core_config_controls.extend(
            [
                ft.Container(height=10),
                _build_legacy_key_warning(),
            ]
        )
    # F4（检视 06）：keyring 后端不可用告警——凭证将降级到 AES 加密配置
    # （较低安全级别），提示改为环境变量方式保存凭证。
    if not settings_state.keyring_available:
        core_config_controls.extend(
            [
                ft.Container(height=10),
                _build_keyring_unavailable_warning(),
            ]
        )
    core_config_controls.extend(
        [
            row_language,
            ft.Divider(height=10, color=AppColors.TRANSPARENT),
            row_theme,
            ft.Divider(
                height=20,
                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
            ),
            tier_panel,
            ft.Divider(
                height=20,
                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
            ),
            # MAJOR-08: 技术参数 (日志级别/并发/线程池/连接池/代理白名单) 收进
            # 默认折叠的高级分组。
            advanced_group,
            ft.Divider(
                height=20,
                color=ft.Colors.with_opacity(0.5, AppColors.BORDER),
            ),
            row_diagnostics,
            # SEC-05（检视 06）：本地数据存储安全提示——数据未静态加密，
            # 置于内容后部而非顶部。置于顶部会持久占据初始视口，把语言/主题/日志
            # 下拉区挤出视口，导致 CanvasKit 语义节点几何为 0、E2E anchor 定位失败
            # （PR #915 回归）。后置仍满足「告知用户数据保护状态」，且不挤压核心操作区。
            ft.Container(height=10),
            _build_storage_security_notice(),
            # MAJOR-08: 保存线程池前, 存在运行中/排队任务时的确认对话框
            # (复用 ConfirmDialog, open_state 由 thread_pool_confirm_open 驱动)。
            ConfirmDialog(
                open_state=thread_pool_confirm_open,
                title=I18n.get("sys_thread_pool_confirm_title"),
                body=I18n.get("sys_thread_pool_confirm_body"),
                on_confirm=_on_confirm_thread_pool_save,
                on_cancel=_on_cancel_thread_pool_save,
                confirm_text=I18n.get("common_confirm"),
                cancel_text=I18n.get("common_cancel"),
            ),
        ]
    )

    return ft.Container(
        content=ft.ListView(
            controls=[
                DashboardCard(
                    content=ft.Column(core_config_controls, spacing=10),
                ),
            ],
            padding=ft.Padding.only(bottom=50),
        ),
    )
