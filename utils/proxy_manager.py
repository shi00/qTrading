import os
import logging
import requests
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

class ProxyManager:
    """
    Manages network proxy settings to ensure stability for both
    Domestic (Direct) and International (Proxy) traffic.
    """
    
    # Default critical domains (Fallback if user config is empty)
    DEFAULT_DOMAINS = [
        "eastmoney.com",
        "sina.com.cn",
        "10jqka.com.cn",
        "sse.com.cn",
        "szse.cn",
        "cninfo.com.cn",
        "push2.eastmoney.com",
        "push2his.eastmoney.com",
        "tushare.pro",
        "waditu.com",
        "cls.cn"
    ]

    @staticmethod
    def apply_smart_proxy_policy():
        """
        Auto-optimizes network settings at startup.
        Strategy:
        1. Identify system proxy settings.
        2. Load whitelist from Config (user_settings.json) OR use Defaults.
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
            
        final_domains = set(current_no_proxy.split(",")) if current_no_proxy else set()
        
        # Load User Configured Domains
        user_domains = ConfigHandler.get_proxy_domains()
        
        # Merge Strategy: Defaults + User Config
        # We rely on the connectivity test to filter out bad domains, so merging is safe.
        # This allows users to just add extra domains without copying the whole default list.
        merged_list = ProxyManager.DEFAULT_DOMAINS + user_domains
        # Safety Filter: Ensure all items are strings (e.g. if user put int in json)
        target_domains = list(set([d for d in merged_list if isinstance(d, str) and d.strip()]))
        
        logger.info(f"[ProxyManager] Testing connectivity for {len(target_domains)} domains (Defaults + {len(user_domains)} User Custom)...")
        
        # Test Function
        def check_direct_access(domain):
            try:
                # Force strictly no proxy for this test request
                session = requests.Session()
                session.trust_env = False # Ignore env proxies
                resp = session.head(f"https://www.{domain}", timeout=3)
                return resp.status_code < 500
            except Exception:
                # Try http if https fails
                try:
                    session = requests.Session()
                    session.trust_env = False
                    resp = session.head(f"http://www.{domain}", timeout=3)
                    return resp.status_code < 500
                except:
                    return False

        # Use ThreadPool to check fast
        logger.info(f"[ProxyManager] Testing direct connectivity for {len(target_domains)} domestic domains...")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_domain = {executor.submit(check_direct_access, d): d for d in target_domains}
            
            for future in future_to_domain:
                domain = future_to_domain[future]
                is_direct_ok = future.result()
                
                if is_direct_ok:
                    final_domains.add(domain)
                    # Also add www. subdomain just in case, though suffix match usually handles it
                    # requests NO_PROXY matches suffixes usually.
                    logger.info(f"[ProxyManager] Domain {domain} - Direct Access OK -> Whitelisted.")
                else:
                    logger.warning(f"[ProxyManager] Domain {domain} - Direct Access FAILED -> Will use Proxy.")

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
        results = {"domestic": False, "global": False, "proxy_used": bool(os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy"))}
        
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

    @staticmethod
    @contextmanager
    def bypass_proxy_for_domestic(domain_substring="eastmoney.com"):
        """
        Temporarily add domain to NO_PROXY to bypass system proxy for domestic APIs.
        This helps when users have global proxy that fails for domestic requests.
        Usage: with ProxyManager.bypass_proxy_for_domestic(): ...
        """
        original_no_proxy = os.environ.get("NO_PROXY", "")
        original_no_proxy_lower = os.environ.get("no_proxy", "")
        
        # Check if already bypassed (simple check)
        if domain_substring in original_no_proxy or domain_substring in original_no_proxy_lower:
            yield
            return

        # Add to NO_PROXY
        # Use lowercase 'no_proxy' as requests/urllib usually checks both or specific
        new_no_proxy = f"{original_no_proxy},{domain_substring}" if original_no_proxy else domain_substring
        os.environ["NO_PROXY"] = new_no_proxy
        os.environ["no_proxy"] = new_no_proxy # Set both for maximum compatibility
        
        try:
            yield
        finally:
            # Restore
            if original_no_proxy:
                os.environ["NO_PROXY"] = original_no_proxy
            else:
                os.environ.pop("NO_PROXY", None)
                
            if original_no_proxy_lower:
                os.environ["no_proxy"] = original_no_proxy_lower
            else:
                os.environ.pop("no_proxy", None)
