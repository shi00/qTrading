import datetime

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bootstrap import check_onboarding_needed, initialize_services, mask_sensitive
from data.persistence.db_migrator import DatabaseMigrationNeeded

pytestmark = pytest.mark.unit


class TestMaskSensitive:
    def test_long_value_masked(self):
        assert mask_sensitive("abcdefghijklmnop") == "abcd****"

    def test_short_value_returns_none(self):
        assert mask_sensitive("abc") == "None"

    def test_none_value_returns_none(self):
        assert mask_sensitive(None) == "None"

    def test_empty_value_returns_none(self):
        assert mask_sensitive("") == "None"

    def test_exact_prefix_len(self):
        assert mask_sensitive("abcd") == "None"

    def test_custom_prefix_len(self):
        assert mask_sensitive("abcdefgh", prefix_len=2) == "ab****"


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
