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
from collections.abc import Callable

import flet as ft

from ui.components.config_panels.backup_restore_panel import BackupRestorePanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel
from ui.components.flet_type_helpers import (
    get_control_value,
    safe_icon_str,
    safe_on_change,
    safe_on_click,
    safe_on_select,
)
from ui.components.health_report_dialog import HealthReportDialog, HealthScanDialog
from ui.components.safe_wrap_row import SafeWrapRow
from ui.components.settings_widgets import (
    ActionChip,
    DashboardCard,
    MetricCard,
    SectionHeader,
    SettingRow,
)
from ui.cache_cleared_state import notify_cache_cleared
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.data_source_view_model import DataSourceState, DataSourceViewModel, HealthResultRow, TaskStatus
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel
from utils.correlation import ensure_correlation_id
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.app_env import is_e2e_mode

logger = logging.getLogger(__name__)


# 「重置本地数据库」将删除的用户数据类别 (i18n key)。
# 该操作执行 ``DROP SCHEMA public CASCADE`` 后重建 schema，会清空全部业务表；
# 此处按 ``data/data_dictionary.py`` 的 TABLE_DEFINITIONS 逐表核对后归并为
# 用户可理解的类别，确保不遗漏、不臆造（表结构/版本标记由 schema_note 说明兜底）：
#   watchlist / screening_history / screening_thinking / news_risk_brief /
#   ai_concept_failures / daily_quotes / daily_indicators / moneyflow_daily /
#   index_daily / index_dailybasic / northbound_holding / margin_daily /
#   stk_limit / limit_list / top_list / top_inst / block_trade / moneyflow_hsgt /
#   index_weight / suspend_d / financial_reports / fina_forecast / fina_audit /
#   fina_mainbz / express / dividend / repurchase / stk_holdernumber /
#   top10_holders / pledge_stat / pledge_detail / share_float / stk_holdertrade /
#   stock_basic / stock_concepts / sw_industry_classify / sw_industry_member /
#   stock_name_history / market_news / macro_economy / shibor_daily /
#   backtest_results / task_history / sync_status / stock_sync_status /
#   app_state / trade_cal
_RESET_DATA_CATEGORY_KEYS: tuple[str, ...] = (
    "danger_reset_cat_watchlist",
    "danger_reset_cat_screening",
    "danger_reset_cat_ai",
    "danger_reset_cat_quotes",
    "danger_reset_cat_financials",
    "danger_reset_cat_basic",
    "danger_reset_cat_news_macro",
    "danger_reset_cat_backtest",
    "danger_reset_cat_task_state",
)


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


