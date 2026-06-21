"""BacktestDataProvider 测试

验证 BacktestDataProvider 使用 ScreenerDao 标准 SQL 获取数据，
确保回测路径与实盘路径一致。
"""

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from strategies.backtest.data_provider import BacktestDataProvider

pytestmark = pytest.mark.unit


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

        sd = context.get("screening_data")
        assert sd is not None
        assert len(sd) == 2
        assert "000002.SZ" not in sd["ts_code"].values

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

        sd = context.get("screening_data")
        assert sd is not None
        assert len(sd) == 2

    @pytest.mark.asyncio
    async def test_build_context_handles_empty_screening_data(self, mock_cache: MagicMock) -> None:
        mock_cache.get_screening_data = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        sd = context.get("screening_data")
        assert sd is not None
        assert sd.empty
        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("base_complete") is False

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

        screening_data = context.get("screening_data")
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

        screening_data = context.get("screening_data")
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

        screening_data = context.get("screening_data")
        assert screening_data is not None
        assert "turnover_rate" in screening_data.columns


class TestBacktestQualityProxy:
    """验证 _BacktestQualityProxy 满足质量门控契约。"""

    def test_default_tier_is_gold(self) -> None:
        from data.persistence.quality_gate import QualityTier

        from strategies.backtest.data_provider import _BacktestQualityProxy

        proxy = _BacktestQualityProxy()
        assert proxy._quality_tier == int(QualityTier.GOLD)

    def test_custom_tier(self) -> None:
        from data.persistence.quality_gate import QualityTier

        from strategies.backtest.data_provider import _BacktestQualityProxy

        proxy = _BacktestQualityProxy(tier=QualityTier.SILVER)
        assert proxy._quality_tier == int(QualityTier.SILVER)

    def test_check_tier_passes_with_gold_proxy(self) -> None:
        """GOLD proxy 应通过任何质量门控检查，不抛 QualityGateError。"""
        from data.persistence.quality_gate import QualityTier, _check_tier

        from strategies.backtest.data_provider import _BacktestQualityProxy

        proxy = _BacktestQualityProxy()
        _check_tier(proxy, QualityTier.GOLD, "test_func")

    def test_check_tier_passes_with_silver_proxy_for_silver_requirement(self) -> None:
        from data.persistence.quality_gate import QualityTier, _check_tier

        from strategies.backtest.data_provider import _BacktestQualityProxy

        proxy = _BacktestQualityProxy(tier=QualityTier.SILVER)
        _check_tier(proxy, QualityTier.SILVER, "test_func")

    def test_check_tier_raises_when_proxy_tier_too_low(self) -> None:
        """SILVER proxy 不满足 GOLD 要求时应抛 QualityGateError。"""
        from data.persistence.quality_gate import (
            QualityGateError,
            QualityTier,
            _check_tier,
        )

        from strategies.backtest.data_provider import _BacktestQualityProxy

        proxy = _BacktestQualityProxy(tier=QualityTier.SILVER)
        with pytest.raises(QualityGateError):
            _check_tier(proxy, QualityTier.GOLD, "test_func")

    @pytest.mark.asyncio
    async def test_proxy_reused_across_build_context_calls(self) -> None:
        """验证 BacktestDataProvider 复用 proxy 实例。"""
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(cache)
        assert provider._quality_proxy is not None

        ctx1 = await provider.build_context(date(2024, 1, 2))
        ctx2 = await provider.build_context(date(2024, 1, 3))

        assert ctx1.get("data_processor") is ctx2.get("data_processor")
        assert ctx1.get("data_processor") is provider._quality_proxy

    def test_no_proxy_when_data_processor_provided(self) -> None:
        """当 data_processor 存在时，不应创建 proxy。"""
        cache = MagicMock()
        processor = MagicMock()
        provider = BacktestDataProvider(cache, data_processor=processor)
        assert provider._quality_proxy is None

    @pytest.mark.asyncio
    async def test_preload_range_success(self) -> None:
        """验证 preload_range 成功读取数据并在 build_context 中进行内存切片。"""
        cache = MagicMock()
        cache.get_screening_data_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240103"],
                    "close": [10.0, 20.0],
                    "is_tradable": [True, True],
                }
            )
        )
        cache.get_fundamental_screening_data_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade_range = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(cache)
        await provider.preload_range(date(2024, 1, 2), date(2024, 1, 3))

        assert provider._preloaded is not None
        assert "screening_data" in provider._preloaded

        # 验证 build_context 时是否直接从预加载数据中切片，而不触发 daily 查询
        cache.get_screening_data.assert_not_called()

        ctx = await provider.build_context(date(2024, 1, 2))
        screening_data = ctx.get("screening_data")
        assert len(screening_data) == 1
        assert screening_data.iloc[0]["ts_code"] == "000001.SZ"

    @pytest.mark.asyncio
    async def test_preload_range_fallback(self) -> None:
        """验证在预加载抛出异常时，能够优雅降级回单日查询逻辑。"""
        cache = MagicMock()
        # 范围查询抛出异常
        cache.get_screening_data_range = AsyncMock(side_effect=Exception("DB Error"))
        cache.get_fundamental_screening_data_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade_range = AsyncMock(return_value=pd.DataFrame())

        # 单日查询正常
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(cache)
        await provider.preload_range(date(2024, 1, 2), date(2024, 1, 3))

        # 发生异常后，_preloaded 内对应项应标记为 None 或异常
        assert provider._preloaded is not None
        assert provider._preloaded["screening_data"] is None

        # 触发 build_context 时应执行 daily get_screening_data
        ctx = await provider.build_context(date(2024, 1, 2))
        cache.get_screening_data.assert_called_once_with("20240102")
        assert len(ctx.get("screening_data")) == 1

    @pytest.mark.asyncio
    async def test_preload_range_cancelled_error_propagation(self) -> None:
        """验证在预加载时，如果是 CancelledError 异常，应该向上抛出（不吞没异常）。"""
        import asyncio

        cache = MagicMock()
        # 模拟其中一个方法抛出 CancelledError
        cache.get_screening_data_range = AsyncMock(side_effect=asyncio.CancelledError())
        cache.get_fundamental_screening_data_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade_range = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(cache)
        with pytest.raises(asyncio.CancelledError):
            await provider.preload_range(date(2024, 1, 2), date(2024, 1, 3))

    @pytest.mark.asyncio
    async def test_preload_range_wide_fallback(self) -> None:
        """验证当时间跨度超过限制（如366天）时，跳过预加载，并优雅降级为每日查询。"""
        cache = MagicMock()
        provider = BacktestDataProvider(cache)

        # 超过 366 天的范围 (2024-01-01 到 2025-02-01)
        await provider.preload_range(date(2024, 1, 1), date(2025, 2, 1))

        # 预加载应该没有初始化 (为 None)
        assert provider._preloaded is None
        # 应无范围方法调用
        cache.get_screening_data_range.assert_not_called()

    @pytest.mark.asyncio
    async def test_preload_range_robust_date_handling(self) -> None:
        """验证对于 null, NaT, None 等无效日期，能够进行过滤且不报错。"""
        cache = MagicMock()
        import numpy as np

        # 返回含有 None/NaT 的非法数据
        cache.get_screening_data_range = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "trade_date": ["20240102", None, np.nan],
                    "close": [10.0, 20.0, 30.0],
                    "is_tradable": [True, True, True],
                }
            )
        )
        cache.get_fundamental_screening_data_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list_range = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade_range = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(cache)
        await provider.preload_range(date(2024, 1, 2), date(2024, 1, 3))

        assert provider._preloaded is not None
        # 应该只保留 20240102 的有效数据，过滤掉其余两条空日期数据
        preloaded_df_dict = provider._preloaded["screening_data"]
        assert isinstance(preloaded_df_dict, dict)
        assert len(preloaded_df_dict) == 1
        assert "20240102" in preloaded_df_dict
        assert len(preloaded_df_dict["20240102"]) == 1


