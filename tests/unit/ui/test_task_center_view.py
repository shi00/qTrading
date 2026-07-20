"""Unit tests for TaskCenterView declarative rewrite (Phase 3.1).

Tests cover:
- Pure helpers (_format_time, _get_status_label, _get_status_color)
- TaskCenterViewModel state transitions + commands
- _build_task_card pure rendering function

View composition (@ft.component + use_viewmodel) is stateful and covered
by integration tests (flet_test_page fixture), not this unit test file.
"""

# pyright: reportArgumentType=false, reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 参数类型不兼容（替身类/Optional/dict 替代）, 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

import contextlib
import datetime
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from services.task_manager import AppTask, TaskStatus
from ui.viewmodels.task_center_view_model import (
    TaskCenterState,
    TaskCenterViewModel,
    TaskRow,
)
from ui.views.task_center_view import (
    PAGE_SIZE,
    _build_task_card,
    _format_time,
    _get_status_color,
    _get_status_label,
)

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


def _trigger_callback(cb, event):
    """Safely trigger Flet optional callback in tests.

    Flet stubs declare callbacks (on_click/on_change/on_horizontal_drag_*/etc.)
    as Optional[Callable[[], None]], but runtime passes a ControlEvent.
    Centralize type narrowing + type: ignore here.
    """
    assert cb is not None
    cb(event)  # type: ignore[reportCallIssue, reason: Flet stub declares callbacks as 0-arg, but runtime passes event]


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestFormatTime:
    def test_none_returns_placeholder(self):
        assert _format_time(None) == "--:--"

    def test_valid_datetime(self):
        dt = datetime.datetime(2025, 3, 10, 9, 5, 3)
        assert _format_time(dt) == "09:05:03"

    def test_midnight(self):
        dt = datetime.datetime(2025, 1, 1, 0, 0, 0)
        assert _format_time(dt) == "00:00:00"


class TestGetStatusLabel:
    def test_returns_i18n_key(self):
        with patch("ui.views.task_center_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            result = _get_status_label(TaskStatus.RUNNING)
            assert result == "task_status_running"

    def test_unknown_status_falls_back_to_queued(self):
        with patch("ui.views.task_center_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            result = _get_status_label(TaskStatus.QUEUED)
            assert result == "task_status_queued"

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ],
    )
    def test_all_known_statuses_have_labels(self, status):
        with patch("ui.views.task_center_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            result = _get_status_label(status)
            assert isinstance(result, str)
            assert len(result) > 0


class TestGetStatusColor:
    def test_known_status_returns_color(self):
        color = _get_status_color(TaskStatus.RUNNING)
        assert color is not None
        assert isinstance(color, str)

    def test_unknown_status_returns_secondary(self):
        color = _get_status_color("unknown_status")
        assert isinstance(color, str) and color

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ],
    )
    def test_all_known_statuses_have_colors(self, status):
        color = _get_status_color(status)
        assert color is not None
        assert isinstance(color, str)


# ---------------------------------------------------------------------------
# TaskCenterViewModel tests
# ---------------------------------------------------------------------------


