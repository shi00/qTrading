"""回测视图

提供回测功能的完整界面：
- 策略选择
- 参数配置
- 结果展示
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from ui.components.backtest import BacktestConfigPanel, BacktestResultPanel
from ui.components.resizable_splitter import ResizableSplitter
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import BacktestViewModel
from utils.log_decorators import UILogger

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestConfig, BacktestResult

logger = logging.getLogger(__name__)


class BacktestView(ft.Container):
    """回测视图。"""

    def __init__(self, page: ft.Page):
        super().__init__(expand=True)
        self._page_ref = page

        self.vm = BacktestViewModel()
        self._selected_strategy: str | None = None
        self._locale_subscription_id: object | None = None

        self.title_text = ft.Text(
            I18n.get("backtest_view_title"),
            size=24,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

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
        self.progress_bar = ft.ProgressBar(visible=False, expand=True)
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

    def did_mount(self):
        super().did_mount()
        if self.page:
            try:
                self.update()
            except Exception as ex:
                logger.warning("[BacktestView] did_mount update skipped: %s", ex, exc_info=True)
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作，禁止 IO）。"""
        try:
            self.title_text.value = I18n.get("backtest_view_title")
            self.strategy_dropdown.label = I18n.get("backtest_select_strategy")
            saved_strategy = self.strategy_dropdown.value
            self.strategy_dropdown.value = None  # 强制触发 dirty（Flet 对相等值短路，§5.8 规范 4）
            strategies = self.vm.get_available_strategies()
            self.strategy_dropdown.options = [ft.dropdown.Option(key, name) for key, name in strategies.items()]
            self.strategy_dropdown.value = saved_strategy
            self.cancel_button.text = I18n.get("common_cancel")
            if hasattr(self.config_panel, "refresh_locale"):
                self.config_panel.refresh_locale()
            if hasattr(self.result_panel, "refresh_locale"):
                self.result_panel.refresh_locale()
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[BacktestView] refresh_locale error: %s", e, exc_info=True)

    def _build_content(self) -> ft.Column:
        return ft.Column(
            [
                ft.Row(
                    [self.title_text],
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
                ResizableSplitter(
                    left_content=self.config_panel,
                    right_content=self.result_panel,
                    config_key="ui_splitter_backtest_config",
                    default_width=360,
                    min_width=280,
                    max_width=600,
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _fixed_vertical_chrome_height(self) -> int:
        """估算视图固定纵向 chrome 高度（不含 splitter 内部内容）。

        估算依据：
        - nav_rail 高度 56
        - tabs 高度 40
        - body 上下 padding 32
        - splitter 自身 padding 16
        - 其他留白 16
        合计 ≈ 160
        """
        return 160

    def handle_resize(self, width: float = 0, height: float = 0) -> None:
        """窗口尺寸变化时调整高度维度布局。

        width 参数由 splitter 管理宽度维度，此处忽略；仅处理 height 维度：
        根据 available_height 与 COMPACT_HEIGHT_THRESHOLD 的关系，调整图表最小高度。
        """
        if height <= 0:
            return
        try:
            # 懒导入避免与 app_layout.py 的循环导入（app_layout 顶部导入 BacktestView）
            from ui.app_layout import COMPACT_HEIGHT_THRESHOLD

            available_height = max(0, height - self._fixed_vertical_chrome_height())
            chart_min_height = 240 if available_height < COMPACT_HEIGHT_THRESHOLD else 360
            if self.result_panel is not None:
                self.result_panel.set_chart_min_height(chart_min_height)
        except Exception as e:
            logger.debug("[BacktestView] handle_resize skipped: %s", e)

    def _load_strategies(self):
        """加载可用策略列表。"""
        strategies = self.vm.get_available_strategies()
        self.strategy_dropdown.options = [ft.dropdown.Option(key, name) for key, name in strategies.items()]
        if strategies:
            first_key = next(iter(strategies.keys()))
            self.strategy_dropdown.value = first_key
            self._selected_strategy = first_key
            self.config_panel.set_strategy_key(first_key)

    def _on_strategy_change(self, e):
        """策略选择变更。"""
        self._selected_strategy = e.control.value
        UILogger.log_action("BacktestView", "Select", f"strategy={self._selected_strategy}")
        self.config_panel.set_strategy_key(self._selected_strategy)

    def _on_run_backtest(self, config: dict):
        """运行回测按钮点击。"""
        UILogger.log_action("BacktestView", "Click", "btn_run_backtest")
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
        UILogger.log_action("BacktestView", "Click", "btn_cancel_backtest")
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
        self.will_unmount()
        self.vm.dispose()
