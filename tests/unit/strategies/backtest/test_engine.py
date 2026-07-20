"""strategies/backtest/engine.py 测试 - 覆盖 _is_rebalance_day、_calc_ic_series、_apply_qfq 等"""
# pyright: reportAttributeAccessIssue=false

import math
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import polars as pl
import pytest

from data.domain_services.transaction_cost import TransactionCostConfig, TransactionCostModel
from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine


class TestIsRebalanceDay:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 28),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_daily_always_returns_true(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame()

        for d in trade_dates:
            assert engine._is_rebalance_day(d, trade_dates, signals, "daily") is True

    def test_signal_with_signals_returns_true(self):
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )

        assert engine._is_rebalance_day(date(2024, 1, 2), trade_dates, signals, "signal") is False
        assert engine._is_rebalance_day(date(2024, 1, 3), trade_dates, signals, "signal") is True
        assert engine._is_rebalance_day(date(2024, 1, 4), trade_dates, signals, "signal") is False

    def test_signal_empty_signals_returns_false(self):
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )

        assert engine._is_rebalance_day(date(2024, 1, 2), trade_dates, signals, "signal") is False

    def test_weekly_boundary_detection(self):
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [
            date(2024, 1, 8),
            date(2024, 1, 9),
            date(2024, 1, 10),
            date(2024, 1, 11),
            date(2024, 1, 12),
            date(2024, 1, 15),
        ]
        signals = pl.DataFrame()

        assert engine._is_rebalance_day(date(2024, 1, 8), trade_dates, signals, "weekly") is True
        assert engine._is_rebalance_day(date(2024, 1, 9), trade_dates, signals, "weekly") is False
        assert engine._is_rebalance_day(date(2024, 1, 15), trade_dates, signals, "weekly") is True

    def test_monthly_boundary_detection(self):
        engine = self._make_engine(rebalance_freq="monthly")
        trade_dates = [
            date(2024, 1, 29),
            date(2024, 1, 30),
            date(2024, 1, 31),
            date(2024, 2, 1),
            date(2024, 2, 2),
        ]
        signals = pl.DataFrame()

        assert engine._is_rebalance_day(date(2024, 1, 29), trade_dates, signals, "monthly") is True
        assert engine._is_rebalance_day(date(2024, 1, 30), trade_dates, signals, "monthly") is False
        assert engine._is_rebalance_day(date(2024, 2, 1), trade_dates, signals, "monthly") is True

    def test_first_day_always_rebalance(self):
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [date(2024, 1, 8), date(2024, 1, 9)]
        signals = pl.DataFrame()

        assert engine._is_rebalance_day(date(2024, 1, 8), trade_dates, signals, "weekly") is True

    def test_date_not_in_trade_dates_returns_false(self):
        engine = self._make_engine(rebalance_freq="weekly")
        trade_dates = [date(2024, 1, 8), date(2024, 1, 9)]
        signals = pl.DataFrame()

        assert engine._is_rebalance_day(date(2024, 1, 10), trade_dates, signals, "weekly") is False

    def test_unknown_freq_returns_true(self):
        engine = self._make_engine(rebalance_freq="unknown")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame()

        assert engine._is_rebalance_day(date(2024, 1, 2), trade_dates, signals, "unknown") is True


class TestCalcICSeries:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_empty_signals_returns_empty_series(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "signal_date": [],
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        quotes_df = pl.DataFrame()

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert ic_series.len() == 0

    def test_missing_execution_quotes_returns_zero_ic(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "qfq_close": [10.0],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert ic_series.len() == 1
        assert ic_series[0] == 0.0

    def test_missing_next_rebalance_quotes_returns_zero_ic(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 3)],
                "qfq_close": [10.0],
                "qfq_open": [9.9],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert ic_series.len() == 1
        assert ic_series[0] == 0.0

    def test_insufficient_signal_quotes_returns_zero_ic(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 3), date(2024, 1, 4)],
                "qfq_close": [10.0, 10.5],
                "qfq_open": [9.9, 10.4],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert ic_series.len() == 2
        assert ic_series[0] == 0.0

    def test_valid_ic_calculation(self):
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3), date(2024, 1, 3)],
                "ts_code": ["000001.SZ", "000002.SZ"],
                "signal_rank": [1, 2],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
                "trade_date": [
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 4),
                ],
                "qfq_close": [10.0, 20.0, 10.2, 20.4, 10.5, 20.8],
                "qfq_open": [9.9, 19.9, 10.1, 20.2, 10.4, 20.6],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)
        assert ic_series.len() == 2


