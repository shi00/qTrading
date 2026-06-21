from unittest.mock import MagicMock, patch
import flet as ft

from ui.views.settings_view import SettingsView
import pytest


pytestmark = pytest.mark.unit


class _FakePage:
    def __init__(self):
        self.toast_messages = []
        self.overlay = []
        self._update_count = 0

    def show_toast(self, message, type="info"):
        self.toast_messages.append((message, type))

    def update(self):
        self._update_count += 1


class TestSettingsView:
    def test_on_tab_click_valid_index(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                view.page = _FakePage()

                event = MagicMock()
                event.control.data = "1"

                view._on_tab_click(event)

                assert view.current_tab_index == 1

    def test_on_tab_click_invalid_index_string(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()

                event = MagicMock()
                event.control.data = "invalid"

                view._on_tab_click(event)

                assert view.current_tab_index == 0

    def test_on_tab_click_index_out_of_range(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()

                event = MagicMock()
                event.control.data = "100"

                view._on_tab_click(event)

                assert view.current_tab_index == 0

    def test_show_snack_with_show_toast(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                page = _FakePage()
                view.page = page

                view.show_snack("test message")

                assert len(page.toast_messages) == 1
                assert page.toast_messages[0] == ("test message", "info")

    def test_show_snack_with_error_color(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                page = _FakePage()
                view.page = page

                view.show_snack("error message", color=ft.Colors.RED)

                assert len(page.toast_messages) == 1
                assert page.toast_messages[0] == ("error message", "error")

    def test_show_snack_with_success_color(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                page = _FakePage()
                view.page = page

                view.show_snack("success message", color=ft.Colors.GREEN)

                assert len(page.toast_messages) == 1
                assert page.toast_messages[0] == ("success message", "success")

    def test_show_snack_with_warning_color(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                page = _FakePage()
                view.page = page

                view.show_snack("warning message", color=ft.Colors.ORANGE)

                assert len(page.toast_messages) == 1
                assert page.toast_messages[0] == ("warning message", "warning")

    def test_show_snack_without_page(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                view.page = None

                result = view.show_snack("test message")

                assert result is None

    def test_page_ref_property(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                page = _FakePage()
                view.page = page

                assert view.page_ref is page

    def test_on_mount_subscribes_to_locale(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                view._on_mount()

                mock_i18n.subscribe.assert_called_once_with(view.refresh_locale)

    def test_on_unmount_unsubscribes_and_cascades(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            mock_tab_with_cleanup = MagicMock()
            mock_tab_with_cleanup._on_unmount = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = mock_tab_with_cleanup
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                view._on_unmount()

                mock_i18n.unsubscribe.assert_called_once()

    def test_on_unmount_handles_exception(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            mock_tab_with_error = MagicMock()
            mock_tab_with_error._on_unmount.side_effect = RuntimeError("cleanup error")

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = mock_tab_with_error
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()
                view._on_unmount()

                mock_i18n.unsubscribe.assert_called_once()

    def test_show_snack_with_snackbar_fallback(self):
        with patch("ui.views.settings_view.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe = MagicMock()
            mock_i18n.unsubscribe = MagicMock()

            with (
                patch("ui.views.settings_view.DataSourceTab") as mock_data_tab,
                patch("ui.views.settings_view.DatabaseTab") as mock_db_tab,
                patch("ui.views.settings_view.AIBrainTab") as mock_ai_tab,
                patch("ui.views.settings_view.AutomationTab") as mock_auto_tab,
                patch("ui.views.settings_view.NotificationsTab") as mock_notify_tab,
                patch("ui.views.settings_view.SystemTab") as mock_system_tab,
            ):
                mock_data_tab.return_value = MagicMock()
                mock_db_tab.return_value = MagicMock()
                mock_ai_tab.return_value = MagicMock()
                mock_auto_tab.return_value = MagicMock()
                mock_notify_tab.return_value = MagicMock()
                mock_system_tab.return_value = MagicMock()

                view = SettingsView()

                class _PageWithoutShowToast:
                    def __init__(self):
                        self.overlay = []
                        self._update_count = 0

                    def update(self):
                        self._update_count += 1

                page = _PageWithoutShowToast()
                view.page = page

                view.show_snack("test message", color=ft.Colors.BLUE)

                assert len(page.overlay) == 1
                assert isinstance(page.overlay[0], ft.SnackBar)
