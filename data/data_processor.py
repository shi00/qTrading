import asyncio
import datetime
import logging
import threading
import time

import pandas as pd

from data.cache_manager import CacheManager
from data.constants import (
    MARKET_CLOSE_HOUR,
    HEALTH_THRESHOLD_FINANCIAL_COVERAGE,
    HEALTH_THRESHOLD_MARKET_LAG_DAYS,
    HEALTH_DEPTH_FULL_TRADE_DAYS,
    HEALTH_DEPTH_SAFETY_MULTIPLIER,
    HEALTH_THRESHOLD_BREADTH,
    TIER_QUOTE_FRESHNESS_DAYS,
    TIER_FINANCIAL_FRESHNESS_DAYS
)
from data.data_dictionary import TABLE_DEFINITIONS
from strategies.base_strategy import _STRATEGY_REGISTRY
from data.data_quality import DataQualityService
from data.news_fetcher import NewsFetcher
from data.sync_strategies.base import SyncContext
from data.sync_strategies.financial import FinancialSyncStrategy
from data.sync_strategies.historical import HistoricalSyncStrategy
from data.sync_strategies.macro import MacroSyncStrategy
from data.sync_strategies.holder import HolderSyncStrategy
from data.tushare_client import TushareClient
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.log_decorators import log_async_operation
from utils.thread_pool import ThreadPoolManager, TaskType

logger = logging.getLogger(__name__)


