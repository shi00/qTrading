"""DataSourceViewModel — MVVM-002 fix.

Extracts business logic from DataSourceTab into a pure ViewModel.
Holds business state, calls services/data layer, notifies View via
frozen state snapshot + subscribe/_notify.

L771 合规: state 字段全部用 frozen dataclass / tuple[Row, ...],
VM 内部不持有 dict/DataFrame 作为业务状态 (移除 dual-track).
VM 不感知 locale: i18n 消息用 Message (key + params) 透传.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from utils.config_handler import ConfigHandler
from utils.error_classifier import classify_error
from utils.thread_pool import TaskType, ThreadPoolManager
from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.external.tushare_client import TushareClient
from services.ai_service import AIService
from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)


class InitSyncError(Exception):
    """Raised when init sync fails with a known generic error (e.g. report is None)."""


@dataclass(frozen=True)
class SnackRow:
    """Snack 通知行数据 frozen dataclass (L771 合规).

    替代 dual-track 的 (Message, str) tuple + version 持有模式, 直接放入 state.
    seq 字段确保连续相同内容也触发 use_state setter 更新 (非 dual-track:
    无 property 包装, 直接放 state 字段).
    """

    message: Message
    color_name: str
    seq: int = 0


@dataclass(frozen=True)
class HealthResultRow:
    """健康检查结果行数据 frozen dataclass (L771 合规).

    替代 dual-track 的 dict 持有模式, 扁平化 dict 结构直接放入 state.
    """

    status: str = "green"
    market_latest_local: str = ""
    market_lag_days: int = 0
    details_financial_coverage: float = 0.0
    details_missing_critical: int = 0
    details_missing_depth: int = 0
    details_missing_breadth: int = 0


@dataclass(frozen=True)
class DataSourceState:
    """DataSourceViewModel 的不可变状态快照。View 通过 subscribe 接收。

    L771 合规: 业务数据直接放入 state (frozen dataclass / Message),
    无 dual-track version + property 间接暴露模式.
    """

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
    progress_message: Message | None = None

    # --- 业务数据 (L771 合规: frozen dataclass / Message, 直接暴露) ---
    # health_result: 最近一次健康检查结果 (None 表示未检查)
    health_result: HealthResultRow | None = None
    # snack: 瞬态通知 (None 表示无待消费通知; seq 确保连续相同内容也触发更新)
    snack: SnackRow | None = None
    # health_error: 健康检查错误消息 (Message, VM 不感知 locale; None 表示无错误)
    health_error: Message | None = None

    # --- 瞬态信号 (无数据负载, 用 int 递增表示事件次数; 非 dual-track: 无 property 包装) ---
    cache_cleared_version: int = 0


class DataSourceViewModel(ObservableViewModelMixin[DataSourceState]):
    """ViewModel for DataSourceTab — manages data source business logic.

    L771 合规: state 字段全部用 frozen dataclass / Message,
    VM 内部不持有 dict/DataFrame 作为业务状态 (移除 dual-track).
    VM 不感知 locale: i18n 消息用 Message (key + params) 透传, View 渲染时翻译.
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

        # --- Snack seq counter (内部计数, 确保 SnackRow.seq 递增) ---
        self._snack_seq = 0

        # --- TaskManager 订阅 (Phase 3.1: 从 View 下沉到 VM, 内部行为) ---
        # _tm_callback 持有订阅 callback 引用, dispose 时用于 unsubscribe
        self._tm_callback: Callable[[list[AppTask]], None] | None = None
        self._subscribe_to_task_manager()

    def _subscribe_to_task_manager(self) -> None:
        """订阅 TaskManager 任务状态更新 (内部行为, View 不感知).

        Phase 3.1: 原 View 的 _setup_tm_subscription/_cleanup_tm_subscription 下沉到 VM,
        VM 构造时自动订阅, dispose 时取消订阅.
        """
        if self._tm_callback is not None:
            return
        self._tm_callback = self.handle_task_update
        self._tm.subscribe(self._tm_callback)

    def _unsubscribe_from_task_manager(self) -> None:
        """取消 TaskManager 订阅 (dispose 时调用)."""
        if self._tm_callback is not None:
            self._tm.unsubscribe(self._tm_callback)
            self._tm_callback = None

    def _cancel_all_active_tasks(self):
        """取消所有活跃任务（防孤儿），再清 _active_task_ids。

        cancel_task 对已终态任务 no-op（TaskManager._cancel_task_impl 有 status guard）。
        NOTE(lazy): init sync 任务仅走 asyncio cancel（cancel_task），不调 processor.request_cancel()
        的协作式取消信号。dispose 是同步方法无法 await request_cancel（async def）。
        ceiling: dispose 改为 async 或 DataProcessor 新增 sync request_cancel。init sync 仍会被
        asyncio cancel 终止，仅非"优雅退出"——对 VM 销毁场景可接受。
        upgrade: DataProcessor 新增 request_cancel_sync() 或 dispose 异步化时.
        """
        for task_id in self._active_task_ids.values():
            self._tm.cancel_task(task_id)
        self._active_task_ids.clear()

    def dispose(self):
        """清理资源：先取消订阅 + 取消活跃任务（防孤儿），再清引用与状态。

        cancellable=False 任务（如 cache_clear）的 cancel_task 是 no-op（TaskManager 记 warning），
        任务将继续运行至完成——属设计意图（不可取消任务应原子完成）。
        """
        self._unsubscribe_from_task_manager()
        self._cancel_all_active_tasks()
        self._subscribers.clear()
        self._state = DataSourceState()

    # --- Emitters (直接 _set_state, 无 dual-track) ---

    def _emit_snack(self, message: Message, color_name: str) -> None:
        """Store snack directly into state (L771 合规, 无 dual-track).

        seq 字段确保连续相同内容也触发 use_state setter 更新.
        """
        self._snack_seq += 1
        self._set_state(snack=SnackRow(message=message, color_name=color_name, seq=self._snack_seq))

    def _emit_health_result(self, result: HealthResultRow) -> None:
        """Store health result directly into state and clear health_error.

        新健康结果覆盖旧错误 (状态转换: error → ok).
        """
        self._set_state(health_result=result, health_error=None)

    def _emit_health_error(self, error_msg: Message) -> None:
        """Store health error message directly into state (VM 不感知 locale).

        error_msg 是 Message (i18n key + params), View 渲染时翻译.
        """
        self._set_state(health_error=error_msg)

    def _emit_cache_cleared(self) -> None:
        """Notify cache cleared via cache_cleared_version (无数据瞬态信号, 非 dual-track)."""
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
                if not self._tm.update_progress(task_id, 0.2, Message("task_progress_checking")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")
                result = await self._processor.check_data_health()
                if not self._tm.update_progress(task_id, 0.9, Message("task_progress_analyzing")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")

                self._emit_health_result(_health_dict_to_row(result))

                return Message("task_result_health_done")

            except asyncio.CancelledError:
                self._set_state(health_checking=False)
                # health_cancelled: use a transient flag via state diff
                # View detects health_checking False transition as "cancelled/finished"
                raise
            except Exception as e:
                logger.error("[DataSourceVM] Health check failed: %s", e, exc_info=True)
                error_info = classify_error(e, context="general")
                self._emit_health_error(_error_info_to_message(error_info))
                raise
            finally:
                self._set_state(health_checking=False)

        task_id = self._tm.submit_task(
            name=Message("task_name_health_check"),
            task_type=Message("task_type_sys_check"),
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
                    Message("snack_full_sync_done_simple"),
                    "success",
                )
                return Message("ds_daily_update_done")
            except asyncio.CancelledError:
                if self._state.is_syncing:
                    self._emit_snack(
                        Message("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    Message("common_op_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=Message("task_name_daily_sync"),
            task_type=Message("sched_task_type_daily"),
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
                if not self._tm.update_progress(task_id, 0.05, Message("ds_ai_concept_rebuild_start")):
                    raise asyncio.CancelledError("task cancelled by user (update_progress returned False)")
                # Manual trigger: manual_trigger=True → execute LLM-driven concept tagging.
                # ai_service injected via kwargs to satisfy R1 (data/ must not import services/).
                await self._processor.run_ai_concept_tagging(
                    task_id=task_id,
                    cancel_event=cancel_event,
                    manual_trigger=True,
                    ai_service=self._ai_service,
                )
                self._emit_snack(Message("snack_ai_concept_done"), "success")
                return Message("ds_ai_concept_rebuild_done")
            except asyncio.CancelledError:
                if self._state.is_syncing:
                    self._emit_snack(
                        Message("settings_msg_sync_cancelled"),
                        "warning",
                    )
                raise
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    Message("common_op_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=Message("task_name_ai_concept_rebuild"),
            task_type=Message("ds_task_type_ai_tagging"),
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
            self._emit_snack(Message("ds_clear_cache_syncing"), "warning")
            return

        self._set_sync_busy(True, "cache_clear")

        async def _clear_logic(task_id: str, **kwargs):
            try:
                await self._cache.clear_all_cache()
                self._emit_snack(Message("ds_cache_cleared"), "success")
                self._emit_cache_cleared()
                return Message("ds_cache_clear_done")
            except Exception as ex:
                classify_error(ex, context="general")
                self._emit_snack(
                    Message("ds_clean_fail"),
                    "error",
                )
                raise
            finally:
                self._set_sync_busy(False)

        task_id = self._tm.submit_task(
            name=Message("task_name_clear_cache"),
            task_type=Message("ds_task_type_system"),
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
                self._set_state(progress=0, progress_message=Message("wizard_status_init"))

                def _combined_progress(c, t, m):
                    # NOTE(lazy): m 是 service 层传入的字符串,作为 key 透传. ceiling: service 层产出 Message. upgrade: service 层重构.
                    self._set_state(progress=c / t if t > 0 else 0, progress_message=Message(m))
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
                    raise InitSyncError(Message("ds_init_fail_generic"))

                self._reset_init_sync(TaskStatus.COMPLETED)
                self._emit_snack(Message("settings_init_done"), "success")

                return Message("sys_init_success")

            except asyncio.CancelledError:
                self._reset_init_sync(TaskStatus.CANCELLED)
                raise
            except InitSyncError as e:
                # Task 3.1: InitSyncError 携带 Message (替代翻译字符串), 透传给 RuntimeError.
                # TaskManager 内部不使用 task.result, 故 RuntimeError 携带 Message 不影响 UI.
                logger.error("[DataSourceVM] Init sync failed: %s", e, exc_info=True)
                self._reset_init_sync(TaskStatus.FAILED)
                self._emit_snack(Message("ds_init_fail_generic"), "error")
                raise RuntimeError(e.args[0]) from e
            except Exception as e:
                # Task 3.1: msg 改为 Message (替代 I18n.get 翻译字符串), 透传给 RuntimeError.
                msg = Message("ds_init_fail_fmt")
                logger.error("[DataSourceVM] Init sync failed: %s", e, exc_info=True)
                self._reset_init_sync(TaskStatus.FAILED)
                self._emit_snack(Message("ds_init_fail_fmt"), "error")
                raise RuntimeError(msg) from e

        task_id = self._tm.submit_task(
            name=Message("task_name_init_sync"),
            task_type=Message("task_type_data_sync"),
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

    def get_history_years(self) -> int:
        """读取历史数据年限配置 (供 View 渲染初始值).

        Phase 3.1: 从 View 下沉 (原 View 直接调 ConfigHandler.get_init_history_years).
        """
        return ConfigHandler.get_init_history_years()

    async def save_tushare_token(self, token: str) -> None:
        """异步保存 Tushare token 到配置并更新客户端 (R16: IO offload via ThreadPoolManager).

        Phase 3.1: 从 View 下沉 (原 View 用 ThreadPoolManager.run_async 包 sync VM 方法).
        """
        token = token.strip()
        if not token:
            return
        await ThreadPoolManager().run_async(TaskType.IO, self._save_tushare_token_sync, token)

    def _save_tushare_token_sync(self, token: str) -> None:
        """同步写入 token (供 ThreadPoolManager 调度, R16 IO offload)."""
        ConfigHandler.save_token(token)
        client = TushareClient()
        client.set_token(token)

    async def set_history_years(self, years: int) -> None:
        """异步保存历史年限配置 (R16: IO offload via ThreadPoolManager).

        Phase 3.1: 从 View 下沉 (原 View 用 ThreadPoolManager.run_async 包 sync VM 方法).
        """
        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_init_history_years, years)

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

    def get_data_processor(self) -> DataProcessor:
        """暴露 DataProcessor 实例 (Task 5.1: 从 View 迁入, 内聚到 VM).

        View 通过本方法获取处理器实例并传递给 HealthScanDialog 组件,
        不再直接 import ``data`` 业务对象 (CLAUDE.md §3.2 MVVM 契约)。
        """
        return self._processor


# ============================================================
# 纯转换函数 (dict → frozen dataclass, 模块级, 无副作用)
# ============================================================


def _health_dict_to_row(result: dict) -> HealthResultRow:
    """dict → HealthResultRow (L771 合规, 扁平化 dict 结构)."""
    market_info = result.get("market") or {}
    details = result.get("details") or {}

    def _to_int(val: Any, default: int = 0) -> int:
        if isinstance(val, (int, float)):
            return int(val)
        return default

    def _to_float(val: Any, default: float = 0.0) -> float:
        if isinstance(val, (int, float)):
            return float(val)
        return default

    return HealthResultRow(
        status=str(result.get("status", "green")),
        market_latest_local=str(market_info.get("latest_local", "") or ""),
        market_lag_days=_to_int(market_info.get("lag_days", 0)),
        details_financial_coverage=_to_float(details.get("financial_coverage", 0.0)),
        details_missing_critical=_to_int(details.get("missing_critical", 0)),
        details_missing_depth=_to_int(details.get("missing_depth", 0)),
        details_missing_breadth=_to_int(details.get("missing_breadth", 0)),
    )


def _error_info_to_message(error_info: dict) -> Message:
    """error_info dict → Message (VM 不感知 locale).

    替代 get_error_message() 已翻译字符串, 改为 i18n key + format_args 透传.
    消除 NOTE(lazy) 标记的 locale 感知技术债.
    """
    message_key = error_info.get("message_key", "common_err_unknown")
    format_args = error_info.get("format_args") or {}
    return Message(message_key, dict(format_args))
