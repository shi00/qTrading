"""回测结果展示面板（声明式 V1）。

展示回测结果：
- 指标卡片
- 净值曲线图表
- 交易明细表格
- IC 序列图表
- 月度统计表格

变更要点（Phase 3.2.6 + D11）：
- 旧命令式 Container 子类 → ``@ft.component def BacktestResultPanel(metrics, trades, nav_curve, ic_series, period_stats)``
- 纯展示组件（接收渲染就绪 props，D11 不再接收 BacktestResult 领域对象），按 project_memory
  责任分层原则用 ``use_state`` 管理 trades_page/selected_tab（UI 局部状态），不建 VM（YAGNI）
- i18n 通过 ``ft.use_state(get_observable_state)`` 订阅自动重渲染
- 移除命令式生命周期回调、手动 update、手动 locale 刷新、set_result 方法
- 消费方 BacktestView 通过重新实例化推送 props（过渡期，Task 3.6.3 BacktestView 声明式
  重写后改为父组件 state 驱动）
- 提取模块级纯函数（颜色判断/metric_card/各子构建器），可独立单测
"""

from __future__ import annotations

import logging
import typing
from collections.abc import Callable

import flet as ft
import flet_charts as fch

from ui.components.flet_type_helpers import safe_controls
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.backtest_view_model import TradeRow

logger = logging.getLogger(__name__)

_TRADES_PAGE_SIZE = 50

# AppStyles.COL_QUARTER 推断为 dict[str, int]，与 ft.ResponsiveNumber 不兼容，cast 一次复用
_COL_QUARTER = typing.cast("ft.ResponsiveNumber", AppStyles.COL_QUARTER)


# --- Pure color helpers (可独立单测) ---


def _get_color_for_value(value: float) -> str:
    if value > 0:
        return AppColors.SUCCESS
    elif value < 0:
        return AppColors.ERROR
    return AppColors.TEXT_PRIMARY


def _get_color_for_sharpe(sharpe: float) -> str:
    if sharpe >= 1.5:
        return AppColors.SUCCESS
    elif sharpe >= 0.5:
        return AppColors.WARNING
    elif sharpe < 0:
        return AppColors.ERROR
    return AppColors.TEXT_PRIMARY


def _get_color_for_ic(ic: float) -> str:
    if abs(ic) >= 0.05:
        return AppColors.SUCCESS if ic > 0 else AppColors.ERROR
    return AppColors.TEXT_PRIMARY


# --- Pure builders (接收必要参数，无 self 依赖) ---


def _metric_card(label: str, value: str, value_color: str) -> ft.Container:
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(label, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(value, size=AppStyles.FONT_SIZE_HEADLINE, weight=ft.FontWeight.BOLD, color=value_color),
            ],
            spacing=4,
        ),
        padding=AppStyles.SPACING_MD,
        bgcolor=AppColors.CARD_BG,
        border_radius=8,
    )


