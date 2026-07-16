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

import asyncio
import datetime
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from core.i18n import Message
from services.task_manager import AppTask, TaskManager, TaskStatus

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


class TaskCenterViewModel:
    """ViewModel for TaskCenterView.

    MVVM + declarative rendering paradigm (CLAUDE.md §3.2):
    - Immutable state snapshot (TaskCenterState) via subscribe/_notify
    - Commands as instance methods (stable references)
    """

    def __init__(self):
        self._task_manager = TaskManager()
        self._state = TaskCenterState()
        self._subscribers: list[Callable[[TaskCenterState], None]] = []
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # Populate initial state synchronously from TaskManager
        self._refresh_from_tasks(self._task_manager.get_all_tasks())
        # Subscribe for future updates
        self._task_manager.subscribe(self._on_tasks_updated)

    # --- State snapshot + subscribe/_notify ---

    @property
    def state(self) -> TaskCenterState:
        """View 只读 state snapshot，不可变。"""
        return self._state

    def subscribe(self, callback: Callable[[TaskCenterState], None]) -> Callable[[], None]:
        """订阅 state 变化，返回退订函数。同时捕获 main loop（hook 在主循环注册）。"""
        self._subscribers.append(callback)
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[TaskCenterVM] subscribed without running loop (test mode)")

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        """state 变化后调所有订阅者，传入新 snapshot。"""
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        """Update state fields and notify subscribers."""
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        """Cleanup resources."""
        self._task_manager.unsubscribe(self._on_tasks_updated)
        self._subscribers.clear()

    # --- TaskManager callback ---

    def _on_tasks_updated(self, tasks: list[AppTask]) -> None:
        """TaskManager subscriber (called from TM thread).

        Schedule state update on main loop if available; else synchronous (test mode).
        """
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.call_soon_threadsafe(self._refresh_from_tasks, tasks)
        else:
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