class TestBacktestDataProviderAuxiliaryTables:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "close": [10.0, 20.0],
                    "is_tradable": [True, True],
                    "turnover_rate": [3.5, 5.2],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "is_tradable": [True],
                    "roe": [12.5],
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
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                    "turnover_rate": [3.5],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "is_tradable": [True],
                    "roe": [12.5],
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
        mock_cache.get_fundamental_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ"],
                    "trade_date": ["20240102", "20240102"],
                    "is_tradable": [True, False],
                    "roe": [12.5, 15.3],
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
        mock_cache.get_fundamental_screening_data = AsyncMock(return_value=None)

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "fundamental_screening_data" not in context
        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("table_status", {}).get("fundamental_screening_data", {}).get("ready") is False

    @pytest.mark.asyncio
    async def test_fundamental_data_empty(self, mock_cache: MagicMock) -> None:
        mock_cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert "fundamental_screening_data" not in context


class TestBacktestDataProviderScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
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
    async def test_screening_data_get_failure(self, mock_cache: MagicMock) -> None:
        mock_cache.get_screening_data = AsyncMock(side_effect=Exception("db error"))

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context.get("screening_data") is None
        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("base_complete") is False

    @pytest.mark.asyncio
    async def test_screening_data_returns_none(self, mock_cache: MagicMock) -> None:
        mock_cache.get_screening_data = AsyncMock(return_value=None)

        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context.get("screening_data") is None


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
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                    "turnover_rate": [3.5],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
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

        diagnostics = context.get("_diagnostics", {})
        assert diagnostics.get("suspended_filtered") == 1