def _build_metrics_section(metrics: dict) -> ft.Column:
    row1 = ft.ResponsiveRow(
        controls=safe_controls(
            [
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_total_return"),
                        f"{metrics.get('total_return', 0) * 100:.2f}%",
                        _get_color_for_value(metrics.get("total_return", 0)),
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_annual_return"),
                        f"{metrics.get('annualized_return', 0) * 100:.2f}%",
                        _get_color_for_value(metrics.get("annualized_return", 0)),
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_sharpe"),
                        f"{metrics.get('sharpe_ratio', 0):.2f}",
                        _get_color_for_sharpe(metrics.get("sharpe_ratio", 0)),
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_max_dd"),
                        f"{metrics.get('max_drawdown', 0) * 100:.2f}%",
                        AppColors.ERROR if metrics.get("max_drawdown", 0) > 0.2 else AppColors.WARNING,
                    ),
                    col=_COL_QUARTER,
                ),
            ]
        ),
        spacing=AppStyles.SPACING_MD,
        run_spacing=AppStyles.SPACING_MD,
    )

    row2 = ft.ResponsiveRow(
        controls=safe_controls(
            [
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_profit_factor"),
                        f"{metrics.get('profit_factor', 0):.2f}",
                        AppColors.SUCCESS if metrics.get("profit_factor", 0) > 1 else AppColors.ERROR,
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_ic_mean"),
                        f"{metrics.get('ic_mean', 0):.4f}",
                        _get_color_for_ic(metrics.get("ic_mean", 0)),
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_ic_ir"),
                        f"{metrics.get('ic_ir', 0):.2f}",
                        _get_color_for_ic(metrics.get("ic_ir", 0)),
                    ),
                    col=_COL_QUARTER,
                ),
                ft.Container(
                    content=_metric_card(
                        I18n.get("backtest_metric_total_trades"),
                        f"{metrics.get('total_trades', 0)}",
                        AppColors.TEXT_PRIMARY,
                    ),
                    col=_COL_QUARTER,
                ),
            ]
        ),
        spacing=AppStyles.SPACING_MD,
        run_spacing=AppStyles.SPACING_MD,
    )

    return ft.Column(
        [
            ft.Text(
                I18n.get("backtest_metrics_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            row1,
            row2,
        ],
        spacing=12,
    )


def _build_empty_content() -> ft.Column:
    return ft.Column(
        [
            ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.ASSESSMENT, size=AppStyles.ICON_SIZE_XXL, color=AppColors.TEXT_SECONDARY),
                        ft.Text(
                            I18n.get("backtest_no_result"),
                            size=AppStyles.FONT_SIZE_TITLE,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            I18n.get("backtest_run_hint"),
                            size=AppStyles.FONT_SIZE_BODY_SM,
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


def _build_nav_chart(nav_curve: tuple[float, ...]) -> ft.Container:
    if not nav_curve:
        return ft.Container(
            content=ft.Text(I18n.get("backtest_no_nav_data"), color=AppColors.TEXT_SECONDARY),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    chart_data = [
        fch.LineChartData(
            points=[fch.LineChartDataPoint(x=i, y=float(v)) for i, v in enumerate(nav_curve)],
            color=AppColors.PRIMARY,
            stroke_width=2,
        )
    ]

    container = ft.Container(
        content=fch.LineChart(
            data_series=chart_data,
            border=ft.Border.all(1, AppColors.DIVIDER),
            left_axis=fch.ChartAxis(label_size=50),
            bottom_axis=fch.ChartAxis(label_size=40),
            expand=True,
        ),
        padding=AppStyles.SPACING_LG,
        expand=True,
    )
    return container


def _build_trades_table(
    trades: tuple[TradeRow, ...],
    trades_page: int,
    set_trades_page: Callable[[int], None],
) -> ft.Container:
    if not trades:
        return ft.Container(
            content=ft.Text(I18n.get("backtest_no_trades"), color=AppColors.TEXT_SECONDARY),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    total_rows = len(trades)
    total_pages = max(1, (total_rows + _TRADES_PAGE_SIZE - 1) // _TRADES_PAGE_SIZE)
    start = trades_page * _TRADES_PAGE_SIZE
    end = min(start + _TRADES_PAGE_SIZE, total_rows)

    columns = [
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_date"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_code"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_action"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_price"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_volume"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_pnl"), color=AppColors.TEXT_PRIMARY)),
    ]

    rows = []
    for trade in trades[start:end]:
        action = trade.action
        action_color = AppColors.SUCCESS if action == "buy" else AppColors.ERROR
        pnl = trade.realized_pnl
        pnl_color = AppColors.SUCCESS if pnl > 0 else AppColors.ERROR if pnl < 0 else AppColors.TEXT_PRIMARY

        rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(trade.trade_date, color=AppColors.TEXT_PRIMARY)),
                    ft.DataCell(ft.Text(trade.ts_code, color=AppColors.TEXT_PRIMARY)),
                    ft.DataCell(ft.Text(action, color=action_color)),
                    ft.DataCell(ft.Text(f"{trade.price:.2f}", color=AppColors.TEXT_PRIMARY)),
                    ft.DataCell(ft.Text(f"{trade.volume:,}", color=AppColors.TEXT_PRIMARY)),
                    ft.DataCell(ft.Text(f"{pnl:.2f}", color=pnl_color)),
                ]
            )
        )

    page_info = ft.Text(
        f"{start + 1}-{end} / {total_rows}",
        size=AppStyles.FONT_SIZE_BODY_SM,
        color=AppColors.TEXT_SECONDARY,
    )

    def _prev_page(e):
        if trades_page > 0:
            set_trades_page(trades_page - 1)

    def _next_page(e):
        if trades_page < total_pages - 1:
            set_trades_page(trades_page + 1)

    pagination = ft.Row(
        [
            ft.IconButton(
                ft.Icons.NAVIGATE_BEFORE,
                on_click=_prev_page,
                disabled=trades_page == 0,
                tooltip=I18n.get("common_prev_page"),
            ),
            page_info,
            ft.IconButton(
                ft.Icons.NAVIGATE_NEXT,
                on_click=_next_page,
                disabled=trades_page >= total_pages - 1,
                tooltip=I18n.get("common_next_page"),
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=8,
    )

    return ft.Container(
        content=ft.Column(
            controls=safe_controls(
                [
                    ft.DataTable(
                        columns=columns,
                        rows=rows,
                        heading_row_color=AppColors.TABLE_HEADER_BG,
                        data_row_color={ft.ControlState.HOVERED: AppColors.TABLE_ROW_HOVER},
                        border=ft.Border.all(1, AppColors.DIVIDER),
                        vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
                    ),
                    pagination,
                ]
            ),
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        padding=AppStyles.SPACING_LG,
        expand=True,
    )


def _build_ic_chart(ic_series: tuple[float, ...]) -> ft.Container:
    if not ic_series:
        return ft.Container(
            content=ft.Text(I18n.get("backtest_no_ic_data"), color=AppColors.TEXT_SECONDARY),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    bars = []
    for i, ic in enumerate(ic_series):
        color = AppColors.SUCCESS if ic > 0 else AppColors.ERROR if ic < 0 else AppColors.TEXT_SECONDARY
        bars.append(
            fch.BarChartGroup(
                x=i,
                rods=[fch.BarChartRod(from_y=0, to_y=float(ic), color=color, width=8)],
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
        padding=AppStyles.SPACING_LG,
        expand=True,
    )
    return container


def _build_monthly_table(
    period_stats: tuple[tuple[str, float, float, float], ...],
) -> ft.Container:
    if not period_stats:
        return ft.Container(
            content=ft.Text(I18n.get("backtest_no_monthly_data"), color=AppColors.TEXT_SECONDARY),
            alignment=ft.Alignment.CENTER,
            expand=True,
        )

    columns = [
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_month"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_return"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_benchmark"), color=AppColors.TEXT_PRIMARY)),
        ft.DataColumn(label=ft.Text(I18n.get("backtest_col_excess"), color=AppColors.TEXT_PRIMARY)),
    ]

    rows = []
    for year_month, monthly_ret, bench_ret, excess in period_stats:
        ret_color = (
            AppColors.SUCCESS if monthly_ret > 0 else AppColors.ERROR if monthly_ret < 0 else AppColors.TEXT_PRIMARY
        )
        excess_color = AppColors.SUCCESS if excess > 0 else AppColors.ERROR if excess < 0 else AppColors.TEXT_PRIMARY

        rows.append(
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(year_month, color=AppColors.TEXT_PRIMARY)),
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
            data_row_color={ft.ControlState.HOVERED: AppColors.TABLE_ROW_HOVER},
            border=ft.Border.all(1, AppColors.DIVIDER),
            vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
        ),
        padding=AppStyles.SPACING_LG,
        expand=True,
    )


def _build_content(
    metrics: tuple[tuple[str, float], ...],
    trades: tuple[TradeRow, ...],
    nav_curve: tuple[float, ...],
    ic_series: tuple[float, ...],
    period_stats: tuple[tuple[str, float, float, float], ...],
    trades_page: int,
    set_trades_page: Callable[[int], None],
    selected_tab: int,
    set_selected_tab: Callable[[int], None],
) -> ft.Column:
    """组装回测结果内容（metrics + Tabs 三件套）。

    D11: 消费渲染就绪 props（metrics/trades/nav_curve/ic_series/period_stats），
    不再接收 BacktestResult 领域对象。
    """
    # V1 三件套：selected_index/on_change 在 ft.Tabs 上（ft.TabBar 无这两个参数，
    # 仅有 on_click/on_hover；Flet 0.85.3 API 已验证）
    tab_bar = ft.TabBar(
        tabs=[
            ft.Tab(label=I18n.get("backtest_tab_nav_curve")),
            ft.Tab(label=I18n.get("backtest_tab_trades")),
            ft.Tab(label=I18n.get("backtest_tab_ic_series")),
            ft.Tab(label=I18n.get("backtest_tab_monthly")),
        ],
    )

    return ft.Column(
        [
            _build_metrics_section(dict(metrics)),
            ft.Divider(color=AppColors.DIVIDER),
            ft.Tabs(
                length=4,
                selected_index=selected_tab,
                animation_duration=300,
                expand=True,
                on_change=lambda e: set_selected_tab(e.control.selected_index),
                content=ft.Column(
                    expand=True,
                    controls=[
                        tab_bar,
                        ft.TabBarView(
                            expand=True,
                            controls=[
                                _build_nav_chart(nav_curve),
                                _build_trades_table(trades, trades_page, set_trades_page),
                                _build_ic_chart(ic_series),
                                _build_monthly_table(period_stats),
                            ],
                        ),
                    ],
                ),
            ),
        ],
        spacing=12,
        expand=True,
    )


@ft.component
def BacktestResultPanel(
    metrics: tuple[tuple[str, float], ...] = (),
    trades: tuple[TradeRow, ...] = (),
    nav_curve: tuple[float, ...] = (),
    ic_series: tuple[float, ...] = (),
    period_stats: tuple[tuple[str, float, float, float], ...] = (),
) -> ft.Container:
    """回测结果展示面板（声明式）。

    CLAUDE.md §3.2 MVVM + §3.3 声明式范式：
    - 纯展示组件（接收渲染就绪 props），用 ``use_state`` 管理
      trades_page/selected_tab（UI 局部状态），不建 VM（YAGNI）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        metrics/trades/nav_curve/ic_series/period_stats: BacktestState 渲染就绪字段
            （D11 拆解，源自 BacktestResult；全部为空时显示空状态）
    """
    # --- Subscribe to i18n changes (auto-rerender on locale switch) ---
    ft.use_state(get_observable_state)

    # --- UI local state (trades pagination + selected tab) ---
    trades_page, set_trades_page = ft.use_state(0)
    selected_tab, set_selected_tab = ft.use_state(0)

    if not metrics and not trades and not nav_curve:
        content = _build_empty_content()
    else:
        content = _build_content(
            metrics,
            trades,
            nav_curve,
            ic_series,
            period_stats,
            trades_page,
            set_trades_page,
            selected_tab,
            set_selected_tab,
        )

    return ft.Container(
        expand=True,
        content=content,
    )
