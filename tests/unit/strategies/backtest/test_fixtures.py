"""回测测试夹具验证测试"""

from datetime import date

import polars as pl
import pytest

from tests.unit.strategies.backtest.fixtures import (
    BacktestTestFixture,
    make_backtest_config,
    make_benchmark_df,
    make_quotes_df,
    make_signals_df,
    make_trade_dates,
)

pytestmark = pytest.mark.unit


class TestMakeTradeDates:
    def test_basic_trade_dates(self) -> None:
        dates = make_trade_dates(date(2024, 1, 8), 5)
        assert len(dates) == 5
        assert dates[0] == date(2024, 1, 8)

    def test_skip_weekends(self) -> None:
        dates = make_trade_dates(date(2024, 1, 8), 10, skip_weekends=True)
        for d in dates:
            assert d.weekday() < 5

    def test_include_weekends(self) -> None:
        dates = make_trade_dates(date(2024, 1, 8), 10, skip_weekends=False)
        assert len(dates) == 10


class TestMakeQuotesDf:
    def test_basic_quotes(self) -> None:
        ts_codes = ["000001.SZ", "000002.SZ"]
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]

        df = make_quotes_df(ts_codes, trade_dates)

        assert len(df) == 4
        assert "ts_code" in df.columns
        assert "trade_date" in df.columns
        assert "raw_open" in df.columns
        assert "raw_close" in df.columns
        assert "qfq_open" in df.columns
        assert "qfq_close" in df.columns

    def test_quotes_with_adj_factor(self) -> None:
        ts_codes = ["000001.SZ"]
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        adj_factors = {"000001.SZ": [0.5, 0.5, 1.0]}

        df = make_quotes_df(ts_codes, trade_dates, adj_factors=adj_factors)

        assert "adj_factor" in df.columns
        qfq_close = df["qfq_close"].to_list()
        assert qfq_close[0] < qfq_close[2]

    def test_quotes_with_limit_status(self) -> None:
        ts_codes = ["000001.SZ"]
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        limit_status = {"000001.SZ": {date(2024, 1, 2): "up_limit"}}

        df = make_quotes_df(ts_codes, trade_dates, limit_status=limit_status)

        limit_col = df.filter(pl.col("trade_date") == date(2024, 1, 2)).select("limit_status")
        assert limit_col.item() == "up_limit"

    def test_quotes_with_suspension(self) -> None:
        ts_codes = ["000001.SZ"]
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        is_tradable = {"000001.SZ": {date(2024, 1, 2): False, date(2024, 1, 3): True}}

        df = make_quotes_df(ts_codes, trade_dates, is_tradable=is_tradable)

        suspended = df.filter((pl.col("trade_date") == date(2024, 1, 2)) & (~pl.col("is_tradable")))
        assert len(suspended) == 1


class TestMakeSignalsDf:
    def test_basic_signals(self) -> None:
        signals = [
            {
                "signal_date": date(2024, 1, 2),
                "execution_date": date(2024, 1, 3),
                "ts_code": "000001.SZ",
                "signal_rank": 1,
            },
        ]
        df = make_signals_df(signals)

        assert len(df) == 1
        assert "signal_date" in df.columns
        assert "execution_date" in df.columns


class TestMakeBenchmarkDf:
    def test_basic_benchmark(self) -> None:
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        df = make_benchmark_df(trade_dates)

        assert len(df) == 3
        assert "pct_chg" in df.columns

    def test_benchmark_with_returns(self) -> None:
        trade_dates = [date(2024, 1, 2), date(2024, 1, 3)]
        daily_returns = [0.01, -0.005]
        df = make_benchmark_df(trade_dates, daily_returns)

        pct_chg = df["pct_chg"].to_list()
        assert pct_chg[0] == pytest.approx(1.0, rel=0.01)
        assert pct_chg[1] == pytest.approx(-0.5, rel=0.01)


class TestMakeBacktestConfig:
    def test_default_config(self) -> None:
        config = make_backtest_config()

        assert config.start_date == date(2024, 1, 1)
        assert config.end_date == date(2024, 1, 31)
        assert config.initial_capital == 1_000_000.0

    def test_custom_config(self) -> None:
        config = make_backtest_config(
            start_date=date(2024, 3, 1),
            end_date=date(2024, 3, 31),
            initial_capital=500_000.0,
        )

        assert config.start_date == date(2024, 3, 1)
        assert config.end_date == date(2024, 3, 31)
        assert config.initial_capital == 500_000.0


class TestBacktestTestFixture:
    def test_basic_fixture(self) -> None:
        fixture = BacktestTestFixture(
            start_date=date(2024, 1, 8),
            num_trade_days=5,
            num_stocks=2,
        )

        assert len(fixture.trade_dates) == 5
        assert len(fixture.ts_codes) == 2

    def test_get_basic_quotes(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=2)
        df = fixture.get_basic_quotes()

        assert len(df) == 10
        assert "raw_open" in df.columns
        assert "qfq_close" in df.columns

    def test_get_quotes_with_ex_dividend(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=2)
        df = fixture.get_quotes_with_ex_dividend(
            ex_div_date=fixture.trade_dates[2],
            adj_ratio=0.5,
        )

        assert "adj_factor" in df.columns

    def test_get_quotes_with_suspension(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=3)
        df = fixture.get_quotes_with_suspension(
            suspended_codes=[fixture.ts_codes[0]],
            suspended_dates=[fixture.trade_dates[0]],
        )

        suspended = df.filter(
            (pl.col("ts_code") == fixture.ts_codes[0])
            & (pl.col("trade_date") == fixture.trade_dates[0])
            & (~pl.col("is_tradable"))
        )
        assert len(suspended) == 1

    def test_get_quotes_with_limit(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=3)
        df = fixture.get_quotes_with_limit(
            up_limit_codes=[fixture.ts_codes[0]],
            limit_date=fixture.trade_dates[0],
        )

        up_limit = df.filter(
            (pl.col("ts_code") == fixture.ts_codes[0])
            & (pl.col("trade_date") == fixture.trade_dates[0])
            & (pl.col("limit_status") == "up_limit")
        )
        assert len(up_limit) == 1

    def test_get_fixed_signals(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=2)
        df = fixture.get_fixed_signals()

        assert len(df) > 0
        assert "signal_date" in df.columns
        assert "execution_date" in df.columns
        assert "ts_code" in df.columns
        assert "signal_rank" in df.columns

    def test_get_benchmark(self) -> None:
        fixture = BacktestTestFixture(num_trade_days=5, num_stocks=2)
        df = fixture.get_benchmark()

        assert len(df) == 5
        assert "pct_chg" in df.columns