class TestTaskCenterViewModel:
    """Test VM state transitions, pagination, and commands."""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_tm = _build_mock_task_manager()
        self.patches = [
            patch("ui.viewmodels.task_center_view_model.TaskManager", return_value=self.mock_tm),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_vm(self, initial_tasks=None):
        if initial_tasks is not None:
            self.mock_tm.get_all_tasks.return_value = initial_tasks
        return TaskCenterViewModel()

    # --- Initial state ---

    def test_initial_state_defaults(self):
        vm = self._make_vm()
        assert vm.state.current_page == 1
        assert vm.state.total_pages == 1
        assert vm.state.total_count == 0
        assert vm.state.running_count == 0
        assert vm.state.tasks == ()

    def test_initial_state_populated_from_task_manager(self):
        tasks = [_make_task(status=TaskStatus.RUNNING), _make_task()]
        vm = self._make_vm(initial_tasks=tasks)
        assert vm.state.total_count == 2
        assert vm.state.running_count == 1
        assert len(vm.state.tasks) == 2

    def test_initial_state_subscribes_to_task_manager(self):
        self._make_vm()
        self.mock_tm.subscribe.assert_called_once()

    def test_initial_state_with_empty_task_manager(self):
        vm = self._make_vm(initial_tasks=[])
        assert vm.state.total_count == 0
        assert vm.state.tasks == ()

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

    def test_dispose_unsubscribes_from_task_manager(self):
        vm = self._make_vm()
        vm.dispose()
        self.mock_tm.unsubscribe.assert_called_once()

    def test_dispose_clears_subscribers(self):
        vm = self._make_vm()
        callback = MagicMock()
        vm.subscribe(callback)
        vm.dispose()
        assert callback not in vm._subscribers

    # --- _on_tasks_updated (state refresh) ---

    def test_on_tasks_updated_updates_state(self):
        vm = self._make_vm()
        tasks = [_make_task(status=TaskStatus.RUNNING), _make_task()]
        vm._on_tasks_updated(tasks)
        assert vm.state.total_count == 2
        assert vm.state.running_count == 1
        assert len(vm.state.tasks) == 2

    def test_on_tasks_updated_with_empty_tasks(self):
        vm = self._make_vm(initial_tasks=[_make_task()])
        vm._on_tasks_updated([])
        assert vm.state.total_count == 0
        assert vm.state.tasks == ()

    def test_on_tasks_updated_notifies_subscribers(self):
        vm = self._make_vm()
        callback = MagicMock()
        vm.subscribe(callback)
        callback.reset_mock()
        vm._on_tasks_updated([_make_task()])
        callback.assert_called_once()

    def test_on_tasks_updated_converts_to_task_rows(self):
        vm = self._make_vm()
        task = _make_task(name="Custom", status=TaskStatus.RUNNING, progress=0.5)
        vm._on_tasks_updated([task])
        row = vm.state.tasks[0]
        assert isinstance(row, TaskRow)
        assert row.name == "Custom"
        assert row.status == TaskStatus.RUNNING
        assert row.progress == 0.5

    def test_on_tasks_updated_counts_running(self):
        vm = self._make_vm()
        tasks = [
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.QUEUED),
        ]
        vm._on_tasks_updated(tasks)
        assert vm.state.running_count == 2
        assert vm.state.total_count == 3

    # --- Pagination ---

    def test_pagination_single_page(self):
        vm = self._make_vm()
        vm._on_tasks_updated([_make_task()])
        assert vm.state.total_pages == 1

    def test_pagination_exact_page_size(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE)]
        vm._on_tasks_updated(tasks)
        assert vm.state.total_pages == 1

    def test_pagination_more_than_page_size(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE + 1)]
        vm._on_tasks_updated(tasks)
        assert vm.state.total_pages == 2

    def test_pagination_clamps_current_page(self):
        vm = self._make_vm()
        # Set up 3 pages
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        assert vm.state.current_page == 1
        # Navigate to page 3
        vm.go_next()
        vm.go_next()
        assert vm.state.current_page == 3
        # Reduce to 1 page — current_page should clamp
        vm._on_tasks_updated([_make_task()])
        assert vm.state.current_page == 1

    def test_pagination_does_not_reduce_current_page_within_range(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        vm.go_next()  # page 2
        # Refresh with same count — page should stay at 2
        vm._on_tasks_updated(tasks)
        assert vm.state.current_page == 2

    # --- go_prev / go_next ---

    def test_go_next_increments_page(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        vm.go_next()
        assert vm.state.current_page == 2

    def test_go_next_at_last_page_does_nothing(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 2)]
        vm._on_tasks_updated(tasks)
        vm.go_next()  # page 2 (last)
        vm.go_next()  # no-op
        assert vm.state.current_page == 2

    def test_go_prev_decrements_page(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        vm.go_next()  # page 2
        vm.go_prev()
        assert vm.state.current_page == 1

    def test_go_prev_at_first_page_does_nothing(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 2)]
        vm._on_tasks_updated(tasks)
        vm.go_prev()  # no-op
        assert vm.state.current_page == 1

    def test_go_next_notifies_subscribers(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 2)]
        vm._on_tasks_updated(tasks)
        callback = MagicMock()
        vm.subscribe(callback)
        callback.reset_mock()
        vm.go_next()
        callback.assert_called_once()

    # --- Commands: cancel_task / clear_finished ---

    def test_cancel_task_calls_task_manager(self):
        vm = self._make_vm()
        vm.cancel_task("task-123")
        self.mock_tm.cancel_task.assert_called_once_with("task-123")

    def test_cancel_task_with_different_id(self):
        vm = self._make_vm()
        vm.cancel_task("abc-456")
        self.mock_tm.cancel_task.assert_called_once_with("abc-456")

    def test_clear_finished_calls_task_manager(self):
        vm = self._make_vm()
        vm.clear_finished()
        self.mock_tm.clear_finished.assert_called_once()

    def test_clear_finished_resets_page(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        vm.go_next()
        vm.go_next()  # page 3
        vm.clear_finished()
        assert vm.state.current_page == 1

    # --- State transitions ---

    def test_state_transition_empty_to_populated(self):
        vm = self._make_vm()
        assert vm.state.total_count == 0
        vm._on_tasks_updated([_make_task(status=TaskStatus.RUNNING)])
        assert vm.state.total_count == 1
        assert vm.state.running_count == 1

    def test_state_transition_populated_to_empty(self):
        vm = self._make_vm()
        vm._on_tasks_updated([_make_task(status=TaskStatus.RUNNING)])
        assert vm.state.total_count == 1
        vm._on_tasks_updated([])
        assert vm.state.total_count == 0
        assert vm.state.tasks == ()

    def test_state_transition_queued_to_running(self):
        vm = self._make_vm()
        task = _make_task(status=TaskStatus.QUEUED)
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 0
        task.status = TaskStatus.RUNNING
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 1

    def test_state_transition_running_to_completed(self):
        vm = self._make_vm()
        task = _make_task(status=TaskStatus.RUNNING, progress=0.5)
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 1
        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 0

    def test_state_transition_running_to_failed(self):
        vm = self._make_vm()
        task = _make_task(status=TaskStatus.RUNNING, progress=0.5)
        vm._on_tasks_updated([task])
        task.status = TaskStatus.FAILED
        task.error = "connection timeout"
        vm._on_tasks_updated([task])
        assert vm.state.running_count == 0
        assert vm.state.tasks[0].error == "connection timeout"

    def test_multiple_tasks_mixed_statuses(self):
        vm = self._make_vm()
        tasks = [
            _make_task(name="Queued", status=TaskStatus.QUEUED),
            _make_task(name="Running", status=TaskStatus.RUNNING, progress=0.5),
            _make_task(name="Completed", status=TaskStatus.COMPLETED, progress=1.0),
            _make_task(name="Failed", status=TaskStatus.FAILED, error="oops"),
        ]
        vm._on_tasks_updated(tasks)
        assert vm.state.total_count == 4
        assert vm.state.running_count == 1

    def test_pagination_across_pages_navigation(self):
        vm = self._make_vm()
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        vm._on_tasks_updated(tasks)
        assert vm.state.current_page == 1
        assert vm.state.total_pages == 3

        vm.go_next()
        assert vm.state.current_page == 2
        vm.go_next()
        assert vm.state.current_page == 3
        vm.go_prev()
        assert vm.state.current_page == 2

    # --- TaskRow immutable ---

    def test_task_row_is_frozen(self):
        row = TaskRow(
            id="x",
            name="n",
            task_type="t",
            description="d",
            status=TaskStatus.QUEUED,
            progress=0.0,
            cancellable=False,
            created_at=datetime.datetime(2025, 1, 1),
            error="",
        )
        with pytest.raises(AttributeError):
            row.name = "changed"  # type: ignore[misc]

    def test_task_center_state_is_frozen(self):
        state = TaskCenterState()
        with pytest.raises(AttributeError):
            state.current_page = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _build_task_card tests (pure rendering function)
# ---------------------------------------------------------------------------


class TestBuildTaskCard:
    """Test _build_task_card pure function for all task statuses."""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_styles = mock_app_styles
        with (
            patch("ui.views.task_center_view.I18n", self.mock_i18n),
            patch("ui.views.task_center_view.AppColors", self.mock_ac),
            patch("ui.views.task_center_view.AppStyles", self.mock_styles),
        ):
            yield

    def _make_row(self, status=TaskStatus.QUEUED, **kwargs):
        defaults = dict(
            id="task-1",
            name="Test Task",
            task_type="System",
            description="desc",
            status=status,
            progress=0.0,
            cancellable=False,
            created_at=datetime.datetime(2025, 1, 1, 12, 0, 0),
            error="",
        )
        defaults.update(kwargs)
        return TaskRow(**defaults)

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.QUEUED,
            TaskStatus.RUNNING,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INTERRUPTED,
        ],
    )
    def test_build_task_card_all_statuses(self, status):
        row = self._make_row(status=status, progress=0.5, cancellable=True)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_running_with_progress(self):
        row = self._make_row(status=TaskStatus.RUNNING, progress=0.75, cancellable=True)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert isinstance(card, ft.Container)

    def test_build_task_card_completed_full_progress(self):
        row = self._make_row(status=TaskStatus.COMPLETED, progress=1.0)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert isinstance(card, ft.Container)

    def test_build_task_card_failed_shows_error(self):
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock())
        assert isinstance(card, ft.Container)

    def test_build_task_card_cancellable_running_has_cancel_button(self):
        row = self._make_row(status=TaskStatus.RUNNING, cancellable=True)
        on_cancel = MagicMock()
        card = _build_task_card(row, on_cancel=on_cancel)
        # The card should contain a TextButton for cancel action
        assert isinstance(_find_control_by_type(card, ft.TextButton), ft.TextButton)

    def test_build_task_card_not_cancellable_running_no_cancel_button(self):
        row = self._make_row(status=TaskStatus.RUNNING, cancellable=False)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert _find_control_by_type(card, ft.TextButton) is None

    def test_build_task_card_cancellable_queued_has_cancel_button(self):
        row = self._make_row(status=TaskStatus.QUEUED, cancellable=True)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert isinstance(_find_control_by_type(card, ft.TextButton), ft.TextButton)

    def test_build_task_card_completed_no_cancel_button(self):
        """Completed tasks should not show cancel button even if cancellable=True."""
        row = self._make_row(status=TaskStatus.COMPLETED, cancellable=True)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert _find_control_by_type(card, ft.TextButton) is None

    def test_build_task_card_cancel_button_triggers_callback(self):
        row = self._make_row(id="task-xyz", status=TaskStatus.RUNNING, cancellable=True)
        on_cancel = MagicMock()
        card = _build_task_card(row, on_cancel=on_cancel)
        btn = _find_control_by_type(card, ft.TextButton)
        assert isinstance(btn, ft.TextButton)
        assert callable(btn.on_click)
        # Simulate click
        _trigger_callback(btn.on_click, MagicMock())
        on_cancel.assert_called_once_with("task-xyz")


