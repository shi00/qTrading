
import asyncio
import datetime
import logging
import pandas as pd
import aiosqlite
import threading

from data.cache_manager import CacheManager
from data.news_fetcher import NewsFetcher
from data.tushare_client import TushareClient
from data.constants import MAJOR_INDICES
from data.sync_strategies.base import SyncContext
from data.sync_strategies.financial import FinancialSyncStrategy
from data.sync_strategies.historical import HistoricalSyncStrategy

from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation, track_performance
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)

class DataProcessor:
    """
    Main data processing class (Refactored Facade).
    Delegates complex sync logic to specific Strategies.
    """
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            # Check if token has changed since initialization
            current_token = ConfigHandler.get_token()
            if hasattr(self, '_current_token') and current_token != self._current_token:
                self.refresh_token(current_token)
            return

        token = ConfigHandler.get_token()
        self._current_token = token
        self.api = TushareClient(token=token)
        self.cache = CacheManager()
        self._first_news_sync = True
        self._shutdown_event = asyncio.Event()
        self._cancel_event = asyncio.Event()  # Unified cancellation event

        # Initialize Context & Strategies
        self.context = SyncContext(api=self.api, cache=self.cache, config=ConfigHandler)
        self.strategies = {
            'financial': FinancialSyncStrategy(self.context),
            'historical': HistoricalSyncStrategy(self.context)
        }

        # Memory Cache for high-frequency small data
        self._trade_cal_cache = None  # Cache structure: {'start': str, 'end': str, 'df': DataFrame}

        # Concurrency Control (Cross-Loop safe) - kept for basic locking if needed
        import threading
        self._sync_lock = threading.Lock()
        self._is_syncing_basic = False

        self._initialized = True

    async def request_cancel(self):
        """
        Request cancellation of current operation.
        Called by UI when user clicks cancel or closes window.
        """
        logger.info("[DataProcessor] Cancel requested")
        self._cancel_event.set()
        self._shutdown_event.set()
        
        # Propagate to all strategies
        for name, strategy in self.strategies.items():
            try:
                await strategy.cancel()
                logger.debug(f"[DataProcessor] Cancelled strategy: {name}")
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to cancel {name}: {e}")
    
    def is_cancelled(self):
        """Check if cancellation has been requested."""
        return self._cancel_event.is_set()
    
    def clear_cancel(self):
        """Clear cancel state before starting new operation."""
        self._cancel_event.clear()
        self._shutdown_event.clear()

    def stop(self):
        """Signal all running tasks to stop"""
        logger.info("[DataProcessor] Global stop signal received.")
        self._cancel_event.set()
        self._shutdown_event.set()
            
        # Delegate cancellation to strategies
        # Note: Since this is synchronous, we verify loop existence
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                for strategy in self.strategies.values():
                     loop.create_task(strategy.cancel())
        except Exception as e:
            logger.warning(f"[DataProcessor] Error during stop propagation: {e}")

    def refresh_token(self, new_token=None):
        """Refresh API token without recreating instance"""
        if new_token is None:
            new_token = ConfigHandler.get_token()
        self._current_token = new_token
        self.api = TushareClient(token=new_token)
        # Update context
        self.context.api = self.api
        logger.info("[DataProcessor] Token refreshed")

    async def close(self):
        """Gracefully close resources"""
        self.stop()
        if self.cache:
            await self.cache.close()

    # ==========================================
    # Delegated Sync Methods
    # ==========================================

    @log_async_operation(operation_name="sync_historical")
    async def sync_historical_data(self, days=365, progress_callback=None):
        """Delegated to HistoricalSyncStrategy"""
        result = await self.strategies['historical'].run(days=days, progress_callback=progress_callback)
        return result

    @log_async_operation(operation_name="sync_financial")
    async def sync_financial_reports(self, periods=None, progress_callback=None, force=False):
        """Delegated to FinancialSyncStrategy"""
        result = await self.strategies['financial'].run(periods=periods, force=force, progress_callback=progress_callback)
        return result.added

    async def sync_comprehensive_fundamentals(self, progress_callback=None, force=False):
        """Delegated to FinancialSyncStrategy (Full Sync Mode)
        
        Returns:
            SyncResult: Full result object with status, added count, and errors
        """
        result = await self.strategies['financial'].run(force=force, progress_callback=progress_callback)
        return result

    async def repair_financial_data(self, ts_codes, progress_callback=None):
        """Delegated to FinancialSyncStrategy"""
        return await self.strategies['financial'].repair_financial_data(ts_codes, progress_callback)
        
    async def sync_daily_market_snapshot(self, trade_date=None, force=False):
        """Delegated to HistoricalSyncStrategy"""
        if trade_date is None:
            trade_date = await self.get_latest_trade_date()
        
        await self.strategies['historical'].sync_daily_market_snapshot(trade_date, force=force)
        # For compatibility with some callers expecting data, we fetch it back from cache
        return await self.get_screening_data(trade_date)

    # ==========================================
    # Core Logic (Business/Orchestration)
    # ==========================================

    @log_async_operation(operation_name="get_latest_trade_date", log_exceptions=True)
    async def get_latest_trade_date(self):
        """Get absolute latest trading date (today or previous trading day)."""
        now = datetime.datetime.now()
        if now.hour < 16:
            end_dt = now - datetime.timedelta(days=1)
        else:
            end_dt = now

        end_str = end_dt.strftime('%Y%m%d')
        start_str = (end_dt - datetime.timedelta(days=20)).strftime('%Y%m%d')

        try:
            dates = await self.get_trade_dates(start_str, end_str)
            if dates:
                return dates[-1]
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to get latest trade date: {e}")

        # Fallback
        dt = end_dt
        while dt.weekday() >= 5:
            dt -= datetime.timedelta(days=1)
        return dt.strftime('%Y%m%d')

    async def get_trade_dates(self, start_date, end_date):
        """Get list of actual trade dates using Persistent DB Cache."""
        try:
            await self.ensure_trade_cal(end_date, required_start_date=start_date)
            cache_df = await self.cache.get_trade_cal(start_date=start_date, end_date=end_date, is_open=1)
            if not cache_df.empty:
                return sorted(cache_df['cal_date'].tolist())
        except Exception as e:
            logger.warning(f"[DataProcessor] Trade calendar sync failed: {e}")
            
        # Fallback (Simple logic)
        dates = []
        current = datetime.datetime.strptime(start_date, '%Y%m%d')
        end = datetime.datetime.strptime(end_date, '%Y%m%d')
        while current <= end:
            if current.weekday() < 5:
                dates.append(current.strftime('%Y%m%d'))
            current += datetime.timedelta(days=1)
        return dates

    async def init_data(self):
        """Initialize DB with enhanced schema"""
        await self.cache.init_db()
        await self.sync_stock_basic()

    async def should_sync_financials(self, force=False):
        """Check if financial data sync is needed."""
        if force:
            return True, "force=True"

        try:
            status = await self.cache.get_sync_status('financial_reports')
            if status is None or not status.get('last_sync_date'):
                return True, "never synced"

            last_sync = datetime.datetime.strptime(status.get('last_sync_date'), '%Y-%m-%d %H:%M:%S')
            days_since = (datetime.datetime.now() - last_sync).days

            if days_since >= 30:
                return True, f"last sync was {days_since} days ago"

            current_month = datetime.datetime.now().month
            if current_month in [1, 4, 7, 10]: # Earnings season
                 if days_since >= 7:
                     return True, "earnings season"

            return False, "recent"
        except Exception as e:
            return True, f"error: {e}"

    @log_async_operation(operation_name="check_data_health", log_result=True)
    async def check_data_health(self):
        """Check data health status."""
        if self._shutdown_event.is_set():
            return {'status': 'unknown', 'msg': 'Shutdown in progress'}

        await self.cache.init_db()

        try:
            from ui.i18n import I18n
            
            end_date = await self.get_latest_trade_date()
            start_date = (datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=365 * 3)).strftime('%Y%m%d')
            official_dates = await self.get_trade_dates(start_date, end_date)

            if not official_dates:
                return {'status': 'red', 'msg': 'Cannot get official calendar'}

            local_dates = await self.cache.get_cached_trade_dates()
            
            # 1. Market Health
            official_set = set(official_dates)
            last_local = sorted(list(local_dates))[-1] if local_dates else None
            
            lag_days = 0
            if official_dates[-1] not in local_dates:
                if local_dates and last_local:
                    lag_days = len([d for d in official_dates if d > last_local])
                else:
                    lag_days = len(official_dates)

            # 2. Financial Health
            deep_health = await self.cache.check_comprehensive_health()
            
            # Scorecard
            status = 'green'
            reasons = []

            if lag_days > 0:
                status = 'yellow'
                reasons.append(I18n.get('health_market_lag').format(days=lag_days))
            if lag_days > 3:
                status = 'red'

            fin_fresh_ratio = deep_health['tables'].get('financial_reports', {}).get('fresh_ratio', 0)
            if fin_fresh_ratio < 0.90:
                status = 'red'
                reasons.append(I18n.get('health_financial_missing').format(ratio=f"{fin_fresh_ratio:.0%}"))
            
            logger.info(f"[check_data_health] Status: {status}, lag_days: {lag_days}, fin_ratio: {fin_fresh_ratio:.1%}")
            
            return {
                'status': status,
                'reasons': reasons,
                'market': {'lag_days': lag_days, 'latest_local': last_local or 'N/A'},
                'fundamentals': deep_health,
                'financial_coverage': f"{fin_fresh_ratio:.1%}"
            }

        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return {'status': 'red', 'msg': str(e)}

    # ... Other simple sync/get methods ...

    @log_async_operation(operation_name="sync_stock_basic")
    async def sync_stock_basic(self):
        """Sync stock basic info (Step 1 of initialization)."""
        if self._shutdown_event.is_set(): 
            return 0
        
        # Deduplication Lock - prevent concurrent runs
        should_run = False
        with self._sync_lock:
            if not self._is_syncing_basic:
                self._is_syncing_basic = True
                should_run = True
        
        if not should_run: 
            logger.debug("[sync_stock_basic] Already running, skipping")
            return 0

        try:
            logger.info("[sync_stock_basic] Starting stock list sync...")
            df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_stock_list)
            
            if df is not None and not df.empty:
                count = await self.cache.save_stock_basic(df)
                await self.cache.update_sync_status('stock_basic', datetime.datetime.now().strftime('%Y%m%d'), count)
                logger.info(f"[sync_stock_basic] ✅ Synced {count} stocks")
                return count
            else:
                logger.warning("[sync_stock_basic] API returned empty data")
                return 0
                
        except Exception as e:
            logger.error(f"[sync_stock_basic] ❌ Failed: {e}")
            return 0
        finally:
            with self._sync_lock:
                self._is_syncing_basic = False

    async def prepare_market_data(self):
        """Prepare data for AI Analysis."""
        now = datetime.datetime.now()
        today_str = now.strftime('%Y%m%d')
        
        target_date = today_str
        # Check if trading day, if not fallback to latest
        # Simplified logic compared to original for brevity but keeping intent
        
        latest = await self.get_latest_trade_date()
        if latest != today_str:
            return latest
            
        # If it IS today, check if we have data
        cached_date = await self.cache.get_latest_trade_date()
        if cached_date != today_str:
             # Sync today
             await self.sync_daily_market_snapshot(today_str)
        
        return today_str

    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar is synced covers [required_start_date, end_date].
        Delegates to CacheManager.ensure_trade_cal.
        """
        await self.cache.ensure_trade_cal(end_date, self.api, required_start_date)

    async def get_market_overview(self):
        """
        Get market overview data for Home Screen.
        Returns Indices (SH, SZ, CYB) and Northbound Money Flow.
        """
        try:
            now = datetime.datetime.now()
            today_str = now.strftime('%Y%m%d')
            start_str = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')

            # Calendar Check (Simplified vs Original but functional)
            await self.ensure_trade_cal(today_str)
            
            # Find latest valid date
            cache_df = await self.cache.get_trade_cal(start_date=start_str, end_date=today_str, is_open=1)
            date = today_str
            if not cache_df.empty:
                 date = sorted(cache_df['cal_date'].tolist())[-1]
            
            # Parallel Fetch
            async def get_idx(code, name):
                df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_index_daily, ts_code=code, trade_date=date)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    c = row.get('pct_chg', 0)
                    v = row.get('close', 0)
                    return {'name': name, 'value': f"{v:.2f}", 'change': f"{c:+.2f}%", 'color': 'red' if c>=0 else 'green'}
                return {'name': name, 'value': '-', 'change': '-', 'color': 'grey'}

            async def get_hsgt():
                df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_moneyflow_hsgt, trade_date=date)
                if df is not None and not df.empty:
                    val = float(df.iloc[0]['north_money'])
                    return {'name': '北向资金', 'value': f"{val/100:.2f}亿" if abs(val)>100 else f"{val:.0f}万", 'sub': "流入" if val>0 else "流出"}
                return {'name': '北向资金', 'value': '-', 'sub': '-'}

            hot_concepts_task = NewsFetcher.get_hot_concepts(limit=8)

            results = await asyncio.gather(
                get_idx('000001.SH', '上证指数'),
                get_idx('399001.SZ', '深证成指'),
                get_idx('399006.SZ', '创业板指'),
                get_hsgt(),
                hot_concepts_task
            )

            return {
                'date': date,
                'indices': results[:3],
                'hsgt': results[3],
                'hot_concepts': results[4]
            }

        except Exception as e:
            logger.error(f"Failed to get market overview: {e}")
            return None

    async def get_screening_data(self, trade_date=None):
        return await self.cache.get_screening_data(trade_date)
    
    async def initialize_system(self, progress_callback=None):
        """
        Orchestrate system initialization with 5 distinct steps.
        
        Steps and weights:
        - Step 1 (5%):  Sync stock list
        - Step 2 (5%):  Sync trade calendar
        - Step 3 (50%): Sync historical data
        - Step 4 (35%): Sync financial data
        - Step 5 (5%):  Health check
        
        Returns:
            dict: Health check result on success
            None: If cancelled or critical failure
            
        Note: Call request_cancel() to cancel this operation.
        """
        from ui.i18n import I18n
        
        # Clear any previous cancel state
        self.clear_cancel()
        
        # Step weights (must sum to 100)
        STEP_WEIGHTS = [5, 5, 50, 35, 5]
        current_step = 0
        
        def report_step(step_num, sub_progress=0, sub_total=1, sub_msg=""):
            """Report progress with weighted calculation."""
            nonlocal current_step
            current_step = step_num
            
            if not progress_callback:
                return
            
            # Calculate base progress (sum of completed step weights)
            base = sum(STEP_WEIGHTS[:step_num - 1])
            
            # Add sub-progress within current step
            step_weight = STEP_WEIGHTS[step_num - 1]
            current = base + (sub_progress / max(sub_total, 1)) * step_weight
            
            step_label = I18n.get(f"init_step_{step_num}")
            msg = f"{step_label}" + (f" - {sub_msg}" if sub_msg else "")
            progress_callback(current, 100, msg)
        
        try:
            # ===== Step 1: Stock List (5%) =====
            report_step(1)
            stock_count = await self.sync_stock_basic()
            if stock_count == 0:
                logger.error("[initialize_system] Step 1 failed: No stocks synced, aborting")
                return None
            if self.is_cancelled(): return None
            
            # ===== Step 2: Trade Calendar (5%) =====
            report_step(2)
            end_date = datetime.datetime.now().strftime('%Y%m%d')
            start_date = (datetime.datetime.now() - datetime.timedelta(days=365*3)).strftime('%Y%m%d')
            cal_success = await self.cache.ensure_trade_cal(end_date, self.api, start_date)
            if not cal_success:
                logger.error("[initialize_system] Step 2 failed: Trade calendar sync failed, aborting")
                return None
            if self.is_cancelled(): return None
            
            # ===== Step 3: Historical Data (50%) =====
            def step3_callback(current, total, msg):
                report_step(3, current, total, msg)
            
            history_result = await self.sync_historical_data(
                days=365*3, 
                progress_callback=step3_callback
            )
            if history_result and history_result.status == "failed":
                logger.error(f"[initialize_system] Step 3 failed: {history_result.errors}")
                return None
            if self.is_cancelled(): return None
            
            # ===== Step 4: Financial Data (35%) =====
            def step4_callback(current, total, msg):
                report_step(4, current, total, msg)
            
            financial_result = await self.sync_comprehensive_fundamentals(
                progress_callback=step4_callback
            )
            if financial_result and financial_result.status == "failed":
                logger.error(f"[initialize_system] Step 4 failed: {financial_result.errors}")
                return None
            if self.is_cancelled(): return None
            
            # ===== Step 5: Health Check (5%) =====
            report_step(5)
            result = await self.check_data_health()
            
            # Report completion
            if progress_callback:
                progress_callback(100, 100, I18n.get("init_step_complete"))
            
            return result
            
        except Exception as e:
            step_label = I18n.get(f"init_step_{current_step}") if current_step > 0 else "Initialization"
            logger.error(f"[initialize_system] ❌ Failed at {step_label}: {e}")
            raise  # Re-raise so UI can catch and display

    # Proxy methods for backward compatibility
    async def sync_daily_quotes_for_date(self, trade_date):
        # We don't really use this individually anymore, but if needed:
        return 0 
    
    async def sync_daily_indicators_for_date(self, trade_date):
        return 0

    async def sync_moneyflow(self, trade_date=None):
        return await self.strategies['historical'].sync_moneyflow(trade_date)

    async def sync_northbound(self, trade_date=None):
        return await self.strategies['historical'].sync_northbound(trade_date)

    async def sync_market_news(self, limit=None):
        """Sync market news."""
        try:
             news = await NewsFetcher.get_latest_global_news(limit=limit or 20)
             if news:
                 for item in news:
                      await self.cache.save_market_news(item)
             return len(news) if news else 0
        except:
             return 0

    # ... get_stock_history, get_strategy_data ...
    async def get_stock_history(self, ts_code, days=365):
        end = datetime.datetime.now().strftime('%Y%m%d')
        start = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        return await self.cache.get_daily_quotes(ts_code=ts_code, start_date=start, end_date=end)

    async def get_strategy_data(self):
        return await self.prepare_screening_context()

    async def prepare_screening_context(self):
        """Prepare context for screening execution."""
        context = {}
        try:
            # 1. Main Screening Data (Quotes + Indicators)
            # Use latest trade date logic
            trade_date = await self.get_latest_trade_date()
            context['screening_data'] = await self.get_screening_data(trade_date)
            
            # 2. Auxiliary Data
            # Northbound
            nb = await self.context.cache.get_northbound(trade_date=trade_date)
            if nb is not None:
                context['northbound_data'] = nb
                
            # Moneyflow
            mf = await self.context.cache.get_moneyflow(trade_date=trade_date)
            if mf is not None:
                context['moneyflow_data'] = mf
            
            # Top List (LHB)
            lhb = await self.context.cache.get_top_list(trade_date=trade_date)
            if lhb is not None:
                context['top_list'] = lhb
                
            # Block Trade
            blk = await self.context.cache.get_block_trade(trade_date=trade_date)
            if blk is not None:
                context['block_trade'] = blk
                
        except Exception as e:
            logger.error(f"prepare_screening_context failed: {e}")
            
        return context
