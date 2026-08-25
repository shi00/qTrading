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
- 回测状态下沉 VM（D2）：可用策略/选中策略/上次提交经 VM state（available_strategies / selected_strategy_key / last_run_summary）+ commands（select_strategy / record_last_run）受控管理
- no_strategy_error 为纯 UI 校验态（use_state）
"""

import logging

import flet as ft

from ui.components.backtest import BacktestConfigPanel, BacktestResultPanel
from ui.components.flet_type_helpers import get_control_value, safe_on_click, safe_on_select
from ui.components.resizable_splitter import ResizableSplitter
from ui.components.state_views import GITHUB_ISSUES_URL, ErrorState
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import BacktestViewModel, consume_pending_prefill
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
    # D2: strategies/selected_strategy/last_run 业务状态已下沉 VM state
    # (available_strategies / selected_strategy_key / last_run_summary);
    # View 仅保留纯 UI 校验态 no_strategy_error.
    no_strategy_error, set_no_strategy_error = ft.use_state(False)

    # Task 8.3: 选股→回测参数透传 — mount 时消费 pending prefill (strategy_key + params)
    # params 经 ref 透传到 run_backtest, 避免不必要的重渲染 (params 仅在 run 时读取)
    _prefilled_params = ft.use_ref(lambda: None)

    def _consume_prefill() -> None:
        prefill = consume_pending_prefill()
        if prefill is None:
            return
        strategy_key = prefill.get("strategy_key")
        # D2: 从 VM state 读取 available_strategies; 命中则 vm.select_strategy 受控更新
        # isinstance 收窄为 str (prefill value 为 dict[str, object])
        if isinstance(strategy_key, str) and any(k == strategy_key for k, _ in state.available_strategies):
            vm.select_strategy(strategy_key)
        _prefilled_params.current = prefill.get("params")

    ft.use_effect(_consume_prefill, dependencies=[])

    # --- Handlers ---
    def _on_strategy_change(e: ft.ControlEvent) -> None:
        UILogger.log_action("BacktestView", "Select", f"strategy={get_control_value(e.control, ft.Dropdown)}")
        # D2: 受控更新 — vm.select_strategy 下沉选中状态到 VM state
        vm.select_strategy(get_control_value(e.control, ft.Dropdown))
        set_no_strategy_error(False)

    def _on_run_backtest(config: dict) -> None:
        UILogger.log_action("BacktestView", "Click", "btn_run_backtest")
        if not state.selected_strategy_key:
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
                # D2: last_run 下沉 VM (vm.record_last_run), retry 时从 state.last_run_summary 读取
                vm.record_last_run(state.selected_strategy_key, backtest_config)
                page.run_task(vm.run_backtest, state.selected_strategy_key, backtest_config, _prefilled_params.current)
        except RuntimeError:
            logger.warning("[BacktestView] page not available for run_task")

    def _on_retry_backtest() -> None:
        """Task 11.3: ErrorState on_retry — 重新提交上次回测配置 (R16: page.run_task 调度).

        透传 _prefilled_params.current 与首次提交保持一致 (Task 8.3).
        D2: last_run 从 state.last_run_summary 读取 (已下沉 VM).
        """
        if state.last_run_summary is None:
            return
        strategy, backtest_config = state.last_run_summary
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(vm.run_backtest, strategy, backtest_config, _prefilled_params.current)
        except RuntimeError:
            logger.warning("[BacktestView] page not available for retry")

    def _on_cta_report() -> None:
        """Task 11.3: ErrorState on_cta — 打开 GitHub Issues.

        page.launch_url 为 async (被 @deprecated 装饰器破坏 iscoroutinefunction 检测,
        须用 async wrapper 包裹后通过 page.run_task 调度, R16).
        """
        try:
            page = ft.context.page
        except RuntimeError:
            logger.warning("[BacktestView] page not available for launch_url")
            return

        if page is not None:

            async def _open_issues() -> None:
                await page.launch_url(GITHUB_ISSUES_URL)

            page.run_task(_open_issues)

    def _on_cancel_backtest(e: ft.ControlEvent) -> None:
        UILogger.log_action("BacktestView", "Click", "btn_cancel_backtest")
        vm.cancel_backtest()

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

    strategy_dropdown = anchored(
        EIDS.BACKTEST.STRATEGY_DROPDOWN,
        ft.Dropdown(
            label=I18n.get("backtest_select_strategy"),
            # D16: 渲染时按当前 locale 翻译 name_key（组件已订阅 locale，切换自动重渲染）
            # D2: options/value 从 VM state 读取 (available_strategies / selected_strategy_key)
            options=[ft.dropdown.Option(key, I18n.get(name_key)) for key, name_key in state.available_strategies],
            value=state.selected_strategy_key,
            on_select=safe_on_select(_on_strategy_change),
            width=AppStyles.CONTROL_WIDTH_LG,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
        ),
    )

    status_text = ft.Text(status_value, color=status_color)
    progress_bar = ft.ProgressBar(visible=state.is_running, value=state.progress, expand=True)
    progress_text = ft.Text(progress_text_value, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY)
    cancel_button = anchored(
        EIDS.BACKTEST.CANCEL_BUTTON,
        ft.Button(
            content=I18n.get("common_cancel"),
            on_click=safe_on_click(_on_cancel_backtest),
            visible=state.is_running,
            style=AppStyles.danger_button(),  # P2-9: 替换 bgcolor/color 为 danger_button 统一风格
        ),
    )

    # Task 11.3: 回测执行失败 (status_color=="error" 且无结果) → ErrorState 替换结果面板;
    # no_strategy_error 是 View 本地 state, 无结果但 status_color 非 error, 不触发 ErrorState
    # D11: result 拆解为渲染字段, 以 metrics/trades 空判断"无结果"
    has_backtest_error = state.status_color == "error" and not state.metrics and not state.trades
    if has_backtest_error:
        right_content = ErrorState(
            icon=ft.Icons.ERROR_OUTLINE,
            title=I18n.get("backtest_failed_title"),
            message=I18n.get("backtest_failed_message"),
            detail=state.error_detail,
            on_retry=_on_retry_backtest,
            retry_text=I18n.get("common_retry"),
            on_cta=_on_cta_report,
            cta_text=I18n.get("error_state_contact_support"),
            cta_icon=ft.Icons.FEEDBACK,  # UX-03 (P2-09): 反馈问题语义匹配
        )
    else:
        right_content = BacktestResultPanel(
            metrics=state.metrics,
            trades=state.trades,
            nav_curve=state.nav_curve,
            ic_series=state.ic_series,
            period_stats=state.period_stats,
        )

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