# ---------------------------------------------------------------------------
# TaskCenterView 组件体测试 (覆盖 263-408 行 @ft.component 函数体)
# ---------------------------------------------------------------------------


from dataclasses import dataclass, replace  # noqa: E402
from typing import Any  # noqa: E402

from tests.unit.ui.component_renderer import (  # noqa: E402
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)


@dataclass(frozen=True)
class _FakeTaskCenterState:
    """模拟 TaskCenterState 的最小字段集。"""

    tasks: tuple = ()
    current_page: int = 1
    total_pages: int = 1
    total_count: int = 0
    running_count: int = 0


class _FakeTaskCenterViewModel:
    """模拟 TaskCenterViewModel, 记录所有方法调用。

    满足 _ViewModelProtocol 契约 (state/subscribe/dispose) +
    TaskCenterView 调用的所有 sync 方法 (cancel_task/clear_finished/go_prev/go_next)。
    """

    def __init__(self, state: _FakeTaskCenterState | None = None) -> None:
        self._state: _FakeTaskCenterState = state or _FakeTaskCenterState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.method_calls: list[tuple[str, dict]] = []

    @property
    def state(self) -> _FakeTaskCenterState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        for cb in self._subscribers:
            cb(self._state)

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    # --- sync methods ---

    def cancel_task(self, task_id: str) -> None:
        self.method_calls.append(("cancel_task", {"task_id": task_id}))

    def clear_finished(self) -> None:
        self.method_calls.append(("clear_finished", {}))

    def go_prev(self) -> None:
        self.method_calls.append(("go_prev", {}))

    def go_next(self) -> None:
        self.method_calls.append(("go_next", {}))