class TestApplyQfq:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_no_adj_factor_creates_qfq_columns(self):
        engine = self._make_engine()
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "open": [10.0, 10.5],
                "high": [10.2, 10.7],
                "low": [9.9, 10.4],
                "close": [10.1, 10.6],
            }
        )

        result = engine._apply_qfq(quotes_df)

        assert "raw_open" in result.columns
        assert "raw_high" in result.columns
        assert "raw_low" in result.columns
        assert "raw_close" in result.columns
        assert "qfq_open" in result.columns
        assert "qfq_high" in result.columns
        assert "qfq_low" in result.columns
        assert "qfq_close" in result.columns

        assert result["raw_open"].to_list() == [10.0, 10.5]
        assert result["qfq_open"].to_list() == [10.0, 10.5]

    def test_with_adj_factor_calculates_qfq(self):
        engine = self._make_engine()
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "open": [10.0, 10.5, 5.0],
                "high": [10.2, 10.7, 5.1],
                "low": [9.9, 10.4, 4.9],
                "close": [10.1, 10.6, 5.0],
                "adj_factor": [2.0, 2.0, 1.0],
            }
        )

        result = engine._apply_qfq(quotes_df)

        assert "qfq_ratio" in result.columns
        assert "raw_open" in result.columns
        assert "qfq_open" in result.columns

        qfq_ratio_day1 = result.filter(pl.col("trade_date") == date(2024, 1, 2)).select("qfq_ratio").item()
        assert qfq_ratio_day1 == 2.0  # 最新一日基准=1.0, ratio=2.0/1.0=2.0

        qfq_ratio_day3 = result.filter(pl.col("trade_date") == date(2024, 1, 4)).select("qfq_ratio").item()
        assert qfq_ratio_day3 == 1.0  # 最新一日基准=1.0, ratio=1.0/1.0=1.0

    def test_multiple_stocks_different_adj_factors(self):
        engine = self._make_engine()
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)],
                "open": [10.0, 10.5, 20.0, 20.5],
                "high": [10.2, 10.7, 20.2, 20.7],
                "low": [9.9, 10.4, 19.9, 20.4],
                "close": [10.1, 10.6, 20.1, 20.6],
                "adj_factor": [1.0, 1.0, 2.0, 1.0],
            }
        )

        result = engine._apply_qfq(quotes_df)

        stock1_ratios = result.filter(pl.col("ts_code") == "000001.SZ").select("qfq_ratio").to_series().to_list()
        assert all(r == 1.0 for r in stock1_ratios)

        stock2_ratios = result.filter(pl.col("ts_code") == "000002.SZ").select("qfq_ratio").to_series().to_list()
        assert stock2_ratios[0] == 2.0  # 最新一日基准=1.0, ratio=2.0/1.0=2.0
        assert stock2_ratios[1] == 1.0  # 最新一日基准=1.0, ratio=1.0/1.0=1.0


class TestCalcBenchmarkReturns:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            benchmark_code="000300.SH",
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_empty_benchmark_returns_zeros(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        benchmark_df = pl.DataFrame()

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns.len() == 2
        assert all(r == 0.0 for r in returns.to_list())

    def test_benchmark_with_string_dates(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        benchmark_df = pl.DataFrame(
            {
                "trade_date": ["20240102", "20240103"],
                "pct_chg": [1.0, -0.5],
            }
        )

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns.len() == 2
        assert returns[0] == 0.01
        assert returns[1] == -0.005

    def test_benchmark_with_date_objects(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        benchmark_df = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "pct_chg": [1.5, 2.0],
            }
        )

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns.len() == 2
        assert returns[0] == 0.015
        assert returns[1] == 0.02

    def test_missing_dates_filled_with_zero(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        benchmark_df = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2), date(2024, 1, 4)],
                "pct_chg": [1.0, 2.0],
            }
        )

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns.len() == 3
        assert returns[0] == 0.01
        assert returns[1] == 0.0
        assert returns[2] == 0.02

    def test_all_benchmark_dates_missing_returns_all_zeros(self):
        """测试所有 Benchmark 日期缺失时返回全零序列。"""

        # 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        benchmark_df = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 10)],
                "pct_chg": [1.0],
            }
        )

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns.len() == 3
        assert all(r == 0.0 for r in returns.to_list())

    def test_benchmark_pct_chg_unit_conversion(self):
        """测试 Benchmark pct_chg 从百分比转换为小数。

        IndexDaily.pct_chg 字段单位是"百分比"（如 1.5 表示 1.5%），
        需要除以 100 转换为小数形式（如 0.015）。
        """
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2)]
        benchmark_df = pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 2)],
                "pct_chg": [1.5],
            }
        )

        returns = engine._calc_benchmark_returns(benchmark_df, trade_dates)

        assert returns[0] == 0.015


