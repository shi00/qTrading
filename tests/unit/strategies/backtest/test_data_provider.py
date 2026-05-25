"""BacktestDataProvider 测试

验证 BacktestDataProvider 使用 ScreenerDao 标准 SQL 获取数据，
确保回测路径与实盘路径一致。
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from strategies.backtest.data_provider import BacktestDataProvider


class TestBacktestDataProvider:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        """Mock CacheManager，返回符合 ScreenerDao SQL 结构的数据。"""
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "name": ["平安银行", "万科A"],
                    "close": [10.0, 20.0],
                    "pe_ttm": [8.5, 12.3],
                    "pb": [1.2, 2.1],
                    "total_mv": [1500000000, 2800000000],
                    "turnover_rate": [3.5, 5.2],
                    "is_tradable": [True, True],
                    "list_status": ["L", "L"],
                    "roe": [12.5, 15.3],
                    "or_yoy": [10.2, 25.8],
                    "netprofit_yoy": [8.5, 20.3],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "name": ["平安银行", "万科A"],
                    "roe": [12.5, 15.3],
                    "or_yoy": [10.2, 25.8],
                    "netprofit_yoy": [8.5, 20.3],
                    "grossprofit_margin": [35.2, 42.1],
                    "debt_to_assets": [65.3, 78.5],
                    "is_tradable": [True, True],
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
        mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "trade_date": ["20240102", "20240102", "20240102"],
                    "close": [10.0, 20.0, 30.0],
                    "is_tradable": [True, False, True],
                    "turnover_rate": [3.5, 5.2, 4.8],
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
        mock_cache.get_screening_data = AsyncMock(
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
        mock_cache.get_screening_data = AsyncMock(return_value=pd.DataFrame())

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

    @pytest.mark.asyncio
    async def test_screening_data_includes_turnover_rate(self, mock_cache: MagicMock) -> None:
        """验证 screening_data 包含 turnover_rate 字段（来自 ScreenerDao 标准 SQL）。"""
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        screening_data = context["screening_data"]
        assert screening_data is not None
        assert "turnover_rate" in screening_data.columns

    @pytest.mark.asyncio
    async def test_fundamental_data_includes_roe_and_yoy_fields(self, mock_cache: MagicMock) -> None:
        """验证 fundamental_screening_data 包含 roe, or_yoy, netprofit_yoy 字段。"""
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        fundamental_data = context.get("fundamental_screening_data")
        assert fundamental_data is not None
        assert "roe" in fundamental_data.columns
        assert "or_yoy" in fundamental_data.columns
        assert "netprofit_yoy" in fundamental_data.columns

    @pytest.mark.asyncio
    async def test_screening_data_includes_list_status(self, mock_cache: MagicMock) -> None:
        """验证 screening_data 包含 list_status 字段（来自 ScreenerDao 标准 SQL）。

        注意：退市股票过滤逻辑在 ScreenerDao.get_screening_data() 的 SQL 中实现，
        BacktestDataProvider 只是调用该方法获取数据。
        """
        mock_cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "close": [10.0, 20.0],
                    "list_status": ["L", "D"],
                    "is_tradable": [True, True],
                    "turnover_rate": [3.5, 5.2],
                }
            )
        )

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        screening_data = context["screening_data"]
        assert screening_data is not None
        assert "list_status" in screening_data.columns


class TestBacktestDataProviderWithProcessor:
    """测试 data_processor 注入路径。"""

    @pytest.fixture
    def mock_processor(self) -> MagicMock:
        processor = MagicMock()
        processor.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "turnover_rate": [3.5],
                    "is_tradable": [True],
                }
            )
        )
        processor.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "roe": [12.5],
                }
            )
        )
        return processor

    @pytest.fixture
    def mock_cache_for_processor(self) -> MagicMock:
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_uses_processor_when_available(
        self,
        mock_processor: MagicMock,
        mock_cache_for_processor: MagicMock,
    ) -> None:
        """当 data_processor 存在时，优先使用 processor 的数据。"""
        provider = BacktestDataProvider(mock_cache_for_processor, data_processor=mock_processor)

        context = await provider.build_context(date(2024, 1, 2))

        mock_processor.get_screening_data.assert_called_once()
        mock_processor.get_fundamental_screening_data.assert_called_once()
        mock_cache_for_processor.get_screening_data.assert_not_called()
        mock_cache_for_processor.get_fundamental_screening_data.assert_not_called()

        screening_data = context["screening_data"]
        assert screening_data is not None
        assert "turnover_rate" in screening_data.columns
