"""NavBadgeViewModel — 导航栏任务运行中角标 ViewModel (Phase 6.1, FR-UX-006).

声明式渲染范式 (CLAUDE.md §3.2 MVVM):
- 不可变 ``NavBadgeState`` (frozen dataclass, 仅 ``running_count`` 字段)
- ``subscribe``/``_notify`` 通知机制 (hook 通过 ``use_viewmodel`` 订阅)
- ``TaskManager.subscribe`` 驱动 state 更新

线程模型 (对齐 ``TaskCenterViewModel``，P2 Segment 2 统一 Mixin):
- TaskManager 回调可能来自后台线程
- subscribe/dispose/_notify 为 Mixin 终态骨架方法，子类不再 override subscribe
- ``_on_tasks_updated`` 直接调 ``_refresh_from_tasks``，末尾 ``_notify`` 由
  Mixin 自动判定同/跨线程并封送（call_soon_threadsafe）
- 无运行循环时（单测）退化为同步执行

设计权衡 (YAGNI):
- 不合并到 ``TaskCenterViewModel``: ``TaskCenterView`` 仅在 tasks tab 激活时挂载,
  而 nav badge 需要在所有 tab 持续显示 running_count, 生命周期独立
- 不缓存 running_count 到 AppLayout use_state: 需要 TaskManager 订阅驱动,
  独立 VM 封装订阅 + state 更新, 避免在 AppLayout 写命令式订阅代码
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

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
        self._task_manager = TaskManager()  # noqa: R16 - 持有注册单例引用（幂等工厂），用于订阅任务状态
        self._state = NavBadgeState()
        self._subscribers: list[Callable[[NavBadgeState], None]] = []
        # Mixin 字段初始化（跨线程修复）- 不再单独维护 self._main_loop
        self._init_mixin_fields()
        # 初次同步获取当前状态
        self._refresh_from_tasks(self._task_manager.get_all_tasks())
        # 订阅未来更新
        self._task_manager.subscribe(self._on_tasks_updated)

    # --- subscribe / _notify / dispose 终态方法从 Mixin 继承 ---
    # （Mixin.subscribe 每次 subscribe 都刷新 _owner_tid + 尝试捕获 loop，更健壮）

    def dispose(self) -> None:
        """清理资源: 退订 TaskManager + Mixin 统一清理 subscribers/loop/pending。"""
        self._task_manager.unsubscribe(self._on_tasks_updated)
        # Mixin 统一清理 subscribers / loop / pending handle / deque
        super().dispose()

    def _on_tasks_updated(self, tasks: list[AppTask]) -> None:
        """TaskManager subscriber (called from TM thread).

        不再手动 call_soon_threadsafe，直接调 _refresh_from_tasks；
        _refresh_from_tasks 末尾调用 Mixin._notify()，
        Mixin 会自动判定跨线程并封送（架构统一双轨消除 P1-3）。
        """
        self._refresh_from_tasks(tasks)

    def _refresh_from_tasks(self, tasks: list[AppTask]) -> None:
        """从 task list 计算 running_count, 仅在变化时更新 state + 通知。"""
        running_count = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        if running_count != self._state.running_count:
            # 经 _set_state 原子绑定（写 state + 捕获快照 + 分配序号），避免跨线程
            # 直写 + _notify() 的「高序号携带旧快照」窗口（CON-03 R2 P1）
            self._set_state(running_count=running_count)