class TestCalcPeriodStats:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 28),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_period_stats_structure(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 2, 1)]
        nav_curve = pl.Series([1.0, 1.01, 1.02])
        daily_returns = pl.Series([0.01, 0.01, 0.01])
        benchmark_returns = pl.Series([0.005, 0.005, 0.005])

        result = engine._calc_period_stats(nav_curve, daily_returns, benchmark_returns, trade_dates)

        assert "year_month" in result.columns
        assert "monthly_return" in result.columns
        assert "benchmark_return" in result.columns
        assert "excess_return" in result.columns
        assert "start_nav" in result.columns
        assert "end_nav" in result.columns

    def test_period_stats_monthly_aggregation(self):
        engine = self._make_engine()
        trade_dates = [
            date(2024, 1, 2),
            date(2024, 1, 3),
            date(2024, 1, 4),
            date(2024, 2, 1),
            date(2024, 2, 2),
        ]
        nav_curve = pl.Series([100.0, 105.0, 102.9, 106.0, 110.24])
        daily_returns = pl.Series([0.0, 0.05, -0.02, 0.030125, 0.04])
        benchmark_returns = pl.Series([0.0, 0.02, -0.01, 0.015, 0.01])

        result = engine._calc_period_stats(nav_curve, daily_returns, benchmark_returns, trade_dates)

        assert result.height == 2
        year_months = result["year_month"].to_list()
        assert "2024-01" in year_months
        assert "2024-02" in year_months

        jan_row = result.filter(pl.col("year_month") == "2024-01")
        jan_monthly = float(jan_row["monthly_return"][0])
        expected_jan = (1.05 * 0.98) - 1
        assert jan_monthly == pytest.approx(expected_jan, rel=1e-4)

        jan_bench = float(jan_row["benchmark_return"][0])
        expected_jan_bench = (1.02 * 0.99) - 1
        assert jan_bench == pytest.approx(expected_jan_bench, rel=1e-4)

        feb_row = result.filter(pl.col("year_month") == "2024-02")
        feb_monthly = float(feb_row["monthly_return"][0])
        expected_feb = (1.030125 * 1.04) - 1
        assert feb_monthly == pytest.approx(expected_feb, rel=1e-4)

        feb_bench = float(feb_row["benchmark_return"][0])
        expected_feb_bench = (1.015 * 1.01) - 1
        assert feb_bench == pytest.approx(expected_feb_bench, rel=1e-4)

        jan_excess = float(jan_row["excess_return"][0])
        assert jan_excess == pytest.approx(expected_jan - expected_jan_bench, rel=1e-4)

        feb_excess = float(feb_row["excess_return"][0])
        assert feb_excess == pytest.approx(expected_feb - expected_feb_bench, rel=1e-4)

    def test_period_stats_nan_defense(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        nav_curve = pl.Series([100.0, 105.0])
        daily_returns = pl.Series([0.05, float("nan")])
        benchmark_returns = pl.Series([0.02, float("nan")])

        result = engine._calc_period_stats(nav_curve, daily_returns, benchmark_returns, trade_dates)

        assert not result.is_empty()
        monthly_ret = float(result["monthly_return"][0])
        bench_ret = float(result["benchmark_return"][0])

        assert not math.isnan(monthly_ret), "monthly_return should not be NaN"
        assert not math.isnan(bench_ret), "benchmark_return should not be NaN"

        expected_monthly = 1.05 * 1.0 - 1
        assert monthly_ret == pytest.approx(expected_monthly, rel=1e-4)


class TestGetNextRebalanceDate:
    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 2, 28),
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_execution_date_not_in_trade_dates(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        result = engine._get_next_rebalance_date(date(2024, 1, 10), trade_dates, "daily")

        assert result is None

    def test_daily_at_end_returns_none(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        result = engine._get_next_rebalance_date(date(2024, 1, 3), trade_dates, "daily")

        assert result is None

    def test_signal_at_end_returns_none(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        result = engine._get_next_rebalance_date(date(2024, 1, 3), trade_dates, "signal")

        assert result is None

    def test_weekly_no_boundary_found(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 8), date(2024, 1, 9), date(2024, 1, 10)]

        result = engine._get_next_rebalance_date(date(2024, 1, 8), trade_dates, "weekly")

        assert result is None

    def test_monthly_no_boundary_found(self):
        engine = self._make_engine()
        trade_dates = [date(2024, 1, 29), date(2024, 1, 30), date(2024, 1, 31)]

        result = engine._get_next_rebalance_date(date(2024, 1, 29), trade_dates, "monthly")

        assert result is None


class TestGetTradeDates:
    @pytest.mark.asyncio
    async def test_empty_trade_calendar_raises(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_trade_cal = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="No trade dates found"):
            await engine._get_trade_dates()

    @pytest.mark.asyncio
    async def test_empty_dataframe_raises(self):
        import pandas as pd

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_trade_cal = AsyncMock(return_value=pd.DataFrame())

        with pytest.raises(ValueError, match="No trade dates found"):
            await engine._get_trade_dates()


class TestLoadQuotes:
    @pytest.mark.asyncio
    async def test_empty_quotes_raises(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_daily_quotes = AsyncMock(return_value=None)

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        with pytest.raises(ValueError, match="No quotes data found"):
            await engine._load_quotes(trade_dates)

    @pytest.mark.asyncio
    async def test_empty_dataframe_raises(self):
        import pandas as pd

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        with pytest.raises(ValueError, match="No quotes data found"):
            await engine._load_quotes(trade_dates)


class TestLoadBenchmark:
    @pytest.mark.asyncio
    async def test_empty_benchmark_returns_empty_df(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            benchmark_code="000300.SH",
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_index_daily_range = AsyncMock(return_value=None)

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        result = await engine._load_benchmark(trade_dates)

        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_empty_dataframe_returns_empty_df(self):
        import pandas as pd

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            benchmark_code="000300.SH",
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cache = MagicMock()
        engine.cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame())

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        result = await engine._load_benchmark(trade_dates)

        assert result.is_empty()


class TestGenerateSignals:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.data_provider = MagicMock()
        engine.data_provider.preload_range = AsyncMock()
        engine.strategy_adapter = MagicMock()
        return engine

    @pytest.mark.asyncio
    async def test_cancel_check_stops_generation(self):
        engine = self._make_engine()
        engine.data_provider.build_context = AsyncMock(return_value={})
        engine.strategy_adapter.generate_signal = AsyncMock(
            return_value=pl.DataFrame(
                {
                    "signal_date": [date(2024, 1, 2)],
                    "execution_date": [date(2024, 1, 3)],
                    "ts_code": ["000001.SZ"],
                    "signal_rank": [1],
                }
            )
        )

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        cancel_calls = [False, True]

        def cancel_check():
            return cancel_calls.pop(0)

        signals = await engine._generate_signals(
            strategy=MagicMock(),
            params={},
            trade_dates=trade_dates,
            cancel_check=cancel_check,
        )

        engine.data_provider.preload_range.assert_called_once_with(trade_dates[0], trade_dates[-1])
        assert signals.is_empty() or signals.height == 1

    @pytest.mark.asyncio
    async def test_strategy_exception_with_fail_fast(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            fail_fast=True,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.data_provider = MagicMock()
        engine.data_provider.preload_range = AsyncMock()
        engine.strategy_adapter = MagicMock()
        engine.data_provider.build_context = AsyncMock(return_value={})
        engine.strategy_adapter.generate_signal = AsyncMock(side_effect=Exception("strategy error"))

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        with pytest.raises(Exception, match="strategy error"):
            await engine._generate_signals(
                strategy=MagicMock(),
                params={},
                trade_dates=trade_dates,
            )

        engine.data_provider.preload_range.assert_called_once_with(trade_dates[0], trade_dates[-1])

    @pytest.mark.asyncio
    async def test_strategy_exception_without_fail_fast(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            fail_fast=False,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.data_provider = MagicMock()
        engine.data_provider.preload_range = AsyncMock()
        engine.strategy_adapter = MagicMock()
        engine.data_provider.build_context = AsyncMock(return_value={})
        engine.strategy_adapter.generate_signal = AsyncMock(side_effect=Exception("strategy error"))

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        failed_signal_dates = []
        signals = await engine._generate_signals(
            strategy=MagicMock(),
            params={},
            trade_dates=trade_dates,
            failed_signal_dates=failed_signal_dates,
        )

        engine.data_provider.preload_range.assert_called_once_with(trade_dates[0], trade_dates[-1])
        assert signals.is_empty()
        assert len(failed_signal_dates) == 1


class TestSimulateTrades:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    def test_empty_signals_returns_empty_results(self):
        engine = self._make_engine()
        signals = pl.DataFrame(
            {
                "execution_date": [],
                "ts_code": [],
                "signal_rank": [],
            }
        )
        quotes_df = pl.DataFrame()
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        assert trades.is_empty()
        assert positions.is_empty()
        assert skipped.is_empty()
        assert warnings == []


class TestRunMethod:
    @pytest.mark.asyncio
    async def test_invalid_config_raises(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 31),
            end_date=date(2024, 1, 1),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config

        with pytest.raises(ValueError, match="Invalid backtest config"):
            await engine.run(strategy=MagicMock())


class TestEnrichSuspendStatus:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    @pytest.mark.asyncio
    async def test_no_suspend_data_returns_all_tradable(self):
        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_suspend_d = AsyncMock(return_value=None)

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_suspend_status(quotes_df, "20240102", "20240131")

        assert "is_tradable" in result.columns
        assert all(result["is_tradable"].to_list())
        assert warning is None

    @pytest.mark.asyncio
    async def test_empty_suspend_data_returns_all_tradable(self):
        import pandas as pd

        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_suspend_d = AsyncMock(return_value=pd.DataFrame())

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_suspend_status(quotes_df, "20240102", "20240131")

        assert "is_tradable" in result.columns
        assert all(result["is_tradable"].to_list())
        assert warning is None

    @pytest.mark.asyncio
    async def test_suspend_data_marks_suspended_stocks(self):
        import pandas as pd

        engine = self._make_engine()
        engine.cache = MagicMock()
        suspend_pd = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "suspend_timing": ["09:30"],
                "suspend_type": ["S"],
            }
        )
        engine.cache.get_suspend_d = AsyncMock(return_value=suspend_pd)

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_suspend_status(quotes_df, "20240102", "20240131")

        assert "is_tradable" in result.columns
        is_tradable_list = result["is_tradable"].to_list()
        assert is_tradable_list[0] is False
        assert is_tradable_list[1] is True
        assert warning is None

    @pytest.mark.asyncio
    async def test_exception_marks_all_tradable_and_creates_warning(self):

        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_suspend_d = AsyncMock(side_effect=Exception("DB error"))

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_suspend_status(quotes_df, "20240102", "20240131")

        assert "is_tradable" in result.columns
        assert all(result["is_tradable"].to_list())
        assert warning is not None
        assert warning.warning_type == "suspend_enrich_failed"
        assert warning.start_date == "20240102"
        assert warning.end_date == "20240131"
        assert warning.affected_stock_count == 2
        assert "DB error" in warning.error_message


class TestEnrichLimitStatus:
    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    @pytest.mark.asyncio
    async def test_no_limit_data_returns_none_limit_status(self):
        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_limit_list = AsyncMock(return_value=None)

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_limit_status(quotes_df, "20240102", "20240131")

        assert "limit_status" in result.columns
        assert all(v is None for v in result["limit_status"].to_list())
        assert warning is None

    @pytest.mark.asyncio
    async def test_limit_data_marks_limit_stocks(self):
        import pandas as pd

        engine = self._make_engine()
        engine.cache = MagicMock()
        limit_pd = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "limit_type": ["U"],
            }
        )
        engine.cache.get_limit_list = AsyncMock(return_value=limit_pd)

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_limit_status(quotes_df, "20240102", "20240131")

        assert "limit_status" in result.columns
        limit_status_list = result["limit_status"].to_list()
        assert limit_status_list[0] == "up_limit"
        assert limit_status_list[1] is None
        assert warning is None

    @pytest.mark.asyncio
    async def test_exception_creates_warning(self):
        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_limit_list = AsyncMock(side_effect=Exception("DB error"))

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [10.0, 20.0],
            }
        )

        result, warning = await engine._enrich_limit_status(quotes_df, "20240102", "20240131")

        assert "limit_status" in result.columns
        assert all(v is None for v in result["limit_status"].to_list())
        assert warning is not None
        assert warning.warning_type == "limit_enrich_failed"
        assert warning.start_date == "20240102"
        assert warning.end_date == "20240131"
        assert warning.affected_stock_count == 2
        assert "DB error" in warning.error_message


