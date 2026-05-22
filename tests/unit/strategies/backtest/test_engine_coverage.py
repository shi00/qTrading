"""strategies/backtest/engine.py 补充测试 - 覆盖 _is_rebalance_day、_calc_ic_series、_apply_qfq 等"""

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
        assert qfq_ratio_day1 == 2.0

        qfq_ratio_day3 = result.filter(pl.col("trade_date") == date(2024, 1, 4)).select("qfq_ratio").item()
        assert qfq_ratio_day3 == 1.0

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
        assert stock2_ratios[0] == 2.0
        assert stock2_ratios[1] == 1.0


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
        nav_curve = pl.Series([1.0, 1.01, 1.02, 1.03, 1.04])
        daily_returns = pl.Series([0.01, 0.01, 0.01, 0.01, 0.01])
        benchmark_returns = pl.Series([0.005, 0.005, 0.005, 0.005, 0.005])

        result = engine._calc_period_stats(nav_curve, daily_returns, benchmark_returns, trade_dates)

        assert result.height == 2
        year_months = result["year_month"].to_list()
        assert "2024-01" in year_months
        assert "2024-02" in year_months


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

        assert signals.is_empty() or signals.height == 1

    @pytest.mark.asyncio
    async def test_strategy_exception_with_fail_fast(self):
        from dataclasses import replace

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            fail_fast=True,
        )
        engine = VectorBacktestEngine.__new__(VectorBacktestEngine)
        engine.config = config
        engine.data_provider = MagicMock()
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
