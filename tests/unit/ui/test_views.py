import asyncio
import contextlib
import datetime
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from services.task_manager import TaskStatus
from tests.unit.ui.conftest import wrap_mock_page
from ui.views.home_view import logger as home_view_logger
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
        view.page = wrap_mock_page(mock_page)
        e = MagicMock()
        e.control.data = "2"
        view._on_tab_click(e)
        assert view.current_tab_index == 2

    def test_on_tab_click_with_invalid_index_does_nothing(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        original = view.current_tab_index
        e = MagicMock()
        e.control.data = "99"
        view._on_tab_click(e)
        assert view.current_tab_index == original

    def test_on_tab_click_with_non_numeric_data_does_nothing(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        original = view.current_tab_index
        e = MagicMock()
        e.control.data = "abc"
        view._on_tab_click(e)
        assert view.current_tab_index == original

    def test_show_snack_with_show_toast_info(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.show_snack("hello")
        mock_page.show_toast.assert_called_once_with("hello", type="info")

    def test_show_snack_with_show_toast_error(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.show_snack("fail", color=ft.Colors.RED)
        mock_page.show_toast.assert_called_once_with("fail", type="error")

    def test_show_snack_with_show_toast_success(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.show_snack("ok", color=ft.Colors.GREEN)
        mock_page.show_toast.assert_called_once_with("ok", type="success")

    def test_show_snack_with_show_toast_warning_amber(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.show_snack("warn", color=ft.Colors.AMBER)
        mock_page.show_toast.assert_called_once_with("warn", type="warning")

    def test_show_snack_with_show_toast_warning_orange(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.show_snack("warn", color=ft.Colors.ORANGE)
        mock_page.show_toast.assert_called_once_with("warn", type="warning")

    def test_show_snack_without_page_does_nothing(self):
        view = self._make_view()
        view.show_snack("hello")

    def test_show_snack_fallback_snackbar(self):
        view = self._make_view()
        page = MagicMock(spec=["overlay", "update", "show_dialog"])
        page.overlay = []
        page.show_dialog.side_effect = lambda snack: page.overlay.append(snack)
        view.page = page
        view.show_snack("fallback", color=ft.Colors.RED)
        assert any(isinstance(o, ft.SnackBar) for o in page.overlay)

    def test_on_unmount_unsubscribes_i18n(self, mock_page):
        view = self._make_view()
        view.page = mock_page
        view._on_unmount()
        self.mock_i18n.unsubscribe.assert_called_with(view.refresh_locale)

    def test_on_unmount_cascades_to_child_tabs(self, mock_page):
        """§5.8 规范 6：_on_unmount 应级联调用子 tab 的 will_unmount（优先）或 _on_unmount（兼容）"""
        view = self._make_view()
        view.page = mock_page
        mock_tab = MagicMock()
        view.tab_contents = [mock_tab]
        view._on_unmount()
        # 优先调用 will_unmount（规范要求），MagicMock 同时具备两者，will_unmount 优先
        mock_tab.will_unmount.assert_called_once()

    def test_update_theme_propagates_to_tabs(self, mock_page):
        view = self._make_view()
        view.page = mock_page
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
        view.page = wrap_mock_page(mock_page)
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
        view.page = wrap_mock_page(mock_page)
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
        self.mock_tm.subscribe.assert_called_once_with(view._on_tasks_updated)
        self.mock_tm.get_all_tasks.assert_called_once()
        assert view._mounted is True  # 副作用：挂载状态变更

    def test_will_unmount_unsubscribes(self, mock_page):
        view = self._make_view(mock_page)
        view.task_manager = self.mock_tm
        view.will_unmount()
        self.mock_tm.unsubscribe.assert_called_once_with(view._on_tasks_updated)
        assert view._mounted is False  # 副作用：挂载状态变更

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
        view.page = wrap_mock_page(mock_page)
        view._refresh_ui([])
        assert view.scroll_area.controls == [view.empty_view]
        assert view.pagination_row.visible is False

    def test_refresh_ui_with_tasks(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        tasks = [self._make_task(status=TaskStatus.QUEUED)]
        view._refresh_ui(tasks)
        assert len(view.scroll_area.controls) == 1
        assert view._all_tasks == tasks

    def test_refresh_ui_stats_text(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        tasks = [
            self._make_task(status=TaskStatus.RUNNING),
            self._make_task(status=TaskStatus.QUEUED),
        ]
        view._refresh_ui(tasks)
        self.mock_i18n.get.assert_any_call("task_stats_fmt")

    def test_refresh_ui_pagination_visible_when_multiple_pages(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is True

    def test_refresh_ui_pagination_hidden_when_single_page(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        tasks = [self._make_task()]
        view._refresh_ui(tasks)
        assert view.pagination_row.visible is False

    def test_refresh_ui_without_page(self, mock_page):
        view = self._make_view(mock_page)
        view._mock_page = None
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
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.RUNNING, progress=0.5, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_completed(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.COMPLETED, progress=1.0)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_failed(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.FAILED, error="some error")
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_cancelled(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.CANCELLED, progress=0.3)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_interrupted(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.INTERRUPTED, progress=0.4)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_queued(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.QUEUED, cancellable=True)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_build_task_card_not_cancellable_running(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        task = self._make_task(status=TaskStatus.RUNNING, cancellable=False)
        card = view._build_task_card(task)
        assert card is not None
        assert isinstance(card, ft.Container)
        assert card.content is not None

    def test_on_tasks_updated_mounted(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
        view._mounted = True
        view._on_tasks_updated([])
        mock_page.run_task.assert_called_once_with(view._safe_refresh, [])

    def test_on_tasks_updated_not_mounted(self, mock_page):
        view = self._make_view(mock_page)
        page = wrap_mock_page(mock_page)
        view.page = page
        view._mounted = False
        view._on_tasks_updated([])
        page.run_task.assert_not_called()

    def test_on_tasks_updated_no_page(self, mock_page):
        view = self._make_view(mock_page)
        view._mounted = True
        view._mock_page = None
        view._on_tasks_updated([])

    @pytest.mark.asyncio
    async def test_safe_refresh(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
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
        view.page = wrap_mock_page(mock_page)
        tasks = [self._make_task() for _ in range(PAGE_SIZE + 1)]
        view._refresh_ui(tasks)
        view._go_prev(None)
        assert view._current_page == 1

    def test_go_next_with_actual_refresh(self, mock_page):
        view = self._make_view(mock_page)
        view.page = wrap_mock_page(mock_page)
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
        view.page = wrap_mock_page(mock_page)
        view._is_mounted = True
        view._on_broadcast_message("cache_cleared")
        self.mock_vm.clear_state.assert_called_once()

    def test_on_broadcast_message_ignores_other_messages(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view._is_mounted = True
        view._on_broadcast_message("other_message")
        self.mock_vm.clear_state.assert_not_called()

    def test_run_if_visible_skips_when_not_visible(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view._is_visible = False
        view._is_mounted = True
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_not_called()

    def test_run_if_visible_skips_when_not_mounted(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view._is_visible = True
        view._is_mounted = False
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_not_called()

    def test_run_if_visible_executes_when_visible_and_mounted(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view._is_visible = True
        view._is_mounted = True
        task_func = MagicMock()
        view._run_if_visible(task_func, "test")
        mock_page.run_task.assert_called_with(task_func, None)

    def test_did_mount_subscribes_pubsub(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.did_mount()
        mock_page.pubsub.subscribe.assert_called_once_with(view._on_broadcast_message)
        assert view._pubsub_subscribed is True

    def test_will_unmount_unsubscribes_pubsub(self, mock_page):
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view._pubsub_subscribed = True
        view.will_unmount()
        mock_page.pubsub.unsubscribe.assert_called_once_with(view._on_broadcast_message)
        assert view._pubsub_subscribed is False

    def test_refresh_locale_cascades_to_sub_components(self, mock_page):
        """§5.8 规范 6：refresh_locale 必须级联调用 dashboard.update_locale 和 news_feed.update_locale。"""
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        view.update = MagicMock()
        # vm.last_market_data 必须返回 dict 以便 .get("date", "--") 与 .get("stale", False) 正常工作
        self.mock_vm.last_market_data = {}

        view.refresh_locale()

        view.dashboard.update_locale.assert_called_once()
        view.news_feed.update_locale.assert_called_once()
        view.update.assert_called_once()
        # 头部文案也应被刷新
        self.mock_i18n.get.assert_any_call("home_title")
        self.mock_i18n.get.assert_any_call("home_live_news")
        self.mock_i18n.get.assert_any_call("home_refresh")

    def test_refresh_locale_swallows_exception(self, mock_page, caplog):
        """refresh_locale 异常时不应抛出，应降级为 logger.warning。"""
        view = self._make_view()
        view.page = wrap_mock_page(mock_page)
        # 强制 I18n.get 抛异常以触发 try/except
        self.mock_i18n.get.side_effect = RuntimeError("i18n boom")

        with caplog.at_level(logging.WARNING, logger=home_view_logger.name):
            # 不应抛出异常
            view.refresh_locale()

        assert any("refresh_locale failed" in r.message and "i18n boom" in r.message for r in caplog.records)


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

    def test_app_layout_body_no_max_width(self, mock_page):
        """回归测试：AppLayout.body 必须无 max_width 约束（v4.3 规范 2）。"""
        layout = self._make_layout(mock_page)
        assert not getattr(layout.body, "max_width", None), "AppLayout.body must not have max_width constraint"

    def test_compact_height_threshold_constant(self):
        """COMPACT_HEIGHT_THRESHOLD 应为 560（基于 min_height=720 估算）。"""
        from ui.app_layout import COMPACT_HEIGHT_THRESHOLD

        assert COMPACT_HEIGHT_THRESHOLD == 560
        assert COMPACT_HEIGHT_THRESHOLD > 0

    @pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5])
    def test_get_view_returns_object_and_caches(self, mock_page, index):
        """_get_view 返回非 None 对象，二次调用返回同一对象（行为断言）。

        覆盖 app_layout.py:191-219 的 _get_view 方法：
        - 193-194 行：缓存命中路径
        - 200-213 行：按 index 创建 View
        - 215 行：写入缓存
        """
        layout = self._make_layout(mock_page)
        view1 = layout._get_view(index)
        view2 = layout._get_view(index)
        assert view1 is not None
        assert view1 is view2  # 缓存生效：同一索引返回同一对象

    def test_get_view_unknown_index_returns_object(self, mock_page):
        """未知索引也应返回对象（不崩溃）。

        覆盖 app_layout.py:213 行：`view = ft.Text(I18n.get("view_unknown"))`
        """
        layout = self._make_layout(mock_page)
        view = layout._get_view(99)
        assert view is not None

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
        mock_view = layout._get_view(0)  # 通过公共 API 创建并缓存视图
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
        """will_unmount 用 subscription_id 反订阅（§5.8 规范 1）"""
        layout = self._make_layout(mock_page)
        layout.did_mount()  # 先订阅获得 subscription_id
        layout.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
        assert layout._locale_subscription_id is None
        assert layout._mounted is False

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

    def test_did_mount_subscribes_i18n(self, mock_page):
        """构造时不订阅，did_mount 后才订阅（§5.8 规范 1：在 did_mount 中订阅）"""
        layout = self._make_layout(mock_page)
        # 构造完不订阅
        self.mock_i18n.subscribe.assert_not_called()
        layout.did_mount()
        self.mock_i18n.subscribe.assert_called_once_with(layout._on_locale_change)
        assert layout._locale_subscription_id == "sub_id"

    def test_did_mount_is_idempotent(self, mock_page):
        """did_mount 幂等守卫：多次调用不重复订阅"""
        layout = self._make_layout(mock_page)
        layout.did_mount()
        layout.did_mount()
        assert self.mock_i18n.subscribe.call_count == 1

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
            await layout.run_strategy_from_home("test_strategy")
            assert layout._current_tab_index == 1

    @pytest.mark.asyncio
    async def test_run_strategy_from_home_calls_select_and_run(self, mock_page):
        layout = self._make_layout(mock_page)
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        select_and_run_mock = AsyncMock()
        FakeSV = type(
            "FakeScreenerView",
            (),
            {
                "__init__": lambda self, *a, **kw: None,
                "select_and_run_strategy": select_and_run_mock,
            },
        )
        with patch("ui.app_layout.ScreenerView", FakeSV):
            await layout.run_strategy_from_home("my_strategy")
            select_and_run_mock.assert_called_once_with("my_strategy")

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
            await layout.run_strategy_from_home("test_strategy")
            mock_task.cancel.assert_called_once()

    def test_on_locale_change_refreshes_ui_state(self, mock_page):
        """§5.8 规范 1/5/6：_on_locale_change 正向路径刷新 UI 控件文案。

        覆盖 app_layout.py:283-310 的正向路径：
        - page.title / brand_text.value / collapse_btn.tooltip 被刷新
        - nav_rail.destinations[i].label.value 被刷新（V1: label 是 ft.Text 控件）
        - nav_rail.update 被调用
        mock_i18n.get 返回 key 本身，便于断言。
        """
        layout = self._make_layout(mock_page)
        # nav_rail 是真实 ft.NavigationRail，未绑定 page 时 update() 抛 AssertionError
        layout.nav_rail.update = MagicMock()
        layout._on_locale_change()
        # 规范 5：实例属性被刷新
        assert layout.page.title == "app_title"
        assert layout.brand_text.value == "app_brand"
        assert layout.collapse_btn.tooltip == "nav_toggle_collapse"
        # 规范 6：nav_rail.destinations 级联刷新
        nav_keys = ["nav_market", "nav_screener", "nav_backtest", "nav_data", "nav_tasks", "nav_settings"]
        for i, key in enumerate(nav_keys):
            assert layout.nav_rail.destinations[i].label.value == key
        # nav_rail.update 被调用一次
        layout.nav_rail.update.assert_called_once()

    def test_on_locale_change_swallows_exception_and_logs_warning(self, mock_page):
        """§5.8 规范 9：_on_locale_change 异常时降级为 logger.warning，不抛出。"""
        layout = self._make_layout(mock_page)
        # 让 I18n.get 抛异常，触发 _on_locale_change 的 except 分支
        self.mock_i18n.get.side_effect = RuntimeError("test error")
        with patch("ui.app_layout.logger") as mock_logger:
            # 不应抛出异常
            layout._on_locale_change()
            mock_logger.warning.assert_called_once()
            # 验证警告消息包含异常信息与方法名
            # logger 改用 %s 参数化后，格式字符串与方法名、异常参数分别校验
            warning_args = mock_logger.warning.call_args[0]
            assert "_on_locale_change" in warning_args[0]  # 格式字符串含方法名
            assert "test error" in str(warning_args[1])  # 异常参数含错误信息

    # ========== Resize 逻辑测试 (覆盖 app_layout.py:83-84, 93-95, 99-116) ==========

    def test_will_unmount_cancels_resize_debounce_task(self, mock_page):
        """覆盖 app_layout.py:83-84，will_unmount 取消 resize debounce task。"""
        layout = self._make_layout(mock_page)
        mock_resize_task = MagicMock()
        layout._resize_debounce_task = mock_resize_task
        layout.will_unmount()
        mock_resize_task.cancel.assert_called_once()
        assert layout._resize_debounce_task is None

    def test_schedule_resize_cancels_existing_task(self, mock_page):
        """覆盖 schedule_resize 取消已有 task。"""
        layout = self._make_layout(mock_page)
        mock_old_task = MagicMock()
        layout._resize_debounce_task = mock_old_task
        mock_page.run_task = MagicMock(return_value=MagicMock())
        layout.schedule_resize(1280, 800)
        mock_old_task.cancel.assert_called_once()
        # 验证实时尺寸被缓存
        assert layout._current_width == 1280
        assert layout._current_height == 800

    def test_schedule_resize_creates_new_task(self, mock_page):
        """覆盖 schedule_resize 创建新 task。"""
        layout = self._make_layout(mock_page)
        mock_new_task = MagicMock()
        mock_page.run_task = MagicMock(return_value=mock_new_task)
        layout.schedule_resize(1280, 800)
        mock_page.run_task.assert_called_once_with(layout._handle_resize)
        assert layout._resize_debounce_task == mock_new_task

    def test_schedule_resize_preserves_cached_size_when_zero(self, mock_page):
        """schedule_resize 传入 0 时保留缓存的尺寸 (nav 折叠场景)。"""
        layout = self._make_layout(mock_page)
        layout._current_width = 1280
        layout._current_height = 800
        mock_page.run_task = MagicMock(return_value=MagicMock())
        layout.schedule_resize()  # 不传参数，模拟 nav 折叠触发
        assert layout._current_width == 1280  # 缓存值未被覆盖
        assert layout._current_height == 800

    @pytest.mark.asyncio
    async def test_handle_resize_propagates_cancelled_error(self, mock_page):
        """覆盖 CancelledError 必须传播（R2 红线）。"""
        layout = self._make_layout(mock_page)
        with pytest.raises(asyncio.CancelledError):
            with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
                await layout._handle_resize()

    @pytest.mark.asyncio
    async def test_handle_resize_returns_without_page(self, mock_page):
        """覆盖 page 缺失时提前返回。"""
        layout = self._make_layout(mock_page)
        layout.page = None
        mock_view = MagicMock()
        layout._view_cache[0] = mock_view
        layout._current_tab_index = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await layout._handle_resize()
        # page 缺失时不应调用视图的 handle_resize
        mock_view.handle_resize.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_resize_returns_without_cached_view(self, mock_page):
        """覆盖视图未缓存时提前返回。"""
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 99  # 无缓存的索引
        mock_page.run_task = MagicMock()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await layout._handle_resize()
        # 无缓存视图时不应有副作用（page.run_task 不应被再次调用）
        mock_page.run_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_resize_calls_view_handle_resize(self, mock_page):
        """覆盖成功调用视图 handle_resize，并传递缓存尺寸。"""
        layout = self._make_layout(mock_page)
        layout._current_width = 1280
        layout._current_height = 800
        mock_view = MagicMock()
        mock_view.handle_resize = MagicMock()
        layout._view_cache[0] = mock_view
        layout._current_tab_index = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await layout._handle_resize()
            mock_view.handle_resize.assert_called_once_with(1280, 800)

    @pytest.mark.asyncio
    async def test_handle_resize_catches_view_handler_exception(self, mock_page):
        """覆盖视图 handle_resize 异常时降级为 debug log。"""
        layout = self._make_layout(mock_page)
        mock_view = MagicMock()
        mock_view.handle_resize = MagicMock(side_effect=RuntimeError("resize error"))
        layout._view_cache[0] = mock_view
        layout._current_tab_index = 0
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch("ui.app_layout.logger") as mock_logger:
                await layout._handle_resize()
                mock_logger.debug.assert_called_once()
                debug_msg = mock_logger.debug.call_args[0][0]
                assert "Resize handler error" in debug_msg

    # ========== _on_locale_change 分支测试 (覆盖 app_layout.py:298-317) ==========

    def test_on_locale_change_skips_missing_brand_text(self, mock_page):
        """覆盖 app_layout.py:298->exit，brand_text 缺失时跳过刷新。"""
        layout = self._make_layout(mock_page)
        layout.brand_text = None
        layout.nav_rail.update = MagicMock()
        layout._on_locale_change()
        # nav_rail 仍被刷新（brand_text 缺失不影响后续流程）
        layout.nav_rail.update.assert_called_once()

    def test_on_locale_change_skips_missing_collapse_btn(self, mock_page):
        """覆盖 app_layout.py:300->exit，collapse_btn 缺失时跳过刷新。"""
        layout = self._make_layout(mock_page)
        layout.collapse_btn = None
        layout.nav_rail.update = MagicMock()
        layout._on_locale_change()
        # nav_rail 仍被刷新
        layout.nav_rail.update.assert_called_once()

    def test_on_locale_change_skips_missing_nav_rail(self, mock_page):
        """覆盖 app_layout.py:302->exit，nav_rail 缺失时跳过刷新。"""
        layout = self._make_layout(mock_page)
        layout.nav_rail = None
        mock_page.update = MagicMock()
        layout._on_locale_change()
        # page.update 仍被调用（nav_rail 缺失不影响 page 更新）
        mock_page.update.assert_called_once()

    def test_on_locale_change_skips_destinations_boundary(self, mock_page):
        """覆盖 app_layout.py:312->exit，destinations 索引边界检查。"""
        layout = self._make_layout(mock_page)
        # 减少 destinations 数量，触发 i < len 检查失败路径
        original_dests = layout.nav_rail.destinations[:]
        layout.nav_rail.destinations = original_dests[:3]
        layout.nav_rail.update = MagicMock()
        layout._on_locale_change()
        # 前 3 个 destinations 被刷新为对应的 nav key
        expected_keys = ["nav_market", "nav_screener", "nav_backtest"]
        for i, expected_key in enumerate(expected_keys):
            assert layout.nav_rail.destinations[i].label.value == expected_key
        layout.nav_rail.update.assert_called_once()

    def test_on_locale_change_updates_page_after_nav_rail(self, mock_page):
        """覆盖 app_layout.py:317->exit，page 存在时调用 update。"""
        layout = self._make_layout(mock_page)
        layout.nav_rail.update = MagicMock()
        mock_page.update = MagicMock()
        layout._on_locale_change()
        mock_page.update.assert_called_once()

    def test_on_locale_change_skips_page_update_if_missing(self, mock_page):
        """覆盖 app_layout.py:317->exit，page 为 falsy 时跳过 update。

        需让 self.page.title=... 能成功（MagicMock 允许属性设置），
        但 if self.page: 为 False（通过 __bool__ 返回 False）。
        """
        layout = self._make_layout(mock_page)
        layout.nav_rail.update = MagicMock()
        # 构造 falsy page：允许设置 title 属性，但 bool(page) 为 False
        falsy_page = MagicMock()
        falsy_page.__bool__ = lambda self: False
        layout.page = falsy_page
        layout._on_locale_change()
        # nav_rail.update 仍被调用（page falsy 不影响 nav_rail 刷新）
        layout.nav_rail.update.assert_called_once()
        # page.update 不应被调用
        falsy_page.update.assert_not_called()

    # ========== _execute_tab_switch 异常降级测试 (覆盖 app_layout.py:398-404, 414-415) ==========

    @pytest.mark.asyncio
    async def test_execute_tab_switch_body_update_fails_fallback_to_page(self, mock_page):
        """覆盖 app_layout.py:398-404，body.update 失败后降级到 page.update。"""
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 1
        layout.nav_rail.update = MagicMock()
        layout.body.update = MagicMock(side_effect=RuntimeError("body update failed"))
        mock_page.update = MagicMock()
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch("ui.app_layout.logger") as mock_logger:
                await layout._execute_tab_switch()
                mock_logger.error.assert_called_once()
                mock_page.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_tab_switch_page_update_fails_silently(self, mock_page):
        """覆盖 app_layout.py:402-404，page.update 失败时静默忽略。"""
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 1
        layout.nav_rail.update = MagicMock()
        layout.body.update = MagicMock(side_effect=RuntimeError("body update failed"))
        mock_page.update = MagicMock(side_effect=RuntimeError("page update failed"))
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await layout._execute_tab_switch()
        # 流程继续：tab 仍被切换，nav_rail.update 仍被调用
        assert layout._current_tab_index == 1
        layout.nav_rail.update.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_tab_switch_refresh_locale_exception_logged(self, mock_page):
        """覆盖 app_layout.py:414-415，refresh_locale 异常时降级为 debug log。"""
        layout = self._make_layout(mock_page)
        layout._current_tab_index = 0
        layout._pending_tab_index = 1
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        mock_view = MagicMock()
        mock_view.refresh_locale = MagicMock(side_effect=RuntimeError("locale refresh error"))
        layout._view_cache[1] = mock_view
        with patch("asyncio.sleep", new_callable=AsyncMock):
            with patch("ui.app_layout.logger") as mock_logger:
                await layout._execute_tab_switch()
                # 检查所有 debug calls，找到包含 "View locale refresh skipped" 的消息
                debug_calls = [call[0][0] for call in mock_logger.debug.call_args_list]
                assert any("View locale refresh skipped" in msg for msg in debug_calls)

    # ========== run_strategy_from_home 分支测试 (覆盖 app_layout.py:431->exit) ==========

    @pytest.mark.asyncio
    async def test_run_strategy_from_home_skips_non_screener_view(self, mock_page):
        """覆盖 app_layout.py:431->exit，screener_view 非 ScreenerView 时跳过调用。

        setup fixture 把 ScreenerView patch 成 MagicMock 实例（非类型），isinstance 会 TypeError。
        此处临时恢复 ScreenerView 为真实类型，使 isinstance 检查正常工作并返回 False。
        """
        layout = self._make_layout(mock_page)
        layout.body.update = MagicMock()
        layout.nav_rail.update = MagicMock()
        # 缓存中放置非 ScreenerView 对象（spec=[] 确保无 select_and_run_strategy 方法）
        non_screener_view = MagicMock(spec=[])
        layout._view_cache[1] = non_screener_view

        # 临时恢复 ScreenerView 为真实类型，使 isinstance 可正常判断
        from ui.views.screener_view import ScreenerView as RealScreenerView

        with patch("ui.app_layout.ScreenerView", RealScreenerView):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await layout.run_strategy_from_home("test_strategy")
        # 验证未调用 select_and_run_strategy（non_screener_view 无此方法，若调用会抛 AttributeError）
        non_screener_view.assert_not_called()
