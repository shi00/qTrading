"""回测视图

提供回测功能的完整界面：
- 策略选择
- 参数配置
- 结果展示
"""

from __future__ import annotations

import logging

import flet as ft

from strategies.backtest.config import BacktestConfig, BacktestResult
from ui.components.backtest import BacktestConfigPanel, BacktestResultPanel
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import BacktestViewModel

logger = logging.getLogger(__name__)


class BacktestView(ft.Container):
    """回测视图。"""

    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self._page_ref = page

        self.vm = BacktestViewModel()
        self._selected_strategy: str | None = None

        self.strategy_dropdown = ft.Dropdown(
            label=I18n.get("backtest_select_strategy"),
            options=[],
            on_change=self._on_strategy_change,
            width=AppStyles.CONTROL_WIDTH_LG,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
        )

        self.status_text = ft.Text("", color=AppColors.TEXT_SECONDARY)
        self.progress_bar = ft.ProgressBar(visible=False, width=400)
        self.progress_text = ft.Text("", size=12, color=AppColors.TEXT_SECONDARY)

        self.cancel_button = ft.ElevatedButton(
            text=I18n.get("common_cancel"),
            on_click=self._on_cancel_backtest,
            visible=False,
            bgcolor=AppColors.ERROR,
            color=ft.Colors.WHITE,
        )

        self.config_panel = BacktestConfigPanel(on_run_backtest=self._on_run_backtest)
        self.result_panel = BacktestResultPanel()

        self.vm.bind(
            on_update=self._on_vm_update,
            on_status=self._on_vm_status,
            on_progress=self._on_vm_progress,
            on_result=self._on_vm_result,
        )

        self.content = self._build_content()
        self._load_strategies()

    def _build_content(self) -> ft.Column:
        return ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            I18n.get("backtest_view_title"),
                            size=24,
                            weight=ft.FontWeight.BOLD,
                            color=AppColors.TEXT_PRIMARY,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Row(
                    [
                        self.strategy_dropdown,
                        self.status_text,
                    ],
                    spacing=16,
                ),
                ft.Row([self.progress_bar, self.progress_text, self.cancel_button], spacing=8),
                ft.Container(height=16),
                ft.Row(
                    [
                        ft.Container(
                            content=self.config_panel,
                            width=400,
                            expand=False,
                        ),
                        ft.VerticalDivider(width=1, color=AppColors.DIVIDER),
                        ft.Container(
                            content=self.result_panel,
                            expand=True,
                        ),
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _load_strategies(self):
        """加载可用策略列表。"""
        strategies = self.vm.get_available_strategies()
        self.strategy_dropdown.options = [ft.dropdown.Option(key, name) for key, name in strategies.items()]
        if strategies:
            first_key = next(iter(strategies.keys()))
            self.strategy_dropdown.value = first_key
            self._selected_strategy = first_key
            self.config_panel.set_strategy_key(first_key)
        if self.page:
            self.update()

    def _on_strategy_change(self, e):
        """策略选择变更。"""
        self._selected_strategy = e.control.value
        self.config_panel.set_strategy_key(self._selected_strategy)

    def _on_run_backtest(self, config: dict):
        """运行回测按钮点击。"""
        if not self._selected_strategy:
            self.status_text.value = I18n.get("backtest_no_strategy")
            self.status_text.color = AppColors.ERROR
            self.update()
            return

        self.progress_bar.visible = True
        self.progress_bar.value = 0
        self.cancel_button.visible = True
        self.status_text.value = I18n.get("backtest_starting")
        self.status_text.color = AppColors.PRIMARY
        self.update()

        backtest_config = self.vm.create_config(
            start_date=config["start_date"],
            end_date=config["end_date"],
            initial_capital=config["initial_capital"],
            rebalance_freq=config["rebalance_freq"],
            max_position_count=config["max_position_count"],
            commission_rate=config["commission_rate"],
            stamp_duty_rate=config["stamp_duty_rate"],
            slippage_bps=config["slippage_bps"],
        )

        self.page.run_task(
            self._start_backtest,
            self._selected_strategy,
            backtest_config,
        )

    async def _start_backtest(self, strategy_key: str, config: BacktestConfig):
        await self.vm.run_backtest(strategy_key, config)

    def _on_vm_update(self):
        """ViewModel 更新回调。"""
        if self.page:
            self.update()

    def _on_vm_status(self, message: str, color: str):
        self.status_text.value = message
        self.status_text.color = color
        if not self.vm.is_running:
            self.cancel_button.visible = False
        if self.page:
            self.update()

    def _on_cancel_backtest(self, e):
        self.vm.cancel_backtest()
        self.cancel_button.visible = False
        self.status_text.value = I18n.get("common_cancelling")
        self.status_text.color = AppColors.WARNING
        if self.page:
            self.update()

    def _on_vm_progress(self, progress: float, message: str):
        """进度更新回调。"""
        self.progress_bar.value = progress
        self.progress_text.value = message
        if self.page:
            self.update()

    def _on_vm_result(self, result: BacktestResult):
        self.result_panel.set_result(result)
        self.progress_bar.visible = False
        self.cancel_button.visible = False
        if self.page:
            self.update()

    def dispose(self):
        """清理资源。"""
        self.vm.dispose()
