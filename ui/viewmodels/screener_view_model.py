import asyncio
import datetime
import inspect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import pandas as pd

from core.i18n import I18n
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager
from data.cache.cache_manager import CacheManager
from data.data_processor import DataProcessor
from data.persistence.quality_gate import QualityGateError
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from strategies.all_strategies import StrategyManager
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)

# Language-neutral constant for task name matching between ViewModel and View.
# Must NOT be i18n'd — both sides use this as a programmatic identifier.
TASK_NAME_PREFIX = "strategy_screening"

# Stream card throttle and limit (moved from View, VM owns card lifecycle)
# NOTE(lazy): 流式节流 50ms (~20fps) 平衡流畅度与 reconcile 压力. ceiling: 策略结果行数 >5000 时 20fps 可能卡顿. upgrade: 行数突破 ceiling 或用户反馈卡顿时改 33ms/动态节流.
_STREAM_THROTTLE = 0.05  # seconds
_MAX_LOG_CARDS = 10


@dataclass(frozen=True)
class LogEntry:
    """Single AI streaming log entry (immutable, §3.0.1)."""

    name: str
    score: float
    thinking: str


@dataclass(frozen=True)
class StreamCard:
    """Single streaming/AI placeholder card (immutable, state-driven).

    - is_analyzing=True: 占位卡 (并发非流式模式, ProgressRing + "分析中")
    - is_analyzing=False: 流式卡 (reasoning + content Markdown)
    """

    name: str
    reasoning: str = ""
    content: str = ""
    is_analyzing: bool = False


@dataclass(frozen=True)
class HistoryTreeRow:
    """历史树单行 (immutable, state-driven, Task 3.2).

    VM 内聚日期格式化 (不依赖 I18n); strategies 中的 strategy_name 为 raw key,
    View 渲染时调 translate_strategy_name 翻译为当前 locale (§3.2 VM 不感知 locale).
    """

    display_date: str
    d_key: str
    total_cnt: int
    strategies: tuple[dict, ...]


@dataclass(frozen=True)
class HistoryTreeState:
    """历史树子结构 (immutable, state-driven, Task 3.2).

    View 不再持有 rows/offset/has_more/loading 的 use_state, 改为派生自
    state.history_tree (消除双轨状态, 每项业务状态只有一个 owner).
    """

    rows: tuple[HistoryTreeRow, ...] = ()
    offset: int = 0
    has_more: bool = False
    loading: bool = False


@dataclass(frozen=True)
class ScreenerState:
    """Immutable state snapshot for ScreenerView (§3.0.1).

    DataFrame (_full_results) is held internally by VM (双轨制, §3.0.4);
    View reads vm.current_page_data after _notify. data_version increments
    on every _full_results mutation so View can invalidate table cache.
    """

    # Pagination
    page_no: int = 1
    page_size: int = 50
    total_pages: int = 0
    total_items: int = 0
    # Sorting
    sort_column: str | None = None
    sort_ascending: bool = True
    # Status bar (message + color)
    loading: bool = False
    status_message: Message | None = None
    status_color: str = ""
    # AI streaming logs (append-only tuple)
    logs: tuple[LogEntry, ...] = ()
    # AI streaming/placeholder cards (state-driven, §3.2 MVVM)
    stream_cards: tuple[StreamCard, ...] = ()
    # Strategy selection (R.2.1: 内聚到 VM, 消除 View 双源真相)
    selected_strategy: str | None = None
    tier_hint: str | None = None
    # Mode: "REALTIME" or "HISTORY"
    mode: str = "REALTIME"
    # Task unlock signal (View resets after consuming)
    task_unlocked: bool = False
    # Data version (incremented on _full_results change)
    data_version: int = 0
    # Strategy loading (R.2.6.1: 业务状态迁入 VM, View 构建 Flet Options 时翻译)
    strategies_loaded: bool = False
    strategies_with_dep: dict[str, dict] = field(default_factory=dict)
    # Strategy description (R.2.6.2: 业务状态迁入 VM, View 映射 color 标识符到 AppColors)
    strategy_desc: str = ""
    strategy_desc_color: str = "default"  # 语义标识符: "default"/"warning"
    # History tree (Task 3.2: 子结构内聚 rows/offset/has_more/loading, 消除 View 双轨状态)
    history_tree: HistoryTreeState = field(default_factory=HistoryTreeState)


