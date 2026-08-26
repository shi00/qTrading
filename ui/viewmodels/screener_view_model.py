import asyncio
import datetime
import inspect
import io
import logging
import time
import typing
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

import pandas as pd

from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local

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
    error: str | None = None  # UX-2.3: 失败时终结为错误状态（含重试按钮）


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
    # Task 5.2: 质量门阻断时的跳转 action key (如 screener_action_go_sync), None 无 action
    status_action_key: str | None = None
    # AI streaming logs (append-only tuple)
    logs: tuple[LogEntry, ...] = ()
    # AI streaming/placeholder cards (state-driven, §3.2 MVVM)
    stream_cards: tuple[StreamCard, ...] = ()
    # Task 8.4: 卡片截断提示 — True 表示已有卡片被 _MAX_LOG_CARDS 截断
    stream_cards_truncated: bool = False
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
    # P3-ScreenerVM-I18n-Get-Residual: 改为 Message 结构 (desc_key + params),
    # View 渲染时翻译 (§3.2 VM 不感知 locale).
    strategy_desc: Message | None = None
    strategy_desc_color: str = "default"  # 语义标识符: "default"/"warning"
    # History tree (Task 3.2: 子结构内聚 rows/offset/has_more/loading, 消除 View 双轨状态)
    history_tree: HistoryTreeState = field(default_factory=HistoryTreeState)
    # UX-2.3: 重试中标志，View 派生 run_disabled 禁用主运行按钮
    is_retrying: bool = False
    # UX-04 (P2-01): 股票代码过滤 (ts_code 子串匹配, 空串=不过滤; 深链/手动输入两来源)
    stock_filter: str = ""


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
        # data_processor 懒构造：DataProcessor.__init__ 同步初始化 TushareClient
        # （40+ API rate_limiter，耗时 34s+），构造期同步会阻塞 Flet 主线程 (R16)。
        # 首次筛选（_execute_screening）经 _ensure_processor() 在 IO 线程池异步构造。
        self.data_processor: DataProcessor | None = None
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
        self._active_task_id: str | None = None  # Task 3.2: 保存运行中 task_id 供 cancel_strategy
        # UX-2.3: 单株重试状态（实例属性，不进 state）
        self._last_ai_context: dict | None = None
        self._last_strategy_key: str | None = None
        self._retrying = False
        # 当前重试的股票名与重试前的错误文案（P1-1: select_strategy 取消重试时终结占位卡用）
        self._retrying_name: str | None = None
        self._retrying_prev_error: str | None = None
        # 当前重试 task 引用（schedule_retry 记录，select_strategy 仅取消它，避免误cancel其它后台任务）
        self._retry_task: asyncio.Task | None = None

        # Mixin 字段初始化（跨线程修复）
        self._init_mixin_fields()

    async def _ensure_processor(self) -> DataProcessor:
        """懒构造 DataProcessor（IO 线程池 offload），避免阻塞 UI 主线程 (R16)。

        双检 + loop-local 锁防并发双检竞态 (R11)。key 带 VM 前缀避免跨实例共享锁。
        View 侧 `vm.data_processor` sync 访问可能为 None（StockDetailDialog 已有
        None 保护）；首次筛选后此属性即非 None。
        """
        if self.data_processor is None:
            lock = get_loop_local("screener_vm_processor_lock", asyncio.Lock)
            async with lock:
                if self.data_processor is None:
                    self.data_processor = await ThreadPoolManager().run_async(TaskType.IO, DataProcessor)
        return self.data_processor

    # --- State snapshot + subscribe/_notify (§3.0.1) ---

    def _set_state(self, **changes) -> None:
        """Update state fields and notify subscribers."""
        # disposed guard 保留（与 Mixin 的 disposed guard 冗余但不报错，保留作为短路优化）
        if self._disposed:
            return
        super()._set_state(**changes)

    def _update_pagination(self, page_size: int | None = None, page_no: int | None = None) -> None:
        """Recompute pagination fields in state, then notify via _set_state.

        不再由 caller 手动 _notify()：走 Mixin._set_state 统一路径（disposed guard +
        跨线程封送 + subscribers snapshot）。
        """
        ps = page_size if page_size is not None else self._state.page_size
        filtered = self._get_filtered_results()
        if filtered is not None:
            total_items = len(filtered)
            total_pages = (total_items + ps - 1) // ps
        else:
            total_items = 0
            total_pages = 0
        pn = page_no if page_no is not None else self._state.page_no
        # UX-04: 页码 clamp — 过滤/模式切换缩小 total_pages 后, 恢复的历史 page_no
        # 可能越界 (HISTORY 中修改过滤后 switch_to_realtime 恢复快照页码 → 空表格)
        pn = max(1, min(pn, total_pages)) if total_pages else 1
        self._set_state(
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
        self._stream_buffers.clear()

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

        # UX-2.3 v4 P2-2: 清空 retry 相关字段，避免 disposed 后残留状态
        self._last_ai_context = None
        self._last_strategy_key = None
        self._retrying = False
        self._retrying_name = None
        self._retrying_prev_error = None
        self._retry_task = None

        self._full_results = None
        self._ai_buffer = []
        self._realtime_snapshot = None
        self._state = ScreenerState()

        # Mixin 统一清理: subscribers / _main_loop / pending handle / deque
        super().dispose()

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
            logger.error("[ScreenerVM] Background task failed: %s", DataSanitizer.sanitize_error(exc), exc_info=exc)

    # --- Splitter width persistence (P1-1/P2-1: View 不再直接 import ConfigHandler) ---

    def get_splitter_width(self, config_key: str, default_width: int) -> int:
        """读取持久化的 splitter 宽度 (P1-1: 经 VM 读取, View 不再直接 import ConfigHandler).

        ConfigHandler._config_cache 命中是纯内存读 (非 IO); 首次未命中触发小 JSON
        文件读 (单次 < 5ms), 在 use_effect 上下文中可接受。返回值由 ResizableSplitter
        内部 clamp 到 [min_width, max_width]。
        """

        return ConfigHandler.get_typed(config_key, int, default_width)

    def persist_splitter_width(self, config_key: str, width: int) -> None:
        """持久化 splitter 宽度 (P1-1/P2-1: 异步写盘, R16 合规). fire-and-forget.

        同步签名以满足 ResizableSplitter ``on_persist_width`` 回调契约; 内部经
        ThreadPoolManager.run_async 提交 IO 写盘, 不阻塞 Flet 事件处理器。
        复用 _background_tasks + _on_background_task_done 跟踪 task 生命周期。
        """

        async def _persist() -> None:
            try:
                await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_typed, config_key, width)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.debug(
                    "[ScreenerVM] persist_splitter_width failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
                )

        loop = self._get_loop_or_none()
        if loop is None:
            return  # 无事件循环 (测试环境/已 disposed), 静默跳过
        task = loop.create_task(_persist())
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    # --- Data Actions ---

    async def get_strategies(self) -> dict[str, str]:
        return self.strategy_mgr.get_all_names()

    def get_strategy_desc(self, key: str) -> Message | None:
        """获取策略描述的 Message (i18n key + params), VM 不感知 locale (§3.2).

        View 渲染时通过翻译 ``msg.key`` + ``msg.params`` 为当前 locale 字符串.
        """
        st = self.strategy_mgr.get_strategy(key)
        return Message(st.desc_key, {}) if st else None

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

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_strategy_prompt, strategy_key, prompt)
        return True, None

    # --- Task 4.1: 筛选方案保存/复用 (FR-UX-003) ---

    def get_preset_names(self, strategy_key: str) -> list[str]:
        """获取策略已保存的预设名称列表 (Task 4.1).

        ConfigHandler._config_cache 命中时为纯内存读 (非 IO); 首次未命中
        触发小 JSON 文件读 (< 5ms), 在 use_effect 上下文中可接受。
        """

        presets = ConfigHandler.get_strategy_presets(strategy_key)
        return list(presets.keys())

    async def save_preset(self, name: str, strategy_key: str, params: dict) -> None:
        """保存命名参数预设 (Task 4.1). 重名覆盖.

        Raises:
            Exception: ConfigHandler 失败时抛出 (View 负责展示错误)
        """

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.save_strategy_preset, strategy_key, name, params)

    def load_preset(self, name: str, strategy_key: str) -> dict:
        """载入命名参数预设 (Task 4.1).

        Returns:
            参数 dict; 预设不存在时返回空 dict.
        """

        presets = ConfigHandler.get_strategy_presets(strategy_key)
        return presets.get(name, {})

    async def delete_preset(self, name: str, strategy_key: str) -> bool:
        """删除命名参数预设 (Task 4.1).

        Returns:
            bool: True 表示已删除, False 表示预设不存在.
        """

        return bool(
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.delete_strategy_preset, strategy_key, name)
        )

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
        # UX-2.3 v4 P0-2: 重试中切换策略 → 只取消重试 task（不取消 persist_splitter_width / _flush_ai_buffer 等其它后台任务）
        if self._retrying:
            if self._retry_task is not None and not self._retry_task.done():
                self._retry_task.cancel()
            self._retry_task = None
            self._retrying = False
            # P1-1: 取消重试后终结占位卡（否则 is_analyzing=True 卡永久停留在"分析中"旋转假死）。
            # 仅当确有重试中的占位卡名时还原为错误态，后续 on_result/on_card_error 均不会再来。
            # 还原重试前的原始错误文案（VM 不感知 locale，§3.2），不调用 I18n。
            if self._retrying_name:
                self._on_card_error(self._retrying_name, self._retrying_prev_error or "AI 分析未完成")
            self._retrying_name = None
            self._retrying_prev_error = None
            # 清空重试上下文（防止 retry_single 完成后回调污染新策略）
            self._last_ai_context = None
            self._last_strategy_key = None
        tier_hint = self._compute_tier_hint(key)
        self._set_state(selected_strategy=key, tier_hint=tier_hint, is_retrying=False)

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
            logger.error("[ScreenerVM] Failed to load strategies: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            self._set_state(
                status_message=Message("screener_load_failed", {}),
                status_color="error",
                status_action_key=None,
            )

    def update_strategy_desc(self, selected_strategy: str | None, params: dict | None = None) -> None:
        """更新策略描述 Message 和颜色到 state (R.2.6.2: 业务状态迁入 VM).

        构造 ``state.strategy_desc`` 为 ``Message`` (desc_key + params), VM 不感知 locale
        (§3.2). View 渲染时通过翻译 ``msg.key`` + ``msg.params`` 为当前 locale 字符串.

        当 ``dep_info.missing_apis`` 非空时, 在 params 中追加 ``missing_apis`` 字段
        (逗号分隔字符串) 并设 ``strategy_desc_color="warning"``; View 渲染时识别该字段
        追加 ``strategy_missing_apis`` 翻译后缀.

        Args:
            selected_strategy: 策略 key, None 表示清空
            params: 动态参数 (可选, 用于 get_dynamic_description; None 时用策略默认参数)
        """
        if not selected_strategy:
            self._set_state(strategy_desc=None, strategy_desc_color="default")
            return

        try:
            strategy_obj = self.strategy_mgr.get_strategy(selected_strategy)
            strategies_with_dep = self.strategy_mgr.get_all_with_dependencies()
            dep_info = strategies_with_dep.get(selected_strategy, {})

            if strategy_obj:
                if params is None:
                    params = {p["name"]: p.get("default") for p in strategy_obj.get_parameters()}
                desc_msg = strategy_obj.get_dynamic_description(params)
            else:
                desc_msg = self.get_strategy_desc(selected_strategy)

            if desc_msg is None:
                self._set_state(strategy_desc=None, strategy_desc_color="default")
                return

            # missing_apis 非空时: params 追加 missing_apis 字段, color=warning
            # View 渲染时识别 missing_apis 字段追加 "strategy_missing_apis" 翻译后缀
            missing_apis = dep_info.get("missing_apis")
            if missing_apis:
                merged_params = dict(desc_msg.params)
                merged_params["missing_apis"] = ", ".join(missing_apis)
                desc_msg = Message(desc_msg.key, merged_params)
                color = "warning"
            else:
                color = "default"

            self._set_state(strategy_desc=desc_msg, strategy_desc_color=color)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                "[ScreenerVM] update_strategy_desc failed: %s", DataSanitizer.sanitize_error(e), exc_info=True
            )
            self._set_state(strategy_desc=None, strategy_desc_color="default")

    def set_history_viewing_status(
        self,
        date_str: str,
        strategy_name: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """设置历史查看状态到 state (R.2.6.3: 业务状态迁入 VM).

        接收 raw ``strategy_name`` (i18n key) 和 ``run_id``, 构造 Message 存入 state.
        VM 不感知 locale (§3.2), View 渲染时通过 ``_render_status_message`` 翻译
        ``label_key`` 后缀 params 为当前 locale 字符串.

        Args:
            date_str: 已格式化的日期字符串 (如 "2024-12-27")
            strategy_name: raw 策略名 i18n key (如 "strategy_oversold_name"), None 表示无策略
            run_id: 运行 ID, 优先于 strategy_name 显示 (locale-independent, 直接存入 label)
        """
        if run_id:
            params: dict = {"date": date_str, "label": f"#{run_id[:8]}"}
        elif strategy_name:
            params = {"date": date_str, "label_key": strategy_name}
        else:
            params = {"date": date_str, "label_key": "screener_all_strategies"}
        self._set_state(
            status_message=Message("screener_history_viewing", params),
            status_color="info",
            status_action_key=None,
        )

    @staticmethod
    def _compute_tier_hint(selected_strategy: str | None) -> str | None:
        """检查策略档位是否足够，不足时返回 i18n key，否则 None。

        返回 i18n key（非翻译值），符合 §3.2 "VM 只产出 i18n key"。
        View 渲染时翻译 ``state.tier_hint``。
        """
        if not selected_strategy:
            return None
        try:
            from data.external.tushare_client import TushareClient
            from services.ai_service import get_strategy_min_tier

            current_tier = ConfigHandler.get_tushare_point_tier()
            min_tier = get_strategy_min_tier(selected_strategy)
            client = TushareClient()
            if client.get_tier_order(current_tier) < client.get_tier_order(min_tier):
                return "sys_strategy_tier_hint"
        except Exception as e:
            logger.debug("[ScreenerVM] tier hint check skipped: %s", DataSanitizer.sanitize_error(e), exc_info=True)
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
                status_action_key=None,
            )
            return

        # Define the inner coroutine for the task manager
        async def _execute_screening(task_id: str, **kwargs):
            try:
                # 1. Prepare Context (may trigger massive data load)
                # 首次筛选在此懒异步构造 DataProcessor（IO 线程池 offload，R16）
                dp = await self._ensure_processor()
                TaskManager().update_progress(
                    task_id,
                    0.05,
                    Message("task_loading_data"),
                )
                context = await dp.get_strategy_data()
                if not context:
                    TaskManager().update_progress(
                        task_id,
                        0.1,
                        Message("task_cache_empty_init"),
                    )
                    await dp.init_data()
                    context = await dp.get_strategy_data()

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
                            status_action_key=None,
                        )
                    else:
                        self._set_state(
                            status_message=Message("strategy_dep_degraded"),
                            status_color="warning",
                            status_action_key=None,
                        )

                context["data_processor"] = dp
                context["params"] = params or {}  # Dynamic strategy parameters from UI

                # Setup AI Callbacks
                # (Forward updates both to ViewModel local UI and Global TaskManager)
                def _combined_ai_progress(current, total, msg):
                    self._on_ai_progress(current, total, msg)  # For local View UI
                    # D7: msg 为 Message, 直接透传 (不再拼 "[c/t] " 前缀, 防 dataclass repr)
                    TaskManager().update_progress(
                        task_id,
                        current / total if total > 0 else 0,
                        msg,
                    )

                context["on_progress"] = _combined_ai_progress
                context["on_result"] = self._on_ai_result_stream
                context["on_stream_start"] = self._on_stream_start_adapter
                context["on_card_start"] = self._on_card_start_adapter
                # UX-2.3: 单股失败回调 + 策略 key 透传给 mixin
                context["on_card_error"] = self._on_card_error
                context["strategy_key"] = strategy_key
                self._last_ai_context = context
                self._last_strategy_key = strategy_key

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

                    # Task 3.3: save_results 失败不再落入 screener_exec_error.
                    # 结果已写入 _full_results 照常上屏, 状态栏提示「未保存：原因」.
                    # trade_date 缺失属于程序错误 (context 协议违规), 仍 raise.
                    save_failed_reason: str | None = None
                    if save_results:
                        analysis_trade_date = context.get("trade_date")
                        if not analysis_trade_date:
                            raise RuntimeError(
                                "Missing analysis trade_date in screening context; refusing to save results",
                            )
                        import uuid as _uuid

                        run_id = _uuid.uuid4().hex[:16]
                        try:
                            await self.review_mgr.save_results(
                                strategy.name_key,
                                result_df,
                                trade_date=analysis_trade_date,
                                run_id=run_id,
                                params_snapshot=params or {},
                            )
                        except Exception as save_err:
                            # 必须捕获 Exception 而非 BaseException, 保留 CancelledError
                            # 传播 (R2 红线).
                            logger.error(
                                "[ScreenerVM] save_results failed (results retained in memory): %s",
                                DataSanitizer.sanitize_error(save_err),
                                exc_info=True,
                            )
                            save_failed_reason = DataSanitizer.sanitize_error(save_err) or save_err.__class__.__name__

                    if save_failed_reason is not None:
                        self._set_state(
                            page_no=1,
                            loading=False,
                            status_message=Message(
                                "screener_done_unsaved",
                                {
                                    "count": len(result_df),
                                    "reason": save_failed_reason,
                                },
                            ),
                            status_color="warning",
                            status_action_key=None,
                            data_version=self._state.data_version + 1,
                        )
                    else:
                        self._set_state(
                            page_no=1,
                            loading=False,
                            status_message=Message(
                                "screener_done_saved",
                                {"count": len(result_df)},
                            ),
                            status_color="success",
                            status_action_key=None,
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
                    status_action_key=None,
                    data_version=self._state.data_version + 1,
                )
                return Message("screener_no_results")

            except asyncio.CancelledError:
                self._set_state(
                    loading=False,
                    status_message=Message("screener_cancelled"),
                    status_color="warning",
                    status_action_key=None,
                )
                raise
            except QualityGateError as e:
                logger.warning(
                    "[ScreenerVM] Strategy execution blocked by Quality Gate: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                self._set_state(
                    loading=False,
                    status_message=Message("screener_blocked", {"reason": DataSanitizer.sanitize_error(e)}),
                    status_color="warning",
                    # Task 5.2: 附 "前往同步" 跳转 action 供 View 渲染按钮
                    status_action_key="screener_action_go_sync",
                )
                return Message("screener_blocked", {"reason": DataSanitizer.sanitize_error(e)})
            except Exception as e:
                logger.error(
                    "[ScreenerVM] Strategy execution failed: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
                # Show generic user-friendly message, avoid raw traceback on UI
                self._set_state(
                    loading=False,
                    status_message=Message("screener_exec_error"),
                    status_color="error",
                    status_action_key=None,
                )
                raise RuntimeError(f"Strategy execution crashed: {DataSanitizer.sanitize_error(e)}") from e
            finally:
                self._active_task_id = None  # Task 3.2: 所有退出路径清空, 防止误取消已结束的 task

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
            # Task 5.2: 重置上一次 QualityGateError 残留的 action key
            status_action_key=None,
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
                status_action_key=None,
            )
        else:
            self._strategy_submitted = True
            self._active_task_id = task_id  # Task 3.2: 保存供 cancel_strategy

    def cancel_strategy(self) -> None:
        """Task 3.2: 取消正在运行的选股策略任务 (本页取消).

        线程安全: TaskManager.cancel_task 通过 call_soon_threadsafe 调度到事件循环,
        可在 Flet 同步 handler 中直接调用 (R16 不适用: 无 IO/CPU 阻塞).
        """
        if self._active_task_id is not None:
            TaskManager().cancel_task(self._active_task_id)

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
            logger.error("Sort failed: %s", DataSanitizer.sanitize_error(e), exc_info=True)
            self._set_state(
                loading=False,
                status_message=Message("screener_sort_failed"),
                status_color="error",
            )

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

    def clear_filters(self) -> None:
        """重置筛选/排序/分页/档位提示至默认值 (P1-3 #71).

        EmptyState 的 ``on_cta`` 回调调用本命令，清空当前筛选状态以便用户重新执行策略。
        不清除 ``_full_results`` (保留上次结果供用户参考); 重置 state 中的
        ``page_no`` / ``sort_column`` / ``sort_ascending`` / ``tier_hint`` /
        ``stock_filter`` 字段 (UX-04), 分页按全量重算保持状态自洽。
        """
        ps = self._state.page_size
        base = self._full_results
        total_items = len(base) if base is not None else 0
        total_pages = (total_items + ps - 1) // ps if total_items else 0
        self._set_state(
            page_no=1,
            sort_column=None,
            sort_ascending=True,
            tier_hint=None,
            stock_filter="",
            total_items=total_items,
            total_pages=total_pages,
        )

    # --- Stock code filter (UX-04 P2-01) ---

    def _get_filtered_results(self, stock_filter: str | None = None) -> pd.DataFrame | None:
        """UX-04: 应用股票代码过滤 — ts_code 子串匹配 (case-insensitive, 字面量).

        Args:
            stock_filter: 显式过滤值 (None 时读 ``state.stock_filter``)。
                匹配前 strip; 空串/列缺失时跳过过滤返回全量。
        """
        if self._full_results is None or self._full_results.empty:
            return self._full_results
        code = (self._state.stock_filter if stock_filter is None else stock_filter).strip()
        if not code or "ts_code" not in self._full_results.columns:
            return self._full_results
        # regex=False: ts_code 含 "." (如 000001.SZ), 字面量匹配防通配误命中
        mask = self._full_results["ts_code"].astype(str).str.contains(code, case=False, na=False, regex=False)
        return self._full_results[mask]

    def set_stock_filter(self, value: str) -> None:
        """UX-04: 设置股票代码过滤 (深链/手动输入), 回到第 1 页并重算分页.

        原值存储不 strip (受控 TextField 光标保护: state 与输入框内容一致,
        防重渲染重置 value 导致光标跳动); 匹配时 ``_get_filtered_results``
        内部 strip。
        """
        if value == self._state.stock_filter:
            return  # 幂等: 相同值不触发重渲染
        ps = self._state.page_size
        filtered = self._get_filtered_results(value)
        total_items = len(filtered) if filtered is not None else 0
        total_pages = (total_items + ps - 1) // ps if total_items else 0
        self._set_state(
            stock_filter=value,
            page_no=1,
            total_items=total_items,
            total_pages=total_pages,
            data_version=self._state.data_version + 1,
        )

    @property
    def has_export_data(self) -> bool:
        """UX-04: 全量结果非空判据 (导出按钮禁用用, 与过滤后 total_items 解耦)."""
        return self._full_results is not None and not self._full_results.empty

    def get_current_page_data(self):
        """Get data for current page (Synchronous, fast slicing)"""
        filtered = self._get_filtered_results()
        if filtered is None or filtered.empty:
            return pd.DataFrame()

        start = (self._state.page_no - 1) * self._state.page_size
        end = start + self._state.page_size
        # Slicing is fast enough for main thread
        return filtered.iloc[start:end]

    # --- Stream Card Management (state-driven, §3.2 MVVM) ---

    def clear_stream_cards(self) -> None:
        """Clear all stream cards and buffers (called on new run)."""
        self._stream_buffers.clear()
        self._set_state(stream_cards=(), stream_cards_truncated=False)

    def start_stream_card(self, name: str, is_analyzing: bool = False) -> None:
        """Create a new stream/placeholder card."""
        self._stream_buffers[name] = {"reasoning": "", "content": "", "last_flush": 0.0, "pending": False}
        card = StreamCard(name=name, is_analyzing=is_analyzing)
        # Task 8.4: 检测截断 — 新增卡片导致超出 _MAX_LOG_CARDS 时标记 truncated
        truncated = len(self._state.stream_cards) + 1 > _MAX_LOG_CARDS
        new_cards = (self._state.stream_cards + (card,))[-_MAX_LOG_CARDS:]
        self._set_state(
            stream_cards=new_cards,
            stream_cards_truncated=self._state.stream_cards_truncated or truncated,
        )

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

    def _on_card_error(self, name: str, error: str) -> None:
        """UX-2.3: 单股 AI 分析失败时终结占位卡为错误状态。"""
        new_cards = tuple(
            replace(c, error=error, is_analyzing=False) if c.name == name and c.is_analyzing else c
            for c in self._state.stream_cards
        )
        self._set_state(stream_cards=new_cards)

    async def retry_single_stock(self, name: str) -> None:
        """UX-2.3: 重试单股 AI 分析。

        流程：防抖检查 → 策略一致性检查 → 移除失败卡片 → 构建新 context → 调用策略 retry_single。
        """
        # 1. 防抖：重试中拒绝再次触发
        if self._retrying:
            logger.debug("[ScreenerVM] retry_single_stock: already retrying, skip")
            return
        # 2. 策略一致性检查：策略切换后 _last_candidates_df 已失效
        if self._last_strategy_key != self._state.selected_strategy:
            self._set_state(
                status_message=Message("ai_retry_strategy_changed"),
                status_color="warning",
            )
            return
        if not self._last_ai_context:
            return
        # R1-6: 策略能力检查前移到卡片转换之前，避免无 retry_single 时卡片卡在 is_analyzing
        strategy = self.strategy_mgr.get_strategy(self._state.selected_strategy)
        if not strategy or not hasattr(strategy, "retry_single"):
            logger.warning(
                "[ScreenerVM] retry_single_stock: strategy %s has no retry_single",
                self._state.selected_strategy,
            )
            return  # 卡片保持 error 状态，用户可切换策略或重试
        # UX-2.3 v4 P0-1: 将失败卡转为重试中占位卡（不删除，避免 on_card_error 找不到目标卡片）
        # 重试成功由 on_result 自然终结；重试失败由 on_card_error 重新标记 error
        # P1-1: 记录重试前的原始错误文案，供 select_strategy 取消重试时还原（VM 不感知 locale, §3.2）
        self._retrying_prev_error = next(
            (c.error for c in self._state.stream_cards if c.name == name and c.error), None
        )
        new_cards = tuple(
            replace(c, error=None, is_analyzing=True) if c.name == name and c.error else c
            for c in self._state.stream_cards
        )
        # UX-2.3: is_retrying 进 state，View 派生 run_disabled 禁用主运行按钮
        self._set_state(stream_cards=new_cards, is_retrying=True)
        self._retrying = True
        self._retrying_name = name  # P1-1: 记录重试中的占位卡名，供 select_strategy 取消时终结
        # 4. 构建新 context（替换 _task_id 和 on_progress，避免污染原批次）
        retry_context = dict(self._last_ai_context)
        retry_context["_task_id"] = None  # 不关联 TaskManager
        retry_context["on_progress"] = lambda c, t, m: None  # noop，不更新进度
        retry_context["strategy_key"] = self._last_strategy_key  # 透传给 retry_single
        # on_result / on_card_start / on_card_error 保持原回调（更新 StreamCard）
        # 5. 调用策略重试
        try:
            await strategy.retry_single(name, retry_context)
        except asyncio.CancelledError:
            raise  # R2 合规（占位卡终结由 select_strategy 取消路径处理）
        except Exception as e:
            logger.error("[ScreenerVM] retry_single_stock failed: %s", e, exc_info=True)
            # A-1: retry_single 抛异常时（含 try 块之前的异常）恢复卡片为 error 状态避免假死
            # _on_card_error 仅更新 is_analyzing=True 的卡片，成功路径不受影响
            self._on_card_error(name, DataSanitizer.sanitize_error(e))
            self._set_state(
                status_message=Message("ai_retry_failed"),
                status_color="error",
            )
        finally:
            self._retrying = False
            self._retrying_name = None
            self._set_state(is_retrying=False)

    def schedule_retry(self, name: str) -> None:
        """UX-2.3: 调度单股重试，task 加入 _background_tasks 跟踪（VM dispose 时自动取消）。

        同步签名以满足 Flet on_click 回调契约；内部经 loop.create_task 提交，
        task 加入 _background_tasks + _on_background_task_done 跟踪生命周期。
        """
        if self._retrying:
            return
        loop = self._get_loop_or_none()
        if loop is None:
            return  # 无事件循环（测试环境/已 disposed），静默跳过
        task = loop.create_task(self.retry_single_stock(name))
        self._retry_task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    # --- AI Streaming Handlers ---

    def _on_ai_progress(self, current, total, msg):
        # D7: msg 为 data 层 ai_mixin 的 Message (key+params), VM 只透传 key:
        # msg_key 走 *_key 约定由 View 翻译, 内层 params (done/total) 平铺供模板填入.
        params: dict[str, Any] = {"done": current, "total": total}
        if isinstance(msg, Message):
            params["msg_key"] = msg.key
            params.update(msg.params)
        else:  # 兜底: 非 Message 路径 (向后兼容)
            params["msg"] = msg
        self._set_state(
            status_message=Message("screener_ai_analyzing", params),
            status_color="info",
            status_action_key=None,
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
        new_logs = self._state.logs + (entry,)

        # Task 3.1: 终结并发模式占位卡 (concurrency>1, is_analyzing=True).
        # _on_card_start_adapter 在并发模式创建 is_analyzing=True 占位卡; 结果到达时
        # 写入最终 reasoning/content 并 is_analyzing=False, 避免占位卡假死.
        # 流式卡 (is_analyzing=False, concurrency=1) 不受影响: 其内容由 chunk 流式写入.
        content = str(row_data.get("ai_reason", ""))
        new_cards = tuple(
            replace(c, reasoning=thinking, content=content, is_analyzing=False)
            if c.name == name and c.is_analyzing
            else c
            for c in self._state.stream_cards
        )
        # 合并为单次 _set_state：2 次 state 写 + 1 次 notify → 1 次 replace + 1 次 _notify
        # (修复 Skeptic P2 #5：语义等价，notify 次数从 2 次潜在可能性收敛为 1 次)
        if new_cards != self._state.stream_cards:
            self._set_state(logs=new_logs, stream_cards=new_cards)
        else:
            self._set_state(logs=new_logs)

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
                # 统一通过 Mixin 的 lazy getter 获取 loop（架构 P1-2 修复）
                loop = self._get_loop_or_none()
                if loop is not None and loop.is_running():
                    task = loop.create_task(self._flush_ai_buffer())
                    self._background_tasks.add(task)
                    task.add_done_callback(self._on_background_task_done)
                else:
                    # 同步上下文（无 loop）场景：同步执行，不创建 task
                    # （通常发生在单测中）
                    self._flush_pending = False
                    logger.warning("[ScreenerVM] Cannot schedule flush: no running loop; sync flush skipped")

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
                if "ai_reason" not in typing.cast("pd.DataFrame", self._full_results).columns:
                    typing.cast("pd.DataFrame", self._full_results)["ai_reason"] = ""
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
            # B12: 先递增 data_version 再通知分页, 保证数据内容变更与版本号原子一致,
            # 避免 _update_pagination 的 render 在旧版本号下命中 View 侧陈旧表格 memo。
            self._set_state(data_version=self._state.data_version + 1)
            self._update_pagination()

            self._last_ai_update = time.time()

        except Exception as e:
            logger.error("Error flushing AI buffer: %s", DataSanitizer.sanitize_error(e), exc_info=True)
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
            # B12: 先递增 data_version 再通知分页, 保证数据内容变更与版本号原子一致,
            # 避免 _update_pagination 的 render 在旧版本号下命中 View 侧陈旧表格 memo。
            self._set_state(
                mode="REALTIME",
                # UX-04: 读回 clamp 后合法页码 — HISTORY 中可能已修改 stock_filter/
                # page_size 使快照 page_no 越界, _update_pagination 已钳制到合法范围
                page_no=self._state.page_no,
                sort_column=sc,
                sort_ascending=sa,
                stream_cards=stream_cards,
                data_version=self._state.data_version + 1,
            )
            self._update_pagination(page_no=pn)
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
                offset=offset + len(df),
                has_more=len(df) >= 30,
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
            # B12: 先递增 data_version 再通知分页, 保证数据内容变更与版本号原子一致,
            # 避免 _update_pagination 的 render 在旧版本号下命中 View 侧陈旧表格 memo。
            self._set_state(
                page_no=1,
                loading=False,
                sort_column=sort_column,
                sort_ascending=False,
                data_version=self._state.data_version + 1,
            )
            self._update_pagination(page_no=1)
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
            return None, DataSanitizer.sanitize_error(e)

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
            return None, DataSanitizer.sanitize_error(e)

    async def export_results_bytes(self, format_: str) -> tuple[bytes | None, str | None]:
        """Export current results to bytes (Web mode: browser download via ``src_bytes``).

        与 ``export_results``/``export_results_excel`` 结构对齐: 通过
        ``ThreadPoolManager.run_async(TaskType.CPU, ...)`` offload CPU 密集的序列化 (R16).
        View (Web 模式) 调用此方法获取 bytes, 传给 ``file_picker.save_file(src_bytes=...)``.

        Args:
            format_: "csv" 或 "xlsx"

        Returns:
            (bytes, None) 成功; (None, error_msg) 失败.
        """
        if self._full_results is None or self._full_results.empty:
            return None, "No data to export"

        try:
            if format_ == "csv":
                csv_str = await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._full_results.to_csv,
                    index=False,
                    encoding="utf-8-sig",
                )
                assert csv_str is not None
                return csv_str.encode("utf-8-sig"), None
            else:
                buf = io.BytesIO()
                await ThreadPoolManager().run_async(
                    TaskType.CPU,
                    self._full_results.to_excel,
                    buf,
                    index=False,
                    engine="openpyxl",
                )
                return buf.getvalue(), None
        except Exception as e:
            logger.error("Export bytes failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("Export bytes failed traceback", exc_info=True)
            return None, DataSanitizer.sanitize_error(e)

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