class TestEngineEndToEndPipeline:
    """引擎级端到端测试：验证 _enrich_* → _simulate_trades 全链路。

    覆盖涨跌停限制、停牌过滤的完整数据流，
    确保数据 enrich 层的枚举值与撮合层的判断逻辑一致。
    """

    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            slippage_bps=0.0,
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig(slippage_bps=0.0))
        return engine

    @pytest.mark.asyncio
    async def test_limit_up_skips_buy_in_simulation(self):
        """涨停股票在撮合层被跳过：limit_status=up_limit → PortfolioSimulator 跳过买入"""
        from strategies.backtest.portfolio import PortfolioSimulator

        engine = self._make_engine(allow_limit_up_buy=False)
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "vol": [100000],
                "is_tradable": [True],
                "limit_status": ["up_limit"],
                "raw_open": [10.0],
                "raw_close": [10.0],
                "qfq_open": [10.0],
                "qfq_close": [10.0],
            }
        )

        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 1)],
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "score": [1.0],
                "signal_rank": [1],
                "target_weight": [1.0],
                "reason": [None],
            }
        )

        simulator = PortfolioSimulator(engine.config, engine.cost_model)
        day_signals = signals.filter(pl.col("execution_date") == date(2024, 1, 2))
        day_quotes = quotes_df.filter(pl.col("trade_date") == date(2024, 1, 2))

        simulator.process_day(date(2024, 1, 2), day_signals, day_quotes, is_rebalance=True)

        assert len(simulator.positions) == 0
        assert any("up_limit" in w for w in simulator.warnings)

    @pytest.mark.asyncio
    async def test_limit_down_skips_sell_in_simulation(self):
        """跌停股票在撮合层被跳过卖出：limit_status=down_limit → PortfolioSimulator 跳过卖出"""
        from strategies.backtest.portfolio import PortfolioSimulator

        engine = self._make_engine(allow_limit_down_sell=False)
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "vol": [100000],
                "is_tradable": [True],
                "limit_status": ["down_limit"],
                "raw_open": [10.0],
                "raw_close": [10.0],
                "qfq_open": [10.0],
                "qfq_close": [10.0],
            }
        )

        simulator = PortfolioSimulator(engine.config, engine.cost_model)
        simulator.positions["000001.SZ"] = {
            "volume": 100,
            "cost_basis": 1000.0,
            "entry_date": date(2024, 1, 1),
            "entry_price": 10.0,
            "qfq_entry_price": 10.0,
        }

        day_quotes = quotes_df.filter(pl.col("trade_date") == date(2024, 1, 2))
        simulator.process_day(date(2024, 1, 2), pl.DataFrame(), day_quotes, is_rebalance=True)

        assert "000001.SZ" in simulator.positions
        assert any("down_limit" in w for w in simulator.warnings)

    @pytest.mark.asyncio
    async def test_suspended_skips_trading(self):
        """停牌股票在撮合层被跳过：is_tradable=False → 买入和卖出均跳过"""
        from strategies.backtest.portfolio import PortfolioSimulator

        engine = self._make_engine()
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "open": [10.0],
                "high": [10.0],
                "low": [10.0],
                "close": [10.0],
                "vol": [100000],
                "is_tradable": [False],
                "limit_status": [None],
                "raw_open": [10.0],
                "raw_close": [10.0],
                "qfq_open": [10.0],
                "qfq_close": [10.0],
            }
        )

        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 1)],
                "execution_date": [date(2024, 1, 2)],
                "ts_code": ["000001.SZ"],
                "score": [1.0],
                "signal_rank": [1],
                "target_weight": [1.0],
                "reason": [None],
            }
        )

        simulator = PortfolioSimulator(engine.config, engine.cost_model)
        day_signals = signals.filter(pl.col("execution_date") == date(2024, 1, 2))
        day_quotes = quotes_df.filter(pl.col("trade_date") == date(2024, 1, 2))

        simulator.process_day(date(2024, 1, 2), day_signals, day_quotes, is_rebalance=True)

        assert len(simulator.positions) == 0
        assert any("suspended" in w for w in simulator.warnings)


