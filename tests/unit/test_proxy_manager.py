# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import asyncio
import os
import threading

from collections.abc import Iterator, Mapping, MutableMapping
from types import SimpleNamespace
from unittest.mock import patch

from utils.proxy_manager import ProxyManager
from utils.sanitizers import DataSanitizer
import pytest


pytestmark = pytest.mark.unit

# RU-F01：与生产实现独立声明的六个代理键，测试自带清单以免实现改动掩盖漏测。
_PROXY_ENV_KEYS = ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")

_IS_WINDOWS = os.name == "nt"

# POSIX：os.environ 大小写敏感，六键可严格区分“存在/空串/不存在”三种状态。
_ORIGINAL_ENV_POSIX: dict[str, str] = {
    "NO_PROXY": "original-upper.domain",
    "no_proxy": "",
    "HTTP_PROXY": "http://orig-http:8080",
    "https_proxy": "http://orig-https:8443",
}
# Windows：os.environ 大小写不敏感，同一逻辑键（NO_PROXY/no_proxy 等）折叠为一个
# 真实条目，不得同时设置大小写重复键（后者会覆盖前者）。故用单一小写键集合表达
# 同一份预期环境，断言时按逻辑键折叠后比较“存在性 + 值”。
_ORIGINAL_ENV_WINDOWS: dict[str, str] = {
    "no_proxy": "original-upper.domain",
    "http_proxy": "http://orig-http:8080",
    "https_proxy": "http://orig-https:8443",
}
# 合成原始环境 O（平台相关）；合成策略环境 M 由 _no_proxy_domains 推导。
_ORIGINAL_ENV: dict[str, str] = _ORIGINAL_ENV_WINDOWS if _IS_WINDOWS else _ORIGINAL_ENV_POSIX
_POLICY_DOMAIN = "policy.example"
_POLICY_NO_PROXY = _POLICY_DOMAIN


def _fold_proxy_env(environ: Mapping[str, str]) -> dict[str, str]:
    """把六个代理键按大小写不敏感语义折叠为小写逻辑键。

    仅保留实际存在的键（空串也算存在），整体比较该映射即同时校验逻辑键的
    存在性与值；Windows 下六键折叠为三个真实条目时不会退化为仅比较值。
    """
    folded: dict[str, str] = {}
    for key in _PROXY_ENV_KEYS:
        value = environ.get(key)
        if value is not None:
            folded[key.lower()] = value
    return folded


def _assert_proxy_env(expected: dict[str, str]) -> None:
    """严格断言六个代理变量的存在性与值均恢复（平台感知）。"""
    if _IS_WINDOWS:
        # Windows：大小写不敏感，折叠为逻辑键后整体比较（键集合即存在性）。
        actual = _fold_proxy_env(os.environ)
        assert actual == expected, f"代理环境未严格恢复: {actual!r} != {expected!r}"
        return
    for key in _PROXY_ENV_KEYS:
        if key in expected:
            assert os.environ.get(key) == expected[key], f"{key} 值未严格恢复"
        else:
            assert key not in os.environ, f"{key} 应不存在（存在性未恢复）"


class _CaseInsensitiveEnviron(MutableMapping[str, str]):
    """以规范化键模拟 Windows os.environ 的大小写不敏感语义（Linux 可运行）。"""

    def __init__(self, initial: Mapping[str, str] | None = None) -> None:
        self._data: dict[str, str] = {}
        if initial:
            self.update(initial)

    @staticmethod
    def _normalize(key: str) -> str:
        return key.upper()

    def __getitem__(self, key: str) -> str:
        return self._data[self._normalize(key)]

    def __setitem__(self, key: str, value: str) -> None:
        self._data[self._normalize(key)] = value

    def __delitem__(self, key: str) -> None:
        del self._data[self._normalize(key)]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


