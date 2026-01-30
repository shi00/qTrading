import pandas as pd
import datetime
import asyncio
import logging
import traceback
from data.tushare_client import TushareClient
from data.news_fetcher import NewsFetcher
from data.cache_manager import CacheManager
from utils.config_handler import ConfigHandler

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
            return
        
        token = ConfigHandler.get_token()
        self.api = TushareClient(token=token)
        self.cache = CacheManager()
        self._first_news_sync = True
        self._initialized = True

    async def close(self):
        """Gracefully close resources"""
        if self.cache:
            await self.cache.close()

    def get_latest_trade_date(self):
        """
        Get most recent actual trading date (handles holidays via API).
        """
        now = datetime.datetime.now()
        # If before 16:00, target is yesterday maximum
        if now.hour < 16:
            end_dt = now - datetime.timedelta(days=1)
        else:
            end_dt = now
            
        end_str = end_dt.strftime('%Y%m%d')
        start_str = (end_dt - datetime.timedelta(days=15)).strftime('%Y%m%d')
        
        try:
            # 1. API Method (Accurate)
            dates = self.api.get_trade_dates(start_str, end_str)
            if dates:
                return dates[-1] # List is sorted asc
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to get latest trade date via API: {e}")

        # 2. Fallback Heuristic
        dt = end_dt
        while dt.weekday() >= 5: # Skip weekends
             dt -= datetime.timedelta(days=1)
        return dt.strftime('%Y%m%d')

    def get_trade_dates(self, start_date, end_date):
        """Get list of actual trade dates using Tushare trade_cal API"""
        try:
            # Use trade calendar API for accurate dates
            dates = self.api.get_trade_dates(start_date, end_date)
            if dates:
                return dates
        except Exception as e:
            logger.warning(f"[DataProcessor] Trade calendar failed, using fallback: {e}")
        
        # Fallback: simple weekend skip
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


    async def check_data_health(self):
        """
        Check data health status: Timeliness and Completeness (3 years).
        Returns:
            dict: {
                'status': 'green'|'yellow'|'red',
                'latest_date': str,
                'lag_days': int,
                'missing_count': int,
                'total_days': int,
                'missing_details': list,
                'coverage': str
            }
        """
        try:
            # 1. Official Calendar (Last 3 Years)
            end_date = self.get_latest_trade_date()
            start_date = (datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=365*3)).strftime('%Y%m%d')
            
            # Run blocking API call in executor
            loop = asyncio.get_running_loop()
            official_dates = await loop.run_in_executor(None, lambda: self.api.get_trade_dates(start_date, end_date))
            
            if not official_dates:
                return {'status': 'red', 'msg': 'Cannot get official calendar'}
                
            # Ensure ascending order for correct indexing
            official_dates.sort()
            official_set = set(official_dates)
            
            # 2. Local Cache
            local_dates = await self.cache.get_cached_trade_dates()
            
            # 3. Analyze logic
            # Filter official dates to only those <= end_date (should be handled by get_trade_dates but double check)
            
            # Calculate missing
            # Only care about missing dates that are in official_set
            missing_dates = sorted(list(official_set - local_dates))
            missing_count = len(missing_dates)
            total_days = len(official_set)
            
            # Timeliness check
            latest_official = official_dates[-1] if official_dates else end_date
            is_latest_synced = latest_official in local_dates
            
            lag_days = 0
            if not is_latest_synced:
                # Calculate lag: how many official trading days exist AFTER the last local date
                if not local_dates:
                    lag_days = total_days
                else:
                    last_local = sorted(list(local_dates))[-1]
                    lag_days = len([d for d in official_dates if d > last_local])
            
            # Status determination
            status = 'green'
            if not is_latest_synced:
                status = 'yellow'
                if lag_days > 3:
                    status = 'red'
            
            # Completeness check (3 years scope)
            # If missing count is significant
            if missing_count > 5: 
                status = 'red'
                
            return {
                'status': status,
                'latest_local': sorted(list(local_dates))[-1] if local_dates else 'None',
                'latest_official': latest_official,
                'lag_days': lag_days,
                'missing_count': missing_count,
                'total_days': total_days,
                'coverage': f"{(total_days - missing_count)/total_days*100:.1f}%" if total_days > 0 else "0%"
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

    async def sync_stock_basic(self):
        """Sync stock basic info"""
        logger.info("[DataProcessor] Syncing stock basic info...")
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(None, lambda: self.api.get_stock_list())
        if df is not None and not df.empty:
            count = await self.cache.save_stock_basic(df)
            await self.cache.update_sync_status('stock_basic', datetime.datetime.now().strftime('%Y%m%d'), count)
            logger.info(f"[DataProcessor] ✅ Saved {count} stocks to cache.")
            return count
        return 0

    async def sync_daily_market_snapshot(self, trade_date=None):
        """
        Sync daily market data (quotes + indicators) for a single date.
        This is the main method for daily updates.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] Syncing market snapshot for {trade_date}...")
        
        # Check if already cached
        cached_date = await self.cache.get_latest_trade_date()
        if cached_date == trade_date:
            logger.info(f"[DataProcessor] Cache hit! Data for {trade_date} already exists.")
            df = await self.cache.get_screening_data(trade_date)
            return df
        
        loop = asyncio.get_running_loop()
        
        # Fetch quotes and indicators in parallel
        logger.info(f"[DataProcessor] Fetching from Tushare API...")
        future_quotes = loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date))
        future_basic = loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date))
        
        df_quotes, df_basic = await asyncio.gather(future_quotes, future_basic)
        
        # Handle case where today's data is not ready
        today_str = datetime.datetime.now().strftime('%Y%m%d')
        # Logic removed: Do NOT silently fallback to yesterday if today is missing.
        # This causes Scheduler mismatches. If data is missing, we should report it.
        if trade_date == today_str and (df_quotes is None or df_quotes.empty):
             logger.warning(f"[DataProcessor] Today's data ({trade_date}) not ready or empty.")
             # We proceed, and it will be caught by "No quotes available" check below
        
        # Save to cache
        quotes_count = 0
        indicators_count = 0
        
        if df_quotes is not None and not df_quotes.empty:
            quotes_count = await self.cache.save_daily_quotes(df_quotes)
            await self.cache.update_sync_status('daily_quotes', trade_date, quotes_count)
            logger.info(f"[DataProcessor] ✅ Saved {quotes_count} daily quotes.")
        
        if quotes_count == 0:
            raise Exception("No quotes available to save (API returned empty or processing failed)")

        if df_basic is not None and not df_basic.empty:
            indicators_count = await self.cache.save_daily_indicators(df_basic)
            await self.cache.update_sync_status('daily_indicators', trade_date, indicators_count)
            logger.info(f"[DataProcessor] ✅ Saved {indicators_count} daily indicators.")
        
        # Return merged data for immediate use
        # Return merged data for immediate use
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
             target_date = self.get_latest_trade_date()
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

    async def sync_historical_data(self, days=365, progress_callback=None, cancel_event=None):
        """
        Sync historical data for the last N days using concurrency.
        Includes Circuit Breaker and Post-Sync Retry mechanism.
        """
        logger.info(f"[DataProcessor] Starting historical sync for last {days} days...")
        
        end_date = datetime.datetime.now().strftime('%Y%m%d')
        start_date = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        
        # Get actual trading dates (inverse order: new -> old)
        trade_dates = self.get_trade_dates(start_date, end_date)
        trade_dates.sort(reverse=True)
        
        # === Breakpoint Resume Logic ===
        try:
            # 1. Get dates that have Quotes
            cached_quotes_dates = await self.cache.get_cached_trade_dates()
            # 2. Get dates that have Indicators
            cached_ind_dates = await self.cache.get_cached_indicator_dates()
            # 3. Intersection: Dates that have BOTH
            existing_dates = cached_quotes_dates.intersection(cached_ind_dates)
            
            # 4. Filter out existing dates
            original_count = len(trade_dates)
            trade_dates = [d for d in trade_dates if d not in existing_dates]
            skipped_count = original_count - len(trade_dates)
            
            if skipped_count > 0:
                logger.info(f"[DataProcessor] ⏭️ Breakpoint resume: Skipped {skipped_count} already synced days.")
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to check cached dates: {e}, will sync all.")

        total_days = len(trade_dates)
        logger.info(f"[DataProcessor] Found {total_days} missing trading dates to sync.")
        
        # Concurrency control
        # Default 5, configurable
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(max(1, concurrency))
        logger.info(f"[DataProcessor] Sync concurrency set to {concurrency}")
        
        # Failure tracking
        failed_dates = []
        # Circuit breaker threshold (e.g. 20 failures or 20% if total is large)
        CB_THRESHOLD = max(20, int(total_days * 0.1) if total_days > 0 else 20)
        abort_sync = False
        
        async def sync_one_day_safe(date, idx):
            nonlocal abort_sync
            if cancel_event and cancel_event.is_set():
                return
            if abort_sync:
                return

            async with semaphore:
                if cancel_event and cancel_event.is_set() or abort_sync:
                    return
                    
                try:
                    # Check circuit breaker
                    if len(failed_dates) > CB_THRESHOLD:
                        abort_sync = True
                        logger.error(f"[DataProcessor] ⛔ Circuit Breaker triggered! Too many failures ({len(failed_dates)}). Aborting sync.")
                        return

                    await self.sync_daily_market_snapshot(date)
                    
                    if progress_callback:
                        progress_callback(idx + 1, total_days, f"Syncing {date} ...")
                except Exception as e:
                    logger.error(f"Failed to sync {date}: {e}")
                    failed_dates.append(date)

        # Create tasks
        tasks = []
        for i, date in enumerate(trade_dates):
            if cancel_event and cancel_event.is_set() or abort_sync:
                break
            tasks.append(sync_one_day_safe(date, i))
            
        # Run main batch
        if tasks:
            await asyncio.gather(*tasks)
            
        if cancel_event and cancel_event.is_set():
            logger.warning("[DataProcessor] Sync cancelled by user.")
            return 0
            
        if abort_sync:
            logger.error("[DataProcessor] Sync aborted due to massive failures (Circuit Breaker).")
            return len(trade_dates) - len(failed_dates)

        # === Smart Retry Mechanism ===
        if failed_dates:
            MAX_RETRIES = 3
            logger.info(f"[DataProcessor] ⚠️ Main sync finished with {len(failed_dates)} failures. Starting Smart Retry (Max {MAX_RETRIES} rounds)...")
            
            for retry_round in range(MAX_RETRIES):
                if not failed_dates or (cancel_event and cancel_event.is_set()):
                    break
                
                logger.info(f"[DataProcessor] 🔄 Retry Round {retry_round+1}/{MAX_RETRIES} for {len(failed_dates)} dates...")
                # Sleep briefly to let API/Network recover
                await asyncio.sleep(2)
                
                current_batch = failed_dates[:]
                failed_dates = [] # Clear for this round
                
                # Retry sequentially or with small concurrency to allow recovery
                # Use a smaller semaphore for retries
                retry_sem = asyncio.Semaphore(2) 
                
                async def retry_one(date):
                    async with retry_sem:
                        try:
                            await self.sync_daily_market_snapshot(date)
                            logger.info(f"[DataProcessor] ✅ Retry success for {date}")
                        except Exception as e:
                            logger.warning(f"[DataProcessor] ❌ Retry failed for {date}: {e}")
                            failed_dates.append(date) # Re-add to queue
                
                retry_tasks = [retry_one(d) for d in current_batch]
                await asyncio.gather(*retry_tasks)
                
        if failed_dates:
            logger.error(f"[DataProcessor] ❌ Sync completed but {len(failed_dates)} dates still failed after retries: {failed_dates}")
        else:
            logger.info("[DataProcessor] ✅ All failed dates recovered successfully!")

        logger.info("[DataProcessor] ✅ Historical sync final complete.")
        return total_days - len(failed_dates)

    async def sync_financial_reports(self, periods=None, progress_callback=None, force=False):
        """
        Sync financial reports using Hybrid Strategy (Full vs Incremental).
        
        :param periods: Explicit periods to sync (triggers Full Sync mode).
        :param force: Force Full Sync even if incremental applies.
        """
        # Decisions:
        # 1. If periods is specified -> Users wants specific data -> Full Sync those periods
        # 2. If force=True -> Full Sync
        # 3. If First Run (no local data) -> Full Sync
        # 4. Otherwise -> Incremental Sync (Day by Day)
        
        should_full_sync = False
        if periods is not None:
            should_full_sync = True
            logger.info("[DataProcessor] Manual periods specified -> Using Full Sync Mode")
        elif force:
            should_full_sync = True
            logger.info("[DataProcessor] Force=True -> Using Full Sync Mode")
        else:
            # Check if we have any financial data
            status = await self.cache.get_sync_status('financial_reports')
            if not status or not status.get('last_sync_date'):
                should_full_sync = True
                logger.info("[DataProcessor] First run (no history) -> Using Full Sync Mode")
        
        if should_full_sync:
            return await self._run_full_sync(periods, progress_callback)
        else:
            return await self._run_incremental_sync(progress_callback)

    async def _run_incremental_sync(self, progress_callback=None):
        """
        Incremental Sync: Query 'disclosure_date' to find *only* updated stocks.
        """
        logger.info("[DataProcessor] 🚀 Starting Incremental Financial Sync...")
        
        status = await self.cache.get_sync_status('financial_reports')
        last_sync_str = status.get('last_sync_date')
        
        # Parse last sync date (e.g. 2025-01-01 12:00:00)
        try:
            last_sync_dt = datetime.datetime.strptime(last_sync_str, '%Y-%m-%d %H:%M:%S')
            start_date_dt = last_sync_dt + datetime.timedelta(days=1)
        except:
            # Fallback (shouldn't happen due to check in main method)
            logger.warning("[DataProcessor] Error parsing last sync date, falling back to 30 days ago")
            start_date_dt = datetime.datetime.now() - datetime.timedelta(days=30)
            
        today_dt = datetime.datetime.now()
        
        # Generate date range [last_sync + 1, today]
        dates_to_sync = []
        curr = start_date_dt
        while curr.date() <= today_dt.date():
            dates_to_sync.append(curr.strftime('%Y%m%d'))
            curr += datetime.timedelta(days=1)
            
        if not dates_to_sync:
            logger.info("[DataProcessor] Data is already up to date.")
            return 0
            
        logger.info(f"[DataProcessor] Incremental sync needs to cover {len(dates_to_sync)} days: {dates_to_sync[0]} to {dates_to_sync[-1]}")
        
        total_saved = 0
        loop = asyncio.get_running_loop()
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(concurrency)
        
        # 1. Identify Target Stocks Day by Day
        # We process day by day to respect 'actual_date' accuracy
        
        for day_str in dates_to_sync:
            logger.info(f"[DataProcessor] Checking disclosures for {day_str}...")
            
            # Fetch list of stocks that published reports on this day
            df_disclosure = await loop.run_in_executor(None, lambda: self.api.get_disclosure_date(date=day_str))
            
            if df_disclosure is None or df_disclosure.empty:
                logger.debug(f"No disclosures on {day_str}")
                continue
                
            # Extract unique ts_codes and their periods
            # Data: ts_code, ann_date, end_date, actual_date
            # We need to sync (ts_code, period) pairs. 
            # Note: actual_date should match day_str
            
            target_list = df_disclosure[['ts_code', 'end_date']].drop_duplicates().to_dict('records')
            logger.info(f"[DataProcessor] Found {len(target_list)} reports released on {day_str}")
            
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
                        
                        df = await loop.run_in_executor(
                            None, 
                            lambda: self.api.get_fina_indicator(period=period, ts_code=ts_code)
                        )
                        
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
                progress_callback(0, 0, f"Completed incremental sync for {day_str}")

        # Update status to NOW
        await self.cache.update_sync_status('financial_reports', datetime.datetime.now().strftime('%Y%m%d'), total_saved)
        logger.info(f"[DataProcessor] ✅ Incremental sync complete. Total new records: {total_saved}")
        return total_saved


    async def _run_full_sync(self, periods=None, progress_callback=None):
        """
        Original Full Sync Logic (Renamed).
        Syncs list of periods for ALL stocks.
        """
        if periods is None:
            # Calculate last 4 quarter end dates
            now = datetime.datetime.now()
            periods = []
            for i in range(4):
                quarter_end = now - datetime.timedelta(days=90 * (i + 1))
                year = quarter_end.year
                month = quarter_end.month
                # Map to quarter end: Q1=0331, Q2=0630, Q3=0930, Q4=1231
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
        
        logger.info(f"[DataProcessor] Syncing financial reports for periods: {periods}")
        
        # Get stock list first
        stock_df = await self.cache.get_stock_basic()
        if stock_df is None or stock_df.empty:
            logger.warning("[DataProcessor] No stock list found. Syncing stock basic first...")
            await self.sync_stock_basic()
            stock_df = await self.cache.get_stock_basic()
        
        if stock_df is None or stock_df.empty:
            logger.error("[DataProcessor] Failed to get stock list for financial sync.")
            return 0
        
        all_stock_codes = stock_df['ts_code'].tolist()
        total_stocks_all = len(all_stock_codes)
        
        # === Breakpoint Resume: Get all cached financial records ===
        try:
            cached_records = await self.cache.get_cached_financial_records()
            logger.info(f"[DataProcessor] Found {len(cached_records)} cached financial records")
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to get cached records: {e}, syncing all")
            cached_records = set()
        
        loop = asyncio.get_running_loop()
        total_saved = 0
        total_skipped = 0
        
        # Use configurable concurrency (rely on smart retry for rate limits)
        concurrency = ConfigHandler.get_sync_concurrency()
        semaphore = asyncio.Semaphore(concurrency)
        logger.info(f"[DataProcessor] Financial sync concurrency: {concurrency}")
        
        # Track failures
        failed_stocks = []
        
        async def sync_one_stock(ts_code, idx, period):
            """Sync financial data for a single stock and period"""
            nonlocal total_saved
            
            async with semaphore:
                try:
                    # Add small delay to avoid rate limits
                    await asyncio.sleep(0.1)
                    
                    # Fetch data for this stock
                    df_indicator = await loop.run_in_executor(
                        None, 
                        lambda: self.api.get_fina_indicator(period=period, ts_code=ts_code)
                    )
                    
                    if df_indicator is None or df_indicator.empty:
                        return 0
                    
                    # Prepare data
                    ind_cols = ['ts_code', 'end_date', 'roe', 'roe_dt', 'debt_to_assets', 
                               'netprofit_margin', 'grossprofit_margin', 
                               'or_yoy', 'netprofit_yoy']
                    
                    for col in ind_cols:
                        if col not in df_indicator.columns:
                            df_indicator[col] = None
                    
                    # Add missing schema columns
                    schema_cols = ['ts_code', 'end_date', 'ann_date', 'report_type', 'total_revenue', 'revenue',
                                 'n_income', 'n_income_attr_p', 'total_assets', 'total_liab', 
                                 'total_hldr_eqy_exc_min_int', 'roe', 'roe_dt', 'grossprofit_margin', 
                                 'netprofit_margin', 'debt_to_assets', 'or_yoy', 'netprofit_yoy']
                    
                    for col in schema_cols:
                        if col not in df_indicator.columns:
                            df_indicator[col] = None
                    
                    # Save
                    count = await self.cache.save_financial_reports(df_indicator[schema_cols])
                    total_saved += count
                    return count
                    
                except Exception as e:
                    if "每分钟" not in str(e) and "抱歉" not in str(e):  # Not a rate limit
                        logger.debug(f"[DataProcessor] Failed {ts_code}: {e}")
                    failed_stocks.append((ts_code, period))
                    return 0
        
        # Process each period
        for period_idx, period in enumerate(periods):
            # === Breakpoint Resume: Filter out already cached stocks for this period ===
            stocks_to_sync = [
                ts_code for ts_code in all_stock_codes 
                if (ts_code, period) not in cached_records
            ]
            skipped_count = total_stocks_all - len(stocks_to_sync)
            total_skipped += skipped_count
            
            if skipped_count > 0:
                logger.info(f"[DataProcessor] ⏭️ Period {period}: Skipped {skipped_count} already synced, {len(stocks_to_sync)} remaining")
            
            if not stocks_to_sync:
                logger.info(f"[DataProcessor] ✅ Period {period}: All stocks already synced, skipping entirely")
                continue
            
            logger.info(f"[DataProcessor] 📊 Processing period {period} ({period_idx+1}/{len(periods)}), {len(stocks_to_sync)} stocks...")
            
            # Create tasks only for stocks that need syncing
            tasks = []
            for i, ts_code in enumerate(stocks_to_sync):
                tasks.append(sync_one_stock(ts_code, i, period))
                
                # Update progress periodically
                if progress_callback and i % 100 == 0:
                    overall_progress = period_idx * total_stocks_all + (skipped_count + i)
                    overall_total = len(periods) * total_stocks_all
                    progress_callback(overall_progress, overall_total, f"Syncing {period} - {ts_code}")
            
            # Run batch
            await asyncio.gather(*tasks)
            
            logger.info(f"[DataProcessor] ✅ Period {period} complete. Total saved so far: {total_saved}")
            
            # Brief pause between periods
            await asyncio.sleep(1)
        
        # Final progress update
        if progress_callback:
            progress_callback(len(periods) * total_stocks_all, len(periods) * total_stocks_all, "Financial data sync complete")
        
        if failed_stocks:
            logger.warning(f"[DataProcessor] ⚠️ {len(failed_stocks)} stock-period pairs failed (likely no data)")
        
        if total_skipped > 0:
            logger.info(f"[DataProcessor] ⏭️ Breakpoint resume: Skipped {total_skipped} already cached records")
        
        await self.cache.update_sync_status('financial_reports', periods[0] if periods else '', total_saved)
        logger.info(f"[DataProcessor] ✅ Financial sync complete! Total: {total_saved} new records")
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
                logger.info(f"[DataProcessor] ✅ Saved {count} money flow records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] ⚠️ Error syncing money flow: {e}")
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
                logger.info(f"[DataProcessor] ✅ Saved {count} northbound records.")
                return count
        except Exception as e:
            logger.error(f"[DataProcessor] ⚠️ Error syncing northbound: {e}")
        return 0

    async def sync_all_daily(self, trade_date=None):
        """
        Sync all daily data: quotes, indicators, moneyflow, northbound.
        This is the main method for daily updates.
        """
        if trade_date is None:
            trade_date = self.get_latest_trade_date()
        
        logger.info(f"[DataProcessor] === Full daily sync for {trade_date} ===")
        
        results = {
            'quotes': 0,
            'indicators': 0,
            'moneyflow': 0,
            'northbound': 0
        }
        
        # Sync in parallel
        loop = asyncio.get_running_loop()
        
        # Fetch all data types
        # Fetch all data types in parallel
        logger.info(f"[DataProcessor] Fetching extended data from Tushare API...")
        futures = [
            loop.run_in_executor(None, lambda: self.api.get_daily_quotes(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_daily_basic(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_moneyflow(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_hk_hold(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_top_list(trade_date=trade_date)),
            loop.run_in_executor(None, lambda: self.api.get_block_trade(trade_date=trade_date)),
        ]
        
        results = await asyncio.gather(*futures, return_exceptions=True)
        df_quotes, df_basic, df_mf, df_north, df_lhb, df_block = results
        
        saved_counts = {}
        
        # Save each type (handling exceptions)
        if isinstance(df_quotes, pd.DataFrame) and not df_quotes.empty:
            saved_counts['quotes'] = await self.cache.save_daily_quotes(df_quotes)
            
        if isinstance(df_basic, pd.DataFrame) and not df_basic.empty:
            saved_counts['indicators'] = await self.cache.save_daily_indicators(df_basic)
            
        if isinstance(df_mf, pd.DataFrame) and not df_mf.empty:
            saved_counts['moneyflow'] = await self.cache.save_moneyflow(df_mf)
            
        if isinstance(df_north, pd.DataFrame) and not df_north.empty:
            saved_counts['northbound'] = await self.cache.save_northbound(df_north)
            
        if isinstance(df_lhb, pd.DataFrame) and not df_lhb.empty:
            saved_counts['top_list'] = await self.cache.save_top_list(df_lhb)
            
        if isinstance(df_block, pd.DataFrame) and not df_block.empty:
            saved_counts['block_trade'] = await self.cache.save_block_trade(df_block)
        
        logger.info(f"[DataProcessor] === Sync complete: {saved_counts} ===")
        return saved_counts


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
                now_str = datetime.datetime.now().strftime('%Y%m%d')
                start_str = (datetime.datetime.now() - datetime.timedelta(days=20)).strftime('%Y%m%d')
                
                # Fetch valid trading dates from Tushare Calendar
                dates = await asyncio.to_thread(self.api.get_trade_dates, start_str, now_str)
                if dates and len(dates) > 0:
                    date = dates[-1] # The most recent trading day
                else:
                    date = now_str # Fallback to today
            except Exception as e:
                logger.warning(f"Failed to fetch trade calendar, defaulting to today: {e}")
                date = datetime.datetime.now().strftime('%Y%m%d')
            
            logger.info(f"Fetching market overview for target: {date}...")

            # 2. Fetch Indices
            # 000001.SH (Shanghai), 399001.SZ (Shenzhen), 399006.SZ (ChiNext)
            async def get_idx(code, name):
                df = await asyncio.to_thread(self.api.get_index_daily, ts_code=code, trade_date=date)
                
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    change = row['pct_chg']
                    return {
                        'name': name,
                        'value': f"{row['close']:.2f}",
                        'change': f"{change:+.2f}%",
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
                logger.info("[DataProcessor] 🚀 Deep Sync triggered for startup (limit=200)")
            else:
                limit = 20
        
        logger.info(f"[DataProcessor] Syncing market news (limit={limit})...")
        try:
            # 1. Cleanup Test Data (safe to do frequently, it's fast)
            # Remove any news with content like "Test News Item%"
            await self.cache.queue.put(("DELETE FROM market_news WHERE content LIKE 'Test News Item%'", (), False))
            
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
                
            logger.info(f"[DataProcessor] ✅ Synced {count} news items.")
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
