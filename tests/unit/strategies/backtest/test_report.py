from datetime import date, datetime
import dataclasses

import polars as pl
import pytest

from core.i18n import I18n
from strategies.backtest.config import BacktestConfig, BacktestResult
from strategies.backtest.metrics import PROFIT_THRESHOLD
from strategies.backtest.report import BacktestReport


@pytest.fixture(autouse=True)
def reset_i18n():
    """Ensure I18n is initialized in zh_CN for each test."""
    I18n._initialized = False
    I18n._locale = "zh_CN"
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None
    I18n._state = None
    I18n.initialize("zh_CN")
    yield
    I18n._initialized = False
    I18n._locale = "zh_CN"
    I18n._strings_cache = {}
    I18n._missing_keys = set()
    I18n._listeners = None
    I18n._state = None


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
        assert I18n.get("report_section_data_warnings") in summary
        assert "test warning" in summary

    def test_format_monthly_stats(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        stats = report.format_monthly_stats(backtest_result)
        assert "2024-01" in stats
        assert "1.00%" in stats

    def test_format_monthly_stats_empty(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        stats = report.format_monthly_stats(empty_result)
        assert I18n.get("report_no_monthly_stats") in stats

    def test_format_trade_summary(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        trade_summary = report.format_trade_summary(backtest_result)
        assert f"{I18n.get('report_total_trades_count')}: 2" in trade_summary
        assert "800.00" in trade_summary

    def test_format_trade_summary_empty(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        trade_summary = report.format_trade_summary(empty_result)
        assert I18n.get("report_no_trades") in trade_summary

    def test_format_trade_summary_zero_pnl_not_counted_as_loss(self, backtest_result: BacktestResult) -> None:
        """realized_pnl == 0 归入 DRAW，不计入亏损次数。

        backtest_result 的 realized_pnl = [0.0, 800.0]：
        - 800.0 为盈利
        - 0.0 为平局（DRAW），不再计入亏损
        """
        report = BacktestReport()
        summary = report.format_trade_summary(backtest_result)
        assert f"{I18n.get('report_winning_count')}: 1" in summary
        assert f"{I18n.get('report_losing_count')}: 0" in summary

    def test_format_trade_summary_draw_excluded_from_loss(self, backtest_result: BacktestResult) -> None:
        """混合盈亏中 0 归入 DRAW，仅负值计入亏损。"""
        trades_with_draw = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ"],
                "action": ["buy", "sell", "sell"],
                "price": [10.0, 11.0, 9.0],
                "volume": [1000, 1000, 1000],
                "realized_pnl": [0.0, 800.0, -500.0],
            }
        )
        result = dataclasses.replace(backtest_result, trades=trades_with_draw)
        report = BacktestReport()
        summary = report.format_trade_summary(result)
        assert f"{I18n.get('report_total_trades_count')}: 3" in summary
        assert f"{I18n.get('report_winning_count')}: 1" in summary
        assert f"{I18n.get('report_losing_count')}: 1" in summary

    def test_profit_threshold_shared_constant(self) -> None:
        """PROFIT_THRESHOLD 常量在 report 与 metrics 间共享，值为 0.0"""
        assert PROFIT_THRESHOLD == 0.0

    def test_to_markdown(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        assert I18n.get("report_title", strategy_name="test_strategy") in md
        assert "test_strategy" in md
        assert f"## {I18n.get('report_section_summary')}" in md
        assert f"## {I18n.get('report_section_monthly')}" in md
        assert f"## {I18n.get('report_section_trades')}" in md

    def test_to_markdown_with_warnings(self, backtest_result: BacktestResult) -> None:
        result_with_warning = backtest_result.with_warnings(list(backtest_result.data_warnings) + ["test warning"])
        report = BacktestReport()
        md = report.to_markdown(result_with_warning)
        assert f"## {I18n.get('report_section_data_warnings')}" in md


class TestBacktestReportI18nLocale:
    """中英文标签验证：切换 locale 后报告标签应随之变化。"""

    def test_summary_labels_in_zh_cn(self, backtest_result: BacktestResult) -> None:
        I18n.set_locale("zh_CN")
        report = BacktestReport()
        summary = report.format_summary(backtest_result)
        assert I18n.get("report_strategy") in summary
        assert I18n.get("report_total_return") in summary
        assert I18n.get("report_sharpe_ratio") in summary
        assert I18n.get("report_max_drawdown") in summary

    def test_summary_labels_in_en_us(self, backtest_result: BacktestResult) -> None:
        I18n.set_locale("en_US")
        report = BacktestReport()
        summary = report.format_summary(backtest_result)
        assert I18n.get("report_strategy") in summary
        assert I18n.get("report_total_return") in summary
        assert I18n.get("report_sharpe_ratio") in summary
        assert I18n.get("report_max_drawdown") in summary

    def test_markdown_sections_in_zh_cn(self, backtest_result: BacktestResult) -> None:
        I18n.set_locale("zh_CN")
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        assert f"## {I18n.get('report_section_summary')}" in md
        assert f"## {I18n.get('report_section_monthly')}" in md
        assert f"## {I18n.get('report_section_trades')}" in md

    def test_markdown_sections_in_en_us(self, backtest_result: BacktestResult) -> None:
        I18n.set_locale("en_US")
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        assert f"## {I18n.get('report_section_summary')}" in md
        assert f"## {I18n.get('report_section_monthly')}" in md
        assert f"## {I18n.get('report_section_trades')}" in md

    def test_locale_switch_changes_labels(self, backtest_result: BacktestResult) -> None:
        """切换 locale 后，相同 key 的标签文本应不同。"""
        report = BacktestReport()
        I18n.set_locale("zh_CN")
        zh_summary = report.format_summary(backtest_result)
        zh_label = I18n.get("report_total_return")

        I18n.set_locale("en_US")
        en_summary = report.format_summary(backtest_result)
        en_label = I18n.get("report_total_return")

        assert zh_label != en_label
        assert zh_label in zh_summary
        assert en_label in en_summary

    def test_no_trades_label_in_both_locales(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        I18n.set_locale("zh_CN")
        assert report.format_trade_summary(empty_result) == I18n.get("report_no_trades")

        I18n.set_locale("en_US")
        assert report.format_trade_summary(empty_result) == I18n.get("report_no_trades")

    def test_no_monthly_stats_label_in_both_locales(self, empty_result: BacktestResult) -> None:
        report = BacktestReport()
        I18n.set_locale("zh_CN")
        assert report.format_monthly_stats(empty_result) == I18n.get("report_no_monthly_stats")

        I18n.set_locale("en_US")
        assert report.format_monthly_stats(empty_result) == I18n.get("report_no_monthly_stats")


class TestBacktestReportI18nParamInjection:
    """参数注入验证：report_title 含 strategy_name，report_data_warnings 含 count。"""

    def test_report_title_includes_strategy_name(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        expected_title = I18n.get("report_title", strategy_name=backtest_result.strategy_name)
        assert f"# {expected_title}" in md

    def test_report_data_warnings_includes_count(self, backtest_result: BacktestResult) -> None:
        warnings = ["w1", "w2", "w3"]
        result_with_warnings = backtest_result.with_warnings(warnings)
        report = BacktestReport()
        summary = report.format_summary(result_with_warnings)
        expected_line = I18n.get("report_data_warnings", count=len(warnings)) + ":"
        assert expected_line in summary

    def test_report_generated_at_includes_time_and_run_id(self, backtest_result: BacktestResult) -> None:
        report = BacktestReport()
        md = report.to_markdown(backtest_result)
        assert backtest_result.run_id in md
        assert I18n.get("report_run_id") in md or "Run ID" in md or "运行ID" in md
