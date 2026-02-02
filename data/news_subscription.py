import asyncio
import logging

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
        logger.info("[NewsService] Started news polling service [STARTED]")
        
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
        
        while self._running:
            # Fire and forget (or track if needed). 
            # We use create_task so the sleep interval is independent of fetch duration (strict interval).
            task = asyncio.create_task(self._safe_fetch_task())
            
            # Simple error handling for the loop itself (unlikely to fail here)
            try:
                await asyncio.sleep(base_interval)
            except asyncio.CancelledError:
                break

    async def _safe_fetch_task(self):
        """Wrapper to handle errors within the independent task"""
        if not self._running: 
            return

        try:
            await self._fetch_and_notify()
        except Exception as e:
            logger.error(f"[NewsService] Error in background fetch task: {e}")
            # Optional: Implement retry logic here if needed, but for periodic polling, 
            # just failing and waiting for next interval is often cleaner.

    async def _fetch_and_notify(self):
        """Fetch latest news and trigger alert if new"""
        try:
            # Use centralized NewsFetcher (which handles proxies and data normalization)
            from data.news_fetcher import NewsFetcher
            
            # Fetch latest 1 news item
            news_list = await NewsFetcher.get_latest_global_news(limit=1)
            
            if not news_list:
                return

            latest_item = news_list[0]
            current_news_content = latest_item.get('content', '')
            current_news_time = latest_item.get('time', '')
            
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
                    emoji = ai_result.get('emoji', '[NEWS]')
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
                
                logger.info(f"[NewsService] [ALERT] New Alert: {display_msg[:30]}...")
                
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
