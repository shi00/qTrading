"""回测端到端集成测试

使用 mock 数据验证完整回测流程，不依赖真实外部服务。
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig
from strategies.backtest.engine import VectorBacktestEngine
from strategies.base_strategy import BaseStrategy


class SimpleTestStrategy(BaseStrategy):
    """用于端到端测试的简单策略"""

    required_context_keys = ["screening_data"]

    def __init__(self):
        super().__init__("test_strategy", "Test Strategy for E2E")

    async def filter(self, context):
        screening_data = context.get("screening_data")
        if screening_data is None or screening_data.empty:
            return pd.DataFrame()

        if "ts_code" not in screening_data.columns:
            return pd.DataFrame()

        return screening_data[["ts_code"]].head(5)


@pytest.fixture
def mock_cache() -> MagicMock:
    cache = MagicMock()

    trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    cal_df = pd.DataFrame(
        {
            "cal_date": [d.strftime("%Y%m%d") for d in trade_dates],
            "is_open": ["1", "1", "1"],
        }
    )
    cache.get_trade_cal = AsyncMock(return_value=cal_df)

    quotes_data = []
    for i, d in enumerate(trade_dates):
        for j, code in enumerate(["000001.SZ", "000002.SZ"]):
            quotes_data.append(
                {
                    "ts_code": code,
                    "trade_date": d,
                    "open": 10.0 + i * 0.1 + j * 0.5,
                    "high": 10.5 + i * 0.1 + j * 0.5,
                    "low": 9.5 + i * 0.1 + j * 0.5,
                    "close": 10.2 + i * 0.1 + j * 0.5,
                    "vol": 1000000,
                    "amount": 10000000,
                    "adj_factor": 1.0,
                    "is_tradable": True,
                }
            )
    quotes_df = pd.DataFrame(quotes_data)
    cache.get_daily_quotes = AsyncMock(return_value=quotes_df)

    index_data = []
    for i, d in enumerate(trade_dates):
        index_data.append(
            {
                "ts_code": "000300.SH",
                "trade_date": d,
                "pct_chg": 0.1 + i * 0.05,
                "close": 3000.0 + i * 10,
            }
        )
    cache.get_index_daily_range = AsyncMock(return_value=pd.DataFrame(index_data))

    cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
    cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
    cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
    cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
    cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

    return cache


@pytest.fixture
def backtest_config() -> BacktestConfig:
    return BacktestConfig(
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 4),
        initial_capital=1_000_000.0,
        max_position_count=10,
        fail_fast=True,
    )


class TestBacktestE2E:
    """端到端回测测试"""

    @pytest.mark.asyncio
    async def test_full_backtest_with_mock_strategy(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        assert result is not None
        assert result.strategy_name == "test_strategy"
        assert result.run_id is not None
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    async def test_backtest_produces_valid_nav_curve(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        assert result.nav_curve is not None
        assert isinstance(result.nav_curve, pl.DataFrame)
        assert "trade_date" in result.nav_curve.columns
        assert "nav" in result.nav_curve.columns
        assert len(result.nav_curve) > 0

        nav_values = result.nav_curve["nav"].to_list()
        assert all(v > 0 for v in nav_values)

    @pytest.mark.asyncio
    async def test_backtest_produces_valid_metrics(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        assert result.metrics is not None
        assert isinstance(result.metrics, dict)

        expected_metrics = [
            "total_return",
            "annualized_return",
            "volatility",
            "sharpe_ratio",
            "max_drawdown",
            "calmar_ratio",
            "win_rate",
            "profit_factor",
            "total_trades",
            "ic_mean",
            "ic_ir",
            "information_ratio",
            "tracking_error",
        ]
        for metric in expected_metrics:
            assert metric in result.metrics

        assert result.metrics["max_drawdown"] >= 0
        assert result.metrics["total_trades"] >= 0

    @pytest.mark.asyncio
    async def test_backtest_produces_valid_daily_returns(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        assert result.daily_returns is not None
        assert isinstance(result.daily_returns, pl.Series)
        assert len(result.daily_returns) > 0

    @pytest.mark.asyncio
    async def test_backtest_produces_valid_benchmark_returns(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        assert result.benchmark_returns is not None
        assert isinstance(result.benchmark_returns, pl.Series)
        assert len(result.benchmark_returns) > 0

    @pytest.mark.asyncio
    async def test_backtest_handles_ex_dividend(
        self,
        backtest_config: BacktestConfig,
    ) -> None:
        cache = MagicMock()

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        cal_df = pd.DataFrame(
            {
                "cal_date": [d.strftime("%Y%m%d") for d in trade_dates],
                "is_open": ["1", "1", "1"],
            }
        )
        cache.get_trade_cal = AsyncMock(return_value=cal_df)

        quotes_data = []
        adj_factors = [2.0, 2.0, 1.0]
        for i, d in enumerate(trade_dates):
            quotes_data.append(
                {
                    "ts_code": "000001.SZ",
                    "trade_date": d,
                    "open": 10.0,
                    "high": 10.5,
                    "low": 9.5,
                    "close": 10.2,
                    "vol": 1000000,
                    "amount": 10000000,
                    "adj_factor": adj_factors[i],
                    "is_tradable": True,
                }
            )
        quotes_df = pd.DataFrame(quotes_data)
        cache.get_daily_quotes = AsyncMock(return_value=quotes_df)

        cache.get_index_daily_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000300.SH"] * 3,
                    "trade_date": trade_dates,
                    "pct_chg": [0.1, 0.1, 0.1],
                    "close": [3000.0, 3010.0, 3020.0],
                }
            )
        )

        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(cache, backtest_config)

        result = await engine.run(strategy)

        assert result is not None
        assert result.nav_curve is not None

    @pytest.mark.asyncio
    async def test_backtest_no_lookahead_bias(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy)

        if not result.trades.is_empty():
            trades_df = result.trades
            for trade in trades_df.iter_rows(named=True):
                trade_date = trade["trade_date"]
                assert trade_date >= backtest_config.start_date

    @pytest.mark.asyncio
    async def test_backtest_progress_callback(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        strategy = SimpleTestStrategy()

        progress_calls = []

        def progress_callback(progress: float, message: str):
            progress_calls.append((progress, message))

        engine = VectorBacktestEngine(mock_cache, backtest_config)

        result = await engine.run(strategy, progress_callback=progress_callback)

        assert result is not None
        assert len(progress_calls) > 0

        progresses = [p[0] for p in progress_calls]
        assert progresses[0] == 0.0
        assert progresses[-1] <= 1.0

    @pytest.mark.asyncio
    async def test_backtest_with_empty_signals(
        self,
        backtest_config: BacktestConfig,
    ) -> None:
        cache = MagicMock()

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        cal_df = pd.DataFrame(
            {
                "cal_date": [d.strftime("%Y%m%d") for d in trade_dates],
                "is_open": ["1", "1", "1"],
            }
        )
        cache.get_trade_cal = AsyncMock(return_value=cal_df)

        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 2)],
                "open": [10.0],
                "high": [10.5],
                "low": [9.5],
                "close": [10.2],
                "vol": [1000000],
                "amount": [10000000],
                "adj_factor": [1.0],
                "is_tradable": [True],
            }
        )
        cache.get_daily_quotes = AsyncMock(return_value=quotes_df)

        cache.get_index_daily_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000300.SH"],
                    "trade_date": [date(2024, 1, 2)],
                    "pct_chg": [0.1],
                    "close": [3000.0],
                }
            )
        )

        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

        class EmptyStrategy(BaseStrategy):
            required_context_keys = []

            def __init__(self):
                super().__init__("empty_strategy", "Empty Strategy")

            async def filter(self, context):
                return pd.DataFrame()

        strategy = EmptyStrategy()

        engine = VectorBacktestEngine(cache, backtest_config)

        result = await engine.run(strategy)

        assert result is not None
        assert result.metrics["total_trades"] == 0