def _render_message(msg: Message | str | None) -> str:
    """渲染 Message / str 到本地化文本 (str 直显, 兼容 E1 暂留的 report_step 路径).

    嵌套 key 约定 (与 task_center_view._render_task_field / screener_view
    ._render_status_message 一致): params 中以 ``_key`` 结尾的字符串参数会先
    翻译再填入主模板 (去掉 ``_key`` 后缀), 使 data 层可传 ``stock_key`` 这类
    i18n key 而无需感知 locale.
    """
    if msg is None:
        return ""
    if isinstance(msg, str):
        return msg
    params = dict(msg.params)
    for k in list(params):
        if k.endswith("_key") and isinstance(params[k], str):
            params[k[:-4]] = I18n.get(params[k])
            del params[k]
    return I18n.get(msg.key, **params)


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
        ft.Icon(core_icon, size=AppStyles.FONT_SIZE_LG, color=core_color),
        ft.Text(core_text, size=AppStyles.FONT_SIZE_BODY_SM, color=core_color),
    ]
    if miss_depth > 0:
        integrity_items.extend(
            [
                ft.Text("|", size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("ds_health_summary_depth").format(miss=miss_depth),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.WARNING,
                ),
            ]
        )
    if miss_breadth > 0:
        integrity_items.extend(
            [
                ft.Text("|", size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("ds_health_summary_breadth").format(miss=miss_breadth),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.WARNING,
                ),
            ]
        )

    return ft.Column(
        [
            ft.Row(
                [
                    ft.Icon(ft.Icons.ANALYTICS, size=AppStyles.FONT_SIZE_LG, color=AppColors.INFO),
                    ft.Text(sys_text, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_PRIMARY),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.START,
            ),
            SafeWrapRow(integrity_items, spacing=5, alignment=ft.MainAxisAlignment.START),
            ft.Row(
                [
                    ft.Icon(ft.Icons.VERIFIED, size=AppStyles.FONT_SIZE_LG, color=AppColors.PRIMARY),
                    # DS-06: E2E 模式（测试态）质量门控被禁用（quality_gate 绕过），
                    # 必须显式呈现而非显示一个"看似未检测"的等级——降级必须可见。
                    ft.Text(
                        I18n.get("ds_e2e_gate_disabled")
                        if is_e2e_mode()
                        else (
                            f"{I18n.get('ds_data_quality_tier')}: {I18n.get(f'quality_tier_{result.quality_tier}')}"
                            if result.quality_tier is not None
                            else f"{I18n.get('ds_data_quality_tier')}: {I18n.get('ds_not_checked')}"
                        ),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.WARNING if is_e2e_mode() else AppColors.TEXT_PRIMARY,
                    ),
                ],
                spacing=5,
                alignment=ft.MainAxisAlignment.START,
            ),
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
# D15: DataSourceTab 区块渲染函数 — 从巨型组件提取为模块级纯函数,
# 每个 DashboardCard 自含派生逻辑 (接收 state + 回调 props, 无闭包/状态).
# ============================================================================


def _build_health_dashboard(
    state: DataSourceState,
    on_check_health: Callable[[ft.ControlEvent], None],
    on_health_report_click: Callable[[ft.ControlEvent], None],
) -> ft.Control:
    """Health Dashboard 区块 (D15: 从 DataSourceTab 提取, 派生逻辑自含)."""
    # 直接从 state 派生 metric 值 (无 dual-track, 与组件原逻辑一致)
    if state.health_checking:
        metric_health_value = I18n.get("ds_status_checking")
        metric_health_icon = ft.Icons.HOURGLASS_TOP
        metric_health_color = AppColors.INFO
    elif state.health_error is not None:
        health_key = "common_check_fail"
        metric_health_value = I18n.get(health_key)
        metric_health_icon, metric_health_color = _HEALTH_STATUS_VISUALS.get(
            health_key, (ft.Icons.HEALTH_AND_SAFETY, AppColors.WARNING)
        )
    elif state.health_result is not None:
        status = state.health_result.status
        if status == "yellow":
            health_key = "ds_health_lag"
        elif status == "red":
            health_key = "ds_health_error"
        else:
            health_key = "ds_health_ok"
        metric_health_value = I18n.get(health_key)
        metric_health_icon, metric_health_color = _HEALTH_STATUS_VISUALS.get(
            health_key, (ft.Icons.HEALTH_AND_SAFETY, AppColors.WARNING)
        )
    else:
        metric_health_value = I18n.get("ds_status_checking")
        metric_health_icon = ft.Icons.HEALTH_AND_SAFETY
        metric_health_color = AppColors.WARNING

    if state.health_result is not None:
        latest = state.health_result.market_latest_local
        metric_sync_value = I18n.get("ds_never_sync") if not latest or str(latest) == "None" else str(latest)
        cov_val = state.health_result.details_financial_coverage
        metric_coverage_value = f"{cov_val:.1f}%"
    else:
        metric_sync_value = I18n.get("ds_not_checked")
        metric_coverage_value = I18n.get("ds_val_placeholder_count")

    # Health summary content
    if state.health_checking:
        health_summary_content: ft.Control = ft.Text(
            I18n.get("health_checking"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY
        )
    elif state.health_result is not None:
        health_summary_content = _build_health_summary_content(state.health_result)
    elif state.health_error is not None:
        health_summary_content = ft.Text(
            I18n.get("ds_health_check_error"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.ERROR
        )
    else:
        health_summary_content = ft.Text(
            I18n.get("settings_check_health"), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY
        )

    style_health = AppStyles.primary_button()
    style_health.padding = ft.Padding.symmetric(horizontal=15, vertical=0)

    btn_check_health = ft.Button(
        content=I18n.get("settings_check_health"),
        icon=ft.Icons.REFRESH,
        on_click=safe_on_click(on_check_health),
        style=style_health,
        height=40,
        width=AppStyles.CONTROL_WIDTH_MD,
        disabled=state.health_checking,
    )
    btn_health_report = ft.IconButton(
        icon=ft.Icons.INFO_OUTLINE,
        tooltip=I18n.get("health_report_title"),
        on_click=safe_on_click(on_health_report_click),
    )

    metric_sync = MetricCard(
        label=I18n.get("ds_last_update"),
        value=metric_sync_value,
        icon=safe_icon_str(ft.Icons.ACCESS_TIME),
        status_color=AppColors.PRIMARY,
    )
    metric_coverage = MetricCard(
        label=I18n.get("ds_data_coverage"),
        value=metric_coverage_value,
        icon=safe_icon_str(ft.Icons.DATA_USAGE),
        status_color=AppColors.INFO,
    )
    metric_health = MetricCard(
        label=I18n.get("ds_sys_health"),
        value=metric_health_value,
        icon=safe_icon_str(metric_health_icon),
        status_color=metric_health_color,
    )

    return DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(I18n.get("settings_sec_health"), title_key="settings_sec_health"),
                        ft.Row([btn_health_report, btn_check_health]),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(height=10, color=AppColors.TRANSPARENT),
                ft.ResponsiveRow(
                    [
                        ft.Column([metric_sync], col={"sm": 6, "md": 4}),
                        ft.Column([metric_coverage], col={"sm": 6, "md": 4}),
                        ft.Column([metric_health], col={"sm": 6, "md": 4}),
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


def _build_action_console(
    state: DataSourceState,
    on_full_sync: Callable[[ft.ControlEvent], None],
    on_ai_concept_rebuild: Callable[[ft.ControlEvent], None],
    on_cancel_active_task: Callable[[ft.ControlEvent], None],
) -> ft.Control:
    """Action Console 区块 (D15: 从 DataSourceTab 提取, P1-5 次级进度自含).

    MAJOR-03: 破坏性「重置本地数据库」入口已移出本区块, 见 ``_build_danger_zone``。
    """
    # ActionChip loading state (derived from is_syncing + active_key)
    action_full_sync_loading = state.is_syncing and state.active_key == "daily_sync"
    action_ai_concept_loading = state.is_syncing and state.active_key == "ai_concept_sync"

    # P1-5: Secondary progress 区域
    secondary_progress_visible = state.is_syncing and state.active_key in (
        "daily_sync",
        "ai_concept_sync",
        "cache_clear",
    )
    secondary_cancellable = state.active_key in ("daily_sync", "ai_concept_sync")
    if secondary_progress_visible and state.progress_message is not None:
        secondary_progress_text_value = f"{state.progress * 100:.1f}% - {_render_message(state.progress_message)}"
    elif secondary_progress_visible and state.active_key != "cache_clear":
        secondary_progress_text_value = f"{state.progress * 100:.1f}%"
    else:
        secondary_progress_text_value = ""

    secondary_progress_bar = ft.ProgressBar(
        value=state.progress if state.active_key != "cache_clear" else None,
        visible=secondary_progress_visible,
        expand=True,
    )
    secondary_progress_text = ft.Text(
        secondary_progress_text_value,
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.INFO,
        visible=bool(secondary_progress_text_value),
    )
    secondary_cancel_button = ft.Button(
        content=I18n.get("common_cancel"),
        on_click=safe_on_click(on_cancel_active_task),
        visible=secondary_progress_visible and secondary_cancellable,
        style=AppStyles.danger_button(),
    )

    action_full_sync = ActionChip(
        icon=safe_icon_str(ft.Icons.SYNC_PROBLEM),
        title=I18n.get("settings_full_sync"),
        subtitle=I18n.get("ds_action_full"),
        on_click=on_full_sync,
        is_loading=action_full_sync_loading,
    )
    action_ai_concept_rebuild = ActionChip(
        icon=safe_icon_str(ft.Icons.AUTO_FIX_HIGH),
        title=I18n.get("ds_btn_ai_concept_rebuild"),
        subtitle=I18n.get("ds_btn_ai_concept_rebuild_desc"),
        on_click=on_ai_concept_rebuild,
        is_loading=action_ai_concept_loading,
    )

    return DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("ds_shortcut_console"), title_key="ds_shortcut_console"),
                ft.Divider(height=10, color=AppColors.TRANSPARENT),
                ft.ResponsiveRow(
                    [
                        ft.Column([action_full_sync], col={"sm": 12, "md": 6}),
                        ft.Column([action_ai_concept_rebuild], col={"sm": 12, "md": 6}),
                    ],
                    run_spacing=10,
                ),
                ft.Row(
                    [secondary_progress_bar, secondary_progress_text, secondary_cancel_button],
                    spacing=8,
                    visible=secondary_progress_visible,
                ),
            ],
        ),
    )


def _build_danger_zone(
    state: DataSourceState,
    on_clear_cache: Callable[[ft.ControlEvent], None],
) -> ft.Control:
    """危险操作区块 (MAJOR-03): 破坏性「重置本地数据库」入口独立成区.

    - 不再与正常同步入口同处一个 ``ResponsiveRow``, 单独成卡并以危险色描边区分；
    - 实际执行 ``DROP SCHEMA public CASCADE`` (本地数据库全量重建), 故视觉与文案
      必须与常规同步操作明确区隔。
    """
    action_clear_cache_loading = state.is_syncing and state.active_key == "cache_clear"
    action_clear_cache = ActionChip(
        icon=safe_icon_str(ft.Icons.WARNING_AMBER_ROUNDED),
        title=I18n.get("settings_reset_database"),
        subtitle=I18n.get("ds_reset_database_desc"),
        on_click=on_clear_cache,
        is_loading=action_clear_cache_loading,
    )
    return ft.Container(
        content=DashboardCard(
            content=ft.Column(
                [
                    SectionHeader(I18n.get("settings_danger_zone"), title_key="settings_danger_zone"),
                    ft.Divider(height=10, color=AppColors.TRANSPARENT),
                    ft.Container(
                        content=action_clear_cache,
                        border=ft.Border.all(1, AppColors.ERROR),
                        border_radius=12,
                    ),
                ],
            ),
        ),
        border=ft.Border.all(1, AppColors.ERROR),
        border_radius=12,
    )


def _build_connection_card(tushare_vm: TushareConfigPanelViewModel) -> ft.Control:
    """Connection Settings 区块 (D15: 从 DataSourceTab 提取)."""
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
    return DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("settings_sec_api"), title_key="settings_sec_api"),
                ft.Container(height=10),
                row_token,
            ],
        ),
    )


