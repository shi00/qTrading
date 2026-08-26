"""TaskCenterViewModel — TaskCenterView 的 ViewModel（CLAUDE.md §3.2 MVVM）。

声明式渲染范式：
- 不可变 state snapshot（TaskCenterState frozen dataclass）
- subscribe/_notify 通知机制（hook 通过 use_viewmodel 订阅）
- commands 作为实例方法（稳定引用，View 事件处理器直接调用）

线程模型：
- TaskManager 回调可能来自后台线程
- subscribe() 捕获 main loop，_on_tasks_updated 通过 call_soon_threadsafe
  将 state 更新调度到主循环，确保 set_state 在主循环执行
- 无运行循环时（单测）退化为同步执行
"""

import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from core.i18n import Message
from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)

PAGE_SIZE = 10  # Tasks per page


@dataclass(frozen=True)
class TaskRow:
    """不可变任务行数据（从 AppTask 转换，供 View 渲染）。

    Task 3.1: ``name``/``task_type``/``description`` 字段类型为 ``Message | str``,
    透传 AppTask 字段 (VM 不调 I18n.get, View 渲染时按 locale 翻译).
    """

    id: str
    name: Message | str
    task_type: Message | str
    description: Message | str
    status: TaskStatus
    progress: float
    cancellable: bool
    created_at: datetime.datetime
    error: str


@dataclass(frozen=True)
class TaskCenterState:
    """TaskCenterView 的不可变 state snapshot。

    tasks 存全量（用于统计），View 根据 current_page 自行切片渲染。
    """

    tasks: tuple[TaskRow, ...] = ()
    current_page: int = 1
    total_pages: int = 1
    total_count: int = 0
    running_count: int = 0


class TaskCenterViewModel(ObservableViewModelMixin[TaskCenterState]):
    """ViewModel for TaskCenterView.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (TaskCenterState) via subscribe/_notify
    - Commands as instance methods (stable references)
    """

    def __init__(self):
        self._task_manager = TaskManager()  # noqa: R16 - 持有注册单例引用（幂等工厂），用于任务中心编排
        self._state = TaskCenterState()
        self._subscribers: list[Callable[[TaskCenterState], None]] = []
        # Mixin 字段初始化（跨线程修复）- 不再单独维护 self._main_loop
        self._init_mixin_fields()
        # Populate initial state synchronously from TaskManager
        self._refresh_from_tasks(self._task_manager.get_all_tasks())
        # Subscribe for future updates
        self._task_manager.subscribe(self._on_tasks_updated)

    # --- State snapshot + subscribe/_notify ---
    # subscribe / dispose 终态方法从 Mixin 继承，不再自定义捕获 loop
    # （Mixin.subscribe 每次 subscribe 都刷新 _owner_tid + 尝试捕获 loop，更健壮）

    def dispose(self) -> None:
        """Cleanup resources."""
        self._task_manager.unsubscribe(self._on_tasks_updated)
        # Mixin 统一清理 subscribers / loop / pending handle / deque
        super().dispose()

    # --- TaskManager callback ---

    def _on_tasks_updated(self, tasks: list[AppTask]) -> None:
        """TaskManager subscriber (called from TM thread).

        不再手动 call_soon_threadsafe，直接调 _refresh_from_tasks；
        _refresh_from_tasks 末尾调用 Mixin._notify() / _set_state，
        Mixin 会自动判定跨线程并封送（架构统一双轨消除 P1-3）。
        """
        self._refresh_from_tasks(tasks)

    def _refresh_from_tasks(self, tasks: list[AppTask]) -> None:
        """Convert AppTask list to TaskRow tuple, recompute pagination, update state."""
        rows = tuple(
            TaskRow(
                id=t.id,
                name=t.name,
                task_type=t.task_type,
                description=t.description,
                status=t.status,
                progress=t.progress,
                cancellable=t.cancellable,
                created_at=t.created_at,
                error=t.error,
            )
            for t in tasks
        )
        total = len(rows)
        running = sum(1 for r in rows if r.status == TaskStatus.RUNNING)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        current_page = max(1, min(self._state.current_page, total_pages))
        self._state = replace(
            self._state,
            tasks=rows,
            total_count=total,
            running_count=running,
            total_pages=total_pages,
            current_page=current_page,
        )
        self._notify()

    # --- Pagination commands ---

    def go_prev(self) -> None:
        """Navigate to previous page if not on first."""
        if self._state.current_page > 1:
            self._set_state(current_page=self._state.current_page - 1)

    def go_next(self) -> None:
        """Navigate to next page if not on last."""
        if self._state.current_page < self._state.total_pages:
            self._set_state(current_page=self._state.current_page + 1)

    # --- Task commands ---

    def cancel_task(self, task_id: str) -> None:
        """Cancel a task via TaskManager."""
        self._task_manager.cancel_task(task_id)

    def clear_finished(self) -> None:
        """Clear finished tasks and reset to first page."""
        self._set_state(current_page=1)
        self._task_manager.clear_finished()

    def retry_task(self, task_id: str) -> None:
        """Retry a failed task via TaskManager (Phase 6.2, FR-UX-006).

        Re-submits the failed task with stored factory + kwargs. TaskManager
        handles validation (task must be FAILED + have stored factory).
        """
        self._task_manager.retry_task(task_id)