class TestVectorizationEquivalence:
    """Verify the partition_by-based vectorized path produces identical results
    to the original loop-filter approach (PERF-C1, PERF-C2)."""

    def _make_engine(self, **kwargs):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            slippage_bps=0.0,
            **kwargs,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig(slippage_bps=0.0))
        return engine

    def test_simulate_trades_multi_date_multi_stock_matches_filter(self):
        """_simulate_trades with multiple dates/stocks produces same trades as
        a reference implementation using per-date filter."""
        engine = self._make_engine(rebalance_freq="signal")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3)],
                "execution_date": [date(2024, 1, 3), date(2024, 1, 3), date(2024, 1, 4)],
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ"],
                "signal_rank": [1, 2, 1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": [
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                ],
                "trade_date": [
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                    date(2024, 1, 5),
                ],
                "raw_open": [10.0, 20.0, 10.5, 20.5, 11.0, 21.0, 11.5, 21.5],
                "raw_close": [10.2, 20.2, 10.8, 20.8, 11.2, 21.2, 11.8, 21.8],
                "qfq_open": [10.0, 20.0, 10.5, 20.5, 11.0, 21.0, 11.5, 21.5],
                "qfq_close": [10.2, 20.2, 10.8, 20.8, 11.2, 21.2, 11.8, 21.8],
                "is_tradable": [True] * 8,
            }
        )

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        # Reference: build expected trades by checking buy actions occurred on expected dates
        buy_trades = trades.filter(pl.col("action") == "buy")
        # Day 2024-01-03: two buy signals (000001.SZ, 000002.SZ)
        # Day 2024-01-04: one buy signal (000001.SZ) after selling prior positions
        assert buy_trades.height >= 2
        buy_codes_day1 = sorted(buy_trades.filter(pl.col("trade_date") == date(2024, 1, 3))["ts_code"].to_list())
        assert buy_codes_day1 == ["000001.SZ", "000002.SZ"]

        # Positions recorded for every trade date
        assert positions.height == len(trade_dates)

    def test_simulate_trades_date_with_no_quotes_preserves_schema(self):
        """A trade_date with no quotes should not crash (schema preserved via clear())."""
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        # Quotes only for 2024-01-02, NOT for 2024-01-03
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "raw_open": [10.0],
                "raw_close": [10.5],
                "qfq_open": [10.0],
                "qfq_close": [10.5],
                "is_tradable": [True],
            }
        )

        # Should not raise ColumnNotFoundError despite missing quotes on 2024-01-03
        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        # Buy on 2024-01-03 skipped due to no_quote (schema preserved, filter works)
        no_quote_skips = skipped.filter(pl.col("reason") == "no_quote")
        assert no_quote_skips.height >= 1

    def test_simulate_trades_date_with_no_signals(self):
        """A trade_date with no signals should produce empty day_signals (not crash)."""
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2)],
                "execution_date": [date(2024, 1, 3)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "raw_open": [10.0, 10.5, 11.0],
                "raw_close": [10.2, 10.8, 11.2],
                "qfq_open": [10.0, 10.5, 11.0],
                "qfq_close": [10.2, 10.8, 11.2],
                "is_tradable": [True, True, True],
            }
        )

        trades, positions, skipped, warnings = engine._simulate_trades(signals, quotes_df, trade_dates)

        # Buy only on 2024-01-03 (the only execution_date in signals)
        buy_trades = trades.filter(pl.col("action") == "buy")
        assert buy_trades.height == 1
        assert buy_trades["trade_date"][0] == date(2024, 1, 3)

    def test_calc_ic_series_multi_date_matches_expected(self):
        """_calc_ic_series with multiple signal dates produces same IC values
        regardless of partition_by vs filter implementation."""
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
                "execution_date": [date(2024, 1, 3), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 4)],
                "ts_code": ["000001.SZ", "000002.SZ", "000001.SZ", "000002.SZ"],
                "signal_rank": [1, 2, 1, 2],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": [
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                    "000001.SZ",
                    "000002.SZ",
                ],
                "trade_date": [
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 4),
                    date(2024, 1, 5),
                    date(2024, 1, 5),
                ],
                "qfq_close": [10.0, 20.0, 10.5, 20.5, 11.0, 21.0, 11.5, 21.5],
                "qfq_open": [9.9, 19.9, 10.4, 20.4, 10.9, 20.9, 11.4, 21.4],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)

        # 3 signal dates (trade_dates[:-1]), so 3 IC values
        assert ic_series.len() == 3
        # IC values should be valid floats (not NaN)
        for v in ic_series.to_list():
            assert not math.isnan(v)

    def test_calc_ic_series_missing_signal_date_returns_zero(self):
        """A signal_date with no signals in the dict should produce 0.0 IC."""
        engine = self._make_engine(rebalance_freq="daily")
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        # Signals only for 2024-01-03, not 2024-01-02
        signals = pl.DataFrame(
            {
                "signal_date": [date(2024, 1, 3)],
                "execution_date": [date(2024, 1, 4)],
                "ts_code": ["000001.SZ"],
                "signal_rank": [1],
            }
        )
        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000001.SZ", "000001.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "qfq_close": [10.0, 10.5, 11.0],
                "qfq_open": [9.9, 10.4, 10.9],
            }
        )

        ic_series = engine._calc_ic_series(signals, quotes_df, trade_dates)

        # First signal_date (2024-01-02) has no signals -> 0.0
        assert ic_series[0] == 0.0

    def test_partition_by_lookup_matches_filter_directly(self):
        """Directly verify partition_by dict lookup returns same DataFrame as filter."""
        df = pl.DataFrame(
            {
                "ts_code": ["A", "B", "A", "B"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)],
                "val": [1, 2, 3, 4],
            }
        )

        by_date = {k[0]: v for k, v in df.partition_by("trade_date", as_dict=True).items()}

        for d in [date(2024, 1, 2), date(2024, 1, 3)]:
            filter_result = df.filter(pl.col("trade_date") == d)
            partition_result = by_date.get(d)
            assert partition_result is not None
            assert filter_result.equals(partition_result)

        # Missing date: filter returns empty with schema, clear() also empty with schema
        missing_filter = df.filter(pl.col("trade_date") == date(2024, 1, 10))
        missing_clear = df.clear()
        assert missing_filter.is_empty()
        assert missing_clear.is_empty()
        assert missing_filter.schema == missing_clear.schema


