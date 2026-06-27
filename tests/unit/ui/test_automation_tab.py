from unittest.mock import MagicMock, patch

from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
import pytest


pytestmark = pytest.mark.unit


class _FakePage:
    def __init__(self):
        self.toast_messages = []
        self.overlay = []
        self._update_count = 0

    def show_toast(self, message, type="info"):
        self.toast_messages.append((message, type))

    def update(self, control=None):
        self._update_count += 1


class TestAutomationTab:
    def test_init_with_default_config(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)

                assert tab.schedule_enabled.value is False
                assert tab.doubao_enabled.value is False

    def test_on_schedule_toggle_enables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.schedule_enabled.value = True
                event = MagicMock()
                tab.on_schedule_toggle(event)

                mock_ch.save_config.assert_called_once_with({"auto_update_enabled": True})
                assert show_snack.called

    def test_on_schedule_toggle_disables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = True
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.schedule_enabled.value = False
                event = MagicMock()
                tab.on_schedule_toggle(event)

                mock_ch.save_config.assert_called_once_with({"auto_update_enabled": False})
                assert show_snack.called

    def test_on_schedule_time_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.schedule_time.value = "17:00"
                event = MagicMock()
                tab.on_schedule_time_change(event)

                mock_ch.save_config.assert_called_once_with({"auto_update_time": "17:00"})

    def test_on_doubao_toggle_enables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.doubao_enabled.value = True
                event = MagicMock()
                tab.on_doubao_toggle(event)

                mock_ch.set_doubao_schedule_enabled.assert_called_once_with(True)

    def test_on_doubao_time_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.doubao_time.value = "18:00"
                event = MagicMock()
                tab.on_doubao_time_change(event)

                mock_ch.set_doubao_schedule_time.assert_called_once_with("18:00")

    def test_did_mount_subscribes_to_locale(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab._mounted = False
                tab.did_mount()

                assert tab._locale_subscription_id == "sub_id"
                mock_i18n.subscribe.assert_called_once()

    def test_did_mount_skips_if_already_mounted(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab._mounted = True
                tab.did_mount()

                mock_i18n.subscribe.assert_not_called()

    def test_will_unmount_unsubscribes(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab._locale_subscription_id = "sub_id"
                tab.will_unmount()

                mock_i18n.unsubscribe.assert_called_once_with("sub_id")
                assert tab._locale_subscription_id is None

    def test_update_theme(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                with patch("ui.views.settings_tabs.automation_tab.AppColors") as mock_colors:
                    mock_colors.INPUT_BG = "#fff"
                    mock_colors.INPUT_TEXT = "#000"
                    mock_colors.INPUT_BORDER = "#ccc"
                    mock_colors.SUCCESS = "#00ff00"

                    tab = AutomationTab(MagicMock())
                    tab.page = _FakePage()
                    tab.update_theme()

                    assert tab.schedule_time.bgcolor == "#fff"
                    assert tab.doubao_time.bgcolor == "#fff"

    def test_on_locale_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                tab._on_locale_change("en")

                assert tab.schedule_enabled.label == "translated_text"

    def test_on_locale_change_preserves_dropdown_value(self):
        """§5.8 规范 4：_on_locale_change 重建 time_options 后 schedule_time/doubao_time 的 value 必须保留。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = True
            mock_ch.get_auto_update_time.return_value = "16:30"
            mock_ch.is_doubao_schedule_enabled.return_value = False
            mock_ch.get_doubao_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_doubao_schedule_enabled = MagicMock()
            mock_ch.set_doubao_schedule_time = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # 模拟用户选择了一个不在默认 _build_time_options 中的值
                tab.schedule_time.value = "08:00"
                tab.doubao_time.value = "08:00"

                # 重建 options 期间不应丢失 value
                original_schedule_value = tab.schedule_time.value
                original_doubao_value = tab.doubao_time.value
                tab._on_locale_change("en")

                assert tab.schedule_time.value == original_schedule_value
                assert tab.schedule_time.value == "08:00"
                assert tab.doubao_time.value == original_doubao_value
                assert tab.doubao_time.value == "08:00"
                # options 被重建（新对象，非 None）
                assert tab.schedule_time.options is not None
                assert len(tab.schedule_time.options) > 0
                assert tab.doubao_time.options is not None
                assert len(tab.doubao_time.options) > 0


class TestNotificationsTab:
    def test_init_with_default_config(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                page = _FakePage()
                tab = NotificationsTab(MagicMock(), page)

                assert tab.news_alerts_enabled.value is True

    def test_on_news_toggle_enables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                page = _FakePage()
                tab = NotificationsTab(show_snack, page)
                tab.page = _FakePage()

                tab.news_alerts_enabled.value = True
                event = MagicMock()
                tab.on_news_toggle(event)

                mock_ch.save_config.assert_called_once_with({"enable_news_alerts": True})

    def test_on_news_toggle_disables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: (
                True if key == "enable_news_alerts" else (default if default is not None else True)
            )
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                page = _FakePage()
                tab = NotificationsTab(show_snack, page)
                tab.page = _FakePage()

                tab.news_alerts_enabled.value = False
                event = MagicMock()
                tab.on_news_toggle(event)

                mock_ch.save_config.assert_called_once_with({"enable_news_alerts": False})

    def test_on_interval_change_valid(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                page = _FakePage()
                tab = NotificationsTab(show_snack, page)
                tab.page = _FakePage()

                tab.news_interval.value = "60"
                event = MagicMock()
                tab.on_interval_change(event)

                mock_ch.save_config.assert_called_once_with({"news_poll_interval": 60})

    def test_on_interval_change_invalid(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                page = _FakePage()
                tab = NotificationsTab(show_snack, page)
                tab.page = _FakePage()

                tab.news_interval.value = "invalid"
                event = MagicMock()
                tab.on_interval_change(event)

                mock_ch.save_config.assert_not_called()

    def test_did_mount_subscribes_to_locale(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                page = _FakePage()
                tab = NotificationsTab(MagicMock(), page)
                tab._mounted2 = False
                tab.did_mount()

                assert tab._locale_subscription_id == "sub_id"
                mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_unsubscribes(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                page = _FakePage()
                tab = NotificationsTab(MagicMock(), page)
                tab._locale_subscription_id = "sub_id"
                tab.will_unmount()

                mock_i18n.unsubscribe.assert_called_once_with("sub_id")
                assert tab._locale_subscription_id is None

    def test_update_theme(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                with patch("ui.views.settings_tabs.automation_tab.AppColors") as mock_colors:
                    mock_colors.INPUT_BG = "#fff"
                    mock_colors.INPUT_TEXT = "#000"
                    mock_colors.INPUT_BORDER = "#ccc"

                    page = _FakePage()
                    tab = NotificationsTab(MagicMock(), page)
                    tab.page = _FakePage()
                    tab.update_theme()

                    assert tab.news_interval.bgcolor == "#fff"

    def test_on_locale_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                page = _FakePage()
                tab = NotificationsTab(MagicMock(), page)
                tab.page = _FakePage()
                tab._on_locale_change("en")

                assert tab.news_alerts_enabled.label == "translated_text"

    def test_on_locale_change_preserves_news_interval_value(self):
        """§5.8 规范 4：_on_locale_change 重建 interval_options 后 news_interval.value 必须保留。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                page = _FakePage()
                tab = NotificationsTab(MagicMock(), page)
                tab.page = _FakePage()
                # 模拟用户选择了一个不在默认 _build_interval_options 中的值
                tab.news_interval.value = "120"

                # 重建 options 期间不应丢失 value
                original_value = tab.news_interval.value
                tab._on_locale_change("en")

                assert tab.news_interval.value == original_value
                assert tab.news_interval.value == "120"
                # options 被重建（新对象，非 None）
                assert tab.news_interval.options is not None
                assert len(tab.news_interval.options) > 0

    def test_page_ref_returns_page(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.get_config.side_effect = lambda key, default=None: default if default is not None else True
            mock_ch.save_config = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                class _FakePageWithWeakRef(_FakePage):
                    pass

                page = _FakePageWithWeakRef()
                tab = NotificationsTab(MagicMock(), page)

                assert tab._page_ref is not None
                assert tab._page_ref() is page
