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
        """_on_tasks_updated 当 _main_loop 运行中时走 call_soon_threadsafe 分支（line 120）。"""
        vm = TaskCenterViewModel()
        # 模拟主循环运行中（subscribe 在真实场景中捕获，单测直接注入 mock loop）
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = True
        vm._main_loop = mock_loop

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        vm._on_tasks_updated(tasks)

        # 验证走 call_soon_threadsafe 分支，而非同步 _refresh_from_tasks
        # 注意：bound method 每次访问创建新对象，用 == 比较（同 __self__ + __func__ 即相等）
        mock_loop.call_soon_threadsafe.assert_called_once()
        scheduled_fn, scheduled_arg = mock_loop.call_soon_threadsafe.call_args.args
        assert scheduled_fn == vm._refresh_from_tasks
        assert scheduled_arg is tasks
        # call_soon_threadsafe 仅调度未执行，state 尚未刷新
        assert vm.state.total_count == 0

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
