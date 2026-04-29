import logging
import os

from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class ProxyManager:
    """
    Manages network proxy settings to ensure stability for both
    Domestic (Direct) and International (Proxy) traffic.
    """

    @staticmethod
    def apply_smart_proxy_policy():
        """
        Applies network proxy settings at startup.
        Strategy:
        1. Identify system proxy settings (inherited from ENV).
        2. Load whitelist from Config (user_settings.json).
        3. Merge Config whitelist with existing NO_PROXY.
        4. Apply updated NO_PROXY to environment.
        """
        logger.info("[ProxyManager] Applying proxy configuration...")

        # Current Proxy State
        # Note: Even if no ENV proxy is set, Windows might have system proxy (WinINET).
        # We append user configured domains to NO_PROXY to ensure they bypass any potential proxy.

        # Prepare list of domains to whitelist
        # S2-2 fix: Union both NO_PROXY and no_proxy environment variables
        no_proxy_upper = os.environ.get("NO_PROXY", "")
        no_proxy_lower = os.environ.get("no_proxy", "")

        final_domains: set[str] = set()

        # Add domains from NO_PROXY
        if no_proxy_upper:
            final_domains.update([d.strip() for d in no_proxy_upper.split(",") if d.strip()])

        # Add domains from no_proxy (case-insensitive merge)
        if no_proxy_lower:
            final_domains.update([d.strip() for d in no_proxy_lower.split(",") if d.strip()])

        # Load User Configured Domains ONLY
        target_domains = ConfigHandler.get_no_proxy_domains()

        # Safety Filter: Ensure all items are strings
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

        # Apply updates
        if final_domains:
            # Filter empty strings
            valid_domains = [d for d in final_domains if d]
            new_no_proxy = ",".join(valid_domains)

            os.environ["NO_PROXY"] = new_no_proxy
            os.environ["no_proxy"] = new_no_proxy
            logger.info(
                f"[ProxyManager] Configuration applied. NO_PROXY={new_no_proxy}",
            )
        else:
            logger.info("[ProxyManager] Configuration applied. No changes.")

    @staticmethod
    def reapply_proxy_policy():
        """
        S2-3 fix: Re-apply proxy policy at runtime when no_proxy_domains config changes.
        This should be called after ConfigHandler settings are updated.
        """
        logger.info("[ProxyManager] Re-applying proxy configuration (runtime update)...")
        ProxyManager.apply_smart_proxy_policy()
