"""data_source_tab — 声明式组件 (Phase E.2).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 class → ``@ft.component def DataSourceTab(show_snack_callback)``
- DataSourceViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (hook 订阅 + dispose)
- TushareConfigPanelViewModel 外部实例化 (消费方需持有引用调 commands), 通过
  ``use_viewmodel(vm=)`` 订阅 state 变化
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
- 移除全部 11 个 ``_on_vm_*`` dispatch 方法 (VM subscribe 自动重渲染替代)
- 移除所有命令式 API: did_mount/will_unmount/refresh_locale/update_theme/handle_resize/
  _safe_update/.update()/PageRefMixin/_page_ref/weakref
- L771 合规: state 字段全部用 frozen dataclass / Message (VM 直接暴露),
  View 渲染直接从 state 读取, 无 dual-track (移除 use_state 快照 + use_effect 拉取)
- snack 瞬态通知用 ``use_effect`` 监听 ``state.snack.seq`` 触发 show_snack_callback
- cache_cleared 瞬态信号用 ``use_effect`` 监听 ``state.cache_cleared_version`` 广播 PubSub
- TaskManager 订阅下沉到 VM (Phase 3.1: VM 构造时订阅, dispose 时取消; View 不感知)
- AlertDialog 用 ``use_state(open)`` + ``ft.use_dialog()`` 条件渲染
- 异步任务用 ``page.run_task``, R2 CancelledError 必须 raise (不被 except Exception 捕获)
"""

import asyncio
import logging
import typing
from collections.abc import Callable

import flet as ft

from services.task_manager import TaskStatus
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel
from ui.components.flet_type_helpers import (
    get_control_value,
    safe_icon_str,
    safe_on_click,
    safe_on_select,
)
from ui.components.health_report_dialog import HealthReportDialog, HealthScanDialog
from ui.components.settings_widgets import (
    ActionChip,
    DashboardCard,
    MetricCard,
    SectionHeader,
    SettingRow,
)
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.pubsub_topics import CACHE_CLEARED_TOPIC
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.data_source_view_model import DataSourceViewModel, HealthResultRow
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel
from utils.correlation import ensure_correlation_id
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


def _build_history_years_options() -> list[ft.dropdown.Option]:
    """构建历史数据年限选项列表 (locale 变更时由组件重渲染自动刷新)。"""
    return [
        ft.dropdown.Option("1", f"1 {I18n.get('unit_year')}".strip()),
        ft.dropdown.Option("2", f"2 {I18n.get('unit_years')}".strip()),
        ft.dropdown.Option("3", f"3 {I18n.get('unit_years')}".strip()),
        ft.dropdown.Option("4", f"4 {I18n.get('unit_years')}".strip()),
        ft.dropdown.Option("5", f"5 {I18n.get('unit_years')}".strip()),
    ]


def _render_message(msg: Message | None) -> str:
    """渲染 Message 到本地化文本。"""
    if msg is None:
        return ""
    return I18n.get(msg.key, **msg.params)


def _resolve_snack_color(color_name: str) -> str:
    """Map snack color_name to AppColors constant."""
    color_map = {
        "success": AppColors.SUCCESS,
        "warning": AppColors.WARNING,
        "error": AppColors.ERROR,
        "info": AppColors.INFO,
    }
    return color_map.get(color_name, AppColors.INFO)


def _build_health_summary_content(result: HealthResultRow) -> ft.Control:
    """从健康检查结果构建摘要内容 (纯函数, 供 DataSourceTab 渲染调用).

    L771 合规: 接收 frozen dataclass (HealthResultRow), 非 dict.
    """
    cov_val = result.details_financial_coverage
    cov_str = f"{cov_val:.1f}%"

    miss_critical = result.details_missing_critical
    miss_depth = result.details_missing_depth
    miss_breadth = result.details_missing_breadth
    lag = result.market_lag_days
    sys_text = I18n.get("ds_health_summary_sys").format(cov=cov_str, lag=lag)

    if miss_critical > 0:
        core_text = I18n.get("ds_health_summary_core").format(miss=miss_critical)
        core_color, core_icon = AppColors.ERROR, ft.Icons.WARNING_AMBER_ROUNDED
    else:
        core_text = I18n.get("ds_health_summary_core_ok")
        core_color, core_icon = AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE_OUTLINE

    integrity_items: list[ft.Control] = [
        ft.Icon(core_icon, size=14, color=core_color),
        ft.Text(core_text, size=12, color=core_color),
    ]
    if miss_depth > 0:
        integrity_items.extend(
            [
                ft.Text("|", size=12, color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("ds_health_summary_depth").format(miss=miss_depth),
                    size=12,
                    color=AppColors.WARNING,
                ),
            ]
        )
    if miss_breadth > 0:
        integrity_items.extend(
            [
                ft.Text("|", size=12, color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("ds_health_summary_breadth").format(miss=miss_breadth),
                    size=12,
                    color=AppColors.WARNING,
                ),
            ]
        )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.ANALYTICS, size=14, color=AppColors.INFO),
                    ft.Text(sys_text, size=12, color=AppColors.TEXT_PRIMARY),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.START,
            ),
            ft.Row(integrity_items, spacing=5, alignment=ft.MainAxisAlignment.START, wrap=True),
        ],
        spacing=6,
    )


