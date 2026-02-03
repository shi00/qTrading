import pandas as pd
import datetime
import asyncio
import aiosqlite
import logging
import traceback
from data.tushare_client import TushareClient
from data.news_fetcher import NewsFetcher
from data.cache_manager import CacheManager
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation, track_performance, AsyncOperationLogger
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

class DataProcessor:
    """
    Main data processing class. Uses singleton TushareClient.
    """
    _instance = None
    
    def __new__(cls):
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
        self._shutdown_event = asyncio.Event() # Global cancellation token
        self._active_tasks = set()  # Track active async tasks for proper cleanup
        
        # Concurrency Control (Cross-Loop safe)
        import threading
        self._tasks_lock = threading.Lock()  # Thread-safe access to _active_tasks
        self._sync_lock = threading.Lock()
        self._is_syncing_basic = False
        
        self._initialized = True

    def stop(self):
        """Signal all running tasks to stop"""
        if not self._shutdown_event.is_set():
            self._shutdown_event.set()
            logger.info("[DataProcessor] Global stop signal received.")
            # Cancel all tracked tasks (thread-safe)
            if hasattr(self, '_active_tasks') and hasattr(self, '_tasks_lock'):
                with self._tasks_lock:
                    tasks_to_cancel = list(self._active_tasks)
                for task in tasks_to_cancel:
                    if not task.done():
                        task.cancel()

    def refresh_token(self, new_token=None):
        """Refresh API token without recreating instance"""
        if new_token is None:
            new_token = ConfigHandler.get_token()
        self._current_token = new_token
        self.api = TushareClient(token=new_token)
        logger.info("[DataProcessor] Token refreshed")

    async def close(self):
        """Gracefully close resources"""
        # First signal shutdown
        self.stop()
        
        # Wait for all active tasks to complete (with timeout)
        if hasattr(self, '_active_tasks') and hasattr(self, '_tasks_lock'):
            with self._tasks_lock:
                pending = [t for t in self._active_tasks if not t.done()]
            
            if pending:
                logger.info(f"[DataProcessor] Waiting for {len(pending)} tasks to complete...")
                try:
                    # Wait with timeout - returns (done, pending) sets
                    # Note: asyncio.wait() requires non-empty iterable
                    done, still_pending = await asyncio.wait(pending, timeout=5.0)
                    
                    # Cancel any still running
                    for task in still_pending:
                        task.cancel()
                    
                    # Await cancellation to complete
                    if still_pending:
                        await asyncio.gather(*still_pending, return_exceptions=True)
                        
                except Exception as e:
                    logger.warning(f"[DataProcessor] Error during task cleanup: {e}")
                finally:
                    with self._tasks_lock:
                        self._active_tasks.clear()
        
        if self.cache:
            await self.cache.close()

    @log_async_operation(operation_name="get_latest_trade_date", log_exceptions=True)
    async def get_latest_trade_date(self):
        """
        Get absolute latest trading date (today or previous trading day).
        Uses persistent calendar cache.
        """
        now = datetime.datetime.now()
        # If before 16:00, target is yesterday maximum
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
            # 保留关键决策日志
            logger.warning(f"[DataProcessor] Failed to get latest trade date, using fallback: {e}")

        # Fallback
        dt = end_dt
        while dt.weekday() >= 5: 
             dt -= datetime.timedelta(days=1)
        return dt.strftime('%Y%m%d')

    async def get_trade_dates(self, start_date, end_date):
        """
        Get list of actual trade dates using Persistent DB Cache.
        """
        try:
            # 1. Ensure Cache is warm
            await self.ensure_trade_cal(end_date, required_start_date=start_date)
            
            # 2. Query Cache
            cache_df = await self.cache.get_trade_cal(start_date=start_date, end_date=end_date, is_open=1)
            
            if not cache_df.empty:
                return sorted(cache_df['cal_date'].tolist())
                
        except Exception as e:
            logger.warning(f"[DataProcessor] Trade calendar sync failed, using fallback: {e}")
        
        # Fallback if DB fails
        dates = []
        try:
            # Try Offline Calendar first (Handles holidays correctly)
            from data.offline_calendar import OfflineCalendar
            dates = OfflineCalendar.get_trade_dates(start_date, end_date)
            
            if not dates:
                 # Last resort: Weekday check
                 raise Exception("Offline calendar returned empty")
                 
        except Exception as ex:
            logger.warning(f"[DataProcessor] Offline calendar fallback failed: {ex}, using simple weekday check")
            try:
                current = datetime.datetime.strptime(start_date, '%Y%m%d')
                end = datetime.datetime.strptime(end_date, '%Y%m%d')
                while current <= end:
                    if current.weekday() < 5:
                        dates.append(current.strftime('%Y%m%d'))
                    current += datetime.timedelta(days=1)
            except:
                pass
        return dates

    async def init_data(self):
        """Initialize DB with enhanced schema"""
        await self.cache.init_db()
        # Ensure stock basic info (including list_status) is up to date
        await self.sync_stock_basic()

    async def should_sync_financials(self, force=False):
        """
        Check if financial data sync is needed.
        
        Returns True if:
        - force=True
        - Never synced before
        - Last sync was more than 30 days ago
        - Currently in earnings season months (Jan, Apr, Jul, Oct)
        
        :param force: Force sync regardless of conditions
        :return: Tuple of (should_sync: bool, reason: str)
        """
        if force:
            return True, "force=True"
        
        try:
            status = await self.cache.get_sync_status('financial_reports')
            
            if status is None:
                return True, "never synced"
            
            last_sync_str = status.get('last_sync_date')
            if not last_sync_str:
                return True, "no last sync date"
            
            # Parse last sync date
            try:
                last_sync = datetime.datetime.strptime(last_sync_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                return True, "invalid last sync date format"
            
            now = datetime.datetime.now()
            days_since_sync = (now - last_sync).days
            
            # Sync if more than 30 days old
            if days_since_sync >= 30:
                return True, f"last sync was {days_since_sync} days ago"
            
            # Sync during earnings season (披露季)
            # Q1: Jan-Apr, Q2: Apr-May, Q3: Jul-Aug, Q4: Oct-Nov
            current_month = now.month
            if current_month in [1, 4, 7, 10]:
                logger.info(f"[DataProcessor] Earnings season month ({current_month}), checking if sync needed...")
                # During earnings season, sync if more than 7 days old
                if days_since_sync >= 7:
                    return True, f"earnings season, last sync was {days_since_sync} days ago"
            
            return False, f"last sync was only {days_since_sync} days ago"
            
        except Exception as e:
            logger.warning(f"[DataProcessor] Error checking financial sync status: {e}")
            return True, f"error checking status: {e}"


    @log_async_operation(operation_name="check_data_health", log_result=True)
    async def check_data_health(self):
        """
        Check data health status: Timeliness (Market) and Completeness (Financials).
        Returns comprehensive report.
        """
        # Ensure schema (indices) are up to date
        await self.cache.init_db()
        
        try:
            # 1. Market Data Logic (Keep existing time-series check)
            end_date = await self.get_latest_trade_date()
            start_date = (datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=365*3)).strftime('%Y%m%d')
            official_dates = await self.get_trade_dates(start_date, end_date)
            
            if not official_dates:
                return {'status': 'red', 'msg': 'Cannot get official calendar'}
                
            official_dates.sort()
            official_set = set(official_dates)
            local_dates = await self.cache.get_cached_trade_dates()
            
            # Market Metrics
            is_latest_synced = official_dates[-1] in local_dates
            last_local = sorted(list(local_dates))[-1] if local_dates else 'None'
            
            lag_days = 0
            if not is_latest_synced and local_dates:
                lag_days = len([d for d in official_dates if d > last_local])
            elif not local_dates:
                lag_days = len(official_dates)
            
            # 2. Comprehensive Fundamentals Check
            deep_health = await self.cache.check_comprehensive_health()
            
            # v2.0 Scorecard (Strict Zero Tolerance)
            status = 'green'
            reasons = []
            
            # Metric 1: Market Lag
            if lag_days > 0:
                status = 'yellow'
                reasons.append(f"行情滞后 {lag_days} 天")
            if lag_days > 3: 
                status = 'red'
            
            # Metric 2: Financial Freshness (Strict)
            fin_stats = deep_health['tables'].get('financial_reports', {})
            fin_fresh_ratio = fin_stats.get('fresh_ratio', 0)
            target_period = fin_stats.get('deadline_desc', '必要报告期')
            
            if fin_fresh_ratio < 0.90:
                status = 'red'
                reasons.append(f"{target_period} 缺失 (仅 {fin_fresh_ratio*100:.0f}%)")
            elif fin_fresh_ratio < 0.98:
                if status == 'green': status = 'yellow'
                reasons.append(f"{target_period} 覆盖不足")
                
            # Metric 3: Gaps (Continuity)
            gap_count = deep_health.get('gap_count', 0)
            if gap_count > 0:
                status = 'red' # Zero Tolerance
                reasons.append(f"发现 {gap_count} 处行情断点")
                
            # Metric 4: Sanity (Integrity)
            sanity_errors = deep_health.get('sanity_errors', 0)
            if sanity_errors > 0:
                if status == 'green': status = 'yellow'
                if sanity_errors > 10: status = 'red'
                reasons.append(f"发现 {sanity_errors} 条脏数据")

            # 保留诊断关键日志
            logger.debug(f"[check_data_health] diagnostics: lag={lag_days}d, fin_fresh={fin_fresh_ratio:.2%}, gaps={gap_count}, sanity_err={sanity_errors}")

            return {
                'status': status,
                'reasons': reasons,
                'market': {
                    'latest_local': last_local,
                    'latest_official': official_dates[-1],
                    'lag_days': lag_days,
                    'coverage_ratio': len(local_dates & official_set) / len(official_set) if official_set else 0
                },
                'fundamentals': deep_health,
                'financial_coverage': f"{fin_stats.get('ratio',0)*100:.1f}%", 
                'missing_count': deep_health['tables']['financial_reports'].get('total_stocks', 0) - deep_health['tables']['financial_reports'].get('covered', 0)
            }
            
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            logger.exception(e)
            return {'status': 'red', 'msg': str(e)}

    # ===== Single Day Sync Methods =====
    
    async def sync_daily_quotes_for_date(self, trade_date):
        """Sync daily quotes for a specific date"""
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date))
        if df is not None and not df.empty:
            count = await self.cache.save_daily_quotes(df)
            return count
        return 0

    async def sync_daily_indicators_for_date(self, trade_date):
        """Sync daily indicators for a specific date"""
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date))
        if df is not None and not df.empty:
            count = await self.cache.save_daily_indicators(df)
            return count
        return 0

    # ===== Full Sync Methods =====

    @log_async_operation(operation_name="sync_stock_basic", performance_threshold_ms=5000)
    async def sync_stock_basic(self, force=False):
        """Sync stock basic info (Thread-Safe & Deduplicated)"""
        if self._shutdown_event.is_set(): 
            return 0
        
        # Cross-Loop Deduplication
        should_run = False
        with self._sync_lock:
            if not self._is_syncing_basic:
                self._is_syncing_basic = True
                should_run = True
        
        if not should_run:
            # Another task is syncing, wait for completion
            while True:
                with self._sync_lock:
                    if not self._is_syncing_basic:
                        break
                await asyncio.sleep(0.1)
            return 0
        
        try:
            loop = asyncio.get_running_loop()
            df = await loop.run_in_executor(None, lambda: self.api.get_stock_list())
            if df is not None and not df.empty:
                count = await self.cache.save_stock_basic(df)
                await self.cache.update_sync_status('stock_basic', datetime.datetime.now().strftime('%Y%m%d'), count)
                return count
            return 0
        finally:
            with self._sync_lock:
                self._is_syncing_basic = False


    @log_async_operation(operation_name="sync_market_snapshot", log_args=True, log_result=False, performance_threshold_ms=5000)
    async def sync_daily_market_snapshot(self, trade_date=None, cancel_event=None):
        """
        Sync FULL daily market data for a single date.
        Includes: Quotes, Indicators, MoneyFlow, Northbound, LHB, BlockTrade.
        """
        if trade_date is None:
            trade_date = await self.get_latest_trade_date()
        
        # Check if already cached (Basic check only, assuming if quotes exist, others might too or we re-sync)
        # Actually for historical sync, we might want to be smarter, but re-syncing ensuring completeness is safer.
        if cancel_event and cancel_event.is_set():
             return None
        
        cached_date = await self.cache.get_latest_trade_date()
        if cached_date == trade_date:
            # We must be careful here. If we only checked quotes before, we might miss extended data.
            # But "get_screening_data" only joins quotes/indicators.
            # Let's assume if it sends a request for a specific date, it wants a sync.
            pass

        loop = asyncio.get_running_loop()
        
        # Fetch ALL data types in parallel
        
        # We wrap each safe fetcher to handle permissions/empty data gracefully
        async def fetch_safe(func, name):
            try:
                return await loop.run_in_executor(None, lambda: func(trade_date=trade_date))
            except Exception as e:
                # 保留关键决策日志 - 权限错误
                if "权限" in str(e) or "2000" in str(e) or "积分" in str(e):
                    logger.warning(f"[sync_market_snapshot] PERMISSION_DENIED: {name}")
                else:
                    logger.debug(f"[sync_market_snapshot] fetch_failed: {name} - {type(e).__name__}")
                return None

        # Prepare Task Configurations
        # Format: (key, api_func, log_name, cache_func, sync_status_key)
        # Note: cache_func and sync_status_key are handled in post-processing
        task_configs = [
            ("quotes", self.api.get_daily_quotes, "Daily Quotes"),
            ("basic", self.api.get_daily_basic, "Daily Indicators"),
            ("limit", self.api.get_limit_list, "Limit List"),
            ("suspend", self.api.get_suspend_d, "Suspend List"),
            ("margin", self.api.get_margin_detail, "Margin Detail"),
            ("mf", self.api.get_moneyflow, "Money Flow"),
            ("north", self.api.get_hk_hold, "Northbound"),
            ("lhb", self.api.get_top_list, "Dragon Tiger"),
            ("block", self.api.get_block_trade, "Block Trade"),
            ("index_basic", self.api.get_index_dailybasic, "Index Indicators")
        ]

        # Wrap fetcher logic
        async def fetch_wrapper(key, func, name):
            try:
                res = await loop.run_in_executor(None, lambda: func(trade_date=trade_date))
                return (key, res)
            except Exception as e:
                if "权限" in str(e) or "2000" in str(e) or "积分" in str(e):
                    logger.warning(f"[sync_market_snapshot] PERMISSION_DENIED: {name}")
                else:
                    logger.debug(f"[sync_market_snapshot] fetch_failed: {name} - {type(e).__name__}")
                return (key, None)

        # Index Data Special Case
        MAJOR_INDICES = ['000001.SH', '399001.SZ', '399006.SZ', '000300.SH', '000905.SH', '000852.SH', '000688.SH']
        async def fetch_indices_safe():
            try:
                tasks = [loop.run_in_executor(None, lambda c=code: self.api.get_index_daily(ts_code=c, trade_date=trade_date)) for code in MAJOR_INDICES]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                dfs = [r for r in results if isinstance(r, pd.DataFrame) and not r.empty]
                if dfs:
                    return ("index", pd.concat(dfs, ignore_index=True))
                return ("index", None)
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to fetch indices: {e}")
                return ("index", None)

        # Launch all tasks
        api_tasks = [fetch_wrapper(tc[0], tc[1], tc[2]) for tc in task_configs]
        api_tasks.append(fetch_indices_safe())
        
        main_future = asyncio.gather(*api_tasks)
        
        # Race against cancel
        wait_tasks = [main_future]
        cancel_wait_task = None
        
        if cancel_event:
            cancel_wait_task = asyncio.create_task(cancel_event.wait())
            wait_tasks.append(cancel_wait_task)
            
        done, pending = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
        
        if cancel_wait_task and cancel_wait_task in pending:
            cancel_wait_task.cancel() # Cleanup
            
        # Handle Cancellation
        if cancel_event and cancel_event.is_set():
            logger.info(f"[sync_market_snapshot] CANCELLED for {trade_date}")
            # Try to cleanup main future
            if not main_future.done():
                main_future.cancel()
                try: await main_future
                except: pass
            return None
            
        # Process Results
        try:
            results_list = main_future.result()
            results_map = {k: v for k, v in results_list}
        except Exception as e:
            logger.error(f"[DataProcessor] Critical Fetch Error: {e}")
            return None

        # Handle Today's Data Logic
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        df_quotes = results_map.get("quotes")
        
        if trade_date == today_str and (df_quotes is None or df_quotes.empty):
             logger.warning(f"[DataProcessor] Today's data ({trade_date}) not ready or empty.")

        # Save Logic (Using a helper or map would be cleaner, but explicit saving allows custom logic like filters)
        c_q = 0
        c_i = 0
        
        # Helper for saving
        async def save_if_exists(key, save_method, *args):
            data = results_map.get(key)
            if data is not None and not data.empty:
                return await save_method(data, *args)
            return 0

        # 1. Quotes & Indicators
        c_q = await save_if_exists("quotes", self.cache.save_daily_quotes)
        if c_q > 0: await self.cache.update_sync_status('daily_quotes', trade_date, c_q)
        
        if c_q == 0 and trade_date != today_str:
             logger.warning(f"[sync_market_snapshot] NO_QUOTES_AVAILABLE for {trade_date}")

        c_i = await save_if_exists("basic", self.cache.save_daily_indicators)
        if c_i > 0: await self.cache.update_sync_status('daily_indicators', trade_date, c_i)
        
        # Other simple saves
        await save_if_exists("limit", self.cache.save_limit_list)
        await save_if_exists("suspend", self.cache.save_suspend_d)
        await save_if_exists("margin", self.cache.save_margin_daily)
        await save_if_exists("lhb", self.cache.save_top_list)
        await save_if_exists("block", self.cache.save_block_trade)
        await save_if_exists("index", self.cache.save_index_daily)
        await save_if_exists("index_basic", self.cache.save_index_dailybasic)
        
        # Special handling for extended logic
        c_mf = await save_if_exists("mf", self.cache.save_moneyflow)
        if c_mf > 0: await self.cache.update_sync_status('moneyflow_daily', trade_date, c_mf)
            
        df_north = results_map.get("north")
        if df_north is not None and not df_north.empty:
            df_north = df_north[df_north['ts_code'].astype(str).str.endswith(('.SH', '.SZ'))]
            if not df_north.empty:
                c_n = await self.cache.save_northbound(df_north)
                await self.cache.update_sync_status('northbound_holding', trade_date, c_n)
        
        # Return merged data
        df_basic = results_map.get("basic")
        if df_quotes is not None and not df_quotes.empty and df_basic is not None and not df_basic.empty:
            df_merged = pd.merge(df_quotes, df_basic, on=['ts_code', 'trade_date'], how='outer', suffixes=('', '_ind'))
            return df_merged
            
        return df_quotes if df_quotes is not None and not df_quotes.empty else df_basic



    async def prepare_market_data(self):
        """
        Smart logic to prepare data for AI Analysis.
        Handles:
        1. Non-Trading Day: Use latest available close (3yr).
        2. Trading Day (Pre-Close): Use yesterday's close.
        3. Trading Day (Post-Close): Sync Today's data, then use Today.
        
        Returns:
            str: The 'target_date' that analysis should use.
        """
        now = datetime.datetime.now()
        today_str = now.strftime('%Y%m%d')
        
        # Determine if today is a trading day
        is_trading_day = False
        try:
            is_trading_day = self.api.is_trading_day(today_str)
        except:
            # Fallback: weekday check
            is_trading_day = now.weekday() < 5
            
        target_date = None
        
        # Branch 1 & 2: Non-Trading OR Pre-Closing (e.g. 14:00)
        # We use "latest valid close" (yesterday or friday)
        if not is_trading_day or now.hour < 16:
             target_date = await self.get_latest_trade_date()
             logger.info(f"[DataProcessor] AI Analysis Target: {target_date} (Pre-Close/Holiday). No sync needed.")
             
        # Branch 3: Trading Day AND Post-Closing (e.g. 20:30)
        else:
            target_date = today_str
            logger.info(f"[DataProcessor] AI Analysis Target: {target_date} (Post-Close). Checking sync...")
            
            # Check if synced
            latest_cached = await self.cache.get_latest_trade_date()
            if latest_cached != target_date:
                logger.info(f"[DataProcessor] {target_date} data missing in cache. Forcing Sync...")
                await self.sync_daily_market_snapshot(trade_date=target_date)
                
        return target_date

    @log_async_operation(operation_name="sync_historical", log_args=True, performance_threshold_ms=30000)
    @track_performance(threshold_seconds=60, alert_level="WARNING")
    async def sync_historical_data(self, days=365, progress_callback=None, cancel_event=None):
        """
        Sync historical data for the last N days using concurrency.
        Includes Circuit Breaker and Post-Sync Retry mechanism.
        """
        from ui.i18n import I18n
        
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        
        # Get actual trading dates (inverse order: new -> old)
        trade_dates = await self.get_trade_dates(start_date, end_date)
        trade_dates.sort(reverse=True)
        
        # === Breakpoint Resume Logic ===
        try:
            cached_quotes_dates = await self.cache.get_cached_trade_dates()
            cached_ind_dates = await self.cache.get_cached_indicator_dates()
            existing_dates = cached_quotes_dates.intersection(cached_ind_dates)
            
            original_count = len(trade_dates)
            trade_dates = [d for d in trade_dates if d not in existing_dates]
            skipped_count = original_count - len(trade_dates)
            
            if skipped_count > 0:
                # 保留断点续传关键日志
                logger.info(f"[sync_historical] SKIP_EXISTING: {skipped_count} days already synced")
        except Exception as e:
            logger.warning(f"[sync_historical] Failed to check cached dates, will sync all: {e}")

        total_days = len(trade_dates)
        
        # Concurrency control
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(max(1, concurrency))
        logger.debug(f"[sync_historical] concurrency={concurrency}")
        
        # Failure tracking
        failed_dates = []
        CB_THRESHOLD = max(20, int(total_days * 0.1) if total_days > 0 else 20)
        abort_sync = False
        
        async def sync_one_day_safe(date, idx):
            nonlocal abort_sync
            # Check global shutdown or local cancel
            if self._shutdown_event.is_set():
                return
            if cancel_event and cancel_event.is_set():
                return
            if abort_sync:
                return

            async with semaphore:
                if self._shutdown_event.is_set():
                     return
                if cancel_event and cancel_event.is_set() or abort_sync:
                    return
                    
                try:
                    # Check circuit breaker
                    if len(failed_dates) > CB_THRESHOLD:
                        abort_sync = True
                        # 保留熟断器关键日志
                        logger.error(f"[sync_historical] CIRCUIT_BREAKER: {len(failed_dates)} failures, threshold={CB_THRESHOLD}")
                        return

                    await self.sync_daily_market_snapshot(date, cancel_event=cancel_event)
                    
                    if progress_callback:
                        progress_callback(idx + 1, total_days, I18n.get('progress_sync_market').format(date=date))
                except Exception as e:
                    logger.debug(f"[sync_historical] sync_failed: {date} - {type(e).__name__}")
                    failed_dates.append(date)

        # Create tasks
        tasks = []
        for i, date in enumerate(trade_dates):
            if self._shutdown_event.is_set():
                 logger.info("[sync_historical] GLOBAL_SHUTDOWN")
                 return total_days - len(trade_dates) + i
            if cancel_event and cancel_event.is_set() or abort_sync:
                break
            tasks.append(asyncio.create_task(sync_one_day_safe(date, i)))
            
        # Run main batch
        if tasks:
            # Register tasks for tracking (thread-safe)
            with self._tasks_lock:
                self._active_tasks.update(tasks)
            
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                # Unregister completed tasks (thread-safe)
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)
            
            if self._shutdown_event.is_set():
                return 0

            if cancel_event and cancel_event.is_set():
                logger.warning("[sync_historical] USER_CANCELLED")
                return 0
            
            if abort_sync:
                logger.error("[sync_historical] ABORTED_BY_CIRCUIT_BREAKER")

        # === Smart Retry Mechanism ===
        if failed_dates and not self._shutdown_event.is_set():
            MAX_RETRIES = ConfigHandler.get_sync_retry_count()
            # 保留重试关键日志
            logger.info(f"[sync_historical] RETRY_START: {len(failed_dates)} failures, max_retries={MAX_RETRIES}")
            
            for retry_round in range(MAX_RETRIES):
                if not failed_dates or (cancel_event and cancel_event.is_set()) or self._shutdown_event.is_set():
                    break
                
                logger.debug(f"[sync_historical] retry_round={retry_round+1}/{MAX_RETRIES}, count={len(failed_dates)}")
                try:
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    break
                
                if self._shutdown_event.is_set():
                    break

                current_batch = failed_dates[:]
                failed_dates = []
                
                retry_sem = asyncio.Semaphore(2) 
                
                async def retry_one(date):
                    if self._shutdown_event.is_set(): return
                    async with retry_sem:
                        try:
                            await self.sync_daily_market_snapshot(date)
                            logger.debug(f"[sync_historical] retry_success: {date}")
                        except Exception as e:
                            logger.debug(f"[sync_historical] retry_fail: {date}")
                            failed_dates.append(date)
                
                retry_tasks = [asyncio.create_task(retry_one(d)) for d in current_batch]
                
                # Register retry tasks for tracking (thread-safe)
                with self._tasks_lock:
                    self._active_tasks.update(retry_tasks)
                
                try:
                    await asyncio.gather(*retry_tasks, return_exceptions=True)
                finally:
                    with self._tasks_lock:
                        self._active_tasks.difference_update(retry_tasks)
                
        # 最终统计 - 保留关键结果日志
        if failed_dates:
            logger.error(f"[sync_historical] RETRY_EXHAUSTED: {len(failed_dates)} dates still failed: {failed_dates[:10]}...")
        else:
            logger.debug(f"[sync_historical] ALL_RECOVERED")

        if self._shutdown_event.is_set():
             logger.info("[sync_historical] GLOBAL_SHUTDOWN during final phase")
             return total_days - len(failed_dates)

        return total_days - len(failed_dates)

    @log_async_operation(operation_name="sync_financial", log_args=True, performance_threshold_ms=10000)
    async def sync_financial_reports(self, periods=None, progress_callback=None, force=False, cancel_event=None):
        """
        Sync financial reports using Hybrid Strategy (Full vs Incremental).
        """
        if self._shutdown_event.is_set():
             logger.info("[sync_financial] GLOBAL_SHUTDOWN")
             return 0
              
        from ui.i18n import I18n

        should_full_sync = False
        if periods is not None:
            should_full_sync = True
            # 保留关键决策日志
            logger.info("[sync_financial] STRATEGY: Full Sync (manual periods)")
        elif force:
            should_full_sync = True
            logger.info("[sync_financial] STRATEGY: Full Sync (force=True)")
        else:
            status = await self.cache.get_sync_status('financial_reports')
            if not status or not status.get('last_sync_date'):
                should_full_sync = True
                logger.info("[sync_financial] STRATEGY: Full Sync (first run)")
        
        reset_checkpoint = False
        if should_full_sync:
            if force:
                 reset_checkpoint = True
                 
            status = await self.cache.get_sync_status('financial_reports')
            if not status or not status.get('last_sync_date'):
                reset_checkpoint = True
                
            return await self._run_full_sync(periods, progress_callback, cancel_event, reset_checkpoint=reset_checkpoint)
        else:
            return await self._run_incremental_sync(progress_callback)

    @log_async_operation(operation_name="incremental_sync", performance_threshold_ms=5000)
    async def _run_incremental_sync(self, progress_callback=None):
        """
        Incremental Sync: Query 'disclosure_date' to find *only* updated stocks.
        """
        if self._shutdown_event.is_set(): return 0
        from ui.i18n import I18n
        
        status = await self.cache.get_sync_status('financial_reports')
        last_sync_str = status.get('last_sync_date')
        
        try:
            last_sync_dt = datetime.datetime.strptime(last_sync_str, '%Y-%m-%d %H:%M:%S')
            start_date_dt = last_sync_dt + datetime.timedelta(days=1)
        except:
            logger.warning("[incremental_sync] Invalid last_sync_date, fallback to 30 days ago")
            start_date_dt = datetime.datetime.now() - datetime.timedelta(days=30)
            
        today_dt = datetime.datetime.now()
        
        # Generate date range
        dates_to_sync = []
        curr = start_date_dt
        while curr.date() <= today_dt.date():
            dates_to_sync.append(curr.strftime('%Y%m%d'))
            curr += datetime.timedelta(days=1)
            
        if not dates_to_sync:
            logger.debug("[incremental_sync] Data already up-to-date")
            return 0
            
        logger.debug(f"[incremental_sync] date_range: {dates_to_sync[0]} to {dates_to_sync[-1]} ({len(dates_to_sync)} days)")
        
        total_saved = 0
        loop = asyncio.get_running_loop()
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(concurrency)
        
        for day_str in dates_to_sync:
            
            df_disclosure = await loop.run_in_executor(None, lambda: self.api.get_disclosure_date(date=day_str))
            
            if df_disclosure is None or df_disclosure.empty:
                continue
            
            target_list = df_disclosure[['ts_code', 'end_date']].drop_duplicates().to_dict('records')
            logger.debug(f"[incremental_sync] {day_str}: {len(target_list)} reports")
            
            if not target_list:
                continue
                
            # Sync these specific targets
            tasks = []
            
            async def sync_one_target(item):
                nonlocal total_saved
                ts_code = item['ts_code']
                period = item['end_date'] # API needs YYYYMMDD
                
                async with semaphore:
                    try:
                        await asyncio.sleep(0.1) # Rate limit protection
                        
                        # Use helper to fetch comprehensive data for this specific period
                        df = await self._fetch_comprehensive_financial_data(ts_code=ts_code, period=period)
                        
                        if df is not None and not df.empty:
                            # Save logic (reuse existing save method if possible or raw)
                            # We need to ensure columns match cache schema
                            # Reusing logic from full sync
                            schema_cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                                         'n_income', 'n_income_attr_p', 'total_assets', 'total_liab', 
                                         'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin', 
                                         'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
                            
                            for col in schema_cols:
                                if col not in df.columns:
                                    df[col] = None
                                    
                            count = await self.cache.save_financial_reports(df[schema_cols])
                            if count > 0:
                                total_saved += count
                                logger.debug(f"Synced {ts_code} for {period}")
                    except Exception as e:
                        logger.warning(f"Failed incremental sync for {ts_code} {period}: {e}")

            # Batch execute for this day
            for item in target_list:
                tasks.append(sync_one_target(item))
            
            await asyncio.gather(*tasks)
            
            if progress_callback:
                progress_callback(0, 0, f"{I18n.get('progress_sync_done')} {day_str}")

        # Update status to NOW
        await self.cache.update_sync_status('financial_reports', datetime.datetime.now().strftime('%Y%m%d'), total_saved)
        logger.info(f"[DataProcessor] [OK] Incremental sync complete. Total new records: {total_saved}")
        return total_saved


    async def initialize_system(self, progress_callback=None, cancel_event=None):
        """
        Master Initialization Sequence (Step 1-5).
        Orchestrates the entire sync process from scratch.
        """
        from ui.i18n import I18n
        
        # Weighted Progress Configuration
        # Step 1: Stock List (2%)
        # Step 2: Calendar (3%)
        # Step 3: Quotes (45%)
        # Step 4: Fundamentals (45%)
        # Step 5: Health Check (5%)
        step_weights = [2, 3, 45, 45, 5]
        step_starts = [0, 2, 5, 50, 95]
        
        def report_progress(step_idx, fraction, msg):
            if not progress_callback: return
            base = step_starts[step_idx]
            width = step_weights[step_idx]
            current_val = base + (width * fraction)
            progress_callback(current_val, 100, msg)

        # Step 1: Basic Info
        report_progress(0, 0.1, f"[1/5] {I18n.get('init_stock_list')}")
        if cancel_event and cancel_event.is_set(): return
        await self.sync_stock_basic()
        report_progress(0, 1.0, f"[1/5] {I18n.get('init_stock_list_done')}")
        
        # Step 2: Trade Calendar (3 Years)
        report_progress(1, 0.1, f"[2/5] {I18n.get('init_calendar')}")
        if cancel_event and cancel_event.is_set(): return
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=365*3)).strftime('%Y%m%d')
        await self.cache.init_db() # Explicit schema check
        # Force sync calendar
        loop = asyncio.get_running_loop()
        df_cal = await loop.run_in_executor(None, lambda: self.api.get_trade_cal(start_date, end_date))
        if df_cal is not None:
             await self.cache.save_trade_cal(df_cal)
        report_progress(1, 1.0, f"[2/5] {I18n.get('init_calendar_done')}")
             
        # Step 3: Historical Quotes (3 Years)
        report_progress(2, 0.0, f"[3/5] {I18n.get('init_quotes')}")
        if cancel_event and cancel_event.is_set(): return
        
        def step3_callback(current, total, msg):
             fraction = current / max(1, total) if total > 0 else 0
             fraction = max(0.0, min(1.0, fraction))
             report_progress(2, fraction, f"[3/5] {msg}")

        # This will use the UPDATED sync_daily_market_snapshot inside
        await self.sync_historical_data(days=365*3, progress_callback=step3_callback, cancel_event=cancel_event)
        
        # Step 4: Comprehensive Fundamentals
        report_progress(3, 0.0, f"[4/5] {I18n.get('init_fundamentals')}")
        if cancel_event and cancel_event.is_set(): return
        
        def step4_callback(current, total, msg):
             fraction = current / max(1, total) if total > 0 else 0
             fraction = max(0.0, min(1.0, fraction))
             report_progress(3, fraction, f"[4/5] {msg}")
                 
        await self.sync_comprehensive_fundamentals(progress_callback=step4_callback, cancel_event=cancel_event)
        
        # Step 5: Health Check
        report_progress(4, 0.1, f"[5/5] {I18n.get('init_health_check')}")
        if cancel_event and cancel_event.is_set(): return
        
        health_report = await self.check_data_health()
        status = health_report.get('status', 'red').upper()
        coverage = health_report.get('financial_coverage', '0%')
        
        final_msg = I18n.get('init_complete').format(status=status, coverage=coverage)
        if progress_callback: progress_callback(100, 100, final_msg)
        
        return health_report

    async def _run_full_sync(self, periods=None, progress_callback=None, cancel_event=None, reset_checkpoint=False):
        """
        Legacy/Alias for Full Sync.
        Redirects to sync_comprehensive_fundamentals for simplicity.
        """
        await self.sync_comprehensive_fundamentals(progress_callback=progress_callback, cancel_event=cancel_event, reset_checkpoint=reset_checkpoint)

    async def sync_comprehensive_fundamentals(self, progress_callback=None, cancel_event=None, reset_checkpoint=False):
        """
        Step 4 Implementation: Loop by Stock, 9 concurrent requests per stock.
        """
        from ui.i18n import I18n
        logger.info("[DataProcessor] Starting Step 4: Comprehensive Fundamentals Sync...")
        
        # 1. Get Stock List
        df_basic = await self.cache.get_stock_basic()
        if df_basic.empty:
            logger.error("[DataProcessor] No stocks found. Run Step 1 first.")
            return

        # Sort active stocks first? List status L.
        df_active = df_basic[df_basic['list_status'] == 'L']
        stocks = df_active['ts_code'].tolist()
        total_stocks = len(stocks)
        
        # 2. Concurrency Control (Stock Level)
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(concurrency)
        
        # 3. Resume Logic
        import json
        import os
        checkpoint_file = 'sync_checkpoint_step4.json'
        
        if reset_checkpoint and os.path.exists(checkpoint_file):
            logger.info(f"[DataProcessor] Reset requested. Deleting stale checkpoint: {checkpoint_file}")
            try:
                os.remove(checkpoint_file)
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to delete checkpoint: {e}")

        start_index = 0
        if os.path.exists(checkpoint_file):
            try:
                with open(checkpoint_file, 'r') as f:
                    data = json.load(f)
                    # Check if total matches (if stock count changed significantly, maybe discard?)
                    # But more importantly: Check if it was already "Done"
                    saved_index = data.get('last_index', 0)
                    saved_total = data.get('total', 0)
                    
                    if saved_index >= saved_total and saved_total > 0:
                         logger.info(f"[DataProcessor] Found COMPLETED checkpoint ({saved_index}/{saved_total}). Assuming user wants RESTART.")
                         start_index = 0
                         try: os.remove(checkpoint_file)
                         except: pass
                    elif data.get('total') == total_stocks:
                        start_index = saved_index
                        logger.info(f"[DataProcessor] Resuming Step 4 from index {start_index}")
                    else:
                        logger.warning(f"[DataProcessor] Stock count mismatch (Saved: {data.get('total')}, Curr: {total_stocks}). Discarding checkpoint.")
                        start_index = 0
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to read checkpoint: {e}")
                start_index = 0
            
        completed_count = start_index
        
        async def process_one_stock(ts_code):
            # Check cancellation BEFORE acquiring semaphore to avoid being stuck
            if cancel_event and cancel_event.is_set(): return
            if self._shutdown_event.is_set(): return
            
            try:
                async with semaphore:
                    # Double-check after acquiring semaphore
                    if cancel_event and cancel_event.is_set(): return
                    if self._shutdown_event.is_set(): return
                    
                    try:
                        loop = asyncio.get_running_loop()
                        # Note: No need to check loop.is_closed() here.
                        # get_running_loop() raises RuntimeError if no loop is running.
                            
                        end_date = datetime.datetime.now().strftime('%Y%m%d')
                        start_date = (datetime.datetime.now() - datetime.timedelta(days=365*3)).strftime('%Y%m%d')
                        
                        async def f(func, **kwargs):
                            try:
                                # Tushare Client is synchronous, so run in executor
                                return await loop.run_in_executor(None, lambda: func(**kwargs))
                            except asyncio.CancelledError:
                                raise  # Propagate cancellation
                            except Exception as e:
                                return None

                        # 9 Data Types
                        t1 = f(self.api.get_income, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t2 = f(self.api.get_balancesheet, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t3 = f(self.api.get_cashflow, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t4 = f(self.api.get_fina_indicator, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t5 = f(self.api.get_fina_audit, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t6 = f(self.api.get_forecast, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t7 = f(self.api.get_fina_mainbz, ts_code=ts_code, start_date=start_date, end_date=end_date)
                        t8 = f(self.api.get_pledge_stat, ts_code=ts_code, end_date=end_date) 
                        t9 = f(self.api.get_repurchase, ts_code=ts_code, start_date=start_date)
                        t10 = f(self.api.get_dividend, ts_code=ts_code, start_date=start_date)
                        
                        results = await asyncio.gather(t1, t2, t3, t4, t5, t6, t7, t8, t9, t10)
                        
                        # Save results if not empty
                        if results[0] is not None: await self.cache.save_financial_reports(results[0])
                        if results[1] is not None: await self.cache.save_financial_reports(results[1])
                        if results[2] is not None: await self.cache.save_financial_reports(results[2])
                        if results[3] is not None: await self.cache.save_financial_reports(results[3])
                        
                        if results[4] is not None: await self.cache.save_fina_audit(results[4])
                        if results[5] is not None: await self.cache.save_fina_forecast(results[5])
                        if results[6] is not None: await self.cache.save_fina_mainbz(results[6])
                        if results[7] is not None: await self.cache.save_pledge_stat(results[7])
                        if results[8] is not None: await self.cache.save_repurchase(results[8])
                        if results[9] is not None: await self.cache.save_dividend(results[9])

                    except (AttributeError, NameError, TypeError, ImportError) as e:
                        # Critical Programming Errors: FAIL FAST
                        # Do not swallow bugs. Let the program crash or bubble up so we know it's broken.
                        logger.critical(f"CRITICAL CODE BUG for {ts_code}: {e}")
                        raise e
                        
                    except Exception as e:
                        # Runtime Errors (Network, API limit, Bad Data): Log and Continue
                        logger.error(f"Failed Step 4 for {ts_code}: {e}")
                        
            except asyncio.CancelledError:
                # Task was cancelled, exit gracefully without releasing semaphore error
                return
            except RuntimeError as e:
                # Handle "Event loop is closed" error gracefully
                if "Event loop is closed" in str(e):
                    return
                raise

        # Batch Processing with proper task tracking
        batch_size = 50
        pending_list = stocks[start_index:]
        
        for i in range(0, len(pending_list), batch_size):
            if cancel_event and cancel_event.is_set(): break
            if self._shutdown_event.is_set(): break
            
            batch = pending_list[i : i+batch_size]
            tasks = [asyncio.create_task(process_one_stock(code)) for code in batch]
            
            # Register tasks for tracking (thread-safe)
            with self._tasks_lock:
                self._active_tasks.update(tasks)
            
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                # Cancel all pending tasks on cancellation
                for task in tasks:
                    if not task.done():
                        task.cancel()
                # Wait for cancellation to complete, suppress exceptions
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                # Unregister completed tasks (thread-safe)
                with self._tasks_lock:
                    self._active_tasks.difference_update(tasks)
            
            completed_count += len(batch)
            
            # Update Checkpoint
            try:
                with open(checkpoint_file, 'w') as f:
                    json.dump({'last_index': completed_count, 'total': total_stocks}, f)
            except: pass
            
            if progress_callback:
                progress_callback(completed_count, total_stocks, f"Fundamentals: {completed_count}/{total_stocks} ({pending_list[i]})")
                
        if completed_count >= total_stocks:
             if os.path.exists(checkpoint_file):
                 os.remove(checkpoint_file)

    async def _fetch_comprehensive_financial_data(self, ts_code, start_date=None, end_date=None, period=None):
        """
        Helper: Fetch and merge Income, Balance Sheet, and Financial Indicators.
        Tushare separates these into different APIs. We need to join them on end_date.
        """
        loop = asyncio.get_running_loop()
        
        # Define tasks for parallel fetching
        # We wrap them in executor to assume thread safety of TushareClient (requests based)
        t1 = loop.run_in_executor(None, lambda: self.api.get_income(ts_code=ts_code, start_date=start_date, end_date=end_date, period=period))
        t2 = loop.run_in_executor(None, lambda: self.api.get_balancesheet(ts_code=ts_code, start_date=start_date, end_date=end_date, period=period))
        t3 = loop.run_in_executor(None, lambda: self.api.get_fina_indicator(ts_code=ts_code, start_date=start_date, end_date=end_date, period=period))
        
        try:
            # Run parallel
            results = await asyncio.gather(t1, t2, t3, return_exceptions=True)
            
            df_inc, df_bal, df_fina = results[0], results[1], results[2]
            
            # Handle exceptions or empty
            dfs = []
            if isinstance(df_inc, pd.DataFrame) and not df_inc.empty:
                # Deduplicate by end_date, keep latest announcement
                dfs.append(df_inc.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))
            if isinstance(df_bal, pd.DataFrame) and not df_bal.empty:
                dfs.append(df_bal.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))
            if isinstance(df_fina, pd.DataFrame) and not df_fina.empty:
                dfs.append(df_fina.sort_values('end_date').drop_duplicates(subset=['end_date'], keep='last'))
                
            if not dfs:
                return None
                
            # Merge Strategy: Outer Join on ts_code + end_date
            # Base is the first non-empty DF
            df_merged = dfs[0]
            for i in range(1, len(dfs)):
                # Merge current df with accumulated result
                # Suffixes handles colliding columns (shouldn't be many except ann_date)
                df_merged = pd.merge(df_merged, dfs[i], on=['ts_code', 'end_date'], how='outer', suffixes=('', '_drop'))
                
            # Cleanup duplicate columns from merge
            for col in df_merged.columns:
                if col.endswith('_drop'):
                    df_merged.drop(columns=[col], inplace=True)
                    
            return df_merged
            
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to fetch comprehensive financial data for {ts_code}: {e}")
            return None

        # Create tasks
        tasks = [sync_one_stock(code, i) for i, code in enumerate(all_stock_codes)]
        await asyncio.gather(*tasks)

        current_period = now.strftime('%Y%m%d')
        await self.cache.update_sync_status('financial_reports', current_period, total_saved)
        
        logger.info(f"[DataProcessor] [OK] Per-Stock Financial Sync complete. Saved {total_saved} records. Errors: {total_errors}")
        return total_saved

    async def repair_financial_data(self, ts_codes, progress_callback=None):
        """
        Targeted repair for specific stocks using Safe Sequential Sync.
        Used by Health Check feature to fix 'limping' stocks.
        """
        if not ts_codes:
            return 0
            
        # Calculate periods (Last 12 quarters - 3 Years)
        now = datetime.datetime.now()
        current_year = now.year
        p_cands = []
        for y in range(current_year, current_year - 4, -1):
            p_cands.extend([f"{y}0331", f"{y}0630", f"{y}0930", f"{y}1231"])
        periods = sorted([p for p in p_cands if p < now.strftime('%Y%m%d')], reverse=True)[:12]
        
        logger.info(f"[DataProcessor] 🚑 Starting repair for {len(ts_codes)} stocks over {len(periods)} periods...")
        
        # Concurrency & Logic (Strict Safe Mode)
        semaphore = asyncio.Semaphore(1) 
        loop = asyncio.get_running_loop()
        
        total_saved = 0
        DELAY = 0.4 # Strict rate limit spacing
        
        for period_idx, period in enumerate(periods):
            logger.info(f"[DataProcessor] 🚑 Repairing period {period} ({period_idx+1}/{len(periods)})...")
            
            async def repair_one(ts_code, idx):
                nonlocal total_saved
                async with semaphore:
                    try:
                        await asyncio.sleep(DELAY)
                        
                        df = await loop.run_in_executor(
                            None, 
                            lambda: self.api.get_fina_indicator(period=period, ts_code=ts_code)
                        )
                        
                        if df is not None and not df.empty:
                            # Verify Schema
                            schema_cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                                         'n_income', 'n_income_attr_p', 'total_assets', 'total_liab', 
                                         'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin', 
                                         'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
                            
                            for col in schema_cols:
                                if col not in df.columns:
                                    df[col] = None
                                    
                            count = await self.cache.save_financial_reports(df[schema_cols])
                            total_saved += count
                        
                        # Progress update
                        if progress_callback and idx % 10 == 0:
                            current = period_idx * len(ts_codes) + idx
                            total = len(periods) * len(ts_codes)
                            progress_callback(current, total, f"Repairing {period} - {ts_code}")
                            
                    except Exception as e:
                        if "Limit" not in str(e):
                            logger.debug(f"[DataProcessor] Repair failed {ts_code}: {e}")

            # Execute sequentially
            for i, ts_code in enumerate(ts_codes):
                await repair_one(ts_code, i)
                if i % 50 == 0:
                    await asyncio.sleep(0.01)
                    
        logger.info(f"[DataProcessor] [OK] Repair complete. Saved {total_saved} missing records.")
        return total_saved

    async def sync_moneyflow(self, trade_date=None):
        """
        Sync money flow data for a specific date.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing money flow for {trade_date}...")
        loop = asyncio.get_running_loop()
        
        try:
            df = await loop.run_in_executor(None, lambda: self.api.get_moneyflow(trade_date=trade_date))
            if df is not None and not df.empty:
                count = await self.cache.save_moneyflow(df)
                await self.cache.update_sync_status('moneyflow_daily', trade_date, count)
                logger.info(f"[DataProcessor] [OK] Saved {count} money flow records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] [WARN] Error syncing money flow: {e}")
        return 0

    async def sync_northbound(self, trade_date=None):
        """
        Sync northbound holding data for a specific date.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing northbound holdings for {trade_date}...")
        loop = asyncio.get_running_loop()
        
        try:
            df = await loop.run_in_executor(None, lambda: self.api.get_hk_hold(trade_date=trade_date))
            if df is not None and not df.empty:
                # Filter for A-shares only (SH/SZ) to avoid Southbound HK stocks
                df = df[df['ts_code'].astype(str).str.endswith(('.SH', '.SZ'))]
                if df.empty:
                    logger.info(f"[DataProcessor] No Northbound A-share holdings found for {trade_date}")
                    return 0
                    
                count = await self.cache.save_northbound(df)
                await self.cache.update_sync_status('northbound_holding', trade_date, count)
                logger.info(f"[DataProcessor] [OK] Saved {count} northbound records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] [WARN] Error syncing northbound: {e}")
        return 0

    # sync_all_daily removed (use sync_daily_market_snapshot)


    async def prepare_screening_context(self):
        """
        Prepare context dictionary for strategies.
        Fetches latest merged data from cache.
        """
        context = {}
        
        # 1. Main merged daily data
        df = await self.get_screening_data()
        context['screening_data'] = df
        
        # 2. Northbound holding
        try:
            northbound_df = await self.cache.get_latest_northbound()
            context['northbound_data'] = northbound_df
        except:
            context['northbound_data'] = pd.DataFrame()

        # 3. LHB (Dragon Tiger Board) - fetching latest available
        # Note: LHB data is daily, we look for the latest trade date we have data for
        try:
            latest_date = await self.cache.get_latest_trade_date()
            if latest_date:
                lhb_df = await self.cache.get_top_list(trade_date=latest_date)
                context['top_list'] = lhb_df
            else:
                context['top_list'] = pd.DataFrame()
        except:
            context['top_list'] = pd.DataFrame()

        # 4. Block Trade
        try:
            latest_date = await self.cache.get_latest_trade_date()
            if latest_date:
                block_df = await self.cache.get_block_trade(trade_date=latest_date)
                context['block_trade'] = block_df
            else:
                context['block_trade'] = pd.DataFrame()
        except:
            context['block_trade'] = pd.DataFrame()
        
        return context

    async def full_historical_sync(self, years=3, progress_callback=None):
        """
        Full historical data sync for specified years.
        Default is 3 years (~750 trading days).
        
        :param years: Number of years to sync (default 3)
        :param progress_callback: Optional callback function(current, total, message)
        """
        days = int(years * 250)  # ~250 trading days per year
        print(f"[DataProcessor] === Starting {years}-year historical sync ({days} days) ===")
        
        # 1. Sync stock basic first
        await self.sync_stock_basic()
        
        # 2. Sync historical quotes and indicators
        await self.sync_historical_data(days=days, progress_callback=progress_callback)
        
        # 3. Sync financial reports (last 12 quarters for 3 years)
        periods = []
        now = datetime.datetime.now()
        for i in range(12):  # 12 quarters = 3 years
            quarter_end = now - datetime.timedelta(days=90 * i)
            year = quarter_end.year
            month = quarter_end.month
            if month <= 3:
                period = f"{year-1}1231"
            elif month <= 6:
                period = f"{year}0331"
            elif month <= 9:
                period = f"{year}0630"
            else:
                period = f"{year}0930"
            if period not in periods:
                periods.append(period)
        
        await self.sync_financial_reports(periods=periods)
        
        print(f"[DataProcessor] === {years}-year historical sync complete! ===")

    # ===== Screening Methods =====

    async def get_screening_data(self, trade_date=None):
        """
        Get merged data for screening from local cache.
        This is the main method used by screening strategies.
        """
        return await self.cache.get_screening_data(trade_date)


    async def get_strategy_data(self):
        """Facade method for UI"""
        return await self.prepare_screening_context()

    # ===== Backward Compatibility =====
    
    async def sync_financial_report(self, period):
        """Backward compatibility wrapper"""
        return await self.sync_financial_reports(periods=[period])

    # ========== Trade Calendar ==========
    async def ensure_trade_cal(self, end_date, required_start_date=None):
        """
        Ensure trade calendar is synced covers [required_start_date, end_date].
        Checks local DB first, fetches from API if needed.
        """
        try:
            # Check range in DB
            async with aiosqlite.connect(self.cache.db_path) as db:
                async with db.execute("SELECT MIN(cal_date), MAX(cal_date) FROM trade_cal") as cursor:
                    row = await cursor.fetchone()
                    min_db_date = row[0] if row and row[0] else None
                    max_db_date = row[1] if row and row[1] else None

            # Calculate defaults if not provided
            if required_start_date:
                target_start = required_start_date
            else:
                # Default: End of current year, trace back 4 years
                # e.g. 2026 -> Start 2022-01-01 (Matches user request for ~4 years history)
                curr_year = int(end_date[:4])
                target_start = datetime.date(curr_year - 4, 1, 1).strftime('%Y%m%d')
            
            # Scenario 1: DB is empty
            if not min_db_date or not max_db_date:
                 logger.info(f"[DataProcessor] Calendar cache empty. Initializing {target_start} to {end_date} (YearEnd)...")
                 await self._fetch_and_save_cal(target_start, end_date)
                 return

            # Scenario 2: Need older history (backward sync)
            if target_start < min_db_date:
                 logger.info(f"[DataProcessor] Calendar cache missing history. Fetching {target_start} to {min_db_date}...")
                 # Fetch up to min_db_date (exclusive? Tushare is inclusive, overlap is fine/safe)
                 await self._fetch_and_save_cal(target_start, min_db_date)

            # Scenario 3: Need future updates (forward sync)
            if max_db_date < end_date:
                 logger.info(f"[DataProcessor] Calendar cache outdated. Fetching {max_db_date} to {end_date} (YearEnd)...")
                 await self._fetch_and_save_cal(max_db_date, end_date)

        except Exception as e:
            logger.error(f"[DataProcessor] Error in ensure_trade_cal: {e}")

    async def _fetch_and_save_cal(self, start_str, end_str):
        """Helper to fetch and save calendar range"""
        try:
            # Logic to extend end_str to Year End
            current_year = int(end_str[:4])
            fetch_end = datetime.date(current_year, 12, 31).strftime('%Y%m%d')
            
            # Logic to extend start_str? Usually exact start is fine.
            # But if start_str is '20230520', maybe just fetch 20230101?
            # Tushare API handles arbitary range fast.
            
            # Overlap safety: If fetching 20210101-20220101, and DB has 20220101...
            # REPLACE INTO handles duplicates.
            
            # Adjust fetch_end if forward sync (max_date < end)
            # If backward sync, we usually just want up to what we explicitly missed.
            if end_str < fetch_end:
                 fetch_end = fetch_end # Always try to complete the year of the END date.
            
            df = await asyncio.to_thread(self.api.get_trade_cal, start_str, fetch_end)
            if df is not None and not df.empty:
                await self.cache.save_trade_cal(df)
                logger.debug(f"[DataProcessor] Synced calendar {len(df)} rows.")
        except Exception as e:
             logger.error(f"[DataProcessor] Failed to fetch calendar {start_str}-{end_str}: {e}")
    async def get_market_overview(self):
        """
        Get market overview data for Home Screen.
        Returns Indices (SH, SZ, CYB) and Northbound Money Flow.
        STRICT MODE: Uses 'date' as is. If data missing, returns empty placeholder.
        """
        try:
            # 1. Determine date: Use REAL latest trading date from API, ignore stale local cache
            # This ensures we try to fetch Today's data (or last Friday's) even if DB is old.
            try:
                now = datetime.datetime.now()
                now_str = now.strftime('%Y%m%d')
                start_str = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')
                
                # Smart Calendar Sync (Persistent Cache)
                # Ensures we have calendar data up to year-end or at least today
                await self.ensure_trade_cal(end_date=now_str)
                
                # Query strictly from local DB (now guaranteed to be synced)
                cache_df = await self.cache.get_trade_cal(start_date=start_str, end_date=now_str, is_open=1)
                
                dates = []
                if not cache_df.empty:
                    dates = sorted(cache_df['cal_date'].tolist())
                    logger.info(f"[DataProcessor] Using cached trade dates: {dates}")
                
                if dates:
                    # Trust the cached (originally from API) calendar
                    date = dates[-1]
                else:
                    logger.warning("[DataProcessor] No trade dates found in cache. Using Today.")
                    date = now_str

            except Exception as e:
                logger.warning(f"Failed to fetch trade calendar, defaulting to today: {e}")
                date = datetime.datetime.now().strftime('%Y%m%d')
            
            logger.info(f"Fetching market overview for target: {date}...")

            # 2. Fetch Indices (Real-time only, no cache fallback)
            # 000001.SH (Shanghai), 399001.SZ (Shenzhen), 399006.SZ (ChiNext)
            async def get_idx(code, name):
                df = await asyncio.to_thread(self.api.get_index_daily, ts_code=code, trade_date=date)
                
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    change = row.get('pct_chg', 0) or 0
                    close_val = row.get('close', 0) or 0
                    return {
                        'name': name,
                        'value': f"{close_val:.2f}" if close_val else '-',
                        'change': f"{change:+.2f}%" if change else '-',
                        'color': 'red' if change >= 0 else 'green' 
                    }
                return {'name': name, 'value': '-', 'change': '-', 'color': 'grey'}

            # Fetch Northbound Flow
            async def get_hsgt():
                df = await asyncio.to_thread(self.api.get_moneyflow_hsgt, trade_date=date)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    try:
                        val = float(row['north_money'])
                    except (ValueError, TypeError):
                        val = 0.0
                        
                    return {
                        'name': '北向资金',
                        'value': f"{val/100:.2f}亿" if abs(val) > 100 else f"{val:.0f}万",
                        'sub': "流入" if val > 0 else "流出"
                    }
                return {'name': '北向资金', 'value': '-', 'sub': '-'}
            
            # 4. Fetch Hot Concepts (Parallel with remaining indices)
            hot_concepts_task = NewsFetcher.get_hot_concepts(limit=8)

            # Run parallel
            results = await asyncio.gather(
                get_idx('000001.SH', '上证指数'),
                get_idx('399001.SZ', '深证成指'),
                get_idx('399006.SZ', '创业板指'),
                get_hsgt(),
                hot_concepts_task
            )
            
            idx_results = [results[0], results[1], results[2]]
            hsgt = results[3]
            hot_concepts = results[4]
            
            return {
                'date': date, 
                'indices': idx_results, 
                'hsgt': hsgt,
                'hot_concepts': hot_concepts
            }

        except Exception as e:
            logger.error(f"Failed to get market overview: {e}")
            return None

    async def sync_market_news(self, limit=None):
        """
        Fetch real-time market news and save to DB.
        Also cleans up legacy test data.
        
        :param limit: Number of news items to fetch. 
                      If None, defaults to 200 on first run (Deep Sync), 20 otherwise.
        """
        # Auto Deep Sync Logic
        if limit is None:
            if getattr(self, '_first_news_sync', True):
                limit = 200
                self._first_news_sync = False
                logger.info("[DataProcessor] Deep Sync triggered for startup (limit=200)")
            else:
                limit = 20
        
        logger.info(f"[DataProcessor] Syncing market news (limit={limit})...")
        try:
            # 1. Soft Cleanup (Optional): Remove very old news (> 30 days) to keep DB light
            # await self.cache.queue.put(("DELETE FROM market_news WHERE publish_time < date('now', '-30 days')", (), False))
            
            # 2. Fetch Real News
            # Use get_latest_global_news which fetches from Cailianshe/Major Portals
            news_items = await NewsFetcher.get_latest_global_news(limit=limit)
            
            if not news_items:
                logger.warning("[DataProcessor] No news fetched.")
                return 0
                
            count = 0
            for item in news_items:
                # Map fields to DB schema
                # DB: content, tags, publish_time, source
                # API: title, content, time
                
                title = item.get('title', '')
                content = item.get('content', '')
                time_str = str(item.get('time', ''))
                
                # Format: "Title: Content"
                full_content = f"{title}: {content}" if title and content else (title or content)
                
                # Check for "Test" in real content just in case? Unlikely.
                
                mapped_item = {
                    'content': full_content,
                    'tags': 'Market', # Default tag
                    'publish_time': time_str,
                    'source': 'CLS' # Cailianshe
                }
                
                await self.cache.save_market_news(mapped_item)
                count += 1
                
            logger.info(f"[DataProcessor] [OK] Synced {count} news items.")
            return count
            
        except Exception as e:
            logger.error(f"[DataProcessor] Error syncing market news: {e}")
            return 0

    async def get_stock_history(self, ts_code, days=365):
        """
        Get OHLC history for a stock for K-line chart.
        Default 365 days (approx 1.5 year of calendar days).
        """
        try:
            # Calculate date range
            end_date = datetime.datetime.now().strftime('%Y%m%d')
            start_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
            
            df = await self.cache.get_daily_quotes(ts_code=ts_code, start_date=start_date, end_date=end_date)
            
            if df is None or df.empty:
                return pd.DataFrame()
                
            # Make sure we have numeric types
            cols = ['open', 'high', 'low', 'close', 'vol']
            for col in cols:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors='coerce')
                
            # Sort ascending for chart (oldest first)
            return df.sort_values('trade_date', ascending=True)
        except Exception as e:
            logger.error(f"[DataProcessor] Error fetching history for {ts_code}: {e}")
            return pd.DataFrame()
