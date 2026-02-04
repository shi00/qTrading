"""
Scheduler service for automatic data updates.
Runs as a background task within the Flet application.
"""
import asyncio
import datetime
import logging
from utils.config_handler import ConfigHandler
from data.data_processor import DataProcessor
from data.review_manager import ReviewManager

logger = logging.getLogger(__name__)

class SchedulerService:
    """
    Background scheduler for automatic data updates.
    Checks every minute if it's time to run the scheduled update.
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
        
        self._running = False
        self._task = None
        self._last_update_date = None
        self._initialized = True
        self._thread = None
        self._loop = None
        # self._lock = asyncio.Lock() -> Moved to run_scheduler to be loop-safe
    
    def start(self):
        """Start the scheduler in a background thread with its own event loop"""
        if self._running:
            return
        
        self._running = True
        
        def run_scheduler():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            # Initialize lock in the correct loop
            self._lock = asyncio.Lock()
            
            try:
                self._loop.run_until_complete(self._scheduler_loop())
            except Exception as e:
                msg = str(e)
                if "Event loop stopped" in msg:
                     logger.info("[Scheduler] Loop stopped cleanly (shutdown).")
                else:
                     logger.error(f"[Scheduler] Thread error: {e}")
            finally:
                self._loop.close()
        
        import threading
        self._thread = threading.Thread(target=run_scheduler, daemon=True)
        self._thread.start()
        logger.info("[Scheduler] Started")
    
    def stop(self):
        """Stop the scheduler"""
        self._running = False
        # Do NO call self._loop.stop() here! It kills the loop immediately.
        # Let the loop exit naturally because _running is False.
        # self._loop.call_soon_threadsafe(self._loop.stop) <- REMOVED
        
        if self._thread and self._thread.is_alive():
            # Wait for thread to finish its current loop iteration
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("[Scheduler] Stopped")
    
    async def _scheduler_loop(self):
        """Main scheduler loop - checks every minute"""
        while self._running:
            try:
                await self._check_and_run()
            except Exception as e:
                logger.error(f"[Scheduler] Error in loop: {e}")
            
            # Check frequently (every 1s) instead of sleeping 60s
            for _ in range(60):
                if not self._running:
                    break
                await asyncio.sleep(1)
    
    async def _check_and_run(self):
        """Check if update should run and execute if needed"""
        # Check if auto-update is enabled
        if not ConfigHandler.is_auto_update_enabled():
            return
        
        # Get scheduled time
        scheduled_time = ConfigHandler.get_auto_update_time()  # e.g., "16:30"
        
        now = datetime.datetime.now()
        today = now.strftime('%Y%m%d')
        
        # Don't run if already updated today
        if self._last_update_date == today:
            return
        
        # Check if it's a trading day (handles Chinese holidays)
        # Import here to avoid circular imports
        from data.tushare_client import TushareClient
        try:
            if not TushareClient().is_trading_day(today):
                logger.debug(f"[Scheduler] Skipping - {today} is not a trading day")
                return
        except Exception as e:
            # Fallback to weekday check if API fails
            if now.weekday() >= 5:  # Saturday or Sunday
                return
            logger.warning(f"[Scheduler] Trade calendar check failed: {e}, using weekday fallback")
        
        # Parse scheduled time
        try:
            hour, minute = map(int, scheduled_time.split(':'))
        except:
            hour, minute = 16, 30  # Default
        
        # Check if current time is past scheduled time
        scheduled_dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if now >= scheduled_dt:
            logger.info(f"[Scheduler] Running scheduled update at {now.strftime('%H:%M')}")
            await self._run_update()
            self._last_update_date = today

        # Check for Evening AI Prediction (e.g. 20:00)
        # Hardcoded for now or add config later. Design doc says 20:00/21:00.
        # Let's target 20:30 to ensure news is ready.
        pred_dt = now.replace(hour=20, minute=30, second=0, microsecond=0)
        
        # We need a separate tracking for prediction run to avoid double run
        if not hasattr(self, '_last_pred_date'):
            self._last_pred_date = None
            
        if now >= pred_dt and self._last_pred_date != today:
             logger.info(f"[Scheduler] Running Nightly AI Prediction at {now.strftime('%H:%M')}")
             await self._run_prediction()
             self._last_pred_date = today

    async def _run_update(self):
        """Execute the data update (16:30)"""
        if self._lock.locked():
            logger.warning("[Scheduler] Update skipped - Task already running")
            return

        async with self._lock:
            try:
                processor = DataProcessor()
                review_mgr = ReviewManager()
                
                await processor.init_data()
                
                # Sync today's data (or whatever latest data)
                result = await processor.sync_daily_market_snapshot()
                logger.info(f"[Scheduler] Data update complete: {result}")
                
                # Sync Financial Reports (Incremental)
                await processor.sync_financial_reports()
                logger.info(f"[Scheduler] Financial reports sync complete.")
                
                # Update review performance (T+1, T+5)
                await review_mgr.run_review()
                logger.info(f"[Scheduler] Review performance updated.")
            
            except Exception as e:
                logger.error(f"[Scheduler] Update failed: {e}")

    async def _run_prediction(self):
        """Execute AI Strategy (20:30)"""
        # Wait for lock if update is running, with timeout circuit breaker
        try:
            # Try to acquire lock with 5-minute timeout
            # If 16:30 update is stuck, we shouldn't wait forever
            await asyncio.wait_for(self._lock.acquire(), timeout=300)
        except asyncio.TimeoutError:
            logger.error("[Scheduler] [WARN] Lock acquisition timed out (5min). Previous task stuck? Skipping prediction.")
            return

        try:
            logger.info("[Scheduler] Starting AI Strategy execution...")
            from strategies.ai_strategy import AISelectionStrategy
            from data.review_manager import ReviewManager
            
            processor = DataProcessor()
            await processor.init_data()
            
            # Prepare context
            # Requirements: "Agent starts analysis ONLY after today's complete data update"
            
            # 1. Prepare Market Data (Branch Logic)
            # This handles: Non-Trading Day, Pre-Close, Post-Close logic
            target_date = await processor.prepare_market_data()
            logger.info(f"[Scheduler] AI Analysis Target Date: {target_date}")
            
            # 2. Get Data Context
            # We must tell data processor WHICH date's data to load if it's not simply 'latest cache'
            # But get_strategy_data currently just loads from cache tables.
            # Assuming cache is now synced to target_date if needed.
            
            # Verify cache date matches (safety check)
            latest_cached = await processor.cache.get_latest_trade_date()
            if latest_cached != target_date:
                 logger.error(f"[Scheduler] Critical Error: Cache date ({latest_cached}) != Target ({target_date}) after preparation.")
                 return

            # 2. Get Data Context
            context = await processor.get_strategy_data()
            if not context:
                logger.error("[Scheduler] Failed to get strategy data context")
                return
            context['data_processor'] = processor
            
            # Run Strategy
            strategy = AISelectionStrategy()
            result_df = await strategy.filter(context)
            
            if result_df is not None and not result_df.empty:
                # Save results automatically
                rm = ReviewManager()
                await rm.save_results("AI_Auto_Nightly", result_df)
                logger.info(f"[Scheduler] AI Strategy completed. Saved {len(result_df)} candidates.")
                
        except Exception as e:
            logger.error(f"[Scheduler] Prediction failed: {e}")
        finally:
            # Ensure lock is released
            if self._lock.locked():
                self._lock.release()

    def get_status(self) -> dict:
        """Get scheduler status for UI display"""
        enabled = ConfigHandler.is_auto_update_enabled()
        scheduled_time = ConfigHandler.get_auto_update_time()
        
        return {
            'enabled': enabled,
            'scheduled_time': scheduled_time,
            'running': self._running,
            'last_update': self._last_update_date,
            'last_prediction': getattr(self, '_last_pred_date', None)
        }

# Global scheduler instance
scheduler = SchedulerService()
