import logging
import os

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class ProxyManager:
    """
    Manages network proxy settings to ensure stability for both
    Domestic (Direct) and International (Proxy) traffic.

    Design decisions:
    - Writes NO_PROXY to os.environ so that third-party libraries
      (litellm/httpx/requests) can respect the proxy bypass rules.
    - Does NOT log the full domain list to prevent enterprise network
      topology leakage (S-P0-2 fix).
    - Also caches domains in a class-level store for programmatic queries.
    - Preserves the ORIGINAL env NO_PROXY (before any ProxyManager writes)
      so that reapply can correctly remove domains that were deleted from config.
    """

    _no_proxy_domains: set[str] = set()
    _initialized: bool = False
    _original_no_proxy: set[str] | None = None

    @staticmethod
    def apply_smart_proxy_policy():
        """
        Computes and applies the NO_PROXY domain list at startup.

        Strategy:
        1. On first call, snapshot the original NO_PROXY from env (before we modify it).
        2. Load whitelist from Config (user_settings.json).
        3. Merge original NO_PROXY with config whitelist.
        4. Apply updated NO_PROXY to environment (required for litellm/httpx).
        5. Cache the result for programmatic queries.
        """
        logger.info("[ProxyManager] Computing proxy configuration...")

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
                f"[ProxyManager] Snapshotted {len(original_domains)} original NO_PROXY domains from env.",
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
                f"[ProxyManager] Adding {len(target_domains)} domains to NO_PROXY whitelist.",
            )
            final_domains.update(target_domains)

        ProxyManager._no_proxy_domains = final_domains
        ProxyManager._initialized = True

        if final_domains:
            valid_domains = [d for d in final_domains if d]
            new_no_proxy = ",".join(valid_domains)

            os.environ["NO_PROXY"] = new_no_proxy
            os.environ["no_proxy"] = new_no_proxy

            logger.info(
                f"[ProxyManager] Configuration applied. {len(valid_domains)} domains in NO_PROXY.",
            )
        else:
            os.environ.pop("NO_PROXY", None)
            os.environ.pop("no_proxy", None)
            logger.info("[ProxyManager] Configuration applied. NO_PROXY cleared.")

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
        if not ProxyManager._initialized:
            ProxyManager.apply_smart_proxy_policy()
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
