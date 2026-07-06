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
import flet_charts as fch

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles

if TYPE_CHECKING:
    from strategies.backtest.config import BacktestResult

logger = logging.getLogger(__name__)


class BacktestResultPanel(ft.Container):
    """回测结果展示面板。"""

    def __init__(self):
        super().__init__(expand=True)
        self._result: BacktestResult | None = None
        self._trades_page: int = 0
        self._trades_page_size: int = 50
        self._chart_min_height: int | None = None
        self._chart_containers: list[ft.Container] = []
        self.content = self._build_empty_content()

    def set_result(self, result: BacktestResult):
        """设置回测结果并刷新界面。"""
        self._result = result
        self.content = self._build_content()
        if self.page:
            self.update()

    def set_chart_min_height(self, min_height: int) -> None:
        """设置图表区的最小高度（用于高度维度响应式调整）。

        局部更新已构建的图表容器 height，并记住该值供下次 _build_content 应用。
        若图表尚未构建（无结果），仅记值，下次构建时生效。
        """
        self._chart_min_height = min_height
        for container in self._chart_containers:
            container.height = min_height
        if self.page:
            try:
                self.update()
            except Exception as e:
                logger.debug("[BacktestResultPanel] set_chart_min_height update skipped: %s", e)

    def refresh_locale(self):
        """语言切换时刷新界面（纯 UI 操作）。

        若已有结果，重建 content 以刷新所有 I18n.get() 文案；
        若为空状态，重建 empty_content 刷新提示文案。
        """
        try:
            self.content = self._build_content()
            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[BacktestResultPanel] refresh_locale error: %s", e, exc_info=True)

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
                    alignment=ft.Alignment.CENTER,
                ),
            ],
            expand=True,
        )

    def _build_content(self) -> ft.Column:
        if not self._result:
            return self._build_empty_content()

        self._chart_containers = []
        metrics = self._result.metrics

        return ft.Column(
            [
                self._build_metrics_section(metrics),
                ft.Divider(color=AppColors.DIVIDER),
                ft.Tabs(
                    length=4,
                    selected_index=0,
                    animation_duration=300,
                    expand=True,
                    content=ft.Column(
                        expand=True,
                        controls=[
                            ft.TabBar(
                                tabs=[
                                    ft.Tab(label=I18n.get("backtest_tab_nav_curve")),
                                    ft.Tab(label=I18n.get("backtest_tab_trades")),
                                    ft.Tab(label=I18n.get("backtest_tab_ic_series")),
                                    ft.Tab(label=I18n.get("backtest_tab_monthly")),
                                ],
                            ),
                            ft.TabBarView(
                                expand=True,
                                controls=[
                                    self._build_nav_chart(),
                                    self._build_trades_table(),
                                    self._build_ic_chart(),
                                    self._build_monthly_table(),
                                ],
                            ),
                        ],
                    ),
                ),
            ],
            spacing=12,
            expand=True,
        )

    def _build_metrics_section(self, metrics: dict) -> ft.Column:
        row1 = ft.ResponsiveRow(
            [
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_total_return"),
                        f"{metrics.get('total_return', 0) * 100:.2f}%",
                        self._get_color_for_value(metrics.get("total_return", 0)),
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_annual_return"),
                        f"{metrics.get('annualized_return', 0) * 100:.2f}%",
                        self._get_color_for_value(metrics.get("annualized_return", 0)),
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_sharpe"),
                        f"{metrics.get('sharpe_ratio', 0):.2f}",
                        self._get_color_for_sharpe(metrics.get("sharpe_ratio", 0)),
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_max_dd"),
                        f"{metrics.get('max_drawdown', 0) * 100:.2f}%",
                        AppColors.ERROR if metrics.get("max_drawdown", 0) > 0.2 else AppColors.WARNING,
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
            ],
            spacing=AppStyles.SPACING_MD,
            run_spacing=AppStyles.SPACING_MD,
        )

        row2 = ft.ResponsiveRow(
            [
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_profit_factor"),
                        f"{metrics.get('profit_factor', 0):.2f}",
                        AppColors.SUCCESS if metrics.get("profit_factor", 0) > 1 else AppColors.ERROR,
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_ic_mean"),
                        f"{metrics.get('ic_mean', 0):.4f}",
                        self._get_color_for_ic(metrics.get("ic_mean", 0)),
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_ic_ir"),
                        f"{metrics.get('ic_ir', 0):.2f}",
                        self._get_color_for_ic(metrics.get("ic_ir", 0)),
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
                ft.Container(
                    content=self._metric_card(
                        I18n.get("backtest_metric_total_trades"),
                        f"{metrics.get('total_trades', 0)}",
                        AppColors.TEXT_PRIMARY,
                    ),
                    col=AppStyles.COL_QUARTER,
                ),
            ],
            spacing=AppStyles.SPACING_MD,
            run_spacing=AppStyles.SPACING_MD,
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
                alignment=ft.Alignment.CENTER,
                expand=True,
            )

        nav_df = self._result.nav_curve
        nav_values = nav_df["nav"].to_list()

        chart_data = [
            fch.LineChartData(
                points=[fch.LineChartDataPoint(x=i, y=float(v)) for i, v in enumerate(nav_values)],
                color=AppColors.PRIMARY,
                stroke_width=2,
            )
        ]

        container = ft.Container(
            content=fch.LineChart(
                data_series=chart_data,
                border=ft.Border.all(1, AppColors.DIVIDER),
                left_axis=fch.ChartAxis(
                    label_size=50,
                ),
                bottom_axis=fch.ChartAxis(
                    label_size=40,
                ),
                expand=True,
            ),
            padding=16,
            expand=True,
        )
        if self._chart_min_height is not None:
            container.height = self._chart_min_height
        self._chart_containers.append(container)
        return container

    def _build_trades_table(self) -> ft.Container:
        if not self._result or self._result.trades.is_empty():
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_trades"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.Alignment.CENTER,
                expand=True,
            )

        trades_df = self._result.trades
        total_rows = len(trades_df)
        total_pages = max(1, (total_rows + self._trades_page_size - 1) // self._trades_page_size)
        start = self._trades_page * self._trades_page_size
        end = min(start + self._trades_page_size, total_rows)

        columns = [
            ft.DataColumn(ft.Text(I18n.get("backtest_col_date"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_code"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_action"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_price"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_volume"), color=AppColors.TEXT_PRIMARY)),
            ft.DataColumn(ft.Text(I18n.get("backtest_col_pnl"), color=AppColors.TEXT_PRIMARY)),
        ]

        rows = []
        for row in trades_df[start:end].iter_rows(named=True):
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

        page_info = ft.Text(
            f"{start + 1}-{end} / {total_rows}",
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )

        def _prev_page(e):
            if self._trades_page > 0:
                self._trades_page -= 1
                self.content = self._build_content()
                self.update()

        def _next_page(e):
            if self._trades_page < total_pages - 1:
                self._trades_page += 1
                self.content = self._build_content()
                self.update()

        pagination = ft.Row(
            [
                ft.IconButton(ft.Icons.NAVIGATE_BEFORE, on_click=_prev_page, disabled=self._trades_page == 0),
                page_info,
                ft.IconButton(
                    ft.Icons.NAVIGATE_NEXT, on_click=_next_page, disabled=self._trades_page >= total_pages - 1
                ),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=8,
        )

        return ft.Container(
            content=ft.Column(
                [
                    ft.DataTable(
                        columns=columns,
                        rows=rows,
                        heading_row_color=AppColors.TABLE_HEADER_BG,
                        data_row_color={"hovered": AppColors.TABLE_ROW_HOVER},
                        border=ft.Border.all(1, AppColors.DIVIDER),
                        vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
                    ),
                    pagination,
                ],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            padding=16,
            expand=True,
        )

    def _build_ic_chart(self) -> ft.Container:
        if not self._result or len(self._result.ic_series) == 0:
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_ic_data"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.Alignment.CENTER,
                expand=True,
            )

        ic_values = self._result.ic_series.to_list()

        bars = []
        for i, ic in enumerate(ic_values):
            color = AppColors.SUCCESS if ic > 0 else AppColors.ERROR if ic < 0 else AppColors.TEXT_SECONDARY
            bars.append(
                fch.BarChartGroup(
                    x=i,
                    rods=[
                        fch.BarChartRod(
                            from_y=0,
                            to_y=float(ic),
                            color=color,
                            width=8,
                        )
                    ],
                )
            )

        container = ft.Container(
            content=fch.BarChart(
                groups=bars,
                border=ft.Border.all(1, AppColors.DIVIDER),
                left_axis=fch.ChartAxis(label_size=50),
                bottom_axis=fch.ChartAxis(label_size=40),
                expand=True,
            ),
            padding=16,
            expand=True,
        )
        if self._chart_min_height is not None:
            container.height = self._chart_min_height
        self._chart_containers.append(container)
        return container

    def _build_monthly_table(self) -> ft.Container:
        if not self._result or self._result.period_stats.is_empty():
            return ft.Container(
                content=ft.Text(I18n.get("backtest_no_monthly_data"), color=AppColors.TEXT_SECONDARY),
                alignment=ft.Alignment.CENTER,
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
                border=ft.Border.all(1, AppColors.DIVIDER),
                vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
            ),
            padding=16,
            expand=True,
        )