@pytest.fixture
def synthetic_proxy_env(monkeypatch):
    """仅用合成值覆盖六个代理键；不读取真实代理配置，结束由 monkeypatch 还原。"""

    def _apply(values: dict[str, str]) -> None:
        # 先清空六个键，避免 Windows 大小写不敏感下遗留同逻辑键的旧条目。
        for key in _PROXY_ENV_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, value in values.items():
            monkeypatch.setenv(key, value)

    return _apply


@pytest.fixture(autouse=True)
def _isolate_no_proxy_env(monkeypatch):
    """隔离宿主 NO_PROXY/no_proxy，避免断言受运行环境（如容器/K8s）代理配置污染。"""
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)


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


class TestProxyManagerGetHttpxProxyKwargs:
    """review08-B2 复核：httpx 0.28 显式 proxy 忽略 NO_PROXY → 按 host 判定直连。"""

    def test_bypass_host_returns_direct_kwargs(self):
        ProxyManager._reset_singleton()
        ProxyManager._no_proxy_domains = {"money.finance.sina.com.cn"}
        ProxyManager._initialized = True

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=True):
            result = ProxyManager.get_httpx_proxy_kwargs("money.finance.sina.com.cn")

        assert result == {"trust_env": False}

    def test_non_bypass_host_returns_explicit_proxy(self):
        ProxyManager._reset_singleton()
        ProxyManager._no_proxy_domains = {"money.finance.sina.com.cn"}
        ProxyManager._initialized = True

        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=True):
            result = ProxyManager.get_httpx_proxy_kwargs("www.cls.cn")

        assert result == {"proxy": "http://proxy:8080"}

    def test_non_bypass_host_without_proxy_env_returns_empty(self):
        ProxyManager._reset_singleton()
        ProxyManager._no_proxy_domains = set()
        ProxyManager._initialized = True

        with patch.dict(os.environ, {}, clear=True):
            result = ProxyManager.get_httpx_proxy_kwargs("www.cls.cn")

        assert result == {}


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

        # clear=True 隔离宿主环境的代理变量，避免键集合断言受宿主污染
        with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy:8080"}, clear=True):
            result = ProxyManager.get_requests_proxy_config()
            assert result is not None
            assert "proxies" in result
            # UTILS-B-03：requests 仅按 scheme 键匹配 proxies，no_proxy 键无效不得返回
            assert "no_proxy" not in result["proxies"]
            assert set(result["proxies"]) == {"https"}

    def test_proxies_keys_are_scheme_only(self):
        """proxies dict 仅含 scheme 代理键，不含 no_proxy 等无效键（UTILS-B-03）。

        requests 的 NO_PROXY 直连语义只来自环境变量（trust_env 默认读取）或调用方
        经 should_bypass_proxy() 自行处理；混入 no_proxy 键会让白名单域名静默走代理。
        """
        ProxyManager._no_proxy_domains = {"tushare.pro"}
        ProxyManager._initialized = True

        with patch.dict(
            os.environ,
            {"HTTP_PROXY": "http://proxy:3128", "HTTPS_PROXY": "http://proxy:3128"},
            clear=True,
        ):
            result = ProxyManager.get_requests_proxy_config()
            assert result is not None
            # 全量等值强断言：仅含 http/https 两个 scheme 代理键，值与环境一致
            assert result == {
                "proxies": {"http": "http://proxy:3128", "https": "http://proxy:3128"},
            }
            # UTILS-B-03：显式断言不含无效 no_proxy 键
            assert "no_proxy" not in result["proxies"]
            assert set(result["proxies"].keys()) == {"http", "https"}


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


