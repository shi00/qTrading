"""回测配置面板

提供回测参数配置界面：
- 日期范围
- 初始资金
- 调仓频率
- 费率设置（含印花税自动分段费率）
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import flet as ft

from ui.i18n import I18n, refresh_dropdown_options
from ui.theme import AppColors

logger = logging.getLogger(__name__)


class BacktestConfigPanel(ft.Container):
    """回测配置面板组件。"""

    def __init__(
        self,
        on_run_backtest,
        strategy_key: str | None = None,
    ):
        super().__init__(expand=True)
        self.on_run_backtest = on_run_backtest
        self._strategy_key = strategy_key

        today = date.today()
        one_year_ago = today - timedelta(days=365)

        self.start_date_picker = ft.DatePicker(
            first_date=date(2020, 1, 1),
            last_date=today,
            current_date=one_year_ago,
            on_change=self._on_start_date_change,
            help_text=I18n.get("date_picker_help"),
            cancel_text=I18n.get("common_cancel"),
            confirm_text=I18n.get("common_ok"),
            error_format_text=I18n.get("date_picker_error_format"),
            error_invalid_text=I18n.get("date_picker_error_invalid"),
        )
        self.end_date_picker = ft.DatePicker(
            first_date=date(2020, 1, 1),
            last_date=today,
            current_date=today,
            on_change=self._on_end_date_change,
            help_text=I18n.get("date_picker_help"),
            cancel_text=I18n.get("common_cancel"),
            confirm_text=I18n.get("common_ok"),
            error_format_text=I18n.get("date_picker_error_format"),
            error_invalid_text=I18n.get("date_picker_error_invalid"),
        )

        self.start_date_value = one_year_ago
        self.end_date_value = today

        self.start_date_btn = ft.OutlinedButton(
            text=one_year_ago.strftime("%Y-%m-%d"),
            icon=ft.Icons.CALENDAR_TODAY,
            on_click=self._show_start_picker,
        )
        self.end_date_btn = ft.OutlinedButton(
            text=today.strftime("%Y-%m-%d"),
            icon=ft.Icons.CALENDAR_TODAY,
            on_click=self._show_end_picker,
        )

        self.initial_capital_input = ft.TextField(
            label=I18n.get("backtest_initial_capital"),
            value="1000000",
            keyboard_type=ft.KeyboardType.NUMBER,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
        )

        self.rebalance_dropdown = ft.Dropdown(
            label=I18n.get("backtest_rebalance_freq"),
            options=[
                ft.dropdown.Option("signal", I18n.get("backtest_rebalance_signal")),
                ft.dropdown.Option("daily", I18n.get("backtest_rebalance_daily")),
                ft.dropdown.Option("weekly", I18n.get("backtest_rebalance_weekly")),
                ft.dropdown.Option("monthly", I18n.get("backtest_rebalance_monthly")),
            ],
            value="signal",
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
        )

        self.max_position_input = ft.TextField(
            label=I18n.get("backtest_max_positions"),
            value="50",
            keyboard_type=ft.KeyboardType.NUMBER,
            bgcolor=AppColors.INPUT_BG,
            border_color=AppColors.INPUT_BORDER,
            color=AppColors.INPUT_TEXT,
        )

        self.commission_slider = ft.Slider(
            min=0,
            max=10,
            divisions=10,
            value=3,
            label="{value}",
            expand=True,
        )
        self.commission_text = ft.Text("3‱", size=12, color=AppColors.TEXT_SECONDARY)

        self.stamp_duty_auto_checkbox = ft.Checkbox(
            label=I18n.get("backtest_stamp_duty_auto"),
            value=True,
            on_change=self._on_stamp_duty_auto_change,
        )
        self.stamp_duty_slider = ft.Slider(
            min=0,
            max=2,
            divisions=4,
            value=0.5,
            label="{value}",
            expand=True,
            disabled=True,
            on_change=self._on_stamp_duty_slider_change,
        )
        self.stamp_duty_text = ft.Text(
            I18n.get("backtest_stamp_duty_auto"),
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )

        self.slippage_slider = ft.Slider(
            min=0,
            max=20,
            divisions=20,
            value=5,
            label="{value}",
            expand=True,
        )
        self.slippage_text = ft.Text("5 bps", size=12, color=AppColors.TEXT_SECONDARY)

        self.run_btn = ft.ElevatedButton(
            text=I18n.get("backtest_run"),
            icon=ft.Icons.PLAY_ARROW,
            on_click=self._on_run_click,
            style=ft.ButtonStyle(
                bgcolor=AppColors.PRIMARY,
                color=AppColors.TEXT_ON_PRIMARY,
            ),
        )

        self.content = self._build_content()

    def did_mount(self):
        """挂载时：将对话框安全地绑定到全局 overlay"""
        super().did_mount()
        if self.page:
            if self.start_date_picker not in self.page.overlay:
                self.page.overlay.append(self.start_date_picker)
            if self.end_date_picker not in self.page.overlay:
                self.page.overlay.append(self.end_date_picker)
            self.page.update()

    def refresh_locale(self):
        """语言切换时刷新所有 I18n.get() 赋值的字段（纯 UI 操作）。"""
        try:
            self.start_date_picker.help_text = I18n.get("date_picker_help")
            self.start_date_picker.cancel_text = I18n.get("common_cancel")
            self.start_date_picker.confirm_text = I18n.get("common_ok")
            self.start_date_picker.error_format_text = I18n.get("date_picker_error_format")
            self.start_date_picker.error_invalid_text = I18n.get("date_picker_error_invalid")
            self.end_date_picker.help_text = I18n.get("date_picker_help")
            self.end_date_picker.cancel_text = I18n.get("common_cancel")
            self.end_date_picker.confirm_text = I18n.get("common_ok")
            self.end_date_picker.error_format_text = I18n.get("date_picker_error_format")
            self.end_date_picker.error_invalid_text = I18n.get("date_picker_error_invalid")
            self.initial_capital_input.label = I18n.get("backtest_initial_capital")
            self.rebalance_dropdown.label = I18n.get("backtest_rebalance_freq")
            refresh_dropdown_options(
                self.rebalance_dropdown,
                [
                    ft.dropdown.Option("signal", I18n.get("backtest_rebalance_signal")),
                    ft.dropdown.Option("daily", I18n.get("backtest_rebalance_daily")),
                    ft.dropdown.Option("weekly", I18n.get("backtest_rebalance_weekly")),
                    ft.dropdown.Option("monthly", I18n.get("backtest_rebalance_monthly")),
                ],
            )
            self.max_position_input.label = I18n.get("backtest_max_positions")
            self.stamp_duty_auto_checkbox.label = I18n.get("backtest_stamp_duty_auto")
            self.run_btn.text = I18n.get("backtest_run")
            # 重建 content 以刷新所有内联 ft.Text(I18n.get(...)) 文案
            self.content = self._build_content()
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[BacktestConfigPanel] refresh_locale error: %s", e, exc_info=True)

    def will_unmount(self):
        """卸载时：确保关闭并清理对话框"""
        super().will_unmount()
        if not self.page:
            return

        needs_update = False
        for picker in (self.start_date_picker, self.end_date_picker):
            if getattr(self, "page", None) and picker in self.page.overlay:
                picker.open = False  # 确保合规关闭，避免前端撕裂
                self.page.overlay.remove(picker)
                needs_update = True

        if needs_update:
            self.page.update()

    def _show_start_picker(self, e):
        if getattr(self, "page", None):
            self.page.open(self.start_date_picker)

    def _show_end_picker(self, e):
        if getattr(self, "page", None):
            self.page.open(self.end_date_picker)

    def _build_content(self) -> ft.Column:
        return ft.Column(
            [
                ft.Text(
                    I18n.get("backtest_config_title"),
                    size=18,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Text(
                    I18n.get("backtest_date_range"),
                    size=14,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [
                                ft.Text(I18n.get("backtest_start_date"), size=12, color=AppColors.TEXT_SECONDARY),
                                self.start_date_btn,
                            ],
                            spacing=4,
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [
                                ft.Text(I18n.get("backtest_end_date"), size=12, color=AppColors.TEXT_SECONDARY),
                                self.end_date_btn,
                            ],
                            spacing=4,
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ],
                    run_spacing=16,
                ),
                ft.Container(height=16),
                ft.Text(
                    I18n.get("backtest_portfolio_settings"),
                    size=14,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [self.initial_capital_input],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [self.rebalance_dropdown],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                        ft.Column(
                            [self.max_position_input],
                            col={"xs": 12, "sm": 6, "md": 4, "xl": 3},
                            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                        ),
                    ],
                    run_spacing=16,
                ),
                ft.Container(height=16),
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("backtest_fee_settings"), color=AppColors.TEXT_PRIMARY),
                    trailing=ft.Icon(ft.Icons.SETTINGS, color=AppColors.TEXT_SECONDARY),
                    controls=[
                        ft.Column(
                            [
                                ft.Row(
                                    [
                                        ft.Text(
                                            I18n.get("backtest_commission_rate"),
                                            size=12,
                                            color=AppColors.TEXT_SECONDARY,
                                        ),
                                        self.commission_slider,
                                        self.commission_text,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                                ft.Column(
                                    [
                                        self.stamp_duty_auto_checkbox,
                                        ft.Row(
                                            [
                                                ft.Text(
                                                    I18n.get("backtest_stamp_duty_rate"),
                                                    size=12,
                                                    color=AppColors.TEXT_SECONDARY,
                                                ),
                                                self.stamp_duty_slider,
                                                self.stamp_duty_text,
                                            ],
                                            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                        ),
                                    ],
                                    spacing=4,
                                ),
                                ft.Row(
                                    [
                                        ft.Text(I18n.get("backtest_slippage"), size=12, color=AppColors.TEXT_SECONDARY),
                                        self.slippage_slider,
                                        self.slippage_text,
                                    ],
                                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                                ),
                            ],
                            spacing=12,
                        ),
                    ],
                ),
                ft.Container(height=24),
                ft.Row([self.run_btn], alignment=ft.MainAxisAlignment.END),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )

    def _on_start_date_change(self, e):
        if e.control.value:
            self.start_date_value = e.control.value
            self.start_date_btn.text = self.start_date_value.strftime("%Y-%m-%d")
            self.start_date_btn.update()

    def _on_end_date_change(self, e):
        if e.control.value:
            self.end_date_value = e.control.value
            self.end_date_btn.text = self.end_date_value.strftime("%Y-%m-%d")
            self.end_date_btn.update()

    def _on_stamp_duty_auto_change(self, e):
        is_auto = e.control.value
        self.stamp_duty_slider.disabled = is_auto
        if is_auto:
            self.stamp_duty_text.value = I18n.get("backtest_stamp_duty_auto")
        else:
            slider_val = self.stamp_duty_slider.value or 0.5
            self.stamp_duty_text.value = f"{slider_val:.1f}‰"
        if self.page:
            self.stamp_duty_slider.update()
            self.stamp_duty_text.update()

    def _on_stamp_duty_slider_change(self, e):
        """滑块值变化时实时更新文案。"""
        if not self.stamp_duty_auto_checkbox.value:
            slider_val = e.control.value or 0.5
            self.stamp_duty_text.value = f"{slider_val:.1f}‰"
            if self.page:
                self.stamp_duty_text.update()

    def _on_run_click(self, e):
        if self.on_run_backtest:
            config = self.get_config()
            self.on_run_backtest(config)

    def get_config(self) -> dict:
        """获取当前配置。"""
        try:
            initial_capital = float(self.initial_capital_input.value or "1000000")
        except ValueError:
            initial_capital = 1_000_000.0

        try:
            max_positions = int(self.max_position_input.value or "50")
        except ValueError:
            max_positions = 50

        if self.stamp_duty_auto_checkbox.value:
            stamp_duty_rate = None
        else:
            stamp_duty_rate = self.stamp_duty_slider.value / 1000 if self.stamp_duty_slider.value else None

        return {
            "start_date": self.start_date_value,
            "end_date": self.end_date_value,
            "initial_capital": initial_capital,
            "rebalance_freq": self.rebalance_dropdown.value or "signal",
            "max_position_count": max_positions,
            "commission_rate": self.commission_slider.value / 10000 if self.commission_slider.value else 3e-4,
            "stamp_duty_rate": stamp_duty_rate,
            "slippage_bps": self.slippage_slider.value or 5.0,
        }

    def set_strategy_key(self, strategy_key: str):
        """设置当前策略。"""
        self._strategy_key = strategy_key
