import asyncio
import logging
import akshare as ak
import pandas as pd
from datetime import datetime
from utils.config_handler import ConfigHandler
from data.cache_manager import CacheManager
from data.ai_client import AIClient

logger = logging.getLogger(__name__)

class NewsSubscriptionService:
    """
    Background service to poll real-time news.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.cache = CacheManager()
        self.ai_client = AIClient()
        self._running = False
        self._task = None
        self._last_news_time = None
        self.on_news_callback = None # Function to call when news arrives (e.g. show snackbar)
        self._initialized = True
        
    def start(self, callback=None):
        """Start the subscription service"""
        if self._running:
            return
            
        self.on_news_callback = callback
        
        # Check config
        enabled = ConfigHandler.get_config('enable_news_alerts', True)
        if not enabled:
            logger.info("[NewsService] News alerts disabled in config.")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[NewsService] Started news polling service 🚀")
        
    def stop(self):
        """Stop the service"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("[NewsService] Stopped news polling service")

    async def _poll_loop(self):
        """Main polling loop"""
        base_interval = ConfigHandler.get_config('news_poll_interval', 60) # Default 60s
        retry_count = 0
        max_retries = 5
        
        while self._running:
            try:
                await self._fetch_and_notify()
                # Success: Reset retries
                if retry_count > 0:
                    logger.info("[NewsService] Connection restored.")
                    retry_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                retry_count += 1
                backoff = min(base_interval * (2 ** (retry_count - 1)), 600) # Max 10 mins
                logger.error(f"[NewsService] Error in poll loop: {e}. Retrying in {backoff}s (Attempt {retry_count})")
                
                # Sleep the backoff period (check running often)
                # Simple sleep for now
                try:
                    await asyncio.sleep(backoff)
                except asyncio.CancelledError:
                    break
                continue # Skip normal interval sleep
            
            await asyncio.sleep(base_interval)

    async def _fetch_and_notify(self):
        """Fetch latest news and trigger alert if new"""
        try:
            loop = asyncio.get_running_loop()
            
            # Use run_in_executor for blocking AKShare call
            # SWITCH TO CAILIANSHE (CLS) for Real-time Telegrpah (Fastest in China)
            df = await loop.run_in_executor(None, lambda: ak.stock_info_global_cls())
            
            if df is None or df.empty:
                return

            # Columns usually: [时间, 内容] or derived ones.
            # Sina structure: index is time? Or specific cols.
            # Based on test, we assume there is time/content.
            # Let's peek columns from previous tests: likely '时间', '内容'.
            
            # Sort by time desc
            # df usually comes sorted, but ensure it.
            # We need to parse time to track "latest".
            
            # Simple dedup: compare top item content/time with last seen
            # Assuming row 0 is latest
            latest_row = df.iloc[0]
            current_news_content = str(latest_row.get('content', '') or latest_row.get('内容', ''))
            current_news_time = str(latest_row.get('time', '') or latest_row.get('时间', ''))
            
            # Setup initial state
            if self._last_news_time is None:
                self._last_news_time = current_news_time
                self._last_news_content = current_news_content
                return # Don't alert on startup, just sync
                
            # Check for new news
            if current_news_time != self._last_news_time or current_news_content != self._last_news_content:
                # Found new news!
                # We might want to filter keywords here (e.g. "A股", "利好", watched stocks)
                # For now, let's push everything or simple filter.
                
                # Update state
                self._last_news_time = current_news_time
                self._last_news_content = current_news_content
                
                # Smart Tagging Logic
                clean_content = current_news_content.strip()
                tag = ""
                
                # Try AI Classification first
                ai_result = await self.ai_client.classify_news(clean_content)
                if ai_result:
                    # AI Success
                    emoji = ai_result.get('emoji', '📰')
                    category = ai_result.get('category', 'News')
                    tag = f"【{emoji} {category}】"
                else:
                    # Fallback to Rule-based
                    if any(k in clean_content for k in ['央行', '证监会', '国务院', '财政部', '政策', '立案', '违规']):
                        tag = "【🏛️ Policy】"
                    elif any(k in clean_content for k in ['美联储', '欧佩克', '纳斯达克', '汇率', '外盘', '美元']):
                        tag = "【🌍 Global】"
                    elif any(k in clean_content for k in ['GDP', 'CPI', 'PPI', 'PMI', '社融', '通胀']):
                        tag = "【📈 Macro】"
                
                display_msg = f"{tag} {clean_content}" if tag else clean_content
                
                logger.info(f"[NewsService] 🔔 New Alert: {display_msg[:30]}...")
                
                if self.on_news_callback:
                    # Notify UI
                    self.on_news_callback(display_msg)
                    
                # PERSISTENCE: Save to DB for AI
                await self.cache.save_market_news({
                    'content': clean_content,
                    'tags': tag,
                    'publish_time': current_news_time,
                    'source': 'CLS'
                })
                    
        except Exception as e:
            logger.warning(f"[NewsService] Poll failed: {e}")
