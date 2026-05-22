"""strategies/backtest/data_provider.py 补充测试 - 辅助表加载、异常处理"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from strategies.backtest.data_provider import BacktestDataProvider


class TestBacktestDataProviderAuxiliaryTables:
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
                    "is_tradable": [True],
                }
            )
        )
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame({"data": [1]}))
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame({"data": [2]}))
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame({"data": [3]}))
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame({"data": [4]}))
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame({"data": [5]}))
        return cache

    @pytest.mark.asyncio
    async def test_auxiliary_tables_loaded_successfully(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "northbound_data" in context
        assert "northbound_flow_data" in context
        assert "moneyflow_data" in context
        assert "top_list" in context
        assert "block_trade" in context

    @pytest.mark.asyncio
    async def test_auxiliary_table_failure_sets_ready_false(self, mock_cache: MagicMock) -> None:
        mock_cache.get_northbound = AsyncMock(side_effect=Exception("network error"))

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("strategy_ready") is False
        assert "northbound_data" in diagnostics.get("table_status", {})
        assert diagnostics["table_status"]["northbound_data"].get("ready") is False

    @pytest.mark.asyncio
    async def test_auxiliary_table_returns_none(self, mock_cache: MagicMock) -> None:
        mock_cache.get_northbound = AsyncMock(return_value=None)

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        diagnostics = context.get("_diagnostics", {})
        assert "northbound_data" in diagnostics.get("table_status", {})
        assert diagnostics["table_status"]["northbound_data"].get("ready") is False

    @pytest.mark.asyncio
    async def test_auxiliary_table_empty_dataframe(self, mock_cache: MagicMock) -> None:
        mock_cache.get_northbound = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        diagnostics = context.get("_diagnostics", {})
        assert "northbound_data" in diagnostics.get("table_status", {})
        assert diagnostics["table_status"]["northbound_data"].get("ready") is True
        assert diagnostics["table_status"]["northbound_data"].get("rows") == 0


class TestBacktestDataProviderFundamentalData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
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
    async def test_fundamental_data_with_is_tradable(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_indicators = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "is_tradable": [True, False],
                }
            )
        )

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        fundamental_data = context.get("fundamental_screening_data")
        assert fundamental_data is not None
        assert len(fundamental_data) == 1
        assert fundamental_data["ts_code"].iloc[0] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_fundamental_data_none(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_indicators = AsyncMock(return_value=None)

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "fundamental_screening_data" not in context
        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("table_status", {}).get("fundamental_screening_data", {}).get("ready") is False

    @pytest.mark.asyncio
    async def test_fundamental_data_empty(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "fundamental_screening_data" not in context


class TestBacktestDataProviderScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_screening_data_get_failure(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(side_effect=Exception("db error"))

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context["screening_data"] is None
        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("base_complete") is False

    @pytest.mark.asyncio
    async def test_screening_data_returns_none(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(return_value=None)

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context["screening_data"] is None


class TestBacktestDataProviderNormalizeTradeDate:
    def test_date_input(self):
        result = BacktestDataProvider._normalize_trade_date(date(2024, 1, 2))
        assert result == "20240102"

    def test_string_input(self):
        result = BacktestDataProvider._normalize_trade_date("20240102")
        assert result == "20240102"

    def test_other_input(self):
        result = BacktestDataProvider._normalize_trade_date(20240102)
        assert result == "20240102"


class TestBacktestDataProviderDiagnostics:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                }
            )
        )
        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_diagnostics_contains_all_tables(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        diagnostics = context.get("_diagnostics", {})
        assert "quality_tier" in diagnostics
        assert "trade_date" in diagnostics
        assert "base_complete" in diagnostics
        assert "strategy_ready" in diagnostics
        assert "table_status" in diagnostics

        table_status = diagnostics.get("table_status", {})
        expected_tables = [
            "screening_data",
            "fundamental_screening_data",
            "northbound_data",
            "northbound_flow_data",
            "moneyflow_data",
            "top_list",
            "block_trade",
        ]
        for table in expected_tables:
            assert table in table_status

    @pytest.mark.asyncio
    async def test_suspended_filtered_count_in_diagnostics(self, mock_cache: MagicMock) -> None:
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

        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("suspended_filtered") == 1


class TestBacktestDataProviderBuildContext:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                }
            )
        )
        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_build_context_with_disable_ai_true(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2), disable_ai=True)

        assert context.get("_disable_ai") is True

    @pytest.mark.asyncio
    async def test_build_context_with_disable_ai_false(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2), disable_ai=False)

        assert "_disable_ai" not in context

    @pytest.mark.asyncio
    async def test_build_context_sets_is_backtest(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context.get("is_backtest") is True

    @pytest.mark.asyncio
    async def test_build_context_sets_trade_date(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context.get("trade_date") == "20240102"


class TestBacktestDataProviderGetScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_indicators = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_get_screening_data_exception(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_quotes = AsyncMock(side_effect=Exception("connection refused"))

        provider = BacktestDataProvider(mock_cache)

        result = await provider._get_screening_data("20240102")

        assert result is None


class TestBacktestDataProviderGetFundamentalScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_daily_quotes = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
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
    async def test_get_fundamental_screening_data_exception(self, mock_cache: MagicMock) -> None:
        mock_cache.get_daily_indicators = AsyncMock(side_effect=Exception("db timeout"))

        provider = BacktestDataProvider(mock_cache)

        result = await provider._get_fundamental_screening_data("20240102")

        assert result is None
