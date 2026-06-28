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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)

                assert tab.schedule_enabled.value is False
                assert tab.ai_concept_enabled.value is False

    def test_on_schedule_toggle_enables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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

    def test_on_ai_concept_toggle_enables(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.ai_concept_enabled.value = True
                event = MagicMock()
                tab.on_ai_concept_toggle(event)

                mock_ch.set_ai_concept_schedule_enabled.assert_called_once_with(True)
                # UI-Minor: Switch 从 False → True 时 search_engine 应同步启用
                assert tab.ai_concept_search_engine.disabled is False

    def test_on_ai_concept_time_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                show_snack = MagicMock()
                tab = AutomationTab(show_snack)
                tab.page = _FakePage()

                tab.ai_concept_time.value = "18:00"
                event = MagicMock()
                tab.on_ai_concept_time_change(event)

                mock_ch.set_ai_concept_schedule_time.assert_called_once_with("18:00")

    def test_did_mount_subscribes_to_locale(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

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
                    assert tab.ai_concept_time.bgcolor == "#fff"

    def test_on_locale_change(self):
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                tab._on_locale_change("en")

                assert tab.schedule_enabled.label == "translated_text"

    def test_on_locale_change_preserves_dropdown_value(self):
        """§5.8 规范 4：_on_locale_change 重建 time_options 后 schedule_time/ai_concept_time 的 value 必须保留。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = True
            mock_ch.get_auto_update_time.return_value = "16:30"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # 模拟用户选择了一个不在默认 _build_time_options 中的值
                tab.schedule_time.value = "08:00"
                tab.ai_concept_time.value = "08:00"

                # 重建 options 期间不应丢失 value
                original_schedule_value = tab.schedule_time.value
                original_ai_concept_value = tab.ai_concept_time.value
                tab._on_locale_change("en")

                assert tab.schedule_time.value == original_schedule_value
                assert tab.schedule_time.value == "08:00"
                assert tab.ai_concept_time.value == original_ai_concept_value
                assert tab.ai_concept_time.value == "08:00"
                # options 被重建（新对象，非 None）
                assert tab.schedule_time.options is not None
                assert len(tab.schedule_time.options) > 0
                assert tab.ai_concept_time.options is not None
                assert len(tab.ai_concept_time.options) > 0


class TestAutomationTabSearchEngine:
    """ai_concept_search_engine Dropdown 配置测试。"""

    def test_init_with_search_engine_default(self):
        """默认值 search_std 应正确加载到 Dropdown。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                assert tab.ai_concept_search_engine.value == "search_std"
                # UI-L2: Switch 初始关闭时 search_engine 应被禁用
                assert tab.ai_concept_search_engine.disabled is True

    def test_init_with_search_engine_pro(self):
        """配置返回 search_pro 时应正确加载到 Dropdown。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_pro"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                assert tab.ai_concept_search_engine.value == "search_pro"

    def test_on_search_engine_change_calls_set(self):
        """切换 search_engine 后应调用 ConfigHandler.set_ai_concept_search_engine。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                tab.ai_concept_search_engine.value = "search_pro"
                tab.on_ai_concept_search_engine_change(MagicMock())
                mock_ch.set_ai_concept_search_engine.assert_called_once_with("search_pro")

    def test_on_ai_concept_toggle_disables_search_engine(self):
        """C2：Switch 关闭时应同步禁用 search_engine Dropdown。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # 初始 enabled=True，Dropdown 应可编辑
                assert tab.ai_concept_search_engine.disabled is False
                # 切换为 False
                tab.ai_concept_enabled.value = False
                tab.on_ai_concept_toggle(MagicMock())
                assert tab.ai_concept_search_engine.disabled is True

    def test_on_locale_change_preserves_search_engine_value(self):
        """§5.8 规范 4：_on_locale_change 重建 search_engine options 后 value 必须保留。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = True
            mock_ch.get_auto_update_time.return_value = "16:30"
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                tab.ai_concept_search_engine.value = "search_pro"
                original = tab.ai_concept_search_engine.value
                tab._on_locale_change("en")
                assert tab.ai_concept_search_engine.value == original
                assert tab.ai_concept_search_engine.value == "search_pro"
                assert tab.ai_concept_search_engine.options is not None
                assert len(tab.ai_concept_search_engine.options) > 0

    def test_on_locale_change_preserves_disabled_state_when_switch_off(self):
        """§5.8 场景补全：Switch 关闭时语言切换后 search_engine.disabled 必须仍为 True。

        验证 _on_locale_change 和 _build_content 重建不重置 Dropdown 的 disabled 状态。
        """
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # Switch 关闭时 search_engine 应禁用
                assert tab.ai_concept_search_engine.disabled is True
                tab._on_locale_change("en")
                # 语言切换后 disabled 状态必须保留
                assert tab.ai_concept_search_engine.disabled is True
                # value 也应保留
                assert tab.ai_concept_search_engine.value == "search_std"

    def test_on_locale_change_preserves_disabled_state_when_switch_on(self):
        """§5.8 场景补全：Switch 开启时语言切换后 search_engine.disabled 必须仍为 False。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = True
            mock_ch.get_auto_update_time.return_value = "16:30"
            mock_ch.is_ai_concept_schedule_enabled.return_value = True
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_pro"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "translated_text"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # Switch 开启时 search_engine 应可编辑
                assert tab.ai_concept_search_engine.disabled is False
                tab._on_locale_change("en")
                # 语言切换后 disabled 状态必须保留
                assert tab.ai_concept_search_engine.disabled is False
                assert tab.ai_concept_search_engine.value == "search_pro"

    def test_update_theme_refreshes_search_engine_colors(self):
        """update_theme 应刷新 search_engine Dropdown 的 bgcolor/color/border_color。"""
        with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
            mock_ch.is_auto_update_enabled.return_value = False
            mock_ch.get_auto_update_time.return_value = "16:00"
            mock_ch.is_ai_concept_schedule_enabled.return_value = False
            mock_ch.get_ai_concept_schedule_time.return_value = "16:00"
            mock_ch.get_ai_concept_search_engine.return_value = "search_std"
            mock_ch.save_config = MagicMock()
            mock_ch.set_ai_concept_schedule_enabled = MagicMock()
            mock_ch.set_ai_concept_schedule_time = MagicMock()
            mock_ch.set_ai_concept_search_engine = MagicMock()

            with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
                mock_i18n.get.return_value = "test"
                mock_i18n.subscribe.return_value = "sub_id"
                mock_i18n.unsubscribe = MagicMock()

                tab = AutomationTab(MagicMock())
                tab.page = _FakePage()
                # 清空颜色以验证 update_theme 会设置
                tab.ai_concept_search_engine.bgcolor = None
                tab.ai_concept_search_engine.color = None
                tab.ai_concept_search_engine.border_color = None
                tab.update_theme()
                from ui.theme import AppColors

                assert tab.ai_concept_search_engine.bgcolor == AppColors.INPUT_BG
                assert tab.ai_concept_search_engine.color == AppColors.INPUT_TEXT
                assert tab.ai_concept_search_engine.border_color == AppColors.INPUT_BORDER


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
