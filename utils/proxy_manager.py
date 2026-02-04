import logging
import os
from concurrent.futures import ThreadPoolExecutor

import requests

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
        Auto-optimizes network settings at startup.
        Strategy:
        1. Identify system proxy settings.
        2. Load whitelist from Config (user_settings.json).
        3. For each critical domestic domain, TEST direct connectivity.
        4. If Direct works -> Add to NO_PROXY (Performance + Stability).
        5. If Direct fails -> Do nothing (Allow System Proxy to handle it).
        """
        logger.info("[ProxyManager] Starting network optimization...")

        # Current Proxy State
        # Note: Even if no ENV proxy is set, Windows might have system proxy (WinINET).
        # We should ALWAYS test direct connectivity and allow whitelisting to override potential system proxies.

        # Prepare list of domains to whitelist
        # We start with existing NO_PROXY
        current_no_proxy = os.environ.get("NO_PROXY", "")
        if not current_no_proxy:
            current_no_proxy = os.environ.get("no_proxy", "")

        final_domains = set(
            [d.strip() for d in current_no_proxy.split(",") if d.strip()]) if current_no_proxy else set()

        # Load User Configured Domains ONLY
        target_domains = ConfigHandler.get_proxy_domains()

        # Safety Filter: Ensure all items are strings (e.g. if user put int in json)
        # Also strip them here to be safe
        target_domains = list(set([d.strip() for d in target_domains if isinstance(d, str) and d.strip()]))

        if not target_domains:
            logger.info("[ProxyManager] No proxy domains configured in user_settings.json. Skipping optimization.")
            return

        logger.info(f"[ProxyManager] Testing connectivity for {len(target_domains)} domains from config...")

        # Test Function
        def check_direct_access(domain):
            # Candidates to test: original domain, and maybe www if it's missing
            candidates = [domain]
            if not domain.startswith("www."):
                candidates.append(f"www.{domain}")

            session = requests.Session()
            session.trust_env = False  # Ignore env proxies

            for candidate in candidates:
                for scheme in ["https", "http"]:
                    url = f"{scheme}://{candidate}"
                    try:
                        resp = session.head(url, timeout=3)
                        if resp.status_code < 500:
                            return True
                    except Exception:
                        continue
            return False

        # Use ThreadPool to check fast
        logger.info(f"[ProxyManager] Testing direct connectivity for {len(target_domains)} domestic domains...")

        from concurrent.futures import as_completed
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_domain = {executor.submit(check_direct_access, d): d for d in target_domains}

            for future in as_completed(future_to_domain):
                domain = future_to_domain[future]
                try:
                    is_direct_ok = future.result()
                    if is_direct_ok:
                        final_domains.add(domain)
                        logger.info(f"[ProxyManager] Domain {domain} - Direct Access OK -> Whitelisted.")
                    else:
                        logger.warning(f"[ProxyManager] Domain {domain} - Direct Access FAILED -> Will use Proxy.")
                except Exception as e:
                    logger.error(f"[ProxyManager] Error checking {domain}: {e}")

        # Apply updates
        if final_domains:
            # Filter empty strings
            valid_domains = [d for d in final_domains if d]
            new_no_proxy = ",".join(valid_domains)

            os.environ["NO_PROXY"] = new_no_proxy
            os.environ["no_proxy"] = new_no_proxy
            logger.info(f"[ProxyManager] Optimization complete. NO_PROXY={new_no_proxy}")
        else:
            logger.info("[ProxyManager] Optimization complete. No changes.")

    @staticmethod
    def verify_connectivity():
        """
        Diagnose network status for global and domestic access.
        Returns dict with status.
        """
        results = {"domestic": False, "global": False,
                   "proxy_used": bool(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))}

        # Test Domestic (EastMoney)
        try:
            requests.get("https://www.eastmoney.com", timeout=5)
            results["domestic"] = True
        except Exception as e:
            logger.warning(f"Domestic connectivity check failed: {e}")

        # Test Global (Google/Github) - Optional, might fail in China
        # We can check if Proxy is working
        if results["proxy_used"]:
            try:
                requests.get("https://www.google.com", timeout=5)
                results["global"] = True
            except:
                pass

        return results
