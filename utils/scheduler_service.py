"""
Scheduler service for automatic data updates.
Runs as a background task within the Flet application using APScheduler.

C-P1-6 fix: Idempotency keys (last run dates) are stored in the database
(app_state table) as the primary source of truth. ConfigHandler (user_settings.json)
is used only as a startup cache for fast access before the DB is available.
"""

import asyncio
import logging
import threading

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from data.data_processor import DataProcessor
from data.persistence.review_manager import ReviewManager
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, classify_severity
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"
_CFG_LAST_AI_CONCEPT_REFRESH = "scheduler_last_ai_concept_refresh"

_DB_KEY_DAILY_UPDATE = "sched_last_daily_update"
_DB_KEY_NIGHTLY_PREDICTION = "sched_last_nightly_prediction"
_DB_KEY_AI_CONCEPT_REFRESH = "sched_last_ai_concept_refresh"


from utils.singleton_registry import register_singleton


@register_singleton
class SchedulerService:
    """
    Background scheduler for automatic data updates.
    Uses AsyncIOScheduler to manage jobs safely within the asyncio event loop.
    """

    _instance = None
    _initialized = False
    _lock = threading.Lock()  # Thread-safe singleton

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            if cls._instance is not None and hasattr(cls._instance, "scheduler"):
                cls._safe_shutdown_scheduler(cls._instance.scheduler, context="reset")
            cls._instance = None
            cls._initialized = False

    @staticmethod
    def _safe_shutdown_scheduler(scheduler, *, context: str) -> None:
        """Safely shutdown AsyncIOScheduler, checking event loop availability.

        Args:
            scheduler: The AsyncIOScheduler instance to shutdown.
            context: Description of the calling context for logging (e.g. "reset", "atexit").
        """
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop already closed")
            if scheduler.running:
                scheduler.shutdown(wait=False)
                logger.info("[Scheduler] Shutdown completed during %s", context)
        except RuntimeError:
            logger.debug("[Scheduler] Event loop unavailable during %s, skipping graceful shutdown", context)
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Error during shutdown (%s) (%s): %s",
                context,
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    @classmethod
    def _atexit_cleanup(cls):
        """C-P2-3: Centralized atexit cleanup via singleton_registry.
        Stops APScheduler as a last-resort fallback when normal async
        shutdown is not taken.
        """
        inst = cls._instance
        if inst is not None and hasattr(inst, "scheduler"):
            cls._safe_shutdown_scheduler(inst.scheduler, context="atexit")

    def __init__(self):
        if self._initialized:
            return

        # Initialize AsyncIOScheduler with explicit timezone
        # 'apscheduler.job_defaults.max_instances': 1 ensures we don't overlap runs
        # 'misfire_grace_time': 60 allows jobs to run up to 60s late during heavy load
        # timezone='Asia/Shanghai' ensures consistent scheduling regardless of server location
        self.scheduler = AsyncIOScheduler(
            job_defaults={
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
            timezone="Asia/Shanghai",
        )
        self._last_update_date = ConfigHandler.get_setting(_CFG_LAST_DAILY_UPDATE)
        self._last_pred_date = ConfigHandler.get_setting(_CFG_LAST_NIGHTLY_PREDICTION)
        self._last_ai_concept_date = ConfigHandler.get_setting(_CFG_LAST_AI_CONCEPT_REFRESH)
        self._db_state_loaded = False
        self._initialized = True
        logger.info("[Scheduler] Initialized (APScheduler, Timezone: Asia/Shanghai)")

    @staticmethod
    def _persist_run_date(config_key: str, value: str | None):
        ConfigHandler.save_config({config_key: value or ""})

    async def _persist_run_date_db(self, db_key: str, config_key: str, value: str | None):
        from data.cache.cache_manager import CacheManager
        from data.persistence.app_state_service import set_app_state

        engine = CacheManager._instance.engine if CacheManager._instance else None
        if engine is not None:
            await set_app_state(engine, db_key, value or "")
        self._persist_run_date(config_key, value)

    def _mark_daily_update_done(self, today_str: str):
        self._last_update_date = today_str
        self._persist_run_date(_CFG_LAST_DAILY_UPDATE, today_str)

    async def _mark_daily_update_done_db(self, today_str: str):
        self._last_update_date = today_str
        await self._persist_run_date_db(_DB_KEY_DAILY_UPDATE, _CFG_LAST_DAILY_UPDATE, today_str)

    def _mark_nightly_prediction_done(self, today_str: str):
        self._last_pred_date = today_str
        self._persist_run_date(_CFG_LAST_NIGHTLY_PREDICTION, today_str)

    async def _mark_nightly_prediction_done_db(self, today_str: str):
        self._last_pred_date = today_str
        await self._persist_run_date_db(_DB_KEY_NIGHTLY_PREDICTION, _CFG_LAST_NIGHTLY_PREDICTION, today_str)

    def start(self):
        """Start the scheduler"""
        if self.scheduler.running:
            return

        self._schedule_jobs()

        from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_MISSED

        self.scheduler.add_listener(self._on_job_missed, EVENT_JOB_MISSED)
        self.scheduler.add_listener(self._on_job_error, EVENT_JOB_ERROR)

        try:
            self.scheduler.start()
            logger.info("[Scheduler] Started")

            self.scheduler.add_job(
                self._watch_config_changes,
                "interval",
                seconds=30,
                id="config_watchdog",
                replace_existing=True,
            )

            self.scheduler.add_job(
                self._load_db_state,
                "date",
                id="load_db_state",
                replace_existing=True,
            )
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Failed to start (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    async def _load_db_state(self):
        """Load idempotency state from database (primary source of truth).

        Called once after scheduler starts. Overrides ConfigHandler cache
        values with database values, ensuring consistency even if
        user_settings.json was externally modified.
        """
        if self._db_state_loaded:
            return

        from data.cache.cache_manager import CacheManager
        from data.persistence.app_state_service import get_app_state

        engine = CacheManager._instance.engine if CacheManager._instance else None
        if engine is None:
            logger.debug("[Scheduler] DB not available, using ConfigHandler cache for idempotency state")
            return

        try:
            db_daily = await get_app_state(engine, _DB_KEY_DAILY_UPDATE)
            db_pred = await get_app_state(engine, _DB_KEY_NIGHTLY_PREDICTION)
            db_ai_concept = await get_app_state(engine, _DB_KEY_AI_CONCEPT_REFRESH)

            if db_daily is not None:
                self._last_update_date = db_daily
            if db_pred is not None:
                self._last_pred_date = db_pred
            if db_ai_concept is not None:
                self._last_ai_concept_date = db_ai_concept

            self._db_state_loaded = True
            logger.info(
                "[Scheduler] DB state loaded: daily=%s, pred=%s, ai_concept=%s",
                self._last_update_date,
                self._last_pred_date,
                self._last_ai_concept_date,
            )
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Failed to load DB state, using ConfigHandler cache (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )

    def _on_job_missed(self, event):
        """Handle missed job events with clear logging"""
        job_id = event.job_id
        run_time = event.scheduled_run_time
        logger.warning(
            "[Scheduler] ⚠️ JOB MISSED: '%s' was skipped because the system was busy (Scheduled: %s)",
            job_id,
            run_time,
        )

    def _on_job_error(self, event):
        """Handle job error events, suppressing CancelledError during shutdown"""
        import asyncio

        if event.exception and isinstance(event.exception, asyncio.CancelledError):
            logger.info(
                "[Scheduler] Job '%s' cancelled during shutdown (expected)",
                event.job_id,
            )
        else:
            logger.error(
                "[Scheduler] Job '%s' raised an exception: %s",
                event.job_id,
                event.exception,
            )

    def stop(self):
        """Stop the scheduler"""
        logger.info("Stopping scheduler... (running=%s)", self.scheduler.running)
        if self.scheduler.running:
            self._safe_shutdown_scheduler(self.scheduler, context="stop")
        else:
            logger.info("Scheduler was not running.")

    def _check_config_sync(self) -> dict:
        """Synchronously check config (runs in thread pool)"""
        return {
            "time": ConfigHandler.get_auto_update_time(),
            "enabled": ConfigHandler.is_auto_update_enabled(),
            "ai_concept_time": ConfigHandler.get_ai_concept_schedule_time(),
            "ai_concept_enabled": ConfigHandler.is_ai_concept_schedule_enabled(),
        }

    async def _watch_config_changes(self):
        """Monitor config changes and reload jobs if needed"""

        # Run sync config check in thread pool to avoid blocking event loop
        try:
            current_config = await ThreadPoolManager().run_async(
                TaskType.IO,
                self._check_config_sync,
            )
        except asyncio.CancelledError:
            logger.info(
                "[Scheduler] _watch_config_changes cancelled (likely shutting down)",
            )
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Config check failed (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
            return

        if not hasattr(self, "_last_known_config"):
            self._last_known_config = current_config
            return

        current_time = current_config["time"]
        current_enabled = current_config["enabled"]
        current_ai_concept_time = current_config["ai_concept_time"]
        current_ai_concept_enabled = current_config["ai_concept_enabled"]

        changed = False
        if current_time != self._last_known_config["time"]:
            logger.info(
                "[Scheduler] Detected schedule time change: %s -> %s",
                self._last_known_config["time"],
                current_time,
            )
            changed = True

        if current_enabled != self._last_known_config["enabled"]:
            logger.info(
                "[Scheduler] Detected enable status change: %s -> %s",
                self._last_known_config["enabled"],
                current_enabled,
            )
            changed = True

        if current_ai_concept_time != self._last_known_config.get(
            "ai_concept_time",
        ) or current_ai_concept_enabled != self._last_known_config.get("ai_concept_enabled"):
            logger.info("[Scheduler] Detected AI Concept schedule config change")
            changed = True

        if changed:
            logger.info("[Scheduler] Reloading jobs...")
            self._schedule_jobs()
            self._last_known_config = current_config

    def _schedule_jobs(self):
        """Register jobs with the scheduler"""
        # Only remove business jobs, NOT the config_watchdog
        for job_id in ["daily_update", "nightly_prediction", "ai_concept_daily_refresh"]:
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
        logger.info("[Scheduler] Scheduled Daily Update at %02d:%02d", hour, minute)

        # 2. Nightly AI Prediction Job (Default 20:30)
        # In the future, this could be configurable
        self.scheduler.add_job(
            self._run_nightly_prediction,
            CronTrigger(hour=20, minute=30),
            id="nightly_prediction",
            replace_existing=True,
        )
        logger.info("[Scheduler] Scheduled Nightly Prediction at 20:30")

        # 3. AI Concept Tagging Job (Daily)
        ai_concept_time = ConfigHandler.get_ai_concept_schedule_time() or "18:00"
        try:
            dh, dm = map(int, ai_concept_time.split(":"))
        except (ValueError, TypeError, AttributeError):
            dh, dm = 18, 0

        self.scheduler.add_job(
            self._run_ai_concept_tagger,
            CronTrigger(hour=dh, minute=dm),
            id="ai_concept_daily_refresh",
            replace_existing=True,
        )
        logger.info(
            "[Scheduler] Scheduled AI Concept Daily Refresh at %02d:%02d",
            dh,
            dm,
        )

    async def _run_daily_update(self):
        """Execute the data update (16:30)"""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        # Global enable check
        if not ConfigHandler.is_auto_update_enabled():
            logger.info("[Scheduler] Update skipped (Auto-update disabled)")
            return

        today = get_now().date()
        today_str = today.strftime("%Y%m%d")
        if self._last_update_date == today_str:
            logger.info("[Scheduler] Update skipped (Already updated today)")
            return

        # Check Trading Day
        try:
            processor = DataProcessor()
            is_trading = await processor.trade_calendar.is_trading_day(today)
            if not is_trading:
                logger.info(
                    "[Scheduler] Update skipped (%s is not a trading day)",
                    today_str,
                )
                return
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Trade calendar check failed (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
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
            has_errors = hasattr(result, "errors") and bool(result.errors)
            if has_errors:
                logger.warning("[Scheduler] Daily update completed with errors, NOT marking done")
            else:
                await self._mark_daily_update_done_db(today_str)
            # NOTE: Never use `if result` here.
            # Pandas DataFrame truth-value is ambiguous and raises ValueError.
            if result is None:
                added = 0
            elif hasattr(result, "added"):
                added = getattr(result, "added", 0)  # type: ignore[union-attr]
            elif hasattr(result, "empty"):
                # DataFrame/Series fallback: treat row count as added amount
                try:
                    added = 0 if result.empty else len(result)  # type: ignore[union-attr]
                except (ValueError, TypeError, AttributeError):
                    added = 0
            else:
                added = result
            return I18n.get("sched_daily_done", added=added)

        TaskManager().submit_task(
            name=I18n.get("sched_task_daily_update", date=today_str),
            task_type=I18n.get("sched_task_type_daily"),
            coroutine_factory=_daily_update_logic,
            cancellable=False,
            unique_key="daily_sync",
        )

    async def _run_ai_concept_tagger(self):
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        if not ConfigHandler.is_ai_concept_schedule_enabled():
            return

        today_str = get_now().strftime("%Y%m%d")
        if self._last_ai_concept_date == today_str:
            logger.debug("[Scheduler] AI Concept tagging already done for %s, skipping", today_str)
            return

        from services.task_manager import TaskManager

        async def _ai_concept_logic(task_id: str, **kwargs):
            tm = TaskManager()
            cancel_event = tm.get_cancel_event(task_id)
            processor = DataProcessor()
            # T8 fix: 若任务已被取消则 update_progress 返回 False，立即抛 CancelledError 早退
            # M3 fix: CancelledError 带消息，便于日志区分"调度取消"与"框架取消"
            if not tm.update_progress(task_id, 0.05, I18n.get("sched_ai_concept_clear_history")):
                raise asyncio.CancelledError("task cancelled by scheduler (update_progress returned False)")
            # Scheduled run: manual_trigger=False → only sync free data sources, no LLM call
            await processor.run_ai_concept_tagging(
                task_id=task_id,
                cancel_event=cancel_event,
                manual_trigger=False,
            )
            self._last_ai_concept_date = today_str
            await self._persist_run_date_db(_DB_KEY_AI_CONCEPT_REFRESH, _CFG_LAST_AI_CONCEPT_REFRESH, today_str)
            return I18n.get("sched_ai_concept_done")

        TaskManager().submit_task(
            name=I18n.get("sched_ai_concept_task_name"),
            task_type=I18n.get("sched_ai_concept_task_type"),
            coroutine_factory=_ai_concept_logic,
            cancellable=True,
            unique_key="ai_concept_sync",
        )

    async def _run_nightly_prediction(self):
        """Execute AI Strategy (20:30)"""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        if not ConfigHandler.is_auto_update_enabled():
            return

        today = get_now().date()
        today_str = today.strftime("%Y%m%d")
        if self._last_pred_date == today_str:
            return

        try:
            processor = DataProcessor()
            is_trading = await processor.trade_calendar.is_trading_day(today)
            if not is_trading:
                logger.info(
                    "[Scheduler] Prediction skipped (%s is not a trading day)",
                    today_str,
                )
                return
        except Exception as e:
            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            if severity == "system":
                _log = logger.critical
            elif severity == "recoverable":
                _log = logger.warning
            else:
                _log = logger.error
            _log(
                "[Scheduler] Trade calendar check failed for prediction (%s): %s",
                error_info["code"],
                DataSanitizer.sanitize_error(e),
                exc_info=True,
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
                analysis_trade_date = context.get("trade_date")
                if not analysis_trade_date:
                    raise RuntimeError("Nightly prediction context missing trade_date; refusing to save results")
                import uuid as _uuid

                run_id = _uuid.uuid4().hex[:16]
                # R.3.1: 存储 i18n key (非 identifier)。
                # 这里有意使用 "strategy_ai_nightly_name" 而非 AISelectionStrategy.name_key
                # (= "strategy_ai_active_name")：夜间定时预测与用户交互式 AI 选股是两个
                # 语义场景，UI 上需区分显示（"夜间 AI 预测" vs "AI 主动选股"），非 DRY 违反。
                await rm.save_results(
                    "strategy_ai_nightly_name",
                    result_df,
                    trade_date=analysis_trade_date,
                    run_id=run_id,
                    params_snapshot={},
                )
                await self._mark_nightly_prediction_done_db(today_str)
                return I18n.get("sched_pred_done_found", count=len(result_df))

            logger.info("[Scheduler] Nightly prediction found no candidates, NOT marking done to allow retry")
            return I18n.get("sched_pred_done_empty")

        TaskManager().submit_task(
            name=I18n.get("sched_task_prediction", date=today_str),
            task_type=I18n.get("task_type_ai_screening"),
            coroutine_factory=_prediction_logic,
            cancellable=False,
            unique_key="nightly_prediction",
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
