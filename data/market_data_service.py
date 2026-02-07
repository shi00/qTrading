"""
MarketDataService - 市场数据后台服务

负责定时获取市场概览数据（指数、北向资金、热点概念），
UI 只从内存缓存读取，类似 NewsSubscriptionService 架构。
"""
import asyncio
import datetime
import logging

from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.news_fetcher import NewsFetcher
from data.cache_manager import CacheManager
from utils.thread_pool import ThreadPoolManager, TaskType
from ui.i18n import I18n

logger = logging.getLogger(__name__)


class MarketDataService:
    """
    后台服务：定时获取市场概览数据并缓存。
    单例模式，确保全局只有一个实例。
    """
    _instance = None
    
    # 刷新间隔（秒）
    # 指数配置：代码 -> I18n Key
    INDICES_CONFIG = [
        ('000001.SH', 'home_index_sh'),
        ('399001.SZ', 'home_index_sz'),
        ('399006.SZ', 'home_index_cyb')
    ]
    
    HOT_CONCEPTS_LIMIT = 8

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.api = TushareClient()
        self.cache = CacheManager()
        self._running = False
        self._task = None
        self._cached_data = None  # 内存缓存
        self.on_update = None     # 数据更新回调（通知 UI 刷新）
        self._initialized = True
    
    def start(self, on_update=None):
        """
        启动服务。
        
        Args:
            on_update: 数据更新回调（用于通知 UI 刷新）
        """
        if self._running:
            return
        
        if on_update:
            self.on_update = on_update
        
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("[MarketDataService] Started market data polling service")
    
    def stop(self):
        """停止服务并重置状态"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        
        # 清理状态
        self._cached_data = None
        self.on_update = None
        
        logger.info("[MarketDataService] Stopped market data polling service")
    
    def get_cached_data(self) -> dict:
        """
        获取缓存的市场数据。
        
        Returns:
            dict: 包含 date, indices, hsgt, hot_concepts 的字典，
                  如果缓存为空则返回 None
        """
        return self._cached_data
    
    async def _poll_loop(self):
        """主轮询循环"""
        # 首次立即执行一次
        await self._safe_fetch()
        
        while self._running:
            try:
                # 动态获取配置的刷新间隔（默认30秒）
                interval = ConfigHandler.get_market_data_poll_interval()
                await asyncio.sleep(interval)
                if self._running:
                    await self._safe_fetch()
            except asyncio.CancelledError:
                break
    
    async def _safe_fetch(self):
        """安全获取数据（带异常处理）"""
        if not self._running:
            return
        
        try:
            await self._fetch_market_data()
        except Exception as e:
            logger.error(f"[MarketDataService] Error fetching market data: {e}")
    
    async def _fetch_market_data(self):
        """获取市场概览数据"""
        logger.debug("[MarketDataService] Fetching market data...")
        
        try:
            now = datetime.datetime.now()
            today_str = now.strftime('%Y%m%d')
            start_str = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')
            
            # 确保交易日历已缓存
            await self._ensure_trade_cal(today_str)
            
            # 获取最近交易日
            cache_df = await self.cache.get_trade_cal(
                start_date=start_str, end_date=today_str, is_open=1
            )
            date = today_str
            if cache_df is not None and not cache_df.empty:
                date = sorted(cache_df['cal_date'].tolist())[-1]
            
            # 构建所有任务
            tasks = [self._get_index(code, key, date) for code, key in self.INDICES_CONFIG]
            tasks.append(self._get_hsgt(date))
            tasks.append(NewsFetcher.get_hot_concepts(limit=self.HOT_CONCEPTS_LIMIT))
            
            # 并行执行
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 处理指数结果 (前N个任务)
            indices = []
            for i, (code, key) in enumerate(self.INDICES_CONFIG):
                res = results[i]
                if isinstance(res, Exception):
                    logger.warning(f"[MarketDataService] Index {code} fetch failed: {res}")
                    indices.append(self._get_empty_index_data(key))
                else:
                    indices.append(res)
            
            # 处理北向资金 (倒数第2个)
            hsgt_res = results[-2]
            hsgt = hsgt_res if not isinstance(hsgt_res, Exception) else self._get_empty_hsgt_data()
            
            # 处理热门概念 (最后1个)
            hot_res = results[-1]
            hot_concepts = hot_res if not isinstance(hot_res, Exception) else []
            
            # 更新缓存
            self._cached_data = {
                'date': date,
                'indices': indices,
                'hsgt': hsgt,
                'hot_concepts': hot_concepts
            }
            
            logger.debug(f"[MarketDataService] Market data updated for date {date}")
            
            # 通知 UI 更新
            if self.on_update:
                self.on_update()
                
        except Exception as e:
            logger.error(f"[MarketDataService] Failed to fetch market data: {e}")
    
    async def _ensure_trade_cal(self, end_date: str):
        """确保交易日历已缓存"""
        try:
            cached = await self.cache.get_trade_cal(
                start_date=(datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y%m%d'),
                end_date=end_date
            )
            if cached is None or cached.empty:
                df = await ThreadPoolManager().run_async(
                    TaskType.IO, self.api.get_trade_cal,
                    start_date='20240101', end_date=end_date
                )
                if df is not None and not df.empty:
                    await self.cache.save_trade_cal(df)
        except Exception as e:
            logger.warning(f"[MarketDataService] Trade calendar check failed: {e}")
    
    async def _get_index(self, code: str, name_key: str, date: str) -> dict:
        """获取指数数据"""
        df = await ThreadPoolManager().run_async(
            TaskType.IO, self.api.get_index_daily, ts_code=code, trade_date=date
        )
        if df is not None and not df.empty:
            row = df.iloc[0]
            c = row.get('pct_chg', 0) or 0
            v = row.get('close', 0) or 0
            return {
                'name': I18n.get(name_key),
                'value': f"{v:.2f}",
                'change': f"{c:+.2f}%",
                'color': 'red' if c >= 0 else 'green'
            }
        return self._get_empty_index_data(name_key)

    def _get_empty_index_data(self, name_key: str) -> dict:
        return {'name': I18n.get(name_key), 'value': '-', 'change': '-', 'color': 'grey'}
    
    async def _get_hsgt(self, date: str) -> dict:
        """获取北向资金数据"""
        df = await ThreadPoolManager().run_async(
            TaskType.IO, self.api.get_moneyflow_hsgt, trade_date=date
        )
        if df is not None and not df.empty:
            val = float(df.iloc[0]['north_money'] or 0)
            return {
                'name': I18n.get('home_northbound'),
                'value': f"{val/100:.2f}{I18n.get('unit_yi')}" if abs(val) > 100 else f"{val:.0f}{I18n.get('unit_wanshou')}",
                'sub': I18n.get('home_inflow') if val > 0 else I18n.get('home_outflow'),
                'color': 'red' if val > 0 else 'green' if val < 0 else 'grey'
            }
        return self._get_empty_hsgt_data()

    def _get_empty_hsgt_data(self) -> dict:
        return {'name': I18n.get('home_northbound'), 'value': '-', 'sub': '-', 'color': 'grey'}
