import asyncio
import datetime

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import hashlib

from data.external.tushare.capability_probe import _RUNTIME_UNAVAILABLE_TTL
from data.external.tushare_client import TushareClient, TushareAPIPermissionError
from data.constants import SYNC_RESULT_SKIPPED_PERMISSION
from utils.time_utils import get_now

# 文件级标记 unit。历史曾因 slow_persist 使用 asyncio.sleep(10) 整文件标 slow，
# 但被测任务在测试中立即 cancel，sleep 时长不影响断言；改为 unit 以纳入默认 CI 门禁。
pytestmark = pytest.mark.unit


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
        # 档位驱动模型需要有效档位值，否则 is_api_covered_by_tier 因 tier 不在
        # _TIER_ORDER 中返回 0（points_120），导致 points_2000+ 的 API 全被过滤
        mock_ch.get_tushare_point_tier.return_value = "points_5000"
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
            "top_inst",
            "limit_list",
            "margin_daily",
            "block_trade",
            "stk_limit",
        ]
        for table in expected_tables:
            assert table in client.TABLE_TO_API_MAP, f"Missing mapping for {table}"

    def test_top_inst_maps_to_top_inst_api(self, tushare_client_mocks):
        """Phase 2E：top_inst 表名映射到同名 Tushare API。"""
        client, _, _ = tushare_client_mocks
        assert client.TABLE_TO_API_MAP["top_inst"] == "top_inst"

    def test_stk_limit_maps_to_stk_limit_api(self, tushare_client_mocks):
        """Phase 2G：stk_limit 表名映射到同名 Tushare API。"""
        client, _, _ = tushare_client_mocks
        assert client.TABLE_TO_API_MAP["stk_limit"] == "stk_limit"

    def test_limit_list_maps_to_limit_list_d_api(self, tushare_client_mocks):
        """本地表名 limit_list 应映射到 Tushare API 名 limit_list_d（带 _d 后缀）。
        修复历史 bug：代码曾错误调用 self.pro.limit_list（不存在的接口名）。"""
        client, _, _ = tushare_client_mocks
        assert client.TABLE_TO_API_MAP["limit_list"] == "limit_list_d"

    def test_slow_api_overrides_uses_limit_list_d(self, tushare_client_mocks):
        """_SLOW_API_OVERRIDES 的 key 是 API 名，应使用 limit_list_d 而非 limit_list。"""
        client, _, _ = tushare_client_mocks
        assert "limit_list_d" in client._SLOW_API_OVERRIDES
        assert "limit_list" not in client._SLOW_API_OVERRIDES


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
            patch(
                "data.persistence.app_state_service.set_app_state",
                new_callable=AsyncMock,
            ) as mock_set,
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
            patch(
                "data.persistence.app_state_service.get_app_state",
                new_callable=AsyncMock,
            ) as mock_get,
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
            patch(
                "data.persistence.app_state_service.get_app_state",
                new_callable=AsyncMock,
            ) as mock_get,
        ):
            mock_cm.return_value.engine = mock_engine
            mock_get.return_value = stored_payload
            await client.load_capabilities_from_app_state()

            assert client.is_api_available("api1") is None


