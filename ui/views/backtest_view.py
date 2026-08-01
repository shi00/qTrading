"""回测视图

提供回测功能的完整界面：
- 策略选择
- 参数配置
- 结果展示

变更要点（Phase C.2）：
- 旧命令式 Container 子类 → ``@ft.component def BacktestView()``
- VM 通过 ``use_viewmodel(BacktestViewModel)`` 消费（state snapshot + commands）
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调 / 手动 locale 刷新 / 窗口尺寸回调 / 重新实例化推送 / 手动重绘
- BacktestConfigPanel/BacktestResultPanel 作为子组件函数直接调用，props 从 VM state 推送
- page 访问改用 ``ft.context.page``（try/except 守卫）
- selected_strategy/no_strategy_error 为 UI 局部状态（use_state）

Issue #448 改造:
- 回测失败 (status_color == "error") 时结果面板区域显示 ErrorState (含 details + 重试 + 联系支持)
- task_rejected (warning) 保留 status_text, 不显示 ErrorState
- 错误历史记录通过 use_effect 监听 state.status_color + state.error_details 变化触发
- 重试机制保存上次 strategy + config, _retry_backtest 调用 _on_run_backtest(last_config)
- cancel_backtest 不记录错误历史 (m5: 用户主动取消)
"""

import logging

import flet as ft

from ui.components.backtest import BacktestConfigPanel, BacktestResultPanel
from ui.components.error_history_store import open_github_issues, record_error
from ui.components.flet_type_helpers import get_control_value, safe_on_click, safe_on_select
from ui.components.resizable_splitter import ResizableSplitter
from ui.components.state_views import ErrorState
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import BacktestViewModel
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)

# VM 产出语义键 (error/warning/success/info), View 映射为 AppColors 实际颜色值 (§3.2 VM 不感知 UI 颜色).
_STATUS_COLOR_MAP = {
    "error": AppColors.ERROR,
    "warning": AppColors.WARNING,
    "success": AppColors.SUCCESS,
    "info": AppColors.INFO,
}


