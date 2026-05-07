import inspect
import os

from unittest.mock import patch

from utils.proxy_manager import ProxyManager


class TestProxyManagerEnvironWrite:
    def setup_method(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = False
        ProxyManager._original_no_proxy = None

    def test_apply_writes_to_os_environ(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro", "localhost"]
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            ProxyManager.apply_smart_proxy_policy()

            assert os.environ.get("NO_PROXY") is not None
            assert "api.tushare.pro" in os.environ["NO_PROXY"]
            assert "localhost" in os.environ["NO_PROXY"]
            assert os.environ.get("no_proxy") == os.environ.get("NO_PROXY")

    def test_apply_caches_domains(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro", "localhost"]
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            ProxyManager.apply_smart_proxy_policy()

            domains = ProxyManager.get_no_proxy_domains()
            assert "api.tushare.pro" in domains
            assert "localhost" in domains

    def test_clears_environ_when_no_domains(self):
        ProxyManager._original_no_proxy = set()
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = []
            os.environ["NO_PROXY"] = "stale.domain"
            os.environ["no_proxy"] = "stale.domain"

            ProxyManager.apply_smart_proxy_policy()

            assert os.environ.get("NO_PROXY") is None
            assert os.environ.get("no_proxy") is None


class TestProxyManagerSnapshotOriginal:
    def setup_method(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = False
        ProxyManager._original_no_proxy = None

    def test_snapshots_original_env_on_first_call(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
            os.environ["NO_PROXY"] = "localhost,127.0.0.1"

            ProxyManager.apply_smart_proxy_policy()

            assert ProxyManager._original_no_proxy == {"localhost", "127.0.0.1"}

    def test_reapply_uses_original_not_current_env(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
            os.environ["NO_PROXY"] = "localhost"

            ProxyManager.apply_smart_proxy_policy()

            assert "api.tushare.pro" in os.environ["NO_PROXY"]

            mock_ch.get_no_proxy_domains.return_value = []
            ProxyManager.reapply_proxy_policy()

            assert "api.tushare.pro" not in os.environ.get("NO_PROXY", "")
            assert "localhost" in os.environ.get("NO_PROXY", "")

    def test_reapply_removes_domain_deleted_from_config(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["tushare.pro", "api.example.com"]
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            ProxyManager.apply_smart_proxy_policy()
            assert "tushare.pro" in os.environ["NO_PROXY"]
            assert "api.example.com" in os.environ["NO_PROXY"]

            mock_ch.get_no_proxy_domains.return_value = ["tushare.pro"]
            ProxyManager.reapply_proxy_policy()

            assert "tushare.pro" in os.environ["NO_PROXY"]
            assert "api.example.com" not in os.environ["NO_PROXY"]


class TestProxyManagerGetNoProxyString:
    def test_returns_comma_separated(self):
        ProxyManager._no_proxy_domains = {"api.tushare.pro", "localhost"}
        ProxyManager._initialized = True

        result = ProxyManager.get_no_proxy_string()
        assert "api.tushare.pro" in result
        assert "localhost" in result
        assert "," in result

    def test_empty_returns_empty_string(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        assert ProxyManager.get_no_proxy_string() == ""


class TestProxyManagerShouldBypassProxy:
    def setup_method(self):
        ProxyManager._no_proxy_domains = {"tushare.pro", "localhost"}
        ProxyManager._initialized = True

    def test_exact_match(self):
        assert ProxyManager.should_bypass_proxy("tushare.pro") is True

    def test_subdomain_match(self):
        assert ProxyManager.should_bypass_proxy("api.tushare.pro") is True

    def test_no_match(self):
        assert ProxyManager.should_bypass_proxy("google.com") is False

    def test_empty_hostname(self):
        assert ProxyManager.should_bypass_proxy("") is False

    def test_none_hostname(self):
        assert ProxyManager.should_bypass_proxy(None) is False

    def test_partial_match_not_bypassed(self):
        assert ProxyManager.should_bypass_proxy("notushare.pro") is False

    def test_localhost(self):
        assert ProxyManager.should_bypass_proxy("localhost") is True


class TestProxyManagerGetHttpxProxyConfig:
    def test_no_proxy_env_returns_empty(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        with patch.dict(os.environ, {}, clear=True):
            result = ProxyManager.get_httpx_proxy_config()
            assert result == {}

    def test_with_proxy_env(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=False):
            result = ProxyManager.get_httpx_proxy_config()
            assert "proxies" in result
            assert "https://" in result["proxies"]


class TestProxyManagerGetRequestsProxyConfig:
    def test_no_proxy_env_returns_none(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        with patch.dict(os.environ, {}, clear=True):
            result = ProxyManager.get_requests_proxy_config()
            assert result is None

    def test_with_proxy_env(self):
        ProxyManager._no_proxy_domains = {"tushare.pro"}
        ProxyManager._initialized = True

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=False):
            result = ProxyManager.get_requests_proxy_config()
            assert result is not None
            assert "proxies" in result
            assert "no_proxy" in result["proxies"]
            assert "tushare.pro" in result["proxies"]["no_proxy"]


class TestProxyManagerMergesExistingEnv:
    def setup_method(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = False
        ProxyManager._original_no_proxy = None

    def test_merges_existing_no_proxy(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]

            with patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1"}, clear=False):
                ProxyManager.apply_smart_proxy_policy()

            domains = ProxyManager.get_no_proxy_domains()
            assert "localhost" in domains
            assert "127.0.0.1" in domains
            assert "api.tushare.pro" in domains


class TestProxyManagerLogSafety:
    def test_apply_does_not_log_full_domain_list(self):
        source = inspect.getsource(ProxyManager.apply_smart_proxy_policy)
        assert "NO_PROXY={new_no_proxy}" not in source, "Should not log full NO_PROXY domain list via f-string"

    def test_log_only_shows_count(self):
        source = inspect.getsource(ProxyManager.apply_smart_proxy_policy)
        log_lines = [line for line in source.split("\n") if "logger.info" in line and "Configuration applied" in line]
        for line in log_lines:
            assert "NO_PROXY=" not in line or "len(" in line, "Log should only show count, not domain values"
