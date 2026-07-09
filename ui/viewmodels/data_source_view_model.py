"""DataSourceViewModel — MVVM-002 fix.

Extracts business logic from DataSourceTab into a pure ViewModel.
Holds business state, calls services/data layer, notifies View via
frozen state snapshot + subscribe/_notify (Phase 2 改造).

Phase 2 改造: 11 个 on_* 回调移除,改用 state + subscribe/_notify。
- 状态型字段直接放入 frozen state (is_syncing/health_checking/init_sync_running 等)
- 瞬态事件/大体积数据用 dual-track (§3.0.4): version 递增 + last_* property
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error, get_error_message
from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.external.tushare_client import TushareClient
from services.ai_service import AIService
from services.task_manager import AppTask, TaskManager, TaskStatus

logger = logging.getLogger(__name__)


class InitSyncError(Exception):
    """Raised when init sync fails with a known generic error (e.g. report is None)."""


@dataclass(frozen=True)
class DataSourceState:
    """DataSourceViewModel 的不可变状态快照。View 通过 subscribe 接收。"""

    # --- Sync state ---
    is_syncing: bool = False
    active_key: str | None = None
    init_sync_cancellable: bool = False

    # --- Health check phase ---
    # health_checking: True during check_health() task lifecycle (checking → finished)
    health_checking: bool = False

    # --- Init sync phase ---
    # init_sync_running: True while init sync task is active
    init_sync_running: bool = False
    # init_sync_final_status: set when init sync ends (COMPLETED/CANCELLED/FAILED)
    init_sync_final_status: TaskStatus | None = None

    # --- Progress ---
    progress: float = 0.0
    progress_message: str = ""

    # --- Dual-track versions (View pulls last_* on version change, §3.0.4) ---
    health_result_version: int = 0
    snack_version: int = 0
    cache_cleared_version: int = 0
    health_error_version: int = 0


class DataSourceViewModel:
    """ViewModel for DataSourceTab — manages data source business logic.

    Phase 2 改造: 11 个 on_* 回调移除,改用 state + subscribe/_notify。
    View 通过 ``subscribe(callback)`` 订阅 state 变化,通过 diff 判断哪些字段变化,
    分派到对应处理方法。瞬态事件/大体积数据通过 dual-track (version + last_* property) 拉取。
    """

    def __init__(
        self,
        processor: DataProcessor | None = None,
        cache: CacheManager | None = None,
        ai_service: AIService | None = None,
    ):
        # Dependencies (constructor injection for testability)
        # T6 fix: 与 _processor / _cache 一致，AIService 也通过构造注入。
        # AIService 已是 @register_singleton，AIService() 默认返回同一实例，
        # 显式注入仅为统一风格、便于测试替换。
        self._processor = processor or DataProcessor()
        self._cache = cache or CacheManager()
        self._ai_service = ai_service or AIService()
        self._tm = TaskManager()

        # Business state (internal mutable tracking, not for View)
        self._active_task_ids: dict[str, str] = {}

        # --- State snapshot + subscribers ---
        self._state: DataSourceState = DataSourceState()
        self._subscribers: list[Callable[[DataSourceState], None]] = []

        # --- Dual-track internal storage (§3.0.4) ---
        self._last_health_result: dict | None = None
        self._last_snack: tuple[str, str] | None = None  # (message, color_name)
        self._last_health_error: str | None = None

    # ------------------------------------------------------------------
    # State / subscribe / notify
    # ------------------------------------------------------------------

    @property
    def state(self) -> DataSourceState:
        return self._state

    @property
    def last_health_result(self) -> dict | None:
        """最近一次健康检查结果 dict（dual-track,View 在 health_result_version 变化时拉取）。"""
        return self._last_health_result

    @property
    def last_snack(self) -> tuple[str, str] | None:
        """最近一次 snack 通知 (message, color_name)（dual-track,View 在 snack_version 变化时拉取）。"""
        return self._last_snack

    @property
    def last_health_error(self) -> str | None:
        """最近一次健康检查错误消息（dual-track,View 在 health_error_version 变化时拉取）。"""
        return self._last_health_error

    def subscribe(self, callback: Callable[[DataSourceState], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self):
        """Clear all subscribers and reset state."""
        self._active_task_ids.clear()
        self._last_health_result = None
        self._last_snack = None
        self._last_health_error = None
        self._subscribers.clear()
        self._state = DataSourceState()

    # --- Dual-track emitters ---

    def _emit_snack(self, message: str, color_name: str) -> None:
        """Store snack and notify via snack_version (dual-track)."""
        self._last_snack = (message, color_name)
        self._set_state(snack_version=self._state.snack_version + 1)

    def _emit_health_result(self, result: dict) -> None:
        """Store health result and notify via health_result_version (dual-track)."""
        self._last_health_result = result
        self._set_state(health_result_version=self._state.health_result_version + 1)

    def _emit_health_error(self, error_msg: str) -> None:
        """Store health error and notify via health_error_version (dual-track)."""
        self._last_health_error = error_msg
        self._set_state(health_error_version=self._state.health_error_version + 1)

    def _emit_cache_cleared(self) -> None:
        """Notify cache cleared via cache_cleared_version (dual-track)."""
        self._set_state(cache_cleared_version=self._state.cache_cleared_version + 1)

    # --- Internal helpers ---

    def _set_sync_busy(self, is_busy: bool, active_key: str | None = None):
        """Update sync busy state and notify View."""
        self._set_state(is_syncing=is_busy, active_key=active_key)

    def _reset_init_sync(self, final_status: TaskStatus = TaskStatus.COMPLETED):
        """Reset init sync state and notify View to reset UI."""
        if not self._state.is_syncing:
            return
        self._set_state(
            init_sync_cancellable=False,
            is_syncing=False,
            active_key=None,
            init_sync_running=False,
            init_sync_final_status=final_status,
        )

    def _recover_after_task_terminated(
        self,
        unique_key: str | None,
        final_status: TaskStatus,
    ):
        """Handle task termination — reset busy state only (snack handled by coroutine catch)."""
        if not self._state.is_syncing:
            return
        if unique_key == "system_init_sync":
            self._reset_init_sync(final_status)
        else:
            self._set_sync_busy(False)

    # --- Health Check ---

    async def check_health(self):
        """Start health check task."""
        self._set_state(health_checking=True)

        async def _run_health_check(task_id: str, **kwargs):
            try:
                # T8 fix: 检查 update_progress 返回值，False 表示任务已被取消或不再 RUNNING，应早退。
                # M3 fix: CancelledError 带消息，便于日志区分"用户取消"与"框架取消"
                if not self._tm.update_progress(task_id, 0.2, I18n.get("task_progress_checking")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")
                result = await self._processor.check_data_health()
                if not self._tm.update_progress(task_id, 0.9, I18n.get("task_progress_analyzing")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")

                self._emit_health_result(result)

                return I18n.get("task_result_health_done")

            except asyncio.CancelledError:
                self._set_state(health_checking=False)
                # health_cancelled: use a transient flag via state diff
                # View detects health_checking False transition as "cancelled/finished"
                raise
            except Exception as e:
                logger.error("[DataSourceVM] Health check failed: %s", e, exc_info=True)
                error_info = classify_error(e, context="general")
                self._emit_health_error(get_error_message(error_info))
                raise
            finally:
                self._set_state(health_checking=False)

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_health_check"),
            task_type=I18n.get("task_type_sys_check"),
            coroutine_factory=_run_health_check,
            cancellable=True,
            unique_key="sys_health_check",
        )

        if task_id is None:
            self._set_state(health_checking=False)

    # --- Full Daily Sync ---

    def execute_full_daily_sync(self):
        """Execute full daily sync (called by View after user confirms dialog)."""
        self._set_sync_busy(True, "daily_sync")

        async def _daily_logic(task_id: str, **kwargs):
            def _progress(c, t, msg):
                # T8 fix: 若 update_progress 返回 False（任务已取消/不再 RUNNING），抛 CancelledError 早退
                # M3 fix: CancelledError 带消息，便于日志区分"用户取消"与"框架取消"
                if not self._tm.update_progress(task_id, c / t if t else 0, msg):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")

            try:
                await self._processor.run_daily_update(progress_callback=_progress)
                self._emit_snack(
                    I18n.get("snack_full_sync_done_simple"),
                    "success",
                )
                return I18n.get("ds_daily_update_done")
            except asyncio.CancelledError:
                if self._state.is_syncing:
                    self._emit_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    I18n.get("common_op_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_daily_sync"),
            task_type=I18n.get("sched_task_type_daily"),
            coroutine_factory=_daily_logic,
            cancellable=True,
            unique_key="daily_sync",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["daily_sync"] = task_id

    # --- AI Concept Rebuild ---

    def execute_ai_concept_rebuild(self):
        """Execute AI concept rebuild (called by View after user confirms)."""
        self._set_sync_busy(True, "ai_concept_sync")

        async def _ai_concept_logic(task_id: str, **kwargs):
            cancel_event = self._tm.get_cancel_event(task_id)
            try:
                # T8 fix: 若任务已被取消则 update_progress 返回 False，立即抛 CancelledError 早退
                # M3 fix: CancelledError 带消息，便于日志区分"用户取消"与"框架取消"
                if not self._tm.update_progress(task_id, 0.05, I18n.get("ds_ai_concept_rebuild_start")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")
                # Manual trigger: manual_trigger=True → execute LLM-driven concept tagging.
                # ai_service injected via kwargs to satisfy R1 (data/ must not import services/).
                await self._processor.run_ai_concept_tagging(
                    task_id=task_id,
                    cancel_event=cancel_event,
                    manual_trigger=True,
                    ai_service=self._ai_service,
                )
                self._emit_snack(I18n.get("snack_ai_concept_done"), "success")
                return I18n.get("ds_ai_concept_rebuild_done")
            except asyncio.CancelledError:
                if self._state.is_syncing:
                    self._emit_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    I18n.get("common_op_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_ai_concept_rebuild"),
            task_type=I18n.get("ds_task_type_ai_tagging"),
            coroutine_factory=_ai_concept_logic,
            cancellable=True,
            unique_key="ai_concept_sync",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["ai_concept_sync"] = task_id

    # --- Clear Cache ---

    def execute_clear_cache(self):
        """Execute cache clear (called by View after user confirms)."""
        running = [
            t for t in self._tm.get_all_tasks() if t.status == TaskStatus.RUNNING and t.unique_key != "cache_clear"
        ]
        if running:
            self._emit_snack(I18n.get("ds_clear_cache_syncing"), "warning")
            return

        self._set_sync_busy(True, "cache_clear")

        async def _clear_logic(task_id: str, **kwargs):
            try:
                await self._cache.clear_all_cache()
                self._emit_snack(I18n.get("ds_cache_cleared"), "success")
                self._emit_cache_cleared()
                return I18n.get("ds_cache_clear_done")
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    I18n.get("ds_clean_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_clear_cache"),
            task_type=I18n.get("ds_task_type_system"),
            coroutine_factory=_clear_logic,
            cancellable=False,
            unique_key="cache_clear",
        )

        if task_id is None:
            self._set_sync_busy(False)
        else:
            self._active_task_ids["cache_clear"] = task_id

    # --- Init Historical Data ---

    def execute_init_historical_data(self):
        """Execute historical data initialization (called by View after user confirms)."""
        self._set_sync_busy(True, "system_init_sync")
        self._set_state(init_sync_cancellable=True, init_sync_running=True)

        async def _run_initial_sync(task_id: str, **kwargs):
            try:
                self._set_state(progress=0, progress_message=I18n.get("wizard_status_init"))

                def _combined_progress(c, t, m):
                    self._set_state(progress=c / t if t > 0 else 0, progress_message=m)
                    # T8 fix: 若任务已被取消则 update_progress 返回 False，立即抛 CancelledError 早退
                    # M3 fix: CancelledError 带消息，便于日志区分"用户取消"与"框架取消"
                    if not self._tm.update_progress(
                        task_id,
                        c / t if t > 0 else 0,
                        f"[{c:.2f}/{t}] {m}",
                    ):
                        raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")

                report = await self._processor.initialize_system(
                    progress_callback=_combined_progress,
                )

                if self._processor.is_cancelled():
                    raise asyncio.CancelledError("task cancelled by user (is_cancelled returned True)")

                if report is None:
                    raise InitSyncError(I18n.get("ds_init_fail_generic"))

                self._reset_init_sync(TaskStatus.COMPLETED)
                self._emit_snack(I18n.get("settings_init_done"), "success")

                return I18n.get("sys_init_success")

            except asyncio.CancelledError:
                self._reset_init_sync(TaskStatus.CANCELLED)
                raise
            except InitSyncError as e:
                msg = str(e)
                logger.error("[DataSourceVM] Init sync failed: %s", e, exc_info=True)
                self._reset_init_sync(TaskStatus.FAILED)
                self._emit_snack(msg, "error")
                raise RuntimeError(msg) from e
            except Exception as e:
                msg = I18n.get("ds_init_fail_fmt")
                logger.error("[DataSourceVM] Init sync failed: %s", e, exc_info=True)
                self._reset_init_sync(TaskStatus.FAILED)
                self._emit_snack(msg, "error")
                raise RuntimeError(msg) from e

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_init_sync"),
            task_type=I18n.get("task_type_data_sync"),
            coroutine_factory=_run_initial_sync,
            cancellable=True,
            unique_key="system_init_sync",
        )

        if task_id is None:
            self._set_state(init_sync_cancellable=False, init_sync_running=False)
            self._reset_init_sync(TaskStatus.FAILED)
        else:
            self._active_task_ids["system_init_sync"] = task_id

    async def cancel_init_sync(self):
        """Cancel running init sync."""
        await self._processor.request_cancel()
        task_id = self._active_task_ids.get("system_init_sync")
        if task_id:
            self._tm.cancel_task(task_id)

    # --- Config Operations ---

    def save_tushare_token(self, token: str):
        """Save Tushare token to config and update client."""
        token = token.strip()
        if not token:
            return
        ConfigHandler.save_token(token)
        client = TushareClient()
        client.set_token(token)

    def set_history_years(self, years: int):
        """Save history years config."""
        ConfigHandler.set_init_history_years(years)

    # --- Task State Management ---

    def handle_task_update(self, current_tasks: list[AppTask]):
        """Handle TaskManager task state updates (called by View forwarding)."""
        if not self._state.is_syncing and not self._active_task_ids:
            return

        active_ids = set(self._active_task_ids.values())
        recovered = False
        for t in current_tasks:
            if t.id in active_ids and t.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.INTERRUPTED,
            ):
                unique_key = next(
                    (k for k, v in self._active_task_ids.items() if v == t.id),
                    None,
                )
                self._active_task_ids = {k: v for k, v in self._active_task_ids.items() if v != t.id}
                if not self._active_task_ids and self._state.is_syncing and not recovered:
                    self._recover_after_task_terminated(unique_key, t.status)
                    recovered = True

    def recover_stale_state(self):
        """Recover from stale task state (e.g. after page remount)."""
        if not self._state.is_syncing and not self._active_task_ids:
            return
        stale_keys = []
        for key, task_id in list(self._active_task_ids.items()):
            task = self._tm.get_task(task_id)
            if task is None or task.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.INTERRUPTED,
            ):
                stale_keys.append(key)
        for key in stale_keys:
            self._active_task_ids.pop(key, None)
        if not self._active_task_ids and self._state.is_syncing:
            self._set_sync_busy(False)

    async def get_health_report(self) -> dict:
        """Get health report data for dialog display."""
        return await self._processor.check_data_health()
