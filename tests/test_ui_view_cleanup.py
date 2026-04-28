import unittest
from unittest.mock import MagicMock, patch

from ui.views.data_view import DataExplorerView
from ui.views.home_view import HomeView
from ui.views.screener_view import ScreenerView


class TestHomeViewCleanup(unittest.TestCase):
    @patch("ui.views.home_view.I18n.unsubscribe")
    @patch("ui.views.home_view.NewsFeed")
    @patch("ui.views.home_view.MarketDashboard")
    @patch("ui.views.home_view.HomeViewModel")
    def test_will_unmount_unsubscribes_pubsub_and_cancels_init_task(
        self,
        mock_vm_cls,
        _mock_dashboard,
        _mock_news_feed,
        mock_i18n_unsubscribe,
    ):
        view = HomeView()
        page = MagicMock()
        page.pubsub = MagicMock()
        view._Control__page = page  # type: ignore[attr-defined]
        view._pubsub_subscribed = True
        view._is_mounted = True
        view._init_task = MagicMock()

        view.will_unmount()

        mock_vm_cls.return_value.dispose.assert_called_once()
        page.pubsub.unsubscribe.assert_called_once_with(view._on_broadcast_message)
        mock_i18n_unsubscribe.assert_called_once_with(view.refresh_locale)
        view._init_task.cancel.assert_called_once()
        self.assertFalse(view._is_mounted)
        self.assertFalse(view._pubsub_subscribed)


class TestScreenerViewCleanup(unittest.TestCase):
    @patch("ui.views.screener_view.TaskManager")
    @patch("ui.views.screener_view.ScreenerViewModel")
    def test_will_unmount_clears_overlay_and_table_references(self, mock_vm_cls, mock_task_manager):
        page = MagicMock()
        page.overlay = []
        page.update = MagicMock()

        view = ScreenerView(page)
        view._Control__page = page  # type: ignore[attr-defined]
        view.result_table.list_view.controls = [MagicMock(), MagicMock()]
        view.detail_dialog = MagicMock()
        page.overlay.extend([view.save_file_picker, view.detail_dialog])

        view.will_unmount()

        mock_task_manager.return_value.unsubscribe.assert_called_once_with(view._on_tasks_updated)
        mock_vm_cls.return_value.dispose.assert_called_once()
        self.assertEqual(view.result_table.list_view.controls, [])
        self.assertNotIn(view.save_file_picker, page.overlay)
        self.assertIsNone(view.detail_dialog)
        page.update.assert_called_once()


class TestDataExplorerViewCleanup(unittest.TestCase):
    @patch("ui.views.data_view.DatabaseManager")
    def test_will_unmount_unsubscribes_pubsub_and_cancels_mount_task(self, _mock_db_manager):
        view = DataExplorerView()
        page = MagicMock()
        page.pubsub = MagicMock()
        view._Control__page = page  # type: ignore[attr-defined]
        view._pubsub_subscribed = True
        mock_mount_task = MagicMock()
        view._mount_task = mock_mount_task

        view.will_unmount()

        page.pubsub.unsubscribe.assert_called_once_with(view._on_broadcast_message)
        mock_mount_task.cancel.assert_called_once()
        self.assertFalse(view._pubsub_subscribed)
        self.assertIsNone(view._mount_task)


if __name__ == "__main__":
    unittest.main()
