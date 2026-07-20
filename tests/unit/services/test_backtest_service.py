"""BacktestService 单元测试"""

import typing
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from data.cache.cache_manager import CacheManager
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

    @pytest.fixture
    def real_engine_factory(self):
        """提供真实 VectorBacktestEngine 工厂（保持端到端测试风格）。

        # 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
        # pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
        # 测试行为由测试用例本身验证。

                通过依赖注入传给 BacktestService，避免 services 层运行时导入 strategies。
        """

        def _factory(cache, config, data_processor):
            from strategies.backtest.engine import VectorBacktestEngine

            return VectorBacktestEngine(cache, config, data_processor=data_processor)

        return _factory

    @pytest.mark.asyncio
    async def test_service_runs_backtest_with_strategy_key(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
        real_engine_factory,
    ) -> None:
        service = BacktestService(
            cache=mock_cache,
            engine_factory=real_engine_factory,
            strategy_lookup=lambda k: MockStrategy if k == "mock_strategy" else None,
        )

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
        # 仅注入 strategy_lookup（返回 None）；engine_factory 不需要，因为 _get_strategy 先抛 ValueError
        service = BacktestService(
            cache=mock_cache,
            strategy_lookup=lambda k: None,
        )

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
        real_engine_factory,
    ) -> None:
        service = BacktestService(
            cache=mock_cache,
            engine_factory=real_engine_factory,
            strategy_lookup=lambda k: MockStrategy if k == "mock_strategy" else None,
        )

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
        real_engine_factory,
    ) -> None:
        # run_backtest_with_strategy 不调用 _get_strategy，仅需 engine_factory
        service = BacktestService(
            cache=mock_cache,
            engine_factory=real_engine_factory,
        )

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
        service = BacktestService(
            cache=MagicMock(),
            strategy_lookup=lambda k: MockStrategy if k == "mock_strategy" else None,
        )
        strategy = service._get_strategy("mock_strategy")

        assert strategy is not None
        assert typing.cast(typing.Any, strategy).key == "mock_strategy"

    def test_get_strategy_returns_none_for_unknown(self):
        """_get_strategy 对未知策略返回 None"""
        service = BacktestService(
            cache=MagicMock(),
            strategy_lookup=lambda k: None,
        )
        strategy = service._get_strategy("nonexistent")

        assert strategy is None

    def test_init_requires_cache_manager(self):
        """Task 6.7: BacktestService 必须注入 CacheManager，传 None 应 fail-fast。"""
        with pytest.raises(ValueError, match="CacheManager"):
            BacktestService(cache=typing.cast("CacheManager", None))

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

    # ------------------------------------------------------------------
    # 依赖注入 fail-late 分支测试（CLAUDE.md §3.1 R1 修复配套）
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_run_backtest_raises_when_engine_factory_none(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        """未注入 engine_factory 时，run_backtest 应 raise RuntimeError（fail-late）。"""
        service = BacktestService(
            cache=mock_cache,
            strategy_lookup=lambda k: MockStrategy if k == "mock_strategy" else None,
        )

        with pytest.raises(RuntimeError, match="engine_factory"):
            await service.run_backtest(
                strategy_key="mock_strategy",
                config=backtest_config,
                persist=False,
            )

    @pytest.mark.asyncio
    async def test_run_backtest_with_strategy_raises_when_engine_factory_none(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
    ) -> None:
        """未注入 engine_factory 时，run_backtest_with_strategy 应 raise RuntimeError（fail-late）。"""
        service = BacktestService(cache=mock_cache)

        with pytest.raises(RuntimeError, match="engine_factory"):
            await service.run_backtest_with_strategy(
                strategy=MockStrategy(),
                config=backtest_config,
                persist=False,
            )

    def test_get_strategy_raises_when_strategy_lookup_none(self):
        """未注入 strategy_lookup 时，_get_strategy 应 raise RuntimeError（fail-late）。"""
        service = BacktestService(cache=MagicMock())

        with pytest.raises(RuntimeError, match="strategy_lookup"):
            service._get_strategy("any_strategy")

    def test_get_strategy_returns_none_on_instantiate_error(self):
        """_get_strategy 实例化失败时应返回 None 并记录 error，不向上抛异常。"""

        class _BoomStrategy:
            def __init__(self):
                raise RuntimeError("boom")

        service = BacktestService(
            cache=MagicMock(),
            strategy_lookup=lambda k: _BoomStrategy,
        )

        with patch("services.backtest_service.logger.error") as mock_error:
            strategy = service._get_strategy("boom_strategy")

        assert strategy is None
        mock_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_result_returns_result_with_warning_on_save_failure(
        self,
        mock_cache: MagicMock,
    ) -> None:
        """_persist_result 在 save_result 抛异常时应返回带 persist_failed 警告的结果（fail-soft）。"""
        from datetime import datetime

        import polars as pl

        from strategies.backtest.config import BacktestConfig

        service = BacktestService(cache=mock_cache)

        config = BacktestConfig(
            start_date=date(2024, 1, 2),
            end_date=date(2024, 1, 4),
            initial_capital=1_000_000.0,
        )
        original_warnings: tuple = ()
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
            data_warnings=original_warnings,
            failed_signal_dates=(),
            run_id="run_002",
            executed_at=datetime(2024, 1, 4, 12, 0, 0),
            duration_ms=100,
        )

        mock_cache.backtest_dao.save_result = AsyncMock(side_effect=RuntimeError("db down"))

        returned = await service._persist_result(result)

        # fail-soft：返回原 result 但 data_warnings 末尾追加 persist_failed 前缀
        assert returned is not None
        assert len(returned.data_warnings) == 1
        assert returned.data_warnings[0].startswith("persist_failed: db down")

    @pytest.mark.asyncio
    async def test_run_backtest_with_strategy_persists_results(
        self,
        mock_cache: MagicMock,
        backtest_config: BacktestConfig,
        real_engine_factory,
    ) -> None:
        """run_backtest_with_strategy 在 persist=True 时应调用 save_result（与 run_backtest 对称）。"""
        service = BacktestService(
            cache=mock_cache,
            engine_factory=real_engine_factory,
        )

        await service.run_backtest_with_strategy(
            strategy=MockStrategy(),
            config=backtest_config,
            persist=True,
        )

        mock_cache.backtest_dao.save_result.assert_called_once()

    def test_get_strategy_returns_none_when_lookup_returns_non_type(self):
        """strategy_lookup 返回非 type 对象（如字符串）时，_get_strategy 应捕获 TypeError 返回 None。"""
        service = BacktestService(
            cache=MagicMock(),
            strategy_lookup=lambda k: "not a class",  # type: ignore[return-value]  # 故意测试非 type 输入
        )

        with patch("services.backtest_service.logger.error") as mock_error:
            strategy = service._get_strategy("any_key")

        assert strategy is None
        mock_error.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_result_uses_dev_fallback_when_version_lookup_fails(
        self,
        mock_cache: MagicMock,
    ) -> None:
        """_get_app_version 在 importlib.metadata.version 抛异常时应返回 'dev' fallback。"""
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
            run_id="run_003",
            executed_at=datetime(2024, 1, 4, 12, 0, 0),
            duration_ms=100,
        )

        with patch("importlib.metadata.version", side_effect=ModuleNotFoundError("no package")):
            await service._persist_result(result)

        saved_dict = mock_cache.backtest_dao.save_result.call_args[0][0]
        assert saved_dict["app_version"] == "dev"