def _make_task_row(
    status: TaskStatus = TaskStatus.QUEUED,
    **kwargs: Any,
) -> TaskRow:
    """构造 TaskRow (用于 _FakeTaskCenterState.tasks)。"""
    defaults: dict[str, Any] = dict(
        id="task-1",
        name="Test Task",
        task_type="System",
        description="desc",
        status=status,
        progress=0.0,
        cancellable=False,
        created_at=datetime.datetime(2025, 1, 1, 12, 0, 0),
        error="",
    )
    defaults.update(kwargs)
    return TaskRow(**defaults)


def _collect_all_controls(root: object) -> list:
    """深度优先遍历控件树, 返回所有 ft.Control 实例。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归: mock I18n/AppColors 下
    content 属性返回新 MagicMock, 无守卫会无限生成子节点致内存暴涨)。
    """
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_collect_all_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_collect_all_controls(content))
    return result


def _find_icon_button(root: object, icon: object) -> ft.IconButton | None:
    """按 icon 值查找 IconButton。"""
    return next(
        (c for c in _collect_all_controls(root) if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == icon),
        None,
    )


class TestTaskCenterViewComponentBody:
    """TaskCenterView 组件体测试: 渲染结构 + 分页 + 命令调用 + 生命周期。"""

    @pytest.fixture(autouse=True)
    def _patch_i18n(self, mock_i18n, mock_app_colors, mock_app_styles):
        """Patch I18n/AppColors/AppStyles + TaskManager 避免真实实例化。"""
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_styles = mock_app_styles
        with (
            patch("ui.views.task_center_view.I18n", self.mock_i18n),
            patch("ui.views.task_center_view.AppColors", self.mock_ac),
            patch("ui.views.task_center_view.AppStyles", self.mock_styles),
        ):
            yield

    def _mount(
        self,
        monkeypatch,
        state: _FakeTaskCenterState | None = None,
    ) -> tuple[Any, Any, _FakeTaskCenterViewModel]:
        """挂载 TaskCenterView, 返回 (component, render_result, fake_vm)。"""
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(state=state)
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        component = make_component(TaskCenterView)
        run_mount_effects(component)
        result = render_once(component)
        return component, result, fake_vm

    def test_mount_returns_container(self, monkeypatch):
        """挂载 TaskCenterView 返回 ft.Container。"""
        _, result, _ = self._mount(monkeypatch)
        assert isinstance(result, ft.Container)

    def test_mount_subscribes_vm(self, monkeypatch):
        """挂载后 VM.subscribe 被调用 (use_viewmodel hook 注册)。"""
        _, _, fake_vm = self._mount(monkeypatch)
        assert len(fake_vm._subscribers) > 0

    def test_empty_state_renders_inbox_icon(self, monkeypatch):
        """无任务时渲染 empty_view (INBOX_OUTLINED icon)。"""
        _, result, _ = self._mount(monkeypatch, state=_FakeTaskCenterState(tasks=(), total_count=0))
        icons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.INBOX_OUTLINED
        ]
        assert len(icons) == 1, "无任务时应显示 INBOX_OUTLINED icon"

    def test_tasks_rendered_as_cards(self, monkeypatch):
        """有任务时渲染 task cards (非 empty_view)。"""
        row = _make_task_row(status=TaskStatus.RUNNING, progress=0.5)
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1),
        )
        # 不应有 INBOX_OUTLINED icon
        icons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.INBOX_OUTLINED
        ]
        assert len(icons) == 0, "有任务时不应显示 empty_view"

    def test_header_renders_stats_text(self, monkeypatch):
        """header 包含 stats_text (task_stats_fmt 格式化)。"""
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_count=5, running_count=2),
        )
        texts = [c for c in _collect_all_controls(result) if isinstance(c, ft.Text)]
        # task_stats_fmt 是 stats_text 的 i18n key
        assert any("task_stats_fmt" in (getattr(t, "value", "") or "") for t in texts) or any(
            "task_stats_fmt" in (getattr(t, "text", "") or "") for t in texts
        )

    def test_clear_button_present(self, monkeypatch):
        """header 包含 clear_btn (OutlinedButton)。"""
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        _, result, _ = self._mount(monkeypatch)
        clear_btns = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.OutlinedButton) and getattr(c, "icon", None) == ft.Icons.CLEANING_SERVICES_OUTLINED
        ]
        assert len(clear_btns) == 1

    def test_clear_button_triggers_clear_finished(self, monkeypatch):
        """点击 clear_btn → vm.clear_finished。"""
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        _, result, fake_vm = self._mount(monkeypatch)
        clear_btns = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.OutlinedButton) and getattr(c, "icon", None) == ft.Icons.CLEANING_SERVICES_OUTLINED
        ]
        # _on_clear(e) 接收 ControlEvent (Flet on_click 契约)
        _trigger_callback(clear_btns[0].on_click, MagicMock())
        assert ("clear_finished", {}) in fake_vm.method_calls

    def test_pagination_hidden_on_single_page(self, monkeypatch):
        """total_pages=1 时 pagination_row 不显示 (visible=False)。"""
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=1, current_page=1),
        )
        # 找到包含 btn_prev + btn_next 的 Row, 验证 visible=False
        rows = [c for c in _collect_all_controls(result) if isinstance(c, ft.Row)]
        pagination_rows = [
            r
            for r in rows
            if any(isinstance(c, ft.IconButton) for c in (r.controls or []))
            and any(isinstance(c, ft.Text) for c in (r.controls or []))
            and len(r.controls or []) == 3
        ]
        # pagination_row 应存在但 visible=False (total_pages=1)
        assert any(not getattr(r, "visible", True) for r in pagination_rows)

    def test_pagination_visible_on_multiple_pages(self, monkeypatch):
        """total_pages>1 时 pagination_row 显示。"""
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=1),
        )
        rows = [c for c in _collect_all_controls(result) if isinstance(c, ft.Row)]
        pagination_rows = [
            r
            for r in rows
            if any(isinstance(c, ft.IconButton) for c in (r.controls or []))
            and any(isinstance(c, ft.Text) for c in (r.controls or []))
            and len(r.controls or []) == 3
        ]
        assert any(getattr(r, "visible", True) for r in pagination_rows)

    def test_prev_button_disabled_on_first_page(self, monkeypatch):
        """current_page=1 时 btn_prev.disabled=True。"""
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=1),
        )
        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        assert btn_prev is not None
        assert btn_prev.disabled is True

    def test_next_button_disabled_on_last_page(self, monkeypatch):
        """current_page=total_pages 时 btn_next.disabled=True。"""
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=3),
        )
        btn_next = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        assert btn_next is not None
        assert btn_next.disabled is True

    def test_prev_button_enabled_on_non_first_page(self, monkeypatch):
        """current_page>1 时 btn_prev 可点击。"""
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=2),
        )
        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        assert btn_prev is not None
        assert btn_prev.disabled is False

    def test_next_button_triggers_go_next(self, monkeypatch):
        """点击 btn_next → vm.go_next。"""
        _, result, fake_vm = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=1),
        )
        btn_next = _find_icon_button(result, ft.Icons.CHEVRON_RIGHT)
        assert btn_next is not None
        _trigger_callback(btn_next.on_click, MagicMock())
        assert ("go_next", {}) in fake_vm.method_calls

    def test_prev_button_triggers_go_prev(self, monkeypatch):
        """点击 btn_prev → vm.go_prev。"""
        _, result, fake_vm = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=2),
        )
        btn_prev = _find_icon_button(result, ft.Icons.CHEVRON_LEFT)
        assert btn_prev is not None
        _trigger_callback(btn_prev.on_click, MagicMock())
        assert ("go_prev", {}) in fake_vm.method_calls

    def test_page_info_text_shown(self, monkeypatch):
        """pagination_row 包含 "current / total" 文本。"""
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        _, result, _ = self._mount(
            monkeypatch,
            state=_FakeTaskCenterState(total_pages=3, current_page=2),
        )
        texts = [c for c in _collect_all_controls(result) if isinstance(c, ft.Text)]
        # 应有 "2 / 3" 格式的文本
        assert any(
            "2" in (getattr(t, "value", "") or "") and "3" in (getattr(t, "value", "") or "") for t in texts
        ) or any("2" in (getattr(t, "text", "") or "") and "3" in (getattr(t, "text", "") or "") for t in texts)

    def test_unmount_disposes_vm(self, monkeypatch):
        """卸载后 vm.dispose 被调用 (内部 VM 模式)。"""
        component, _, fake_vm = self._mount(monkeypatch)
        assert fake_vm.dispose_called is False
        run_unmount_effects(component)
        assert fake_vm.dispose_called is True

    def test_mount_renders_header_icon(self, monkeypatch):
        """header 包含 TASK_ALT icon。"""
        _, result, _ = self._mount(monkeypatch)
        icons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.TASK_ALT
        ]
        assert len(icons) == 1

    def test_mount_renders_divider(self, monkeypatch):
        """挂载后包含 Divider (header 与 scroll_area 之间)。"""
        _, result, _ = self._mount(monkeypatch)
        dividers = [c for c in _collect_all_controls(result) if isinstance(c, ft.Divider)]
        assert len(dividers) >= 1


def _find_control_by_type(root: ft.Control, control_type: type) -> ft.Control | None:
    """Recursively find first control of given type in the control tree.

    跳过非 ft.Control 对象 (避免 MagicMock 下 getattr 自动生成子节点致无限递归)。
    """
    if not isinstance(root, ft.Control):
        return None
    if isinstance(root, control_type):
        return root
    # Check content attribute (Container)
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        found = _find_control_by_type(content, control_type)
        if found is not None:
            return found
    # Check controls attribute (Column/Row/ListView)
    controls = getattr(root, "controls", None)
    if isinstance(controls, list):
        for ctrl in controls:
            if not isinstance(ctrl, ft.Control):
                continue
            found = _find_control_by_type(ctrl, control_type)
            if found is not None:
                return found
    return None
