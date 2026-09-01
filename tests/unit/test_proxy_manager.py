# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import os

from unittest.mock import patch

from utils.proxy_manager import ProxyManager
from utils.sanitizers import DataSanitizer
import pytest


pytestmark = pytest.mark.unit


class TestProxyManagerNoEnvironWrite:
    def setup_method(self):
        ProxyManager._reset_singleton()

    def test_apply_does_not_write_to_os_environ(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro", "localhost"]
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_PROXY", None)
                os.environ.pop("no_proxy", None)

                ProxyManager.apply_smart_proxy_policy()

                assert os.environ.get("NO_PROXY") is None
                assert os.environ.get("no_proxy") is None

    def test_apply_caches_domains(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro", "localhost"]
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_PROXY", None)
                os.environ.pop("no_proxy", None)

                ProxyManager.apply_smart_proxy_policy()

                domains = ProxyManager.get_no_proxy_domains()
                assert "api.tushare.pro" in domains
                assert "localhost" in domains

    def test_existing_env_not_modified(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]
            with patch.dict(
                os.environ,
                {"NO_PROXY": "original.domain", "no_proxy": "original.domain"},
                clear=False,
            ):
                ProxyManager.apply_smart_proxy_policy()

                assert os.environ.get("NO_PROXY") == "original.domain"

    def test_clears_nothing_when_no_domains(self):
        ProxyManager._original_no_proxy = set()
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = []
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_PROXY", None)
                os.environ.pop("no_proxy", None)

                ProxyManager.apply_smart_proxy_policy()

                assert os.environ.get("NO_PROXY") is None


class TestProxyManagerSnapshotOriginal:
    def setup_method(self):
        ProxyManager._reset_singleton()

    def test_snapshots_original_env_on_first_call(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]
            with patch.dict(os.environ, {"NO_PROXY": "localhost,127.0.0.1"}, clear=False):
                ProxyManager.apply_smart_proxy_policy()

                assert ProxyManager._original_no_proxy == {"localhost", "127.0.0.1"}

    def test_reapply_uses_original_not_current_env(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = ["api.tushare.pro"]
            with patch.dict(os.environ, {"NO_PROXY": "localhost"}, clear=False):
                ProxyManager.apply_smart_proxy_policy()

                mock_ch.get_no_proxy_domains.return_value = []
                ProxyManager.reapply_proxy_policy()

                assert "api.tushare.pro" not in ProxyManager.get_no_proxy_string()

    def test_reapply_removes_domain_deleted_from_config(self):
        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            mock_ch.get_no_proxy_domains.return_value = [
                "tushare.pro",
                "api.example.com",
            ]
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("NO_PROXY", None)
                os.environ.pop("no_proxy", None)

                ProxyManager.apply_smart_proxy_policy()
                assert "tushare.pro" in ProxyManager.get_no_proxy_domains()
                assert "api.example.com" in ProxyManager.get_no_proxy_domains()

                mock_ch.get_no_proxy_domains.return_value = ["tushare.pro"]
                ProxyManager.reapply_proxy_policy()

                assert "tushare.pro" in ProxyManager.get_no_proxy_domains()
                assert "api.example.com" not in ProxyManager.get_no_proxy_domains()


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
        ProxyManager._reset_singleton()
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
            assert result == {"proxy": "http://proxy:8080"}

    def test_httpx_async_client_accepts_config(self):
        """httpx 0.28 AsyncClient(**get_httpx_proxy_config()) 可真实构造（防 proxies 回归）。

        B16 检视修复：httpx 0.28 移除 ``proxies`` 参数，旧 ``{"proxies": ...}`` 格式
        会抛 TypeError（有代理环境变量的用户功能失效）。
        """
        import httpx

        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=False):
            cfg = ProxyManager.get_httpx_proxy_config()

        import asyncio

        # 核心验证：构造不应抛 TypeError（httpx 0.28 移除 proxies 参数）
        client = httpx.AsyncClient(**cfg)
        asyncio.run(client.aclose())  # httpx 0.28 AsyncClient 用 aclose


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
        ProxyManager._reset_singleton()

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
        with patch.object(
            ProxyManager,
            "apply_smart_proxy_policy",
            wraps=ProxyManager.apply_smart_proxy_policy,
        ):
            assert hasattr(ProxyManager, "apply_smart_proxy_policy")

    def test_log_only_shows_count(self):
        with patch("utils.proxy_manager.logger") as mock_logger:
            ProxyManager._no_proxy_domains = {"tushare.pro", "localhost"}
            ProxyManager._initialized = True
            ProxyManager.apply_smart_proxy_policy()
            for call in mock_logger.info.call_args_list:
                msg = str(call)
                if "NO_PROXY" in msg:
                    assert "tushare.pro" not in msg and "localhost" not in msg, (
                        "Log should only show count, not domain values"
                    )


class TestProxyManagerLitellmEnvContext:
    def setup_method(self):
        ProxyManager._reset_singleton()
        ProxyManager._no_proxy_domains = {"tushare.pro", "localhost"}
        ProxyManager._initialized = True
        ProxyManager._env_written = False

    def test_sets_env_inside_context(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") is not None
                assert "tushare.pro" in os.environ["NO_PROXY"]
                assert "localhost" in os.environ["NO_PROXY"]

    def test_restores_env_after_context(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            with ProxyManager.litellm_env_context():
                pass

            assert os.environ.get("NO_PROXY") is None
            assert os.environ.get("no_proxy") is None

    def test_preserves_existing_env_on_exit(self):
        with patch.dict(
            os.environ,
            {"NO_PROXY": "pre-existing.domain", "no_proxy": "pre-existing.domain"},
            clear=False,
        ):
            with ProxyManager.litellm_env_context():
                assert "tushare.pro" in os.environ["NO_PROXY"]

            assert os.environ["NO_PROXY"] == "pre-existing.domain"

    def test_handles_exception_and_restores(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)

            try:
                with ProxyManager.litellm_env_context():
                    raise RuntimeError("test error")
            except RuntimeError:
                pass

            assert os.environ.get("NO_PROXY") is None

    def test_preserves_http_proxy_on_exit(self):
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://original:8080"}, clear=False):
            os.environ.pop("NO_PROXY", None)

            with ProxyManager.litellm_env_context():
                assert "tushare.pro" in os.environ.get("NO_PROXY", "")

            assert os.environ["HTTPS_PROXY"] == "http://original:8080"
            assert os.environ.get("NO_PROXY") is None

    def test_no_proxy_domains_empty_no_env_write(self):
        ProxyManager._no_proxy_domains = set()

        with patch.dict(os.environ, {}, clear=False):
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") is None

    def test_with_proxy_env_vars(self):
        ProxyManager._no_proxy_domains = {"tushare.pro"}
        with patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://proxy:3128", "HTTPS_PROXY": "http://proxy:3128"},
            clear=False,
        ):
            with ProxyManager.litellm_env_context():
                assert os.environ.get("HTTP_PROXY") == "http://proxy:3128"
                assert "tushare.pro" in os.environ.get("NO_PROXY", "")

            assert os.environ["HTTP_PROXY"] == "http://proxy:3128"


class TestProxyManagerRegistersProxyCredentials:
    """F11（检视 06）：认证代理 URL 的凭据须注册进 DataSanitizer，确保脱敏兜底。"""

    def setup_method(self):
        # DataSanitizer 非单例，测试间手动重置，避免 R7 状态污染
        DataSanitizer._reset_known_secrets()

    def test_registers_authenticated_proxy_credentials(self):
        ProxyManager._register_proxy_credentials("http://user:s3cret@proxy:8080")
        known = DataSanitizer._known_secrets.copy()
        assert "http://user:s3cret@proxy:8080" in known
        assert "user:s3cret" in known

    def test_ignores_proxy_without_credentials(self):
        DataSanitizer._reset_known_secrets()
        ProxyManager._register_proxy_credentials("http://proxy:8080")
        assert len(DataSanitizer._known_secrets) == 0

    def test_ignores_empty_url(self):
        DataSanitizer._reset_known_secrets()
        ProxyManager._register_proxy_credentials("")
        assert len(DataSanitizer._known_secrets) == 0

    def test_get_httpx_proxy_config_registers_credentials(self):
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True
        with patch.dict(
            os.environ,
            {"HTTPS_PROXY": "http://user:s3cret@proxy:8080"},
            clear=False,
        ):
            ProxyManager.get_httpx_proxy_config()
        known = DataSanitizer._known_secrets.copy()
        assert "http://user:s3cret@proxy:8080" in known
        assert "user:s3cret" in known

    def test_sanitize_dict_masks_authenticated_proxy_url(self):
        # 非敏感 key（proxy）下的 URL 走 _known_secrets 精确替换兜底（R9）。
        # 集合迭代顺序不确定，完整 URL 与 userinfo 谁先替换都可能，只断言密码已隐藏。
        ProxyManager._register_proxy_credentials("http://user:s3cret@proxy:8080")
        out = DataSanitizer.sanitize_dict({"proxy": "http://user:s3cret@proxy:8080"})
        assert "s3cret" not in out["proxy"]

    def test_sanitize_error_masks_authenticated_proxy_url(self):
        ProxyManager._register_proxy_credentials("http://user:s3cret@proxy:8080")
        msg = DataSanitizer.sanitize_error(RuntimeError("proxy http://user:s3cret@proxy:8080 failed"))
        assert "s3cret" not in msg
