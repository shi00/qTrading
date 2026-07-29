"""TaskCenterViewModel 补充分支测试。

现有 ``test_task_center_view.py::TestTaskCenterViewModel`` 已覆盖 VM 主路径
（init / subscribe / dispose / pagination / commands / state transitions），
覆盖率 97%。本文件仅补充 2 个未覆盖分支：

- ``_unsubscribe`` 防御分支（callback 已移除时的 no-op，line 91->exit）
- ``_on_tasks_updated`` 主循环运行分支（``call_soon_threadsafe``，line 120）

不重复已有测试，遵循 YAGNI。
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from services.task_manager import AppTask, TaskStatus
from ui.viewmodels.task_center_view_model import TaskCenterViewModel

pytestmark = pytest.mark.unit


def _build_mock_task_manager():
    m = MagicMock()
    m.get_all_tasks.return_value = []
    m.subscribe = MagicMock()
    m.unsubscribe = MagicMock()
    m.cancel_task = MagicMock()
    m.clear_finished = MagicMock()
    return m


def _make_task(status=TaskStatus.QUEUED, **kwargs):
    defaults = dict(
        name="Test Task",
        task_type="System",
        description="desc",
        status=status,
        progress=0.0,
        cancellable=False,
    )
    defaults.update(kwargs)
    return AppTask(**defaults)


class TestTaskCenterViewModelMissingBranches:
    """补充未覆盖的 2 个分支。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_tm = _build_mock_task_manager()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("ui.viewmodels.task_center_view_model.TaskManager", return_value=self.mock_tm))
            yield

    def test_unsubscribe_when_callback_already_removed_is_noop(self):
        """_unsubscribe 二次调用：callback 已不在列表，走 91->exit no-op 分支。"""
        vm = TaskCenterViewModel()
        callback = MagicMock()
        unsub = vm.subscribe(callback)
        unsub()  # 第一次：移除 callback
        assert callback not in vm._subscribers
        # 第二次：callback 已不在列表，if 条件为 False，直接 exit（覆盖 91->exit）
        unsub()
        assert callback not in vm._subscribers

    def test_on_tasks_updated_schedules_on_running_main_loop(self):
        """Phase2 P2-4 更新：_on_tasks_updated 从不同线程调用时，Mixin 跨线程封送通知。

        旧实现：VM._on_tasks_updated 手动调用 loop.call_soon_threadsafe(_refresh_from_tasks, ...)
        新实现：VM._on_tasks_updated → _refresh_from_tasks() → _set_state() →
                Mixin._notify() → 跨线程判定后调用 loop.call_soon_threadsafe(_do_notify_cross_thread)

        验证点：
        (1) 跨线程场景触发 call_soon_threadsafe（owner_tid != current_tid + 有 subscriber）
        (2) state 同步已更新（_refresh_from_tasks 同步完成 state 更新）
        (3) 通知被封送到主 loop（call_soon_threadsafe 被调用）
        """
        import threading

        vm = TaskCenterViewModel()
        # 前置：必须先 subscribe（真实世界 View 总是先 subscribe）
        # Mixin._notify 当 subscribers 为空时短路（不调用 call_soon_threadsafe）
        unsub = vm.subscribe(lambda _s: None)

        # 模拟主循环运行中（subscribe 在真实场景中捕获，单测直接注入 mock loop）
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        vm._main_loop = mock_loop
        # 关键：设置 _owner_tid 为「其他线程」，触发 Mixin 的跨线程分支
        other_thread_tid = -12345  # 非当前线程
        vm._owner_tid = other_thread_tid
        assert other_thread_tid != threading.get_ident()

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        vm._on_tasks_updated(tasks)

        # (2) state 同步已刷新（_refresh_from_tasks 同步执行）
        assert vm.state.total_count == 1
        assert vm.state.running_count == 1

        # (1, 3) Mixin 的跨线程通知调度：call_soon_threadsafe 被调用
        # 注意：callback 参数是 Mixin 内部闭包 _do_notify_cross_thread，
        # 不再是 _refresh_from_tasks。验证至少一次跨线程调度 + callback 可调用。
        mock_loop.call_soon_threadsafe.assert_called_once()
        scheduled_cb = mock_loop.call_soon_threadsafe.call_args.args[0]
        assert callable(scheduled_cb), "跨线程调度的第一个参数必须是可调用对象（Mixin._do_notify_cross_thread）"
        unsub()

    def test_on_tasks_updated_skips_call_soon_when_loop_not_running(self):
        """_on_tasks_updated 当 _main_loop 存在但未运行时退化为同步执行。"""
        vm = TaskCenterViewModel()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        vm._main_loop = mock_loop

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        vm._on_tasks_updated(tasks)

        mock_loop.call_soon_threadsafe.assert_not_called()
        # 同步执行了 _refresh_from_tasks，state 已刷新
        assert vm.state.total_count == 1
        assert vm.state.running_count == 1


class TestRetryTask:
    """Phase 6.2 FR-UX-006: retry_task 委托给 TaskManager。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_tm = _build_mock_task_manager()
        self.mock_tm.retry_task = MagicMock(return_value="new_tid_abc")
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("ui.viewmodels.task_center_view_model.TaskManager", return_value=self.mock_tm))
            yield

    def test_retry_task_delegates_to_manager(self):
        """retry_task(tid) 调用一次 TaskManager.retry_task(tid)。"""
        vm = TaskCenterViewModel()
        vm.retry_task("tid_123")
        self.mock_tm.retry_task.assert_called_once_with("tid_123")