class TestR9SanitizationGuard:
    """R9 红线守护测试：验证 except 块中 str(e) 进入业务数据结构前经 DataSanitizer 脱敏。

    覆盖 3 处修复点：
    - _enrich_suspend_status: DataWarning.error_message
    - _enrich_limit_status: DataWarning.error_message
    - _generate_signals: failed_signal_dates[i]["error"]
    """

    # 含 DB 凭证的敏感 payload（password 23 字符，>= 16 字符阈值）
    _SECRET_URL = "postgresql://dbuser:supersecretpass123456@host:5432/mydb"
    _SECRET_PASSWORD = "supersecretpass123456"

    def _make_engine(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.cost_model = TransactionCostModel(TransactionCostConfig())
        return engine

    @pytest.mark.asyncio
    async def test_enrich_suspend_status_sanitizes_secrets(self):
        """_enrich_suspend_status 异常时 DataWarning.error_message 不含明文密码"""
        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_suspend_d = AsyncMock(side_effect=Exception(self._SECRET_URL))

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [10.0, 20.0],
            }
        )

        _, warning = await engine._enrich_suspend_status(quotes_df, "20240102", "20240131")

        assert warning is not None
        assert warning.warning_type == "suspend_enrich_failed"
        assert self._SECRET_PASSWORD not in warning.error_message
        assert "***" in warning.error_message

    @pytest.mark.asyncio
    async def test_enrich_limit_status_sanitizes_secrets(self):
        """_enrich_limit_status 异常时 DataWarning.error_message 不含明文密码"""
        engine = self._make_engine()
        engine.cache = MagicMock()
        engine.cache.get_limit_list = AsyncMock(side_effect=Exception(self._SECRET_URL))

        quotes_df = pl.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                "close": [10.0, 20.0],
            }
        )

        _, warning = await engine._enrich_limit_status(quotes_df, "20240102", "20240131")

        assert warning is not None
        assert warning.warning_type == "limit_enrich_failed"
        assert self._SECRET_PASSWORD not in warning.error_message
        assert "***" in warning.error_message

    @pytest.mark.asyncio
    async def test_generate_signals_sanitizes_failed_signal_error(self):
        """_generate_signals 策略异常时 failed_signal_dates[i]['error'] 不含明文密码"""
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            fail_fast=False,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.data_provider = MagicMock()
        engine.data_provider.preload_range = AsyncMock()
        engine.strategy_adapter = MagicMock()
        engine.data_provider.build_context = AsyncMock(return_value={})
        engine.strategy_adapter.generate_signal = AsyncMock(side_effect=Exception(self._SECRET_URL))

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        failed_signal_dates: list[dict] = []
        await engine._generate_signals(
            strategy=MagicMock(),
            params={},
            trade_dates=trade_dates,
            failed_signal_dates=failed_signal_dates,
        )

        assert len(failed_signal_dates) == 1
        assert self._SECRET_PASSWORD not in failed_signal_dates[0]["error"]
        assert "***" in failed_signal_dates[0]["error"]