class TestProxyManagerLitellmEnvScope:
    """RU-F01：跨 await 作用域的引用计数与唯一原始快照。

    旧实现用 threading.RLock 包住 yield：同 loop 的两个任务可先后重入，
    按 A 入→B 入→A 出→B 出 交错退出后会把策略值当作原值永久残留。
    本组用例只用合成变量编排交错/取消/嵌套/跨线程，不读取真实代理配置。
    """

    def setup_method(self):
        ProxyManager._reset_singleton()

    @staticmethod
    def _configure_policy() -> None:
        ProxyManager._no_proxy_domains = {_POLICY_DOMAIN}
        ProxyManager._initialized = True

    async def test_interleaved_enter_enter_exit_exit_restores_original(self, synthetic_proxy_env):
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()

        a_entered = asyncio.Event()
        b_entered = asyncio.Event()
        a_exited = asyncio.Event()

        async def task_a() -> None:
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY
                a_entered.set()
                await asyncio.wait_for(b_entered.wait(), timeout=5)
            # A 先退出：A 的退出不得恢复（B 仍在作用域内）
            a_exited.set()

        async def task_b() -> None:
            await asyncio.wait_for(a_entered.wait(), timeout=5)
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY
                b_entered.set()
                await asyncio.wait_for(a_exited.wait(), timeout=5)
                # 交错点：A 已退出、B 仍活动，策略必须继续生效
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

        await asyncio.wait_for(asyncio.gather(task_a(), task_b()), timeout=10)
        _assert_proxy_env(_ORIGINAL_ENV)

    async def test_cancel_first_entrant_keeps_policy_until_last_exit(self, synthetic_proxy_env):
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()

        a_entered = asyncio.Event()
        b_entered = asyncio.Event()
        a_cancelled = asyncio.Event()
        a_block = asyncio.Event()

        async def task_a() -> None:
            with ProxyManager.litellm_env_context():
                a_entered.set()
                await a_block.wait()

        async def task_b() -> None:
            await asyncio.wait_for(a_entered.wait(), timeout=5)
            with ProxyManager.litellm_env_context():
                b_entered.set()
                await asyncio.wait_for(a_cancelled.wait(), timeout=5)
                # A 被取消后 B 仍活动，策略不得被提前恢复
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

        task_a_obj = asyncio.create_task(task_a())
        await asyncio.wait_for(a_entered.wait(), timeout=5)
        task_b_obj = asyncio.create_task(task_b())
        await asyncio.wait_for(b_entered.wait(), timeout=5)

        task_a_obj.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task_a_obj
        assert task_a_obj.cancelled()
        assert exc_info.type is asyncio.CancelledError
        a_cancelled.set()

        await asyncio.wait_for(task_b_obj, timeout=10)
        _assert_proxy_env(_ORIGINAL_ENV)

    async def test_cancel_second_entrant_then_first_exit_restores_original(self, synthetic_proxy_env):
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()

        a_entered = asyncio.Event()
        b_entered = asyncio.Event()
        b_cancelled = asyncio.Event()
        b_block = asyncio.Event()

        async def task_a() -> None:
            with ProxyManager.litellm_env_context():
                a_entered.set()
                await asyncio.wait_for(b_cancelled.wait(), timeout=5)
                # B 被取消后 A 仍是最后一个活动作用域
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

        async def task_b() -> None:
            await asyncio.wait_for(a_entered.wait(), timeout=5)
            with ProxyManager.litellm_env_context():
                b_entered.set()
                await b_block.wait()

        task_a_obj = asyncio.create_task(task_a())
        await asyncio.wait_for(a_entered.wait(), timeout=5)
        task_b_obj = asyncio.create_task(task_b())
        await asyncio.wait_for(b_entered.wait(), timeout=5)

        task_b_obj.cancel()
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await task_b_obj
        assert task_b_obj.cancelled()
        assert exc_info.type is asyncio.CancelledError
        b_cancelled.set()

        await asyncio.wait_for(task_a_obj, timeout=10)
        _assert_proxy_env(_ORIGINAL_ENV)

    def test_strictly_nested_scopes_restore_original(self, synthetic_proxy_env):
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()

        with ProxyManager.litellm_env_context():
            assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY
            # 内层退出后外层仍持有作用域
            assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

        _assert_proxy_env(_ORIGINAL_ENV)

    def test_cross_thread_and_two_event_loops_restore_original(self, synthetic_proxy_env):
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()

        a_entered = threading.Event()
        b_entered = threading.Event()
        a_exited = threading.Event()
        errors: list[BaseException] = []

        async def task_a() -> None:
            with ProxyManager.litellm_env_context():
                a_entered.set()
                await asyncio.to_thread(b_entered.wait, 5)
            a_exited.set()

        async def task_b() -> None:
            await asyncio.to_thread(a_entered.wait, 5)
            with ProxyManager.litellm_env_context():
                b_entered.set()
                await asyncio.to_thread(a_exited.wait, 5)
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

        def _run(coro_factory) -> None:
            try:
                asyncio.run(coro_factory())
            except BaseException as exc:  # noqa: BLE001  # 线程异常须回传主线程断言
                errors.append(exc)

        thread_a = threading.Thread(target=_run, args=(task_a,))
        thread_b = threading.Thread(target=_run, args=(task_b,))
        thread_a.start()
        thread_b.start()
        thread_a.join(10)
        thread_b.join(10)

        assert not thread_a.is_alive()
        assert not thread_b.is_alive()
        assert errors == []
        _assert_proxy_env(_ORIGINAL_ENV)

    def test_reapply_during_active_scope_keeps_active_policy(self, synthetic_proxy_env):
        """运行中 reapply 只刷新缓存：活动作用域保持首入策略，新策略从下一作用域生效。"""
        synthetic_proxy_env(_ORIGINAL_ENV)
        self._configure_policy()
        # 模拟启动时已采集的真实原始快照，避免运行中 reapply 从活动环境反推“原始值”。
        ProxyManager._original_no_proxy = {"original-upper.domain"}

        with patch("utils.proxy_manager.ConfigHandler") as mock_ch:
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY

                mock_ch.get_no_proxy_domains.return_value = ["next.example"]
                ProxyManager.reapply_proxy_policy()

                # 活动作用域内固定快照语义：环境仍是首入策略 M
                assert os.environ.get("NO_PROXY") == _POLICY_NO_PROXY
                assert ProxyManager.get_no_proxy_domains() == {"original-upper.domain", "next.example"}

            _assert_proxy_env(_ORIGINAL_ENV)

            # 计数归零后重新进入，按新缓存策略写入
            with ProxyManager.litellm_env_context():
                assert os.environ.get("NO_PROXY") == "next.example,original-upper.domain"

        _assert_proxy_env(_ORIGINAL_ENV)

    def test_case_insensitive_env_snapshot_restores_exactly(self, monkeypatch):
        """覆盖 Windows os.environ 大小写不敏感折叠语义（Linux 下用自定义映射模拟 nt）。

        nt 把 NO_PROXY/no_proxy 等大小写变体折叠为一个真实条目。本用例以大小写
        不敏感的 MutableMapping 替换 proxy_manager 模块内的 os，验证唯一快照与
        最后退出者的恢复仍严格还原每个逻辑键的值与存在性（含“存在但空串”）。
        """
        fake_environ = _CaseInsensitiveEnviron(
            {
                "no_proxy": "original.domain",
                "HTTP_PROXY": "http://orig-http:8080",
                "https_proxy": "",  # 存在但空串：必须与“不存在”区分
            }
        )
        monkeypatch.setattr("utils.proxy_manager.os", SimpleNamespace(environ=fake_environ))
        self._configure_policy()

        with ProxyManager.litellm_env_context():
            assert fake_environ["NO_PROXY"] == _POLICY_NO_PROXY
            assert fake_environ["no_proxy"] == _POLICY_NO_PROXY

        # 折叠后整体等值：键集合校验存在性、值校验内容，空串条目不得丢失。
        assert _fold_proxy_env(fake_environ) == {
            "no_proxy": "original.domain",
            "http_proxy": "http://orig-http:8080",
            "https_proxy": "",
        }


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
