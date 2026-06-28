import logging
import os
import threading
from collections.abc import Generator
from contextlib import contextmanager

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


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

    _no_proxy_domains: set[str] = set()
    _initialized: bool = False
    _original_no_proxy: set[str] | None = None
    _env_written: bool = False
    _env_lock: threading.RLock = threading.RLock()
    _config_lock: threading.RLock = threading.RLock()  # Lock for config mutation (domains, initialized)

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
        """
        Return proxy configuration suitable for httpx clients.

        Returns:
            Dict with 'proxies' key for httpx, or empty dict if no proxy needed.
        """
        http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", ""))
        https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

        if not http_proxy and not https_proxy:
            return {}

        proxies = {}
        if http_proxy:
            proxies["http://"] = http_proxy
        if https_proxy:
            proxies["https://"] = https_proxy

        return {"proxies": proxies} if proxies else {}

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

        http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", ""))
        https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

        if http_proxy:
            result["HTTP_PROXY"] = http_proxy
            result["http_proxy"] = http_proxy
        if https_proxy:
            result["HTTPS_PROXY"] = https_proxy
            result["https_proxy"] = https_proxy

        return result

    @staticmethod
    def get_requests_proxy_config() -> dict | None:
        """
        Return proxy configuration suitable for requests library.

        Returns:
            Dict with 'proxies' key for requests, or None if no proxy needed.
        """
        http_proxy = os.environ.get("HTTP_PROXY", os.environ.get("http_proxy", ""))
        https_proxy = os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", ""))

        if not http_proxy and not https_proxy:
            return None

        proxies = {"no_proxy": ProxyManager.get_no_proxy_string()}
        if http_proxy:
            proxies["http"] = http_proxy
        if https_proxy:
            proxies["https"] = https_proxy

        return {"proxies": proxies}

    @staticmethod
    @contextmanager
    def litellm_env_context() -> Generator[None]:
        """
        Context manager that temporarily sets NO_PROXY in os.environ
        for libraries that read proxy config from env (e.g. litellm).

        Thread-safe: uses a class-level lock to prevent concurrent coroutines
        or threads from overwriting each other's env snapshot.

        Restores the original env vars on exit, avoiding process-wide pollution.

        Usage:
            with ProxyManager.litellm_env_context():
                result = await litellm.acompletion(...)
        """
        with ProxyManager._env_lock:
            env_dict = ProxyManager.get_no_proxy_env_dict()
            saved: dict[str, str | None] = {}

            for key in ("NO_PROXY", "no_proxy", "HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"):
                saved[key] = os.environ.get(key)

            try:
                os.environ.update(env_dict)
                yield
            finally:
                for key, original_value in saved.items():
                    if original_value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = original_value