class TestProbeApiCapabilities:
    @pytest.mark.asyncio
    async def test_returns_dict_with_results(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        # probe_api_capabilities 内部调用 _handle_probe_call（非 _handle_api_call），
        # mock _handle_api_call 是无效的：真实 _handle_probe_call 会经过 TokenBucket
        # 限速器（probe 桶 50/min, capacity 5），29 个探测被节流 ~26s。
        async def mock_handle(api_name, func, **kwargs):
            return MagicMock()

        with (
            patch.object(client, "_handle_probe_call", new_callable=AsyncMock, side_effect=mock_handle),
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
            "limit_list_d",
            "margin_detail",
            "block_trade",
            "fina_indicator",
            "fina_mainbz",
            "stk_holdernumber",
            "top10_holders",
        ]

        async def mock_handle(api_name, func, **kwargs):
            return MagicMock()

        with (
            patch.object(client, "_handle_probe_call", new_callable=AsyncMock, side_effect=mock_handle),
            patch.object(client, "persist_capabilities_to_app_state", new_callable=AsyncMock),
        ):
            results = await client.probe_api_capabilities()

            for api in expected_apis:
                assert api in results, f"Expected API '{api}' not in probe results"

    @pytest.mark.asyncio
    async def test_probe_uses_correct_parameters(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks

        call_log = []

        async def mock_probe_call(api_name, func, **params):
            # probe_api_capabilities 内部调用 _handle_probe_call（非 _handle_api_call），
            # 捕获 params 以验证 ts_code/period/enddate 参数正确性
            call_log.append(params)

        # 固定时间避免年末边界导致 period 断言不稳定：
        # get_now().year - 1 = 2024 → PROBE_RECENT_PERIOD = "20241231"
        fixed_now = datetime.datetime(2025, 1, 1)
        with (
            patch("utils.time_utils.get_now", return_value=fixed_now),
            patch.object(client, "_handle_probe_call", new_callable=AsyncMock, side_effect=mock_probe_call),
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
            patch(
                "data.persistence.app_state_service.set_app_state",
                new_callable=AsyncMock,
            ) as mock_set,
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

        # Mock _persist_capability_safely to be a long-running coroutine so we can observe the task
        # 任务在测试末尾被 cancel，sleep 时长不影响断言；用 0.1 即可观察强引用持有
        async def slow_persist():
            await asyncio.sleep(0.1)

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

    @pytest.mark.asyncio
    async def test_token_invalid_keyword_triggers_breaker(self, tushare_client_mocks):
        """TOKEN_INVALID_KEYWORDS 命中时触发全局熔断。"""
        client, _, _ = tushare_client_mocks
        client._bg_tasks = set()

        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
            patch.object(client, "_persist_capability_safely", new_callable=AsyncMock),
        ):
            mock_func = MagicMock()
            mock_func.__name__ = "moneyflow_hsgt"
            mock_func.side_effect = Exception("您的token不对，请确认。")

            with pytest.raises(TushareAPIPermissionError):
                await client._handle_api_call(mock_func, trade_date="20240101")

            assert client._token_invalid is True

        for t in list(client._bg_tasks):
            t.cancel()
        client._bg_tasks.clear()

    @pytest.mark.asyncio
    async def test_breaker_fast_fails_subsequent_calls(self, tushare_client_mocks):
        """熔断开启时 _handle_api_call 快速失败，不触达 func / self.pro。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True
        client._bg_tasks = set()

        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
        ):
            mock_func = MagicMock()
            mock_func.__name__ = "moneyflow_hsgt"

            with pytest.raises(TushareAPIPermissionError):
                await client._handle_api_call(mock_func, trade_date="20240101")

            # 熔断快速失败路径在调用 func 之前抛出
            mock_func.assert_not_called()

    @pytest.mark.asyncio
    async def test_breaker_recovers_after_set_token(self, tushare_client_mocks):
        """T4.5: 熔断 → set_token 重置 → 再次调用不再快速失败（端到端恢复）。"""
        client, _, _ = tushare_client_mocks
        client._bg_tasks = set()

        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
            patch.object(client, "_persist_capability_safely", new_callable=AsyncMock),
        ):
            # 第一次调用：func 抛 token 无效错误，触发熔断（_token_invalid=True）
            mock_func_bad = MagicMock()
            mock_func_bad.__name__ = "moneyflow_hsgt"
            mock_func_bad.side_effect = Exception("您的token不对，请确认。")

            with pytest.raises(TushareAPIPermissionError):
                await client._handle_api_call(mock_func_bad, trade_date="20240101")
            assert client._token_invalid is True

        # set_token_async 重置熔断
        await client.set_token_async("new_token_after_invalid")
        assert client._token_invalid is False

        # 第二次调用：验证不再快速失败，成功返回结果
        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
        ):
            mock_func_good = MagicMock()
            mock_func_good.__name__ = "moneyflow_hsgt"
            loop = asyncio.get_running_loop()
            with patch.object(
                loop,
                "run_in_executor",
                new=AsyncMock(return_value=MagicMock()),
            ):
                result = await client._handle_api_call(mock_func_good, trade_date="20240101")
            assert result is not None

        # 清理 bg_tasks
        for t in list(client._bg_tasks):
            t.cancel()
        client._bg_tasks.clear()

    @pytest.mark.asyncio
    async def test_set_token_new_token_resets_breaker(self, tushare_client_mocks):
        """set_token_async 传入新 token 时重置熔断标志。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True

        await client.set_token_async("new_token_xxx")

        assert client._token_invalid is False

    @pytest.mark.asyncio
    async def test_set_token_same_token_resets_breaker(self, tushare_client_mocks):
        """set_token_async 传入相同 token 时也重置熔断标志。"""
        client, _, _ = tushare_client_mocks
        client._token_invalid = True

        await client.set_token_async(client.token)

        assert client._token_invalid is False

    @pytest.mark.asyncio
    async def test_permission_denied_not_triggers_breaker(self, tushare_client_mocks):
        """per-API 权限错误（积分不足 / 接口名错误）不触发全局熔断。"""
        client, _, _ = tushare_client_mocks
        client._bg_tasks = set()

        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
            patch.object(client, "_persist_capability_safely", new_callable=AsyncMock),
        ):
            # 积分不足：per-API 权限错误，不熔断
            mock_func = MagicMock()
            mock_func.__name__ = "moneyflow_hsgt"
            mock_func.side_effect = Exception("权限不足，积分不够")

            with pytest.raises(TushareAPIPermissionError):
                await client._handle_api_call(mock_func, trade_date="20240101")

            assert client._token_invalid is False

            # 接口名错误（伪装报错）：同样不触发全局熔断
            mock_func2 = MagicMock()
            mock_func2.__name__ = "daily"
            mock_func2.side_effect = Exception("请指定正确的接口名")

            with pytest.raises(TushareAPIPermissionError):
                await client._handle_api_call(mock_func2, trade_date="20240101")

            assert client._token_invalid is False

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


class TestProbeTokenConsistency:
    """B3+B5/B19 修复：probe 竞态与 _capability_cache 持锁。

    验证：
    - probe 入口快照 token，异常回滚时若 token 变化则不回滚（路径 A 污染修复）
    - probe 完成写入前检查 token 一致性，不一致则丢弃结果（路径 B 污染修复）
    - 所有 _capability_cache 访问持锁（间接验证：回滚用 clear+update 而非引用替换）
    """

    def _make_client(self, tier="points_120"):
        """创建 client 并 mock 档位（points_120 仅 probe daily + shibor_lpr，简化测试）。"""
        with (
            patch("data.external.tushare_client.ts") as mock_ts,
            patch("data.external.tushare_client.ConfigHandler") as mock_ch,
        ):
            mock_ts.pro_api.return_value = MagicMock()
            mock_ch.get_token.return_value = "test_token"
            mock_ch.get_tushare_timeout.return_value = 30
            mock_ch.get_request_max_retries.return_value = 3
            mock_ch.get_tushare_point_tier.return_value = tier
            client = TushareClient(token="test_token")
        client._get_tushare_point_tier = lambda: tier
        return client

    @pytest.mark.asyncio
    async def test_probe_discards_results_when_token_changed(self):
        """B3 路径 B：probe 完成写入前 token 变化则丢弃结果（不写入新 probe 结果）。"""
        client = self._make_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        client.mark_api_unavailable("daily")  # 预设 daily=False

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                # 模拟 probe 期间 set_token_async 改变 token + 清空 cache
                await client.set_token_async("new_token_during_probe")
            return None  # probe 成功 → daily=True

        client._handle_probe_call = fake_probe_call
        await client.probe_api_capabilities()

        # token 变化后，probe 结果被丢弃，daily 不被写入（set_token 已清空 cache）
        assert client.is_api_available("daily") is None
        assert client.token == "new_token_during_probe"

    @pytest.mark.asyncio
    async def test_probe_skip_rollback_when_token_changed_on_exception(self):
        """B3 路径 A：异常回滚时若 token 变化则不回滚（避免污染新 token 的 cache）。"""
        client = self._make_client(tier="points_120")
        client.mark_api_unavailable("daily")  # 预设 daily=False

        async def persist_with_token_change():
            # 模拟 persist 期间 set_token_async 改变 token + 清空 cache
            await client.set_token_async("new_token_after_exception")
            raise RuntimeError("persist failed")

        client.persist_capabilities_to_app_state = persist_with_token_change

        async def fake_probe_call(api_name, func, **params):
            return None  # probe 成功 → daily=True

        client._handle_probe_call = fake_probe_call
        await client.probe_api_capabilities()

        # token 变化后，回滚被跳过，daily=False 不被恢复（set_token 已清空 cache）
        assert client.is_api_available("daily") is None
        assert client.token == "new_token_after_exception"

    @pytest.mark.asyncio
    async def test_probe_rollback_when_token_unchanged_on_exception(self):
        """B3：异常回滚时若 token 不变则回滚到入口快照（不保留 probe 写入的新值）。"""
        client = self._make_client(tier="points_120")
        client.mark_api_unavailable("daily")  # 预设 daily=False

        client.persist_capabilities_to_app_state = AsyncMock(side_effect=RuntimeError("persist failed"))

        async def fake_probe_call(api_name, func, **params):
            return None  # probe 成功 → daily=True

        client._handle_probe_call = fake_probe_call
        await client.probe_api_capabilities()

        # token 不变，回滚到入口快照，daily=False 保留（不保留 probe 写入的 True）
        assert client.is_api_available("daily") is False
        assert client.token == "test_token"

    @pytest.mark.asyncio
    async def test_probe_skip_rollback_when_token_changed_on_cancel(self):
        """B3 路径 A：CancelledError 回滚时若 token 变化则不回滚。"""
        client = self._make_client(tier="points_120")
        client.persist_capabilities_to_app_state = AsyncMock()
        client.mark_api_unavailable("daily")  # 预设 daily=False

        async def fake_probe_call(api_name, func, **params):
            if api_name == "daily":
                # 模拟 probe 期间 set_token_async 改变 token + 清空 cache
                await client.set_token_async("new_token_after_cancel")
                raise asyncio.CancelledError()
            return None

        client._handle_probe_call = fake_probe_call
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await client.probe_api_capabilities()
        assert isinstance(exc_info.value, asyncio.CancelledError)

        # token 变化后，回滚被跳过，daily=False 不被恢复（set_token 已清空 cache）
        assert client.is_api_available("daily") is None
        assert client.token == "new_token_after_cancel"
        # 互斥标志应释放（finally 块）
        assert client._probe_in_progress is False


class TestResetSingletonBgTasks:
    """B4 修复：_reset_singleton 后旧协程的 _persist_capability_safely task 不被添加到旧实例 _bg_tasks。"""

    @pytest.mark.asyncio
    async def test_old_coroutine_skips_persist_after_reset(self, tushare_client_mocks):
        """_reset_singleton 后，飞行中的 _handle_api_call 协程命中权限错误路径时，
        不应将 _persist_capability_safely task 添加到旧实例的 _bg_tasks。"""
        client, _, _ = tushare_client_mocks
        client._bg_tasks = set()

        # 保存原 _instance，模拟 _reset_singleton 后 _instance=None
        original_instance = TushareClient._instance
        TushareClient._instance = None
        try:
            mock_func = MagicMock()
            mock_func.__name__ = "daily"
            mock_func.side_effect = Exception("权限不足，积分不够")

            with patch.object(client, "_persist_capability_safely", new_callable=AsyncMock) as mock_persist:
                with pytest.raises(TushareAPIPermissionError):
                    await client._handle_api_call(mock_func, ts_code="000001.SZ")

            # B4 修复：self is not TushareClient._instance（None），不创建 task
            assert len(client._bg_tasks) == 0
            mock_persist.assert_not_called()
            # mark_api_unavailable 仍被调用（cache 更新不受 B4 影响）
            assert client.is_api_available("daily") is False
        finally:
            TushareClient._instance = original_instance


class TestRuntimeUnavailableCacheTTL:
    """review09-24/05 MINOR-02：区分运行时负缓存与 probe 权威负缓存。

    运行时负缓存（``_handle_api_call`` 单次权限类报错得出）可能因关键字误判产生，
    仅保留 ``_RUNTIME_UNAVAILABLE_TTL`` 后惰性过期，且不写入持久化 payload；
    probe 得出的权威负缓存不受 TTL 影响、正常持久化。
    """

    def _set_runtime_mark_age(self, client, api_name: str, age: datetime.timedelta) -> None:
        """直接设定运行时标记的年龄，避免依赖真实等待。"""
        client._capability_runtime_marks[api_name] = get_now() - age

    def test_runtime_mark_expires_to_none_after_ttl(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list", runtime=True)
        assert client.is_api_available("top_list") is False

        self._set_runtime_mark_age(client, "top_list", _RUNTIME_UNAVAILABLE_TTL + datetime.timedelta(seconds=1))

        # 超 TTL → 惰性恢复为 None，且内部两字典均清掉该条目
        assert client.is_api_available("top_list") is None
        assert "top_list" not in client._capability_cache
        assert "top_list" not in client._capability_runtime_marks

    def test_runtime_mark_stays_false_before_ttl(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list", runtime=True)
        # TTL 边界内（差 1 分钟到期）→ 仍为 False
        self._set_runtime_mark_age(client, "top_list", _RUNTIME_UNAVAILABLE_TTL - datetime.timedelta(minutes=1))

        assert client.is_api_available("top_list") is False
        assert "top_list" not in client.get_effective_synced_tables(["top_list"])

    def test_expired_runtime_mark_reenables_effective_table(self, tushare_client_mocks):
        """MINOR-02 核心：运行时负缓存过期后，对应表重新纳入有效同步表。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list", runtime=True)
        assert "top_list" not in client.get_effective_synced_tables(["top_list"])

        self._set_runtime_mark_age(client, "top_list", _RUNTIME_UNAVAILABLE_TTL + datetime.timedelta(seconds=1))

        assert "top_list" in client.get_effective_synced_tables(["top_list"])

    def test_durable_mark_clears_runtime_timestamp(self, tushare_client_mocks):
        """权威负缓存（runtime=False）清除残留时间戳 → 不再被 TTL 误判过期。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list", runtime=True)
        assert "top_list" in client._capability_runtime_marks

        client.mark_api_unavailable("top_list")  # 升格为权威负缓存

        assert "top_list" not in client._capability_runtime_marks
        assert client.is_api_available("top_list") is False

    def test_mark_api_available_clears_runtime_timestamp(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list", runtime=True)
        assert "top_list" in client._capability_runtime_marks

        client.mark_api_available("top_list")

        assert client.is_api_available("top_list") is True
        assert "top_list" not in client._capability_runtime_marks

    def test_runtime_mark_does_not_downgrade_durable(self, tushare_client_mocks):
        """已由 probe 判定的权威负缓存不被运行时信号降级（避免 TTL 到期丢失权威知识）。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("top_list")  # 权威负缓存（无时间戳）

        client.mark_api_unavailable("top_list", runtime=True)

        assert "top_list" not in client._capability_runtime_marks
        assert client.get_capability_cache()["top_list"] is False
        assert "top_list" not in client.get_effective_synced_tables(["top_list"])

    def test_runtime_mark_refreshes_existing_runtime_entry(self, tushare_client_mocks):
        """同一 API 再次运行时报错 → 时间戳刷新，仍为运行时负缓存。"""
        client, _, _ = tushare_client_mocks
        first_ts = get_now() - datetime.timedelta(minutes=30)
        second_ts = get_now()

        with patch("data.external.tushare.capability_probe.get_now", side_effect=[first_ts, second_ts]):
            client.mark_api_unavailable("top_list", runtime=True)
            client.mark_api_unavailable("top_list", runtime=True)

        assert client._capability_runtime_marks["top_list"] == second_ts
        assert client.is_api_available("top_list") is False

    def test_delegation_passes_runtime_flag(self, tushare_client_mocks):
        """TushareClient.mark_api_unavailable 的 runtime 关键字透传到服务子模块。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("api_runtime", runtime=True)
        client.mark_api_unavailable("api_durable")

        assert "api_runtime" in client._capability_runtime_marks
        assert "api_durable" not in client._capability_runtime_marks

    def test_clear_capability_cache_clears_both_dicts(self, tushare_client_mocks):
        client, _, _ = tushare_client_mocks
        client.mark_api_unavailable("api_runtime", runtime=True)
        client.mark_api_available("api_ok")

        client.clear_capability_cache()

        assert client._capability_cache == {}
        assert client._capability_runtime_marks == {}

    def test_get_capability_cache_excludes_runtime_marks(self, tushare_client_mocks):
        """运行时负缓存不对外暴露（否则上层快照回写会「升格」为持久负缓存）。"""
        client, _, _ = tushare_client_mocks
        client.mark_api_available("api_ok")
        client.mark_api_unavailable("api_durable")
        client.mark_api_unavailable("api_runtime", runtime=True)

        cache = client.get_capability_cache()

        assert cache["api_ok"] is True
        assert cache["api_durable"] is False
        assert "api_runtime" not in cache
        # 内部仍保留运行时条目（仅不对外暴露）
        assert client._capability_cache["api_runtime"] is False

    @pytest.mark.asyncio
    async def test_persist_payload_excludes_runtime_marks(self, tushare_client_mocks):
        """运行时负缓存不落库，避免跨重启存活的错误负缓存。"""
        import json

        client, _, _ = tushare_client_mocks
        client.mark_api_available("api_ok")
        client.mark_api_unavailable("api_durable")
        client.mark_api_unavailable("api_runtime", runtime=True)

        captured: dict[str, str] = {}

        async def fake_set_app_state(engine, key, value):
            captured["key"] = key
            captured["value"] = value

        with (
            patch("data.cache.cache_manager.CacheManager") as mock_cm,
            patch("data.persistence.app_state_service.set_app_state", new=fake_set_app_state),
        ):
            mock_cm.return_value.engine = MagicMock()
            await client.persist_capabilities_to_app_state()

        assert captured["key"] == "tushare_capabilities"
        capabilities = json.loads(captured["value"])["capabilities"]
        assert capabilities["api_ok"] is True
        assert capabilities["api_durable"] is False
        assert "api_runtime" not in capabilities

    @pytest.mark.asyncio
    async def test_handle_api_call_permission_error_records_runtime_mark(self, tushare_client_mocks):
        """权限报错经运行时分支落地时间戳（可 TTL 过期、不持久化）。"""
        client, _, _ = tushare_client_mocks
        with (
            patch.object(client, "pro", MagicMock()),
            patch.object(client, "_rate_limiter", None),
            patch.object(client, "_api_limiters", {}),
            patch.object(client, "_persist_capability_safely", new_callable=AsyncMock),
        ):
            mock_func = MagicMock()
            mock_func.__name__ = "top_list"
            mock_func.side_effect = Exception("权限不足，积分不够")

            with pytest.raises(TushareAPIPermissionError, match="权限不足") as exc_info:
                await client._handle_api_call(mock_func, trade_date="20240101")

            assert exc_info.value.api_name == "top_list"

        assert client.is_api_available("top_list") is False
        assert "top_list" in client._capability_runtime_marks

    @pytest.mark.asyncio
    async def test_probe_rollback_restores_runtime_marks(self, tushare_client_mocks):
        """probe 异常回滚同时还原运行时标记字典，避免残留时间戳让权威负缓存被误判过期。"""
        client, _, _ = tushare_client_mocks
        # 入口：top_list 为权威负缓存（无时间戳）
        client.mark_api_unavailable("top_list")
        assert "top_list" not in client._capability_runtime_marks

        async def fake_probe_call(api_name, func, **params):
            if api_name == "top_list":
                # 模拟 probe 期间并发真实调用给 top_list 打上运行时标记
                client.mark_api_unavailable("top_list", runtime=True)
                # 该 API 探测结果为 None（网络错误）→ probe 主流程对该 API 不写入，
                # 运行时标记得以保留到回滚路径，从而验证回滚是否还原时间戳字典
                raise ConnectionError("network error")
            return None

        client._handle_probe_call = fake_probe_call
        client.persist_capabilities_to_app_state = AsyncMock(side_effect=RuntimeError("persist failed"))

        await client.probe_api_capabilities()

        # 回滚后：权威负缓存保留且无运行时时间戳（若仅回滚 cache 会残留时间戳，
        # 后续 TTL 到期会把权威负缓存误判为可过期）
        assert client.is_api_available("top_list") is False
        assert "top_list" not in client._capability_runtime_marks
