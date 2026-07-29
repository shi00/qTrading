"""NavBadgeViewModel — 导航栏任务运行中角标 ViewModel (Phase 6.1, FR-UX-006).

声明式渲染范式 (CLAUDE.md §3.2 MVVM):
- 不可变 ``NavBadgeState`` (frozen dataclass, 仅 ``running_count`` 字段)
- ``subscribe``/``_notify`` 通知机制 (hook 通过 ``use_viewmodel`` 订阅)
- ``TaskManager.subscribe`` 驱动 state 更新

线程模型 (对齐 ``TaskCenterViewModel``):
- TaskManager 回调可能来自后台线程
- ``subscribe()`` 捕获 main loop, ``_on_tasks_updated`` 通过 ``call_soon_threadsafe``
  将 state 更新调度到主循环
- 无运行循环时 (单测) 退化为同步执行

设计权衡 (YAGNI):
- 不合并到 ``TaskCenterViewModel``: ``TaskCenterView`` 仅在 tasks tab 激活时挂载,
  而 nav badge 需要在所有 tab 持续显示 running_count, 生命周期独立
- 不缓存 running_count 到 AppLayout use_state: 需要 TaskManager 订阅驱动,
  独立 VM 封装订阅 + state 更新, 避免在 AppLayout 写命令式订阅代码
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NavBadgeState:
    """导航栏角标的不可变 state snapshot。"""

    running_count: int = 0


class NavBadgeViewModel(ObservableViewModelMixin[NavBadgeState]):
    """``nav_tasks`` 导航项的角标 ViewModel (FR-UX-006).

    跟踪 TaskManager 中 RUNNING 状态任务数, 触发 nav_tasks 角标重渲染。
    View 通过 ``use_viewmodel(NavBadgeViewModel)`` 消费 state, 当
    ``running_count > 0`` 时在 nav_tasks icon 上叠加数字角标。
    """

    def __init__(self):
        self._task_manager = TaskManager()
        self._state = NavBadgeState()
        self._subscribers: list[Callable[[NavBadgeState], None]] = []
        self._main_loop: asyncio.AbstractEventLoop | None = None
        # 初次同步获取当前状态
        self._refresh_from_tasks(self._task_manager.get_all_tasks())
        # 订阅未来更新
        self._task_manager.subscribe(self._on_tasks_updated)

    def subscribe(self, callback: Callable[[NavBadgeState], None]) -> Callable[[], None]:
        """订阅 state 变化, 返回退订函数。同时捕获 main loop。"""
        self._subscribers.append(callback)
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.debug("[NavBadgeVM] subscribed without running loop (test mode)")

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def dispose(self) -> None:
        """清理资源: 退订 TaskManager + 清空订阅者。"""
        self._task_manager.unsubscribe(self._on_tasks_updated)
        self._subscribers.clear()

    def _on_tasks_updated(self, tasks: list[AppTask]) -> None:
        """TaskManager subscriber (called from TM thread).

        Schedule state update on main loop if available; else synchronous (test mode).
        """
        if self._main_loop and self._main_loop.is_running():
            self._main_loop.call_soon_threadsafe(self._refresh_from_tasks, tasks)
        else:
            self._refresh_from_tasks(tasks)

    def _refresh_from_tasks(self, tasks: list[AppTask]) -> None:
        """从 task list 计算 running_count, 仅在变化时更新 state + 通知。"""
        running_count = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        if running_count != self._state.running_count:
            self._state = replace(self._state, running_count=running_count)
            self._notify()