# Health status key → (icon, color) mapping
_HEALTH_STATUS_VISUALS: dict[str, tuple[ft.IconData, str]] = {
    "ds_health_ok": (ft.Icons.CHECK_CIRCLE, AppColors.SUCCESS),
    "ds_health_lag": (ft.Icons.WARNING, AppColors.WARNING),
    "ds_health_error": (ft.Icons.ERROR, AppColors.ERROR),
    "ds_health_cancelled": (ft.Icons.CANCEL_OUTLINED, AppColors.WARNING),
    "common_check_fail": (ft.Icons.ERROR, AppColors.ERROR),
}


# ============================================================================
# DataSourceTab
# ============================================================================


@ft.component
def DataSourceTab(show_snack_callback: Callable) -> ft.Container:
    """数据源配置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - DataSourceViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化
    - TushareConfigPanelViewModel 外部实例化, ``use_viewmodel(vm=)`` 订阅
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - L771 合规: 渲染直接从 state 读取 (health_result/health_error/snack),
      无 dual-track use_state 快照 + use_effect 拉取
    - snack 瞬态通知: ``use_effect`` 监听 ``state.snack.seq`` 触发 show_snack_callback
    - cache_cleared 瞬态信号: ``use_effect`` 监听 ``state.cache_cleared_version`` 广播 PubSub
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步任务: ``page.run_task`` 调度, R2 CancelledError 不被 ``except Exception`` 捕获

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- DataSourceViewModel (内部模式: hook 实例化 + 卸载时 dispose) ---
    state, vm = use_viewmodel(factory=lambda: DataSourceViewModel())

    # --- TushareConfigPanelViewModel (外部模式: 消费方持有引用调 commands) ---
    # NOTE(lazy): tushare_vm 通过 use_ref 持久化, 不使用 use_viewmodel 内部模式,
    # 因为 on_verify_success/on_save 回调需要捕获 show_snack_callback 和 vm 引用。
    # ceiling: use_viewmodel 支持外部 VM 模式. upgrade: 无 (本模式即目标范式).
    def _create_tushare_vm() -> TushareConfigPanelViewModel:
        def _on_verify_success(token: str) -> None:
            if show_snack_callback:
                show_snack_callback(
                    I18n.get("settings_snack_token_verified"),
                    color=AppColors.SUCCESS,
                )

        def _on_save(config: dict) -> None:
            token = config.get("token", "").strip()
            if not token:
                return
            page = _get_page()
            if page is not None:
                page.run_task(_do_tushare_save, token)

        return TushareConfigPanelViewModel(
            on_verify_success=_on_verify_success,
            on_save=_on_save,
            show_internal_loading=True,
        )

    tushare_vm_ref = ft.use_ref(_create_tushare_vm)
    tushare_vm = tushare_vm_ref.current
    assert tushare_vm is not None
    # 外部 VM 模式订阅 state 变化 (hook 仅订阅, 不 dispose; tushare_vm 生命周期由本组件管理)
    _tushare_state, _ = use_viewmodel(vm=tushare_vm)

    # --- Pure UI state (仅 dialog 开关/配置, 业务数据从 state 直接派生, 无 dual-track) ---
    # health_report_data: _do_show_health_report 通过 vm.get_health_report() 拉取的报告原始 dict,
    #   非业务状态 dual-track, 而是按需加载的 dialog 数据 (HealthReportDialog 接收 dict)
    health_report_data, set_health_report_data = ft.use_state({})
    health_report_open, set_health_report_open = ft.use_state(False)
    scan_dialog_open, set_scan_dialog_open = ft.use_state(False)
    # confirm dialog 配置 dict, {} 表示关闭, 含 title_key/content_key/confirm_btn_key/callback/is_destructive
    confirm_dialog_config, set_confirm_dialog_config = ft.use_state({})

    # --- Async handlers (R2: except Exception 不捕获 CancelledError) ---
    async def _do_tushare_save(token: str) -> None:
        try:
            await vm.save_tushare_token(token)
            if show_snack_callback:
                show_snack_callback(I18n.get("settings_msg_saved"), color=AppColors.SUCCESS)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error(
                "[DataSourceTab] Tushare save failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_history_years_change(new_val: str) -> None:
        try:
            val = int(new_val)
            await vm.set_history_years(val)
            if show_snack_callback:
                show_snack_callback(I18n.get("common_saved"), color=AppColors.SUCCESS)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[DataSourceTab] HistoryRange | Failed to set config: %s", ex, exc_info=True)
            if show_snack_callback:
                show_snack_callback(I18n.get("sys_snack_save_err"), color=AppColors.ERROR)

    async def _do_check_health() -> None:
        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_check_health")
        try:
            await vm.check_health()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[DataSourceTab] Health check failed: %s", DataSanitizer.sanitize_error(ex))

    async def _do_full_sync() -> None:
        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_full_sync")
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        vm.execute_full_daily_sync()

    async def _do_ai_concept_rebuild() -> None:
        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_ai_concept_rebuild")
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        vm.execute_ai_concept_rebuild()

    async def _do_clear_cache() -> None:
        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_clear_cache")
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_clear_cache_syncing"), color=AppColors.WARNING)
            return
        vm.execute_clear_cache()

    async def _do_init_historical() -> None:
        ensure_correlation_id()
        # 取消已运行的 init sync
        if state.is_syncing and state.init_sync_cancellable:
            UILogger.log_action("DataSourceTab", "Click", "btn_cancel_sync")
            await vm.cancel_init_sync()
            return
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        UILogger.log_action("DataSourceTab", "Click", "btn_init_historical")
        vm.execute_init_historical_data()

    async def _do_show_health_report() -> None:
        UILogger.log_action("DataSourceTab", "Click", "btn_health_report")
        try:
            if show_snack_callback:
                show_snack_callback(I18n.get("health_checking"), color=AppColors.INFO)
            report = await vm.get_health_report()
            set_health_report_data(report)
            set_health_report_open(True)
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="general")
            if show_snack_callback:
                show_snack_callback(get_error_message(error_info), color=AppColors.ERROR)

    # --- Event handlers (调度异步任务) ---
    def _on_check_health(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_check_health)

    def _on_full_sync(e: ft.ControlEvent) -> None:
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        set_confirm_dialog_config(
            {
                "title_key": "dialog_confirm_full_sync_title",
                "content_key": "dialog_confirm_full_sync_content",
                "confirm_btn_key": "btn_confirm_sync",
                "callback": _do_full_sync,
                "is_destructive": False,
            }
        )

    def _on_ai_concept_rebuild(e: ft.ControlEvent) -> None:
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        set_confirm_dialog_config(
            {
                "title_key": "dialog_ai_concept_rebuild_title",
                "content_key": "dialog_ai_concept_rebuild_content",
                "confirm_btn_key": "btn_confirm_rebuild",
                "callback": _do_ai_concept_rebuild,
                "is_destructive": True,
            }
        )

    def _on_clear_cache(e: ft.ControlEvent) -> None:
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_clear_cache_syncing"), color=AppColors.WARNING)
            return
        set_confirm_dialog_config(
            {
                "title_key": "dialog_confirm_clear_title",
                "content_key": "dialog_confirm_clear_content",
                "confirm_btn_key": "btn_confirm_clear",
                "callback": _do_clear_cache,
                "is_destructive": True,
            }
        )

    def _on_init_historical(e: ft.ControlEvent) -> None:
        # 取消已运行的 init sync (直接调用, 无需 confirm dialog)
        if state.is_syncing and state.init_sync_cancellable:
            page = _get_page()
            if page is not None:
                page.run_task(_do_init_historical)
            return
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        set_confirm_dialog_config(
            {
                "title_key": "dialog_confirm_init_title",
                "content_key": "dialog_confirm_init_content",
                "confirm_btn_key": "btn_confirm_init",
                "callback": _do_init_historical,
                "is_destructive": False,
            }
        )

    def _on_history_years_change(e: ft.ControlEvent) -> None:
        new_val = get_control_value(e.control, ft.Dropdown) if e and e.control else None
        if not new_val:
            return
        page = _get_page()
        if page is not None:
            page.run_task(_do_history_years_change, new_val)

    def _on_health_report_click(e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_show_health_report)

    def _on_health_report_close() -> None:
        set_health_report_open(False)
        set_health_report_data({})

    def _on_deep_scan() -> None:
        set_scan_dialog_open(True)

    def _on_scan_close() -> None:
        set_scan_dialog_open(False)

    def _on_confirm_dialog_close() -> None:
        set_confirm_dialog_config({})

    def _on_confirm_dialog_confirm() -> None:
        if not confirm_dialog_config:
            return
        callback = confirm_dialog_config.get("callback")
        set_confirm_dialog_config({})
        page = _get_page()
        if page is not None and callback is not None:
            page.run_task(callback)

    # --- Mount: recover stale state + reload tushare config ---
    def _on_mount() -> None:
        vm.recover_stale_state()
        tushare_vm.reload_config()

    ft.use_effect(_on_mount, dependencies=[])

    # --- Unmount: dispose tushare_vm (主 VM 由 use_viewmodel hook 自动 dispose) ---
    def _cleanup_tushare_vm() -> None:
        tushare_vm.dispose()

    ft.use_effect(lambda: None, dependencies=[], cleanup=_cleanup_tushare_vm)

    # --- Snack effect: state.snack → show_snack (瞬态通知, 监听 seq 变化触发回调) ---
    # L771 合规: 直接从 state.snack 读取 (frozen SnackRow), 无 vm.last_snack property 拉取.
    # seq 字段确保连续相同内容也触发 use_state setter 更新.
    def _on_snack_change() -> None:
        snack = state.snack
        if snack is None:
            return
        text = I18n.get(snack.message.key, **snack.message.params)
        if show_snack_callback:
            show_snack_callback(text, color=_resolve_snack_color(snack.color_name))

    ft.use_effect(_on_snack_change, dependencies=[state.snack.seq if state.snack else 0])

    # --- cache_cleared effect: state.cache_cleared_version → broadcast PubSub (瞬态信号, 非 dual-track) ---
    def _on_cache_cleared_version_change() -> None:
        page = _get_page()
        if page is not None:
            page.pubsub.send_all_on_topic(CACHE_CLEARED_TOPIC, "cache_cleared")

    ft.use_effect(_on_cache_cleared_version_change, dependencies=[state.cache_cleared_version])

    # --- Derived state for MetricCard rendering (直接从 state 派生, 无 dual-track) ---
    if state.health_checking:
        metric_health_value = I18n.get("ds_status_checking")
        metric_health_icon = ft.Icons.HOURGLASS_TOP
        metric_health_color = AppColors.INFO
        metric_storage_value = I18n.get("ds_status_calc")
        metric_storage_icon = ft.Icons.HOURGLASS_TOP
        metric_storage_color = AppColors.TEXT_HINT
    elif state.health_error is not None:
        # 健康检查出错 (common_check_fail)
        health_key = "common_check_fail"
        metric_health_value = I18n.get(health_key)
        metric_storage_value = I18n.get(health_key)
        metric_health_icon, metric_health_color = _HEALTH_STATUS_VISUALS.get(
            health_key, (ft.Icons.HEALTH_AND_SAFETY, AppColors.WARNING)
        )
        metric_storage_icon, metric_storage_color = _HEALTH_STATUS_VISUALS.get(
            health_key, (ft.Icons.STORAGE, AppColors.TEXT_HINT)
        )
    elif state.health_result is not None:
        # 有健康检查结果, 从 status 派生 health_key
        status = state.health_result.status
        if status == "yellow":
            health_key = "ds_health_lag"
        elif status == "red":
            health_key = "ds_health_error"
        else:
            health_key = "ds_health_ok"
        metric_health_value = I18n.get(health_key)
        metric_storage_value = I18n.get("common_normal")
        metric_health_icon, metric_health_color = _HEALTH_STATUS_VISUALS.get(
            health_key, (ft.Icons.HEALTH_AND_SAFETY, AppColors.WARNING)
        )
        metric_storage_icon = ft.Icons.STORAGE
        metric_storage_color = AppColors.SUCCESS
    else:
        # 未检查过 (初始状态)
        metric_health_value = I18n.get("ds_status_checking")
        metric_health_icon = ft.Icons.HEALTH_AND_SAFETY
        metric_health_color = AppColors.WARNING
        metric_storage_value = I18n.get("ds_status_calc")
        metric_storage_icon = ft.Icons.STORAGE
        metric_storage_color = AppColors.TEXT_HINT

    # metric_sync / metric_coverage: 从 state.health_result 派生 (有结果时) 或占位值
    if state.health_result is not None:
        latest = state.health_result.market_latest_local
        metric_sync_value = I18n.get("ds_never_sync") if not latest or str(latest) == "None" else str(latest)
        cov_val = state.health_result.details_financial_coverage
        metric_coverage_value = f"{cov_val:.1f}%"
    else:
        metric_sync_value = f"{I18n.get('time_today')} 15:30"
        metric_coverage_value = I18n.get("ds_val_placeholder_count")

    metric_sync_icon = ft.Icons.ACCESS_TIME
    metric_sync_color = AppColors.PRIMARY
    metric_coverage_icon = ft.Icons.DATA_USAGE
    metric_coverage_color = AppColors.INFO

    # --- Sync button state (init sync) ---
    if state.is_syncing and state.init_sync_cancellable:
        sync_button_content = I18n.get("settings_cancel_sync")
        sync_button_icon = ft.Icons.STOP_CIRCLE
        sync_button_style = AppStyles.danger_button()  # P2-9: 替换为 danger_button 统一风格
    elif state.is_syncing:
        sync_button_content = I18n.get("sys_init_cancel_wait")
        sync_button_icon = ft.Icons.CLOUD_DOWNLOAD
        sync_button_style = AppStyles.primary_button()
    else:
        sync_button_content = I18n.get("settings_init_data")
        sync_button_icon = ft.Icons.CLOUD_DOWNLOAD
        sync_button_style = AppStyles.primary_button()

    # --- ActionChip loading state (derived from is_syncing + active_key) ---
    action_full_sync_loading = state.is_syncing and state.active_key == "daily_sync"
    action_ai_concept_loading = state.is_syncing and state.active_key == "ai_concept_sync"
    action_clear_cache_loading = state.is_syncing and state.active_key == "cache_clear"
    # 当任一 action 处于 loading 时, 其他 action 禁用
    any_action_loading = state.is_syncing and state.active_key in ("daily_sync", "ai_concept_sync", "cache_clear")
    # init sync 运行时也禁用 actions (除 sync_button 自身)
    init_sync_running = state.is_syncing and state.active_key == "system_init_sync"
    actions_disabled = any_action_loading or init_sync_running

    # --- Progress bar/text (derived from init sync state) ---
    progress_visible = state.init_sync_running or (state.is_syncing and state.active_key == "system_init_sync")
    if state.init_sync_final_status == TaskStatus.CANCELLED:
        progress_text_value = I18n.get("ds_progress_cancelled_fmt", msg=I18n.get("settings_msg_sync_cancelled"))
    elif state.init_sync_final_status == TaskStatus.FAILED:
        progress_text_value = I18n.get("ds_init_fail_generic")
    elif state.progress_message is not None:
        progress_text_value = f"{state.progress * 100:.1f}% - {_render_message(state.progress_message)}"
    else:
        progress_text_value = ""

    # --- Health summary content (直接从 state 派生, 无 dual-track) ---
    if state.health_checking:
        health_summary_content: ft.Control = ft.Text(
            I18n.get("health_checking"), size=12, color=AppColors.TEXT_SECONDARY
        )
    elif state.health_result is not None:
        health_summary_content = _build_health_summary_content(state.health_result)
    elif state.health_error is not None:
        health_summary_content = ft.Text(I18n.get("ds_health_check_error"), size=12, color=AppColors.ERROR)
    else:
        health_summary_content = ft.Text(I18n.get("settings_check_health"), size=12, color=AppColors.TEXT_SECONDARY)

    # --- Build controls ---
    style_health = AppStyles.primary_button()
    style_health.padding = ft.Padding.symmetric(horizontal=15, vertical=0)

    btn_check_health = ft.Button(
        content=I18n.get("settings_check_health"),
        icon=ft.Icons.REFRESH,
        on_click=safe_on_click(_on_check_health),
        style=style_health,
        height=40,
        width=AppStyles.CONTROL_WIDTH_MD,
        disabled=state.health_checking,
    )
    btn_health_report = ft.IconButton(
        icon=ft.Icons.INFO_OUTLINE,
        tooltip=I18n.get("health_report_title"),
        on_click=safe_on_click(_on_health_report_click),
    )

    # MetricCards (props 推送, 声明式自动重渲染)
    metric_sync = MetricCard(
        label=I18n.get("ds_last_update"),
        value=metric_sync_value,
        icon=safe_icon_str(metric_sync_icon),
        status_color=metric_sync_color,
    )
    metric_coverage = MetricCard(
        label=I18n.get("ds_data_coverage"),
        value=metric_coverage_value,
        icon=safe_icon_str(metric_coverage_icon),
        status_color=metric_coverage_color,
    )
    metric_health = MetricCard(
        label=I18n.get("ds_sys_health"),
        value=metric_health_value,
        icon=safe_icon_str(metric_health_icon),
        status_color=metric_health_color,
    )
    metric_storage = MetricCard(
        label=I18n.get("ds_storage_usage"),
        value=metric_storage_value,
        icon=safe_icon_str(metric_storage_icon),
        status_color=metric_storage_color,
    )

    health_dashboard = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(I18n.get("settings_sec_health"), title_key="settings_sec_health"),
                        ft.Row([btn_health_report, btn_check_health]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.ResponsiveRow(
                    [
                        ft.Column([metric_sync], col={"sm": 6, "md": 3}),
                        ft.Column([metric_coverage], col={"sm": 6, "md": 3}),
                        ft.Column([metric_health], col={"sm": 6, "md": 3}),
                        ft.Column([metric_storage], col={"sm": 6, "md": 3}),
                    ],
                ),
                ft.Container(height=10),
                ft.Container(height=10),
                ft.Container(height=10),
                ft.Container(
                    content=health_summary_content,
                    padding=ft.Padding.symmetric(vertical=10, horizontal=15),
                    bgcolor=AppColors.SURFACE_VARIANT,
                    border_radius=8,
                    border=ft.Border.all(1, AppColors.DIVIDER),
                ),
            ],
        ),
    )

    # ActionChips (props 推送, is_loading 派生自 state)
    action_full_sync = ActionChip(
        icon=safe_icon_str(ft.Icons.SYNC_PROBLEM),
        title=I18n.get("settings_full_sync"),
        subtitle=I18n.get("ds_action_full"),
        on_click=_on_full_sync,
        is_loading=action_full_sync_loading,
    )
    action_ai_concept_rebuild = ActionChip(
        icon=safe_icon_str(ft.Icons.AUTO_FIX_HIGH),
        title=I18n.get("ds_btn_ai_concept_rebuild"),
        subtitle=I18n.get("ds_btn_ai_concept_rebuild_desc"),
        on_click=_on_ai_concept_rebuild,
        is_loading=action_ai_concept_loading,
    )
    action_clear_cache = ActionChip(
        icon=safe_icon_str(ft.Icons.CLEANING_SERVICES),
        title=I18n.get("settings_clear_cache"),
        subtitle=I18n.get("ds_action_clear"),
        on_click=_on_clear_cache,
        is_loading=action_clear_cache_loading,
    )

    action_console = DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("ds_shortcut_console"), title_key="ds_shortcut_console"),
                ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                ft.ResponsiveRow(
                    [
                        ft.Column([action_full_sync], col={"sm": 12, "md": 4}),
                        ft.Column([action_ai_concept_rebuild], col={"sm": 12, "md": 4}),
                        ft.Column([action_clear_cache], col={"sm": 12, "md": 4}),
                    ],
                    run_spacing=10,
                ),
            ],
        ),
    )

    # Connection Settings (TushareConfigPanel 消费 tushare_vm, props 推送)
    tushare_panel = TushareConfigPanel(
        vm=tushare_vm,
        compact=False,
        show_save_button=True,
        show_register_link=False,
    )

    row_token = SettingRow(
        icon=safe_icon_str(ft.Icons.KEY_ROUNDED),
        title=I18n.get("settings_token"),
        subtitle=I18n.get("settings_token_desc"),
        control=tushare_panel,
        icon_color=AppColors.ACCENT,
        title_key="settings_token",
        subtitle_key="settings_token_desc",
        left_col={"xs": 12, "sm": 12, "md": 5, "lg": 4},
        right_col={"xs": 12, "sm": 12, "md": 7, "lg": 8},
    )
    connection_card = DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("settings_sec_api"), title_key="settings_sec_api"),
                ft.Container(height=10),
                row_token,
            ],
        ),
    )

    # Historical Data
    progress_bar = ft.ProgressBar(width=None, visible=progress_visible, expand=True)
    if progress_visible:
        progress_bar.value = state.progress
    progress_text = ft.Text(progress_text_value, size=12, color=AppColors.INFO, visible=bool(progress_text_value))

    style_init = AppStyles.primary_button()
    style_init.padding = ft.Padding.symmetric(horizontal=15, vertical=0)

    sync_button = ft.Button(
        content=sync_button_content,
        icon=sync_button_icon,
        on_click=safe_on_click(_on_init_historical),
        tooltip=I18n.get("settings_init_desc"),
        style=sync_button_style,
        height=40,
        width=AppStyles.CONTROL_WIDTH_MD,
        disabled=actions_disabled and not (state.is_syncing and state.init_sync_cancellable),
    )

    years_value = str(vm.get_history_years())
    history_years_dropdown = ft.Dropdown(
        label=I18n.get("settings_history_range"),
        value=years_value,
        options=_build_history_years_options(),
        width=150,
        on_select=safe_on_select(_on_history_years_change),
    )

    row_init = SettingRow(
        icon=safe_icon_str(ft.Icons.HISTORY_ROUNDED),
        title=I18n.get("settings_init_data"),
        subtitle=I18n.get("settings_hint_first_run"),
        control=ft.Column(
            [
                ft.Row(
                    [history_years_dropdown, sync_button],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=10,
                    wrap=True,
                ),
                ft.Row(
                    [
                        ft.Column(
                            [progress_bar, progress_text],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.END,
                ),
            ],
            spacing=5,
            alignment=ft.MainAxisAlignment.CENTER,
            expand=True,
        ),
        icon_color=ft.Colors.PURPLE,
        title_key="settings_init_data",
        subtitle_key="settings_hint_first_run",
    )
    historical_card = DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("settings_init_data"), title_key="settings_init_data"),
                ft.Container(height=10),
                row_init,
            ],
        ),
    )

    # --- Confirm dialog (条件渲染 + use_state) ---
    if confirm_dialog_config:
        title_key = confirm_dialog_config.get("title_key", "")
        content_key = confirm_dialog_config.get("content_key", "")
        confirm_btn_key = confirm_dialog_config.get("confirm_btn_key", "")
        is_destructive = confirm_dialog_config.get("is_destructive", False)
        btn_style = ft.ButtonStyle(color=AppColors.ERROR) if is_destructive else ft.ButtonStyle(color=AppColors.PRIMARY)
        confirm_dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get(title_key)),
            content=ft.Text(I18n.get(content_key)),
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=lambda e: _on_confirm_dialog_close()),
                ft.TextButton(
                    I18n.get(confirm_btn_key),
                    on_click=lambda e: _on_confirm_dialog_confirm(),
                    style=btn_style,
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        ft.use_dialog(confirm_dialog)

    # --- Health report dialog (条件渲染) ---
    if health_report_open and health_report_data:
        page = _get_page()
        health_report_dialog_ctrl = HealthReportDialog(
            report=health_report_data,
            page=page,
            open_state=True,
            on_close=_on_health_report_close,
            on_deep_scan=_on_deep_scan,
        )
        ft.use_dialog(typing.cast("ft.DialogControl | None", health_report_dialog_ctrl))

    # --- Health scan dialog (条件渲染) ---
    if scan_dialog_open:
        page = _get_page()
        scan_dialog_ctrl = HealthScanDialog(
            data_processor=vm.get_data_processor(),
            page=page,
            open_state=True,
            on_close=_on_scan_close,
        )
        ft.use_dialog(typing.cast("ft.DialogControl | None", scan_dialog_ctrl))

    return ft.Container(
        content=ft.ListView(
            controls=[
                health_dashboard,
                action_console,
                connection_card,
                historical_card,
            ],
            spacing=15,
            padding=ft.Padding.only(bottom=50),
        ),
        expand=True,
    )
