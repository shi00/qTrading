"""BacktestResultPanel 单元测试"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import flet as ft
import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from ui.components.backtest.backtest_result_panel import BacktestResultPanel
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


@pytest.fixture
def panel() -> BacktestResultPanel:
    with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
        mock_i18n.return_value = "mock_text"
        return BacktestResultPanel()


class TestBacktestResultPanel:
    def test_init(self) -> None:
        with patch("ui.components.backtest.backtest_result_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            panel = BacktestResultPanel()

        assert panel._result is None
        assert isinstance(panel.content, ft.Column)

    def test_set_result_with_data(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel.page = MagicMock()
        panel.update = MagicMock()

        panel.set_result(sample_result)

        assert panel._result == sample_result
        assert isinstance(panel.content, ft.Column)
        panel.update.assert_called_once()

    def test_set_result_without_page(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel.page = None

        panel.set_result(sample_result)

        assert panel._result == sample_result

    def test_build_empty_content(self, panel: BacktestResultPanel) -> None:
        content = panel._build_empty_content()

        assert isinstance(content, ft.Column)
        assert len(content.controls) == 1

    def test_build_content_with_result(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel._result = sample_result

        content = panel._build_content()

        assert isinstance(content, ft.Column)
        assert len(content.controls) == 3

    def test_build_content_without_result(self, panel: BacktestResultPanel) -> None:
        panel._result = None

        content = panel._build_content()

        assert isinstance(content, ft.Column)

    def test_build_metrics_section(self, panel: BacktestResultPanel) -> None:
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

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)
        assert len(content.controls) == 3

    def test_metric_card(self, panel: BacktestResultPanel) -> None:
        card = panel._metric_card("Test Label", "Test Value", AppColors.SUCCESS)

        assert isinstance(card, ft.Container)
        assert card.width == 150

    def test_get_color_for_value_positive(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_value(0.1)
        assert color == AppColors.SUCCESS

    def test_get_color_for_value_negative(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_value(-0.1)
        assert color == AppColors.ERROR

    def test_get_color_for_value_zero(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_value(0.0)
        assert color == AppColors.TEXT_PRIMARY

    def test_get_color_for_sharpe_excellent(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_sharpe(2.0)
        assert color == AppColors.SUCCESS

    def test_get_color_for_sharpe_good(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_sharpe(1.0)
        assert color == AppColors.WARNING

    def test_get_color_for_sharpe_negative(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_sharpe(-0.5)
        assert color == AppColors.ERROR

    def test_get_color_for_sharpe_neutral(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_sharpe(0.3)
        assert color == AppColors.TEXT_PRIMARY

    def test_get_color_for_ic_positive_significant(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_ic(0.06)
        assert color == AppColors.SUCCESS

    def test_get_color_for_ic_negative_significant(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_ic(-0.06)
        assert color == AppColors.ERROR

    def test_get_color_for_ic_insignificant(self, panel: BacktestResultPanel) -> None:
        color = panel._get_color_for_ic(0.03)
        assert color == AppColors.TEXT_PRIMARY

    def test_build_nav_chart_with_data(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel._result = sample_result

        container = panel._build_nav_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.LineChart)

    def test_build_nav_chart_empty(self, panel: BacktestResultPanel, empty_result: BacktestResult) -> None:
        panel._result = empty_result

        container = panel._build_nav_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_nav_chart_no_result(self, panel: BacktestResultPanel) -> None:
        panel._result = None

        container = panel._build_nav_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_trades_table_with_data(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel._result = sample_result

        container = panel._build_trades_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Column)

    def test_build_trades_table_empty(self, panel: BacktestResultPanel, empty_result: BacktestResult) -> None:
        panel._result = empty_result

        container = panel._build_trades_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_trades_table_no_result(self, panel: BacktestResultPanel) -> None:
        panel._result = None

        container = panel._build_trades_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_ic_chart_with_data(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel._result = sample_result

        container = panel._build_ic_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.BarChart)

    def test_build_ic_chart_empty(self, panel: BacktestResultPanel, empty_result: BacktestResult) -> None:
        panel._result = empty_result

        container = panel._build_ic_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_ic_chart_no_result(self, panel: BacktestResultPanel) -> None:
        panel._result = None

        container = panel._build_ic_chart()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_monthly_table_with_data(self, panel: BacktestResultPanel, sample_result: BacktestResult) -> None:
        panel._result = sample_result

        container = panel._build_monthly_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.DataTable)

    def test_build_monthly_table_empty(self, panel: BacktestResultPanel, empty_result: BacktestResult) -> None:
        panel._result = empty_result

        container = panel._build_monthly_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_build_monthly_table_no_result(self, panel: BacktestResultPanel) -> None:
        panel._result = None

        container = panel._build_monthly_table()

        assert isinstance(container, ft.Container)
        assert isinstance(container.content, ft.Text)

    def test_metrics_with_missing_keys(self, panel: BacktestResultPanel) -> None:
        metrics = {}

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)

    def test_max_drawdown_color_high(self, panel: BacktestResultPanel) -> None:
        metrics = {"max_drawdown": 0.25}

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)

    def test_max_drawdown_color_low(self, panel: BacktestResultPanel) -> None:
        metrics = {"max_drawdown": 0.10}

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)

    def test_win_rate_color_high(self, panel: BacktestResultPanel) -> None:
        metrics = {"win_rate": 0.6}

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)

    def test_win_rate_color_low(self, panel: BacktestResultPanel) -> None:
        metrics = {"win_rate": 0.4}

        content = panel._build_metrics_section(metrics)

        assert isinstance(content, ft.Column)
