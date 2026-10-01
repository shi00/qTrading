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
from dataclasses import dataclass

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

# D7-1/MAJOR-01: 补偿同步失败后的指数退避序列（秒）：30s → 5min → 30min → 2h。
# 达到末档后保持 2h 封顶，避免持续性失败（积分权限不足 / 限流 / 数据源延迟发布）下
# 30 秒看门狗无限固定频率重试——持续消耗 Tushare 配额并挤占任务面板 200 条历史。
_CATCHUP_BACKOFF_SECONDS: tuple[int, ...] = (30, 300, 1800, 7200)

# D7-6: 状态面板展示的定时任务全集（与 _schedule_jobs 注册的 id 同源，顺序即展示顺序）。
_SCHEDULED_JOB_IDS: tuple[str, ...] = (
    "daily_update",
    "review_backfill",
    "ai_concept_daily_refresh",
    "nightly_prediction",
)


@dataclass(frozen=True)
class ScheduledJobStatus:
    """D7-6: 单个定时任务的只读状态快照（不可变，跨层传递到 ViewModel/View）。

    字段语义（缺失一律为 None，遵守 R21——不得用 0 / 空串 / 「正常」伪装缺失）：
    - ``last_success_at``：该 job 最近一次成功推进的幂等水位（YYYYMMDD）；无来源或未成功过为 None。
    - ``last_failure_reason``：最近一次失败原因（已经 DataSanitizer 脱敏，R9）；未记录为 None。
    - ``next_run_at``：APScheduler 计划的下次运行时刻（"%Y-%m-%d %H:%M:%S"）；未注册/未计划为 None。
    - ``consecutive_failures``：连续失败次数；仅 daily_update 有权威计数（与 D7-1 退避状态同源），
      其余 job 为 None（未记录），不得伪造成 0。
    """

    job_id: str
    last_success_at: str | None
    last_failure_reason: str | None
    next_run_at: str | None
    consecutive_failures: int | None


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
            # D7-1/MAJOR-01: 补偿失败退避状态（进程内，与 _last_update_date 同生命周期：
            # 重启即重置，不引入 DB schema 变更）。_catchup_next_retry_at 为绝对时刻
            # （= 上次失败时间 + 当前档位退避），退避窗口内看门狗不再提交补偿。
            self._catchup_consecutive_failures = 0
            self._catchup_next_retry_at: datetime.datetime | None = None
            # D7-6: 各 job 最近一次失败原因（进程内，重启即重置；写入前经 DataSanitizer 脱敏，R9），
            # 供数据源页调度状态面板展示"上次失败原因"。成功/水位推进时清除对应项。
            self._job_last_failure_reason: dict[str, str] = {}
            # D7-3/MINOR-01: 看门狗补触发夜间预测的"当日已补触发"标记（进程内，重启即重置）。
            # 夜间预测"零落库/无候选"时刻意不标记 _last_pred_date（允许重试），若无此标记，
            # 30 秒看门狗会在数据就绪后反复补触发付费 AI 选股。每日至多补触发一次。
            self._nightly_catchup_triggered_date: str | None = None
            # 夜间预测计划时刻 (hour, minute)，由 _schedule_jobs 解析配置后写入（与 cron 同源），
            # 供 _is_past_nightly_prediction_time 判定而无需在看门狗热路径重复读配置。
            self._nightly_hm: tuple[int, int] = (20, 30)
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
        # D7-1/MAJOR-01: 水位推进即"数据已到最新"，补偿退避状态随之清零——避免旧的持续失败
        # 退避阻塞后续新出现的遗漏。日更成功与补偿成功两条路径均经本方法推进水位，故一处覆盖。
        self._reset_catchup_backoff()
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
            # bypass_backoff=True（D7-1）：misfire 为每天每 job 至多一次的一次性事件，
            # 不受 30 秒看门狗退避约束，避免当天数据永久丢失。
            try:
                loop = asyncio.get_running_loop()
                task = loop.create_task(self._catch_up_missed_updates(include_today=True, bypass_backoff=True))
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
            # D7-6: 记录失败原因供状态面板展示（仅业务 job，避免无界增长；脱敏后写入，R9）。
            if event.job_id in _SCHEDULED_JOB_IDS:
                self._job_last_failure_reason[event.job_id] = DataSanitizer.sanitize_error(event.exception)

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

    def _is_market_sync_busy(self) -> bool:
        """D7-5/MINOR-03: 是否有写行情表的任务在运行/排队，或缓存清理在运行。

        只读 TaskManager 公开快照（内存遍历，无 IO），供看门狗路径决定是否跳过本轮补偿检查；
        misfire 路径不经过此判定（否则当天数据永久丢失），故守卫只加在看门狗调用点。
        """
        from services.task_manager import (  # lazy-import: 启动性能（R1 例外 EX-0007）
            EXCLUSIVE_GROUP_MARKET_SYNC,
            TaskManager,
            TaskStatus,
        )

        active = (TaskStatus.RUNNING, TaskStatus.QUEUED)
        return any(
            t.status in active and (t.exclusive_group == EXCLUSIVE_GROUP_MARKET_SYNC or t.unique_key == "cache_clear")
            for t in TaskManager().get_all_tasks()
        )

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
        # D7-3/MINOR-01: 已过夜间预测计划时刻时把"今天"纳入补偿范围——否则傍晚才启动
        # 应用的用户水位永远推不到今天，夜间预测前置检查永不放行（预测被永久跳过）。
        # 用统一谓词而非常态 include_today=True：16:30 日更及其 30 分钟 misfire 宽限窗口
        # 内不补今天，避免与运行中的日更并发同步同一天；退避约束照常生效（不传
        # bypass_backoff），同步持续失败时不出选股结论。
        # D7-5/MINOR-03: 同组（写同一批行情表）任务运行/排队中、或缓存清理运行中时跳过本轮
        # 补偿检查——此时提交也只会排在同组锁之后，徒增面板噪音；下个周期（30s）重判。
        # 仅看门狗路径加守卫：misfire 路径（bypass_backoff=True）跳过会导致当天数据永久丢失。
        if self._is_market_sync_busy():
            logger.info("[Scheduler] 行情表写入任务或清理缓存运行中，跳过本次补偿检查（D7-5/MINOR-03）")
        else:
            await self._catch_up_missed_updates(include_today=self._is_past_nightly_prediction_time())
        # D7-3/MINOR-01: 数据就绪 + 当日未预测 + 已过预测时刻时补触发一次（每日至多一次）。
        await self._catch_up_nightly_prediction()

    def _schedule_jobs(self):
        """Register jobs with the scheduler"""
        # Only remove business jobs, NOT the config_watchdog
        # D7-6: 以 _SCHEDULED_JOB_IDS 为业务 job 全集唯一正本（避免与状态面板的 id 清单漂移）。
        for job_id in _SCHEDULED_JOB_IDS:
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
        # D7-3/MINOR-01: 与 cron 同源记录计划时刻，供看门狗谓词使用。
        self._nightly_hm = (n_hour, n_minute)

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

    def _catchup_backoff_active(self) -> bool:
        """D7-1/MAJOR-01: 退避窗口内（连续失败且未到下次重试时刻）返回 True。

        进程内状态，无锁——读写在单线程事件循环内（看门狗协程与补偿任务协程），不跨线程。
        """
        return (
            self._catchup_consecutive_failures > 0
            and self._catchup_next_retry_at is not None
            and get_now() < self._catchup_next_retry_at
        )

    def _record_catchup_failure(self, reason: str | None = None) -> None:
        """D7-1/MAJOR-01: 记录一次补偿失败，并按指数退避更新下次重试时刻。

        序列 30s → 5min → 30min → 2h，达到末档后保持 2h 封顶（不再无限增长）。
        D7-6: ``reason`` 为本次失败简述，脱敏后写入 daily_update 行的失败原因（R9）。
        """
        self._catchup_consecutive_failures += 1
        index = min(self._catchup_consecutive_failures - 1, len(_CATCHUP_BACKOFF_SECONDS) - 1)
        delay_seconds = _CATCHUP_BACKOFF_SECONDS[index]
        self._catchup_next_retry_at = get_now() + datetime.timedelta(seconds=delay_seconds)
        if reason:
            self._job_last_failure_reason["daily_update"] = DataSanitizer.sanitize_error(reason)
        logger.info(
            "[Scheduler] 补偿连续失败 %d 次，退避 %d 秒后重试（下次重试：%s）",
            self._catchup_consecutive_failures,
            delay_seconds,
            self._catchup_next_retry_at,
        )

    def _reset_catchup_backoff(self) -> None:
        """D7-1/MAJOR-01: 补偿成功或水位推进后清零失败计数与退避状态。

        D7-6: 同步清除 daily_update 行的失败原因——成功之后仍残留旧失败原因会误导状态面板。
        """
        if self._catchup_consecutive_failures:
            logger.info(
                "[Scheduler] 补偿退避状态清零（此前连续失败 %d 次）",
                self._catchup_consecutive_failures,
            )
        self._catchup_consecutive_failures = 0
        self._catchup_next_retry_at = None
        self._job_last_failure_reason.pop("daily_update", None)

    def _is_past_nightly_prediction_time(self) -> bool:
        """D7-3/MINOR-01: 当前时刻是否已到/已过夜间预测计划时刻（默认 20:30）。

        统一谓词，同时服务两处判定：① 看门狗补偿是否把"今天"纳入范围（include_today）；
        ② 是否补触发一次当日夜间预测。计划时刻取 ``self._nightly_hm``（``_schedule_jobs``
        解析配置写入，与注册的 cron 同源），避免在 30 秒热路径重复读配置。
        """
        now = get_now()
        return (now.hour, now.minute) >= self._nightly_hm

    async def _catch_up_nightly_prediction(self) -> None:
        """D7-3/MINOR-01: 数据就绪 + 当日未预测 + 已过预测时刻时，补触发一次夜间预测。

        覆盖"20:30 时应用未运行（或当日同步晚于 20:30 才完成）"导致夜间预测永久跳过：
        前置检查（``_last_update_date == today``）在数据未就绪时直接跳过，数据就绪后需有
        一处再触发者；30 秒看门狗即该触发者。每日进程内至多补触发一次
        （``_nightly_catchup_triggered_date``）——预测因无候选/预算超限而"不标记完成、
        允许重试"时，避免看门狗每 30 秒重跑付费 AI 选股。
        """
        if "nightly_prediction" not in self._registered_jobs:
            return
        if not ConfigHandler.is_auto_update_enabled():
            return
        today_str = get_now().date().strftime("%Y%m%d")
        if self._last_update_date != today_str:
            return
        if self._last_pred_date == today_str:
            return
        if self._nightly_catchup_triggered_date == today_str:
            return
        if not self._is_past_nightly_prediction_time():
            return
        self._nightly_catchup_triggered_date = today_str
        logger.warning(
            "[Scheduler] 夜间预测未在计划时刻执行且当日数据已就绪（%s），补触发一次（D7-3/MINOR-01）",
            today_str,
        )
        await self._run_nightly_prediction()

    async def _catch_up_missed_updates(self, include_today: bool = False, *, bypass_backoff: bool = False) -> None:
        """D6-1 启动/周期/misfire 补偿：检查自 _last_update_date 以来是否有遗漏交易日并回补。

        桌面应用无法保证在 cron 时刻处于运行状态，且 misfire_grace_time 在启动期/繁忙期
        极易超时。纯时间触发会导致当天永久丢失且用户无感。本方法以"上次成功日期"为权威
        状态驱动补偿。三处触发：_load_db_state 后 / _watch_config_changes 内 / _on_job_missed 内。
        include_today=True 表示把"今天"也纳入回补范围（默认只补 [last+1, 昨天]，今天由 16:30
        cron 负责）：misfire 路径（_on_job_missed，计划时间 + 1800s 宽限已过、已收盘）与
        D7-3 看门狗"已过夜间预测时刻"路径均传 True，否则"当天永久跳过"依旧存在。
        bypass_backoff=True 仅由 _on_job_missed 传入：misfire 为每天每 job 至多一次的一次性
        事件（非 30s 循环），且对应报告所述"下一个 cron 时刻"重试，施加退避可能导致当天数据
        永久丢失。范围（include_today）与退避绕过（bypass_backoff）刻意解耦——D7-3 看门狗
        需要"纳入今天"但仍必须受 D7-1 退避约束（否则 MAJOR-01 的 30 秒固定频率重试风暴复发）。
        幂等由补偿任务独立 unique_key 去重 + 同步层 check_data_exists 缓存跳过共同保证。
        """
        # D7-1/MAJOR-01: 退避窗口内跳过重复提交，打断"失败→30s 后重试"的固定频率风暴。
        # 此处可提前返回，亦省去退避期间的交易日历查询。
        if not bypass_backoff and self._catchup_backoff_active():
            logger.debug(
                "[Scheduler] 补偿退避中（连续失败 %d 次），本次跳过提交，下次重试：%s",
                self._catchup_consecutive_failures,
                self._catchup_next_retry_at,
            )
            return
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
        from services.task_manager import (  # lazy-import: 启动性能——仅提交补偿任务时加载
            EXCLUSIVE_GROUP_MARKET_SYNC,
            TaskManager,
        )

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
            # D7-5/MINOR-03: 与日更/全量初始化写同一批行情表，入同组互斥
            exclusive_group=EXCLUSIVE_GROUP_MARKET_SYNC,
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
        重抛、其他异常记日志后继续），单日失败不中断整批。
        MAJOR-05: 幂等键的推进以「每一天都明确成功」为准，而非仅看共享 sync_result 的
        is_complete——单日同步的系统级异常在失败表登记段（historical.py 的 _record_failed）
        之前就重抛，该日「失败了却没记账」，只看 is_complete 会把未记账的失败日当成功。
        """
        from services.task_manager import TaskManager  # lazy-import: 启动性能
        from data.data_processor import DataProcessor  # lazy-import: 启动性能
        from data.sync.base import SyncResult  # lazy-import: 启动性能

        tm = TaskManager()
        processor = DataProcessor()
        sync_result = SyncResult()
        total = len(missed_dates)
        # MAJOR-05 逐日记账：
        # - failed_dates：抛异常的日子（该日失败未反映到 sync_result）；
        # - contiguous_success_end：最后一个「无异常且关键表全部成功」的连续日。
        # 关键表仅其一失败时单日同步不抛异常（historical.py 仅两者都失败才 raise），故
        # 不能只按「无异常」判定成功；failed_critical_tables 批内只增不减、is_complete 单调，
        # 一旦为假即冻结，保证部分推进时水位不越过任何关键表失败日（R21/R22）。
        # 已知边界（不在本次修复内，属报告 MINOR-02/MAJOR-02）：关键表因权限被拒时单日同步
        # 同样不抛异常、也不登记失败表（SYNC_RESULT_SKIPPED_PERMISSION），本层无法区分该日的
        # 「无数据」与「合法空」，仍会判为成功。
        failed_dates: list[datetime.date] = []
        contiguous_success_end: datetime.date | None = None
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
                failed_dates.append(d)
                log_classified(
                    logger,
                    e,
                    "general",
                    "[Scheduler] Catch-up failed for %s, continuing (%s): %s",
                    d.strftime("%Y%m%d"),
                    exc_info=True,
                )
                continue
            if not failed_dates and sync_result.is_complete:
                contiguous_success_end = d
        # D1-2/MAJOR-05: 仅「全部日期成功且关键表全成功」才推进到最后一个遗漏交易日。
        if not failed_dates and sync_result.is_complete:
            latest = missed_dates[-1]
            await self._mark_daily_update_done_db(latest.strftime("%Y%m%d"))
            return Message("sched_catchup_done", {"days": total})
        # MAJOR-05: 存在失败日 → 不推进到末位，使失败日及其之后仍留在补偿区间内；
        # 若失败日之前有连续成功日，只推进到该日（水位单调由 _mark_daily_update_done_db
        # 的内存 max() + DB GREATEST 保证，R22），避免这些已成功日反复重试。
        if failed_dates and contiguous_success_end is not None:
            await self._mark_daily_update_done_db(contiguous_success_end.strftime("%Y%m%d"))
            logger.warning(
                "[Scheduler] Catch-up partially advanced watermark to %s (failed days: %s), NOT marking last day done",
                contiguous_success_end.strftime("%Y%m%d"),
                [d.strftime("%Y%m%d") for d in failed_dates],
            )
        logger.warning(
            "[Scheduler] Catch-up NOT complete (critical=%s), NOT marking done",
            sync_result.failed_critical_tables,
        )
        # D7-1/MAJOR-01: 记录失败并退避，避免看门狗每 30 秒重复提交（消耗配额 + 刷屏任务面板）。
        # 必须置于水位推进之后——_mark_daily_update_done_db 内部会清零退避状态，顺序颠倒会
        # 使本次刚设置的退避被立刻清掉。
        # D7-6: 同时记录失败原因（关键表清单 + 未记账失败日）供状态面板展示。
        reason = f"failed_critical_tables={sync_result.failed_critical_tables}"
        if failed_dates:
            reason = f"failed_days={[d.strftime('%Y%m%d') for d in failed_dates]} {reason}"
        self._record_catchup_failure(reason=reason)
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
            )  # lazy-import: 日历降级（"R1: utils must not import business layers" 契约例外 EX-0016）

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
        from services.task_manager import (  # lazy-import: 启动性能——仅提交任务时加载 TaskManager
            EXCLUSIVE_GROUP_MARKET_SYNC,
            TaskManager,
        )

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
            # D7-5/MINOR-03: 日更与补偿同步/全量初始化写同一批行情表，入同组互斥
            exclusive_group=EXCLUSIVE_GROUP_MARKET_SYNC,
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
        消除 ``utils → strategies`` 方向性违规（"R1: utils must not import business layers" 契约）与隐藏三角依赖（A6）。
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

    def _job_next_run_at(self, job_id: str) -> str | None:
        """D7-6: 取 APScheduler 计划的下次运行时刻；未注册/未计划时返回 None（R21，不伪造）。"""
        job = self.scheduler.get_job(job_id)
        next_run = getattr(job, "next_run_time", None)
        return next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else None

    def get_jobs_status_snapshot(self) -> tuple[ScheduledJobStatus, ...]:
        """D7-6: 全部已注册业务定时任务的只读状态快照（供数据源页调度状态面板）。

        纯内存读取（APScheduler job 表 + 进程内状态），无 IO / DB 访问，可在 UI 线程直调，
        不触发 R16。字段缺失一律为 None（R21），由 View 层渲染为占位符。

        同源口径（不另立第二份状态）：
        - ``last_success_at`` 取既有幂等水位（``_last_update_date`` / ``_last_pred_date`` /
          ``_last_ai_concept_date``）；``review_backfill`` 不推进统一水位，恒为 None。
        - ``consecutive_failures`` 仅 daily_update 取 ``_catchup_consecutive_failures``
          （与 D7-1 退避状态同源，不新增计数）；其余 job 恒为 None。

        NOTE(lazy): 仅日更域有权威连续失败计数. ceiling: 非补偿路径（回填/概念/预测）的
        失败不累积计数，面板对其显示「—」. upgrade: 引入 per-job 失败计数并可在成功时清零时.
        """
        last_success_source = {
            "daily_update": self._last_update_date,
            "review_backfill": None,
            "ai_concept_daily_refresh": self._last_ai_concept_date,
            "nightly_prediction": self._last_pred_date,
        }
        return tuple(
            ScheduledJobStatus(
                job_id=job_id,
                last_success_at=last_success_source[job_id] or None,
                last_failure_reason=self._job_last_failure_reason.get(job_id),
                next_run_at=self._job_next_run_at(job_id),
                consecutive_failures=self._catchup_consecutive_failures if job_id == "daily_update" else None,
            )
            for job_id in _SCHEDULED_JOB_IDS
        )
