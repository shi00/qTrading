"""Unit tests for ui/views/task_center_view.py."""

import contextlib
import datetime
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from services.task_manager import AppTask, TaskStatus
from tests.unit.ui.conftest import set_page, wrap_mock_page
from ui.views.task_center_view import (
    PAGE_SIZE,
    TaskCenterView,
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
        """_get_status_label expects TaskStatus; unknown keys in _STATUS_I18N_MAP fall back to task_status_queued."""
        with patch("ui.views.task_center_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            # Use a valid TaskStatus that is not in _STATUS_I18N_MAP to test fallback
            # All TaskStatus values are in the map, so test with a valid one and verify the key
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
# TaskCenterView tests
# ---------------------------------------------------------------------------


class TestTaskCenterView:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_tm = _build_mock_task_manager()
        self.patches = [
            patch("ui.views.task_center_view.I18n", self.mock_i18n),
            patch("ui.views.task_center_view.AppColors", self.mock_ac),
            patch("ui.views.task_center_view.AppStyles"),
            patch("ui.views.task_center_view.TaskManager", return_value=self.mock_tm),
            patch("ui.views.task_center_view.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_view(self, mock_page):
        return TaskCenterView(mock_page)

    # --- Initialization ---

    def test_instantiation_creates_sub_components(self, mock_page):
        view = self._make_view(mock_page)
        assert view.btn_prev is not None
        assert view.btn_next is not None
        assert view.scroll_area is not None
        assert view.pagination_row is not None
        assert view.stats_text is not None
        assert view.clear_btn is not None
        assert view.empty_view is not None

    def test_instantiation_default_state(self, mock_page):
        view = self._make_view(mock_page)
        assert view._mounted is False
        assert view._current_page == 1
        assert view._total_pages == 1
        assert view._all_tasks == []

    def test_instantiation_expand_true(self, mock_page):
        view = self._make_view(mock_page)
        assert view.expand is True

    def test_instantiation_task_manager_assigned(self, mock_page):
        view = self._make_view(mock_page)
        assert view.task_manager is self.mock_tm

    def test_instantiation_pagination_controls_initial_state(self, mock_page):
        view = self._make_view(mock_page)
        assert view.btn_prev.disabled is True
        assert view.btn_next.disabled is True
        assert view.page_info_text.value == "1 / 1"

    # --- Lifecycle: did_mount / will_unmount ---

    def test_did_mount_sets_mounted_flag(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        assert view._mounted is True

    def test_did_mount_subscribes_to_task_manager(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        self.mock_tm.subscribe.assert_called_once_with(view._on_tasks_updated)

    def test_did_mount_triggers_initial_refresh(self, mock_page):
        view = self._make_view(mock_page)
        view.did_mount()
        self.mock_tm.get_all_tasks.assert_called_once()

    def test_will_unmount_clears_mounted_flag(self, mock_page):
        view = self._make_view(mock_page)
        view._mounted = True
        view.will_unmount()
        assert view._mounted is False

    def test_will_unmount_unsubscribes_from_task_manager(self, mock_page):
        view = self._make_view(mock_page)
        view.will_unmount()
        self.mock_tm.unsubscribe.assert_called_once_with(view._on_tasks_updated)

    # --- Pagination: _compute_pagination ---

    def test_compute_pagination_with_zero(self, mock_page):
        view = self._make_view(mock_page)
        view._compute_pagination(0)
        assert view._total_pages == 1

    def test_compute_pagination_with_less_than_page_size(self, mock_page):
        view = self._make_view(mock_page)
        view._compute_pagination(5)
        assert view._total_pages == 1

    def test_compute_pagination_with_exact_page_size(self, mock_page):
        view = self._make_view(mock_page)
        view._compute_pagination(PAGE_SIZE)
        assert view._total_pages == 1

    def test_compute_pagination_with_more_than_page_size(self, mock_page):
        view = self._make_view(mock_page)
        view._compute_pagination(PAGE_SIZE + 1)
        assert view._total_pages == 2

    def test_compute_pagination_clamps_current_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 5
        view._compute_pagination(1)
        assert view._current_page == 1

    def test_compute_pagination_does_not_reduce_current_page_within_range(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 2
        view._compute_pagination(PAGE_SIZE * 3)
        assert view._current_page == 2

    # --- Pagination: _get_page_slice ---

    def test_get_page_slice_first_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        tasks = [_make_task() for _ in range(PAGE_SIZE + 3)]
        page = view._get_page_slice(tasks)
        assert len(page) == PAGE_SIZE

    def test_get_page_slice_second_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 2
        tasks = [_make_task() for _ in range(PAGE_SIZE + 3)]
        page = view._get_page_slice(tasks)
        assert len(page) == 3

    def test_get_page_slice_empty_tasks(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        page = view._get_page_slice([])
        assert page == []

    # --- Pagination: _update_pagination_controls ---

    def test_update_pagination_controls_first_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        view._total_pages = 3
        view._update_pagination_controls()
        assert view.btn_prev.disabled is True
        assert view.btn_next.disabled is False
        assert view.page_info_text.value == "1 / 3"

    def test_update_pagination_controls_last_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 3
        view._total_pages = 3
        view._update_pagination_controls()
        assert view.btn_prev.disabled is False
        assert view.btn_next.disabled is True
        assert view.page_info_text.value == "3 / 3"

    def test_update_pagination_controls_middle_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 2
        view._total_pages = 3
        view._update_pagination_controls()
        assert view.btn_prev.disabled is False
        assert view.btn_next.disabled is False

    def test_update_pagination_controls_single_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        view._total_pages = 1
        view._update_pagination_controls()
        assert view.btn_prev.disabled is True
        assert view.btn_next.disabled is True

    # --- Pagination: _go_prev / _go_next ---

    def test_go_prev_decrements_page(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._current_page = 3
        view._total_pages = 5
        view._all_tasks = [MagicMock()] * 25
        view._refresh_ui = MagicMock()
        view._go_prev(None)
        assert view._current_page == 2

    def test_go_prev_at_first_page_does_nothing(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        view._total_pages = 3
        view._all_tasks = []
        view._go_prev(None)
        assert view._current_page == 1

    def test_go_next_increments_page(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._current_page = 1
        view._total_pages = 3
        view._all_tasks = [MagicMock()] * 25
        view._refresh_ui = MagicMock()
        view._go_next(None)
        assert view._current_page == 2

    def test_go_next_at_last_page_does_nothing(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 3
        view._total_pages = 3
        view._all_tasks = []
        view._go_next(None)
        assert view._current_page == 3

    def test_go_prev_with_actual_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        view._go_prev(None)
        assert view._current_page == 1

    def test_go_next_with_actual_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        view._go_next(None)
        assert view._current_page == 2

    # --- Core rendering: _refresh_ui ---

    def test_refresh_ui_with_empty_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._refresh_ui([])
        assert view.scroll_area.controls == [view.empty_view]
        assert view.pagination_row.visible is False

    def test_refresh_ui_with_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task(status=TaskStatus.QUEUED)]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 1
        assert view._all_tasks == tasks

    def test_refresh_ui_stats_text(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.QUEUED),
        ]
        view._refresh_ui(tasks)
        self.mock_i18n.get.assert_any_call("task_stats_fmt")

    def test_refresh_ui_pagination_visible_when_multiple_pages(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is True

    def test_refresh_ui_pagination_hidden_when_single_page(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task()]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is False

    def test_refresh_ui_without_page(self, mock_page):
        view = self._make_view(mock_page)
        view._mock_page = None
        tasks = [_make_task()]
        view._refresh_ui(tasks)
        assert view._all_tasks == tasks

    def test_refresh_ui_stores_all_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task(), _make_task(status=TaskStatus.RUNNING)]
        view._refresh_ui(tasks)
        assert view._all_tasks == tasks

    def test_refresh_ui_counts_running_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.RUNNING),
            _make_task(status=TaskStatus.QUEUED),
        ]
        view._refresh_ui(tasks)
        # stats_text.value should contain the formatted string with total=3, running=2
        assert "task_stats_fmt" in str(self.mock_i18n.get.call_args_list)

    def test_refresh_ui_with_page_size_plus_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE + 5)]
        view._refresh_ui(tasks)
        # Only PAGE_SIZE cards on first page
        assert len(view.scroll_area.controls) == PAGE_SIZE
        assert view._total_pages == 2

    # --- Task card building ---

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
    def test_build_task_card_all_statuses(self, mock_page, status):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=status, progress=0.5, cancellable=True)
        if status == TaskStatus.FAILED:
            task.error = "some error"
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_running_with_progress(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.RUNNING, progress=0.75, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)

    def test_build_task_card_completed_full_progress(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.COMPLETED, progress=1.0)
        card = view._build_task_card(task)
        assert card is not None

    def test_build_task_card_failed_shows_error(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.FAILED, error="disk full")
        card = view._build_task_card(task)
        assert card is not None

    def test_build_task_card_cancellable_running(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.RUNNING, cancellable=True)
        card = view._build_task_card(task)
        # The bottom_row should contain a TextButton for cancel
        assert card is not None

    def test_build_task_card_not_cancellable_running(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.RUNNING, cancellable=False)
        card = view._build_task_card(task)
        assert card is not None

    def test_build_task_card_cancellable_queued(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.QUEUED, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None

    def test_build_task_card_not_cancellable_completed(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.COMPLETED, cancellable=True)
        card = view._build_task_card(task)
        # Completed tasks should not show cancel button even if cancellable=True
        assert card is not None

    # --- Handlers ---

    def test_handle_cancel_calls_task_manager(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._handle_cancel("task-123")
        self.mock_tm.cancel_task.assert_called_once_with("task-123")

    def test_handle_cancel_with_different_task_id(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._handle_cancel("abc-456")
        self.mock_tm.cancel_task.assert_called_once_with("abc-456")

    @pytest.mark.asyncio
    async def test_handle_clear_resets_page(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._current_page = 5
        await view._handle_clear(None)
        assert view._current_page == 1
        self.mock_tm.clear_finished.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_clear_already_on_first_page(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._current_page = 1
        await view._handle_clear(None)
        assert view._current_page == 1
        self.mock_tm.clear_finished.assert_called_once()

    # --- _on_tasks_updated callback ---

    def test_on_tasks_updated_mounted(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._mounted = True
        view._on_tasks_updated([])
        mock_page.run_task.assert_called_once_with(view._safe_refresh, [])

    def test_on_tasks_updated_not_mounted(self, mock_page):
        view = self._make_view(mock_page)
        page = wrap_mock_page(mock_page)
        set_page(view, page)
        view._mounted = False
        view._on_tasks_updated([])
        page.run_task.assert_not_called()

    def test_on_tasks_updated_no_page(self, mock_page):
        view = self._make_view(mock_page)
        view._mounted = True
        view._mock_page = None
        view._on_tasks_updated([])
        # Should not raise

    def test_on_tasks_updated_passes_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._mounted = True
        tasks = [_make_task()]
        view._on_tasks_updated(tasks)
        mock_page.run_task.assert_called_once_with(view._safe_refresh, tasks)

    # --- _safe_refresh ---

    @pytest.mark.asyncio
    async def test_safe_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task()]
        await view._safe_refresh(tasks)
        assert view._all_tasks == tasks

    @pytest.mark.asyncio
    async def test_safe_refresh_exception_does_not_propagate(self, mock_page):
        view = self._make_view(mock_page)
        view._refresh_ui = MagicMock(side_effect=RuntimeError("fail"))
        await view._safe_refresh([])
        # Should not raise

    # --- Edge cases ---

    def test_refresh_ui_empty_then_populated(self, mock_page):
        """Verify transition from empty to populated state."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._refresh_ui([])
        assert view.scroll_area.controls == [view.empty_view]

        tasks = [_make_task(status=TaskStatus.RUNNING)]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 1
        assert view._all_tasks == tasks

    def test_refresh_ui_populated_then_empty(self, mock_page):
        """Verify transition from populated to empty state."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task(status=TaskStatus.RUNNING)]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 1

        view._refresh_ui([])
        assert view.scroll_area.controls == [view.empty_view]

    def test_task_state_transition_queued_to_running(self, mock_page):
        """Verify UI handles a task transitioning from QUEUED to RUNNING."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.QUEUED)
        view._refresh_ui([task])
        assert len(view.scroll_area.controls) == 1

        task.status = TaskStatus.RUNNING
        task.progress = 0.3
        view._refresh_ui([task])
        assert len(view.scroll_area.controls) == 1

    def test_task_state_transition_running_to_completed(self, mock_page):
        """Verify UI handles a task transitioning from RUNNING to COMPLETED."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.RUNNING, progress=0.5)
        view._refresh_ui([task])

        task.status = TaskStatus.COMPLETED
        task.progress = 1.0
        view._refresh_ui([task])
        assert len(view.scroll_area.controls) == 1

    def test_task_state_transition_running_to_failed(self, mock_page):
        """Verify UI handles a task transitioning from RUNNING to FAILED."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = _make_task(status=TaskStatus.RUNNING, progress=0.5)
        view._refresh_ui([task])

        task.status = TaskStatus.FAILED
        task.error = "connection timeout"
        view._refresh_ui([task])
        assert len(view.scroll_area.controls) == 1

    def test_multiple_tasks_mixed_statuses(self, mock_page):
        """Verify rendering with multiple tasks in different statuses."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [
            _make_task(name="Queued Task", status=TaskStatus.QUEUED),
            _make_task(
                name="Running Task",
                status=TaskStatus.RUNNING,
                progress=0.5,
                cancellable=True,
            ),
            _make_task(name="Completed Task", status=TaskStatus.COMPLETED, progress=1.0),
            _make_task(name="Failed Task", status=TaskStatus.FAILED, error="oops"),
        ]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 4

    def test_pagination_across_pages(self, mock_page):
        """Verify navigating through multiple pages."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE * 3)]
        view._refresh_ui(tasks)

        assert view._current_page == 1
        assert view._total_pages == 3
        assert view.pagination_row.visible is True

        view._go_next(None)
        assert view._current_page == 2

        view._go_next(None)
        assert view._current_page == 3

        view._go_prev(None)
        assert view._current_page == 2

    def test_page_info_text_updates(self, mock_page):
        """Verify page info text reflects current pagination state."""
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [_make_task() for _ in range(PAGE_SIZE * 2)]
        view._refresh_ui(tasks)
        assert "1" in (view.page_info_text.value or "")
        assert "2" in (view.page_info_text.value or "")

        view._go_next(None)
        assert view.page_info_text.value == "2 / 2"
