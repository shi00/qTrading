"""
Scheduler service for automatic data updates.
Runs as a background task within the Flet application using APScheduler.
"""
import datetime
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from data.data_processor import DataProcessor
from data.review_manager import ReviewManager
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Background scheduler for automatic data updates.
    Uses AsyncIOScheduler to manage jobs safely within the asyncio event loop.
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

        # Initialize AsyncIOScheduler with explicit timezone
        # 'apscheduler.job_defaults.max_instances': 1 ensures we don't overlap runs
        # timezone='Asia/Shanghai' ensures consistent scheduling regardless of server location
        self.scheduler = AsyncIOScheduler(job_defaults={'max_instances': 1}, timezone='Asia/Shanghai')
        self._last_update_date = None
        self._last_pred_date = None
        self._initialized = True
        logger.info("[Scheduler] Initialized (APScheduler, Timezone: Asia/Shanghai)")

    def start(self):
        """Start the scheduler"""
        if self.scheduler.running:
            return

        # Schedule jobs based on config
        self._schedule_jobs()

        try:
            self.scheduler.start()
            logger.info("[Scheduler] Started")
            
            # Start Config Watchdog (Every 30s)
            # This ensures we pick up changes from Settings UI without restart
            self.scheduler.add_job(
                self._watch_config_changes,
                'interval',
                seconds=30,
                id='config_watchdog',
                replace_existing=True
            )
        except Exception as e:
            logger.error(f"[Scheduler] Failed to start: {e}")

    def stop(self):
        """Stop the scheduler"""
        logger.info(f"Stopping scheduler... (running={self.scheduler.running})")
        if self.scheduler.running:
            try:
                self.scheduler.shutdown(wait=False) 
                logger.info("Scheduler shutdown initiated.")
            except Exception as e:
                logger.error(f"Error shutting down scheduler: {e}")
        else:
            logger.info("Scheduler was not running.")

    async def _watch_config_changes(self):
        """Monitor config changes and reload jobs if needed"""
        if not hasattr(self, '_last_known_config'):
             self._last_known_config = {
                 'time': ConfigHandler.get_auto_update_time(),
                 'enabled': ConfigHandler.is_auto_update_enabled()
             }
             return

        current_time = ConfigHandler.get_auto_update_time()
        current_enabled = ConfigHandler.is_auto_update_enabled()
        
        changed = False
        if current_time != self._last_known_config['time']:
            logger.info(f"[Scheduler] Detected schedule time change: {self._last_known_config['time']} -> {current_time}")
            changed = True
            
        if current_enabled != self._last_known_config['enabled']:
            logger.info(f"[Scheduler] Detected enable status change: {self._last_known_config['enabled']} -> {current_enabled}")
            changed = True
            
        if changed:
            logger.info("[Scheduler] Reloading jobs...")
            self._schedule_jobs()
            self._last_known_config = {
                'time': current_time,
                'enabled': current_enabled
            }

    def _schedule_jobs(self):
        """Register jobs with the scheduler"""
        self.scheduler.remove_all_jobs()

        # 1. Daily Data Update Job
        # Get scheduled time from config or default to 16:30
        scheduled_time = ConfigHandler.get_auto_update_time() or "16:30"
        try:
            hour, minute = map(int, scheduled_time.split(':'))
        except:
            hour, minute = 16, 30

        self.scheduler.add_job(
            self._run_daily_update,
            CronTrigger(hour=hour, minute=minute),
            id='daily_update',
            replace_existing=True
        )
        logger.info(f"[Scheduler] Scheduled Daily Update at {hour:02d}:{minute:02d}")

        # 2. Nightly AI Prediction Job (Default 20:30)
        # In the future, this could be configurable
        self.scheduler.add_job(
            self._run_nightly_prediction,
            CronTrigger(hour=20, minute=30),
            id='nightly_prediction',
            replace_existing=True
        )
        logger.info(f"[Scheduler] Scheduled Nightly Prediction at 20:30")

    async def _run_daily_update(self):
        """Execute the data update (16:30)"""
        # Global enable check
        if not ConfigHandler.is_auto_update_enabled():
            logger.info("[Scheduler] Update skipped (Auto-update disabled)")
            return

        today = datetime.datetime.now().strftime('%Y%m%d')
        if self._last_update_date == today:
            logger.info("[Scheduler] Update skipped (Already updated today)")
            return

        # Check Trading Day (Lazy Import)
        from data.tushare_client import TushareClient
        try:
            if not TushareClient().is_trading_day(today):
                logger.info(f"[Scheduler] Update skipped ({today} is not a trading day)")
                return
        except Exception as e:
            logger.warning(f"[Scheduler] Trade calendar check failed: {e}")
            if datetime.datetime.now().weekday() >= 5:  # Weekend fallback
                return

        logger.info(f"[Scheduler] Running Scheduled Update for {today}...")

        try:
            processor = DataProcessor()
            review_mgr = ReviewManager()

            await processor.init_data()

            # Sync today's data
            result = await processor.sync_daily_market_snapshot()
            logger.info(f"[Scheduler] Data update complete. Rows: {result if isinstance(result, int) else 'N/A'}")

            # Sync Financial Reports
            await processor.sync_financial_reports()
            logger.info(f"[Scheduler] Financial reports sync complete.")

            # Update Review Stats
            await review_mgr.run_review()
            logger.info(f"[Scheduler] Review performance updated.")

            self._last_update_date = today

        except Exception as e:
            logger.error(f"[Scheduler] Update failed: {e}", exc_info=True)

    async def _run_nightly_prediction(self):
        """Execute AI Strategy (20:30)"""
        if not ConfigHandler.is_auto_update_enabled():
            return

        today = datetime.datetime.now().strftime('%Y%m%d')
        if self._last_pred_date == today:
            return

        # Simple verification that update ran (optional but good for safety)
        # if self._last_update_date != today: ...

        logger.info(f"[Scheduler] Running Nightly Prediction for {today}...")

        try:
            from strategies.ai_strategy import AISelectionStrategy
            from data.review_manager import ReviewManager

            processor = DataProcessor()
            await processor.init_data()

            # 1. Prepare Market Data
            target_date = await processor.prepare_market_data()  # Smart check logic
            logger.info(f"[Scheduler] AI Target Date: {target_date}")

            # 2. Get Context
            context = await processor.get_strategy_data()
            if not context:
                logger.error("[Scheduler] No strategy data context available.")
                return

            context['data_processor'] = processor

            # 3. Run Strategy
            strategy = AISelectionStrategy()
            result_df = await strategy.filter(context)

            if result_df is not None and not result_df.empty:
                # Save results
                rm = ReviewManager()
                await rm.save_results("AI_Auto_Nightly", result_df)
                logger.info(f"[Scheduler] Prediction completed. Saved {len(result_df)} candidates.")
            else:
                logger.info("[Scheduler] Prediction completed. No candidates found.")

            self._last_pred_date = today

        except Exception as e:
            logger.error(f"[Scheduler] Prediction failed: {e}", exc_info=True)

    def get_status(self) -> dict:
        """Get scheduler status for UI display"""
        enabled = ConfigHandler.is_auto_update_enabled()
        scheduled_time = ConfigHandler.get_auto_update_time()

        # In APScheduler, next_run_time is available on jobs
        next_run = "N/A"
        job = self.scheduler.get_job('daily_update')
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')

        return {
            'enabled': enabled,
            'scheduled_time': scheduled_time,
            'running': self.scheduler.running,
            'last_update': self._last_update_date,
            'last_prediction': self._last_pred_date,
            'next_run': next_run
        }


# Global scheduler instance
scheduler = SchedulerService()
