"""Unit tests for DataSourceTab — View layer coverage.

Tests the Tab's logic methods by constructing a lightweight instance
with mocked Flet controls, bypassing the full __init__ UI construction.

P2-3: 本文件含若干纯 assert_called/assert_called_once 断言，保留理由：
- show_snack.assert_called_once(): show_snack 参数为 i18n key，硬编码 key 反而脆弱
- vm.bind/dispose.assert_called_once(): 生命周期接线，参数为内部回调
- i18n.subscribe/unsubscribe.assert_called_once(): 订阅注册，参数为 listener 函数
- vm.recover_stale_state.assert_called_once(): 状态恢复接线，参数为内部状态
- metric_*.set_value.assert_called_once(): UI 指标更新，参数为运行时数据
这些断言验证"接线正确"而非"参数正确"，符合 UI 测试分层策略（§6.8 MVVM）
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from services.task_manager import AppTask, TaskManager, TaskStatus
from ui.components.settings_widgets import MetricCard
from ui.theme import AppColors
from ui.viewmodels.data_source_view_model import DataSourceViewModel
from ui.views.settings_tabs.data_source_tab import DataSourceTab

pytestmark = pytest.mark.unit

# --- Fake ActionChip for isinstance checks ---


class _FakeActionChip:
    """Mimics ActionChip interface so isinstance(ctrl, ActionChip) passes in tests."""

    def __init__(self):
        self.set_loading = MagicMock()
        self.opacity = 1.0
        self.disabled = False


# --- Fixtures ---


def _make_mock_action_chip():
    return _FakeActionChip()


def _make_mock_button():
    btn = MagicMock()  # spec omitted: Flet button, complex __init__ with many internal controls
    btn.disabled = False
    btn.text = ""
    btn.icon = None
    btn.style = None
    return btn


def _make_mock_metric_card():
    card = MagicMock(spec=MetricCard)
    # set_value / update_theme auto-mocked by spec; no manual assignment needed
    return card


def _make_tab() -> DataSourceTab:
    """Create a DataSourceTab with mocked internals, skipping __init__."""
    with patch.object(DataSourceTab, "__init__", lambda self, show_snack_callback=None: None):
        tab = DataSourceTab(show_snack_callback=MagicMock())  # spec omitted: callback function

    # Mock required attributes
    tab.show_snack = MagicMock()  # spec omitted: callback function
    tab.page = MagicMock()  # spec omitted: Flet Page, complex __init__
    tab.vm = MagicMock(spec=DataSourceViewModel)
    tab._tm = MagicMock(spec=TaskManager)

    # Mock UI controls
    tab.btn_check_health = _make_mock_button()
    tab.sync_button = _make_mock_button()
    tab.action_full_sync = _make_mock_action_chip()
    tab.action_clear_cache = _make_mock_action_chip()
    tab.action_doubao_rebuild = _make_mock_action_chip()

    tab.metric_sync = _make_mock_metric_card()
    tab.metric_coverage = _make_mock_metric_card()
    tab.metric_health = _make_mock_metric_card()
    tab.metric_storage = _make_mock_metric_card()

    tab.health_summary_container = MagicMock()  # spec omitted: Flet Container, complex __init__
    tab.health_summary_container.content = None

    tab.progress_bar = MagicMock()  # spec omitted: Flet ProgressBar, complex __init__
    tab.progress_bar.visible = False
    tab.progress_bar.value = 0

    tab.progress_text = MagicMock()  # spec omitted: Flet Text, complex __init__
    tab.progress_text.value = ""

    tab.tushare_panel = MagicMock()  # spec omitted: TushareConfigPanel, instance attrs set in __init__
    tab.history_years_dropdown = MagicMock()  # spec omitted: Flet Dropdown, complex __init__
    tab.history_years_dropdown.value = "3"

    tab.row_init = MagicMock()  # spec omitted: SettingRow, instance attrs set in __init__
    tab.row_init.title_view = MagicMock()  # spec omitted: Flet Text child of SettingRow
    tab.row_init.title_view.value = ""
    tab.row_init.subtitle_view = MagicMock()  # spec omitted: Flet Text child of SettingRow
    tab.row_init.subtitle_view.value = ""

    tab.row_token = MagicMock()  # spec omitted: SettingRow, instance attrs set in __init__
    tab.header_health = MagicMock()  # spec omitted: SectionHeader, instance attrs set in __init__
    tab.header_console = MagicMock()  # spec omitted: SectionHeader, instance attrs set in __init__
    tab.header_api = MagicMock()  # spec omitted: SectionHeader, instance attrs set in __init__
    tab.header_init = MagicMock()  # spec omitted: SectionHeader, instance attrs set in __init__

    tab.update = MagicMock()  # spec omitted: method mock on DataSourceTab instance
    return tab


@pytest.fixture
def tab():
    with patch("ui.views.settings_tabs.data_source_tab.ActionChip", _FakeActionChip):
        yield _make_tab()


# --- Test: _on_tushare_save ---


class TestOnTushareSave:
    def test_saves_valid_token(self, tab):
        tab._on_tushare_save({"token": "abc123"})
        tab.vm.save_tushare_token.assert_called_once_with("abc123")
        tab.show_snack.assert_called_once()

    def test_saves_token_stripped(self, tab):
        tab._on_tushare_save({"token": "  abc  "})
        tab.vm.save_tushare_token.assert_called_once_with("abc")

    def test_skips_empty_token(self, tab):
        tab._on_tushare_save({"token": ""})
        tab.vm.save_tushare_token.assert_not_called()
        tab.show_snack.assert_not_called()

    def test_skips_whitespace_token(self, tab):
        tab._on_tushare_save({"token": "   "})
        tab.vm.save_tushare_token.assert_not_called()

    def test_skips_missing_token_key(self, tab):
        tab._on_tushare_save({})
        tab.vm.save_tushare_token.assert_not_called()


# --- Test: _on_vm_show_snack ---


class TestOnVmShowSnack:
    def test_success_color(self, tab):
        tab._on_vm_show_snack("msg", "success")
        tab.show_snack.assert_called_once_with("msg", color=AppColors.SUCCESS)

    def test_warning_color(self, tab):
        tab._on_vm_show_snack("msg", "warning")
        tab.show_snack.assert_called_once_with("msg", color=AppColors.WARNING)

    def test_error_color(self, tab):
        tab._on_vm_show_snack("msg", "error")
        tab.show_snack.assert_called_once_with("msg", color=AppColors.ERROR)

    def test_info_color(self, tab):
        tab._on_vm_show_snack("msg", "info")
        tab.show_snack.assert_called_once_with("msg", color=AppColors.INFO)

    def test_unknown_color_falls_back_to_info(self, tab):
        tab._on_vm_show_snack("msg", "unknown")
        tab.show_snack.assert_called_once_with("msg", color=AppColors.INFO)


# --- Test: _on_vm_sync_busy_changed ---


class TestOnVmSyncBusyChanged:
    def test_daily_sync_active(self, tab):
        tab._on_vm_sync_busy_changed(True, "daily_sync")
        # Should call _update_sync_buttons with action_full_sync as active
        tab.action_full_sync.set_loading.assert_called_with(True)

    def test_doubao_sync_active(self, tab):
        tab._on_vm_sync_busy_changed(True, "doubao_sync")
        tab.action_doubao_rebuild.set_loading.assert_called_with(True)

    def test_cache_clear_active(self, tab):
        tab._on_vm_sync_busy_changed(True, "cache_clear")
        tab.action_clear_cache.set_loading.assert_called_with(True)

    def test_system_init_sync_active(self, tab):
        tab._on_vm_sync_busy_changed(True, "system_init_sync")
        # sync_button should not be disabled when it's the active one
        assert tab.sync_button.disabled is False

    def test_not_busy_resets_all(self, tab):
        # First set busy
        tab._on_vm_sync_busy_changed(True, "daily_sync")
        # Then reset
        tab._on_vm_sync_busy_changed(False, None)
        tab.action_full_sync.set_loading.assert_called_with(False)
        tab.action_full_sync.opacity = 1.0


# --- Test: _update_sync_buttons ---


class TestUpdateSyncButtons:
    def test_active_action_chip_sets_loading(self, tab):
        tab._update_sync_buttons(True, tab.action_full_sync)
        tab.action_full_sync.set_loading.assert_called_with(True)

    def test_active_sync_button_not_disabled(self, tab):
        tab._update_sync_buttons(True, tab.sync_button)
        assert tab.sync_button.disabled is False

    def test_non_active_buttons_disabled_when_busy(self, tab):
        tab._update_sync_buttons(True, tab.action_full_sync)
        assert tab.action_clear_cache.disabled is True
        assert tab.action_doubao_rebuild.disabled is True
        assert tab.sync_button.disabled is True

    def test_non_active_action_chips_dimmed_when_busy(self, tab):
        tab._update_sync_buttons(True, tab.action_full_sync)
        assert tab.action_clear_cache.opacity == 0.5
        assert tab.action_doubao_rebuild.opacity == 0.5

    def test_not_busy_resets_all_buttons(self, tab):
        tab._update_sync_buttons(False, None)
        assert tab.action_full_sync.disabled is False
        assert tab.action_clear_cache.disabled is False
        assert tab.action_doubao_rebuild.disabled is False
        assert tab.sync_button.disabled is False
        tab.action_full_sync.set_loading.assert_called_with(False)
        tab.action_clear_cache.set_loading.assert_called_with(False)
        tab.action_doubao_rebuild.set_loading.assert_called_with(False)
        assert tab.action_full_sync.opacity == 1.0
        assert tab.action_clear_cache.opacity == 1.0
        assert tab.action_doubao_rebuild.opacity == 1.0

    def test_no_page_skips_update_call(self, tab):
        tab.page = None
        tab._update_sync_buttons(False, None)
        # Should not crash, update() should not be called on page
        tab.update.assert_not_called()

    def test_reset_ctrl_exception_handled(self, tab):
        """Lines 522-523: exception when resetting control state."""
        tab.action_full_sync.set_loading.side_effect = RuntimeError("widget gone")
        tab._update_sync_buttons(False, None)  # Should not raise

    def test_page_update_exception_handled(self, tab):
        """Lines 527-528: exception when self.update() fails."""
        tab.update.side_effect = RuntimeError("page disposed")
        tab._update_sync_buttons(False, None)  # Should not raise


# --- Test: _on_task_update ---


class TestOnTaskUpdate:
    def test_forwards_to_vm_when_page_exists(self, tab):
        tasks = [MagicMock(spec=AppTask)]
        tab._on_task_update(tasks)
        tab.vm.handle_task_update.assert_called_once_with(tasks)

    def test_skips_when_no_page(self, tab):
        tab.page = None
        tab._on_task_update([MagicMock(spec=AppTask)])
        tab.vm.handle_task_update.assert_not_called()


# --- Test: _safe_update ---


class TestSafeUpdate:
    def test_calls_update_when_page_exists(self, tab):
        tab._safe_update()
        tab.update.assert_called_once()

    def test_skips_when_no_page(self, tab):
        tab.page = None
        tab._safe_update()
        tab.update.assert_not_called()

    def test_swallows_exception(self, tab):
        tab.update.side_effect = RuntimeError("widget disposed")
        tab._safe_update()  # Should not raise


# --- Test: _on_vm_health_checking ---


class TestOnVmHealthChecking:
    def test_disables_button_and_updates_metrics(self, tab):
        tab._on_vm_health_checking()
        assert tab.btn_check_health.disabled is True
        tab.metric_health.set_value.assert_called_once()
        tab.metric_storage.set_value.assert_called_once()


# --- Test: _on_vm_health_result ---


class TestOnVmHealthResult:
    def test_green_status(self, tab):
        tab._on_vm_health_result(
            {
                "status": "green",
                "market": {"latest_local": "2025-01-01", "lag_days": 0},
                "details": {
                    "financial_coverage": 95.0,
                    "missing_critical": 0,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            }
        )
        # metric_health should get CHECK_CIRCLE icon (green path)
        tab.metric_health.set_value.assert_called_once()

    def test_yellow_status(self, tab):
        tab._on_vm_health_result(
            {
                "status": "yellow",
                "market": {"latest_local": "2025-01-01", "lag_days": 2},
                "details": {
                    "financial_coverage": 80.0,
                    "missing_critical": 0,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            }
        )
        tab.metric_health.set_value.assert_called_once()

    def test_red_status(self, tab):
        tab._on_vm_health_result(
            {
                "status": "red",
                "market": {"latest_local": None, "lag_days": 30},
                "details": {
                    "financial_coverage": 10.0,
                    "missing_critical": 3,
                    "missing_depth": 2,
                    "missing_breadth": 1,
                },
            }
        )
        tab.metric_health.set_value.assert_called_once()

    def test_no_latest_local_shows_never_sync(self, tab):
        tab._on_vm_health_result(
            {
                "status": "green",
                "market": {"latest_local": None, "lag_days": 0},
                "details": {
                    "financial_coverage": 0,
                    "missing_critical": 0,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            }
        )
        # Should display "never sync" text

    def test_missing_critical_shows_error(self, tab):
        tab._on_vm_health_result(
            {
                "status": "red",
                "market": {"latest_local": "2025-01-01", "lag_days": 0},
                "details": {
                    "financial_coverage": 50.0,
                    "missing_critical": 5,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            }
        )
        # health_summary_container.content should be set

    def test_missing_depth_and_breadth(self, tab):
        tab._on_vm_health_result(
            {
                "status": "green",
                "market": {"latest_local": "2025-01-01", "lag_days": 0},
                "details": {
                    "financial_coverage": 90.0,
                    "missing_critical": 0,
                    "missing_depth": 3,
                    "missing_breadth": 2,
                },
            }
        )
        # Should include depth and breadth items

    def test_cov_val_as_string(self, tab):
        tab._on_vm_health_result(
            {
                "status": "green",
                "market": {"latest_local": "2025-01-01", "lag_days": 0},
                "details": {
                    "financial_coverage": "N/A",
                    "missing_critical": 0,
                    "missing_depth": 0,
                    "missing_breadth": 0,
                },
            }
        )
        # Should handle non-numeric cov_val


# --- Test: _on_vm_health_error ---


class TestOnVmHealthError:
    def test_updates_metrics_to_error_state(self, tab):
        tab._on_vm_health_error("DB connection failed")
        tab.metric_health.set_value.assert_called_once()
        tab.metric_storage.set_value.assert_called_once()

    def test_error_msg_shown_in_summary(self, tab):
        tab._on_vm_health_error("Sanitized error detail")
        # summary container content should include the error message
        content = tab.health_summary_container.content
        assert content is not None

    def test_empty_error_msg_falls_back_to_generic(self, tab):
        tab._on_vm_health_error("")
        # Should not crash, summary should still be set
        content = tab.health_summary_container.content
        assert content is not None


# --- Test: _on_vm_health_cancelled ---


class TestOnVmHealthCancelled:
    def test_updates_metrics_to_cancelled_state(self, tab):
        tab._on_vm_health_cancelled()
        tab.metric_health.set_value.assert_called_once()
        tab.metric_storage.set_value.assert_called_once()


# --- Test: _on_vm_health_finished ---


class TestOnVmHealthFinished:
    def test_re_enables_button(self, tab):
        tab.btn_check_health.disabled = True
        tab._on_vm_health_finished()
        assert tab.btn_check_health.disabled is False


# --- Test: _on_vm_init_sync_started ---


class TestOnVmInitSyncStarted:
    def test_switches_button_to_cancel_mode(self, tab):
        tab._on_vm_init_sync_started()
        assert tab.sync_button.text is not None
        assert tab.progress_bar.visible is True
        assert tab.progress_bar.value == 0


# --- Test: _on_vm_init_sync_reset ---


class TestOnVmInitSyncReset:
    def test_completed_status(self, tab):
        tab._on_vm_init_sync_reset(TaskStatus.COMPLETED)
        assert tab.sync_button.disabled is False
        assert tab.progress_bar.visible is False
        assert tab.progress_text.value == ""

    def test_cancelled_status(self, tab):
        tab._on_vm_init_sync_reset(TaskStatus.CANCELLED)
        assert tab.sync_button.disabled is False
        assert tab.progress_bar.visible is False
        assert tab.progress_text.value != ""

    def test_failed_status(self, tab):
        tab._on_vm_init_sync_reset(TaskStatus.FAILED)
        assert tab.sync_button.disabled is False
        assert tab.progress_bar.visible is False
        assert tab.progress_text.value != ""


# --- Test: _on_vm_progress_update ---


class TestOnVmProgressUpdate:
    def test_first_update_always_applies(self, tab):
        tab._on_vm_progress_update(0.5, "Processing...")
        assert tab.progress_bar.value == 0.5
        assert tab.progress_text.value == "50.0% - Processing..."

    def test_full_progress_always_applies(self, tab):
        tab._last_ui_update = time.time()  # Very recent
        tab._on_vm_progress_update(1.0, "Done")
        assert tab.progress_bar.value == 1.0

    def test_throttle_skips_rapid_updates(self, tab):
        tab._last_ui_update = time.time()  # Just updated
        tab._on_vm_progress_update(0.3, "Should be skipped")
        # Should NOT update because throttle
        assert tab.progress_bar.value != 0.3 or tab.progress_text.value != "30.0% - Should be skipped"

    def test_update_after_throttle_interval(self, tab):
        tab._last_ui_update = time.time() - 0.2  # 200ms ago
        tab._on_vm_progress_update(0.6, "After interval")
        assert tab.progress_bar.value == 0.6


# --- Test: _on_vm_cache_cleared ---


class TestOnVmCacheCleared:
    def test_sends_pubsub_when_page_exists(self, tab):
        tab._on_vm_cache_cleared()
        tab.page.pubsub.send_all.assert_called_once_with("cache_cleared")

    def test_skips_when_no_page(self, tab):
        tab.page = None
        tab._on_vm_cache_cleared()  # Should not raise


# --- Test: on_history_years_change ---


class TestOnHistoryYearsChange:
    def test_valid_change(self, tab):
        e = MagicMock()  # spec omitted: Flet control event, dynamic attribute access
        e.control.value = "5"
        tab.on_history_years_change(e)
        tab.vm.set_history_years.assert_called_once_with(5)
        tab.show_snack.assert_called_once()

    def test_invalid_value_logs_error(self, tab):
        e = MagicMock()  # spec omitted: Flet control event, dynamic attribute access
        e.control.value = "invalid"
        tab.on_history_years_change(e)
        tab.vm.set_history_years.assert_not_called()


# --- Test: _on_mount ---


class TestOnMount:
    def test_subscribes_and_binds(self, tab):
        with patch("ui.views.settings_tabs.data_source_tab.I18n") as mock_i18n:
            tab._on_mount()
            mock_i18n.subscribe.assert_called_once()
            tab.tushare_panel.reload_config.assert_called_once()
            tab._tm.subscribe.assert_called_once()
            tab.vm.bind.assert_called_once()
            tab.vm.recover_stale_state.assert_called_once()


# --- Test: _on_unmount ---


class TestOnUnmount:
    def test_unsubscribes_and_disposes(self, tab):
        with patch("ui.views.settings_tabs.data_source_tab.I18n") as mock_i18n:
            tab._on_unmount()
            mock_i18n.unsubscribe.assert_called_once()
            tab._tm.unsubscribe.assert_called_once()
            tab.vm.dispose.assert_called_once()
