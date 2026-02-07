import asyncio
import logging

from datetime import datetime
from utils.config_handler import ConfigHandler
from data.cache_manager import CacheManager
from services.ai_service import AIService
from ui.i18n import I18n

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
        self.ai_client = AIService()
        self._running = False
        self._task = None
        self._last_news_time = None
        self._last_news_content = None
        self.on_news_callback = None  # 弹窗通知回调 (受 enable_news_alerts 控制)
        self.on_news_update = None     # 数据更新回调 (始终触发，用于UI刷新)
        self._current_fetch_task = None
        self._initialized = True
        
    def start(self, callback=None, on_update=None):
        """
        Start the subscription service.
        
        Args:
            callback: 弹窗通知回调（受 enable_news_alerts 配置控制）
            on_update: 数据更新回调（始终触发，用于 UI 刷新）
        """
        if self._running:
            return
            
        if callback:
            self.on_news_callback = callback
        if on_update:
            self.on_news_update = on_update

        # 始终启动服务进行数据同步（enable_news_alerts 只控制弹窗推送）
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[NewsService] Started news polling service [STARTED]")
        
    def stop(self):
        """Stop the service and reset state"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
            
        # Also cancel the detached fetch task if running
        if self._current_fetch_task and not self._current_fetch_task.done():
            self._current_fetch_task.cancel()
            self._current_fetch_task = None
        
        # 重置状态，确保下次 start() 时能正确执行首次同步
        self._last_news_time = None
        self._last_news_content = None
        
        # 清理回调引用，防止内存泄漏
        self.on_news_callback = None
        self.on_news_update = None
            
        logger.info("[NewsService] Stopped news polling service")

    async def _poll_loop(self):
        """Main polling loop"""
        
        while self._running:
            # Read config dynamically
            base_interval = ConfigHandler.get_config('news_poll_interval', 60)
            
            # Fire and forget (but track it for cleanup). 
            # We use create_task so the sleep interval is independent of fetch duration (strict interval).
            self._current_fetch_task = asyncio.create_task(self._safe_fetch_task())
            
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
        logger.debug("[NewsService] Polling for latest news...")
        try:
            # Use centralized NewsFetcher (which handles proxies and data normalization)
            from data.news_fetcher import NewsFetcher
            from data.cache_manager import CacheManager
            
            # 首次启动时批量拉取新闻（确保首页有数据显示）
            is_initial_sync = self._last_news_time is None
            fetch_limit = 20 if is_initial_sync else 1
            
            news_list = await NewsFetcher.get_latest_global_news(limit=fetch_limit)
            
            if not news_list:
                return
            
            # 首次启动：批量保存所有新闻到数据库（无AI分类，快速同步）
            if is_initial_sync:
                logger.info(f"[NewsService] Initial sync: saving {len(news_list)} news items")
                for item in news_list:
                    normalized = CacheManager.normalize_news_item(item, default_source='CLS')
                    await self.cache.save_market_news(normalized)
                
                # 设置初始状态（用最新一条作为基准）
                latest_item = news_list[0]
                self._last_news_time = latest_item.get('time', '')
                self._last_news_content = latest_item.get('content', '')
                logger.info("[NewsService] Initial sync complete, monitoring for new news...")
                
                # 通知 UI 数据已就绪（首次同步完成）
                if self.on_news_update:
                    self.on_news_update()
                return
            
            # 后续轮询：只检测最新1条，用于判断是否有新消息
            latest_item = news_list[0]
            current_news_content = latest_item.get('content', '')
            current_news_time = latest_item.get('time', '')
                
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
                    
                    # Map AI category to I18n key if possible
                    i18n_key = f"tag_{category.lower()}"
                    localized_category = I18n.get(i18n_key)
                    
                    # If key missing or matches fallback (English), use it, otherwise use original if not found
                    if localized_category == i18n_key: 
                        localized_category = category # Fallback to original if I18n key missing
                        
                    tag = f"【{emoji} {localized_category}】"
                else:
                    # Fallback to Rule-based
                    if any(k in clean_content for k in ['央行', '证监会', '国务院', '财政部', '政策', '立案', '违规']):
                        tag = f"【🏛️ {I18n.get('tag_policy')}】"
                    elif any(k in clean_content for k in ['美联储', '欧佩克', '纳斯达克', '汇率', '外盘', '美元']):
                         tag = f"【🌍 {I18n.get('tag_global')}】"
                    elif any(k in clean_content for k in ['GDP', 'CPI', 'PPI', 'PMI', '社融', '通胀']):
                         tag = f"【📈 {I18n.get('tag_macro')}】"
                
                display_msg = f"{tag} {clean_content}" if tag else clean_content
                
                logger.info(f"[NewsService] [ALERT] New Alert: {display_msg[:30]}...")
                
                # 弹窗通知（受 enable_news_alerts 配置控制）
                enable_alerts = ConfigHandler.get_config('enable_news_alerts', True)
                if enable_alerts and self.on_news_callback:
                    self.on_news_callback(display_msg)
                    
                # PERSISTENCE: Save to DB for AI（使用公共方法标准化字段）
                from data.cache_manager import CacheManager
                normalized = CacheManager.normalize_news_item({
                    'content': clean_content,
                    'tags': tag,
                    'publish_time': current_news_time,
                    'source': 'CLS'
                })
                await self.cache.save_market_news(normalized)
                
                # 通知 UI 有新数据（始终触发）
                if self.on_news_update:
                    self.on_news_update()
                    
        except Exception as e:
            logger.warning(f"[NewsService] Poll failed: {e}")
