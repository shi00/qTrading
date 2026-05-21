"""BacktestDataProvider 测试"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from strategies.backtest.data_provider import BacktestDataProvider


class TestBacktestDataProvider:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "close": [10.0, 20.0],
                    "is_tradable": [True, True],
                }
            )
        )
        cache.get_daily_indicators = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                }
            )
        )
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_build_context_contains_required_fields(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "trade_date" in context
        assert "is_backtest" in context
        assert context["is_backtest"] is True
        assert "screening_data" in context
        assert "_diagnostics" in context

    @pytest.mark.asyncio
    async def test_build_context_disables_ai(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2), disable_ai=True)

        assert context.get("_disable_ai") is True

    @pytest.mark.asyncio
    async def test_build_context_enables_ai(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2), disable_ai=False)

        assert "_disable_ai" not in context

    @pytest.mark.asyncio
    async def test_build_context_filters_suspended_stocks(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "trade_date": ["20240102", "20240102", "20240102"],
                    "close": [10.0, 20.0, 30.0],
                    "is_tradable": [True, False, True],
                }
            )
        )

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context["screening_data"] is not None
        assert len(context["screening_data"]) == 2
        assert "000002.SZ" not in context["screening_data"]["ts_code"].values

    @pytest.mark.asyncio
    async def test_build_context_handles_missing_is_tradable(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "close": [10.0, 20.0],
                }
            )
        )

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context["screening_data"] is not None
        assert len(context["screening_data"]) == 2

    @pytest.mark.asyncio
    async def test_build_context_handles_empty_screening_data(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context["screening_data"] is not None
        assert context["screening_data"].empty
        assert context["_diagnostics"]["base_complete"] is False

    @pytest.mark.asyncio
    async def test_build_context_includes_diagnostics(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        diagnostics = context.get("_diagnostics", {})
        assert "trade_date" in diagnostics
        assert "base_complete" in diagnostics
        assert "strategy_ready" in diagnostics
        assert "table_status" in diagnostics
