"""NavBadgeViewModel 单元测试 (Phase 6.1, FR-UX-006).

覆盖:
- 初始 state 默认值 + 从 TaskManager 同步初始化
- subscribe/dispose 契约 (注册订阅者 + 捕获 main loop + 退订 TaskManager)
- _on_tasks_updated 线程模型 (主循环运行走 call_soon_threadsafe, 否则同步)
- _refresh_from_tasks 仅在 running_count 变化时更新 state + 通知
- 多状态混合任务的 running_count 计算

对齐 ``test_task_center_view_model.py`` 的测试范式 (mock TaskManager + contextlib patch)。
"""

# pyright: reportArgumentType=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import contextlib
from unittest.mock import MagicMock, patch

import pytest

from services.task_manager import AppTask, TaskStatus
from ui.viewmodels.nav_badge_view_model import NavBadgeState, NavBadgeViewModel

pytestmark = pytest.mark.unit


def _build_mock_task_manager():
    m = MagicMock()
    m.get_all_tasks.return_value = []
    m.subscribe = MagicMock()
    m.unsubscribe = MagicMock()
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


class TestNavBadgeViewModel:
    """NavBadgeViewModel 主路径测试。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_tm = _build_mock_task_manager()
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch("ui.viewmodels.nav_badge_view_model.TaskManager", return_value=self.mock_tm))
            yield

    def _make_vm(self, initial_tasks=None):
        if initial_tasks is not None:
            self.mock_tm.get_all_tasks.return_value = initial_tasks
        return NavBadgeViewModel()

    # --- 初始 state ---

    def test_initial_state_defaults(self):
        vm = self._make_vm()
        assert vm.state.running_count == 0

    def test_initial_state_populated_from_task_manager(self):
        tasks = [_make_task(status=TaskStatus.RUNNING), _make_task()]
        vm = self._make_vm(initial_tasks=tasks)
        assert vm.state.running_count == 1

    def test_initial_state_subscribes_to_task_manager(self):
        vm = self._make_vm()
        assert self.mock_tm.subscribe.call_count == 1
        subscribed_cb = self.mock_tm.subscribe.call_args.args[0]
        assert subscribed_cb == vm._on_tasks_updated

    def test_initial_state_with_empty_task_manager(self):
        vm = self._make_vm(initial_tasks=[])
        assert vm.state.running_count == 0

    def test_nav_badge_state_is_frozen(self):
        state = NavBadgeState()
        raised = False
        try:
            state.running_count = 5  # type: ignore[misc]
        except AttributeError:
            raised = True
        assert raised, "frozen dataclass should raise AttributeError on mutation"
        assert state.running_count == 0  # 赋值失败，state 不变

    # --- subscribe / dispose ---

    def test_subscribe_registers_callback(self):
        vm = self._make_vm()
        callback = MagicMock()
        unsub = vm.subscribe(callback)
        assert callback in vm._subscribers
        unsub()
        assert callback not in vm._subscribers

    def test_subscribe_returns_unsubscribe_callable(self):
        vm = self._make_vm()
        unsub = vm.subscribe(MagicMock())
        assert callable(unsub)

    def test_unsubscribe_when_callback_already_removed_is_noop(self):
        vm = self._make_vm()
        callback = MagicMock()
        unsub = vm.subscribe(callback)
        unsub()
        assert callback not in vm._subscribers
        # 二次调用：callback 已不在列表，no-op
        unsub()
        assert callback not in vm._subscribers

    def test_dispose_unsubscribes_from_task_manager(self):
        vm = self._make_vm()
        vm.dispose()
        assert self.mock_tm.unsubscribe.call_count == 1
        unsubscribed_cb = self.mock_tm.unsubscribe.call_args.args[0]
        assert unsubscribed_cb == vm._on_tasks_updated

    def test_dispose_clears_subscribers(self):
        vm = self._make_vm()
        callback = MagicMock()
        vm.subscribe(callback)
        vm.dispose()
        assert callback not in vm._subscribers

    # --- _on_tasks_updated (线程模型) ---

    def test_on_tasks_updated_updates_state(self):
        vm = self._make_vm()
        tasks = [_make_task(status=TaskStatus.RUNNING), _make_task()]
        vm._on_tasks_updated(tasks)
        assert vm.state.running_count == 1

    def test_on_tasks_updated_with_empty_tasks(self):
        vm = self._make_vm(initial_tasks=[_make_task(status=TaskStatus.RUNNING)])
        vm._on_tasks_updated([])
        assert vm.state.running_count == 0

    def test_on_tasks_updated_notifies_subscribers(self):
        vm = self._make_vm()
        callback = MagicMock()
        vm.subscribe(callback)
        callback.reset_mock()
        vm._on_tasks_updated([_make_task(status=TaskStatus.RUNNING)])
        assert callback.call_count == 1
        notified_state = callback.call_args.args[0]
        assert notified_state.running_count == 1

    def test_on_tasks_updated_schedules_on_running_main_loop(self):
        """_on_tasks_updated 当 _main_loop 运行中时走 call_soon_threadsafe 分支。"""
        vm = self._make_vm()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        vm._main_loop = mock_loop

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        vm._on_tasks_updated(tasks)

        mock_loop.call_soon_threadsafe.assert_called_once()  # noqa: weak-assertion 参数在后续 call_args.args 断言验证
        scheduled_fn, scheduled_arg = mock_loop.call_soon_threadsafe.call_args.args
        assert scheduled_fn == vm._refresh_from_tasks
        assert scheduled_arg is tasks
        # call_soon_threadsafe 仅调度未执行，state 尚未刷新
        assert vm.state.running_count == 0

    def test_on_tasks_updated_skips_call_soon_when_loop_not_running(self):
        """_on_tasks_updated 当 _main_loop 存在但未运行时退化为同步执行。"""
        vm = self._make_vm()
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        vm._main_loop = mock_loop

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        vm._on_tasks_updated(tasks)

        mock_loop.call_soon_threadsafe.assert_not_called()
        assert vm.state.running_count == 1

    # --- _refresh_from_tasks (state 更新 + 通知) ---

    def test_refresh_from_tasks_counts_running(self):
        vm = self._make_vm()
        tasks = [
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.QUEUED),
        ]
        vm._refresh_from_tasks(tasks)
        assert vm.state.running_count == 2

    def test_refresh_from_tasks_no_change_skips_notify(self):
        """running_count 未变化时不通知订阅者 (避免无效重渲染)。"""
        vm = self._make_vm(initial_tasks=[_make_task(status=TaskStatus.RUNNING)])
        assert vm.state.running_count == 1

        callback = MagicMock()
        vm.subscribe(callback)
        callback.reset_mock()

        # 同样 1 个 RUNNING 任务，running_count 未变
        vm._refresh_from_tasks([_make_task(status=TaskStatus.RUNNING)])
        callback.assert_not_called()
        assert vm.state.running_count == 1

    def test_refresh_from_tasks_change_notifies(self):
        vm = self._make_vm()
        callback = MagicMock()
        vm.subscribe(callback)
        callback.reset_mock()

        vm._refresh_from_tasks([_make_task(status=TaskStatus.RUNNING)])
        assert callback.call_count == 1
        notified_state = callback.call_args.args[0]
        assert notified_state.running_count == 1
        assert vm.state.running_count == 1

    # --- 状态转换 ---

    def test_state_transition_empty_to_populated(self):
        vm = self._make_vm()
        assert vm.state.running_count == 0
        vm._on_tasks_updated([_make_task(status=TaskStatus.RUNNING)])
        assert vm.state.running_count == 1

    def test_state_transition_populated_to_empty(self):
        vm = self._make_vm(initial_tasks=[_make_task(status=TaskStatus.RUNNING)])
        assert vm.state.running_count == 1
        vm._on_tasks_updated([])
        assert vm.state.running_count == 0

    def test_state_transition_queued_to_running(self):
        vm = self._make_vm()
        task = _make_task(status=TaskStatus.QUEUED)
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 0
        task.status = TaskStatus.RUNNING
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 1

    def test_state_transition_running_to_failed(self):
        vm = self._make_vm()
        task = _make_task(status=TaskStatus.RUNNING, progress=0.5)
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 1
        task.status = TaskStatus.FAILED
        task.error = "connection timeout"
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 0

    def test_multiple_tasks_mixed_statuses(self):
        vm = self._make_vm()
        tasks = [
            _make_task(name="Queued", status=TaskStatus.QUEUED),
            _make_task(name="Running", status=TaskStatus.RUNNING, progress=0.5),
            _make_task(name="Completed", status=TaskStatus.COMPLETED, progress=1.0),
            _make_task(name="Failed", status=TaskStatus.FAILED, error="oops"),
        ]
        vm._on_tasks_updated(tasks)
        assert vm.state.running_count == 1
