"""BacktestService 单元测试"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from services.backtest_service import BacktestService
from strategies.base_strategy import BaseStrategy

pytestmark = pytest.mark.unit


class MockStrategy(BaseStrategy):
    required_context_keys = ()

    def __init__(self):
        super().__init__("mock_strategy", "Mock Strategy for Testing")

    async def filter(self, context):
        import pandas as pd

        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "close": [10.0, 20.0],
            }
        )


class TestBacktestService:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()

        trade_dates = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
        cal_df = pd.DataFrame(
            {
                "cal_date": [d.strftime("%Y%m%d") for d in trade_dates],
                "is_open": ["1"] * len(trade_dates),
            }
        )
        cache.get_trade_cal = AsyncMock(return_value=cal_df)

        quotes_df = pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"] * 3,
                "trade_date": [
                    date(2024, 1, 2),
                    date(2024, 1, 2),
                    date(2024, 1, 3),
                    date(2024, 1, 3),
                    date(2024, 1, 4),
                    date(2024, 1, 4),
                ],
                "open": [10.0, 20.0, 10.5, 21.0, 11.0, 22.0],
                "high": [10.5, 21.0, 11.0, 22.0, 11.5, 23.0],
                "low": [9.5, 19.0, 10.0, 20.0, 10.5, 21.0],
                "close": [10.2, 20.5, 10.8, 21.5, 11.2, 22.5],
                "vol": [1000000, 2000000, 1100000, 2200000, 1200000, 2400000],
                "amount": [10000000, 40000000, 11000000, 45000000, 12000000, 50000000],
                "adj_factor": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "is_tradable": [True, True, True, True, True, True],
            }
        )
        cache.get_daily_quotes = AsyncMock(return_value=quotes_df)

        benchmark_df = pd.DataFrame(
            {
                "ts_code": ["000300.SH"] * 3,
                "trade_date": [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)],
                "pct_chg": [0.1, 0.1, 0.1],
                "close": [3000.0, 3010.0, 3020.0],
            }
        )
        cache.get_index_daily_range = AsyncMock(return_value=benchmark_df)

        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

        backtest_dao = MagicMock()
        backtest_dao.save_result = AsyncMock(return_value=1)
        backtest_dao.get_result = AsyncMock(return_value=None)
        backtest_dao.list_results = AsyncMock(return_value=[])
        backtest_dao.delete_result = AsyncMock(return_value=True)
        cache.backtest_dao = backtest_dao

        return cache

    @pytest.fixture
    def backtest_config(self) -> BacktestConfig:
        return BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_capital=1_000_000.0,
        )

    @pytest.mark.asyncio
    async def test_service_runs_backtest_with_strategy_key(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        with patch(
            "strategies.base_strategy.get_strategy_registry",
            return_value={"mock_strategy": MockStrategy},
        ):
            service = BacktestService(cache=mock_cache)

            result = await service.run_backtest(
                strategy_key="mock_strategy",
                config=backtest_config,
                persist=False,
            )

            assert result is not None
            assert isinstance(result, BacktestResult)
            assert result.strategy_name == "mock_strategy"

    @pytest.mark.asyncio
    async def test_service_raises_on_unknown_strategy(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        with patch(
            "strategies.base_strategy.get_strategy_registry",
            return_value={},
        ):
            service = BacktestService(cache=mock_cache)

            with pytest.raises(ValueError, match="Strategy not found"):
                await service.run_backtest(
                    strategy_key="unknown_strategy",
                    config=backtest_config,
                )

    @pytest.mark.asyncio
    async def test_service_persists_results(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        with patch(
            "strategies.base_strategy.get_strategy_registry",
            return_value={"mock_strategy": MockStrategy},
        ):
            service = BacktestService(cache=mock_cache)

            await service.run_backtest(
                strategy_key="mock_strategy",
                config=backtest_config,
                persist=True,
            )

            mock_cache.backtest_dao.save_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_service_runs_backtest_with_strategy_instance(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        service = BacktestService(cache=mock_cache)

        strategy = MockStrategy()

        result = await service.run_backtest_with_strategy(
            strategy=strategy,
            config=backtest_config,
            persist=False,
        )

        assert result is not None
        assert isinstance(result, BacktestResult)
        assert result.strategy_name == "mock_strategy"

    @pytest.mark.asyncio
    async def test_service_gets_result(
        self,
        mock_cache: MagicMock,
    ) -> None:
        service = BacktestService(cache=mock_cache)

        mock_cache.backtest_dao.get_result = AsyncMock(return_value={"run_id": "test123", "strategy_name": "test"})

        result = await service.get_result("test123")

        assert result is not None
        assert result["run_id"] == "test123"

    @pytest.mark.asyncio
    async def test_service_lists_results(
        self,
        mock_cache: MagicMock,
    ) -> None:
        service = BacktestService(cache=mock_cache)

        mock_cache.backtest_dao.list_results = AsyncMock(
            return_value=[
                {"run_id": "test1", "strategy_name": "strategy1"},
                {"run_id": "test2", "strategy_name": "strategy2"},
            ]
        )

        results = await service.list_results()

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_service_deletes_result(
        self,
        mock_cache: MagicMock,
    ) -> None:
        service = BacktestService(cache=mock_cache)

        success = await service.delete_result("test123")

        assert success is True
        mock_cache.backtest_dao.delete_result.assert_called_once_with("test123")

    def test_get_strategy_sets_key_attribute(self):
        """_get_strategy 应设置 instance.key = strategy_key"""
        with patch(
            "strategies.base_strategy.get_strategy_registry",
            return_value={"mock_strategy": MockStrategy},
        ):
            service = BacktestService(cache=MagicMock())
            strategy = service._get_strategy("mock_strategy")

            assert strategy is not None
            assert strategy.key == "mock_strategy"

    def test_get_strategy_returns_none_for_unknown(self):
        """_get_strategy 对未知策略返回 None"""
        with patch(
            "strategies.base_strategy.get_strategy_registry",
            return_value={},
        ):
            service = BacktestService(cache=MagicMock())
            strategy = service._get_strategy("nonexistent")

            assert strategy is None

    def test_init_requires_cache_manager(self):
        """Task 6.7: BacktestService 必须注入 CacheManager，传 None 应 fail-fast。"""
        with pytest.raises(ValueError, match="CacheManager"):
            BacktestService(cache=None)

    @pytest.mark.asyncio
    async def test_persist_result_uses_to_persist_dict_and_adds_app_version(
        self,
        mock_cache: MagicMock,
    ) -> None:
        """Task 6.10: _persist_result 应调用 to_persist_dict() 并补充 app_version。"""
        from datetime import datetime

        import polars as pl

        from strategies.backtest.config import BacktestConfig

        service = BacktestService(cache=mock_cache)

        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_capital=1_000_000.0,
        )
        result = BacktestResult(
            config=config,
            strategy_name="mock_strategy",
            params_snapshot={"p": 1},
            nav_curve=pl.DataFrame({"trade_date": [date(2024, 1, 2)], "nav": [1_000_000.0]}),
            daily_returns=pl.Series([0.0]),
            benchmark_returns=pl.Series([0.0]),
            trades=pl.DataFrame(),
            positions=pl.DataFrame(),
            skipped_orders=pl.DataFrame(),
            metrics={"total_return": 0.0},
            ic_series=pl.Series([0.0]),
            period_stats=pl.DataFrame(),
            data_warnings=(),
            failed_signal_dates=(),
            run_id="run_001",
            executed_at=datetime(2024, 1, 4, 12, 0, 0),
            duration_ms=100,
        )

        await service._persist_result(result)

        mock_cache.backtest_dao.save_result.assert_called_once()
        saved_dict = mock_cache.backtest_dao.save_result.call_args[0][0]
        assert saved_dict["run_id"] == "run_001"
        assert saved_dict["strategy_name"] == "mock_strategy"
        assert saved_dict["start_date"] == config.start_date
        assert saved_dict["initial_capital"] == config.initial_capital
        assert saved_dict["execution_price"] == config.execution_price
        assert "app_version" in saved_dict
        assert saved_dict["metrics"] == result.metrics
