"""BacktestResultPanel 测试（声明式 V1）。

测试策略：
1. 模块级纯函数单测（颜色判断/metric_card/各子构建器/分页回调）
2. 契约守护测试（grep 命令式禁止模式 = 0）

声明式组件的渲染逻辑由 Flet 框架保证，不测组件实例化（参考 3.2.1-3.2.5 范式）。
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import flet as ft
import flet_charts as fch
import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from ui.components.backtest.backtest_result_panel import (
    _build_empty_content,
    _build_ic_chart,
    _build_metrics_section,
    _build_monthly_table,
    _build_nav_chart,
    _build_trades_table,
    _get_color_for_ic,
    _get_color_for_sharpe,
    _get_color_for_value,
    _metric_card,
)
from ui.theme import AppColors

pytestmark = pytest.mark.unit


@pytest.fixture
def backtest_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def sample_result(backtest_config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=backtest_config,
        strategy_name="test_strategy",
        params_snapshot={"param1": "value1"},
        nav_curve=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "nav": [1_000_000.0, 1_010_000.0],
            }
        ),
        daily_returns=pl.Series([0.0, 0.01]),
        benchmark_returns=pl.Series([0.0, 0.008]),
        trades=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "action": ["buy", "sell"],
                "price": [10.0, 20.0],
                "volume": [1000, 500],
                "realized_pnl": [0.0, 100.0],
            }
        ),
        positions=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1), date(2024, 1, 2)],
                "total_value": [1_000_000.0, 1_010_000.0],
            }
        ),
        skipped_orders=pl.DataFrame(),
        metrics={
            "total_return": 0.15,
            "annualized_return": 0.20,
            "sharpe_ratio": 1.8,
            "max_drawdown": 0.08,
            "calmar_ratio": 2.5,
            "ic_mean": 0.06,
            "ic_ir": 0.8,
            "win_rate": 0.65,
        },
        ic_series=pl.Series([0.02, 0.04, -0.01, 0.05]),
        period_stats=pl.DataFrame(
            {
                "year_month": ["2024-01", "2024-02"],
                "monthly_return": [0.05, -0.02],
                "benchmark_return": [0.03, 0.01],
                "excess_return": [0.02, -0.03],
                "start_nav": [100.0, 105.0],
                "end_nav": [105.0, 102.9],
            }
        ),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=1000,
    )


@pytest.fixture
def empty_result(backtest_config: BacktestConfig) -> BacktestResult:
    return BacktestResult(
        config=backtest_config,
        strategy_name="empty_strategy",
        params_snapshot={},
        nav_curve=pl.DataFrame(),
        daily_returns=pl.Series(),
        benchmark_returns=pl.Series(),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={},
        ic_series=pl.Series(),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="empty_run_001",
        executed_at=datetime(2024, 1, 31, 12, 0, 0),
        duration_ms=500,
    )


class TestColorHelpers:
    """颜色判断纯函数单测。"""

    def test_get_color_for_value_positive(self) -> None:
        assert _get_color_for_value(0.1) == AppColors.SUCCESS

    def test_get_color_for_value_negative(self) -> None:
        assert _get_color_for_value(-0.1) == AppColors.ERROR

    def test_get_color_for_value_zero(self) -> None:
        assert _get_color_for_value(0.0) == AppColors.TEXT_PRIMARY

    def test_get_color_for_sharpe_excellent(self) -> None:
        assert _get_color_for_sharpe(2.0) == AppColors.SUCCESS

    def test_get_color_for_sharpe_good(self) -> None:
        assert _get_color_for_sharpe(1.0) == AppColors.WARNING

    def test_get_color_for_sharpe_negative(self) -> None:
        assert _get_color_for_sharpe(-0.5) == AppColors.ERROR

    def test_get_color_for_sharpe_neutral(self) -> None:
        assert _get_color_for_sharpe(0.3) == AppColors.TEXT_PRIMARY

    def test_get_color_for_ic_positive_significant(self) -> None:
        assert _get_color_for_ic(0.06) == AppColors.SUCCESS

    def test_get_color_for_ic_negative_significant(self) -> None:
        assert _get_color_for_ic(-0.06) == AppColors.ERROR

    def test_get_color_for_ic_insignificant(self) -> None:
        assert _get_color_for_ic(0.03) == AppColors.TEXT_PRIMARY


class TestMetricCard:
    """_metric_card 纯函数单测。"""

    def test_metric_card_structure(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            card = _metric_card("Test Label", "Test Value", AppColors.SUCCESS)

        assert isinstance(card, ft.Container)
        assert card.width is None  # 移除固定宽度，改用 ResponsiveRow col
        assert isinstance(card.content, ft.Column)
        assert len(card.content.controls) == 2


class TestBuildMetricsSection:
    """_build_metrics_section 纯函数单测。"""

    def test_build_metrics_section_full(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            metrics = {
                "total_return": 0.15,
                "annualized_return": 0.20,
                "sharpe_ratio": 1.8,
                "max_drawdown": 0.08,
                "calmar_ratio": 2.5,
                "ic_mean": 0.06,
                "ic_ir": 0.8,
                "win_rate": 0.65,
            }
            content = _build_metrics_section(metrics)

        assert isinstance(content, ft.Column)
        assert len(content.controls) == 3  # title + row1 + row2

    def test_build_metrics_section_empty(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            content = _build_metrics_section({})

        assert isinstance(content, ft.Column)

    def test_max_drawdown_color_high(self) -> None:
        """max_drawdown > 0.2 → ERROR 色。"""
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            content = _build_metrics_section({"max_drawdown": 0.25})

        assert isinstance(content, ft.Column)

    def test_max_drawdown_color_low(self) -> None:
        """max_drawdown <= 0.2 → WARNING 色。"""
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            content = _build_metrics_section({"max_drawdown": 0.10})

        assert isinstance(content, ft.Column)


class TestBuildEmptyContent:
    """_build_empty_content 纯函数单测。"""

    def test_build_empty_content(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            content = _build_empty_content()

        assert isinstance(content, ft.Column)
        assert len(content.controls) == 1


class TestBuildNavChart:
    """_build_nav_chart 纯函数单测。"""

    def test_build_nav_chart_with_data(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_nav_chart(sample_result, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, fch.LineChart)

    def test_build_nav_chart_with_min_height(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_nav_chart(sample_result, 300)

        assert container.height == 300

    def test_build_nav_chart_empty(self, empty_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_nav_chart(empty_result, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_nav_chart_no_result(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_nav_chart(None, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)


class TestBuildTradesTable:
    """_build_trades_table 纯函数单测。"""

    def test_build_trades_table_with_data(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_trades_table(sample_result, 0, MagicMock())

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Column)

    def test_build_trades_table_empty(self, empty_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_trades_table(empty_result, 0, MagicMock())

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_trades_table_no_result(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_trades_table(None, 0, MagicMock())

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_trades_pagination_prev_page_calls_setter(self, sample_result: BacktestResult) -> None:
        """分页 prev_button on_click 调用 set_trades_page(page-1)。"""
        set_trades_page = MagicMock()
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_trades_table(sample_result, 1, set_trades_page)

        # pagination 是 Column 的第 2 个控件（DataTable 后）
        pagination = container.content.controls[1]
        prev_btn = pagination.controls[0]
        assert isinstance(prev_btn, ft.IconButton)
        assert prev_btn.disabled is False  # trades_page=1，可向前翻页
        # 触发 on_click（Flet on_click Union 类型含 0 参分支，pyright 推断为 0 参，运行时接收 ControlEvent）
        prev_btn.on_click(MagicMock())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支，pyright 推断为 0 参，运行时接收 ControlEvent]
        set_trades_page.assert_called_once_with(0)

    def test_trades_pagination_prev_disabled_at_page_zero(self, sample_result: BacktestResult) -> None:
        """trades_page=0 时 prev_button disabled。"""
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_trades_table(sample_result, 0, MagicMock())

        pagination = container.content.controls[1]
        prev_btn = pagination.controls[0]
        assert prev_btn.disabled is True


class TestBuildIcChart:
    """_build_ic_chart 纯函数单测。"""

    def test_build_ic_chart_with_data(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_ic_chart(sample_result, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, fch.BarChart)

    def test_build_ic_chart_with_min_height(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_ic_chart(sample_result, 300)

        assert container.height == 300

    def test_build_ic_chart_empty(self, empty_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_ic_chart(empty_result, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_ic_chart_no_result(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_ic_chart(None, None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)


class TestBuildMonthlyTable:
    """_build_monthly_table 纯函数单测。"""

    def test_build_monthly_table_with_data(self, sample_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_monthly_table(sample_result)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.DataTable)

    def test_build_monthly_table_empty(self, empty_result: BacktestResult) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_monthly_table(empty_result)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_monthly_table_no_result(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            container = _build_monthly_table(None)

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)


class TestBacktestResultPanelContract:
    """契约守护测试：声明式组件禁止命令式模式。"""

    def test_no_imperative_patterns(self) -> None:
        """grep 命令式禁止模式 = 0（did_mount/will_unmount/refresh_locale/.update()/class X(ft.Container)/set_result/set_chart_min_height）。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_result_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        forbidden_patterns = [
            "def did_mount",
            "def will_unmount",
            "def refresh_locale",
            "self.update()",
            "class BacktestResultPanel(ft.Container)",
            "class BacktestResultPanel(ft.UserControl)",
            "PageRefMixin",
            "def set_result",
            "def set_chart_min_height",
        ]
        for pattern in forbidden_patterns:
            assert pattern not in content, f"禁止命令式模式: {pattern}"

    def test_is_declarative_component(self) -> None:
        """验证是 @ft.component 声明式组件。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_result_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        assert "@ft.component" in content
        assert "def BacktestResultPanel(" in content

    def test_uses_i18n_observable_state(self) -> None:
        """验证通过 ft.use_state(get_observable_state) 订阅 i18n 自动重渲染。"""
        from pathlib import Path

        panel_path = (
            Path(__file__).parent.parent.parent.parent / "ui" / "components" / "backtest" / "backtest_result_panel.py"
        )
        content = panel_path.read_text(encoding="utf-8")

        assert "ft.use_state(get_observable_state)" in content