def _build_historical_card(
    state: DataSourceState,
    vm: DataSourceViewModel,
    on_init_historical: Callable[[ft.ControlEvent], None],
    on_history_years_change: Callable[[ft.ControlEvent], None],
    on_cancel_init_sync: Callable[[ft.ControlEvent], None],
) -> ft.Control:
    """Historical Data 区块 (D15: 从 DataSourceTab 提取, init sync 进度自含)."""
    # Sync button state (init sync)
    # MINOR-11: 开始按钮恒定显示「初始化历史数据」且同步中禁用，不再 morph 成取消按钮——
    # 避免同一按钮位置前一秒「开始」、后一秒「无确认取消」，双击即中断刚启动的长任务。
    sync_button_content = I18n.get("settings_init_data")
    sync_button_icon = ft.Icons.CLOUD_DOWNLOAD
    sync_button_style = AppStyles.primary_button()

    # Progress bar/text (derived from init sync state)
    progress_visible = state.init_sync_running or (state.is_syncing and state.active_key == "system_init_sync")
    if state.init_sync_final_status == TaskStatus.CANCELLED:
        progress_text_value = I18n.get("ds_progress_cancelled_fmt", msg=I18n.get("settings_msg_sync_cancelled"))
    elif state.init_sync_final_status == TaskStatus.FAILED:
        progress_text_value = I18n.get("ds_init_fail_generic")
    elif state.progress_message is not None:
        progress_text_value = f"{state.progress * 100:.1f}% - {_render_message(state.progress_message)}"
    else:
        progress_text_value = ""

    progress_bar = ft.ProgressBar(width=None, visible=progress_visible, expand=True)
    if progress_visible:
        progress_bar.value = state.progress
    progress_text = ft.Text(
        progress_text_value, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.INFO, visible=bool(progress_text_value)
    )

    style_init = AppStyles.primary_button()
    style_init.padding = ft.Padding.symmetric(horizontal=15, vertical=0)

    sync_button = ft.Button(
        content=sync_button_content,
        icon=sync_button_icon,
        on_click=safe_on_click(on_init_historical),
        tooltip=I18n.get("settings_init_desc"),
        style=sync_button_style,
        height=40,
        width=AppStyles.CONTROL_WIDTH_MD,
        # MINOR-11: 任意同步进行中禁用开始按钮 (含 init sync 本身)，防止重复启动。
        disabled=state.is_syncing,
    )

    # MINOR-11: 独立位置的取消按钮 (不复用开始按钮位置, 消除「双击即中断」误触风险)。
    # 仅在 init sync 可取消时显示；取消本身走确认对话框 (on_cancel_init_sync 事件处理器),
    # 与开始按钮位置分离, 避免误点。
    cancel_init_button = ft.Button(
        content=I18n.get("settings_cancel_sync"),
        icon=ft.Icons.STOP_CIRCLE,
        on_click=safe_on_click(on_cancel_init_sync),
        style=AppStyles.danger_button(),
        height=40,
        visible=state.is_syncing and state.init_sync_cancellable,
    )

    years_value = str(vm.get_history_years())
    history_years_dropdown = ft.Dropdown(
        label=I18n.get("settings_history_range"),
        value=years_value,
        options=_build_history_years_options(),
        width=150,
        on_select=safe_on_select(on_history_years_change),
    )

    row_init = SettingRow(
        icon=safe_icon_str(ft.Icons.HISTORY_ROUNDED),
        title=I18n.get("settings_init_data"),
        subtitle=I18n.get("settings_hint_first_run"),
        control=ft.Column(
            [
                SafeWrapRow(
                    [history_years_dropdown, sync_button],
                    alignment=ft.MainAxisAlignment.END,
                    spacing=10,
                ),
                ft.Row(
                    [
                        ft.Column(
                            [progress_bar, progress_text],
                            spacing=2,
                            expand=True,
                        ),
                        cancel_init_button,
                    ],
                    alignment=ft.MainAxisAlignment.END,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
            spacing=5,
            alignment=ft.MainAxisAlignment.CENTER,
            expand=True,
        ),
        icon_color=AppColors.TERTIARY,
        title_key="settings_init_data",
        subtitle_key="settings_hint_first_run",
    )
    return DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("settings_init_data"), title_key="settings_init_data"),
                ft.Container(height=10),
                row_init,
            ],
        ),
    )


