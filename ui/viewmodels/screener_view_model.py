import asyncio
import logging
from collections.abc import Callable
from types import MappingProxyType

import pandas as pd

from data.data_processor import DataProcessor
from data.persistence.review_manager import ReviewManager
from services.task_manager import TaskManager
from strategies.all_strategies import StrategyManager
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

# C3 拆分（UIX-07）：原 1808 行/66 方法的单体 VM 按职责拆为 5 个 mixin +
# 共享类型模块 screener_types.py。本文件保留基座（跨职责编排/资源生命周期/异步基建）
# 与最终组合类 ``ScreenerViewModel``，并再导出全部公开状态类型，保持既有
# ``from ui.viewmodels.screener_view_model import X`` 接口不变。
from ui.viewmodels.ai_stream_mixin import AIStreamMixin
from ui.viewmodels.export_mixin import ExportMixin
from ui.viewmodels.history_mode_mixin import HistoryModeMixin
from ui.viewmodels.pagination_sorting_mixin import PaginationSortingMixin
from ui.viewmodels.screener_types import (
    HistoryTreeRow,  # re-export
    HistoryTreeState,  # re-export
    LogEntry,  # re-export
    RealtimeSnapshot,  # re-export
    ScreenerRow,  # re-export
    ScreenerState,  # re-export
    StrategyDepRow,  # re-export
    StrategyRunRow,  # re-export
    StreamCard,  # re-export
    _MAX_LOG_CARDS,  # re-export
)
from ui.viewmodels.strategy_meta_mixin import StrategyMetaMixin
from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

# 模块级常量（拆分后保留在组合文件，维持既有 import 接口。C2b 后 task 检测
# 改用 task_type.key，本常量仅由测试断言锁定历史值，不再参与字符串匹配。）
TASK_NAME_PREFIX = "strategy_screening"

__all__ = [
    # 组合类 + re-export 状态类型（维持旧 `from ...screener_view_model import X` 接口）
    "ScreenerViewModel",
    "ScreenerRow",
    "ScreenerState",
    "StreamCard",
    "HistoryTreeRow",
    "HistoryTreeState",
    "StrategyDepRow",
    "StrategyRunRow",
    "LogEntry",
    "RealtimeSnapshot",
    "TASK_NAME_PREFIX",
    "_MAX_LOG_CARDS",
]

logger = logging.getLogger(__name__)


class ScreenerViewModel(
    ObservableViewModelMixin[ScreenerState],
    PaginationSortingMixin,
    AIStreamMixin,
    HistoryModeMixin,
    StrategyMetaMixin,
    ExportMixin,
):
    """ViewModel for ScreenerView（C3 拆分后的组合类）。

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (ScreenerState) via subscribe/_notify
    - Commands as instance methods (stable references)
    - DataFrame held internally (双轨制); C2b: View 经 state.current_page_rows
      读当前页 locale-neutral 原始行, 切片由 _update_pagination 唯一切片 owner 产出

    职责拆分（C3-2 ~ C3-7）：
    - ``screener_types``: 共享 immutable dataclass/常量 (见 screener_types.py)
    - ``PaginationSortingMixin``: 分页唯一 owner + 排序 + stock 过滤
    - ``AIStreamMixin``: run_strategy 编排 + 流式卡片/AI缓冲/单股重试
    - ``HistoryModeMixin``: REALTIME/HISTORY 切换 + 历史树/历史记录加载
    - ``StrategyMetaMixin``: 策略查询/参数草稿/预设/描述/tier 提示
    - ``ExportMixin``: CSV/Excel/bytes 导出
    - 本类：跨职责编排（``__init__``/``dispose``/``subscribe_task_manager``）、
      异步基建（``_ensure_processor``/``_on_background_task_done``）、
      splitter 配置持久化、``_set_state`` 守卫、``_on_tasks_updated``。
    """

    def __init__(self):
        # Dependencies
        # data_processor 懒构造：DataProcessor.__init__ 同步初始化 TushareClient
        # （40+ API rate_limiter，耗时 34s+），构造期同步会阻塞 Flet 主线程 (R16)。
        # 首次筛选（_execute_screening）经 _ensure_processor() 在 IO 线程池异步构造。
        self.data_processor: DataProcessor | None = None
        self.strategy_mgr = StrategyManager()  # noqa: R16 - 持有注册单例引用（幂等工厂），用于策略注册表查询
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

        # History mode snapshot (internal, frozen dataclass — M12-017)
        self._realtime_snapshot: RealtimeSnapshot | None = None

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

    def _set_state(self, **changes) -> None:
        """Update state fields and notify subscribers."""
        # disposed guard 保留（与 Mixin 的 disposed guard 冗余但不报错，保留作为短路优化）
        if self._disposed:
            return
        if "strategy_params" in changes:
            # M12-017 根因护栏：state 边界统一强制只读，防内部/测试绕开命名
            # setter 以可变 dict 注入，破坏不可变快照契约。dict(mapping) 拷贝后只读包装。
            changes = {**changes, "strategy_params": MappingProxyType(dict(changes["strategy_params"]))}
        super()._set_state(**changes)

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
