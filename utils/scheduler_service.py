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
    
    def start(self):
        """Start the scheduler background task"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("[Scheduler] Started")
    
    def stop(self):
        """Stop the scheduler"""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None
        logger.info("[Scheduler] Stopped")
    
    async def _scheduler_loop(self):
        """Main scheduler loop - checks every minute"""
        while self._running:
            try:
                await self._check_and_run()
            except Exception as e:
                logger.error(f"[Scheduler] Error in loop: {e}")
            
            # Wait 60 seconds before next check
            await asyncio.sleep(60)
    
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
        
        # Check if it's a weekday (trading day approximation)
        if now.weekday() >= 5:  # Saturday or Sunday
            return
        
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
    
    async def _run_update(self):
        """Execute the data update"""
        try:
            processor = DataProcessor()
            review_mgr = ReviewManager()
            
            await processor.init_data()
            
            # Sync today's data (or whatever latest data)
            result = await processor.sync_daily_market_snapshot()
            logger.info(f"[Scheduler] Data update complete: {result}")
            
            # Update review performance (T+1, T+5)
            await review_mgr.update_performance()
            logger.info(f"[Scheduler] Review performance updated.")
            
        except Exception as e:
            logger.error(f"[Scheduler] Update failed: {e}")
    
    def get_status(self) -> dict:
        """Get scheduler status for UI display"""
        enabled = ConfigHandler.is_auto_update_enabled()
        scheduled_time = ConfigHandler.get_auto_update_time()
        
        return {
            'enabled': enabled,
            'scheduled_time': scheduled_time,
            'running': self._running,
            'last_update': self._last_update_date,
        }


# Global scheduler instance
scheduler = SchedulerService()