def _build_data_flow_card() -> ft.Control:
    """数据存储与流向说明区块 (静态展示, D15: 从 DataSourceTab 提取)."""
    return DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("ds_data_flow_title"), title_key="ds_data_flow_title"),
                ft.Container(height=10),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.STORAGE, size=AppStyles.FONT_SIZE_LG, color=AppColors.INFO),
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("ds_data_flow_storage_title"),
                                    size=AppStyles.FONT_SIZE_BODY,
                                    weight=ft.FontWeight.BOLD,
                                    color=AppColors.TEXT_PRIMARY,
                                ),
                                ft.Text(
                                    I18n.get("ds_data_flow_storage_desc"),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                    spacing=10,
                    alignment=ft.MainAxisAlignment.START,
                ),
                ft.Divider(height=10, color=AppColors.TRANSPARENT),
                ft.Row(
                    [
                        ft.Icon(ft.Icons.CLOUD_SYNC, size=AppStyles.FONT_SIZE_LG, color=AppColors.ACCENT),
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("ds_data_flow_outbound_title"),
                                    size=AppStyles.FONT_SIZE_BODY,
                                    weight=ft.FontWeight.BOLD,
                                    color=AppColors.TEXT_PRIMARY,
                                ),
                                ft.Text(
                                    f"• {I18n.get('ds_data_flow_outbound_tushare')}",
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                ft.Text(
                                    f"• {I18n.get('ds_data_flow_outbound_llm')}",
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                            ],
                            spacing=2,
                            expand=True,
                        ),
                    ],
                    spacing=10,
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
        ),
    )


