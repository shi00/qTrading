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

import asyncio
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
    _format_duration,
    _format_time,
    _get_page,
    _get_status_color,
    _get_status_label,
    _task_time_summary,
)
from utils.time_utils import get_now

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


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """渲染上下文可用时返回 page 实例。"""
        mock_page = MagicMock(name="page")
        with patch("ui.views.task_center_view.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)  # noqa: B010
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        with patch("ui.views.task_center_view.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))  # noqa: B010
            assert _get_page() is None


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
# MINOR-11: _format_duration / _task_time_summary
# ---------------------------------------------------------------------------


class TestFormatDuration:
    """MINOR-11: 时长格式化 (mm:ss / h:mm:ss, 负值夹取为 0)。"""

    def test_zero(self):
        assert _format_duration(0) == "00:00"

    def test_seconds_and_minutes(self):
        assert _format_duration(75) == "01:15"

    def test_hours(self):
        assert _format_duration(3661) == "1:01:01"

    def test_negative_clamped_to_zero(self):
        assert _format_duration(-12.5) == "00:00"

    def test_fractional_seconds_truncated(self):
        assert _format_duration(59.9) == "00:59"


class TestTaskTimeSummary:
    """MINOR-11: 运行中任务「已耗时 / 预计剩余」文本生成。"""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        with patch("ui.views.task_center_view.I18n", mock_i18n):
            yield

    def _row(self, status=TaskStatus.RUNNING, started_at=datetime.datetime(2025, 1, 1, 12, 0, 0), **kwargs):
        defaults = dict(
            id="t1",
            name="n",
            task_type="System",
            description="d",
            status=status,
            progress=0.5,
            cancellable=True,
            created_at=datetime.datetime(2025, 1, 1, 11, 0, 0),
            error="",
            started_at=started_at,
        )
        defaults.update(kwargs)
        return TaskRow(**defaults)

    def test_returns_none_for_non_running(self):
        row = self._row(status=TaskStatus.COMPLETED)
        assert _task_time_summary(row, datetime.datetime(2025, 1, 1, 12, 1, 0)) is None

    def test_returns_none_without_started_at(self):
        row = self._row(started_at=None)
        assert _task_time_summary(row, datetime.datetime(2025, 1, 1, 12, 1, 0)) is None

    def test_returns_none_without_now(self):
        row = self._row()
        assert _task_time_summary(row, None) is None

    def test_progress_zero_shows_estimating(self):
        """R21: 无法推算剩余时显示「计算中」而非伪造数值。"""
        row = self._row(progress=0.0)
        text = _task_time_summary(row, datetime.datetime(2025, 1, 1, 12, 2, 0))
        assert text is not None
        assert "task_elapsed_fmt" in text
        assert "task_remaining_estimating" in text

    def test_partial_progress_shows_elapsed_and_remaining(self):
        row = self._row(progress=0.5)
        text = _task_time_summary(row, datetime.datetime(2025, 1, 1, 12, 2, 0))
        assert text is not None
        assert "task_elapsed_fmt" in text
        assert "task_remaining_fmt" in text

    def test_complete_progress_shows_elapsed_only(self):
        row = self._row(progress=1.0)
        text = _task_time_summary(row, datetime.datetime(2025, 1, 1, 12, 2, 0))
        assert text is not None
        assert "task_elapsed_fmt" in text
        assert "task_remaining" not in text


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
        # D6-7: 默认按 status 推断 is_retryable——FAILED/INTERRUPTED 视为带 factory 的
        # 可重试任务（真实世界对应活动任务或仍持 factory 的任务），无需调用方显式传值。
        defaults.setdefault("is_retryable", status in (TaskStatus.FAILED, TaskStatus.INTERRUPTED))
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

    # --- Phase 6.2 (FR-UX-006): FAILED task retry + details buttons ---

    def test_build_task_card_failed_has_retry_button(self):
        """FAILED task card should show Retry button when on_retry is provided."""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        # FAILED card should have at least 1 TextButton (retry)
        assert len(buttons) >= 1

    def test_build_task_card_failed_has_details_button(self):
        """FAILED task card should show View Details button when on_view_details is provided."""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock(), on_view_details=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        # FAILED card should have 2 TextButtons (retry + details)
        assert len(buttons) >= 2

    def test_build_task_card_failed_no_retry_button_when_callback_none(self):
        """FAILED task card should not show Retry button when on_retry is None."""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=None, on_view_details=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        # Only details button, no retry
        assert len(buttons) == 1

    def test_build_task_card_failed_retry_triggers_callback(self):
        """Clicking Retry button should call on_retry with task_id."""
        row = self._make_row(id="failed-task-1", status=TaskStatus.FAILED, error="timeout")
        on_retry = MagicMock()
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=on_retry)
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) >= 1
        # First button is retry (appended before details)
        _trigger_callback(buttons[0].on_click, MagicMock())
        on_retry.assert_called_once_with("failed-task-1")

    def test_build_task_card_failed_details_triggers_callback(self):
        """Clicking View Details button should call on_view_details with task_id."""
        row = self._make_row(id="failed-task-2", status=TaskStatus.FAILED, error="timeout")
        on_view_details = MagicMock()
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock(), on_view_details=on_view_details)
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) >= 2
        # Second button is details (appended after retry)
        _trigger_callback(buttons[1].on_click, MagicMock())
        on_view_details.assert_called_once_with("failed-task-2")

    def test_build_task_card_failed_no_buttons_when_callbacks_none(self):
        """FAILED task card should have no action buttons when callbacks are None."""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full")
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=None, on_view_details=None)
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) == 0

    # --- D6-3: INTERRUPTED task retry button（与 FAILED 业务等价，均可重新发起续传）---

    def test_build_task_card_interrupted_has_retry_button(self):
        """INTERRUPTED task card should show Retry button when on_retry is provided."""
        row = self._make_row(status=TaskStatus.INTERRUPTED)
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) >= 1

    def test_build_task_card_interrupted_retry_triggers_callback(self):
        """Clicking Retry button on INTERRUPTED card should call on_retry with task_id."""
        row = self._make_row(id="interrupted-task-1", status=TaskStatus.INTERRUPTED)
        on_retry = MagicMock()
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=on_retry)
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) >= 1
        _trigger_callback(buttons[0].on_click, MagicMock())
        on_retry.assert_called_once_with("interrupted-task-1")

    def test_build_task_card_interrupted_no_retry_button_when_callback_none(self):
        """INTERRUPTED task card should not show Retry button when on_retry is None."""
        row = self._make_row(status=TaskStatus.INTERRUPTED)
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=None)
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) == 0

    # --- D6-7: 重试按钮可见性基于 is_retryable（历史任务无 factory 时重试入口无效）---

    def test_build_task_card_failed_no_retry_button_when_not_retryable(self):
        """FAILED task without factory (is_retryable=False) must NOT show Retry button even with on_retry.

        D6-7: 从 DB 加载的历史任务即使状态为 FAILED 也没有 _coroutine_factory，无法重建
        协程重试；UI 以 is_retryable 隐藏重试按钮，避免"按钮存在但无效"。
        """
        row = self._make_row(status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock(), on_view_details=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        # 仅详情按钮，无重试按钮
        assert len(buttons) == 1

    def test_build_task_card_interrupted_no_retry_button_when_not_retryable(self):
        """INTERRUPTED task without factory (is_retryable=False) must NOT show Retry button."""
        row = self._make_row(status=TaskStatus.INTERRUPTED, is_retryable=False)
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock())
        buttons = _find_all_controls_by_type(card, ft.TextButton)
        assert len(buttons) == 0

    # --- MINOR-11: 运行中任务显示已耗时 / 预计剩余 ---

    def test_build_task_card_running_shows_time_summary(self):
        """运行中任务卡含已耗时文本 (DoD 1)。"""
        row = self._make_row(
            status=TaskStatus.RUNNING,
            cancellable=True,
            progress=0.5,
            started_at=datetime.datetime(2025, 1, 1, 12, 0, 0),
        )
        card = _build_task_card(row, on_cancel=MagicMock(), now=datetime.datetime(2025, 1, 1, 12, 2, 0))
        texts = _find_all_controls_by_type(card, ft.Text)
        assert any("task_elapsed_fmt" in (getattr(t, "value", "") or "") for t in texts)

    def test_build_task_card_running_without_now_has_no_time_summary(self):
        """未传 now (或 started_at 未知) 时不渲染耗时文本 (纯函数降级)。"""
        row = self._make_row(status=TaskStatus.RUNNING, cancellable=True, progress=0.5, started_at=None)
        card = _build_task_card(row, on_cancel=MagicMock(), now=None)
        texts = _find_all_controls_by_type(card, ft.Text)
        assert not any("task_elapsed_fmt" in (getattr(t, "value", "") or "") for t in texts)

    # --- MINOR-11: 历史失败任务（不可重试）显示「前往数据源页重新发起」指引 ---

    def test_build_task_card_not_retryable_failed_shows_reopen_source(self):
        """DoD 3: 不可重试的历史失败任务显示重新发起指引。"""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        card = _build_task_card(
            row,
            on_cancel=MagicMock(),
            on_retry=MagicMock(),
            on_view_details=MagicMock(),
            on_reopen_source=MagicMock(),
        )
        btn = _find_textbutton_by_content(card, "task_failed_reopen_source")
        assert isinstance(btn, ft.TextButton)
        assert btn.icon == ft.Icons.OPEN_IN_NEW

    def test_build_task_card_reopen_source_triggers_callback(self):
        row = self._make_row(id="hist-1", status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        on_reopen = MagicMock()
        card = _build_task_card(row, on_cancel=MagicMock(), on_reopen_source=on_reopen)
        btn = _find_textbutton_by_content(card, "task_failed_reopen_source")
        assert isinstance(btn, ft.TextButton)
        _trigger_callback(btn.on_click, MagicMock())
        on_reopen.assert_called_once_with("hist-1")

    def test_build_task_card_retryable_failed_hides_reopen_source(self):
        """可重试任务已显示「重试」，不应再显示重新发起指引 (避免重复入口)。"""
        row = self._make_row(status=TaskStatus.FAILED, error="disk full", is_retryable=True)
        card = _build_task_card(row, on_cancel=MagicMock(), on_retry=MagicMock(), on_reopen_source=MagicMock())
        assert _find_textbutton_by_content(card, "task_failed_reopen_source") is None


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
    from flet.components.component import Component

    if root is None or not isinstance(root, (ft.Control, Component)):
        return []
    result: list = [root]

    if isinstance(root, Component):
        try:
            from tests.unit.ui.component_renderer import render_once

            root._state.mounted = True
            rendered = render_once(root)
            result.extend(_collect_all_controls(rendered))
        except Exception:
            pass
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

    # --- MINOR-11: 长任务反馈与控制 ---

    def _mount_with_page(self, monkeypatch, state: _FakeTaskCenterState | None = None):
        """挂载并返回 (component, result, fake_vm, page), 以便检查 use_dialog 挂载结果。"""
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(state=state)
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        component = make_component(TaskCenterView)
        page = run_mount_effects(component)
        result = render_once(component)
        return component, result, fake_vm, page

    def test_running_task_card_shows_elapsed_text(self, monkeypatch):
        """DoD 1: 运行中任务卡含已耗时文本。"""
        row = _make_task_row(
            status=TaskStatus.RUNNING,
            cancellable=True,
            progress=0.5,
            started_at=get_now() - datetime.timedelta(seconds=61),
        )
        _, result, _, _ = self._mount_with_page(
            monkeypatch, state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1)
        )
        texts = [c for c in _collect_all_controls(result) if isinstance(c, ft.Text)]
        assert any("task_elapsed_fmt" in (getattr(t, "value", "") or "") for t in texts)

    def test_not_retryable_failed_card_shows_reopen_source_hint(self, monkeypatch):
        """DoD 3: 历史失败任务 (不可重试) 渲染「前往数据源页重新发起」指引。"""
        row = _make_task_row(id="hist-1", status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        _, result, _, _ = self._mount_with_page(monkeypatch, state=_FakeTaskCenterState(tasks=(row,), total_count=1))
        btn = _find_textbutton_by_content(result, "task_failed_reopen_source")
        assert isinstance(btn, ft.TextButton)
        assert btn.icon == ft.Icons.OPEN_IN_NEW

    def test_cancel_click_opens_confirm_without_cancelling(self, monkeypatch):
        """DoD 2: 取消点击仅打开确认对话框，未确认时不执行取消。"""
        row = _make_task_row(id="run-1", status=TaskStatus.RUNNING, cancellable=True)
        component, result, fake_vm, page = self._mount_with_page(
            monkeypatch, state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1)
        )
        btns = _find_all_controls_by_type(result, ft.TextButton)
        assert len(btns) == 1
        _trigger_callback(btns[0].on_click, MagicMock())
        assert ("cancel_task", {"task_id": "run-1"}) not in fake_vm.method_calls
        render_once(component)
        dialogs = [c for c in page._dialogs.controls if isinstance(c, ft.AlertDialog)]
        assert len(dialogs) >= 1, "取消应弹出确认对话框"

    def test_confirm_cancel_calls_cancel_task(self, monkeypatch):
        """DoD 2: 确认后执行取消。"""
        row = _make_task_row(id="run-2", status=TaskStatus.RUNNING, cancellable=True)
        component, result, fake_vm, page = self._mount_with_page(
            monkeypatch, state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1)
        )
        btns = _find_all_controls_by_type(result, ft.TextButton)
        _trigger_callback(btns[0].on_click, MagicMock())
        render_once(component)
        dialog = next(c for c in page._dialogs.controls if isinstance(c, ft.AlertDialog))
        confirm_btn = dialog.actions[1]  # [0]=dismiss, [1]=confirm
        _trigger_callback(confirm_btn.on_click, MagicMock())
        assert ("cancel_task", {"task_id": "run-2"}) in fake_vm.method_calls

    def test_dismiss_cancel_does_not_cancel(self, monkeypatch):
        """DoD 2: 关闭确认框不执行取消。"""
        row = _make_task_row(id="run-3", status=TaskStatus.RUNNING, cancellable=True)
        component, result, fake_vm, page = self._mount_with_page(
            monkeypatch, state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1)
        )
        btns = _find_all_controls_by_type(result, ft.TextButton)
        _trigger_callback(btns[0].on_click, MagicMock())
        render_once(component)
        dialog = next(c for c in page._dialogs.controls if isinstance(c, ft.AlertDialog))
        dismiss_btn = dialog.actions[0]
        _trigger_callback(dismiss_btn.on_click, MagicMock())
        assert ("cancel_task", {"task_id": "run-3"}) not in fake_vm.method_calls

    def test_ticker_not_started_without_running_task(self, monkeypatch):
        """无运行中任务时不启动 1s ticker (无定时器开销)。"""
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(total_count=1, running_count=0))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        page.run_task = MagicMock()
        component = make_component(TaskCenterView)
        run_mount_effects(component, page=page)
        page.run_task.assert_not_called()

    def test_ticker_started_with_running_task(self, monkeypatch):
        """存在运行中任务时启动 ticker 以刷新耗时文本。"""
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(total_count=1, running_count=1))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        page.run_task = MagicMock()
        component = make_component(TaskCenterView)
        run_mount_effects(component, page=page)
        assert page.run_task.call_count == 1
        args, _kwargs = page.run_task.call_args
        assert len(args) == 1 and asyncio.iscoroutinefunction(args[0])
        run_unmount_effects(component)

    def test_ticker_tick_advances_and_propagates_cancelled_error(self, monkeypatch):
        """MINOR-11: ticker 循环体每 1s 递增计数 (驱动耗时文本刷新), 且 CancelledError 必须传播 (R2)。"""
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(total_count=1, running_count=1))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        page.run_task = MagicMock()
        component = make_component(TaskCenterView)
        run_mount_effects(component, page=page)
        # page.run_task(handler) 要求传入协程函数 (Flet 内部执行 handler(*args))，
        # 故此处取出的是 _tick 函数本身，需调用后得到协程再驱动。
        tick_factory = page.run_task.call_args[0][0]

        sleep_calls = 0

        async def _fake_sleep(_seconds: float) -> None:
            nonlocal sleep_calls
            sleep_calls += 1
            if sleep_calls > 1:
                raise asyncio.CancelledError

        monkeypatch.setattr("ui.views.task_center_view.asyncio.sleep", _fake_sleep)
        with pytest.raises(asyncio.CancelledError) as exc_info:
            asyncio.run(tick_factory())
        # R2: 异常原样传播, 未被 except 分支吞没
        assert isinstance(exc_info.value, asyncio.CancelledError)
        # 首轮 sleep 后执行循环体 (递增计数), 次轮 sleep 抛 CancelledError 终止循环
        assert sleep_calls == 2
        run_unmount_effects(component)

    # --- F10: ticker 门控 active（页面可见）——隐藏页不参与每秒唤醒 ---

    def _mount_ticker_view(self, monkeypatch, active: bool, running_count: int):
        """挂载带可控 active 的 TaskCenterView, 返回 (component, fake_vm, page)。

        FakePage.run_task 注入 MagicMock 以记录 ticker 创建/取消次数。
        """
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        fake_vm = _FakeTaskCenterViewModel(
            state=_FakeTaskCenterState(total_count=max(running_count, 1), running_count=running_count)
        )
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        page.run_task = MagicMock()
        component = make_component(TaskCenterView, active=active)
        run_mount_effects(component, page=page)
        return component, fake_vm, page

    def _rerender_with_active(self, component, active: bool):
        """以新 active prop 重渲染并触发 render effects (deps 变化检测), 返回渲染结果。"""
        component.kwargs = {"active": active}
        result = render_once(component)
        component._run_render_effects()
        return result

    def test_ticker_not_started_when_inactive_even_with_running_task(self, monkeypatch):
        """F10: 页面 inactive 时即使存在运行中任务也不启动 ticker。"""
        component, _, page = self._mount_ticker_view(monkeypatch, active=False, running_count=1)
        page.run_task.assert_not_called()
        # inactive 期间重复渲染 (如 VM 推送) 也不创建
        render_once(component)
        component._run_render_effects()
        page.run_task.assert_not_called()
        run_unmount_effects(component)

    def test_ticker_stops_and_restarts_on_active_toggle(self, monkeypatch):
        """F10: 切离页面取消 ticker, 切回只重建一个; inactive 期间无新建。"""
        component, _, page = self._mount_ticker_view(monkeypatch, active=True, running_count=1)
        assert page.run_task.call_count == 1
        _rerender = self._rerender_with_active
        _rerender(component, active=False)
        assert page.run_task.call_count == 1, "inactive 时不应创建新 ticker"
        _rerender(component, active=True)
        assert page.run_task.call_count == 2, "重激活应只重建一个 ticker"
        run_unmount_effects(component)

    def test_ticker_single_instance_across_repeated_cycles(self, monkeypatch):
        """F10: 反复往返切页, 每个 active 周期只建一个 ticker, 不累积。"""
        component, _, page = self._mount_ticker_view(monkeypatch, active=True, running_count=1)
        created = 1  # mount 时建 1 个
        for _ in range(3):
            self._rerender_with_active(component, active=False)
            assert page.run_task.call_count == created, "inactive 段不建 ticker"
            self._rerender_with_active(component, active=True)
            created += 1
            assert page.run_task.call_count == created, "重激活只建一个 ticker"
        assert page.run_task.call_count == 4, "mount 1 次 + 3 次重激活各 1 次"
        run_unmount_effects(component)

    def test_ticker_stops_when_running_count_drops(self, monkeypatch):
        """F10: 页面 active 下运行任务归零, ticker 经 cleanup 取消且不重建。"""
        component, fake_vm, page = self._mount_ticker_view(monkeypatch, active=True, running_count=1)
        assert page.run_task.call_count == 1
        page.run_task.return_value.cancel.assert_not_called()
        fake_vm._set_state(tasks=(), total_count=0, running_count=0)
        render_once(component)
        component._run_render_effects()
        # 任务归零 → cleanup 取消旧 ticker, setup 守卫不新建
        # (assert_called_once_with: 恰好一次且无参调用, 与 _cleanup_ticker 的 cancel() 签名对齐)
        page.run_task.return_value.cancel.assert_called_once_with()
        assert page.run_task.call_count == 1
        # 归零后重复渲染仍不重建
        render_once(component)
        component._run_render_effects()
        assert page.run_task.call_count == 1
        run_unmount_effects(component)

    def test_ticker_cancelled_on_unmount(self, monkeypatch):
        """F10: 组件卸载时显式 cleanup 取消在跑 ticker, 无遗留循环。"""
        component, _, page = self._mount_ticker_view(monkeypatch, active=True, running_count=1)
        ticker_task = page.run_task.return_value
        assert not ticker_task.cancel.called
        run_unmount_effects(component)
        # assert_called_once_with: 恰好一次且无参调用, 与 _cleanup_ticker 的 cancel() 签名对齐
        ticker_task.cancel.assert_called_once_with()

    def test_elapsed_time_shows_latest_wall_clock_on_reactivation(self, monkeypatch):
        """F10: 切回页面即时按墙钟计算经过时间, 包含隐藏期真实流逝 (不累计假时间)。

        隐藏期间 ticker 停止、不重渲染; 切回时 active prop 变化触发重渲染,
        渲染时 get_now() 取最新墙钟 → elapsed = 最新墙钟 - started_at。
        """
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        started_at = datetime.datetime(2025, 1, 1, 12, 0, 0)
        row = _make_task_row(status=TaskStatus.RUNNING, cancellable=True, progress=0.5, started_at=started_at)
        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(tasks=(row,), total_count=1, running_count=1))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        # 可控时钟: 挂载时 13:00 (elapsed 1h); 隐藏期前进 5 分钟后 13:05 (elapsed 1h05m)
        clock = {"now": datetime.datetime(2025, 1, 1, 13, 0, 0)}
        monkeypatch.setattr("ui.views.task_center_view.get_now", lambda: clock["now"])
        # duration 参数透出以便断言具体时长 (task_elapsed_fmt 带 duration kwarg)
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: kw.get("duration", key)

        page = FakePage()
        page.run_task = MagicMock()
        component = make_component(TaskCenterView, active=True)
        run_mount_effects(component, page=page)
        result = render_once(component)
        texts = [(getattr(t, "value", "") or "") for t in _find_all_controls_by_type(result, ft.Text)]
        assert any("1:00:00" in t for t in texts), "挂载时应显示 1h 经过时间"

        # 切离页面 (时钟静止不推进——隐藏期 ticker 停止)
        self._rerender_with_active(component, active=False)
        # 隐藏 5 分钟后切回: 重渲染以最新墙钟即时计算
        clock["now"] = datetime.datetime(2025, 1, 1, 13, 5, 0)
        result = self._rerender_with_active(component, active=True)
        texts = [(getattr(t, "value", "") or "") for t in _find_all_controls_by_type(result, ft.Text)]
        assert any("1:05:00" in t for t in texts), "切回后应显示包含隐藏期流逝的最新时长"
        assert not any("1:00:00" in t for t in texts), "不应残留隐藏前的旧时长"
        run_unmount_effects(component)

    def test_reopen_source_click_navigates_to_data_source(self, monkeypatch):
        """DoD 3: 点击「前往数据源页重新发起」→ PubSub 广播 TOPIC_NAVIGATE / "settings:data"。"""
        from tests.unit.ui.component_renderer import FakePage
        from ui.pubsub_topics import TOPIC_NAVIGATE
        from ui.views.task_center_view import TaskCenterView

        row = _make_task_row(id="hist-9", status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(tasks=(row,), total_count=1))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        page.pubsub = MagicMock()  # type: ignore[attr-defined]  # [reason: FakePage 未定义 pubsub, 测试注入以验证深链导航]
        component = make_component(TaskCenterView)
        run_mount_effects(component, page=page)
        result = render_once(component)
        btn = _find_textbutton_by_content(result, "task_failed_reopen_source")
        assert isinstance(btn, ft.TextButton)
        _trigger_callback(btn.on_click, MagicMock())
        page.pubsub.send_all_on_topic.assert_called_once_with(TOPIC_NAVIGATE, "settings:data")
        run_unmount_effects(component)

    def test_reopen_source_without_pubsub_does_not_raise(self, monkeypatch):
        """无 pubsub 的 page (或未在渲染上下文) 时导航静默降级, 不抛异常。"""
        from tests.unit.ui.component_renderer import FakePage
        from ui.views.task_center_view import TaskCenterView

        row = _make_task_row(id="hist-10", status=TaskStatus.FAILED, error="disk full", is_retryable=False)
        fake_vm = _FakeTaskCenterViewModel(state=_FakeTaskCenterState(tasks=(row,), total_count=1))
        monkeypatch.setattr("ui.views.task_center_view.TaskCenterViewModel", lambda: fake_vm)
        page = FakePage()
        component = make_component(TaskCenterView)
        run_mount_effects(component, page=page)
        result = render_once(component)
        btn = _find_textbutton_by_content(result, "task_failed_reopen_source")
        assert isinstance(btn, ft.TextButton)
        _trigger_callback(btn.on_click, MagicMock())
        run_unmount_effects(component)


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


def _find_all_controls_by_type(root: ft.Control, control_type: type) -> list[ft.Control]:
    """Recursively find ALL controls of given type in the control tree (Phase 6.2)."""
    results: list[ft.Control] = []
    if not isinstance(root, ft.Control):
        return results
    if isinstance(root, control_type):
        results.append(root)
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        results.extend(_find_all_controls_by_type(content, control_type))
    controls = getattr(root, "controls", None)
    if isinstance(controls, list):
        for ctrl in controls:
            if not isinstance(ctrl, ft.Control):
                continue
            results.extend(_find_all_controls_by_type(ctrl, control_type))
    return results


def _find_textbutton_by_content(root: ft.Control, content_text: str) -> ft.Control | None:
    """按 content 文本查找 TextButton (MINOR-11: 定位重新发起指引按钮)。"""
    return next(
        (c for c in _find_all_controls_by_type(root, ft.TextButton) if getattr(c, "content", None) == content_text),
        None,
    )
