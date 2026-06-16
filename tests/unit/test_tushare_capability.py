import asyncio

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import hashlib

from data.external.tushare_client import TushareClient, TushareAPIPermissionError
from data.constants import SYNC_RESULT_SKIPPED_PERMISSION


@pytest.fixture
def tushare_client_mocks():
    with (
        patch("data.external.tushare_client.ts") as mock_ts,
        patch("data.external.tushare_client.ConfigHandler") as mock_ch,
    ):
        mock_ts.pro_api.return_value = MagicMock()
        mock_ch.get_token.return_value = "test_token"
        mock_ch.get_tushare_timeout.return_value = 30
        mock_ch.get_request_max_retries.return_value = 3
        mock_ch.get_tushare_api_limit.return_value = 120
        client = TushareClient(token="test_token")
        yield client, mock_ts, mock_ch


class TestTableToApiMap:
    def test_table_to_api_map_defined(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        assert hasattr(client, "TABLE_TO_API_MAP")
        assert isinstance(client.TABLE_TO_API_MAP, dict)
        assert len(client.TABLE_TO_API_MAP) > 0

    def test_table_to_api_map_contains_expected_tables(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        expected_tables = [
            "moneyflow_hsgt",
            "northbound_holding",
            "moneyflow_daily",
            "top_list",
            "limit_list",
            "margin_daily",
            "block_trade",
        ]
        for table in expected_tables:
            assert table in client.TABLE_TO_API_MAP, f"Missing mapping for {table}"


class TestGetEffectiveSyncedTables:
    def test_all_tables_included_when_all_apis_available(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        all_tables = ["daily_quotes", "daily_indicators", "moneyflow_hsgt", "top_list"]

        for api in client.TABLE_TO_API_MAP.values():
            client.mark_api_available(api)

        effective = client.get_effective_synced_tables(all_tables)
        assert effective == all_tables

    def test_tables_excluded_when_api_unavailable(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        all_tables = ["daily_quotes", "moneyflow_hsgt", "top_list"]

        client.mark_api_available("moneyflow_hsgt")
        client.mark_api_unavailable("top_list")

        effective = client.get_effective_synced_tables(all_tables)
        assert "daily_quotes" in effective
        assert "moneyflow_hsgt" in effective
        assert "top_list" not in effective

    def test_tables_included_when_api_status_unknown(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        all_tables = ["daily_quotes", "moneyflow_hsgt"]

        effective = client.get_effective_synced_tables(all_tables)
        assert "daily_quotes" in effective
        assert "moneyflow_hsgt" in effective

    def test_base_tables_always_included(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        all_tables = ["daily_quotes", "daily_indicators", "stock_basic"]

        client.mark_api_unavailable("some_api")

        effective = client.get_effective_synced_tables(all_tables)
        assert "daily_quotes" in effective
        assert "daily_indicators" in effective
        assert "stock_basic" in effective


class TestCapabilityCache:
    def test_is_api_available_returns_none_when_unknown(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        assert client.is_api_available("unknown_api") is None

    def test_mark_api_available_updates_cache(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_available("test_api")
        assert client.is_api_available("test_api") is True

    def test_mark_api_unavailable_updates_cache(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("test_api")
        assert client.is_api_available("test_api") is False

    def test_get_capability_cache_returns_copy(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_available("api1")
        client.mark_api_unavailable("api2")

        cache = client.get_capability_cache()
        assert cache["api1"] is True
        assert cache["api2"] is False


class TestSyncResultSkippedPermission:
    def test_constant_defined(self):
        assert SYNC_RESULT_SKIPPED_PERMISSION == "SKIPPED_PERMISSION"

    def test_constant_is_string(self):
        assert isinstance(SYNC_RESULT_SKIPPED_PERMISSION, str)


class TestPersistCapabilitiesToAppState:
    @pytest.mark.asyncio
    async def test_skips_when_engine_not_ready(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_available("test_api")

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm.return_value.engine = None
            await client.persist_capabilities_to_app_state()

    @pytest.mark.asyncio
    async def test_persists_when_engine_ready(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_available("api1")
        client.mark_api_unavailable("api2")

        mock_engine = MagicMock()
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.set_app_state", new_callable=AsyncMock) as mock_set,
        ):
            mock_cm.return_value.engine = mock_engine
            await client.persist_capabilities_to_app_state()

            assert mock_set.called
            call_args = mock_set.call_args
            assert call_args[0][0] == mock_engine
            assert call_args[0][1] == "tushare_capabilities"


class TestLoadCapabilitiesFromAppState:
    @pytest.mark.asyncio
    async def test_skips_when_engine_not_ready(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        with patch("data.cache.cache_manager.CacheManager") as mock_cm:
            mock_cm.return_value.engine = None
            await client.load_capabilities_from_app_state()

    @pytest.mark.asyncio
    async def test_loads_when_token_hash_matches(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        token_hash = hashlib.sha256(b"test_token").hexdigest()[:16]
        stored_payload = f'{{"token_hash": "{token_hash}", "capabilities": {{"api1": true, "api2": false}}}}'

        mock_engine = MagicMock()
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.get_app_state", new_callable=AsyncMock) as mock_get,
        ):
            mock_cm.return_value.engine = mock_engine
            mock_get.return_value = stored_payload
            await client.load_capabilities_from_app_state()

            assert client.is_api_available("api1") is True
            assert client.is_api_available("api2") is False

    @pytest.mark.asyncio
    async def test_skips_when_token_hash_mismatch(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        stored_payload = '{"token_hash": "different_hash", "capabilities": {"api1": true}}'

        mock_engine = MagicMock()
        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.get_app_state", new_callable=AsyncMock) as mock_get,
        ):
            mock_cm.return_value.engine = mock_engine
            mock_get.return_value = stored_payload
            await client.load_capabilities_from_app_state()

            assert client.is_api_available("api1") is None


class TestProbeApiCapabilities:
    @pytest.mark.asyncio
    async def test_returns_dict_with_results(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        async def mock_handle(func, **kwargs):
            return MagicMock()

        with (
            patch.object(client, "_handle_api_call", side_effect=mock_handle),
            patch.object(client, "persist_capabilities_to_app_state", new_callable=AsyncMock),
        ):
            results = await client.probe_api_capabilities()

            assert isinstance(results, dict)
            assert len(results) > 0

    @pytest.mark.asyncio
    async def test_probes_extended_api_list(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        expected_apis = [
            "daily",
            "moneyflow_hsgt",
            "moneyflow",
            "hk_hold",
            "top_list",
            "limit_list",
            "margin_detail",
            "block_trade",
            "fina_indicator",
            "fina_mainbz",
            "stk_holdernumber",
            "top10_holders",
        ]

        async def mock_handle(func, **kwargs):
            return MagicMock()

        with (
            patch.object(client, "_handle_api_call", side_effect=mock_handle),
            patch.object(client, "persist_capabilities_to_app_state", new_callable=AsyncMock),
        ):
            results = await client.probe_api_capabilities()

            for api in expected_apis:
                assert api in results, f"Expected API '{api}' not in probe results"

    @pytest.mark.asyncio
    async def test_probe_uses_correct_parameters(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        call_log = []

        async def mock_handle(func, **kwargs):
            call_log.append(kwargs)
            return MagicMock()

        with (
            patch.object(client, "_handle_api_call", side_effect=mock_handle),
            patch.object(client, "persist_capabilities_to_app_state", new_callable=AsyncMock),
        ):
            await client.probe_api_capabilities()

            stock_based_calls = [c for c in call_log if "ts_code" in c]
            assert len(stock_based_calls) >= 4

            for call in stock_based_calls:
                assert call.get("ts_code") == "000001.SZ"
                if "period" in call:
                    assert call["period"] == "20241231"
                if "enddate" in call:
                    assert call["enddate"] == "20241231"


class TestCheckDependenciesWithRequiredApis:
    def test_check_dependencies_includes_missing_apis(self, tushare_client_mocks):
        from strategies.base_strategy import BaseStrategy
        from strategies.utils import StrategyContext

        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("test_api")

        class TestStrategy(BaseStrategy):
            required_apis = ("test_api",)
            required_context_keys = ()
            required_tables = ()

            def __init__(self):
                super().__init__(name_key="test", desc_key="test_desc")

            def run(self, context):
                return []

            async def filter(self, context):
                return []

        strategy = TestStrategy()
        context = StrategyContext()

        result = strategy.check_dependencies(context)
        assert "missing_apis" in result
        assert "test_api" in result["missing_apis"]
        assert result["ready"] is False

    def test_check_dependencies_ready_when_api_available(self, tushare_client_mocks):
        from strategies.base_strategy import BaseStrategy
        from strategies.utils import StrategyContext

        client, _, _ = tushare_client_mocks
        client.mark_api_available("test_api")

        class TestStrategy(BaseStrategy):
            required_apis = ("test_api",)
            required_context_keys = ()
            required_tables = ()

            def __init__(self):
                super().__init__(name_key="test", desc_key="test_desc")

            def run(self, context):
                return []

            async def filter(self, context):
                return []

        strategy = TestStrategy()
        context = StrategyContext()

        result = strategy.check_dependencies(context)
        assert result["missing_apis"] == []


class TestRuntimePermissionPersistence:
    @pytest.mark.asyncio
    async def test_handle_api_call_persists_on_permission_error(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
            patch.object(client, "_persist_capability_safely", new_callable=AsyncMock) as mock_persist_safe,
        ):
            mock_func = MagicMock()
            mock_func.__name__ = "moneyflow_hsgt"
            mock_func.side_effect = Exception("权限不足，积分不够")

            with pytest.raises(Exception, match="权限不足"):
                await client._handle_api_call(mock_func, trade_date="20240101")

            assert mock_persist_safe.called

    @pytest.mark.asyncio
    async def test_persist_capability_safely_swallows_errors(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_available("test_api")

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.set_app_state", new_callable=AsyncMock) as mock_set,
        ):
            mock_cm.return_value.engine = MagicMock()
            mock_set.side_effect = RuntimeError("DB down")

            await client._persist_capability_safely()
            assert mock_set.called

    @pytest.mark.asyncio
    async def test_permission_error_holds_strong_reference_to_persist_task(self, tushare_client_mocks):
        """ASYNC-002: create_task for _persist_capability_safely must be held in _bg_tasks."""
        client, mock_ts, mock_ch = tushare_client_mocks
        client._bg_tasks = set()  # Ensure initialized

        # Mock _persist_capability_safely to be a slow coroutine so we can observe the task
        async def slow_persist():
            await asyncio.sleep(10)

        mock_func = MagicMock()
        mock_func.__name__ = "daily"
        mock_func.side_effect = Exception("权限不足，积分不够")

        with patch.object(client, "_persist_capability_safely", side_effect=slow_persist):
            with patch.object(client, "mark_api_unavailable"):
                with pytest.raises(TushareAPIPermissionError):
                    await client._handle_api_call(mock_func, ts_code="000001.SZ")

        # The task should be in _bg_tasks (strong reference held)
        assert len(client._bg_tasks) == 1
        # Clean up
        for t in list(client._bg_tasks):
            t.cancel()
        client._bg_tasks.clear()


class TestBlockTradeStrategyRequiredApis:
    def test_block_trade_strategy_has_required_apis(self, tushare_client_mocks):
        from strategies.market import BlockTradeStrategy

        assert hasattr(BlockTradeStrategy, "required_apis")
        assert "block_trade" in BlockTradeStrategy.required_apis

    def test_block_trade_strategy_check_dependencies_respects_api_status(self, tushare_client_mocks):
        from strategies.market import BlockTradeStrategy
        from strategies.utils import StrategyContext

        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("block_trade")

        strategy = BlockTradeStrategy()
        context = StrategyContext()

        result = strategy.check_dependencies(context)
        assert "block_trade" in result["missing_apis"]
        assert result["ready"] is False

    def test_block_trade_strategy_ready_when_api_available(self, tushare_client_mocks):
        from strategies.market import BlockTradeStrategy
        from strategies.utils import StrategyContext

        client, _, _ = tushare_client_mocks
        client.mark_api_available("block_trade")

        strategy = BlockTradeStrategy()
        context: StrategyContext = {"block_trade": MagicMock(empty=False)}

        result = strategy.check_dependencies(context)
        assert result["missing_apis"] == []