# ============================================================================
# D7-6 调度状态面板 (只读快照渲染)
# ============================================================================

# job_id → 展示名 i18n key (与 SchedulerService._SCHEDULED_JOB_IDS 顺序一致)。
_SCHEDULER_JOB_NAME_KEYS: dict[str, str] = {
    "daily_update": "ds_sched_job_daily_update",
    "review_backfill": "ds_sched_job_review_backfill",
    "ai_concept_daily_refresh": "ds_sched_job_ai_concept",
    "nightly_prediction": "ds_sched_job_nightly_prediction",
}


def _sched_last_success_text(value: str | None) -> str:
    """上次成功时间: 无记录时渲染「未记录」(R21: 不得伪装为 0/正常)。"""
    return value if value else I18n.get("ds_sched_not_recorded")


def _sched_optional_text(value: str | None) -> str:
    """通用可选字段: 缺失渲染为占位符「—」(R21)。"""
    return value if value else I18n.get("ds_sched_placeholder_none")


def _sched_fail_count_text(value: int | None) -> str:
    """连续失败次数: None(未记录)为「—」; 0 为真实计数(非缺失), 格式化为「0 次」。"""
    if value is None:
        return I18n.get("ds_sched_placeholder_none")
    return I18n.get("ds_sched_fail_count_fmt", count=value)