class ScreenerViewModel(ObservableViewModelMixin[ScreenerState]):
    """ViewModel for ScreenerView.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (ScreenerState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - DataFrame held internally (双轨制 §3.0.4); View reads current_page_data
    """

    AI_UPDATE_INTERVAL = 0.5  # Seconds

    def __init__(self):
        # Dependencies
        self.data_processor = DataProcessor()
        self.strategy_mgr = StrategyManager()
        self.review_mgr = ReviewManager()

        # Immutable state + subscribers (§3.0.1)
        self._state: ScreenerState = ScreenerState()
        self._subscribers: list[Callable[[ScreenerState], None]] = []

        # Internal mutable data (双轨制, not in state)
        self._full_results: pd.DataFrame | None = None
        self._ai_buffer: list[dict] = []
        self._discarded_buffer: list[dict] = []  # U-3 fix: buffer for discarded items during HISTORY mode
        self._last_ai_update = 0.0
        self._flush_pending = False

        # History mode snapshot (internal)
        self._realtime_snapshot: dict | None = None

        # Stream card buffers (VM owns card lifecycle, §3.2 MVVM state-driven)
        self._stream_buffers: dict[str, dict] = {}

        # Async infrastructure
        self._main_loop = None
        self._background_tasks: set = set()
        self._threadsafe_futures: set = set()
        # Task 4.2: dispose 后阻止延迟完成的任务更新 state/subscriber
        self._disposed = False

        # TaskManager subscription state
        self._strategy_submitted = False

    # --- State snapshot + subscribe/_notify (§3.0.1) ---

    def subscribe(self, callback: Callable[[ScreenerState], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。同时捕获 main loop（hook 在主循环注册）。"""
        self._subscribers.append(callback)
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("ScreenerViewModel subscribed without running loop")

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _set_state(self, **changes) -> None:
        """Update state fields and notify subscribers."""
        if self._disposed:
            return
        self._state = replace(self._state, **changes)
        self._notify()

    def _update_pagination(self, page_size: int | None = None, page_no: int | None = None) -> None:
        """Recompute pagination fields in state. Does NOT notify — caller must _set_state or _notify."""
        ps = page_size if page_size is not None else self._state.page_size
        if self._full_results is not None:
            total_items = len(self._full_results)
            total_pages = (total_items + ps - 1) // ps
        else:
            total_items = 0
            total_pages = 0
        pn = page_no if page_no is not None else self._state.page_no
        self._state = replace(
            self._state,
            page_size=ps,
            page_no=pn,
            total_items=total_items,
            total_pages=total_pages,
        )

    def init(self):
        """Initialize resources"""
        pass

    def dispose(self):
        """Cleanup resources and ensure aggressive GC of large dataframes"""
        # Task 4.2: 先标记 disposed, 使后续延迟完成的任务 _set_state/_notify 不再
        # 更新 state/subscriber (取消是协作式的, 任务可能仍执行到下一个 await)
        self._disposed = True
        self.unsubscribe_task_manager()
        self._subscribers.clear()
        self._stream_buffers.clear()
        self._main_loop = None

        for f in list(self._threadsafe_futures):
            f.cancel()
        self._threadsafe_futures.clear()

        for t in list(self._background_tasks):
            if not t.done():
                t.cancel()
        # NOTE(lazy): 不立即 clear _background_tasks — done_callback (_on_background_task_done)
        # 会在任务完成时移除并读取 exception(), 避免 'Task exception was never retrieved'.
        # ceiling: 事件循环关闭导致 callback 不触发时, 任务随 VM 一起被 GC.
        # upgrade: 引入 async_dispose() 显式 await drain (Flet use_effect cleanup 已
        # 确认支持 async, 本任务范围内不引入以保持微创修改; app-shutdown 由
        # ShutdownCoordinator._step0_cancel_tasks 的 asyncio.wait 覆盖).

        self._full_results = None
        self._ai_buffer = []
        self._realtime_snapshot = None
        self._state = ScreenerState()

    def _on_background_task_done(self, task: asyncio.Task) -> None:
        """Done callback: 移除已完成任务并记录非取消异常.

        - 丢弃任务引用前读取 task.exception() 标记异常已 retrieved,
          避免 'Task exception was never retrieved' 警告 (DoD #3).
        - CancelledError 不记录为 error, 取消正常传播 (R2/DoD #4).
        """
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("[ScreenerVM] Background task failed: %s", exc, exc_info=exc)

    # --- Data Actions ---

    async def get_strategies(self) -> dict[str, str]:
        return self.strategy_mgr.get_all_names()

    def get_strategy_desc(self, key: str) -> str:
        # I18N_GET_ALLOWED: 返回翻译字符串供 update_strategy_desc 拼接为 state.strategy_desc (str).
        # 迁移路径: state.strategy_desc 改为 Message 结构, View 渲染时翻译; 同时重设
        # strategy_obj.get_dynamic_description(params) 返回值为 i18n key (而非翻译字符串),
        # 整体与 R.3 strategy_name 标准化一并处理 (Task 3.1 遗留).
        st = self.strategy_mgr.get_strategy(key)
        return I18n.get(st.desc_key) if st else ""

    def get_strategy_params(self, key: str) -> list:
        """Get dynamic parameter definitions for a strategy."""
        # Defensive copy to prevent mutating a strategy's cached class attributes
        params = list(self.strategy_mgr.get_strategy_params(key))

        # Inject AI System Prompt override parameter globally so ALL strategies can use it.
        # Check to avoid duplicate if a strategy still happens to implement it natively.
        if not any(p.get("name") == "ai_system_prompt" for p in params):
            params.append(
                {
                    "name": "ai_system_prompt",
                    "label_key": "ai_system_prompt",
                    "type": "textarea",
                    "default": "",  # UI uses vm.get_base_prompt to map the value dynamically
                },
            )

        return params

    def get_base_prompt(self, strategy_key: str) -> str:
        """获取策略基础 prompt (Task 5.1: 从 View 迁入, 内聚到 VM).

        View 通过本方法消费 ``strategy_prompts.get_base_prompt``，不再直接 import
        ``strategies`` 业务对象 (CLAUDE.md §3.2 MVVM 契约)。
        """
        from strategies.strategy_prompts import get_base_prompt

        return get_base_prompt(strategy_key)

    async def reset_strategy_prompt(self, strategy_key: str) -> str:
        """重置策略 prompt 为默认值 (Phase 3.3: 从 View 迁入, 内聚到 VM).

        通过 ``ConfigHandler.set_strategy_prompt(strategy_key, None)`` 清除用户覆盖,
        然后返回基础 prompt 字符串供 View 更新 UI state.

        Args:
            strategy_key: 策略 key

        Returns:
            基础 prompt 字符串

        Raises:
            Exception: ConfigHandler 失败时抛出 (View 负责展示错误)
        """
        from utils.config_handler import ConfigHandler

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strategy_key, None)
        # get_base_prompt 内部调 ConfigHandler.get_strategy_prompt / get_ai_system_prompt (load_config IO),
        # 需 ThreadPoolManager 包装 (R16).
        return str(await ThreadPoolManager().run_async(TaskType.IO, self.get_base_prompt, strategy_key))

    async def save_strategy_prompt(self, strategy_key: str, prompt: str) -> tuple[bool, str | None]:
        """保存策略 prompt (Phase 3.3: 从 View 迁入, 内聚到 VM).

        内部完成 ``validate_prompt`` + ``ConfigHandler.set_strategy_prompt`` 编排.

        Args:
            strategy_key: 策略 key
            prompt: 用户输入的 prompt 字符串

        Returns:
            (success, error_key): 成功时 (True, None); 失败时 (False, error_key) 其中
            error_key 为 i18n key (如 ``prompt_err_length`` / ``prompt_err_injection``)
        """
        from utils.prompt_guard import validate_prompt

        is_valid, warning = validate_prompt(prompt)
        if not is_valid:
            return False, warning

        from utils.config_handler import ConfigHandler

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strategy_key, prompt)
        return True, None

    def get_column_alias(self, table_name: str | None, col: str) -> str:
        """获取列别名 (Task 5.1: 从 View 迁入, 内聚到 VM).

        View 通过本方法消费 ``MetaDataManager.get_column_alias``，不再直接 import
        ``data`` 业务对象 (CLAUDE.md §3.2 MVVM 契约)。
        """
        from data.persistence.metadata_manager import MetaDataManager

        return MetaDataManager.get_column_alias(table_name, col)

    def select_strategy(self, key: str | None) -> None:
        """选中策略 + 计算 tier_hint（R.2.1: 内聚到 VM, 消除 View 双源真相）。

        Args:
            key: 策略 key, None 表示清空选择
        """
        tier_hint = self._compute_tier_hint(key)
        self._set_state(selected_strategy=key, tier_hint=tier_hint)

    def load_strategies(self) -> None:
        """加载策略列表到 state (R.2.6.1: 业务状态迁入 VM).

        从 strategy_mgr 获取策略+依赖信息, 存入 state.strategies_with_dep.
        View 渲染时调 _build_strategy_options(state.strategies_with_dep, ...) 构建 Flet Options,
        确保 locale 切换后 Options 自动重新翻译 (避免 use_state 缓存旧 locale 翻译).
        """
        try:
            strategies_with_dep = self.strategy_mgr.get_all_with_dependencies()
            self._set_state(
                strategies_with_dep=strategies_with_dep,
                strategies_loaded=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("[ScreenerVM] Failed to load strategies: %s", e, exc_info=True)
            self._set_state(
                status_message=Message("screener_load_failed", {}),
                status_color="error",
            )

    def update_strategy_desc(self, selected_strategy: str | None, params: dict | None = None) -> None:
        """更新策略描述和颜色到 state (R.2.6.2: 业务状态迁入 VM).

        计算策略描述文本和颜色语义标识符, 存入 state.strategy_desc/strategy_desc_color.
        View 渲染时映射 color 标识符到 AppColors (避免 VM 感知 UI 颜色, §3.2).

        Args:
            selected_strategy: 策略 key, None 表示清空
            params: 动态参数 (可选, 用于 get_dynamic_description; None 时用策略默认参数)
        """
        # I18N_GET_ALLOWED: strategy_desc 是 str 字段, 需拼接 warning_suffix (含翻译字符串)
        # 形成 desc 字符串. 迁移路径: state.strategy_desc 改为 Message, 嵌套 desc_msg + missing_apis
        # (与 R.3 strategy_name 标准化一并处理, Task 3.1 遗留).
        if not selected_strategy:
            self._set_state(strategy_desc="", strategy_desc_color="default")
            return

        try:
            strategy_obj = self.strategy_mgr.get_strategy(selected_strategy)
            strategies_with_dep = self.strategy_mgr.get_all_with_dependencies()
            dep_info = strategies_with_dep.get(selected_strategy, {})

            if strategy_obj:
                if params is None:
                    params = {p["name"]: p.get("default") for p in strategy_obj.get_parameters()}
                desc = strategy_obj.get_dynamic_description(params)
            else:
                desc = self.get_strategy_desc(selected_strategy)

            # NOTE(lazy): I18n.get(strategy_missing_apis) 及 get_strategy_desc 回退路径的翻译值拼入 desc 字符串,
            # locale 切换后不自动刷新. ceiling: VM state 非 Message, 无 *_key params 翻译机制.
            # upgrade: desc 改为 Message 结构或引入 desc_key+params 时统一修复 (与 R.2.5 同类, R.3 一并处理).
            if dep_info.get("missing_apis"):
                warning_suffix = f"\n⚠️ {I18n.get('strategy_missing_apis')}: {', '.join(dep_info['missing_apis'])}"
                desc = f"{desc}{warning_suffix}"
                color = "warning"
            else:
                color = "default"

            self._set_state(strategy_desc=desc, strategy_desc_color=color)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[ScreenerVM] update_strategy_desc failed: %s", e, exc_info=True)
            self._set_state(strategy_desc="", strategy_desc_color="default")

    def set_history_viewing_status(self, date_str: str, label: str) -> None:
        """设置历史查看状态到 state (R.2.6.3: 业务状态迁入 VM).

        将历史查看状态包装为 Message + params, 存入 state.status_message/status_color.
        View 传入已格式化的 date_str 和已翻译的 label (因 translate_strategy_name 是 View 层 i18n 函数),
        VM 只存 key + params 不调 I18n.get (§3.2 VM 不感知 locale).

        Args:
            date_str: 已格式化的日期字符串 (如 "2024-12-27")
            label: 已翻译的标签字符串 (如 "#abc12345" 或 "价值策略" 或 "全部策略")
        """
        # NOTE(lazy): label 为 View 层已翻译字符串 (translate_strategy_name 是 View 层函数),
        # locale 切换后 state.status_message.params["label"] 残留旧 locale 翻译.
        # ceiling: translate_strategy_name 未迁入 VM 或未引入 strategy_name_key 机制.
        # upgrade: R.3 strategy_name 标准化后, label 改为传 raw strategy_name + View 渲染时翻译.
        self._set_state(
            status_message=Message(
                "screener_history_viewing",
                {"date": date_str, "label": label},
            ),
            status_color="info",
        )

    @staticmethod
    def _compute_tier_hint(selected_strategy: str | None) -> str | None:
        """检查策略档位是否足够，不足时返回 i18n key，否则 None。

        返回 i18n key（非翻译值），符合 §3.2 "VM 只产出 i18n key"。
        View 渲染时 ``I18n.get(state.tier_hint)``。
        """
        if not selected_strategy:
            return None
        try:
            from data.external.tushare_client import TushareClient
            from services.ai_service import get_strategy_min_tier
            from utils.config_handler import ConfigHandler

            current_tier = ConfigHandler.get_tushare_point_tier()
            min_tier = get_strategy_min_tier(selected_strategy)
            client = TushareClient()
            if client.get_tier_order(current_tier) < client.get_tier_order(min_tier):
                return "sys_strategy_tier_hint"
        except Exception as e:
            logger.debug("[ScreenerVM] tier hint check skipped: %s", e, exc_info=True)
        return None

    async def run_strategy(
        self,
        strategy_key: str,
        save_results: bool = True,
        params: dict | None = None,  # type: ignore[untyped]
    ):
        """Execute strategy screening via the global TaskManager."""
        from utils.correlation import ensure_correlation_id

        ensure_correlation_id()
        self.clear_stream_cards()

        strategy = self.strategy_mgr.get_strategy(strategy_key)
        if not strategy:
            logger.error("[ScreenerVM] Strategy not found: %s", strategy_key)
            self._set_state(
                status_message=Message("screener_strategy_not_found"),
                status_color="error",
            )
            return

        # Define the inner coroutine for the task manager
        async def _execute_screening(task_id: str, **kwargs):
            try:
                # 1. Prepare Context (may trigger massive data load)
                TaskManager().update_progress(
                    task_id,
                    0.05,
                    Message("task_loading_data"),
                )
                context = await self.data_processor.get_strategy_data()
                if not context:
                    TaskManager().update_progress(
                        task_id,
                        0.1,
                        Message("task_cache_empty_init"),
                    )
                    await self.data_processor.init_data()
                    context = await self.data_processor.get_strategy_data()

                if not context or "screening_data" not in context or context["screening_data"].empty:
                    raise RuntimeError("No valid screening data available")

                diagnostics = context.get("_diagnostics") if isinstance(context, dict) else None
                if isinstance(diagnostics, dict) and diagnostics.get("strategy_ready") is False:
                    table_status = diagnostics.get("table_status") or {}
                    not_ready = [
                        key
                        for key, status in table_status.items()
                        if isinstance(status, dict) and not status.get("ready", True)
                    ]
                    if not_ready:
                        self._set_state(
                            status_message=Message(
                                "strategy_dep_degraded_detail",
                                {"tables": ", ".join(not_ready)},
                            ),
                            status_color="warning",
                        )
                    else:
                        self._set_state(
                            status_message=Message("strategy_dep_degraded"),
                            status_color="warning",
                        )

                context["data_processor"] = self.data_processor
                context["params"] = params or {}  # Dynamic strategy parameters from UI

                # Setup AI Callbacks
                # (Forward updates both to ViewModel local UI and Global TaskManager)
                def _combined_ai_progress(current, total, msg):
                    self._on_ai_progress(current, total, msg)  # For local View UI
                    TaskManager().update_progress(
                        task_id,
                        current / total if total > 0 else 0,
                        f"[{current}/{total}] {msg}",
                    )

                context["on_progress"] = _combined_ai_progress
                context["on_result"] = self._on_ai_result_stream
                context["on_stream_start"] = self._on_stream_start_adapter
                context["on_card_start"] = self._on_card_start_adapter

                # We inject the task_id into context so deep AI tasks can check cancellation
                context["_task_id"] = task_id

                TaskManager().update_progress(
                    task_id,
                    0.2,
                    Message("task_executing_strategy", {"name_key": strategy.name_key}),
                )

                if inspect.iscoroutinefunction(strategy.filter):
                    # Async strategy (e.g. PolarsBaseStrategy) — CPU-intensive work
                    # is already offloaded inside the strategy's filter() method,
                    # so awaiting here only blocks for IO (thread pool result, AI API calls)
                    result_df = await strategy.filter(context)
                else:
                    # Sync strategy — offload entire filter() to CPU thread pool
                    result_df = await ThreadPoolManager().run_async(
                        TaskType.CPU,
                        strategy.filter,
                        context,
                    )

                TaskManager().update_progress(
                    task_id,
                    0.95,
                    Message("task_aggregating_results"),
                )

                if result_df is not None and not result_df.empty:
                    self._full_results = result_df
                    self._update_pagination(page_no=1)

                    if save_results:
                        analysis_trade_date = context.get("trade_date")
                        if not analysis_trade_date:
                            raise RuntimeError(
                                "Missing analysis trade_date in screening context; refusing to save results",
                            )
                        import uuid as _uuid

                        run_id = _uuid.uuid4().hex[:16]
                        await self.review_mgr.save_results(
                            strategy.name_key,
                            result_df,
                            trade_date=analysis_trade_date,
                            run_id=run_id,
                            params_snapshot=params or {},
                        )

                    self._set_state(
                        page_no=1,
                        loading=False,
                        status_message=Message(
                            "screener_done_saved",
                            {"count": len(result_df)},
                        ),
                        status_color="success",
                        data_version=self._state.data_version + 1,
                    )
                    return Message("task_screening_success", {"count": len(result_df)})

                self._full_results = pd.DataFrame()
                self._update_pagination(page_no=1)
                self._set_state(
                    page_no=1,
                    loading=False,
                    status_message=Message("screener_no_results"),
                    status_color="warning",
                    data_version=self._state.data_version + 1,
                )
                return Message("screener_no_results")

            except asyncio.CancelledError:
                self._set_state(
                    loading=False,
                    status_message=Message("screener_cancelled"),
                    status_color="warning",
                )
                raise
            except QualityGateError as e:
                logger.warning(
                    "[ScreenerVM] Strategy execution blocked by Quality Gate: %s",
                    e,
                    exc_info=True,
                )
                self._set_state(
                    loading=False,
                    status_message=Message("screener_blocked", {"reason": str(e)}),
                    status_color="warning",
                )
                return Message("screener_blocked", {"reason": str(e)})
            except Exception as e:
                logger.error(
                    "[ScreenerVM] Strategy execution failed: %s",
                    e,
                    exc_info=True,
                )
                # Show generic user-friendly message, avoid raw traceback on UI
                self._set_state(
                    loading=False,
                    status_message=Message("screener_exec_error"),
                    status_color="error",
                )
                raise RuntimeError(f"Strategy execution crashed: {e}") from e

        # Reset Local UI State
        self._full_results = None
        self._ai_buffer = []
        self._set_state(
            page_no=1,
            loading=True,
            # §3.2: VM 只产出 i18n key (name_key), View 渲染时翻译为当前 locale 策略名.
            # 避免 VM 持有翻译字符串导致 locale 切换后 state 残留旧 locale 翻译.
            status_message=Message(
                "screener_running_strategy",
                {"name_key": strategy.name_key},
            ),
            status_color="info",
        )

        # Dispatch to TaskManager!
        # Task 3.1: name 改为 Message (复用 screener_running_strategy key + name_key params),
        # task_type 也是 Message. _on_tasks_updated 通过 task_type.key 检测策略任务 (替代
        # 旧 TASK_NAME_PREFIX in t.name 字符串检测, 因 t.name 现为 Message 实例不支持 `in`).
        task_id = TaskManager().submit_task(
            name=Message("screener_running_strategy", {"name_key": strategy.name_key}),
            task_type=Message("task_type_ai_screening"),
            coroutine_factory=_execute_screening,
            cancellable=True,
        )

        if task_id is None:
            self._set_state(
                loading=False,
                status_message=Message("screener_task_rejected"),
                status_color="warning",
            )
        else:
            self._strategy_submitted = True

    # --- Sorting & Pagination ---

    async def sort_data(self, column_key: str, ascending: bool | None = None):
        """Sort data using ThreadPool to avoid blocking UI"""
        if self._full_results is None or self._full_results.empty:
            return

        if ascending is not None:
            sort_column = column_key
            sort_ascending = ascending
        elif self._state.sort_column == column_key:
            sort_ascending = not self._state.sort_ascending
            sort_column = column_key
        else:
            sort_column = column_key
            sort_ascending = True

        self._set_state(loading=True)

        try:
            # Offload sorting to thread
            sorted_df = await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._sort_helper,
                self._full_results,
                column_key,
                sort_ascending,
            )

            self._full_results = sorted_df
            self._set_state(
                sort_column=sort_column,
                sort_ascending=sort_ascending,
                page_no=1,
                loading=False,
                data_version=self._state.data_version + 1,
            )

        except Exception as e:
            logger.error("Sort failed: %s", e, exc_info=True)
            self._set_state(loading=False)

    @staticmethod
    def _sort_helper(df, col, ascending):
        """Static helper for pickling/thread safety"""
        try:
            return df.sort_values(by=col, ascending=ascending, na_position="last")
        except KeyError:
            return df

    def change_page(self, delta: int):
        new_page = self._state.page_no + delta
        if 1 <= new_page <= self._state.total_pages:
            self._set_state(page_no=new_page)

    def change_page_size(self, new_size: int):
        """Update pagination size and jump back to page 1."""
        if new_size > 0 and new_size != self._state.page_size:
            self._update_pagination(page_size=new_size, page_no=1)
            self._notify()

    def get_current_page_data(self):
        """Get data for current page (Synchronous, fast slicing)"""
        if self._full_results is None or self._full_results.empty:
            return pd.DataFrame()

        start = (self._state.page_no - 1) * self._state.page_size
        end = start + self._state.page_size
        # Slicing is fast enough for main thread
        return self._full_results.iloc[start:end]

    # --- Stream Card Management (state-driven, §3.2 MVVM) ---

    def clear_stream_cards(self) -> None:
        """Clear all stream cards and buffers (called on new run)."""
        self._stream_buffers.clear()
        self._set_state(stream_cards=())

    def start_stream_card(self, name: str, is_analyzing: bool = False) -> None:
        """Create a new stream/placeholder card."""
        self._stream_buffers[name] = {"reasoning": "", "content": "", "last_flush": 0.0, "pending": False}
        card = StreamCard(name=name, is_analyzing=is_analyzing)
        new_cards = (self._state.stream_cards + (card,))[-_MAX_LOG_CARDS:]
        self._set_state(stream_cards=new_cards)

    def append_stream_chunk(self, name: str, chunk: str, is_reasoning: bool) -> None:
        """Accumulate LLM chunk, throttle-flush to state."""
        buf = self._stream_buffers.get(name)
        if not buf:
            return
        if is_reasoning:
            buf["reasoning"] += chunk
        else:
            buf["content"] += chunk
        now = time.time()
        if now - buf["last_flush"] >= _STREAM_THROTTLE:
            self._flush_stream_card(name)
        else:
            buf["pending"] = True

    def finalize_stream_card(self, name: str) -> None:
        """Force flush pending buffer (called by strategy on completion)."""
        buf = self._stream_buffers.get(name)
        if buf and buf.get("pending"):
            self._flush_stream_card(name)

    def _flush_stream_card(self, name: str) -> None:
        """Flush single card buffer to state."""
        buf = self._stream_buffers.get(name)
        if not buf:
            return
        # Guard: card may have been truncated by _MAX_LOG_CARDS; avoid orphan buffer + noop notify
        if not any(c.name == name for c in self._state.stream_cards):
            self._stream_buffers.pop(name, None)
            return
        new_cards = tuple(
            replace(c, reasoning=buf["reasoning"], content=buf["content"], is_analyzing=False) if c.name == name else c
            for c in self._state.stream_cards
        )
        self._set_state(stream_cards=new_cards)
        buf["last_flush"] = time.time()
        buf["pending"] = False

    def _on_stream_start_adapter(self, name: str) -> Callable:
        """Adapter for strategy's on_stream_start contract (returns on_chunk closure)."""
        self.start_stream_card(name, is_analyzing=False)

        def _on_chunk(chunk_text: str, is_reasoning: bool = False) -> None:
            self.append_stream_chunk(name, chunk_text, is_reasoning)

        _on_chunk.final_flush = lambda: self.finalize_stream_card(name)  # type: ignore[attr-defined]  # [reason: ai_mixin.py:576 用 hasattr 检查 final_flush]
        return _on_chunk

    def _on_card_start_adapter(self, name: str) -> None:
        """Adapter for strategy's on_card_start contract."""
        self.start_stream_card(name, is_analyzing=True)

    # --- AI Streaming Handlers ---

    def _on_ai_progress(self, current, total, msg):
        # Pass through status update
        self._set_state(
            status_message=Message(
                "screener_ai_analyzing",
                {"done": current, "total": total, "msg": msg},
            ),
            status_color="info",
        )

    def _on_ai_result_stream(self, row_data):
        """Buffer incoming AI results and update in batches"""
        if not row_data:
            return

        # 1. Update Log immediately (append-only tuple in state, §3.2 H5)
        name = row_data.get("name", "Unknown")
        score = row_data.get("ai_score", 0)
        thinking = str(row_data.get("thinking", ""))
        entry = LogEntry(name=name, score=score, thinking=thinking)
        self._state = replace(self._state, logs=self._state.logs + (entry,))
        self._notify()

        # 2. Buffer for Table Update
        self._ai_buffer.append(row_data)

        now = time.time()
        if now - self._last_ai_update > self.AI_UPDATE_INTERVAL or len(self._ai_buffer) >= 20:
            # Trigger Batch Update
            # Note: We trigger a task to run the update on main thread context eventually,
            # but here we are likely in a background thread from AI Strategy?
            # Actually AI Strategy runs awaitable, so we are in async context.
            # We can't await here directly if this is called synchronously.
            # But on_result is usually called from async loop.

            # Schedule update if not already pending
            if not self._flush_pending:
                self._flush_pending = True
                try:
                    loop = asyncio.get_running_loop()
                    if not self._main_loop:
                        self._main_loop = loop
                    task = loop.create_task(self._flush_ai_buffer())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._on_background_task_done)
                except RuntimeError:
                    if self._main_loop and self._main_loop.is_running():
                        future = asyncio.run_coroutine_threadsafe(
                            self._flush_ai_buffer(),
                            self._main_loop,
                        )
                        self._threadsafe_futures.add(future)
                        future.add_done_callback(lambda f: self._threadsafe_futures.discard(f))
                    else:
                        self._flush_pending = False
                        logger.error("Cannot schedule flush: No event loop available")

    async def _flush_ai_buffer(self):
        """Flush buffer to main DataFrame"""
        try:
            if not self._ai_buffer:
                return
            # U-3 fix: Race guard - save buffer to discarded_buffer if user has switched to history mode
            if self._state.mode != "REALTIME":
                self._discarded_buffer.extend(self._ai_buffer)
                self._ai_buffer = []
                self._flush_pending = False
                logger.debug(
                    "[ScreenerVM] Saved %s items to discarded_buffer during HISTORY mode",
                    len(self._discarded_buffer),
                )
                return

            # Swap buffer to process safely
            current_batch = self._ai_buffer
            self._ai_buffer = []

            new_df = pd.DataFrame(current_batch)

            # Offload Concatenation
            if self._full_results is None or self._full_results.empty:
                self._full_results = new_df
            else:
                # Append
                self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    pd.concat,
                    [self._full_results, new_df],
                    ignore_index=True,
                )

            # Sort by Score (Best on top)
            if "ai_score" in self._full_results.columns:
                self._full_results = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._sort_helper,
                    self._full_results,
                    "ai_score",
                    False,
                )

                # Pin ai_score and ai_reason to the front (after name)
                # Ensure ai_reason column exists (some AI results may only return score)
                if "ai_reason" not in self._full_results.columns:
                    self._full_results["ai_reason"] = ""
                cols = list(self._full_results.columns)  # type: ignore[untyped]
                # Remove if exists
                if "ai_score" in cols:
                    cols.remove("ai_score")
                if "ai_reason" in cols:
                    cols.remove("ai_reason")

                # Find insertion index (after 'name', or else at idx 1)
                insert_idx = cols.index("name") + 1 if "name" in cols else 1

                # Insert back
                cols.insert(insert_idx, "ai_score")
                cols.insert(insert_idx + 1, "ai_reason")

                self._full_results = self._full_results[cols]  # type: ignore[untyped]
            self._update_pagination()
            self._set_state(data_version=self._state.data_version + 1)

            self._last_ai_update = time.time()

        except Exception as e:
            logger.error("Error flushing AI buffer: %s", e, exc_info=True)
        finally:
            self._flush_pending = False

    # --- History Mode ---

    def switch_to_history(self):
        """Switch to HISTORY mode, snapshot current realtime state."""
        if self._state.mode == "HISTORY":
            return
        # Snapshot realtime state
        self._realtime_snapshot = {
            "full_results": self._full_results,
            "page_no": self._state.page_no,
            "sort_column": self._state.sort_column,
            "sort_ascending": self._state.sort_ascending,
            "ai_buffer": self._ai_buffer[:],
            "stream_cards": self._state.stream_cards,
            "stream_buffers": dict(self._stream_buffers),
        }
        # Clear for history data
        self._full_results = None
        self._ai_buffer = []
        self._stream_buffers.clear()
        # _update_pagination only updates pagination fields; sort_* are set in _set_state below.
        self._update_pagination(page_no=1)
        # Task 3.2: 重置 history_tree state (消除 View 双轨状态, View 不再 set_history_tree_*)
        self._set_state(
            mode="HISTORY",
            page_no=1,
            sort_column=None,
            sort_ascending=True,
            stream_cards=(),
            data_version=self._state.data_version + 1,
            history_tree=HistoryTreeState(),
        )
        logger.info("[ScreenerVM] Switched to HISTORY mode")

    def switch_to_realtime(self):
        """Switch back to REALTIME mode, restore snapshot."""
        if self._state.mode == "REALTIME":
            return
        # Restore snapshot
        if self._realtime_snapshot:
            self._full_results = self._realtime_snapshot["full_results"]
            pn = self._realtime_snapshot["page_no"]
            sc = self._realtime_snapshot["sort_column"]
            sa = self._realtime_snapshot["sort_ascending"]
            self._ai_buffer = self._realtime_snapshot["ai_buffer"]
            stream_cards = self._realtime_snapshot.get("stream_cards", ())
            self._stream_buffers = self._realtime_snapshot.get("stream_buffers", {})
            self._realtime_snapshot = None
            # U-3 fix: Merge discarded_buffer back to ai_buffer
            if self._discarded_buffer:
                self._ai_buffer.extend(self._discarded_buffer)
                logger.debug("[ScreenerVM] Merged %s discarded items back to ai_buffer", len(self._discarded_buffer))
                self._discarded_buffer = []
            self._update_pagination(page_no=pn)
            self._set_state(
                mode="REALTIME",
                page_no=pn,
                sort_column=sc,
                sort_ascending=sa,
                stream_cards=stream_cards,
                data_version=self._state.data_version + 1,
            )
        else:
            self._set_state(mode="REALTIME")
        logger.info("[ScreenerVM] Switched to REALTIME mode")

    async def load_history_tree(self, append: bool = False) -> None:
        """加载历史树数据并更新 state.history_tree (Task 3.2: 不再返回 dict).

        Args:
            append: True 追加到现有 rows (load_more 路径); False 重置 rows (切换模式/初始加载).
        """
        cache = CacheManager()
        offset = self._state.history_tree.offset if append else 0
        df = await cache.get_history_tree(offset=offset)
        if df is None or df.empty:
            if not append:
                # 重置 rows (切换到 HISTORY 模式后无数据)
                self._set_state(
                    history_tree=replace(
                        self._state.history_tree,
                        rows=(),
                        offset=0,
                        has_more=False,
                    )
                )
            else:
                # append 路径下无更多数据, 仅隐藏 load_more
                self._set_state(history_tree=replace(self._state.history_tree, has_more=False))
            return

        new_rows = self._build_history_tree_rows(df)
        if append:
            merged_rows = self._state.history_tree.rows + new_rows
        else:
            merged_rows = new_rows
        self._set_state(
            history_tree=replace(
                self._state.history_tree,
                rows=merged_rows,
                offset=offset + len(df) * 5,
                has_more=len(df) >= 5,
            )
        )

    @staticmethod
    def _build_history_tree_rows(df: pd.DataFrame) -> tuple[HistoryTreeRow, ...]:
        """从 DataFrame 构建历史树行 (不依赖 I18n, 日期格式化内聚到 VM).

        策略名 strategy_name 为 raw key, View 渲染时调 translate_strategy_name 翻译 (§3.2).
        """
        # Group by trade_date -> {date: [{run_id, strategy_name, cnt}, ...]}
        tree: dict[str, list[dict]] = {}
        for _, row in df.iterrows():
            date = str(row["trade_date"])
            tree.setdefault(date, []).append(
                {
                    "run_id": row["run_id"],
                    "strategy_name": row["strategy_name"],
                    "cnt": int(row["cnt"]),
                }
            )
        rows: list[HistoryTreeRow] = []
        for date_str, strategies in tree.items():
            display_date, d_key = ScreenerViewModel._format_history_date(date_str)
            total_cnt = sum(s["cnt"] for s in strategies)
            rows.append(
                HistoryTreeRow(
                    display_date=display_date,
                    d_key=d_key,
                    total_cnt=total_cnt,
                    strategies=tuple(strategies),
                )
            )
        return tuple(rows)

    @staticmethod
    def _format_history_date(date_str) -> tuple[str, str]:
        """格式化历史树日期: 返回 (display_date, internal_key).

        纯函数不依赖 I18n, 与 View 中同名函数保持一致行为 (Task 3.2 内聚到 VM).
        """
        if isinstance(date_str, (datetime.date, datetime.datetime)):
            display = date_str.strftime("%Y-%m-%d")
            key = display
        else:
            s = str(date_str)
            display = f"{s[:4]}-{s[4:6]}-{s[6:]}" if len(s) == 8 and s.isdigit() else s
            key = s
        return display, key

    async def load_history_data(self, trade_date: str, strategy_name: str | None = None, run_id: str | None = None):  # type: ignore[untyped]
        """Load historical screening records for a specific run_id, or fall back to trade_date/strategy_name.

        Task 3.2: VM 内聚 loading 管理 (View 不再 set_progress_visible).
        """
        self._set_state(loading=True)
        try:
            cache = CacheManager()
            df = await cache.get_history_records(trade_date, strategy_name, run_id)
            if df is not None and not df.empty:
                self._full_results = df
            else:
                self._full_results = pd.DataFrame()
            if df is not None and not df.empty and "ai_score" in df.columns:
                sort_column = "ai_score"
            else:
                sort_column = None
            self._update_pagination(page_no=1)
            self._set_state(
                page_no=1,
                loading=False,
                sort_column=sort_column,
                sort_ascending=False,
                data_version=self._state.data_version + 1,
            )
        except asyncio.CancelledError:
            self._set_state(loading=False)
            raise
        except Exception:
            self._set_state(loading=False)
            raise

    def get_export_data(self):
        """Get the current results DataFrame for export"""
        if self._full_results is None or self._full_results.empty:
            return None
        return self._full_results

    async def export_results(self, filepath):
        """Export current results to CSV at the specified path"""
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_csv,
                filepath,
                index=False,
                encoding="utf-8-sig",
            )
            return filepath, None
        except Exception as e:
            logger.error("Export failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export failed traceback", exc_info=True)
            return None, str(e)

    async def export_results_excel(self, filepath: str) -> tuple[str | None, str | None]:
        """Export current results to Excel (.xlsx) at the specified path.

        与 ``export_results`` 结构对齐: 通过 ``ThreadPoolManager.run_async(TaskType.CPU, ...)``
        offload CPU 密集的 ``df.to_excel`` 调用 (R16). ``asyncio.CancelledError`` 为
        BaseException, 不被 ``except Exception`` 捕获, 自动传播 (R2 与 ``export_results`` 一致).
        """
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            await ThreadPoolManager().run_async(
                TaskType.CPU,
                self._full_results.to_excel,
                filepath,
                index=False,
                engine="openpyxl",
            )
            return filepath, None
        except Exception as e:
            logger.error("Export Excel failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export Excel failed traceback", exc_info=True)
            return None, str(e)

    # --- TaskManager Subscription ---

    def subscribe_task_manager(self):
        """Subscribe to TaskManager for strategy task monitoring."""
        TaskManager().subscribe(self._on_tasks_updated)

    def unsubscribe_task_manager(self):
        """Unsubscribe from TaskManager."""
        TaskManager().unsubscribe(self._on_tasks_updated)

    def _on_tasks_updated(self, tasks: list):
        """TaskManager subscriber: detect strategy task completion and notify View."""
        # Task 3.1: 改用 task_type.key 检测策略任务 (替代旧 TASK_NAME_PREFIX in t.name).
        # 因 t.name 现为 Message 实例, 不支持 `in` 操作; task_type 也是 Message,
        # 其 key 为 "task_type_ai_screening" 标识本 VM 提交的筛选任务.
        running = [
            t
            for t in tasks
            if isinstance(t.task_type, Message)
            and t.task_type.key == "task_type_ai_screening"
            and t.status.name in ("RUNNING", "QUEUED")
        ]
        if not running and self._strategy_submitted:
            self._strategy_submitted = False
            self._set_state(task_unlocked=True)
