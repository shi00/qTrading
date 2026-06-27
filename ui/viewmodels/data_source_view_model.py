"""DataSourceViewModel — MVVM-002 fix.

Extracts business logic from DataSourceTab into a pure ViewModel.
Holds business state, calls services/data layer, notifies View via callbacks.
No Flet control references.
"""

import asyncio
import logging
from collections.abc import Callable

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


class DataSourceViewModel:
    """ViewModel for DataSourceTab — manages data source business logic.

    Callbacks (View binders):
        on_show_snack(message, color_name) — color_name: "success"|"warning"|"error"|"info"
        on_sync_busy_changed(is_busy, active_key) — active_key identifies the running operation
        on_health_checking() — health check started
        on_health_result(result_dict) — health check succeeded with raw data
        on_health_error(error_msg) — health check failed
        on_health_cancelled() — health check was cancelled
        on_health_finished() — health check completed (success/error/cancel), re-enable button
        on_init_sync_started() — init sync started, switch button to cancel mode
        on_init_sync_reset(final_status) — init sync ended, reset button to normal
        on_progress_update(progress, message) — progress update for init sync
        on_cache_cleared() — cache cleared successfully, send pubsub
    """

    def __init__(
        self,
        processor: DataProcessor | None = None,
        cache: CacheManager | None = None,
    ):
        # Dependencies (constructor injection for testability)
        self._processor = processor or DataProcessor()
        self._cache = cache or CacheManager()
        self._tm = TaskManager()

        # Business state
        self.is_syncing = False
        self.init_sync_cancellable = False
        self._active_task_ids: dict[str, str] = {}

        # View callbacks
        self.on_show_snack: Callable[[str, str], None] | None = None
        self.on_sync_busy_changed: Callable[[bool, str | None], None] | None = None
        self.on_health_checking: Callable[[], None] | None = None
        self.on_health_result: Callable[[dict], None] | None = None
        self.on_health_error: Callable[[str], None] | None = None
        self.on_health_cancelled: Callable[[], None] | None = None
        self.on_health_finished: Callable[[], None] | None = None
        self.on_init_sync_started: Callable[[], None] | None = None
        self.on_init_sync_reset: Callable[[TaskStatus], None] | None = None
        self.on_progress_update: Callable[[float, str], None] | None = None
        self.on_cache_cleared: Callable[[], None] | None = None

    def bind(
        self,
        on_show_snack: Callable[[str, str], None],
        on_sync_busy_changed: Callable[[bool, str | None], None],
        on_health_checking: Callable[[], None],
        on_health_result: Callable[[dict], None],
        on_health_error: Callable[[str], None],
        on_health_cancelled: Callable[[], None],
        on_health_finished: Callable[[], None],
        on_init_sync_started: Callable[[], None],
        on_init_sync_reset: Callable[[TaskStatus], None],
        on_progress_update: Callable[[float, str], None],
        on_cache_cleared: Callable[[], None],
    ):
        self.on_show_snack = on_show_snack
        self.on_sync_busy_changed = on_sync_busy_changed
        self.on_health_checking = on_health_checking
        self.on_health_result = on_health_result
        self.on_health_error = on_health_error
        self.on_health_cancelled = on_health_cancelled
        self.on_health_finished = on_health_finished
        self.on_init_sync_started = on_init_sync_started
        self.on_init_sync_reset = on_init_sync_reset
        self.on_progress_update = on_progress_update
        self.on_cache_cleared = on_cache_cleared

    def dispose(self):
        """Clear all callbacks and reset state."""
        self.on_show_snack = None
        self.on_sync_busy_changed = None
        self.on_health_checking = None
        self.on_health_result = None
        self.on_health_error = None
        self.on_health_cancelled = None
        self.on_health_finished = None
        self.on_init_sync_started = None
        self.on_init_sync_reset = None
        self.on_progress_update = None
        self.on_cache_cleared = None

    # --- Internal helpers ---

    def _set_sync_busy(self, is_busy: bool, active_key: str | None = None):
        """Update sync busy state and notify View."""
        self.is_syncing = is_busy
        if self.on_sync_busy_changed:
            self.on_sync_busy_changed(is_busy, active_key)

    def _reset_init_sync(self, final_status: TaskStatus = TaskStatus.COMPLETED):
        """Reset init sync state and notify View to reset UI."""
        if not self.is_syncing:
            return
        self.init_sync_cancellable = False
        self._set_sync_busy(False)
        if self.on_init_sync_reset:
            self.on_init_sync_reset(final_status)

    def _recover_after_task_terminated(
        self,
        unique_key: str | None,
        final_status: TaskStatus,
    ):
        """Handle task termination — reset busy state only (snack handled by coroutine catch)."""
        if not self.is_syncing:
            return
        if unique_key == "system_init_sync":
            self._reset_init_sync(final_status)
        else:
            self._set_sync_busy(False)

    # --- Health Check ---

    async def check_health(self):
        """Start health check task."""
        if self.on_health_checking:
            self.on_health_checking()

        async def _run_health_check(task_id: str, **kwargs):
            try:
                self._tm.update_progress(task_id, 0.2, I18n.get("task_progress_checking"))
                result = await self._processor.check_data_health()
                self._tm.update_progress(task_id, 0.9, I18n.get("task_progress_analyzing"))

                if self.on_health_result:
                    self.on_health_result(result)

                return I18n.get("task_result_health_done")

            except asyncio.CancelledError:
                if self.on_health_cancelled:
                    self.on_health_cancelled()
                raise
            except Exception as e:
                logger.error(f"[DataSourceVM] Health check failed: {e}", exc_info=True)
                error_info = classify_error(e, context="general")
                if self.on_health_error:
                    self.on_health_error(get_error_message(error_info))
                raise
            finally:
                if self.on_health_finished:
                    self.on_health_finished()

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_health_check"),
            task_type=I18n.get("task_type_sys_check"),
            coroutine_factory=_run_health_check,
            cancellable=True,
            unique_key="sys_health_check",
        )

        if task_id is None:
            if self.on_health_finished:
                self.on_health_finished()

    # --- Full Daily Sync ---

    def execute_full_daily_sync(self):
        """Execute full daily sync (called by View after user confirms dialog)."""
        self._set_sync_busy(True, "daily_sync")

        async def _daily_logic(task_id: str, **kwargs):
            def _progress(c, t, msg):
                self._tm.update_progress(task_id, c / t if t else 0, msg)

            try:
                await self._processor.run_daily_update(progress_callback=_progress)
                if self.on_show_snack:
                    self.on_show_snack(
                        I18n.get("snack_full_sync_done_simple"),
                        "success",
                    )
                return I18n.get("ds_daily_update_done")
            except asyncio.CancelledError:
                if self.is_syncing and self.on_show_snack:
                    self.on_show_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                if self.on_show_snack:
                    self.on_show_snack(
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
            task = self._tm.get_task(task_id)
            cancel_event = getattr(task, "_cancel_event", None) if task else None
            try:
                self._tm.update_progress(task_id, 0.05, I18n.get("ds_doubao_rebuild_start"))
                # Manual trigger: manual_trigger=True → execute LLM-driven concept tagging.
                # ai_service injected via kwargs to satisfy R1 (data/ must not import services/).
                await self._processor.run_ai_concept_tagging(
                    task_id=task_id,
                    cancel_event=cancel_event,
                    manual_trigger=True,
                    ai_service=AIService(),
                )
                if self.on_show_snack:
                    self.on_show_snack(I18n.get("snack_doubao_done"), "success")
                return I18n.get("ds_doubao_rebuild_done")
            except asyncio.CancelledError:
                if self.is_syncing and self.on_show_snack:
                    self.on_show_snack(
                        I18n.get("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                if self.on_show_snack:
                    self.on_show_snack(
                        I18n.get("common_op_fail"),
                        "error",
                    )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_doubao_rebuild"),
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
            if self.on_show_snack:
                self.on_show_snack(I18n.get("ds_clear_cache_syncing"), "warning")
            return

        self._set_sync_busy(True, "cache_clear")

        async def _clear_logic(task_id: str, **kwargs):
            try:
                await self._cache.clear_all_cache()
                if self.on_show_snack:
                    self.on_show_snack(I18n.get("ds_cache_cleared"), "success")
                if self.on_cache_cleared:
                    self.on_cache_cleared()
                return I18n.get("ds_cache_clear_done")
            except Exception as ex:
                classify_error(ex, context="general")
                if self.on_show_snack:
                    self.on_show_snack(
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
        self.init_sync_cancellable = True

        if self.on_init_sync_started:
            self.on_init_sync_started()

        async def _run_initial_sync(task_id: str, **kwargs):
            try:
                if self.on_progress_update:
                    self.on_progress_update(0, I18n.get("wizard_status_init"))

                def _combined_progress(c, t, m):
                    if self.on_progress_update:
                        self.on_progress_update(c / t if t > 0 else 0, m)
                    self._tm.update_progress(
                        task_id,
                        c / t if t > 0 else 0,
                        f"[{c:.2f}/{t}] {m}",
                    )

                report = await self._processor.initialize_system(
                    progress_callback=_combined_progress,
                )

                if self._processor.is_cancelled():
                    raise asyncio.CancelledError()

                if report is None:
                    raise InitSyncError(I18n.get("ds_init_fail_generic"))

                self._reset_init_sync(TaskStatus.COMPLETED)
                if self.on_show_snack:
                    self.on_show_snack(I18n.get("settings_init_done"), "success")

                return I18n.get("sys_init_success")

            except asyncio.CancelledError:
                self._reset_init_sync(TaskStatus.CANCELLED)
                raise
            except InitSyncError as e:
                msg = str(e)
                logger.error(f"[DataSourceVM] Init sync failed: {e}")
                self._reset_init_sync(TaskStatus.FAILED)
                if self.on_show_snack:
                    self.on_show_snack(msg, "error")
                raise RuntimeError(msg) from e
            except Exception as e:
                msg = I18n.get("ds_init_fail_fmt")
                logger.error(f"[DataSourceVM] Init sync failed: {e}", exc_info=True)
                self._reset_init_sync(TaskStatus.FAILED)
                if self.on_show_snack:
                    self.on_show_snack(msg, "error")
                raise RuntimeError(msg) from e

        task_id = self._tm.submit_task(
            name=I18n.get("task_name_init_sync"),
            task_type=I18n.get("task_type_data_sync"),
            coroutine_factory=_run_initial_sync,
            cancellable=True,
            unique_key="system_init_sync",
        )

        if task_id is None:
            self.init_sync_cancellable = False
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
        if not self.is_syncing and not self._active_task_ids:
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
                if not self._active_task_ids and self.is_syncing and not recovered:
                    self._recover_after_task_terminated(unique_key, t.status)
                    recovered = True

    def recover_stale_state(self):
        """Recover from stale task state (e.g. after page remount)."""
        if not self.is_syncing and not self._active_task_ids:
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
        if not self._active_task_ids and self.is_syncing:
            self._set_sync_busy(False)

    async def get_health_report(self) -> dict:
        """Get health report data for dialog display."""
        return await self._processor.check_data_health()