def _sched_field(label_key: str, value_text: str) -> ft.Column:
    """单个字段块 (标签在上, 值在下), 供面板内 2x2 网格布局复用。"""
    return ft.Column(
        [
            ft.Text(I18n.get(label_key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
            ft.Text(value_text, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_PRIMARY),
        ],
        spacing=2,
    )


def _build_scheduler_status_card(state: DataSourceState) -> ft.Control:
    """D7-6: 调度状态面板 (各定时任务的上次成功/失败原因/下次计划/连续失败次数)。

    数据源为 ``state.scheduler_jobs`` (SchedulerService 只读快照, VM 刷新写入); 空快照
    (尚未刷新) 显式提示「未记录」, 不得渲染为「一切正常」(R21)。字段缺失由 View 层渲染
    为占位符, 不透传 None 给控件。
    """
    jobs = state.scheduler_jobs
    if not jobs:
        body: ft.Control = ft.Text(
            I18n.get("ds_sched_not_recorded"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_SECONDARY,
        )
    else:
        job_blocks: list[ft.Control] = []
        for job in jobs:
            job_blocks.append(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Text(
                                I18n.get(_SCHEDULER_JOB_NAME_KEYS.get(job.job_id, job.job_id)),
                                size=AppStyles.FONT_SIZE_BODY,
                                weight=ft.FontWeight.BOLD,
                                color=AppColors.TEXT_PRIMARY,
                            ),
                            ft.ResponsiveRow(
                                [
                                    ft.Column(
                                        [
                                            _sched_field(
                                                "ds_sched_col_last_success",
                                                _sched_last_success_text(job.last_success_at),
                                            )
                                        ],
                                        col={"sm": 6, "md": 3},
                                    ),
                                    ft.Column(
                                        [_sched_field("ds_sched_col_next_run", _sched_optional_text(job.next_run_at))],
                                        col={"sm": 6, "md": 3},
                                    ),
                                    ft.Column(
                                        [
                                            _sched_field(
                                                "ds_sched_col_fail_count",
                                                _sched_fail_count_text(job.consecutive_failures),
                                            )
                                        ],
                                        col={"sm": 6, "md": 3},
                                    ),
                                    ft.Column(
                                        [
                                            _sched_field(
                                                "ds_sched_col_last_failure",
                                                _sched_optional_text(job.last_failure_reason),
                                            )
                                        ],
                                        col={"sm": 6, "md": 3},
                                    ),
                                ],
                            ),
                        ],
                        spacing=6,
                    ),
                    padding=ft.Padding.symmetric(vertical=8, horizontal=12),
                    bgcolor=AppColors.SURFACE_VARIANT,
                    border_radius=8,
                    border=ft.Border.all(1, AppColors.DIVIDER),
                )
            )
        body = ft.Column(job_blocks, spacing=8)

    return DashboardCard(
        content=ft.Column(
            [
                SectionHeader(I18n.get("ds_sched_panel_title"), title_key="ds_sched_panel_title"),
                ft.Divider(height=10, color=AppColors.TRANSPARENT),
                body,
            ],
        ),
    )


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
    # MAJOR-03: 重置本地数据库的确认框额外携带 categories(将删除的数据类别 i18n key 列表)
    #   与 checkbox_key(必须勾选后才启用确认按钮); 其余确认框沿用简单 content_key。
    confirm_dialog_config, set_confirm_dialog_config = ft.use_state({})
    # 重置确认框的勾选状态 (对话框打开/关闭时复位, 防止残留勾选绕过二次确认)
    reset_ack_checked, set_reset_ack_checked = ft.use_state(False)
    # 「先导出备份」子对话框 (挂载既有 BackupRestorePanel)
    backup_dialog_open, set_backup_dialog_open = ft.use_state(False)

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
            logger.error(
                "[DataSourceTab] HistoryRange | Failed to set config: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
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

    async def _do_probe_credits() -> None:
        """Task 5.1: snack action '重新探测积分' → tushare_vm.verify_token()."""
        ensure_correlation_id()
        try:
            await tushare_vm.verify_token()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("[DataSourceTab] Probe credits failed: %s", DataSanitizer.sanitize_error(ex))

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
        # MINOR-11: 取消不再复用开始入口 (独立按钮 + 确认对话框), 此处仅负责启动。
        if state.is_syncing:
            if show_snack_callback:
                show_snack_callback(I18n.get("ds_sync_in_progress"), color=AppColors.WARNING)
            return
        UILogger.log_action("DataSourceTab", "Click", "btn_init_historical")
        vm.execute_init_historical_data()

    async def _do_cancel_init_sync() -> None:
        """MINOR-11: 独立取消入口 (经确认对话框回调触发), 而非复用开始按钮。"""
        ensure_correlation_id()
        UILogger.log_action("DataSourceTab", "Click", "btn_cancel_sync")
        try:
            await vm.cancel_init_sync()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as ex:
            logger.error("取消初始化同步失败: %s", DataSanitizer.sanitize_error(ex), exc_info=True)

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
        # Task 2.2: 未确认 AI 外发政策时，dialog 增加外发说明
        content_key = (
            "dialog_ai_concept_rebuild_content_with_external"
            if not vm.is_ai_external_acknowledged()
            else "dialog_ai_concept_rebuild_content"
        )
        set_confirm_dialog_config(
            {
                "title_key": "dialog_ai_concept_rebuild_title",
                "content_key": content_key,
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
        # MAJOR-03: 破坏性重置需逐项列出将删除的数据类别 + 勾选确认后才可执行。
        set_reset_ack_checked(False)
        set_confirm_dialog_config(
            {
                "title_key": "dialog_confirm_reset_title",
                "content_key": "dialog_confirm_reset_content",
                "confirm_btn_key": "btn_confirm_reset",
                "callback": _do_clear_cache,
                "is_destructive": True,
                "categories": list(_RESET_DATA_CATEGORY_KEYS),
                "checkbox_key": "dialog_reset_ack_checkbox",
            }
        )

    def _on_init_historical(e: ft.ControlEvent) -> None:
        # MINOR-11: 开始按钮恒定只负责启动 (取消走独立按钮 _on_init_cancel + 确认)。
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

    def _on_init_cancel(e: ft.ControlEvent) -> None:
        """MINOR-11: 独立取消按钮 → 二次确认后才真正取消长任务。"""
        if not (state.is_syncing and state.init_sync_cancellable):
            return
        set_confirm_dialog_config(
            {
                "title_key": "dialog_confirm_cancel_init_title",
                "content_key": "dialog_confirm_cancel_init_content",
                "confirm_btn_key": "btn_confirm_cancel_sync",
                "callback": _do_cancel_init_sync,
                "is_destructive": True,
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

    def _on_cancel_active_task(e: ft.ControlEvent) -> None:
        """P1-5: 取消 daily_sync/ai_concept_sync 活跃任务 (cache_clear 不显示此按钮)."""
        UILogger.log_action("DataSourceTab", "Click", "btn_cancel_active_task")
        vm.cancel_active_task()

    def _on_health_report_close() -> None:
        set_health_report_open(False)
        set_health_report_data({})

    def _on_deep_scan() -> None:
        set_scan_dialog_open(True)

    def _on_scan_close() -> None:
        set_scan_dialog_open(False)

    def _on_sync_now_from_health_report() -> None:
        """Task 5.3: 健康报告弹窗"立即同步" → 触发完整日更新 (修复深链)."""
        page = _get_page()
        if page is not None:
            page.run_task(_do_full_sync)

    def _on_init_data_from_scan() -> None:
        """Task 5.3: 深度扫描结果"初始化数据" → 触发初始化历史数据 (修复深链)."""
        page = _get_page()
        if page is not None:
            page.run_task(_do_init_historical)

    def _on_reset_ack_change(e: ft.ControlEvent) -> None:
        """重置确认框勾选变化 → 控制确认按钮可用性 (R16: 仅 set_state, 无同步阻塞)。"""
        set_reset_ack_checked(bool(get_control_value(e.control, ft.Checkbox)))

    def _on_open_backup_dialog(e: ft.ControlEvent) -> None:
        """「先导出备份」入口 → 打开承载既有 BackupRestorePanel 的子对话框。"""
        set_backup_dialog_open(True)

    def _on_close_backup_dialog() -> None:
        set_backup_dialog_open(False)

    def _on_confirm_dialog_close() -> None:
        set_confirm_dialog_config({})
        set_reset_ack_checked(False)

    def _on_confirm_dialog_confirm() -> None:
        if not confirm_dialog_config:
            return
        # MAJOR-03: 破坏性重置必须已勾选确认项 (防御纵深: 与按钮 disabled 双重守护)。
        if confirm_dialog_config.get("categories") and not reset_ack_checked:
            return
        callback = confirm_dialog_config.get("callback")
        set_confirm_dialog_config({})
        set_reset_ack_checked(False)
        page = _get_page()
        if page is not None and callback is not None:
            page.run_task(callback)

    # --- Mount: recover stale state + refresh scheduler status + reload tushare config ---
    def _on_mount() -> None:
        vm.recover_stale_state()
        # D7-6: 首帧拉取调度状态快照 (纯内存读, 不阻塞主循环)。
        vm.refresh_scheduler_status()
        tushare_vm.reload_config()

    ft.use_effect(_on_mount, dependencies=[])

    # --- Unmount: dispose tushare_vm (主 VM 由 use_viewmodel hook 自动 dispose) ---
    def _cleanup_tushare_vm() -> None:
        tushare_vm.dispose()

    ft.use_effect(lambda: None, dependencies=[], cleanup=_cleanup_tushare_vm)

    # --- Snack effect: state.snack → show_snack (瞬态通知, 监听 seq 变化触发回调) ---
    # L771 合规: 直接从 state.snack 读取 (frozen SnackRow), 无 vm.last_snack property 拉取.
    # seq 字段确保连续相同内容也触发 use_state setter 更新.
    # Task 5.1: snack.action_key 非空时附 action 按钮 (检查健康/重新探测积分).
    def _on_snack_change() -> None:
        snack = state.snack
        if snack is None:
            return
        text = I18n.get(snack.message.key, **snack.message.params)
        action_text: str | None = None
        on_action: Callable[[], None] | None = None
        if snack.action_key is not None:
            action_text = I18n.get(snack.action_key)
            if snack.action_key == "snack_action_check_health":

                def _go_check_health() -> None:
                    page = _get_page()
                    if page is not None:
                        page.run_task(_do_check_health)

                on_action = _go_check_health
            elif snack.action_key == "snack_action_probe_credits":

                def _go_probe_credits() -> None:
                    page = _get_page()
                    if page is not None:
                        page.run_task(_do_probe_credits)

                on_action = _go_probe_credits
        if show_snack_callback:
            show_snack_callback(
                text,
                color=_resolve_snack_color(snack.color_name),
                action_text=action_text,
                on_action=on_action,
            )

    ft.use_effect(_on_snack_change, dependencies=[state.snack.seq if state.snack else 0])

    # --- cache_cleared effect: state.cache_cleared_version → notify Observable (瞬时信号, 非 dual-track) ---
    def _on_cache_cleared_version_change() -> None:
        notify_cache_cleared()

    ft.use_effect(_on_cache_cleared_version_change, dependencies=[state.cache_cleared_version])

    # --- Build section cards (D15: 各区块为模块级纯函数, 从巨型组件中提出) ---
    health_dashboard = _build_health_dashboard(state, _on_check_health, _on_health_report_click)
    # D7-6: 调度状态面板 (紧跟健康看板, 展示各定时任务的上次成功/失败原因/下次计划/连续失败)。
    scheduler_status_card = _build_scheduler_status_card(state)
    action_console = _build_action_console(state, _on_full_sync, _on_ai_concept_rebuild, _on_cancel_active_task)
    danger_zone = _build_danger_zone(state, _on_clear_cache)
    connection_card = _build_connection_card(tushare_vm)
    historical_card = _build_historical_card(state, vm, _on_init_historical, _on_history_years_change, _on_init_cancel)
    data_flow_card = _build_data_flow_card()

    # --- Confirm dialog content (MAJOR-03: 破坏性重置逐项列出数据类别 + 勾选确认) ---
    confirm_categories = confirm_dialog_config.get("categories")
    if confirm_categories:
        confirm_content: ft.Control = ft.Column(
            [
                ft.Text(
                    I18n.get(confirm_dialog_config.get("content_key", "")),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(
                                    ft.Icons.CIRCLE,
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                    color=AppColors.ERROR,
                                ),
                                ft.Text(
                                    I18n.get(cat_key),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_PRIMARY,
                                ),
                            ],
                            spacing=8,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        )
                        for cat_key in confirm_categories
                    ],
                    spacing=6,
                ),
                ft.Text(
                    I18n.get("danger_reset_cat_schema_note"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.WARNING,
                ),
                ft.Checkbox(
                    label=I18n.get(confirm_dialog_config.get("checkbox_key", "")),
                    value=reset_ack_checked,
                    on_change=safe_on_change(_on_reset_ack_change),
                ),
                ft.TextButton(
                    I18n.get("dialog_reset_backup_entry"),
                    icon=ft.Icons.SAVE,
                    on_click=safe_on_click(_on_open_backup_dialog),
                ),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
        )
    else:
        confirm_content = ft.Text(I18n.get(confirm_dialog_config.get("content_key", "")))

    # 确认按钮可用性: 破坏性重置未勾选确认项时必须 disabled
    confirm_requires_ack = bool(confirm_categories) and not reset_ack_checked

    # --- Confirm dialog (use_dialog 无条件调用, 以 None/AlertDialog 切换显隐) ---
    # Flet hook 顺序必须跨渲染稳定: 禁止把 use_dialog 放进 if 块条件调用, 否则破坏
    # hook 状态导致 dialog 关闭无响应。
    confirm_dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get(confirm_dialog_config.get("title_key", ""))),
            content=confirm_content,
            actions=[
                ft.TextButton(I18n.get("common_cancel"), on_click=lambda e: _on_confirm_dialog_close()),
                ft.TextButton(
                    I18n.get(confirm_dialog_config.get("confirm_btn_key", "")),
                    on_click=lambda e: _on_confirm_dialog_confirm(),
                    disabled=confirm_requires_ack,
                    style=ft.ButtonStyle(color=AppColors.ERROR)
                    if confirm_dialog_config.get("is_destructive", False)
                    else ft.ButtonStyle(color=AppColors.PRIMARY),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if confirm_dialog_config
        else None
    )
    ft.use_dialog(confirm_dialog)

    # --- Backup dialog (MAJOR-03: 「先导出备份」复用既有 BackupRestorePanel) ---
    backup_dialog = (
        ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("danger_zone_backup_title")),
            content=ft.Container(content=BackupRestorePanel(), width=520),
            actions=[ft.TextButton(I18n.get("common_close"), on_click=lambda e: _on_close_backup_dialog())],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        if backup_dialog_open
        else None
    )
    ft.use_dialog(backup_dialog)

    return ft.Container(
        content=ft.ListView(
            controls=[
                health_dashboard,
                action_console,
                danger_zone,
                connection_card,
                historical_card,
                # D7-6: 调度状态面板插在 data_flow_card 之前。ListView 只构建进入视口的项，
                # 若插到靠前位置会把既有卡片（如 Tushare 配置「验证 Token」）推出视口导致
                # E2E 语义查询失败；此处保持前 5 张卡片顺序/可见性不变，并保留 data_flow_card
                # 作为列表末尾卡片（既有契约 test_data_flow_section_is_last_card_in_listview）。
                scheduler_status_card,
                data_flow_card,
                # 组件型 dialog 内部 use_dialog 无条件自挂载, 条件加入 controls 列表不影响
                # 父组件 hook 顺序; 仅在打开时实例化, 关闭即卸载 (open_state=True 每次推送)。
                *(
                    [
                        HealthReportDialog(
                            report=health_report_data,
                            page=_get_page(),
                            open_state=True,
                            on_close=_on_health_report_close,
                            on_deep_scan=_on_deep_scan,
                            on_sync_now=_on_sync_now_from_health_report,
                        )
                    ]
                    if (health_report_open and health_report_data)
                    else []
                ),
                *(
                    [
                        HealthScanDialog(
                            data_processor=vm.get_data_processor(),
                            page=_get_page(),
                            open_state=True,
                            on_close=_on_scan_close,
                            on_init_data=_on_init_data_from_scan,
                        )
                    ]
                    if scan_dialog_open
                    else []
                ),
            ],
            spacing=15,
            padding=ft.Padding.only(bottom=50),
        ),
        expand=True,
    )
