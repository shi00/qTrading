"""
Scheduler service for automatic data updates.
Runs as a background task within the Flet application using APScheduler.

C-P1-6 fix: Idempotency keys (last run dates) are stored in the database
(app_state table) as the primary source of truth. ConfigHandler (user_settings.json)
is used only as a startup cache for fast access before the DB is available.
"""

import asyncio
import datetime
import logging
import threading
from collections.abc import Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from core.i18n import Message
from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from utils.time_utils import get_now, parse_date, to_yyyymmdd_str

logger = logging.getLogger(__name__)

_CFG_LAST_DAILY_UPDATE = "scheduler_last_daily_update"
_CFG_LAST_NIGHTLY_PREDICTION = "scheduler_last_nightly_prediction"
_CFG_LAST_AI_CONCEPT_REFRESH = "scheduler_last_ai_concept_refresh"

_DB_KEY_DAILY_UPDATE = "sched_last_daily_update"
_DB_KEY_NIGHTLY_PREDICTION = "sched_last_nightly_prediction"
_DB_KEY_AI_CONCEPT_REFRESH = "sched_last_ai_concept_refresh"

# D6-6: 依赖注入的必需 job（启动期契约）。这些 job 由 app 层装配注册，缺失即装配
# 遗漏，应在启动期立即暴露而非等触发时仅留一条 warning 静默跳过。app 层 `_register_scheduler_jobs`
# 无条件注册下列全部 job（nightly_prediction 与 AI 功能开关无关）。
_REQUIRED_JOBS: frozenset[str] = frozenset({"nightly_prediction"})


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
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] Error during shutdown (%s) (%s): %s",
                context,
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
        # CON-01: double-checked locking。__new__ 持锁创建实例，但 __init__ 在锁外执行会
        # 导致并发首次访问时两线程都见 _initialized=False 而重复初始化。初始化整体移入
        # 锁内并二次检查，保证恰好执行一次。
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            # Initialize AsyncIOScheduler with explicit timezone
            # 'apscheduler.job_defaults.max_instances': 1 ensures we don't overlap runs
            # 'misfire_grace_time': 1800 (D6-1) 允许任务在重负载下最多晚 30 分钟执行；
            # 超过宽限期触发 _on_job_missed，由补偿机制（_catch_up_missed_updates）回补遗漏交易日。
            # timezone='Asia/Shanghai' ensures consistent scheduling regardless of server location
            self.scheduler = AsyncIOScheduler(
                job_defaults={
                    "max_instances": 1,
                    "misfire_grace_time": 1800,
                },
                timezone="Asia/Shanghai",
            )
            self._last_update_date = ConfigHandler.get_setting(_CFG_LAST_DAILY_UPDATE)
            self._last_pred_date = ConfigHandler.get_setting(_CFG_LAST_NIGHTLY_PREDICTION)
            self._last_ai_concept_date = ConfigHandler.get_setting(_CFG_LAST_AI_CONCEPT_REFRESH)
            self._db_state_loaded = False
            # review01-A2-1: 业务 job 注册表（services/scheduled_jobs/ 提供 build_<job>_job），
            # SchedulerService 仅调度注册的 callable，不感知具体业务类。
            self._registered_jobs: dict[str, Callable[[object], Awaitable[object]]] = {}
            # F1 (OSS 检视): 持引用补偿任务，防止事件循环弱引用下任务被 GC 静默丢失
            # （_on_job_missed 创建的 create_task 在无强引用时可能中途被回收）。
            self._catchup_tasks: set[asyncio.Task] = set()
            self._initialized = True
            logger.info("[Scheduler] Initialized (APScheduler, Timezone: Asia/Shanghai)")

    def register_job(self, job_name: str, job_fn: Callable[[object], Awaitable[object]]) -> None:
        """注册定时业务 job（review01-A2-1 依赖注入）。

        由 app 层启动装配调用（app 可合法 import services + utils）。job_fn 接收
        SchedulerService 实例（提供 idempotency 状态与进度上报接口），返回 None。
        """
        self._registered_jobs[job_name] = job_fn
        logger.info("[Scheduler] Registered job '%s'", job_name)

    @staticmethod
    def _persist_run_date(config_key: str, value: str | None):
        ConfigHandler.save_config({config_key: value or ""})

    async def _persist_run_date_db(self, db_key: str, config_key: str, value: str | None):
        from data.persistence.app_state_service import (
            set_app_state_max,
        )  # lazy-import: 同上（DB idempotency 状态写入；GREATEST 单调写，R22）
        from data.persistence.engine_provider import (
            get_engine,
            is_disposed,
        )  # lazy-import: 同上（R5 引擎状态守卫，review03-C11 中立模块）

        engine = get_engine()
        if engine is None:
            # DB 尚未就绪（正常启动窗口）：仅写配置缓存，静默（与 _load_db_state 一致）
            pass
        elif is_disposed(engine):
            # REVIEW-06 TO-04: 引擎已释放仍写 DB 会抛 EngineDisposedError 并逃逸到日更逻辑。
            # 明确告警并降级为仅写配置缓存，避免下次启动整日重跑浪费配额。
            logger.warning("[Scheduler] 引擎已释放，幂等键 %s 未持久化到 DB（仅写入配置缓存）", db_key)
        else:
            # REVIEW-06 TO-02: 幂等键为高水位语义（"已成功同步到的最大日期"，零填充 YYYYMMDD
            # 字典序与时间序一致），必须单调写（set_app_state_max，SQL GREATEST 保护），
            # 防止补偿任务与日更任务并发时 last-writer-wins 把水位写回旧日期。
            await set_app_state_max(engine, db_key, value or "")
        self._persist_run_date(config_key, value)

    async def _mark_daily_update_done_db(self, today_str: str):
        # REVIEW-06 TO-02: 内存侧同样单调（与 DB GREATEST 语义一致），防止并发下水位倒退。
        self._last_update_date = max(self._last_update_date or "", today_str)
        await self._persist_run_date_db(_DB_KEY_DAILY_UPDATE, _CFG_LAST_DAILY_UPDATE, today_str)

    async def _mark_nightly_prediction_done_db(self, today_str: str):
        # REVIEW-06 TO-02: 内存侧单调（同 _mark_daily_update_done_db）。
        self._last_pred_date = max(self._last_pred_date or "", today_str)
        await self._persist_run_date_db(_DB_KEY_NIGHTLY_PREDICTION, _CFG_LAST_NIGHTLY_PREDICTION, today_str)

    def start(self):
        """Start the scheduler"""
        if self.scheduler.running:
            return

        missing = _REQUIRED_JOBS - self._registered_jobs.keys()
        if missing:
            # D6-6: 必需 job 未注册即装配遗漏（app 层依赖注入漏调/重构漏改）。启动期失败
            # 比每晚触发时留一条 warning 静默跳过好：装配问题在开发/测试阶段立即暴露。
            raise RuntimeError(
                f"[Scheduler] 必需的定时 job 未注册: {sorted(missing)} "
                "(call SchedulerService.register_job during app bootstrap)"
            )

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
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] Failed to start (%s): %s",
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

        from data.cache.cache_manager import CacheManager  # lazy-import: 启动性能——DB 就绪后才读取 idempotency 状态
        from data.persistence.app_state_service import get_app_state  # lazy-import: 同上（DB idempotency 状态读取）

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
            # D6-1: DB 幂等状态就绪后立即检查遗漏交易日并启动补偿。
            await self._catch_up_missed_updates()
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] Failed to load DB state, using ConfigHandler cache (%s): %s",
                exc_info=True,
            )

    def _on_job_missed(self, event):
        """Handle missed job events with clear logging + catch-up trigger (D6-1).

        misfire（超过 misfire_grace_time 仍未执行）意味着当天任务永久丢失。
        业务 job 被跳过时安排 _catch_up_missed_updates 检查遗漏交易日并补偿。
        """
        job_id = event.job_id
        run_time = event.scheduled_run_time
        logger.warning(
            "[Scheduler] ⚠️ JOB MISSED: '%s' was skipped because the system was busy (Scheduled: %s). "
            "Triggering catch-up check.",
            job_id,
            run_time,
        )
        if job_id in ("daily_update", "nightly_prediction", "ai_concept_daily_refresh", "review_backfill"):
            # _on_job_missed 为 APScheduler listener 回调；AsyncIOScheduler 的 listener
            # 在事件循环线程执行，可直接获取 running loop 调度异步补偿协程。
            # include_today=True（D6-1 Q21）：misfire 已过计划时刻+宽限（已收盘），
            # 若仅补到昨天则"当天永久跳过"依旧存在。
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._catch_up_missed_updates(include_today=True))
                # F1 (OSS 检视): 事件循环只持弱引用，create_task 返回值须保存强引用直至任务
                # 完成，否则补偿任务可能在执行中途被 GC 静默丢弃（漏跑且无日志）。
                self._catchup_tasks.add(task)
                task.add_done_callback(self._catchup_tasks.discard)
            except RuntimeError:
                logger.debug("[Scheduler] No running loop for catch-up on job missed, skipping")

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
                DataSanitizer.sanitize_error(event.exception, show_traceback=True),
                exc_info=True,
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
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] Config check failed (%s): %s",
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

        # D6-1: 复用 30 秒周期任务检查遗漏交易日并补偿（无新增定时器）。
        # 幂等由 unique_key 去重 + 同步层 check_data_exists 缓存跳过共同保证。
        await self._catch_up_missed_updates()

    def _schedule_jobs(self):
        """Register jobs with the scheduler"""
        # Only remove business jobs, NOT the config_watchdog
        for job_id in ["daily_update", "nightly_prediction", "ai_concept_daily_refresh", "review_backfill"]:
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

        # 2. Nightly AI Prediction Job
        # Task 7.3: 时辰从 ConfigHandler.get_nightly_prediction_time() 读取 (原硬编码 20:30)
        nightly_time = ConfigHandler.get_nightly_prediction_time() or "20:30"
        try:
            n_hour, n_minute = map(int, nightly_time.split(":"))
        except (ValueError, TypeError, AttributeError):
            n_hour, n_minute = 20, 30

        self.scheduler.add_job(
            self._run_nightly_prediction,
            CronTrigger(hour=n_hour, minute=n_minute),
            id="nightly_prediction",
            replace_existing=True,
        )
        logger.info("[Scheduler] Scheduled Nightly Prediction at %02d:%02d", n_hour, n_minute)

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

        # 4. T+5 Review Backfill Job (D2-4)
        # 晚于默认日跟新 16:30，确保当日行情已同步后再回填远期收益。
        self.scheduler.add_job(
            self._run_review_backfill,
            CronTrigger(hour=17, minute=0),
            id="review_backfill",
            replace_existing=True,
        )
        logger.info("[Scheduler] Scheduled Review Backfill at 17:00")

        # REVIEW-06 TO-03: 后三者（回填/概念/预测）都消费日更产出的当日行情，必须晚于日更
        # 执行，否则会用 T-1 数据出选股结果且用户无感。review_backfill 硬编码 17:00 而
        # auto_update_time 用户可调，顺序不变量需显式校验（方案 B，低风险短期落地）。
        self._validate_job_ordering((hour, minute), (17, 0), (dh, dm), (n_hour, n_minute))

    @staticmethod
    def _validate_job_ordering(daily_hm: tuple[int, int], backfill_hm, concept_hm, pred_hm: tuple[int, int]) -> None:
        """REVIEW-06 TO-03: 校验依赖 job 是否晚于日更，违反时 warning（消费 T-1 数据）。

        日更（daily_update）产出当日行情，review_backfill / ai_concept_daily_refresh /
        nightly_prediction 均以其为先决条件。任一依赖 job 此刻不晚于日更即顺序违反
        （相等意味着同日同时段执行，顺序未保证，同样按违反处理）。
        """
        for name, hm in (
            ("review_backfill", backfill_hm),
            ("ai_concept_daily_refresh", concept_hm),
            ("nightly_prediction", pred_hm),
        ):
            if hm <= daily_hm:
                logger.warning(
                    "[Scheduler] %s (%02d:%02d) 早于日更 (%02d:%02d)，将消费 T-1 数据（请将日更时间调早或该任务调晚）",
                    name,
                    hm[0],
                    hm[1],
                    daily_hm[0],
                    daily_hm[1],
                )

    async def _infer_baseline_from_db(self) -> str | None:
        """REVIEW-06 TO-01: 以本地已落库的最新交易日（daily_quotes.MAX(trade_date)）作为补偿下界自举。

        这是"已经同步到哪天"的天然真相源，比调度器自身维护的影子状态更可信，且仅在基准
        缺失时调用一次（成功后 _last_update_date 非空，后续走常规检查路径）。查询失败或
        本地无行情数据时返回 None，由调用方告警跳过并交由后续周期重试（幂等）。
        """
        from data.data_processor import DataProcessor  # lazy-import: 启动性能——仅自举路径加载

        try:
            processor = DataProcessor()
            latest = await processor.cache.quote_dao.get_latest_trade_date()
        except asyncio.CancelledError:
            raise  # R2: 配合优雅停机，不得吞没
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] 补偿基准自举查询失败 (%s): %s",
                exc_info=True,
            )
            return None
        if latest is None:
            return None
        return to_yyyymmdd_str(latest)

    async def _catch_up_missed_updates(self, include_today: bool = False) -> None:
        """D6-1 启动/周期/misfire 补偿：检查自 _last_update_date 以来是否有遗漏交易日并回补。

        桌面应用无法保证在 cron 时刻处于运行状态，且 misfire_grace_time 在启动期/繁忙期
        极易超时。纯时间触发会导致当天永久丢失且用户无感。本方法以"上次成功日期"为权威
        状态驱动补偿。三处触发：_load_db_state 后 / _watch_config_changes 内 / _on_job_missed 内。
        include_today=True 仅由 _on_job_missed 传入：misfire 意味着计划时间已过（16:30 + 1800s
        宽限 ≥ 17:00，已收盘），此时今天也应回补，否则"当天永久跳过"依旧存在。
        幂等由补偿任务独立 unique_key 去重 + 同步层 check_data_exists 缓存跳过共同保证。
        """
        if not self._last_update_date:
            # REVIEW-06 TO-01: 无基准（None 或空串，_persist_run_date 以空串表示无值）时不再静默
            # 早退——注释原称"首次运行由全量初始化路径负责"，但该路径并不写调度幂等键（唯一写入
            # 点在补偿/日更逻辑自身），形成闭环依赖：从未在 cron 时刻运行过的用户基准永远为空、
            # 补偿静默失效（自动更新永久停止）。改为以本地已落库的最新交易日作为可信补偿下界自举，
            # 该基准是"已经同步到哪天"的天然真相源，且仅在缺失时查询一次（幂等）。
            baseline = await self._infer_baseline_from_db()
            if baseline is None:
                logger.warning(
                    "[Scheduler] 无补偿基准且本地无行情数据（daily_quotes 为空），跳过补偿（需先完成全量初始化）"
                )
                return
            logger.info("[Scheduler] 补偿基准缺失，以本地最新交易日 %s 自举", baseline)
            self._last_update_date = baseline
        from data.data_processor import DataProcessor  # lazy-import: 启动性能——仅补偿检查时加载
        from services.task_manager import TaskManager  # lazy-import: 启动性能——仅提交补偿任务时加载

        last_dt = parse_date(self._last_update_date).date()
        today = get_now().date()
        # 常规路径只补 [last_update_date+1, 昨天]：今天由 16:30 cron 负责，避免在盘中提前
        # 同步今日数据（数据不完整）。misfire 路径（include_today=True）已过计划时刻+宽限，
        # 今天数据已收盘，end=today 一并回补。
        end = today if include_today else today - datetime.timedelta(days=1)
        if end <= last_dt:
            return

        processor = DataProcessor()
        missed = await processor.trade_calendar.get_trade_dates(start_date=last_dt, end_date=end)
        # 过滤：get_trade_dates 是闭区间，需排除基准日本身
        missed = [d for d in missed if d > last_dt]
        if not missed:
            return

        logger.warning(
            "[Scheduler] 检测到 %d 个遗漏交易日，提交补偿同步: %s",
            len(missed),
            [d.strftime("%Y%m%d") for d in missed[:5]],
        )
        task_id = TaskManager().submit_task(
            name=Message("sched_task_catchup", {"days": len(missed)}),
            task_type=Message("sched_task_type_daily"),
            coroutine_factory=self._catchup_logic,
            cancellable=True,
            unique_key="daily_sync_catchup",  # 独立 key，与常规同步解耦（D6-1 Q2 修订）
            missed_dates=missed,
        )
        # D6-5: 返回值 None（去重命中/无事件循环）时，本次补偿未真正提交；不写幂等键，
        # 下个补偿周期（30s 看门狗 / misfire）会重新检查遗漏并尝试。
        if task_id is None:
            logger.warning(
                "[Scheduler] Catch-up task not submitted (dedup hit or no event loop); will re-check on next cycle"
            )

    async def _catchup_logic(self, task_id: str, missed_dates: list, **kwargs):
        """D6-1 补偿执行：对每个遗漏交易日执行单日市场快照同步。

        同步层 `HistoricalSyncStrategy.sync_daily_market_snapshot(trade_date=d)` 已有
        check_data_exists 缓存跳过，重复同步安全（天然续传）。逐日捕获异常（CancelledError
        重抛、其他异常记日志后继续），单日失败不中断整批；全部结束后按 is_complete 判定
        是否推进幂等键，已成功日期下次被 check_data_exists 跳过。
        """
        from services.task_manager import TaskManager  # lazy-import: 启动性能
        from data.data_processor import DataProcessor  # lazy-import: 启动性能
        from data.sync.base import SyncResult  # lazy-import: 启动性能

        tm = TaskManager()
        processor = DataProcessor()
        sync_result = SyncResult()
        total = len(missed_dates)
        for i, d in enumerate(missed_dates):
            if not tm.update_progress(
                task_id, i / total, Message("sched_catchup_progress", {"date": d.strftime("%Y%m%d")})
            ):
                raise asyncio.CancelledError("catchup cancelled (update_progress returned False)")
            try:
                await processor.sync_daily_market_snapshot(trade_date=d, sync_result=sync_result)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[Scheduler] Catch-up failed for %s, continuing (%s): %s",
                    d.strftime("%Y%m%d"),
                    exc_info=True,
                )
        # D1-2: 幂等键以 is_complete 为准（关键表全部成功）
        if sync_result.is_complete:
            latest = missed_dates[-1]
            await self._mark_daily_update_done_db(latest.strftime("%Y%m%d"))
            return Message("sched_catchup_done", {"days": total})
        logger.warning(
            "[Scheduler] Catch-up NOT complete (critical=%s), NOT marking done",
            sync_result.failed_critical_tables,
        )
        return Message("sched_catchup_partial", {"days": total})

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
            from data.data_processor import DataProcessor  # lazy-import: 启动性能——仅交易日检查时加载 DataProcessor

            processor = DataProcessor()
            is_trading = await processor.trade_calendar.is_trading_day(today)
            if is_trading is False:
                logger.info(
                    "[Scheduler] Update skipped (%s is not a trading day)",
                    today_str,
                )
                return
            if is_trading is None:
                logger.warning(
                    "[Scheduler] Update skipped (%s status unknown: offline calendar beyond trusted interval, D2-7)",
                    today_str,
                )
                return
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Scheduler] Trade calendar check failed (%s): %s",
                exc_info=True,
            )
            # D6-2: 降级到离线日历（三级降级链的最后一级），而非退化为 weekday 判断。
            # weekday 无法识别法定节假日（全年约 15-20 天），会导致节假日发起无效同步、
            # 消耗 API 配额并可能污染质量分。
            from data.domain_services.offline_calendar import (
                OfflineCalendar,
            )  # lazy-import: 日历降级（契约 5 例外 EX-0016）

            offline_result = OfflineCalendar.is_trading_day(today)
            if offline_result is False:
                logger.info(
                    "[Scheduler] 离线日历判定 %s 非交易日，跳过",
                    today,
                )
                return
            if offline_result is None:
                # 离线日历也无法判定（超出可信区间，D2-7）：保守跳过，交由 D6-1 补偿机制回补。
                logger.warning(
                    "[Scheduler] 交易日无法判定（离线日历超可信区间），跳过本次并交由补偿机制处理",
                )
                return
            # offline_result is True → 继续执行

        # Submit via TaskManager for visibility and persistence
        from services.task_manager import TaskManager  # lazy-import: 启动性能——仅提交任务时加载 TaskManager

        async def _daily_update_logic(task_id: str, **kwargs):
            tm = TaskManager()
            from data.data_processor import DataProcessor  # lazy-import: 启动性能——编排闭包内延迟加载 DataProcessor

            processor = DataProcessor()

            def _progress(current, total, msg):
                tm.update_progress(task_id, current / total if total else 0, msg)

            result = await processor.run_daily_update(progress_callback=_progress)
            # D1-2: 幂等键以 SyncResult.is_complete 为准（关键表全部成功），而非 not errors/无 is_complete。
            # is_complete 缺省 False（保守）：即便收到无法判定的结果也不写入幂等键，宁可下次重跑。
            is_complete = getattr(result, "is_complete", False)
            if is_complete:
                await self._mark_daily_update_done_db(today_str)
            else:
                logger.warning(
                    "[Scheduler] Daily update NOT complete (critical=%s, optional=%s), NOT marking done",
                    getattr(result, "failed_critical_tables", []),
                    getattr(result, "failed_optional_tables", []),
                )
            # NOTE: Never use `if result` here.
            # Pandas DataFrame truth-value is ambiguous and raises ValueError.
            if result is None:
                return Message("sched_daily_done", {"days": 0, "rows": 0})
            days = getattr(result, "days_processed", None)
            if days is not None:
                # D1-4: SyncResult 路径——天数与条数分开展示，避免"天数被当条数"的语义错位。
                rows = getattr(result, "rows_written", 0)  # type: ignore[union-attr]
                if rows == 0 and days > 0:
                    # D1-4: 空日显式警告——处理了交易日却 0 行落库，可能是全市场停牌或权限不足，
                    # 用户无法仅凭数字区分"拉到数据"与"拉到空"，需显式暴露。
                    logger.warning(
                        "[Scheduler] Daily update produced 0 rows across %s trading day(s) "
                        "— possibly empty market or insufficient permission",
                        days,
                    )
                return Message("sched_daily_done", {"days": days, "rows": rows})
            # fallback（D1-2 后 run_daily_update 恒返回 SyncResult，以下为防御旧路径）
            if hasattr(result, "added"):
                added = getattr(result, "added", 0)  # type: ignore[union-attr]
            elif hasattr(result, "empty"):
                # DataFrame/Series fallback: treat row count as added amount
                try:
                    added = 0 if result.empty else len(result)  # type: ignore[union-attr]
                except (ValueError, TypeError, AttributeError):
                    added = 0
            else:
                added = result
            return Message("sched_daily_done", {"days": 0, "rows": added})

        # D6-5: submit_task 返回 None 有两种原因——unique_key 去重命中（正常，跳过合理）
        # 或无可用事件循环（故障，任务未提交）。TaskManager 内部已分别记 warning/error；
        # 此处仅记录调度器视角告警，本次不标记完成，交由 D6-1 补偿机制在下次检查时重试。
        task_id = TaskManager().submit_task(
            name=Message("sched_task_daily_update", {"date": today_str}),
            task_type=Message("sched_task_type_daily"),
            coroutine_factory=_daily_update_logic,
            cancellable=False,
            unique_key="daily_sync",
        )
        if task_id is None:
            logger.warning(
                "[Scheduler] Daily update task not submitted (dedup hit or no event loop); "
                "not marking done, catch-up will retry"
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

        from services.task_manager import TaskManager  # lazy-import: 启动性能——仅提交任务时加载 TaskManager

        async def _ai_concept_logic(task_id: str, **kwargs):
            tm = TaskManager()
            cancel_event = tm.get_cancel_event(task_id)
            from data.data_processor import DataProcessor  # lazy-import: 启动性能——编排闭包内延迟加载 DataProcessor

            processor = DataProcessor()
            # T8 fix: 若任务已被取消则 update_progress 返回 False，立即抛 CancelledError 早退
            # M3 fix: CancelledError 带消息，便于日志区分"调度取消"与"框架取消"
            if not tm.update_progress(task_id, 0.05, Message("sched_ai_concept_clear_history")):
                raise asyncio.CancelledError("task cancelled by scheduler (update_progress returned False)")
            # Scheduled run: manual_trigger=False → only sync free data sources, no LLM call
            await processor.run_ai_concept_tagging(
                task_id=task_id,
                cancel_event=cancel_event,
                manual_trigger=False,
            )
            # REVIEW-06 TO-02: 内存侧单调（同 daily/nightly 标记方法）
            self._last_ai_concept_date = max(self._last_ai_concept_date or "", today_str)
            await self._persist_run_date_db(_DB_KEY_AI_CONCEPT_REFRESH, _CFG_LAST_AI_CONCEPT_REFRESH, today_str)
            return Message("sched_ai_concept_done")

        # D6-5: 检查返回值为 None 的两种情形（去重命中/无事件循环），日志在 TaskManager
        # 内已分别记录，此处仅从调度器视角告警，交由 D6-1 补偿机制兜底。
        task_id = TaskManager().submit_task(
            name=Message("sched_ai_concept_task_name"),
            task_type=Message("sched_ai_concept_task_type"),
            coroutine_factory=_ai_concept_logic,
            cancellable=True,
            unique_key="ai_concept_sync",
        )
        if task_id is None:
            logger.warning("[Scheduler] AI concept task not submitted (dedup hit or no event loop)")

    async def _run_nightly_prediction(self):
        """Execute registered nightly prediction job (review01-A2-1 下沉).

        夜间预测完整编排（交易日检查 + TaskManager 提交 + AI 选股 + 结果保存）已迁移至
        ``services/scheduled_jobs/nightly_prediction.py``。本方法仅调度注册的 job，
        不再感知 AISelectionStrategy/DataProcessor/ReviewManager/TaskManager 等业务类，
        消除 ``utils → strategies`` 方向性违规（契约 5）与隐藏三角依赖（A6）。
        """
        job_fn = self._registered_jobs.get("nightly_prediction")
        if job_fn is None:
            logger.warning(
                "[Scheduler] nightly_prediction job not registered (call SchedulerService.register_job)",
            )
            return
        await job_fn(self)

    async def _run_review_backfill(self):
        """D2-4: 调度 T+5 延迟回填 job（review_backfill 注册于 services/scheduled_jobs/review_backfill.py）。"""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()

        job_fn = self._registered_jobs.get("review_backfill")
        if job_fn is None:
            logger.warning(
                "[Scheduler] review_backfill job not registered (call SchedulerService.register_job)",
            )
            return
        await job_fn(self)

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