class TestBacktestDataProviderBuildContext:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                    "turnover_rate": [3.5],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_build_context_sets_trade_date(self, mock_cache: MagicMock) -> None:
        provider = BacktestDataProvider(mock_cache)

        context = await provider.build_context(date(2024, 1, 2))

        assert context.get("trade_date") == "20240102"


class TestBacktestDataProviderGetScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
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
    async def test_get_screening_data_exception(self, mock_cache: MagicMock) -> None:
        mock_cache.get_screening_data = AsyncMock(side_effect=Exception("connection refused"))

        provider = BacktestDataProvider(mock_cache)

        result = await provider._get_screening_data("20240102")

        assert result is None


class TestBacktestDataProviderGetFundamentalScreeningData:
    @pytest.fixture
    def mock_cache(self) -> MagicMock:
        cache = MagicMock()
        cache.get_screening_data = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ"],
                    "trade_date": ["20240102"],
                    "close": [10.0],
                    "is_tradable": [True],
                    "turnover_rate": [3.5],
                }
            )
        )
        cache.get_fundamental_screening_data = AsyncMock(return_value=pd.DataFrame())
        cache.get_northbound = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow_hsgt = AsyncMock(return_value=pd.DataFrame())
        cache.get_moneyflow = AsyncMock(return_value=pd.DataFrame())
        cache.get_top_list = AsyncMock(return_value=pd.DataFrame())
        cache.get_block_trade = AsyncMock(return_value=pd.DataFrame())
        return cache

    @pytest.mark.asyncio
    async def test_get_fundamental_screening_data_exception(self, mock_cache: MagicMock) -> None:
        mock_cache.get_fundamental_screening_data = AsyncMock(side_effect=Exception("db timeout"))

        provider = BacktestDataProvider(mock_cache)

        result = await provider._get_fundamental_screening_data("20240102")

        assert result is None


class TestGetStockMeta:
    """BT-002: get_stock_meta 加载 delist_date 字段测试"""

    @pytest.mark.asyncio
    async def test_get_stock_meta_returns_delist_date(self) -> None:
        """get_stock_meta 返回 {ts_code: {"delist_date": date | None}} 结构"""
        cache = MagicMock()
        cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
                    "delist_date": [pd.Timestamp("2024-01-15"), None, pd.NaT],
                }
            )
        )
        provider = BacktestDataProvider(cache)

        meta = await provider.get_stock_meta()

        assert "000001.SZ" in meta
        assert meta["000001.SZ"]["delist_date"] == date(2024, 1, 15)
        assert meta["000002.SZ"]["delist_date"] is None
        assert meta["000003.SZ"]["delist_date"] is None

    @pytest.mark.asyncio
    async def test_get_stock_meta_empty_df(self) -> None:
        """stock_basic 为空时返回空 dict"""
        cache = MagicMock()
        cache.get_stock_basic = AsyncMock(return_value=pd.DataFrame())
        provider = BacktestDataProvider(cache)

        meta = await provider.get_stock_meta()
        assert meta == {}

    @pytest.mark.asyncio
    async def test_get_stock_meta_load_failure(self) -> None:
        """加载失败时返回空 dict 并不抛异常"""
        cache = MagicMock()
        cache.get_stock_basic = AsyncMock(side_effect=RuntimeError("db error"))
        provider = BacktestDataProvider(cache)

        meta = await provider.get_stock_meta()
        assert meta == {}

    @pytest.mark.asyncio
    async def test_get_stock_meta_timestamp_converted_to_date(self) -> None:
        """Timestamp 类型的 delist_date 被转换为 datetime.date"""
        cache = MagicMock()
        cache.get_stock_basic = AsyncMock(
            return_value=pd.DataFrame(
                {
                    "ts_code": ["000004.SZ"],
                    "delist_date": [pd.Timestamp("2024-06-30")],
                }
            )
        )
        provider = BacktestDataProvider(cache)

        meta = await provider.get_stock_meta()

        delist_date = meta["000004.SZ"]["delist_date"]
        assert isinstance(delist_date, date)
        assert not hasattr(delist_date, "date")  # 不是 Timestamp
        assert delist_date == date(2024, 6, 30)
