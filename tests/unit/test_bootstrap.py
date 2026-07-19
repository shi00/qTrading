import asyncio
import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bootstrap import check_onboarding_needed, initialize_services, mask_sensitive
from data.persistence.db_migrator import DatabaseMigrationNeeded

pytestmark = pytest.mark.unit


class TestMaskSensitive:
    """P3-4: mask_sensitive 复用 DataSanitizer.sanitize_token。

    行为变更：短 token（< 32）全部隐藏为 ***，长 token（≥ 32）部分脱敏（前 3 + *** + 后 4）。
    """

    def test_short_value_masked(self):
        assert mask_sensitive("abcdefghijklmnop") == "***"

    def test_very_short_value_masked(self):
        assert mask_sensitive("abc") == "***"

    def test_none_value_masked(self):
        assert mask_sensitive(None) == "***"

    def test_empty_value_masked(self):
        assert mask_sensitive("") == "***"

    def test_exact_boundary_31_masked(self):
        assert mask_sensitive("a" * 31) == "***"

    def test_long_token_32_partial_mask(self):
        token = "a" * 32
        assert mask_sensitive(token) == "aaa***aaaa"

    def test_long_token_64_partial_mask(self):
        token = "tushare_abc123xyz78901234567890123456789"
        result = mask_sensitive(token)
        assert result.startswith("tus")
        assert result.endswith("6789")
        assert "***" in result


class TestCheckOnboardingNeeded:
    def test_all_present_not_needed(self):
        assert check_onboarding_needed("db_url", "token", "api_key", True) is False

    def test_missing_db_url_needed(self):
        assert check_onboarding_needed("", "token", "api_key", True) is True

    def test_missing_token_needed(self):
        assert check_onboarding_needed("db_url", "", "api_key", True) is True

    def test_missing_api_key_needed(self):
        assert check_onboarding_needed("db_url", "token", "", True) is True

    def test_not_onboarding_complete_needed(self):
        assert check_onboarding_needed("db_url", "token", "api_key", False) is True

    def test_none_db_url_needed(self):
        assert check_onboarding_needed(None, "token", "api_key", True) is True

    def test_none_token_needed(self):
        assert check_onboarding_needed("db_url", None, "api_key", True) is True

    def test_none_api_key_needed(self):
        assert check_onboarding_needed("db_url", "token", None, True) is True

    def test_all_missing_needed(self):
        assert check_onboarding_needed(None, None, None, False) is True


