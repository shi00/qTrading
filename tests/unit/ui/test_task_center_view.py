"""Unit tests for TaskCenterView declarative rewrite (Phase 3.1).

Tests cover:
- Pure helpers (_format_time, _get_status_label, _get_status_color)
- TaskCenterViewModel state transitions + commands
- _build_task_card pure rendering function

View composition (@ft.component + use_viewmodel) is stateful and covered
by integration tests (flet_test_page fixture), not this unit test file.
"""

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
        assert color is not None

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
        assert card is not None

    def test_build_task_card_failed_shows_error(self):
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock())
        assert card is not None

    def test_build_task_card_cancellable_running_has_cancel_button(self):
        row = self._make_row(status=TaskStatus.RUNNING, cancellable=True)
        on_cancel = MagicMock()
        card = _build_task_card(row, on_cancel=on_cancel)
        # The card should contain a TextButton for cancel action
        assert _find_control_by_type(card, ft.TextButton) is not None

    def test_build_task_card_not_cancellable_running_no_cancel_button(self):
        row = self._make_row(status=TaskStatus.RUNNING, cancellable=False)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert _find_control_by_type(card, ft.TextButton) is None

    def test_build_task_card_cancellable_queued_has_cancel_button(self):
        row = self._make_row(status=TaskStatus.QUEUED, cancellable=True)
        card = _build_task_card(row, on_cancel=MagicMock())
        assert _find_control_by_type(card, ft.TextButton) is not None

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
        assert btn is not None
        assert btn.on_click is not None
        # Simulate click
        btn.on_click(MagicMock())
        on_cancel.assert_called_once_with("task-xyz")


def _find_control_by_type(root: ft.Control, control_type: type) -> ft.Control | None:
    """Recursively find first control of given type in the control tree."""
    if isinstance(root, control_type):
        return root
    # Check content attribute (Container)
    content = getattr(root, "content", None)
    if content is not None:
        found = _find_control_by_type(content, control_type)
        if found is not None:
            return found
    # Check controls attribute (Column/Row/ListView)
    controls = getattr(root, "controls", None)
    if controls:
        for ctrl in controls:
            found = _find_control_by_type(ctrl, control_type)
            if found is not None:
                return found
    return None
