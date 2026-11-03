import logging
import os
import re
import threading
import typing
from collections.abc import Generator
from contextlib import contextmanager

from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# litellm_env_context 统一管理的六个代理环境变量（大小写形态均可能被 SDK 读取）。
_PROXY_ENV_KEYS: tuple[str, ...] = (
    "NO_PROXY",
    "no_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "HTTPS_PROXY",
    "https_proxy",
)


class ProxyManager:
    """
    Manages network proxy settings to ensure stability for both
    Domestic (Direct) and International (Proxy) traffic.

    Design decisions (S-P0-2 fix):
    - NO_PROXY is cached internally but NOT written to os.environ by default.
    - Callers should use get_httpx_proxy_config() / get_requests_proxy_config()
      for per-client proxy injection.
    - For libraries that read os.environ (e.g. litellm), use the
      litellm_env_context() context manager to temporarily set/restore env.
    - Does NOT log the full domain list to prevent enterprise network
      topology leakage.
    - Preserves the ORIGINAL env NO_PROXY (before any ProxyManager writes)
      so that reapply can correctly remove domains that were deleted from config.
    """

    _no_proxy_domains: typing.ClassVar[set[str]] = set()
    _initialized: bool = False
    _original_no_proxy: set[str] | None = None
    _env_written: bool = False
    # 短临界区锁：只在 enter/exit 的同步段内持有，绝不跨 await/yield。
    # 进程环境是全局的，RLock/Lock 的差别在跨 await 场景下没有意义——这里改用
    # 不可重入 Lock，明确“只保护计数与快照的原子更新”，不假装能互斥协程。
    _env_lock: threading.Lock = threading.Lock()
    _config_lock: threading.RLock = threading.RLock()  # Lock for config mutation (domains, initialized)
    # 活动作用域引用计数与唯一原始快照（首个进入者采集，最后一个退出者恢复）。
    _env_scope_depth: int = 0
    _env_original: dict[str, str | None] | None = None

    @classmethod
    def _reset_singleton(cls):
        """Reset class-level state for testing only. NEVER call in production."""
        with cls._env_lock, cls._config_lock:
            cls._no_proxy_domains = set()
            cls._initialized = False
            cls._original_no_proxy = None
            cls._env_written = False
            cls._env_scope_depth = 0
            cls._env_original = None

    @staticmethod
    def apply_smart_proxy_policy():
        """
        Computes and caches the NO_PROXY domain list at startup.

        Strategy:
        1. On first call, snapshot the original NO_PROXY from env (before we modify it).
        2. Load whitelist from Config (user_settings.json).
        3. Merge original NO_PROXY with config whitelist.
        4. Cache the result for programmatic queries.
        5. Apply to os.environ ONLY if _env_written is explicitly enabled
           (for backward compatibility with litellm).
        """
        logger.info("[ProxyManager] Computing proxy configuration...")

        with ProxyManager._config_lock:
            if ProxyManager._original_no_proxy is None:
                original_domains: set[str] = set()
                no_proxy_upper = os.environ.get("NO_PROXY", "")
                no_proxy_lower = os.environ.get("no_proxy", "")

                if no_proxy_upper:
                    original_domains.update([d.strip() for d in no_proxy_upper.split(",") if d.strip()])
                if no_proxy_lower:
                    original_domains.update([d.strip() for d in no_proxy_lower.split(",") if d.strip()])

                ProxyManager._original_no_proxy = original_domains
                logger.info(
                    "[ProxyManager] Snapshotted %s original NO_PROXY domains from env.",
                    len(original_domains),
                )

            final_domains: set[str] = ProxyManager._original_no_proxy.copy()

            target_domains = ConfigHandler.get_no_proxy_domains()

            target_domains = list(
                set([d.strip() for d in target_domains if isinstance(d, str) and d.strip()]),
            )

            if not target_domains:
                logger.info("[ProxyManager] No cache/whitelist domains configured.")
            else:
                logger.info(
                    "[ProxyManager] Adding %s domains to NO_PROXY whitelist.",
                    len(target_domains),
                )
                final_domains.update(target_domains)

            ProxyManager._no_proxy_domains = final_domains
            ProxyManager._initialized = True

            if final_domains:
                valid_domains = [d for d in final_domains if d]
                logger.info(
                    "[ProxyManager] Configuration cached. %s domains in NO_PROXY.",
                    len(valid_domains),
                )
            else:
                logger.info("[ProxyManager] Configuration cached. NO_PROXY empty.")

    @staticmethod
    def reapply_proxy_policy():
        """
        Re-compute proxy policy at runtime when no_proxy_domains config changes.
        This should be called after ConfigHandler settings are updated.

        Uses the original env snapshot (not the current os.environ) so that
        domains removed from config are correctly removed from NO_PROXY.
        """
        logger.info("[ProxyManager] Re-computing proxy configuration (runtime update)...")
        ProxyManager.apply_smart_proxy_policy()

    @staticmethod
    def get_no_proxy_domains() -> set[str]:
        """Return the cached set of NO_PROXY domains."""
        with ProxyManager._config_lock:
            if not ProxyManager._initialized:
                pass  # Need to initialize outside lock to avoid deadlock
        if not ProxyManager._initialized:
            ProxyManager.apply_smart_proxy_policy()
        with ProxyManager._config_lock:
            return ProxyManager._no_proxy_domains.copy()

    @staticmethod
    def _register_proxy_credentials(proxy_url: str) -> None:
        """Register credentials embedded in an authenticated proxy URL with DataSanitizer.

        认证代理 URL（http://user:pass@host:port）内含密码。仅靠 sanitize_error 的
        URL-credentials 正则不足：sanitize_dict/sanitize_args 对非敏感 key（如 proxy）
        下的 URL 不会正则去码。此处把完整 URL 与其 userinfo 前缀注册进 `_known_secrets`，
        使日志脱敏走精确替换兜底（R9）。
        """
        if not proxy_url:
            return
        match = re.match(r"(?:[a-z][a-z0-9+.\-]*)://([^/@\s]+)@", proxy_url, re.IGNORECASE)
        if not match:
            # 无 user:pass@ 认证信息时无需注册（host/port 非敏感，注册会污染 _known_secrets）
            return
        DataSanitizer.register_secret(proxy_url)
        DataSanitizer.register_secret(match.group(1))

    @staticmethod
    def _resolve_proxy_env() -> tuple[str, str]:
        """Read HTTP_PROXY/HTTPS_PROXY (upper+lower) and register embedded credentials.

        Returns:
            (http_proxy, https_proxy)。
        """
        http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", ""))
        https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))
        ProxyManager._register_proxy_credentials(http_proxy)
        ProxyManager._register_proxy_credentials(https_proxy)
        return http_proxy, https_proxy

    @staticmethod
    def get_no_proxy_string() -> str:
        """Return the NO_PROXY domains as a comma-separated string."""
        domains = ProxyManager.get_no_proxy_domains()
        return ",".join(sorted(domains)) if domains else ""

    @staticmethod
    def should_bypass_proxy(hostname: str) -> bool:
        """
        Check if a given hostname should bypass the proxy.

        Args:
            hostname: The hostname to check (e.g. 'api.tushare.pro').

        Returns:
            True if the hostname matches any NO_PROXY domain.
        """
        if not hostname:
            return False

        domains = ProxyManager.get_no_proxy_domains()
        return any(hostname == domain or hostname.endswith("." + domain) for domain in domains)

    @staticmethod
    def get_httpx_proxy_config() -> dict:
        """Return proxy configuration suitable for httpx 0.28+ clients.

        httpx 0.28 移除 ``proxies``/``mounts`` 参数，改用单一 ``proxy``（str/Proxy）。
        返回 ``{"proxy": <url>}``（https 优先）或空 dict（无代理时，调用方依赖
        httpx ``trust_env=True`` 默认读环境代理）。
        # NOTE(lazy): httpx 0.28 显式 proxy 时不再应用 NO_PROXY 域（httpx 0.28
        移除 Proxy.no_proxy 参数）. ceiling: 本方法只表达「有代理则用代理」，
        依赖 NO_PROXY/直连域名配置的调用点须改用 get_httpx_proxy_kwargs(host)
        （review08-B2 复核修复已接入 news_fetcher 三处）. upgrade: httpx 恢复显式
        no_proxy 支持时把 host 判定收敛回本方法.
        """
        http_proxy, https_proxy = ProxyManager._resolve_proxy_env()

        if not http_proxy and not https_proxy:
            return {}

        # httpx 0.28 单 URL proxy；https 优先（与 requests 版 proxies 语义对齐）
        url = https_proxy or http_proxy
        return {"proxy": url}

    @staticmethod
    def get_httpx_proxy_kwargs(hostname: str) -> dict:
        """按 NO_PROXY 语义返回 httpx.AsyncClient 的代理 kwargs（review08-B2 复核修复）。

        httpx 0.28 显式 ``proxy`` 时不再应用 NO_PROXY 域（见 ``get_httpx_proxy_config``
        的 NOTE(lazy)），会让「直连域名配置」对显式传代理的调用点失效。此处按目标 host
        显式判定：命中 NO_PROXY 白名单 → ``{"trust_env": False}`` 强制直连（同时不继承
        环境代理；代价是不再发现 SSL_CERT_FILE 等 env 覆盖，直连公共域名场景可接受）；
        未命中 → 沿用 ``get_httpx_proxy_config()``（有代理则走代理，与既有一致）。

        Args:
            hostname: 目标主机名（如 ``"www.cls.cn"``），须与请求 URL 同源。
        """
        if ProxyManager.should_bypass_proxy(hostname):
            return {"trust_env": False}
        return ProxyManager.get_httpx_proxy_config()

    @staticmethod
    def get_no_proxy_env_dict() -> dict[str, str]:
        """
        Return a dict of proxy-related env vars WITHOUT writing to os.environ.

        Includes NO_PROXY, HTTP_PROXY, HTTPS_PROXY (both upper and lower case).
        Use this to pass proxy config to subprocesses or per-client setups
        instead of polluting the global process environment.

        Returns:
            Dict like {"NO_PROXY": "...", "no_proxy": "...", "HTTP_PROXY": "...", ...}
            or empty dict if no proxy config exists at all.
        """
        result: dict[str, str] = {}

        no_proxy_str = ProxyManager.get_no_proxy_string()
        if no_proxy_str:
            result["NO_PROXY"] = no_proxy_str
            result["no_proxy"] = no_proxy_str

        http_proxy, https_proxy = ProxyManager._resolve_proxy_env()

        if http_proxy:
            result["HTTP_PROXY"] = http_proxy
            result["http_proxy"] = http_proxy
        if https_proxy:
            result["HTTPS_PROXY"] = https_proxy
            result["https_proxy"] = https_proxy

        return result

    @staticmethod
    def get_requests_proxy_config() -> dict | None:
        """返回适用于 requests 库的代理配置。

        requests 的 ``proxies`` dict 仅按 scheme 键（``http``/``https``/``all``）
        匹配代理，``no_proxy`` 键不参与直连判定（UTILS-B-03：旧实现混入该键会让
        调用方误以为 NO_PROXY 白名单生效，实际白名单域名仍会静默走代理），故本
        方法只返回 scheme 代理键。

        NO_PROXY 直连语义的两种正确用法：
        1. 环境变量路径：requests 默认 ``trust_env=True``，会自行读取进程环境中
           的 ``NO_PROXY``（``litellm_env_context()`` 会临时写入进程环境；
           ``get_no_proxy_env_dict()`` 仅返回 env dict，须由调用方写入
           ``os.environ`` 或传给子进程后才能被 requests 读到）；
        2. 调用方自行判定：对具体请求目标主机调用
           ``ProxyManager.should_bypass_proxy(hostname)`` 决定是否携带本配置，
           直连主机不要传 proxies。

        Returns:
            ``{"proxies": {"http": ..., "https": ...}}``（仅含环境中实际存在的
            scheme 代理键），无代理时返回 None。
        """
        http_proxy, https_proxy = ProxyManager._resolve_proxy_env()

        if not http_proxy and not https_proxy:
            return None

        # 仅 scheme 代理键；NO_PROXY 语义不经此 dict 传递（见 docstring）
        proxies: dict[str, str] = {}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy

        return {"proxies": proxies}

    @staticmethod
    @contextmanager
    def litellm_env_context() -> Generator[None]:
        """
        Context manager that temporarily sets proxy env vars in os.environ
        for libraries that read proxy config from env (e.g. litellm).

        Concurrency model (RU-F01):
        - 进程环境是全局资源，且 ``with`` 内部会跨 ``await``。因此在 enter/exit
          的**同步短临界区**内维护活动作用域计数与唯一原始快照：
          首个进入者采集六个代理键的存在性/值并写入策略，最后一个退出者
          按快照恢复（值相同也恢复存在性）；期间任何其他协程/线程进入只增加
          引用计数，不重写环境。
        - 不跨 ``await`` 持锁：锁只在 enter/exit 的同步段持有，事件循环不会被阻塞。
        - 同进程不同事件循环/不同线程共享同一份计数与快照，因此不存在
          “loop-local 锁各自为政”或线程身份重入导致的快照覆盖。
        - 运行中 ``reapply_proxy_policy`` 的固定语义：仅刷新缓存策略，不修改
          已生效的活动环境；新策略从下一个作用域（计数归零后重新进入）开始生效。

        Restores the original env vars on exit, avoiding process-wide pollution.

        Usage:
            with ProxyManager.litellm_env_context():
                result = await litellm.acompletion(...)
        """
        # 策略计算在锁外完成（可能读取配置/磁盘），避免把慢操作放进临界区。
        env_dict = ProxyManager.get_no_proxy_env_dict()

        with ProxyManager._env_lock:
            if ProxyManager._env_scope_depth == 0:
                # 唯一原始快照：None 表示该键原本不存在（区别于存在但为空串）。
                ProxyManager._env_original = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
                os.environ.update(env_dict)
            ProxyManager._env_scope_depth += 1

        try:
            yield
        finally:
            # finally 在 CancelledError 传播时同样执行；恢复段无 await，不会被二次取消打断。
            with ProxyManager._env_lock:
                ProxyManager._env_scope_depth = max(0, ProxyManager._env_scope_depth - 1)
                if ProxyManager._env_scope_depth == 0 and ProxyManager._env_original is not None:
                    for key, original_value in ProxyManager._env_original.items():
                        if original_value is None:
                            os.environ.pop(key, None)
                        else:
                            os.environ[key] = original_value
                    ProxyManager._env_original = None