class TestInitializeServices:
    @pytest.mark.asyncio
    async def test_success(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock()
        mock_cm.engine = MagicMock()

        with (
            patch("app.bootstrap.MetaDataManager") as mock_md,
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService") as mock_ss,
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService") as mock_mds,
        ):
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            mock_tm_instance.init_db = AsyncMock()

            mock_ss_instance = MagicMock()
            mock_ss.return_value = mock_ss_instance

            mock_ns_instance = MagicMock()
            mock_ns_instance.start = AsyncMock()
            mock_ns.return_value = mock_ns_instance

            mock_mds_instance = MagicMock()
            mock_mds_instance.start = AsyncMock()
            mock_mds.return_value = mock_mds_instance

            result = await initialize_services(mock_cm)

        assert result["success"] is True
        mock_cm.init_db.assert_awaited_once()
        mock_md.preload_aliases.assert_called_once()
        mock_tm_instance.init_db.assert_awaited_once()
        mock_ss_instance.start.assert_called_once()
        mock_ns_instance.start.assert_awaited_once()
        mock_mds_instance.start.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_db_init_failed(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock(side_effect=Exception("connection refused"))

        result = await initialize_services(mock_cm)

        assert result["success"] is False
        assert result["error"] == "db_init_failed"
        detail = result.get("detail", "")
        assert isinstance(detail, str)
        assert "connection refused" in detail

    @pytest.mark.asyncio
    async def test_db_init_failed_with_toast(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock(side_effect=Exception("connection refused"))
        mock_toast = MagicMock()

        result = await initialize_services(mock_cm, show_toast_fn=mock_toast)

        assert result["success"] is False
        mock_toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_engine_none(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock()
        mock_cm.engine = None

        with patch("app.bootstrap.MetaDataManager"):
            result = await initialize_services(mock_cm)

        assert result["success"] is False
        assert result["error"] == "db_engine_missing"

    @pytest.mark.asyncio
    async def test_engine_none_with_toast(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock()
        mock_cm.engine = None
        mock_toast = MagicMock()

        with patch("app.bootstrap.MetaDataManager"):
            result = await initialize_services(mock_cm, show_toast_fn=mock_toast)

        assert result["success"] is False
        mock_toast.assert_called_once()

    @pytest.mark.asyncio
    async def test_task_manager_init_failed(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock()
        mock_cm.engine = MagicMock()

        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
        ):
            mock_tm_instance = MagicMock()
            mock_tm.return_value = mock_tm_instance
            mock_tm_instance.init_db = AsyncMock(side_effect=Exception("tm error"))

            result = await initialize_services(mock_cm)

        assert result["success"] is False
        assert result["error"] == "task_manager_init_failed"
        detail = result.get("detail", "")
        assert isinstance(detail, str)
        assert "tm error" in detail

    @pytest.mark.asyncio
    async def test_db_upgrade_needed(self):
        mock_cm = MagicMock()
        mock_cm.init_db = AsyncMock(side_effect=DatabaseMigrationNeeded(current_rev="abc123", head_rev="def456"))

        result = await initialize_services(mock_cm)

        assert result["success"] is False
        assert result["error"] == "db_upgrade_needed"
        assert result["current_rev"] == "abc123"
        assert result["head_rev"] == "def456"


class TestMaybeAutoProbeOnStartup:
    """Phase 2A.1 Task 2A.1.13：bootstrap 启动期自动 probe 测试。"""

    def _make_client(self, *, token: str = "test_token", last_probe: datetime.datetime | None = None):
        client = MagicMock()
        client.token = token
        client.get_last_probe_time = MagicMock(return_value=last_probe)
        client.probe_api_capabilities = AsyncMock()
        return client

    @pytest.mark.asyncio
    async def test_auto_probe_on_startup_within_7_days(self):
        """距上次 probe 不到 7 天 → 不触发 probe。"""
        now = datetime.datetime.now(datetime.UTC)
        last_probe = now - datetime.timedelta(days=3)  # 3 天前，在 7 天内
        client = self._make_client(last_probe=last_probe)

        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            await _maybe_auto_probe_on_startup()

        client.probe_api_capabilities.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_probe_on_startup_over_7_days(self):
        """距上次 probe 超过 7 天 → 触发 probe。"""
        now = datetime.datetime.now(datetime.UTC)
        last_probe = now - datetime.timedelta(days=10)  # 10 天前，超过 7 天
        client = self._make_client(last_probe=last_probe)

        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            await _maybe_auto_probe_on_startup()

        client.probe_api_capabilities.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_probe_on_startup_never_probed(self):
        """从未 probe（last_probe is None）→ 触发 probe。"""
        now = datetime.datetime.now(datetime.UTC)
        client = self._make_client(last_probe=None)

        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            await _maybe_auto_probe_on_startup()

        client.probe_api_capabilities.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_probe_on_startup_failure_tolerant(self):
        """probe 抛 Exception → 降级 warning，不 raise（不影响主流程）。"""
        now = datetime.datetime.now(datetime.UTC)
        last_probe = now - datetime.timedelta(days=10)
        client = self._make_client(last_probe=last_probe)
        client.probe_api_capabilities = AsyncMock(side_effect=RuntimeError("network error"))

        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            # 不应 raise（异常降级 warning）
            await _maybe_auto_probe_on_startup()

        client.probe_api_capabilities.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_auto_probe_on_startup_skips_when_no_token(self):
        """Token 未配置 → 短路跳过，不读 AppState、不触发 probe。"""
        now = datetime.datetime.now(datetime.UTC)
        client = self._make_client(token="", last_probe=None)

        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            await _maybe_auto_probe_on_startup()

        client.probe_api_capabilities.assert_not_called()


class TestWarmupTushareCapabilities:
    """_warmup_tushare_capabilities 路径覆盖（warmup 成功/网络异常/超时/无 token 短路）。"""

    def _make_client(
        self,
        *,
        token: str = "test_token",
        cache: dict | None = None,
        load_exc: Exception | None = None,
    ) -> MagicMock:
        client = MagicMock()
        client.token = token
        if load_exc is not None:
            client.load_capabilities_from_app_state = AsyncMock(side_effect=load_exc)
        else:
            client.load_capabilities_from_app_state = AsyncMock()
        client.get_capability_cache = MagicMock(return_value=cache if cache is not None else {})
        return client

    @pytest.mark.asyncio
    async def test_warmup_success_with_cache(self):
        """warmup 成功且缓存非空 → info 日志路径。"""
        client = self._make_client(cache={"api1": True, "api2": False})
        with patch("data.external.tushare_client.TushareClient", return_value=client):
            from app.bootstrap import _warmup_tushare_capabilities

            await _warmup_tushare_capabilities()
        client.load_capabilities_from_app_state.assert_awaited_once()
        client.get_capability_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_warmup_success_empty_cache(self):
        """warmup 成功但缓存为空（首次启动或 token 变更）→ debug 日志路径。"""
        client = self._make_client(cache={})
        with patch("data.external.tushare_client.TushareClient", return_value=client):
            from app.bootstrap import _warmup_tushare_capabilities

            await _warmup_tushare_capabilities()
        client.load_capabilities_from_app_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warmup_no_token_skips(self):
        """无 Tushare token → 短路跳过，不调用 load_capabilities。"""
        client = self._make_client(token="")
        with patch("data.external.tushare_client.TushareClient", return_value=client):
            from app.bootstrap import _warmup_tushare_capabilities

            await _warmup_tushare_capabilities()
        client.load_capabilities_from_app_state.assert_not_called()

    @pytest.mark.asyncio
    async def test_warmup_network_error_tolerant(self):
        """网络异常 → 降级日志，不 raise（非关键路径）。"""
        client = self._make_client(load_exc=ConnectionError("network down"))
        with patch("data.external.tushare_client.TushareClient", return_value=client):
            from app.bootstrap import _warmup_tushare_capabilities

            await _warmup_tushare_capabilities()  # 不应 raise
        client.load_capabilities_from_app_state.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_warmup_timeout_tolerant(self):
        """超时异常 → 降级日志，不 raise。"""
        client = self._make_client(load_exc=TimeoutError("probe timeout"))
        with patch("data.external.tushare_client.TushareClient", return_value=client):
            from app.bootstrap import _warmup_tushare_capabilities

            await _warmup_tushare_capabilities()  # 不应 raise
        client.load_capabilities_from_app_state.assert_awaited_once()


class TestValidateFailoverCredentials:
    """_validate_failover_credentials 路径覆盖（凭据缺失/无效/多供应商混合）。"""

    def test_missing_credentials_warns(self):
        """凭据缺失 → 记录 warning，不 raise。"""
        with patch(
            "utils.config_handler.ConfigHandler.validate_failover_credentials",
            return_value=["openai"],
        ):
            from app.bootstrap import _validate_failover_credentials

            _validate_failover_credentials()  # 不应 raise

    def test_all_credentials_present(self):
        """所有凭据齐全 → 不记录 warning。"""
        with patch(
            "utils.config_handler.ConfigHandler.validate_failover_credentials",
            return_value=[],
        ):
            from app.bootstrap import _validate_failover_credentials

            _validate_failover_credentials()

    def test_mixed_providers_partial_missing(self):
        """多供应商混合：部分缺失 → warning 列出缺失项。"""
        with patch(
            "utils.config_handler.ConfigHandler.validate_failover_credentials",
            return_value=["openai", "deepseek", "qwen"],
        ):
            from app.bootstrap import _validate_failover_credentials

            _validate_failover_credentials()

    def test_validate_raises_exception_tolerant(self):
        """ConfigHandler 抛异常（凭据无效/配置损坏）→ 降级日志，不 raise。"""
        with patch(
            "utils.config_handler.ConfigHandler.validate_failover_credentials",
            side_effect=RuntimeError("config corrupted"),
        ):
            from app.bootstrap import _validate_failover_credentials

            _validate_failover_credentials()  # 不应 raise


class TestValidateStrategyTierCoverage:
    """_validate_strategy_tier_coverage 路径覆盖（全部/部分/未覆盖/异常降级）。"""

    def test_all_covered(self):
        """所有策略都在 _STRATEGY_MIN_TIER 中 → 调用 validate_strategy_tier_coverage。"""
        registered = {"oversold": MagicMock(), "volume_surge": MagicMock()}
        with (
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
            patch("services.ai_service.validate_strategy_tier_coverage") as mock_validate,
        ):
            mock_sm.return_value.strategies = registered
            from app.bootstrap import _validate_strategy_tier_coverage

            _validate_strategy_tier_coverage()
        mock_validate.assert_called_once_with(set(registered.keys()))

    def test_partial_coverage(self):
        """部分策略未登记 → 仍调用 validate_strategy_tier_coverage（内部 warning）。"""
        registered = {"oversold": MagicMock(), "unknown_strategy": MagicMock()}
        with (
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
            patch("services.ai_service.validate_strategy_tier_coverage") as mock_validate,
        ):
            mock_sm.return_value.strategies = registered
            from app.bootstrap import _validate_strategy_tier_coverage

            _validate_strategy_tier_coverage()
        mock_validate.assert_called_once_with(set(registered.keys()))

    def test_strategy_uncovered_warning_only(self):
        """策略完全未覆盖 → validate 内部 warning，不 raise。"""
        registered = {"unknown_strategy": MagicMock()}
        with (
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
            patch("services.ai_service.validate_strategy_tier_coverage") as mock_validate,
        ):
            mock_sm.return_value.strategies = registered
            from app.bootstrap import _validate_strategy_tier_coverage

            _validate_strategy_tier_coverage()
        mock_validate.assert_called_once_with({"unknown_strategy"})

    def test_strategy_manager_init_failed_tolerant(self):
        """StrategyManager 初始化抛异常 → 降级日志，不 raise。"""
        with patch("strategies.all_strategies.StrategyManager", side_effect=RuntimeError("sm init failed")):
            from app.bootstrap import _validate_strategy_tier_coverage

            _validate_strategy_tier_coverage()  # 不应 raise

    def test_validate_strategy_tier_coverage_raises_tolerant(self):
        """validate_strategy_tier_coverage 抛异常 → 降级日志，不 raise。"""
        with (
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
            patch(
                "services.ai_service.validate_strategy_tier_coverage",
                side_effect=RuntimeError("validate failed"),
            ),
        ):
            mock_sm.return_value.strategies = {"oversold": MagicMock()}
            from app.bootstrap import _validate_strategy_tier_coverage

            _validate_strategy_tier_coverage()  # 不应 raise


class TestInitializeServicesStartFailures:
    """initialize_services 中 SchedulerService/NewsSubscriptionService/MarketDataService start 异常路径。

    bootstrap.py 在 start 调用段未加 try/except，异常沿调用栈传播给 main.py。
    """

    def _make_cm(self) -> MagicMock:
        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        return cm

    @pytest.mark.asyncio
    async def test_scheduler_service_start_raises(self):
        """SchedulerService.start 抛异常 → 沿调用栈传播。"""
        cm = self._make_cm()
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService") as mock_ss,
            patch("app.bootstrap.NewsSubscriptionService"),
            patch("app.bootstrap.MarketDataService"),
            patch("app.bootstrap._warmup_tushare_capabilities", new_callable=AsyncMock),
            patch("app.bootstrap._validate_failover_credentials"),
            patch("app.bootstrap._validate_strategy_tier_coverage"),
            patch("app.bootstrap._maybe_auto_probe_on_startup", new_callable=AsyncMock),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ss.return_value.start = MagicMock(side_effect=RuntimeError("scheduler failed"))
            with pytest.raises(RuntimeError, match="scheduler failed"):
                await initialize_services(cm)

    @pytest.mark.asyncio
    async def test_news_subscription_service_start_raises(self):
        """NewsSubscriptionService.start 抛异常 → 沿调用栈传播。"""
        cm = self._make_cm()
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService"),
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService"),
            patch("app.bootstrap._warmup_tushare_capabilities", new_callable=AsyncMock),
            patch("app.bootstrap._validate_failover_credentials"),
            patch("app.bootstrap._validate_strategy_tier_coverage"),
            patch("app.bootstrap._maybe_auto_probe_on_startup", new_callable=AsyncMock),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ns.return_value.start = AsyncMock(side_effect=RuntimeError("news failed"))
            with pytest.raises(RuntimeError, match="news failed"):
                await initialize_services(cm)

    @pytest.mark.asyncio
    async def test_market_data_service_start_raises(self):
        """MarketDataService.start 抛异常 → 沿调用栈传播。"""
        cm = self._make_cm()
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService"),
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService") as mock_mds,
            patch("app.bootstrap._warmup_tushare_capabilities", new_callable=AsyncMock),
            patch("app.bootstrap._validate_failover_credentials"),
            patch("app.bootstrap._validate_strategy_tier_coverage"),
            patch("app.bootstrap._maybe_auto_probe_on_startup", new_callable=AsyncMock),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ns.return_value.start = AsyncMock()
            mock_mds.return_value.start = AsyncMock(side_effect=RuntimeError("market failed"))
            with pytest.raises(RuntimeError, match="market failed"):
                await initialize_services(cm)


class TestInitializeServicesCancelledError:
    """R2 守卫：CancelledError 在 initialize_services 内部必须原样传播，不被 except Exception 吞没。

    Python 3.8+ 中 asyncio.CancelledError 继承自 BaseException，不被 except Exception 捕获，
    因此 cache_manager.init_db / TaskManager.init_db / *_warmup_tushare_capabilities 内部的
    try/except Exception 不会拦截 CancelledError，自动满足 R2 红线。
    """

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_news_start(self):
        """NewsSubscriptionService.start 抛 CancelledError → 必须原样 raise（R2 守卫）。"""
        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService"),
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService"),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ns.return_value.start = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await initialize_services(cm)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_market_data_start(self):
        """MarketDataService.start 抛 CancelledError → 必须原样 raise（R2 守卫）。"""
        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService"),
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService") as mock_mds,
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ns.return_value.start = AsyncMock()
            mock_mds.return_value.start = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await initialize_services(cm)

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_from_warmup(self):
        """_warmup_tushare_capabilities 内部 await 抛 CancelledError → 必须原样 raise（R2 守卫）。

        _warmup_tushare_capabilities 内部 except Exception 不会捕获 CancelledError，
        会传播到 initialize_services 的 await 点。
        """
        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        client = MagicMock()
        client.token = "test_token"
        client.load_capabilities_from_app_state = AsyncMock(side_effect=asyncio.CancelledError())
        client.get_capability_cache = MagicMock(return_value={})
        with (
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService"),
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService") as mock_mds,
            patch("data.external.tushare_client.TushareClient", return_value=client),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            mock_ns.return_value.start = AsyncMock()
            mock_mds.return_value.start = AsyncMock()
            with pytest.raises(asyncio.CancelledError):
                await initialize_services(cm)

    @pytest.mark.asyncio
    async def test_cancelled_error_raises_from_auto_probe(self):
        """R2 守卫：_maybe_auto_probe_on_startup 内部 CancelledError 必须 raise（不降级 warning）。

        覆盖 bootstrap.py 第 255-257 行的 ``except asyncio.CancelledError: raise`` 路径，
        确保 CancelledError 不被后续 ``except Exception`` 误捕。
        """
        now = datetime.datetime.now(datetime.UTC)
        last_probe = now - datetime.timedelta(days=10)
        client = MagicMock()
        client.token = "test_token"
        client.get_last_probe_time = MagicMock(return_value=last_probe)
        client.probe_api_capabilities = AsyncMock(side_effect=asyncio.CancelledError())
        with (
            patch("data.external.tushare_client.TushareClient", return_value=client),
            patch("utils.time_utils.get_now", return_value=now),
        ):
            from app.bootstrap import _maybe_auto_probe_on_startup

            with pytest.raises(asyncio.CancelledError):
                await _maybe_auto_probe_on_startup()


class TestE2ETestingShortCircuit:
    """E2E_TESTING 短路分支：跳过 SchedulerService/NewsSubscriptionService/MarketDataService。"""

    @pytest.mark.asyncio
    async def test_e2e_testing_skips_schedulers(self):
        """E2E_TESTING=true → 不启动后台调度/数据轮询服务。"""
        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        with (
            patch.dict("os.environ", {"E2E_TESTING": "true"}),
            patch("app.bootstrap.MetaDataManager"),
            patch("app.bootstrap.TaskManager") as mock_tm,
            patch("app.bootstrap.SchedulerService") as mock_ss,
            patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
            patch("app.bootstrap.MarketDataService") as mock_mds,
            patch("app.bootstrap._warmup_tushare_capabilities", new_callable=AsyncMock),
            patch("app.bootstrap._validate_failover_credentials"),
            patch("app.bootstrap._validate_strategy_tier_coverage"),
            patch("app.bootstrap._maybe_auto_probe_on_startup", new_callable=AsyncMock),
        ):
            mock_tm.return_value.init_db = AsyncMock()
            result = await initialize_services(cm)
        assert result["success"] is True
        mock_ss.assert_not_called()
        mock_ns.assert_not_called()
        mock_mds.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_e2e_starts_schedulers(self):
        """非 E2E 模式 → 正常启动 SchedulerService/NewsSubscriptionService/MarketDataService。"""
        import os

        cm = MagicMock()
        cm.init_db = AsyncMock()
        cm.engine = MagicMock()
        saved = os.environ.pop("E2E_TESTING", None)
        try:
            with (
                patch("app.bootstrap.MetaDataManager"),
                patch("app.bootstrap.TaskManager") as mock_tm,
                patch("app.bootstrap.SchedulerService") as mock_ss,
                patch("app.bootstrap.NewsSubscriptionService") as mock_ns,
                patch("app.bootstrap.MarketDataService") as mock_mds,
                patch("app.bootstrap._warmup_tushare_capabilities", new_callable=AsyncMock),
                patch("app.bootstrap._validate_failover_credentials"),
                patch("app.bootstrap._validate_strategy_tier_coverage"),
                patch("app.bootstrap._maybe_auto_probe_on_startup", new_callable=AsyncMock),
            ):
                mock_tm.return_value.init_db = AsyncMock()
                mock_ns.return_value.start = AsyncMock()
                mock_mds.return_value.start = AsyncMock()
                result = await initialize_services(cm)
        finally:
            if saved is not None:
                os.environ["E2E_TESTING"] = saved
        assert result["success"] is True
        mock_ss.return_value.start.assert_called_once()
        mock_ns.return_value.start.assert_awaited_once()
        mock_mds.return_value.start.assert_awaited_once()


class TestMaskSensitiveEdgeCases:
    """P3-4: mask_sensitive 边界覆盖（R9 守卫：Token/API Key/Password 脱敏）。

    复用 DataSanitizer.sanitize_token 后，非字符串/短字符串/长字符串均安全脱敏：
    - 非字符串输入 → "***"（sanitize_token 的 isinstance 守卫）
    - 短字符串（< 32）→ "***"（避免旧实现固定泄露前 4 字符）
    - 长字符串（≥ 32）→ "前3***后4"（部分脱敏，便于人工辨识）
    """

    def test_empty_string_returns_masked(self):
        """空字符串 → '***'。"""
        assert mask_sensitive("") == "***"

    def test_no_sensitive_field_regular_string(self):
        """普通短字符串（< 32）→ 全部隐藏为 '***'。"""
        result = mask_sensitive("hello world")
        assert result == "***"
        assert "hello world" not in result

    def test_token_masked(self):
        """R9 守卫：短 token 字符串脱敏后不含完整 token。"""
        token = "sk-1234567890abcdef"
        result = mask_sensitive(token)
        assert result == "***"
        assert "1234567890abcdef" not in result
        assert token not in result

    def test_api_key_masked(self):
        """R9 守卫：API key 脱敏后不含完整 key。"""
        api_key = "sk-abcdef1234567890"
        result = mask_sensitive(api_key)
        assert api_key not in result
        assert result == "***"

    def test_password_masked(self):
        """R9 守卫：密码字符串脱敏后不含完整密码。"""
        password = "super_secret_password_123"
        result = mask_sensitive(password)
        assert password not in result
        assert result == "***"

    def test_nested_dict_returns_masked(self):
        """嵌套字典 → '***'（sanitize_token 对非字符串输入统一返回 '***'）。"""
        nested = {"a": {"token": "secret"}, "b": {"api_key": "k"}}
        assert mask_sensitive(nested) == "***"

    def test_nested_dict_long_returns_masked(self):
        """嵌套字典（>4 keys）→ '***'（不再触发 KeyError 边界行为）。"""
        nested = {f"k{i}": {"token": f"secret{i}"} for i in range(5)}
        assert mask_sensitive(nested) == "***"

    def test_list_of_dicts_returns_masked(self):
        """列表内字典 → '***'（sanitize_token 对非字符串输入统一返回 '***'）。"""
        data = [{"token": "secret"}, {"api_key": "k"}]
        assert mask_sensitive(data) == "***"

    def test_list_of_dicts_long_returns_masked(self):
        """列表内字典（>4 元素）→ '***'（不再泄露 list 切片 repr）。"""
        data = [{"token": f"secret{i}"} for i in range(5)]
        result = mask_sensitive(data)
        assert result == "***"
        for item in data:
            assert str(item) not in result
