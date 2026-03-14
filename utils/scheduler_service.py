"""
Scheduler service for automatic data updates.
Runs as a background task within the Flet application using APScheduler.
"""

import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from data.data_processor import DataProcessor
from data.review_manager import ReviewManager
from data.tushare_client import TushareClient
from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

logger = logging.getLogger(__name__)


class SchedulerService:
    """
    Background scheduler for automatic data updates.
    Uses AsyncIOScheduler to manage jobs safely within the asyncio event loop.
    """

    _instance = None
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        # Initialize AsyncIOScheduler with explicit timezone
        # Initialize AsyncIOScheduler with explicit timezone
        # 'apscheduler.job_defaults.max_instances': 1 ensures we don't overlap runs
        # timezone='Asia/Shanghai' ensures consistent scheduling regardless of server location
        self.scheduler = AsyncIOScheduler(
            job_defaults={"max_instances": 1}, timezone="Asia/Shanghai",
        )
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

        # Add listener for missed jobs
        from apscheduler.events import EVENT_JOB_MISSED

        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)

        try:
            self.scheduler.start()
            logger.info("[Scheduler] Started")

            # Start Config Watchdog (Every 30s)
            # This ensures we pick up changes from Settings UI without restart
            self.scheduler.add_job(
                self._watch_config_changes,
                "interval",
                seconds=30,
                id="config_watchdog",
                replace_existing=True,
            )
        except Exception as e:
            logger.error(f"[Scheduler] Failed to start: {e}")

    def _on_job_missed(self, event):
        """Handle missed job events with clear logging"""
        job_id = event.job_id
        run_time = event.scheduled_run_time
        logger.warning(
            f"[Scheduler] ⚠️ JOB MISSED: '{job_id}' was skipped because the system was busy (Scheduled: {run_time})",
        )

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

    def _check_config_sync(self) -> dict:
        """Synchronously check config (runs in thread pool)"""
        return {
            "time": ConfigHandler.get_auto_update_time(),
            "enabled": ConfigHandler.is_auto_update_enabled(),
            "doubao_time": ConfigHandler.get_doubao_schedule_time(),
            "doubao_enabled": ConfigHandler.is_doubao_schedule_enabled(),
        }

    async def _watch_config_changes(self):
        """Monitor config changes and reload jobs if needed"""
        import asyncio

        # Run sync config check in thread pool to avoid blocking event loop
        try:
            current_config = await ThreadPoolManager().run_async(
                TaskType.IO, self._check_config_sync,
            )
        except asyncio.CancelledError:
            logger.info(
                "[Scheduler] _watch_config_changes cancelled (likely shutting down)",
            )
            raise
        except Exception as e:
            logger.error(f"[Scheduler] Config check failed: {e}")
            return

        if not hasattr(self, "_last_known_config"):
            self._last_known_config = current_config
            return

        current_time = current_config["time"]
        current_enabled = current_config["enabled"]
        current_doubao_time = current_config["doubao_time"]
        current_doubao_enabled = current_config["doubao_enabled"]

        changed = False
        if current_time != self._last_known_config["time"]:
            logger.info(
                f"[Scheduler] Detected schedule time change: {self._last_known_config['time']} -> {current_time}",
            )
            changed = True

        if current_enabled != self._last_known_config["enabled"]:
            logger.info(
                f"[Scheduler] Detected enable status change: {self._last_known_config['enabled']} -> {current_enabled}",
            )
            changed = True

        if current_doubao_time != self._last_known_config.get(
            "doubao_time",
        ) or current_doubao_enabled != self._last_known_config.get("doubao_enabled"):
            logger.info("[Scheduler] Detected Doubao schedule config change")
            changed = True

        if changed:
            logger.info("[Scheduler] Reloading jobs...")
            self._schedule_jobs()
            self._last_known_config = current_config

    def _schedule_jobs(self):
        """Register jobs with the scheduler"""
        # Only remove business jobs, NOT the config_watchdog
        for job_id in ["daily_update", "nightly_prediction", "doubao_weekly_refresh"]:
            existing = self.scheduler.get_job(job_id)
            if existing:
                existing.remove()

        # 1. Daily Data Update Job
        # Get scheduled time from config or default to 16:30
        scheduled_time = ConfigHandler.get_auto_update_time() or "16:30"
        try:
            hour, minute = map(int, scheduled_time.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 16, 30

        self.scheduler.add_job(
            self._run_daily_update,
            CronTrigger(hour=hour, minute=minute),
            id="daily_update",
            replace_existing=True,
        )
        logger.info(f"[Scheduler] Scheduled Daily Update at {hour:02d}:{minute:02d}")

        # 2. Nightly AI Prediction Job (Default 20:30)
        # In the future, this could be configurable
        self.scheduler.add_job(
            self._run_nightly_prediction,
            CronTrigger(hour=20, minute=30),
            id="nightly_prediction",
            replace_existing=True,
        )
        logger.info("[Scheduler] Scheduled Nightly Prediction at 20:30")

        # 3. Doubao AI Concept Tagging Job (Weekly on Saturday)
        doubao_time = ConfigHandler.get_doubao_schedule_time() or "10:00"
        try:
            dh, dm = map(int, doubao_time.split(":"))
        except (ValueError, TypeError, AttributeError):
            dh, dm = 10, 0

        self.scheduler.add_job(
            self._run_doubao_tagger,
            CronTrigger(day_of_week="sat", hour=dh, minute=dm),
            id="doubao_weekly_refresh",
            replace_existing=True,
        )
        logger.info(
            f"[Scheduler] Scheduled Doubao Weekly Refresh at {dh:02d}:{dm:02d} on Saturdays",
        )

    async def _run_daily_update(self):
        """Execute the data update (16:30)"""
        # Global enable check
        if not ConfigHandler.is_auto_update_enabled():
            logger.info("[Scheduler] Update skipped (Auto-update disabled)")
            return

        today = get_now().strftime("%Y%m%d")
        if self._last_update_date == today:
            logger.info("[Scheduler] Update skipped (Already updated today)")
            return

        # Check Trading Day
        try:
            client = TushareClient()
            is_trading = await ThreadPoolManager().run_async(
                TaskType.IO, client.is_trading_day, today,
            )  # TODO: P0-4 Phase 2 will make this fully async
            if not is_trading:
                logger.info(
                    f"[Scheduler] Update skipped ({today} is not a trading day)",
                )
                return
        except Exception as e:
            logger.warning(f"[Scheduler] Trade calendar check failed: {e}")
            if get_now().weekday() >= 5:
                return

        # Submit via TaskManager for visibility and persistence
        from services.task_manager import TaskManager

        async def _daily_update_logic(task_id: str, **kwargs):
            tm = TaskManager()
            processor = DataProcessor()

            def _progress(current, total, msg):
                tm.update_progress(task_id, current / total if total else 0, msg)

            result = await processor.run_daily_update(progress_callback=_progress)
            self._last_update_date = today
            added = getattr(result, "added", result) if result else 0
            return I18n.get("sched_daily_done", added=added)

        TaskManager().submit_task(
            name=I18n.get("sched_task_daily_update", date=today),
            task_type=I18n.get("sched_task_type_daily"),
            coroutine_factory=_daily_update_logic,
            cancellable=False,
            unique_key="daily_sync",
        )

    async def _run_doubao_tagger(self):
        if not ConfigHandler.is_doubao_schedule_enabled():
            return

        from services.task_manager import TaskManager

        async def _doubao_logic(task_id: str, **kwargs):
            tm = TaskManager()
            task = tm.get_task(task_id)
            cancel_event = task._cancel_event if task else None
            processor = DataProcessor()
            tm.update_progress(task_id, 0.05, "清空历史豆包概念...")
            await processor.run_doubao_tagging(
                task_id=task_id, cancel_event=cancel_event,
            )
            return "豆包概念重塑完成"

        TaskManager().submit_task(
            name="豆包概念周度重塑",
            task_type="AI打标",
            coroutine_factory=_doubao_logic,
            cancellable=True,
            unique_key="doubao_sync",
        )

    async def _run_nightly_prediction(self):
        """Execute AI Strategy (20:30)"""
        if not ConfigHandler.is_auto_update_enabled():
            return

        today = get_now().strftime("%Y%m%d")
        if self._last_pred_date == today:
            return

        try:
            client = TushareClient()
            is_trading = await ThreadPoolManager().run_async(
                TaskType.IO, client.is_trading_day, today,
            )  # TODO: P0-4 Phase 2 will make this fully async
            if not is_trading:
                logger.info(
                    f"[Scheduler] Prediction skipped ({today} is not a trading day)",
                )
                return
        except Exception as e:
            logger.warning(
                f"[Scheduler] Trade calendar check failed for prediction: {e}",
            )
            if get_now().weekday() >= 5:
                return

        from services.task_manager import TaskManager

        async def _prediction_logic(task_id: str, **kwargs):
            tm = TaskManager()
            from strategies.ai_strategy import AISelectionStrategy

            tm.update_progress(task_id, 0.1, I18n.get("sched_pred_init"))
            processor = DataProcessor()
            await processor.init_data()

            tm.update_progress(task_id, 0.2, I18n.get("sched_pred_prepare"))
            await processor.prepare_market_data()

            tm.update_progress(task_id, 0.3, I18n.get("sched_pred_context"))
            context = await processor.get_strategy_data()
            if not context:
                raise RuntimeError(I18n.get("sched_pred_no_context"))
            context["data_processor"] = processor

            tm.update_progress(task_id, 0.5, I18n.get("sched_pred_running"))

            # Inject progress callback so strategy.filter() reports AI analysis sub-progress
            def _ai_progress(current, total, msg):
                # Map to 50%→90% range
                sub_pct = 0.5 + (current / max(total, 1)) * 0.4
                tm.update_progress(task_id, sub_pct, f"[{current}/{total}] {msg}")

            context["on_progress"] = _ai_progress

            strategy = AISelectionStrategy()
            result_df = await strategy.filter(context)

            if result_df is not None and not result_df.empty:
                tm.update_progress(task_id, 0.9, I18n.get("sched_pred_saving"))
                rm = ReviewManager()
                await rm.save_results("AI_Auto_Nightly", result_df)
                self._last_pred_date = today
                return I18n.get("sched_pred_done_found", count=len(result_df))
            self._last_pred_date = today
            return I18n.get("sched_pred_done_empty")

        TaskManager().submit_task(
            name=I18n.get("sched_task_prediction", date=today),
            task_type=I18n.get("task_type_ai_screening"),
            coroutine_factory=_prediction_logic,
            cancellable=False,
        )

    def get_status(self) -> dict:
        """Get scheduler status for UI display"""
        enabled = ConfigHandler.is_auto_update_enabled()
        scheduled_time = ConfigHandler.get_auto_update_time()

        # In APScheduler, next_run_time is available on jobs
        next_run = "N/A"
        job = self.scheduler.get_job("daily_update")
        if job and job.next_run_time:
            next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "enabled": enabled,
            "scheduled_time": scheduled_time,
            "running": self.scheduler.running,
            "last_update": self._last_update_date,
            "last_prediction": self._last_pred_date,
            "next_run": next_run,
        }


# Global scheduler instance
scheduler = SchedulerService()
