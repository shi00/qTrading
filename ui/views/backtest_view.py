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
"""

import logging

import flet as ft

from ui.components.backtest import BacktestConfigPanel, BacktestResultPanel
from ui.components.resizable_splitter import ResizableSplitter
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import BacktestViewModel
from utils.log_decorators import UILogger

logger = logging.getLogger(__name__)


@ft.component
def BacktestView() -> ft.Container:
    """回测视图（声明式）。

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - state + commands via ``use_viewmodel(BacktestViewModel)``
    - i18n/theme via ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - BacktestConfigPanel/BacktestResultPanel 子组件 props 从 VM state 推送，
      state 变化自动重渲染（替代旧重新实例化推送模式）
    - 无 page ref / 生命周期回调 / 手动刷新
    """
    state, vm = use_viewmodel(BacktestViewModel)
    # 订阅 i18n + theme 变化（locale/theme 切换时自动重渲染）
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- UI local state ---
    strategies = ft.use_state(lambda: vm.get_available_strategies())[0]
    selected_strategy, set_selected_strategy = ft.use_state(lambda: next(iter(strategies), None))
    no_strategy_error, set_no_strategy_error = ft.use_state(False)

    # --- Handlers ---
    def _on_strategy_change(e: ft.ControlEvent) -> None:
        UILogger.log_action("BacktestView", "Select", f"strategy={e.control.value}")
        set_selected_strategy(e.control.value)
        set_no_strategy_error(False)

    def _on_run_backtest(config: dict) -> None:
        UILogger.log_action("BacktestView", "Click", "btn_run_backtest")
        if not selected_strategy:
            set_no_strategy_error(True)
            return
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

    # --- Status / progress rendering (from VM state) ---
    if no_strategy_error and not state.is_running:
        status_value = I18n.get("backtest_no_strategy")
        status_color = AppColors.ERROR
    elif state.status_message is not None:
        status_value = I18n.get(state.status_message.key, **state.status_message.params)
        status_color = state.status_color or AppColors.TEXT_SECONDARY
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
        size=24,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )

    strategy_dropdown = ft.Dropdown(
        label=I18n.get("backtest_select_strategy"),
        options=[ft.dropdown.Option(key, name) for key, name in strategies.items()],
        value=selected_strategy,
        on_select=_on_strategy_change,
        width=AppStyles.CONTROL_WIDTH_LG,
        bgcolor=AppColors.INPUT_BG,
        border_color=AppColors.INPUT_BORDER,
        color=AppColors.INPUT_TEXT,
    )

    status_text = ft.Text(status_value, color=status_color)
    progress_bar = ft.ProgressBar(visible=state.is_running, value=state.progress, expand=True)
    progress_text = ft.Text(progress_text_value, size=12, color=AppColors.TEXT_SECONDARY)
    cancel_button = ft.Button(
        content=I18n.get("common_cancel"),
        on_click=_on_cancel_backtest,
        visible=state.is_running,
        bgcolor=AppColors.ERROR,
        color=ft.Colors.WHITE,
    )

    # NOTE(lazy): chart_min_height 固定为 None（移除窗口尺寸命令式回调）。
    # 图表容器 expand=True 自动填充，丢失紧凑模式(240)/标准模式(360)的高度切换。
    # ceiling: 窗口尺寸响应式重设计. upgrade: app_layout 声明式重写已完成(Phase G.1), page 尺寸响应式 state 待独立任务实现.
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
                    right_content=BacktestResultPanel(result=vm.result, chart_min_height=None),
                    config_key="ui_splitter_backtest_config",
                    default_width=360,
                    min_width=280,
                    max_width=600,
                ),
            ],
            spacing=12,
            expand=True,
        ),
        expand=True,
    )