@ft.component
def BacktestView(active: bool = True) -> ft.Container:
    """回测视图（声明式）。

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(BacktestViewModel)``
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - BacktestConfigPanel/BacktestResultPanel 子组件 props 从 VM state 推送，
      state 变化自动重渲染（替代旧重新实例化推送模式）
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        active: 当前 tab 是否激活 (控制副作用执行)。
    """
    state, vm = use_viewmodel(BacktestViewModel)
    # 订阅 i18n + theme 变化（locale/theme 切换时自动重渲染）
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- UI local state ---
    strategies = ft.use_state(lambda: vm.get_available_strategies())[0]
    selected_strategy, set_selected_strategy = ft.use_state(lambda: next(iter(strategies), None))
    no_strategy_error, set_no_strategy_error = ft.use_state(False)

    # Issue #448: 上次运行的 strategy + config (供 ErrorState 重试使用)
    last_strategy_ref = ft.use_ref(lambda: None)
    last_config_ref = ft.use_ref(lambda: None)
    # Issue #448: 上一次 status_color (用于去重, 避免重复记录错误历史)
    previous_status_color_ref = ft.use_ref(lambda: "")

    # --- Handlers ---
    def _on_strategy_change(e: ft.ControlEvent) -> None:
        UILogger.log_action("BacktestView", "Select", f"strategy={get_control_value(e.control, ft.Dropdown)}")
        set_selected_strategy(get_control_value(e.control, ft.Dropdown))
        set_no_strategy_error(False)

    def _on_run_backtest(config: dict) -> None:
        UILogger.log_action("BacktestView", "Click", "btn_run_backtest")
        if not selected_strategy:
            set_no_strategy_error(True)
            return
        # Issue #448: 保存本次 strategy + config (供 _retry_backtest 使用)
        last_strategy_ref.current = selected_strategy
        last_config_ref.current = config
        backtest_config = vm.create_config(
            start_date=config["start_date"],
            end_date=config["end_date"],
            initial_capital=config["initial_capital"],
            rebalance_freq=config["rebalance_freq"],
            max_position_count=config["max_position_count"],
            commission_rate=config["commission_rate"],
            stamp_duty_rate=config["stamp_duty_rate"],
            slippage_bps=config["slippage_bps"],
        )
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.run_backtest, selected_strategy, backtest_config)
        except RuntimeError:
            logger.warning("[BacktestView] page not available for run_task")

    def _on_cancel_backtest(e: ft.ControlEvent) -> None:
        UILogger.log_action("BacktestView", "Click", "btn_cancel_backtest")
        vm.cancel_backtest()

    # Issue #448: 失败重试 (复用上次 strategy + config 重新运行)
    def _retry_backtest() -> None:
        strategy = last_strategy_ref.current
        config = last_config_ref.current
        if strategy and config:
            _on_run_backtest(config)

    # Issue #448: 回测失败记录错误历史 (use_effect 监听 state.status_color + error_details)
    # m5: cancel_backtest (status 变为 CANCELLED warning) 不触发错误记录,
    #     因 previous_status_color_ref 仅追踪 error 状态, cancel 时 status_color="warning" 不匹配
    def _record_backtest_error() -> None:
        current_color = state.status_color
        if current_color == "error" and current_color is not previous_status_color_ref.current:
            record_error(
                source="backtest",
                title=I18n.get("backtest_failed_title"),
                message=I18n.get("backtest_failed_message"),
                details=state.error_details,
            )
        previous_status_color_ref.current = current_color

    ft.use_effect(
        _record_backtest_error,
        dependencies=[state.status_color, state.error_details],
    )

    # --- Status / progress rendering (from VM state) ---
    if no_strategy_error and not state.is_running:
        status_value = I18n.get("backtest_no_strategy")
        status_color = AppColors.ERROR
    elif state.status_message is not None:
        status_value = I18n.get(state.status_message.key, **state.status_message.params)
        status_color = _STATUS_COLOR_MAP.get(state.status_color, AppColors.TEXT_SECONDARY)
    else:
        status_value = ""
        status_color = AppColors.TEXT_SECONDARY

    if state.progress_message is not None:
        progress_text_value = I18n.get(state.progress_message.key, **state.progress_message.params)
    else:
        progress_text_value = ""

    # --- Controls ---
    title_text = ft.Text(
        I18n.get("backtest_view_title"),
        size=AppStyles.FONT_SIZE_XL,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    strategy_dropdown = ft.Dropdown(
        label=I18n.get("backtest_select_strategy"),
        options=[ft.dropdown.Option(key, name) for key, name in strategies.items()],
        value=selected_strategy,
        on_select=safe_on_select(_on_strategy_change),
        width=AppStyles.CONTROL_WIDTH_LG,
        bgcolor=AppColors.INPUT_BG,
        border_color=AppColors.INPUT_BORDER,
        color=AppColors.INPUT_TEXT,
    )

    status_text = ft.Text(status_value, color=status_color)
    progress_bar = ft.ProgressBar(visible=state.is_running, value=state.progress, expand=True)
    progress_text = ft.Text(progress_text_value, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY)
    cancel_button = ft.Button(
        content=I18n.get("common_cancel"),
        on_click=safe_on_click(_on_cancel_backtest),
        visible=state.is_running,
        style=AppStyles.danger_button(),  # P2-9: 替换 bgcolor/color 为 danger_button 统一风格
    )

    # Issue #448: 右侧面板 — error 时显示 ErrorState (保留配置面板供修改参数重试);
    # warning (task_rejected) 保留 BacktestResultPanel, status_text 已展示警告
    if state.status_color == "error" and state.result is None:
        right_content = ErrorState(
            icon=ft.Icons.ERROR_OUTLINE,
            title=I18n.get("backtest_failed_title"),
            message=I18n.get("backtest_failed_message"),
            details=state.error_details,
            on_retry=_retry_backtest,
            retry_text=I18n.get("common_retry"),
            on_contact_support=open_github_issues,
            contact_text=I18n.get("common_contact_support"),
        )
    else:
        right_content = BacktestResultPanel(result=state.result)

    return ft.Container(
        content=ft.Column(
            [
                ft.Row([title_text], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Row([strategy_dropdown, status_text], spacing=16),
                ft.Row([progress_bar, progress_text, cancel_button], spacing=8),
                ft.Container(height=16),
                ResizableSplitter(
                    left_content=BacktestConfigPanel(on_run_backtest=_on_run_backtest),
                    right_content=right_content,
                    config_key="ui_splitter_backtest_config",
                    default_width=360,
                    min_width=280,
                    max_width=600,
                    on_load_width=lambda: vm.get_splitter_width("ui_splitter_backtest_config", 360),
                    on_persist_width=lambda w: vm.persist_splitter_width("ui_splitter_backtest_config", w),
                ),
            ],
            spacing=12,
            expand=True,
        ),
        expand=True,
    )
