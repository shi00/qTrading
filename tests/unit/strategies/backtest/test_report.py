from datetime import date, datetime

import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.report import BacktestReport


@pytest.fixture
def backtest_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        initial_capital=1_000_000.0,
    )


@pytest.fixture
def backtest_result(backtest_config: BacktestConfig) -> BacktestResult:
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
                "ts_code": ["000001.SZ", "000001.SZ"],
                "action": ["buy", "sell"],
                "price": [10.0, 11.0],
                "volume": [1000, 1000],
                "realized_pnl": [0.0, 800.0],
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
            "total_return": 0.01,
            "annualized_return": 0.12,
            "sharpe_ratio": 1.5,
            "max_drawdown": 0.05,
            "calmar_ratio": 2.4,
            "ic_mean": 0.03,
            "ic_ir": 0.5,
            "win_rate": 0.6,
            "profit_factor": 1.8,
            "total_trades": 2,
        },
        ic_series=pl.Series([0.02, 0.04]),
        period_stats=pl.DataFrame(
            {
                "year_month": ["2024-01"],
                "monthly_return": [0.01],
                "benchmark_return": [0.008],
                "excess_return": [0.002],
                "start_nav": [100.0],
                "end_nav": [101.0],
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


class TestBacktestReport:
    def test_format_summary_uses_metrics_dict(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        summary = report.format_summary(backtest_result)
        assert "test_strategy" in summary
        assert "1.00%" in summary
        assert "1.5000" in summary
        assert "5.00%" in summary

    def test_format_summary_with_empty_metrics(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        summary = report.format_summary(empty_result)
        assert "empty_strategy" in summary
        assert "0.00%" in summary

    def test_format_summary_with_data_warnings(self, backtest_result: BacktestResult) -> None:
        result_with_warning = backtest_result.with_warnings(list(backtest_result.data_warnings) + ["test warning"])
        report = BacktestReport()
        summary = report.format_summary(result_with_warning)
        assert "数据警告" in summary
        assert "test warning" in summary

    def test_format_monthly_stats(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        stats = report.format_monthly_stats(backtest_result)
        assert "2024-01" in stats
        assert "1.00%" in stats

    def test_format_monthly_stats_empty(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        stats = report.format_monthly_stats(empty_result)
        assert "无月度统计数据" in stats

    def test_format_trade_summary(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        trade_summary = report.format_trade_summary(backtest_result)
        assert "总交易: 2" in trade_summary
        assert "800.00" in trade_summary

    def test_format_trade_summary_empty(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        trade_summary = report.format_trade_summary(empty_result)
        assert "无交易记录" in trade_summary

    def test_to_markdown(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        assert "# 回测报告" in md
        assert "test_strategy" in md
        assert "## 摘要" in md
        assert "## 月度统计" in md
        assert "## 交易统计" in md

    def test_to_markdown_with_warnings(self, backtest_result: BacktestResult) -> None:
        result_with_warning = backtest_result.with_warnings(list(backtest_result.data_warnings) + ["test warning"])
        report = BacktestReport()
        md = report.to_markdown(result_with_warning)
        assert "## 数据警告" in md