class DataProcessor:
    """
    Main data processing class (Refactored Facade).
    Delegates complex sync logic to specific Strategies.
    Safeguarded with strict Singleton pattern.
    """
    _instance = None
    _lock = threading.Lock()
    _is_initialized = False

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        # Double-check initialization state with lock to prevent race conditions
        if self.__class__._is_initialized:
            # Check if token has changed since initialization
            current_token = ConfigHandler.get_token()
            if hasattr(self, '_current_token') and current_token != self._current_token:
                self.refresh_token(current_token)
            return

        with self.__class__._lock:
            if self.__class__._is_initialized:
                return

            self._health_cache = {'time': 0, 'data': None}

            token = ConfigHandler.get_token()
            self._current_token = token
            self.api = TushareClient(token=token)
            self.cache = CacheManager()
            self._first_news_sync = True
            self._cancel_event = None  # ST-01: Lazy initialization to avoid loop binding issues
            self._quality_tier = None  # None=Uninitialized, 0=Critical, 1=Bronze, 2=Silver, 3=Gold

            # Initialize Context & Strategies
            self.context = SyncContext(api=self.api, cache=self.cache, config=ConfigHandler)
            self.strategies = {
                'financial': FinancialSyncStrategy(self.context),
                'historical': HistoricalSyncStrategy(self.context),
                'macro': MacroSyncStrategy(self.context),
                'holder': HolderSyncStrategy(self.context)
            }

            # Memory Cache for high-frequency small data
            self._trade_cal_cache = {}  # Cache structure: {'start': str, 'end': str, 'df': DataFrame}

            # Concurrency Control (Cross-Loop safe) - kept for basic locking if needed
            self._sync_lock = threading.Lock()
            self._is_syncing_basic = False

            self.__class__._is_initialized = True

    def _get_cancel_event(self):
        """Get or create cancel event dynamically per event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.Event()

        if not hasattr(current_loop, '_processor_cancel_evt'):
            setattr(current_loop, '_processor_cancel_evt', asyncio.Event())

        return getattr(current_loop, '_processor_cancel_evt')

    async def request_cancel(self):
        """
        Request cancellation of current operation.
        Called by UI when user clicks cancel or closes window.
        """
        logger.info("[DataProcessor] Cancel requested")
        self._get_cancel_event().set()
        self._quality_tier = None  # Reset to uninitialized; will re-evaluate on next strategy run

        # Propagate to all strategies
        for name, strategy in self.strategies.items():
            try:
                await strategy.cancel()
                logger.debug(f"[DataProcessor] Cancelled strategy: {name}")
            except Exception as e:
                logger.warning(f"[DataProcessor] Failed to cancel {name}: {e}")

    def is_cancelled(self):
        """Check if cancellation has been requested."""
        return self._get_cancel_event().is_set()

    def clear_cancel(self):
        """Clear cancel state before starting new operation."""
        self._get_cancel_event().clear()

    async def stop(self):
        """Signal all running tasks to stop. Async to ensure proper cleanup."""
        logger.info("[DataProcessor] Global stop signal received.")
        self._get_cancel_event().set()

        # Delegate cancellation to strategies
        try:
            tasks = []
            for name, strategy in self.strategies.items():
                tasks.append(strategy.cancel())

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
                logger.info("[DataProcessor] All strategies cancelled.")

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
        try:
            await self.stop()
            # Give pending tasks a moment to catch cancellation and release DB locks
            await asyncio.sleep(1.0)

            if self.cache:
                await self.cache.close()
        except Exception as e:
            logger.error(f"[DataProcessor] Error during close: {e}")

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
        result = await self.strategies['financial'].run(periods=periods, force=force,
                                                        progress_callback=progress_callback)
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

        # Clear caches to ensure fresh data visibility
        if hasattr(self, '_trade_date_cache'):
            self._trade_date_cache = {'ts': 0, 'val': None}
        self.get_trade_dates.cache_clear()

        # For compatibility with some callers expecting data, we fetch it back from cache
        return await self.get_screening_data(trade_date)

    # ==========================================
    # Core Logic (Business/Orchestration)
    # ==========================================

    # CR-02: Use manual TTL cache (5 min) instead of infinite alru_cache
    # @alru_cache(maxsize=1) 
    @log_async_operation(operation_name="get_latest_trade_date", log_exceptions=True)
    async def get_latest_trade_date(self):
        """Get absolute latest trading date (today or previous trading day)."""
        # Initialize cache if missing (guard for edge-case hot paths)
        if not hasattr(self, '_trade_date_cache'):
            self._trade_date_cache = {'ts': 0, 'val': None}

        now_ts = time.time()
        if self._trade_date_cache['val'] and (now_ts - self._trade_date_cache['ts'] < 300):
            return self._trade_date_cache['val']

        now = datetime.datetime.now()
        if now.hour < MARKET_CLOSE_HOUR:
            end_dt = now - datetime.timedelta(days=1)
        else:
            end_dt = now

        end_str = end_dt.strftime('%Y%m%d')
        start_str = (end_dt - datetime.timedelta(days=20)).strftime('%Y%m%d')

        try:
            dates = await self.get_trade_dates(start_str, end_str)
            if dates:
                result = dates[-1]
                # Update cache
                self._trade_date_cache = {'ts': now_ts, 'val': result}
                return result
        except Exception as e:
            logger.warning(f"[DataProcessor] Failed to get latest trade date: {e}")

        # Fallback
        dt = end_dt
        while dt.weekday() >= 5:
            dt -= datetime.timedelta(days=1)
        fallback_res = dt.strftime('%Y%m%d')
        return fallback_res

    @log_async_operation(operation_name="get_trade_dates", log_exceptions=True)
    async def get_trade_dates(self, start_date, end_date):
        """Get list of trade dates between start and end."""
        try:
            await self.ensure_trade_cal(end_date, required_start_date=start_date)
            # Use strict type check for safety with generic read
            cache_df = await self.cache.get_trade_cal(start_date=start_date, end_date=end_date, is_open=1)

            if not cache_df.empty:
                # Polars or Pandas? CacheManager returns DataFrame (Pandas)
                # Ensure it's list of strings
                return sorted(cache_df['cal_date'].astype(str).tolist())
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

    @log_async_operation(operation_name="init_data")
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
                return True, I18n.get("status_never_synced")

            last_sync = datetime.datetime.strptime(status.get('last_sync_date'), '%Y-%m-%d %H:%M:%S')
            days_since = (datetime.datetime.now() - last_sync).days

            if days_since >= 30:
                return True, I18n.get("status_days_ago", days=days_since)

            current_month = datetime.datetime.now().month
            if current_month in [1, 4, 7, 10]:  # Earnings season
                if days_since >= 7:
                    return True, I18n.get("status_earnings_season")

            return False, I18n.get("status_recent")
        except Exception as e:
            return True, f"error: {e}"

    async def _assign_basic_tier(self):
        """
        Fast-path to assign a basic quality tier (Bronze/Silver/Gold) without 
        scanning actual table counts. It relies solely on the `sync_status` table.
        Used primarily during silent startup.
        
        Tier Logic:
          - CRITICAL (0): No sync_status records at all, or daily_quotes never synced.
          - BRONZE  (1): daily_quotes exists but is stale (> TIER_QUOTE_FRESHNESS_DAYS lag).
          - SILVER  (2): daily_quotes is fresh. Sufficient for MA/RSI strategies.
          - GOLD    (3): daily_quotes fresh AND financial_reports recent (< TIER_FINANCIAL_FRESHNESS_DAYS).
        """
        try:
            sync_records = await self.cache.get_sync_status()

            # _read_db returns a pandas DataFrame
            if sync_records is None or (hasattr(sync_records, 'empty') and sync_records.empty):
                self._quality_tier = 0
                logger.warning("[Fast Health Check] No sync records found. Tier = CRITICAL (0)")
                return

            # Convert to dictionary for easy lookup: {table_name: row_dict}
            sync_dict = sync_records.set_index('table_name').to_dict('index')
            logger.info(f"[Fast Health Check] Found sync records for: {list(sync_dict.keys())}")

            # ── SILVER gate: only requires daily_quotes to be fresh ──
            # This is the ONLY hard requirement for RSI/MA strategies.
            # financial_reports is optional (upgrades to GOLD if present & fresh).
            latest_quote_date = sync_dict.get('daily_quotes', {}).get('last_data_date', '')
            
            # Fast verification: if sync_status is missing or stale, double check actual table MAX(date)
            try:
                if not latest_quote_date:
                    db_max_date = await self.cache.get_latest_trade_date()
                    if db_max_date:
                        latest_quote_date = str(db_max_date)
            except Exception as e:
                logger.warning(f"[Fast Health Check] Failed to fallback to DB max date: {e}")

            if not latest_quote_date:
                self._quality_tier = 1
                logger.warning("[Fast Health Check] daily_quotes has no last_data_date. Tier = BRONZE (1)")
                return

            try:
                latest_dt = datetime.datetime.strptime(str(latest_quote_date), '%Y%m%d')
                days_lag = (datetime.datetime.now() - latest_dt).days
                logger.info(f"[Fast Health Check] daily_quotes last_data_date={latest_quote_date}, lag={days_lag}d")
                
                # Double check actual table if sync_status claims it's stale (sync_status could be out of sync with DB)
                if days_lag > TIER_QUOTE_FRESHNESS_DAYS:
                    logger.info("[Fast Health Check] sync_status points to stale data. Verifying actual DB table...")
                    try:
                        db_max_date = await self.cache.get_latest_trade_date()
                        if db_max_date:
                            latest_dt = datetime.datetime.strptime(str(db_max_date), '%Y%m%d')
                            days_lag = (datetime.datetime.now() - latest_dt).days
                            logger.info(f"[Fast Health Check] Corrected with DB MAX(trade_date)={db_max_date}, lag={days_lag}d")
                    except Exception as e:
                        logger.warning(f"[Fast Health Check] Fallback DB verification skipped: {e}")
                        
            except (ValueError, TypeError) as e:
                self._quality_tier = 1
                logger.warning(f"[Fast Health Check] Cannot parse date '{latest_quote_date}': {e}. Tier = BRONZE (1)")
                return

            if days_lag <= TIER_QUOTE_FRESHNESS_DAYS:
                self._quality_tier = 2  # SILVER — safe for MA/RSI

                # Optional upgrade to GOLD if financial data is also fresh
                fin_info = sync_dict.get('financial_reports', {})
                fin_date = fin_info.get('last_data_date', '') if fin_info else ''
                if fin_date:
                    try:
                        fin_lag = (datetime.datetime.now() - datetime.datetime.strptime(str(fin_date), '%Y%m%d')).days
                        if fin_lag < TIER_FINANCIAL_FRESHNESS_DAYS:
                            self._quality_tier = 3  # GOLD
                    except (ValueError, TypeError):
                        pass  # Stay at SILVER, no downgrade
            else:
                self._quality_tier = 1  # Stale quotes -> BRONZE

            logger.info(f"[Fast Health Check] Final Quality Tier = {self._quality_tier}")
        except Exception as e:
            logger.error(f"[Fast Health Check] Failed: {e}", exc_info=True)
            # If we can't even read metadata, be conservative but don't block everything
            self._quality_tier = 1

    @log_async_operation(operation_name="check_data_health", log_result=True)
    async def check_data_health(self):
        """Check data health status. Read-only diagnostic — immune to sync cancellation."""
        import time
        now = time.time()
        # 10s cache to prevent double-tap on startup
        if self._health_cache.get('data') and (now - self._health_cache.get('time', 0) < 10):
            return self._health_cache['data']

        # PF-01: Removed redundant init_db call. Database is initialized at startup.
        # await self.cache.init_db()

        try:
            end_date = await self.get_latest_trade_date()
            # Generate start date 3 years ago
            start_date_obj = datetime.datetime.strptime(end_date, '%Y%m%d') - datetime.timedelta(days=365 * 3)
            start_date = start_date_obj.strftime('%Y%m%d')

            official_dates = await self.get_trade_dates(start_date, end_date)

            if not official_dates:
                return {'status': 'red', 'msg': I18n.get('health_err_calendar')}

            local_dates = await self.cache.get_cached_trade_dates()

            # 1. Market Health
            last_local = sorted(list(local_dates))[-1] if local_dates else None

            lag_days = 0
            # If latest official date is not in local cache, calculate lag
            if official_dates and (not local_dates or official_dates[-1] > last_local):
                if local_dates and last_local:
                    # Count business days lag
                    lag_days = len([d for d in official_dates if d > last_local])
                else:
                    # No local data, lag is total days
                    lag_days = len(official_dates)

            # 1.5 Concept Health
            try:
                concept_count = await self.cache.get_concept_count()
            except Exception as e:
                logger.warning(f"Concept check failed: {e}")
                concept_count = 0

            # 2. Financial Health
            deep_health = await self.cache.check_comprehensive_health()

            # Scorecard construction
            status = 'green'
            reasons = []

            if lag_days > 0:
                status = 'yellow'
                reasons.append(I18n.get('health_market_lag').format(days=lag_days))
            if lag_days > HEALTH_THRESHOLD_MARKET_LAG_DAYS:
                status = 'red'

            # 2.2 Comprehensive Data Coverage Check
            tables = deep_health.get('tables', {})
            fin_fresh_ratio = tables.get('financial_reports', {}).get('ratio', 0)

            # Identify missing critical tables dynamically from data dictionary
            critical_tables = [
                name for name, meta in TABLE_DEFINITIONS.items()
                if meta.get('quality_config', {}).get('critical')
            ]
            missing_critical = [
                t for t in critical_tables
                if tables.get(t, {}).get('ratio', 0) < 0.1
            ]

            # Count all missing stock tables
            all_missing = [t for t, v in tables.items() if v.get('type') != 'global' and v.get('ratio', 0) < 0.1]

            # Determine Data Status
            data_status = 'green'
            if missing_critical:
                data_status = 'red'
                reasons.append(f"{len(missing_critical)} Critical Tables Missing")
            elif len(all_missing) > 3:
                data_status = 'yellow'
                reasons.append(f"{len(all_missing)} Tables Missing Data")
            elif fin_fresh_ratio < HEALTH_THRESHOLD_FINANCIAL_COVERAGE:
                data_status = 'yellow'
                reasons.append(I18n.get('health_financial_missing').format(ratio=f"{fin_fresh_ratio:.0%}"))

            # --- Depth & Breadth: Strategy-driven evaluation ---
            max_required = max(
                (cls.required_history_days for cls in _STRATEGY_REGISTRY.values()),
                default=0
            )
            depth_threshold = min(1.0, (max_required * HEALTH_DEPTH_SAFETY_MULTIPLIER) / HEALTH_DEPTH_FULL_TRADE_DAYS) if max_required > 0 else 0
            logger.info(f"[Health] Depth threshold: {depth_threshold:.3f} (from max_required={max_required})")

            missing_depth = []
            if depth_threshold > 0:
                missing_depth = [
                    t for t in critical_tables
                    if tables.get(t, {}).get('depth_ratio') is not None
                    and tables.get(t, {}).get('depth_ratio', 1.0) < depth_threshold
                ]
                if missing_depth:
                    if data_status == 'green':
                        data_status = 'yellow'
                    reasons.append(I18n.get('health_depth_warning').format(
                        count=len(missing_depth), required=max_required * HEALTH_DEPTH_SAFETY_MULTIPLIER))

            missing_breadth = [
                t for t in critical_tables
                if tables.get(t, {}).get('breadth_ratio') is not None
                and tables.get(t, {}).get('breadth_ratio', 1.0) < HEALTH_THRESHOLD_BREADTH
            ]
            if missing_breadth:
                if data_status == 'green':
                    data_status = 'yellow'
                reasons.append(I18n.get('health_breadth_warning').format(count=len(missing_breadth)))

            # Log Metrics
            logger.info(
                f"Health Metrics: Lag={lag_days}d, FinCoverage={fin_fresh_ratio:.1%}, Missing={len(all_missing)}, "
                f"MissDepth={len(missing_depth)}, MissBreadth={len(missing_breadth)}"
            )

            # Final Status Aggregation
            if status == 'red' or data_status == 'red':
                status = 'red'
            elif status == 'yellow' or data_status == 'yellow':
                status = 'yellow'

            if status != 'green':
                logger.warning(f"Health Check Abnormal: Status={status}, Reasons={reasons}")

            # Update Tier State
            if status == 'red':
                self._quality_tier = 0
            elif status == 'yellow':
                # Force downgrade to Bronze if data is stale, missing depth, or missing breadth
                self._quality_tier = 1 
            else:
                # If everything is green, grant Gold. If yellow (e.g. minor lag), grant Silver.
                # Only upgrade, never blindly carry over an unjustified current tier
                if fin_fresh_ratio > 0.9:
                    self._quality_tier = 3  # GOLD
                elif fin_fresh_ratio > 0.5 or lag_days <= 5:
                    self._quality_tier = 2  # SILVER
                else:
                    self._quality_tier = 1  # BRONZE

            # Calculate overall system coverage (using financial as main proxy)
            sys_coverage = fin_fresh_ratio * 100

            if lag_days == 0:
                status_desc = I18n.get("health_status_ok_short")
            else:
                status_desc = I18n.get("health_status_lag_short", days=lag_days)

            status_msg = I18n.get("init_complete").format(
                status=status_desc,
                coverage=f"{sys_coverage:.1f}%"
            )
            # Append concept info
            status_msg += f" | {I18n.get('health_concepts_count', count=concept_count)}"

            # Construction of Market Info with None safety
            latest_official = official_dates[-1] if official_dates else "N/A"
            market_info = {
                'latest_local': last_local if last_local else "N/A",
                'latest_official': latest_official,
                'lag_days': lag_days
            }

            result_dict = {
                'status': status,
                'msg': status_msg,
                'reasons': reasons,
                'market': market_info,
                'fundamentals': deep_health,
                'details': {
                    'lag': lag_days,
                    'financial_coverage': sys_coverage,
                    'concept_count': concept_count,
                    'missing_critical': len(missing_critical),
                    'missing_depth': len(missing_depth),
                    'missing_breadth': len(missing_breadth),
                    'missing_all': len(all_missing)
                }
            }
            self._health_cache = {'time': now, 'data': result_dict}
            return result_dict
        except Exception as e:
            logger.error(f"Health check failed: {e}", exc_info=True)
            return {'status': 'red', 'msg': f"Check failed: {str(e)}"}

    @log_async_operation(operation_name="run_quality_scan")
    async def run_quality_scan(self, sample_size=50, progress_callback=None):
        """
        Tier 2/Tier 3 Deep Health Scan.
        Samples stocks and runs DataQualityService checks.
        
        Args:
            sample_size: Number of stocks to sample (default 50).
            progress_callback: Callback(current, total, msg).
        """
        import random

        # Reset cancel event (prevents immediate skipped scan if previous op was cancelled)
        self.clear_cancel()

        if progress_callback:
            progress_callback(0, 100, I18n.get("scan_step_init"))

        try:
            # 1. Select Sample
            basics = await self.cache.get_stock_basic()
            if basics is None or basics.empty:
                return {'score': 0, 'tier': 0, 'details': {}}

            active_stocks = basics[basics['list_status'] == 'L']['ts_code'].tolist()
            sample = random.sample(active_stocks, min(sample_size, len(active_stocks)))

            logger.info(f"[QualityScan] Starting scan on {len(sample)} stocks.")

            # 2. Prepare Context
            scan_results = {'continuity': [], 'recency': [], 'nulls': []}

            # --- Architecture Optimization: One-Pass Batch Fetch ---
            # Fetch 1 year of data for all sampled stocks at once to avoid N+1 queries 
            # and over-fetching entire 20-year history for single stocks.
            start_date_str = (datetime.datetime.now() - datetime.timedelta(days=365)).strftime('%Y%m%d')

            # Only fetch open trading days within scan window (halves data vs full calendar)
            trade_cal_df = await self.cache.get_trade_cal(start_date=start_date_str, is_open=1)
            if trade_cal_df is None or trade_cal_df.empty:
                logger.warning("[QualityScan] Trade calendar empty/unavailable - continuity check will be skipped.")

            batch_df = await self.cache.get_daily_quotes(ts_code_list=sample, start_date=start_date_str)

            # 3. Iterate Sample (DataFrame Slicing in Memory)
            # We use a simplified loop. In production, could be parallelized.
            total_steps = len(sample)

            for idx, ts_code in enumerate(sample):
                if self.is_cancelled():
                    break

                # Update Progress
                pct = int((idx / total_steps) * 100)
                if progress_callback:
                    progress_callback(pct, 100, I18n.get("scan_scanning", code=ts_code))

                # Fetch Data via Batch Slice (No DB hit)
                if batch_df is not None and not batch_df.empty:
                    df_daily = batch_df[batch_df['ts_code'] == ts_code]
                else:
                    df_daily = None

                if df_daily is not None and not df_daily.empty:
                    # Sort explicitly to guarantee recency check safety
                    df_daily = df_daily.sort_values('trade_date', ascending=False)

                    # Check Continuity (only if trade_cal is available)
                    if trade_cal_df is not None and not trade_cal_df.empty:
                        cont_res = DataQualityService.check_continuity(df_daily, 'trade_date', trade_cal_df)
                        scan_results['continuity'].append(cont_res['coverage_ratio'])

                    # Check Recency (vs today)
                    rec_res = DataQualityService.check_recency(df_daily, 'trade_date',
                                                               datetime.datetime.now().strftime('%Y%m%d'))
                    scan_results['recency'].append(rec_res['lag_days'])

                    # Check Nulls (Close price)
                    null_res = DataQualityService.check_nulls(df_daily, ['close', 'vol'])
                    scan_results['nulls'].append(null_res.get('close', 0.0))

            # 4. Aggregate
            avg_continuity = sum(scan_results['continuity']) / len(scan_results['continuity']) if scan_results[
                'continuity'] else 0
            avg_recency = sum(scan_results['recency']) / len(scan_results['recency']) if scan_results['recency'] else 99

            tier = 1
            if avg_continuity > 0.95 and avg_recency < 5:
                tier = 2
            if avg_continuity > 0.99 and avg_recency < 3:
                tier = 3  # Placeholder logic for Tier 3

            self._quality_tier = tier
            logger.info(f"[QualityScan] Scan Complete. Tier={tier}, Score={int(avg_continuity * 100)}")

            result = {
                'score': int(avg_continuity * 100),
                'tier': tier,
                'sample_size': len(sample),
                'avg_continuity': avg_continuity,
                'avg_lag': avg_recency
            }

            if progress_callback:
                progress_callback(100, 100, I18n.get("scan_complete"))
            return result

        except Exception as e:
            logger.error(f"[QualityScan] Failed: {e}", exc_info=True)
            return {'score': 0, 'tier': 0, 'error': str(e)}
        finally:
            # Ensure cancel state doesn't leak into subsequent operations
            self.clear_cancel()

    # ... Other simple sync/get methods ...

    @log_async_operation(operation_name="sync_stock_basic")
    async def sync_stock_basic(self):
        """Sync stock basic info (Step 1 of initialization)."""
        if self.is_cancelled():
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
            logger.error(f"[sync_stock_basic] ❌ Failed: {e}", exc_info=True)
            return 0
        finally:
            with self._sync_lock:
                self._is_syncing_basic = False

    @log_async_operation(operation_name="sync_concepts")
    async def sync_concepts(self):
        """Sync stock concepts from Tushare."""
        if self.is_cancelled():
            return 0

        try:
            logger.info("[sync_concepts] Starting concept sync...")
            # Strategy:
            # 1. Get all concepts: `df_concepts = pro.concept()`
            # 2. Parallel fetch `pro.concept_detail(id=c)` for all `c` using ThreadPool and Semaphore.
            # 3. Merge results and save atomically.

            # Fetch Concept List
            # Fetch Concept List (uses _handle_api_call for rate limiting)
            df_c = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_concept_list)
            if df_c is None or df_c.empty:
                return 0

            c_codes = df_c['code'].tolist()

            # Use Semaphore to limit concurrency (Architecturally better than chunking)
            # TushareClient has internal rate limiting, so we just limit concurrency to avoid
            # overloading the ThreadPool or local resources.
            concurrency = ConfigHandler.get_sync_concurrency_light()
            sem = asyncio.Semaphore(concurrency or 20)

            async def fetch_one(c):
                # Check cancellation before acquiring semaphore to fail fast
                if self.is_cancelled(): return None
                async with sem:
                    # Double check inside semaphore
                    if self.is_cancelled(): return None
                    return await ThreadPoolManager().run_async(TaskType.IO, self.api.get_concept_detail_by_id, c)

            # Create tasks eagerly but execute with semaphore
            # This avoids "coroutine never awaited" because we wrap in create_task
            tasks = [asyncio.create_task(fetch_one(c)) for c in c_codes]

            all_dfs = []
            try:
                # Wait for all tasks to complete or be cancelled
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check if we were cancelled during gather
                if self.is_cancelled():
                    return 0

                for r in results:
                    if isinstance(r, pd.DataFrame) and not r.empty:
                        all_dfs.append(r)
                    elif isinstance(r, Exception):
                        # Log but don't stop everything for one failed concept
                        logger.warning(f"[sync_concepts] Task failed: {r}")

            except asyncio.CancelledError:
                logger.info("[sync_concepts] Cancelled during gather")
                raise
            finally:
                # Ensure all pending tasks are cancelled if we exit early
                for t in tasks:
                    if not t.done():
                        t.cancel()

            if not all_dfs:
                return 0

            full_df = pd.concat(all_dfs)

            # Rename cols to match Schema
            # API returns: id, concept_name, ts_code, name
            # Schema: ts_code, concept_name, concept_id

            full_df = full_df.rename(columns={'id': 'concept_id'})
            # Ensure unique
            full_df = full_df[['ts_code', 'concept_name', 'concept_id']].drop_duplicates()

            # Atomic overwrite (refresh)
            count = await self.cache.overwrite_concepts(full_df)
            logger.info(f"[sync_concepts] ✅ Synced {count} concept mappings (Atomic)")
            return count

        except Exception as e:
            logger.error(f"[sync_concepts] ❌ Failed: {e}", exc_info=True)
            return 0
        finally:
            pass

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
        Ensure trade calendar covers [required_start_date, end_date].
        Includes memory caching to avoid frequent DB/API checks (and log spam).
        """
        # Optimized path for frequent checks (e.g. Home Screen polling)
        # Only cache if using default start date (required_start_date is None)
        if required_start_date is None and self._trade_cal_cache.get('date') == end_date:
            return True

        success = await self._ensure_trade_cal_impl(end_date, required_start_date)

        if success and required_start_date is None:
            self._trade_cal_cache = {'date': end_date}

        return success

    @log_async_operation(operation_name="ensure_trade_cal_impl")
    async def _ensure_trade_cal_impl(self, end_date, required_start_date=None):
        """
        Ensure trade calendar covers [required_start_date, end_date].
        """
        try:
            min_db, max_db = await self.cache.get_trade_cal_range()

            curr_year = int(end_date[:4])
            # Default start to 4 years ago if not specified
            target_start = required_start_date if required_start_date else datetime.date(curr_year - 4, 1, 1).strftime(
                '%Y%m%d')

            async def fetch_and_save(s, e):
                y = int(e[:4])
                real_end = datetime.date(y, 12, 31).strftime('%Y%m%d')
                if e < real_end: e = real_end

                logger.info(f"[DataProcessor] Syncing trade calendar: {s} - {e}")
                df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_trade_cal, start_date=s, end_date=e)
                if df is not None and not df.empty:
                    await self.cache.save_trade_cal(df)
                    return True
                return False

            if not min_db or not max_db:
                return await fetch_and_save(target_start, end_date)
            else:
                # Check coverage and fetch missing parts
                tasks = []
                if target_start < min_db:
                    gap = (datetime.datetime.strptime(min_db, '%Y%m%d') - datetime.datetime.strptime(target_start,
                                                                                                     '%Y%m%d')).days
                    if gap > 10:
                        tasks.append(fetch_and_save(target_start, min_db))

                if max_db < end_date:
                    tasks.append(fetch_and_save(max_db, end_date))

                if tasks:
                    results = await asyncio.gather(*tasks)
                    return all(results)

            return True
        except Exception as e:
            logger.error(f"[DataProcessor] ensure_trade_cal failed: {e}")
            return False

    async def get_market_overview(self):
        """
        Get market overview data for Home Screen.
        Returns Indices (SH, SZ, CYB) and Northbound Money Flow.
        """
        try:
            now = datetime.datetime.now()
            today_str = now.strftime('%Y%m%d')
            start_str = (now - datetime.timedelta(days=30)).strftime('%Y%m%d')

            # Calendar Check (Cached inside ensure_trade_cal)
            await self.ensure_trade_cal(today_str)

            # Find latest valid date
            cache_df = await self.cache.get_trade_cal(start_date=start_str, end_date=today_str, is_open=1)
            date = today_str
            if not cache_df.empty:
                date = sorted(cache_df['cal_date'].tolist())[-1]

            # Parallel Fetch
            async def get_idx(code, name_key):
                df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_index_daily, ts_code=code,
                                                         trade_date=date)
                name = I18n.get(name_key)
                if df is not None and not df.empty:
                    row = df.iloc[0]
                    c = row.get('pct_chg', 0)
                    v = row.get('close', 0)
                    return {'name': name, 'value': f"{v:.2f}", 'change': f"{c:+.2f}%",
                            'color': 'red' if c >= 0 else 'green'}
                return {'name': name, 'value': '-', 'change': '-', 'color': 'grey'}

            async def get_hsgt():
                df = await ThreadPoolManager().run_async(TaskType.IO, self.api.get_moneyflow_hsgt, trade_date=date)
                name = I18n.get('home_northbound')
                if df is not None and not df.empty:
                    val = float(df.iloc[0]['north_money'])
                    val_str = f"{val / 100:.2f}{I18n.get('unit_yi')}" if abs(
                        val) > 100 else f"{val:.0f}{I18n.get('unit_wan')}"
                    sub_str = I18n.get('home_inflow') if val > 0 else I18n.get('home_outflow')
                    return {'name': name, 'value': val_str, 'sub': sub_str}
                return {'name': name, 'value': '-', 'sub': '-'}

            hot_concepts_task = NewsFetcher.get_hot_concepts(limit=8)

            results = await asyncio.gather(
                get_idx('000001.SH', 'home_index_sh'),
                get_idx('399001.SZ', 'home_index_sz'),
                get_idx('399006.SZ', 'home_index_cyb'),
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
            logger.error(f"Failed to get market overview: {e}", exc_info=True)
            return None

    async def get_screening_data(self, trade_date=None):
        return await self.cache.get_screening_data(trade_date)

    async def initialize_system(self, progress_callback=None):
        """
        Orchestrate system initialization with 6 distinct steps.
        
        Steps and weights:
        - Step 1 (1%):  Sync stock list
        - Step 2 (1%):  Sync trade calendar
        - Step 3 (45%): Sync historical data
        - Step 4 (38%): Sync financial data
        - Step 5 (10%): Sync AI core data (macro/holders)
        - Step 6 (5%):  Health check
        
        Returns:
            dict: Health check result on success
            None: If cancelled or critical failure
            
        Note: Call request_cancel() to cancel this operation.
        """
        from ui.i18n import I18n
        from data.data_dictionary import validate_schema_definitions

        # Run schema validation (DD-01)
        validate_schema_definitions()

        # Clear any previous cancel state
        self.clear_cancel()

        # Step weights (must sum to 100)
        # Optimized based on user feedback (Steps 1 & 2 represent 2% total)
        # Added Step 5 (AI Data) -> 10%
        STEP_WEIGHTS = [1, 1, 45, 38, 10, 5]
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

            # ===== Step 1.5: Concepts (runs as part of Step 1) =====
            report_step(1, 0.5, 1, I18n.get("init_sync_concepts"))
            await self.sync_concepts()

            # ===== Step 2: Trade Calendar (5%) =====
            report_step(2)
            end_date = datetime.datetime.now().strftime('%Y%m%d')
            start_date = (datetime.datetime.now() - datetime.timedelta(days=365 * 3)).strftime('%Y%m%d')
            cal_success = await self.ensure_trade_cal(end_date, required_start_date=start_date)
            if not cal_success:
                logger.error("[initialize_system] Step 2 failed: Trade calendar sync failed, aborting")
                return None
            if self.is_cancelled(): return None

            # ===== Step 3: Historical Data (50%) =====
            def step3_callback(current, total, msg):
                report_step(3, current, total, msg)

            history_result = await self.sync_historical_data(
                days=365 * 3,
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

            # ===== Step 5: AI Alpha Data (10%) =====
            report_step(5, 0, 3, I18n.get("init_sync_macro"))

            macro_res = await self.strategies['macro'].run()
            if macro_res.status == "failed":
                logger.warning(f"[initialize_system] Macro sync failed: {macro_res.errors}")

            report_step(5, 1, 3, I18n.get("init_sync_holders"))

            holder_res = await self.strategies['holder'].run()
            if holder_res.status == "failed":
                logger.warning(f"[initialize_system] Holder sync failed: {holder_res.errors}")

            report_step(5, 2, 3, I18n.get("init_step_5_done"))

            if self.is_cancelled(): return None

            # ===== Step 6: Health Check (5%) =====
            report_step(6)
            result = await self.check_data_health()

            # Report completion
            if progress_callback:
                progress_callback(100, 100, I18n.get("init_step_complete"))

            return result

        except Exception as e:
            step_label = I18n.get(f"init_step_{current_step}") if current_step > 0 else "Initialization"
            logger.error(f"[initialize_system] ❌ Failed at {step_label}: {e}")
            raise  # Re-raise so UI can catch and display

    # ... get_stock_history, get_strategy_data ...
    async def get_stock_history(self, ts_code, days=365):
        end = datetime.datetime.now().strftime('%Y%m%d')
        start = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime('%Y%m%d')
        return await self.cache.get_daily_quotes(ts_code=ts_code, start_date=start, end_date=end)

    async def get_strategy_data(self):
        return await self.prepare_screening_context()

    @log_async_operation(operation_name="prepare_screening_context")
    async def prepare_screening_context(self):
        """Prepare context for screening execution."""

        # Ensure quality tier has been initialized before screening
        # None = never checked; runs the lightweight fast-path exactly once per app lifecycle
        if self._quality_tier is None:
            await self._assign_basic_tier()

        context = {}
        # Decorator handles exception logging. Exceptions will bubble up to ViewModel.

        # 1. Main Screening Data (Quotes + Indicators)
        # Use actual latest trade date available in the database
        trade_date = await self.cache.get_latest_trade_date()
        context['screening_data'] = await self.get_screening_data(trade_date)

        # 2. Auxiliary Data
        # Northbound
        nb = await self.cache.get_northbound(trade_date=trade_date)
        if nb is not None:
            context['northbound_data'] = nb

        # Moneyflow
        mf = await self.cache.get_moneyflow(trade_date=trade_date)
        if mf is not None:
            context['moneyflow_data'] = mf

        # Top List (LHB)
        lhb = await self.cache.get_top_list(trade_date=trade_date)
        if lhb is not None:
            context['top_list'] = lhb

        # Block Trade
        blk = await self.cache.get_block_trade(trade_date=trade_date)
        if blk is not None:
            context['block_trade'] = blk

        return context
