import asyncio
import contextlib
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from services.task_manager import TaskStatus
from tests.unit.ui.conftest import set_page, wrap_mock_page
from ui.views.task_center_view import (
    PAGE_SIZE,
    TaskCenterView,
    _format_time,
    _get_status_color,
    _get_status_label,
)


def _build_mock_task_manager():
    m = MagicMock()
    m.get_all_tasks.return_value = []
    m.subscribe = MagicMock()
    m.unsubscribe = MagicMock()
    m.cancel_task = MagicMock()
    m.clear_finished = MagicMock()
    return m


class TestSettingsView:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_view.I18n", self.mock_i18n),
            patch("ui.views.settings_view.AppColors", self.mock_ac),
            patch("ui.views.settings_view.DataSourceTab", MagicMock()),
            patch("ui.views.settings_view.DatabaseTab", MagicMock()),
            patch("ui.views.settings_view.AIBrainTab", MagicMock()),
            patch("ui.views.settings_view.AutomationTab", MagicMock()),
            patch("ui.views.settings_view.NotificationsTab", MagicMock()),
            patch("ui.views.settings_view.SystemTab", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_view(self):
        from ui.views.settings_view import SettingsView

        return SettingsView()

    def test_instantiation_creates_tab_contents(self):
        view = self._make_view()
        assert len(view.tab_contents) == len(view.TAB_CONFIG)

    def test_instantiation_creates_tab_buttons(self):
        view = self._make_view()
        assert len(view.tab_buttons) == len(view.TAB_CONFIG)

    def test_initial_tab_index_is_zero(self):
        view = self._make_view()
        assert view.current_tab_index == 0

    def test_on_tab_click_updates_current_tab_index(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        e = MagicMock()
        e.control.data = "2"
        view._on_tab_click(e)
        assert view.current_tab_index == 2

    def test_on_tab_click_with_invalid_index_does_nothing(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        original = view.current_tab_index
        e = MagicMock()
        e.control.data = "99"
        view._on_tab_click(e)
        assert view.current_tab_index == original

    def test_on_tab_click_with_non_numeric_data_does_nothing(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        original = view.current_tab_index
        e = MagicMock()
        e.control.data = "abc"
        view._on_tab_click(e)
        assert view.current_tab_index == original

    def test_show_snack_with_show_toast_info(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.show_snack("hello")
        mock_page.show_toast.assert_called_once_with("hello", type="info")

    def test_show_snack_with_show_toast_error(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.show_snack("fail", color=ft.Colors.RED)
        mock_page.show_toast.assert_called_once_with("fail", type="error")

    def test_show_snack_with_show_toast_success(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.show_snack("ok", color=ft.Colors.GREEN)
        mock_page.show_toast.assert_called_once_with("ok", type="success")

    def test_show_snack_with_show_toast_warning_amber(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.show_snack("warn", color=ft.Colors.AMBER)
        mock_page.show_toast.assert_called_once_with("warn", type="warning")

    def test_show_snack_with_show_toast_warning_orange(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.show_snack("warn", color=ft.Colors.ORANGE)
        mock_page.show_toast.assert_called_once_with("warn", type="warning")

    def test_show_snack_without_page_does_nothing(self):
        view = self._make_view()
        view.show_snack("hello")

    def test_show_snack_fallback_snackbar(self):
        view = self._make_view()
        page = MagicMock(spec=["overlay", "update"])
        page.overlay = []
        set_page(view, page)
        view.show_snack("fallback", color=ft.Colors.RED)
        assert any(isinstance(o, ft.SnackBar) for o in page.overlay)

    def test_on_unmount_unsubscribes_i18n(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        view._on_unmount()
        self.mock_i18n.unsubscribe.assert_called_with(view.refresh_locale)

    def test_on_unmount_cascades_to_child_tabs(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        mock_tab = MagicMock()
        view.tab_contents = [mock_tab]
        view._on_unmount()
        mock_tab._on_unmount.assert_called_once()

    def test_update_theme_propagates_to_tabs(self, mock_page):
        view = self._make_view()
        set_page(view, mock_page)
        mock_tab = MagicMock()
        view.tab_contents = [mock_tab]
        view.update_theme()
        mock_tab.update_theme.assert_called_once()


class TestTaskCenterView:
    patches: list

    def test_format_time_with_none(self):
        assert _format_time(None) == "--:--"

    def test_format_time_with_valid_datetime(self):
        dt = datetime.datetime(2025, 1, 15, 14, 30, 45)
        assert _format_time(dt) == "14:30:45"

    def test_get_status_label_returns_key(self):
        with patch("ui.views.task_center_view.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, *a, **kw: key
            result = _get_status_label(TaskStatus.RUNNING)
            assert result == "task_status_running"

    def test_get_status_color_returns_known_color(self):
        color = _get_status_color(TaskStatus.RUNNING)
        assert color is not None

    def test_get_status_color_returns_secondary_for_unknown(self):
        color = _get_status_color("unknown_status")
        assert color is not None

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

    def test_instantiation_creates_sub_components(self, mock_page):
        view = self._make_view(mock_page)
        assert view.btn_prev is not None
        assert view.btn_next is not None
        assert view.scroll_area is not None
        assert view.pagination_row is not None

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

    def test_handle_cancel_calls_task_manager(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._handle_cancel("task-123")
        self.mock_tm.cancel_task.assert_called_once_with("task-123")

    @pytest.mark.asyncio
    async def test_handle_clear_resets_page(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view._current_page = 5
        await view._handle_clear(None)
        assert view._current_page == 1
        self.mock_tm.clear_finished.assert_called_once()

    def test_did_mount_subscribes_and_refreshes(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view.did_mount()
        self.mock_tm.subscribe.assert_called_once()
        self.mock_tm.get_all_tasks.assert_called_once()

    def test_will_unmount_unsubscribes(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view.will_unmount()
        self.mock_tm.unsubscribe.assert_called_once()

    def _make_task(self, status=TaskStatus.QUEUED, **kwargs):
        from services.task_manager import AppTask

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

    def test_refresh_ui_with_empty_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._refresh_ui([])
        assert view.scroll_area.controls == [view.empty_view]
        assert view.pagination_row.visible is False

    def test_refresh_ui_with_tasks(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task(status=TaskStatus.QUEUED)]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 1
        assert view._all_tasks == tasks

    def test_refresh_ui_stats_text(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [
            self._make_task(status=TaskStatus.RUNNING),
            self._make_task(status=TaskStatus.QUEUED),
        ]
        view._refresh_ui(tasks)
        self.mock_i18n.get.assert_any_call("task_stats_fmt")

    def test_refresh_ui_pagination_visible_when_multiple_pages(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is True

    def test_refresh_ui_pagination_hidden_when_single_page(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task()]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is False

    def test_refresh_ui_without_page(self, mock_page):
        view = self._make_view(mock_page)
        view._Control__page = None
        tasks = [self._make_task()]
        view._refresh_ui(tasks)
        assert view._all_tasks == tasks

    def test_get_page_slice_first_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 1
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 3)]
        page = view._get_page_slice(tasks)
        assert len(page) == PAGE_SIZE

    def test_get_page_slice_second_page(self, mock_page):
        view = self._make_view(mock_page)
        view._current_page = 2
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 3)]
        page = view._get_page_slice(tasks)
        assert len(page) == 3

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

    def test_build_task_card_running(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.RUNNING, progress=0.5, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_completed(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.COMPLETED, progress=1.0)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_failed(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.FAILED, error="some error")
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_cancelled(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.CANCELLED, progress=0.3)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_interrupted(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.INTERRUPTED, progress=0.4)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_queued(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.QUEUED, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_not_cancellable_running(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        task = self._make_task(status=TaskStatus.RUNNING, cancellable=False)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_on_tasks_updated_mounted(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        view._mounted = True
        view._on_tasks_updated([])
        mock_page.run_task.assert_called_once()

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
        view._Control__page = None
        view._on_tasks_updated([])

    @pytest.mark.asyncio
    async def test_safe_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task()]
        await view._safe_refresh(tasks)
        assert view._all_tasks == tasks

    @pytest.mark.asyncio
    async def test_safe_refresh_exception(self, mock_page):
        view = self._make_view(mock_page)
        view._refresh_ui = MagicMock(side_effect=RuntimeError("fail"))
        await view._safe_refresh([])

    def test_go_prev_with_actual_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        view._go_prev(None)
        assert view._current_page == 1

    def test_go_next_with_actual_refresh(self, mock_page):
        view = self._make_view(mock_page)
        set_page(view, wrap_mock_page(mock_page))
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        view._go_next(None)
        assert view._current_page == 2


class TestHomeView:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_vm = MagicMock()
        self.mock_vm.init = MagicMock()
        self.mock_vm.dispose = MagicMock()
        self.mock_vm.clear_state = MagicMock()
        self.patches = [
            patch("ui.views.home_view.I18n", self.mock_i18n),
            patch("ui.views.home_view.AppColors", self.mock_ac),
            patch("ui.views.home_view.HomeViewModel", return_value=self.mock_vm),
            patch("ui.views.home_view.MarketDashboard", MagicMock()),
            patch("ui.views.home_view.NewsFeed", MagicMock()),
            patch("ui.views.home_view.CacheManager"),
            patch("ui.views.home_view.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_view(self):
        from ui.views.home_view import HomeView

        return HomeView()

    def test_instantiation_creates_sub_components(self):
        view = self._make_view()
        assert view.dashboard is not None
        assert view.news_feed is not None
        assert view.header is not None

    def test_set_visible_toggles_flag(self):
        view = self._make_view()
        view._is_visible = True
        view.set_visible(False)
        assert view._is_visible is False

    def test_set_visible_same_value_no_change(self):
        view = self._make_view()
        view._is_visible = True
        view.set_visible(True)
        assert view._is_visible is True

    def test_on_broadcast_message_cache_cleared(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._is_mounted = True
        view._on_broadcast_message("cache_cleared")
        self.mock_vm.clear_state.assert_called_once()

    def test_on_broadcast_message_ignores_other_messages(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._is_mounted = True
        view._on_broadcast_message("other_message")
        self.mock_vm.clear_state.assert_not_called()

    def test_run_if_visible_skips_when_not_visible(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._is_visible = False
        view._is_mounted = True
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_not_called()

    def test_run_if_visible_skips_when_not_mounted(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._is_visible = True
        view._is_mounted = False
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_not_called()

    def test_run_if_visible_executes_when_visible_and_mounted(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._is_visible = True
        view._is_mounted = True
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_called_with(task_func, None)

    def test_did_mount_subscribes_pubsub(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view.did_mount()
        mock_page.pubsub.subscribe.assert_called_once_with(view._on_broadcast_message)
        assert view._pubsub_subscribed is True

    def test_will_unmount_unsubscribes_pubsub(self, mock_page):
        view = self._make_view()
        set_page(view, wrap_mock_page(mock_page))
        view._pubsub_subscribed = True
        view.will_unmount()
        mock_page.pubsub.unsubscribe.assert_called_once_with(view._on_broadcast_message)
        assert view._pubsub_subscribed is False


class TestAppLayout:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.app_layout.I18n", self.mock_i18n),
            patch("ui.app_layout.AppColors", self.mock_ac),
            patch("ui.app_layout.ScreenerView", MagicMock()),
            patch("ui.app_layout.HomeView", MagicMock()),
            patch("ui.app_layout.DataExplorerView", MagicMock()),
            patch("ui.app_layout.TaskCenterView", MagicMock()),
            patch("ui.app_layout.SettingsView", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_layout(self, mock_page):
        from ui.app_layout import AppLayout

        return AppLayout(mock_page)

    def test_instantiation_creates_nav_rail(self, mock_page):
        layout = self._make_layout(mock_page)
        assert layout.nav_rail is not None

    def test_instantiation_creates_body(self, mock_page):
        layout = self._make_layout(mock_page)
        assert layout.body is not None

    def test_get_view_creates_and_caches(self, mock_page):
        layout = self._make_layout(mock_page)
        view = layout._get_view(0)
        assert 0 in layout._view_cache
        assert view is layout._view_cache[0]

    def test_get_view_returns_cached_on_second_call(self, mock_page):
        layout = self._make_layout(mock_page)
        view1 = layout._get_view(0)
        view2 = layout._get_view(0)
        assert view1 is view2

    def test_change_tab_same_index_does_nothing(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout.change_tab(0)
        assert layout._pending_tab_index is None

    def test_change_tab_different_index_sets_pending(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout.change_tab(2)
        assert layout._pending_tab_index == 2

    def test_update_theme_propagates_to_cached_views(self, mock_page):
        layout = self._make_layout(mock_page)
        mock_view = MagicMock()
        layout._view_cache[0] = mock_view
        layout.update_theme()
        mock_view.update_theme.assert_called_once()

    def test_update_theme_skips_views_without_method(self, mock_page):
        layout = self._make_layout(mock_page)
        plain_view = MagicMock(spec=[])
        layout._view_cache[0] = plain_view
        layout.update_theme()

    def test_will_unmount_unsubscribes_appcolors(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.will_unmount()
        self.mock_ac.unsubscribe.assert_called_once_with(layout.update_theme)

    def test_will_unmount_unsubscribes_i18n(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with(layout._on_locale_change)

    def test_get_view_screener(self, mock_page):
        layout = self._make_layout(mock_page)
        view = layout._get_view(1)
        assert 1 in layout._view_cache
        assert view is layout._view_cache[1]

    def test_get_view_data(self, mock_page):
        layout = self._make_layout(mock_page)
        view = layout._get_view(2)
        assert 2 in layout._view_cache
        assert view is layout._view_cache[2]

    def test_get_view_tasks(self, mock_page):
        layout = self._make_layout(mock_page)
        view = layout._get_view(3)
        assert 3 in layout._view_cache
        assert view is layout._view_cache[3]

    def test_get_view_settings(self, mock_page):
        layout = self._make_layout(mock_page)
        view = layout._get_view(4)
        assert 4 in layout._view_cache
        assert view is layout._view_cache[4]

    def test_get_view_unknown_index(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._get_view(99)
        assert 99 in layout._view_cache

    def test_on_nav_change_calls_change_tab(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.change_tab = MagicMock()
        e = MagicMock()
        e.control.selected_index = 2
        layout._on_nav_change(e)
        layout.change_tab.assert_called_once_with(2)

    def test_change_tab_cancels_previous_debounce(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        mock_task = MagicMock()
        layout._debounce_task = mock_task
        layout.change_tab(2)
        mock_task.cancel.assert_called_once()
        assert layout._pending_tab_index == 2

    def test_change_tab_cancels_previous_debounce_rapid(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        first_task = MagicMock()
        layout._debounce_task = first_task
        with patch("ui.app_layout.asyncio.sleep", new_callable=AsyncMock):
            layout.change_tab(1)
        first_task.cancel.assert_called_once()

    def test_subscribe_events_subscribes_i18n(self, mock_page):
        layout = self._make_layout(mock_page)
        self.mock_i18n.subscribe.assert_called_with(layout._on_locale_change)

    @pytest.mark.asyncio
    async def test_execute_tab_switch_changes_tab(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 2
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        await layout._execute_tab_switch()
        assert layout._current_tab_index == 2
        assert layout.nav_rail.selected_index == 2
        layout.body.update.assert_called_once()
        layout.nav_rail.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_tab_switch_returns_on_cancelled(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 2
        with pytest.raises(asyncio.CancelledError):
            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                await layout._execute_tab_switch()
        assert layout._current_tab_index == 0

    @pytest.mark.asyncio
    async def test_execute_tab_switch_returns_if_pending_none(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = None
        await layout._execute_tab_switch()
        assert layout._current_tab_index == 0

    @pytest.mark.asyncio
    async def test_execute_tab_switch_returns_if_same_index(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 0
        await layout._execute_tab_switch()
        assert layout._current_tab_index == 0

    @pytest.mark.asyncio
    async def test_execute_tab_switch_sets_home_visible_false(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 1
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        mock_home = layout._get_view(0)
        await layout._execute_tab_switch()
        mock_home.set_visible.assert_called_once_with(False)

    @pytest.mark.asyncio
    async def test_execute_tab_switch_sets_home_visible_true(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 1
        layout._pending_tab_index = 0
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        mock_home = layout._get_view(0)
        await layout._execute_tab_switch()
        mock_home.set_visible.assert_called_once_with(True)

    @pytest.mark.asyncio
    async def test_execute_tab_switch_home_without_set_visible(self, mock_page):
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 2
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        plain_home = MagicMock(spec=[])
        layout._view_cache[0] = plain_home
        await layout._execute_tab_switch()
        assert layout._current_tab_index == 2

    @pytest.mark.asyncio
    async def test_run_strategy_from_home_switches_tab(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        FakeSV = type(
            "FakeScreenerView",
            (),
            {
                "__init__": lambda self, *a, **kw: None,
                "select_and_run_strategy": AsyncMock(),
            },
        )
        with patch("ui.app_layout.ScreenerView", FakeSV):
            layout._view_cache[1] = FakeSV()
            await layout.run_strategy_from_home("test_strategy")
            assert layout._current_tab_index == 1

    @pytest.mark.asyncio
    async def test_run_strategy_from_home_calls_select_and_run(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()

        FakeSV = type("FakeScreenerView", (), {"__init__": lambda self, *a, **kw: None})
        with patch("ui.app_layout.ScreenerView", FakeSV):
            mock_screener = MagicMock(spec=FakeSV)
            mock_screener.select_and_run_strategy = AsyncMock()
            layout._view_cache[1] = mock_screener
            await layout.run_strategy_from_home("my_strategy")
            mock_screener.select_and_run_strategy.assert_called_once_with("my_strategy")

    @pytest.mark.asyncio
    async def test_run_strategy_from_home_cancels_existing_debounce(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        mock_task = MagicMock()
        layout._debounce_task = mock_task
        FakeSV = type(
            "FakeScreenerView",
            (),
            {
                "__init__": lambda self, *a, **kw: None,
                "select_and_run_strategy": AsyncMock(),
            },
        )
        with patch("ui.app_layout.ScreenerView", FakeSV):
            layout._view_cache[1] = FakeSV()
            await layout.run_strategy_from_home("test_strategy")
            mock_task.cancel.assert_called_once()
