import os
import logging
import requests
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class ProxyManager:
    """
    Manages network proxy settings to ensure stability for both
    Domestic (Direct) and International (Proxy) traffic.
    """
    
    # Critical domains that should usually bypass proxy in China
    DOMESTIC_DOMAINS = [
        "eastmoney.com",
        "sina.com.cn",
        "10jqka.com.cn",
        "sse.com.cn",
        "szse.cn",
        "cninfo.com.cn",
        "push2.eastmoney.com",
        "push2his.eastmoney.com",
        "cls.cn"
    ]

    @staticmethod
    def apply_smart_proxy_policy():
        """
        Auto-optimizes network settings at startup.
        Strategy:
        1. Identify system proxy settings.
        2. For each critical domestic domain, TEST direct connectivity.
        3. If Direct works -> Add to NO_PROXY (Performance + Stability).
        4. If Direct fails -> Do nothing (Allow System Proxy to handle it).
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
        logger.info(f"[ProxyManager] Testing direct connectivity for {len(ProxyManager.DOMESTIC_DOMAINS)} domestic domains...")
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_domain = {executor.submit(check_direct_access, d): d for d in ProxyManager.DOMESTIC_DOMAINS}
            
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
