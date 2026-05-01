"""
Tests for ProxyManager module.

S2-2: ProxyManager union NO_PROXY/no_proxy 环境变量测试。
S2-3: ProxyManager reapply_proxy_policy 运行时重应用测试。

Note: On Windows, environment variable names are case-insensitive,
so NO_PROXY and no_proxy refer to the same variable.
"""

import os
import sys
from unittest.mock import patch

import pytest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))


class TestProxyManagerNoProxyUnion:
    """S2-2: NO_PROXY/no_proxy 环境变量合并测试"""

    @pytest.fixture(autouse=True)
    def manage_env(self):
        """自动管理环境变量"""
        original_no_proxy = os.environ.get("NO_PROXY")

        yield

        if original_no_proxy is not None:
            os.environ["NO_PROXY"] = original_no_proxy
        elif "NO_PROXY" in os.environ:
            del os.environ["NO_PROXY"]

    def test_no_proxy_preserved(self, manage_env):
        """NO_PROXY 环境变量被保留"""
        from utils.proxy_manager import ProxyManager

        os.environ["NO_PROXY"] = "localhost,127.0.0.1"

        with patch("utils.proxy_manager.ConfigHandler.get_no_proxy_domains", return_value=[]):
            ProxyManager.apply_smart_proxy_policy()

        no_proxy = os.environ.get("NO_PROXY", "")
        assert "localhost" in no_proxy
        assert "127.0.0.1" in no_proxy

    def test_config_domains_added(self, manage_env):
        """配置中的域名被添加到 NO_PROXY"""
        from utils.proxy_manager import ProxyManager

        os.environ["NO_PROXY"] = "localhost"

        with patch("utils.proxy_manager.ConfigHandler.get_no_proxy_domains", return_value=["example.com", ".local"]):
            ProxyManager.apply_smart_proxy_policy()

        no_proxy = os.environ.get("NO_PROXY", "")
        assert "localhost" in no_proxy
        assert "example.com" in no_proxy
        assert ".local" in no_proxy

    def test_no_duplicate_domains(self, manage_env):
        """重复域名不重复添加"""
        from utils.proxy_manager import ProxyManager

        os.environ["NO_PROXY"] = "localhost,example.com"

        with patch("utils.proxy_manager.ConfigHandler.get_no_proxy_domains", return_value=["localhost", "example.com"]):
            ProxyManager.apply_smart_proxy_policy()

        no_proxy = os.environ.get("NO_PROXY", "")
        assert no_proxy.count("localhost") == 1
        assert no_proxy.count("example.com") == 1

    def test_empty_no_proxy_with_config(self, manage_env):
        """空 NO_PROXY 时添加配置域名"""
        from utils.proxy_manager import ProxyManager

        if "NO_PROXY" in os.environ:
            del os.environ["NO_PROXY"]

        with patch("utils.proxy_manager.ConfigHandler.get_no_proxy_domains", return_value=["example.com"]):
            ProxyManager.apply_smart_proxy_policy()

        no_proxy = os.environ.get("NO_PROXY", "")
        assert "example.com" in no_proxy


class TestProxyManagerReapply:
    """S2-3: reapply_proxy_policy should update NO_PROXY at runtime"""

    @pytest.fixture(autouse=True)
    def manage_env(self):
        original_no_proxy = os.environ.get("NO_PROXY")

        yield

        if original_no_proxy is not None:
            os.environ["NO_PROXY"] = original_no_proxy
        elif "NO_PROXY" in os.environ:
            del os.environ["NO_PROXY"]

    def test_reapply_updates_no_proxy(self, manage_env):
        """reapply_proxy_policy should apply new config domains"""
        from utils.proxy_manager import ProxyManager

        os.environ["NO_PROXY"] = "localhost"

        with patch("utils.proxy_manager.ConfigHandler.get_no_proxy_domains", return_value=["newdomain.com"]):
            ProxyManager.reapply_proxy_policy()

        no_proxy = os.environ.get("NO_PROXY", "")
        assert "newdomain.com" in no_proxy
        assert "localhost" in no_proxy

    def test_reapply_calls_apply_smart_proxy_policy(self, manage_env):
        """reapply_proxy_policy should delegate to apply_smart_proxy_policy"""
        from utils.proxy_manager import ProxyManager

        with patch("utils.proxy_manager.ProxyManager.apply_smart_proxy_policy") as mock_apply:
            ProxyManager.reapply_proxy_policy()
            mock_apply.assert_called_once()
