"""回测结果展示面板

展示回测结果：
- 指标卡片
- 净值曲线图表
- 交易明细表格
- IC 序列图表
- 月度统计表格
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestResult

logger = logging.getLogger(__name__)


class BacktestResultPanel(ft.Container):
    """回测结果展示面板。"""

    def __init__(self):
        super().__init__(expand=True)
        self._result: BacktestResult | None = None
        self.content = self._build_empty_content()

    def set_result(self, result: BacktestResult):
        """设置回测结果并刷新界面。"""
        self._result = result
        self.content = self._build_content()
        if self.page:
            self.update()

    def _build_empty_content(self) -> ft.Column:
        return ft.Column(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.ASSESSMENT, size=64, color=AppColors.TEXT_SECONDARY),
                            ft.Text(
                                I18n.get("backtest_no_result"),
                                size=16,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            ft.Text(
                                I18n.get("backtest_run_hint"),
                                size=12,
                                color=AppColors.TEXT_HINT,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=16,
                    ),
                    expand=True,
                    alignment=ft.alignment.center,
                ),
            ],
            expand=True,
        )

    def _build_content(self) -> ft.Column:
        if not self._result:
            return self._build_empty_content()

        metrics = self._result.metrics

        return ft.Column(
            [
                self._build_metrics_section(metrics),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Tabs(
                    selected_index=0,
                    animation_duration=300,
                    tabs=[
                        ft.Tab(
                            text=I18n.get("backtest_tab_nav_curve"),
                            content=self._build_nav_chart(),
                        ),
                        ft.Tab(
                            text=I18n.get("backtest_tab_trades"),
                            content=self._build_trades_table(),
                        ),
                        ft.Tab(
                            text=I18n.get("backtest_tab_ic_series"),
                            content=self._build_ic_chart(),
                        ),
                        ft.Tab(
                            text=I18n.get("backtest_tab_monthly"),
                            content=self._build_monthly_table(),
                        ),
                    ],
                    expand=True,
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _build_metrics_section(self, metrics: dict) -> ft.Column:
        row1 = ft.Row(
            [
                self._metric_card(
                    I18n.get("backtest_metric_total_return"),
                    f"{metrics.get('total_return', 0) * 100:.2f}%",
                    self._get_color_for_value(metrics.get("total_return", 0)),
                ),
                self._metric_card(
                    I18n.get("backtest_metric_annual_return"),
                    f"{metrics.get('annualized_return', 0) * 100:.2f}%",
                    self._get_color_for_value(metrics.get("annualized_return", 0)),
                ),
                self._metric_card(
                    I18n.get("backtest_metric_sharpe"),
                    f"{metrics.get('sharpe_ratio', 0):.2f}",
                    self._get_color_for_sharpe(metrics.get("sharpe_ratio", 0)),
                ),
                self._metric_card(
                    I18n.get("backtest_metric_max_dd"),
                    f"{metrics.get('max_drawdown', 0) * 100:.2f}%",
                    AppColors.ERROR if metrics.get("max_drawdown", 0) > 0.2 else AppColors.WARNING,
                ),
            ],
            spacing=12,
            wrap=True,
        )

        row2 = ft.Row(
            [
                self._metric_card(
                    I18n.get("backtest_metric_calmar"),
                    f"{metrics.get('calmar_ratio', 0):.2f}",
                    AppColors.TEXT_PRIMARY,
                ),
                self._metric_card(
                    I18n.get("backtest_metric_ic_mean"),
                    f"{metrics.get('ic_mean', 0):.4f}",
                    self._get_color_for_ic(metrics.get("ic_mean", 0)),
                ),
                self._metric_card(
                    I18n.get("backtest_metric_ic_ir"),
                    f"{metrics.get('ic_ir', 0):.2f}",
                    self._get_color_for_ic(metrics.get("ic_ir", 0)),
                ),
                self._metric_card(
                    I18n.get("backtest_metric_win_rate"),
                    f"{metrics.get('win_rate', 0) * 100:.1f}%",
                    AppColors.SUCCESS if metrics.get("win_rate", 0) > 0.5 else AppColors.TEXT_PRIMARY,
                ),
            ],
            spacing=12,
            wrap=True,
        )

        return ft.Column(
            [
                ft.Text(
                    I18n.get("backtest_metrics_title"),
                    size=16,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                row1,
                row2,
            ],
            spacing=12,
        )

    def _metric_card(self, label: str, value: str, value_color: str) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [
                    ft.Text(label, size=11, color=AppColors.TEXT_SECONDARY),
                    ft.Text(value, size=18, weight=ft.FontWeight.BOLD, color=value_color),
                ],
                spacing=4,
            ),
            padding=12,
            bgcolor=AppColors.CARD_BG,
            border_radius=8,
            width=150,
        )

    def _get_color_for_value(self, value: float) -> str:
        if value > 0:
            return AppColors.SUCCESS
        elif value < 0:
            return AppColors.ERROR
        return AppColors.TEXT_PRIMARY

    def _get_color_for_sharpe(self, sharpe: float) -> str:
        if sharpe >= 1.5:
            return AppColors.SUCCESS
        elif sharpe >= 0.5:
            return AppColors.WARNING
        elif sharpe < 0:
            return AppColors.ERROR
        return AppColors.TEXT_PRIMARY

    def _get_color_for_ic(self, ic: float) -> str:
        if abs(ic) >= 0.05:
            return AppColors.SUCCESS if ic > 0 else AppColors.ERROR
        return AppColors.TEXT_PRIMARY

    def _build_nav_chart(self) -> ft.Container:
        if not self._result or self._result.nav_curve.is_empty():
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_nav_data"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.alignment.center,
                expand=True,
            )

        nav_df = self._result.nav_curve
        nav_values = nav_df["nav"].to_list()

        chart_data = [
            ft.LineChartData(
                data_points=[ft.LineChartDataPoint(x=i, y=float(v)) for i, v in enumerate(nav_values)],
                color=AppColors.PRIMARY,
                stroke_width=2,
            )
        ]

        return ft.Container(
            content=ft.LineChart(
                data_series=chart_data,
                border=ft.border.all(1, AppColors.DIVIDER),
                left_axis=ft.ChartAxis(
                    labels_size=50,
                ),
                bottom_axis=ft.ChartAxis(
                    labels_size=40,
                ),
                expand=True,
            ),
            padding=16,
            expand=True,
        )

    def _build_trades_table(self) -> ft.Container:
        if not self._result or self._result.trades.is_empty():
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_trades"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.alignment.center,
                expand=True,
            )

        trades_df = self._result.trades
        columns = [
            ft.DataColumn(ft.Text(I18n.get("backtest_col_date"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_code"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_action"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_price"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_volume"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_pnl"), color=AppColors.TEXT_PRIMARY)),
        ]

        rows = []
        for row in trades_df.iter_rows(named=True):
            action = row.get("action", "")
            action_color = AppColors.SUCCESS if action == "buy" else AppColors.ERROR
            pnl = row.get("realized_pnl", 0)
            pnl_color = AppColors.SUCCESS if pnl > 0 else AppColors.ERROR if pnl < 0 else AppColors.TEXT_PRIMARY

            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(row.get("trade_date", "")), color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(str(row.get("ts_code", "")), color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(action, color=action_color)),
                        ft.DataCell(ft.Text(f"{row.get('price', 0):.2f}", color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(f"{row.get('volume', 0):,}", color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(f"{pnl:.2f}", color=pnl_color)),
                    ]
                )
            )

        return ft.Container(
            content=ft.DataTable(
                columns=columns,
                rows=rows[:100],
                heading_row_color=AppColors.TABLE_HEADER_BG,
                data_row_color={"hovered": AppColors.TABLE_ROW_HOVER},
                border=ft.border.all(1, AppColors.DIVIDER),
                vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
            ),
            padding=16,
            expand=True,
        )

    def _build_ic_chart(self) -> ft.Container:
        if not self._result or len(self._result.ic_series) == 0:
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_ic_data"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.alignment.center,
                expand=True,
            )

        ic_values = self._result.ic_series.to_list()

        bars = []
        for i, ic in enumerate(ic_values):
            color = AppColors.SUCCESS if ic > 0 else AppColors.ERROR if ic < 0 else AppColors.TEXT_SECONDARY
            bars.append(
                ft.BarChartGroup(
                    x=i,
                    bar_rods=[
                        ft.BarChartRod(
                            from_y=0,
                            to_y=float(ic),
                            color=color,
                            width=8,
                        )
                    ],
                )
            )

        return ft.Container(
            content=ft.BarChart(
                bar_groups=bars[:50],
                border=ft.border.all(1, AppColors.DIVIDER),
                left_axis=ft.ChartAxis(labels_size=50),
                bottom_axis=ft.ChartAxis(labels_size=40),
                expand=True,
            ),
            padding=16,
            expand=True,
        )

    def _build_monthly_table(self) -> ft.Container:
        if not self._result or self._result.period_stats.is_empty():
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_monthly_data"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.alignment.center,
                expand=True,
            )

        stats_df = self._result.period_stats
        columns = [
            ft.DataColumn(ft.Text(I18n.get("backtest_col_month"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_return"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_benchmark"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_excess"), color=AppColors.TEXT_PRIMARY)),
        ]

        rows = []
        for row in stats_df.iter_rows(named=True):
            monthly_ret = row.get("monthly_return", 0) or 0
            bench_ret = row.get("benchmark_return", 0) or 0
            excess = row.get("excess_return", 0) or 0

            ret_color = (
                AppColors.SUCCESS if monthly_ret > 0 else AppColors.ERROR if monthly_ret < 0 else AppColors.TEXT_PRIMARY
            )
            excess_color = (
                AppColors.SUCCESS if excess > 0 else AppColors.ERROR if excess < 0 else AppColors.TEXT_PRIMARY
            )

            rows.append(
                ft.DataRow(
                    cells=[
                        ft.DataCell(ft.Text(str(row.get("year_month", "")), color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(f"{monthly_ret * 100:.2f}%", color=ret_color)),
                        ft.DataCell(ft.Text(f"{bench_ret * 100:.2f}%", color=AppColors.TEXT_PRIMARY)),
                        ft.DataCell(ft.Text(f"{excess * 100:.2f}%", color=excess_color)),
                    ]
                )
            )

        return ft.Container(
            content=ft.DataTable(
                columns=columns,
                rows=rows,
                heading_row_color=AppColors.TABLE_HEADER_BG,
                data_row_color={"hovered": AppColors.TABLE_ROW_HOVER},
                border=ft.border.all(1, AppColors.DIVIDER),
                vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
            ),
            padding=16,
            expand=True,
        )
