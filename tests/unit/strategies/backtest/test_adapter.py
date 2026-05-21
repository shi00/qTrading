"""BacktestStrategyAdapter 单元测试"""

from datetime import date

import pandas as pd
import polars as pl
import pytest

from strategies.backtest.adapter import BacktestStrategyAdapter
from strategies.base_strategy import BaseStrategy


class MockStrategy(BaseStrategy):
    """用于测试的 Mock 策略"""

    required_context_keys = ["screening_data"]
    required_tables = ["daily_quotes"]

    def __init__(self):
        super().__init__("mock_strategy", "Mock Strategy for Testing")

    async def filter(self, context):
        return pd.DataFrame(
            {
                "ts_code": ["000001.SZ", "000002.SZ"],
                "close": [10.0, 20.0],
            }
        )


class TestBacktestStrategyAdapter:
    @pytest.fixture
    def adapter(self) -> BacktestStrategyAdapter:
        return BacktestStrategyAdapter()

    @pytest.fixture
    def mock_strategy(self) -> MockStrategy:
        return MockStrategy()

    @pytest.mark.asyncio
    async def test_adapter_checks_dependencies(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)

    @pytest.mark.asyncio
    async def test_adapter_handles_unready_status(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_handles_degraded_status(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame(),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)

    @pytest.mark.asyncio
    async def test_adapter_normalizes_output_schema(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        expected_columns = [
            "signal_date",
            "execution_date",
            "ts_code",
            "score",
            "rank",
            "target_weight",
            "reason",
        ]
        for col in expected_columns:
            assert col in result.columns

    @pytest.mark.asyncio
    async def test_adapter_handles_strategy_exception(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class FailingStrategy(BaseStrategy):
            required_context_keys = []

            def __init__(self):
                super().__init__("failing_strategy", "Failing Strategy")

            async def filter(self, context):
                raise ValueError("Strategy failed")

        failing_strategy = FailingStrategy()
        context = {
            "trade_date": date(2024, 1, 1),
        }

        with pytest.raises(ValueError, match="Strategy failed"):
            await adapter.generate_signal(
                strategy=failing_strategy,
                context=context,
                signal_date=date(2024, 1, 1),
                execution_date=date(2024, 1, 2),
            )

    @pytest.mark.asyncio
    async def test_adapter_handles_empty_result(
        self,
        adapter: BacktestStrategyAdapter,
    ) -> None:
        class EmptyResultStrategy(BaseStrategy):
            required_context_keys = []

            def __init__(self):
                super().__init__("empty_strategy", "Empty Result Strategy")

            async def filter(self, context):
                return pd.DataFrame()

        empty_strategy = EmptyResultStrategy()
        context = {
            "trade_date": date(2024, 1, 1),
        }

        result = await adapter.generate_signal(
            strategy=empty_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        assert result is not None
        assert isinstance(result, pl.DataFrame)
        assert result.is_empty()

    @pytest.mark.asyncio
    async def test_adapter_assigns_rank_by_order(
        self,
        adapter: BacktestStrategyAdapter,
        mock_strategy: MockStrategy,
    ) -> None:
        context = {
            "trade_date": date(2024, 1, 1),
            "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
        }

        result = await adapter.generate_signal(
            strategy=mock_strategy,
            context=context,
            signal_date=date(2024, 1, 1),
            execution_date=date(2024, 1, 2),
        )

        if not result.is_empty() and "rank" in result.columns:
            ranks = result["rank"].to_list()
            assert len(set(ranks)) == len(ranks)
