import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from ui.views.settings_tabs.automation_tab import AutomationTab, NotificationsTab
import pytest


pytestmark = pytest.mark.unit


async def _run_async_passthrough(task_type, func, *args, **kwargs):
    """Mock helper: 立即同步执行 func 并返回结果，模拟线程池 offload。"""
    return func(*args, **kwargs)


@pytest.fixture(autouse=True)
def _patch_thread_pool():
    """Patch automation_tab 模块级 ThreadPoolManager，run_async 直接同步执行。"""
    mock_tpm = MagicMock()
    mock_tpm.run_async = AsyncMock(side_effect=_run_async_passthrough)
    with patch("ui.views.settings_tabs.automation_tab.ThreadPoolManager", return_value=mock_tpm):
        yield


class _FakePage:
    def __init__(self):
        self.toast_messages = []
        self.overlay = []
        self._update_count = 0

    def show_toast(self, message, type="info"):
        self.toast_messages.append((message, type))

    def update(self, control=None):
        self._update_count += 1

    def run_task(self, coro_func, *args, **kwargs):
        """同步执行协程，模拟 Flet page.run_task 调度。"""
        asyncio.run(coro_func(*args, **kwargs))


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
                tab._on_locale_change()

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
                tab._on_locale_change()

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
                tab._on_locale_change()
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
                tab._on_locale_change()
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
                tab._on_locale_change()
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
                tab._on_locale_change()

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
                tab._on_locale_change()

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


# ============================================================================
# 测试工厂：patch 依赖并 yield (tab, mock_ch, mock_i18n)
# 测试逻辑必须在 with 块内执行，以保证 mock 生效（模块级 ConfigHandler/I18n 引用）。
# ============================================================================


@contextmanager
def _automation_tab_cxt(**config):
    """AutomationTab 测试工厂：默认 show_snack=MagicMock()，测试中可覆盖 tab.show_snack。"""
    with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
        mock_ch.is_auto_update_enabled.return_value = config.get("auto_update_enabled", False)
        mock_ch.get_auto_update_time.return_value = config.get("auto_update_time", "16:00")
        mock_ch.is_ai_concept_schedule_enabled.return_value = config.get("ai_concept_enabled", False)
        mock_ch.get_ai_concept_schedule_time.return_value = config.get("ai_concept_time", "16:00")
        mock_ch.get_ai_concept_search_engine.return_value = config.get("search_engine", "search_std")
        mock_ch.save_config = MagicMock()
        mock_ch.set_ai_concept_schedule_enabled = MagicMock()
        mock_ch.set_ai_concept_schedule_time = MagicMock()
        mock_ch.set_ai_concept_search_engine = MagicMock()

        with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe.return_value = "sub_id"
            mock_i18n.unsubscribe = MagicMock()

            tab = AutomationTab(MagicMock())
            yield tab, mock_ch, mock_i18n


@contextmanager
def _notifications_tab_cxt(page=None, **config):
    """NotificationsTab 测试工厂。

    Args:
        page: 传入 _FakePage() 以让 on_xxx 调度 run_task；None 时 _page_ref=None 跳过调度。
    """
    enable_news = config.get("enable_news_alerts", True)
    news_interval = config.get("news_poll_interval", 60)

    with patch("ui.views.settings_tabs.automation_tab.ConfigHandler") as mock_ch:
        mock_ch.get_config.side_effect = lambda key, default=None: (
            enable_news if key == "enable_news_alerts" else news_interval if key == "news_poll_interval" else default
        )
        mock_ch.save_config = MagicMock()

        with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
            mock_i18n.get.return_value = "test"
            mock_i18n.subscribe.return_value = "sub_id"
            mock_i18n.unsubscribe = MagicMock()

            tab = NotificationsTab(MagicMock(), page)
            yield tab, mock_ch, mock_i18n


# ============================================================================
# AutomationTab 异常路径覆盖
# 直接 await _do_xxx_async() 以确保 coverage.py 追踪分支
# （sync 通过 page.run_task + asyncio.run 会导致分支追踪缺失）
# ============================================================================


class TestAutomationTabErrorPaths:
    """AutomationTab async handler except 块 + UI rollback 路径。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "switch_attr,handler_name,mock_attr_to_fail",
        [
            ("schedule_enabled", "_do_schedule_toggle_async", "save_config"),
            ("ai_concept_enabled", "_do_ai_concept_toggle_async", "set_ai_concept_schedule_enabled"),
        ],
        ids=["schedule_toggle", "ai_concept_toggle"],
    )
    async def test_toggle_async_rollback_on_error(self, switch_attr, handler_name, mock_attr_to_fail):
        """开关切换 save 失败 → 回滚开关 value + 错误 snackbar 反馈。"""
        with _automation_tab_cxt() as (tab, mock_ch, _):
            getattr(mock_ch, mock_attr_to_fail).side_effect = RuntimeError("save failed")
            show_snack = MagicMock()
            tab.show_snack = show_snack

            switch = getattr(tab, switch_attr)
            switch.value = True
            await getattr(tab, handler_name)()

            # rollback：开关被回滚为 False
            assert switch.value is False
            show_snack.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "handler_name,dropdown_attr,value,mock_attr_to_fail",
        [
            ("_do_schedule_time_change_async", "schedule_time", "17:00", "save_config"),
            ("_do_ai_concept_time_change_async", "ai_concept_time", "18:00", "set_ai_concept_schedule_time"),
            (
                "_do_ai_concept_search_engine_change_async",
                "ai_concept_search_engine",
                "search_pro",
                "set_ai_concept_search_engine",
            ),
        ],
        ids=["schedule_time", "ai_concept_time", "ai_concept_search_engine"],
    )
    async def test_async_change_handles_error(self, handler_name, dropdown_attr, value, mock_attr_to_fail):
        """time/engine 变更 save 失败 → 错误 snackbar（无 rollback）。"""
        with _automation_tab_cxt() as (tab, mock_ch, _):
            getattr(mock_ch, mock_attr_to_fail).side_effect = RuntimeError("fail")
            show_snack = MagicMock()
            tab.show_snack = show_snack

            getattr(tab, dropdown_attr).value = value
            await getattr(tab, handler_name)()

            show_snack.assert_called_once()

    @pytest.mark.parametrize("scenario", ["no_page", "update_raises"], ids=["no_page", "update_raises"])
    def test_safe_update_does_not_raise(self, scenario):
        """_safe_update 在 page=None 或 update() 抛异常时降级，不传播。"""
        with _automation_tab_cxt() as (tab, _, _):
            if scenario == "no_page":
                tab._safe_update()
            else:
                tab.page = _FakePage()
                tab.update = MagicMock(side_effect=RuntimeError("update failed"))
                tab._safe_update()

    def test_on_locale_change_swallows_exception(self):
        """_build_content 抛异常时 _on_locale_change 降级为 warning 日志，不传播。"""
        with _automation_tab_cxt() as (tab, _, _):
            tab.page = _FakePage()
            with patch.object(tab, "_build_content", side_effect=RuntimeError("build failed")):
                tab._on_locale_change()

    def test_will_unmount_skips_when_no_subscription(self):
        """_locale_subscription_id=None 时跳过 unsubscribe。"""
        with _automation_tab_cxt() as (tab, _, mock_i18n):
            tab._locale_subscription_id = None
            tab.will_unmount()
            mock_i18n.unsubscribe.assert_not_called()

    def test_update_theme_skips_when_no_page(self):
        """page=None 时 update_theme 不调用 update()。"""
        with _automation_tab_cxt() as (tab, _, _):
            update_mock = MagicMock()
            tab.update = update_mock
            tab.update_theme()
            update_mock.assert_not_called()


class TestAutomationTabNoPage:
    """page=None 时事件入口应跳过 run_task 调度。"""

    @pytest.mark.parametrize(
        "event_method",
        [
            "on_schedule_toggle",
            "on_schedule_time_change",
            "on_ai_concept_toggle",
            "on_ai_concept_time_change",
            "on_ai_concept_search_engine_change",
        ],
    )
    def test_event_handler_skips_when_no_page(self, event_method):
        with _automation_tab_cxt() as (tab, _, _):
            getattr(tab, event_method)(MagicMock())


class TestAutomationTabNoSnackCallback:
    """show_snack=None 时 async handler 不应报错（覆盖 if self.show_snack False 分支）。"""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "handler_name,setup_trigger",
        [
            ("_do_schedule_toggle_async", lambda tab: setattr(tab.schedule_enabled, "value", True)),
            ("_do_schedule_time_change_async", lambda tab: setattr(tab.schedule_time, "value", "17:00")),
            ("_do_ai_concept_toggle_async", lambda tab: setattr(tab.ai_concept_enabled, "value", True)),
            ("_do_ai_concept_time_change_async", lambda tab: setattr(tab.ai_concept_time, "value", "18:00")),
            (
                "_do_ai_concept_search_engine_change_async",
                lambda tab: setattr(tab.ai_concept_search_engine, "value", "search_pro"),
            ),
        ],
        ids=["schedule_toggle", "schedule_time", "ai_concept_toggle", "ai_concept_time", "ai_concept_search_engine"],
    )
    async def test_async_handler_no_snack_callback(self, handler_name, setup_trigger):
        with _automation_tab_cxt() as (tab, _, _):
            tab.show_snack = None
            setup_trigger(tab)
            await getattr(tab, handler_name)()


# ============================================================================
# NotificationsTab 异常路径覆盖
# ============================================================================


class TestNotificationsTabErrorPaths:
    """NotificationsTab async handler except 块 + UI rollback 路径。"""

    @pytest.mark.asyncio
    async def test_do_news_toggle_async_rollback_on_error(self):
        """save_config 失败时开关回滚 + 错误反馈。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, mock_ch, _):
            mock_ch.save_config.side_effect = RuntimeError("fail")
            show_snack = MagicMock()
            tab.show_snack = show_snack

            tab.news_alerts_enabled.value = True
            await tab._do_news_toggle_async()

            assert tab.news_alerts_enabled.value is False
            assert tab.news_interval.disabled is True
            show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_interval_change_async_handles_error(self):
        """save_config 抛 Exception 时错误反馈。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, mock_ch, _):
            mock_ch.save_config.side_effect = RuntimeError("fail")
            show_snack = MagicMock()
            tab.show_snack = show_snack

            tab.news_interval.value = "60"
            await tab._do_interval_change_async()

            show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_interval_change_async_value_error_no_snack(self):
        """news_interval 非数字 + show_snack=None 时 ValueError 路径不报错（覆盖 L657 分支）。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, _):
            tab.show_snack = None
            tab.news_interval.value = "invalid"
            await tab._do_interval_change_async()

    @pytest.mark.parametrize("scenario", ["no_page", "update_raises"], ids=["no_page", "update_raises"])
    def test_safe_update_does_not_raise(self, scenario):
        """_safe_update 在 page=None 或 update() 抛异常时降级，不传播。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, _):
            if scenario == "no_page":
                tab._safe_update()
            else:
                tab.page = _FakePage()
                tab.update = MagicMock(side_effect=RuntimeError("update failed"))
                tab._safe_update()

    def test_on_locale_change_swallows_exception(self):
        """_build_content 抛异常时 _on_locale_change 降级为 warning 日志。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, _):
            tab.page = _FakePage()
            with patch.object(tab, "_build_content", side_effect=RuntimeError("build failed")):
                tab._on_locale_change()

    def test_will_unmount_skips_when_no_subscription(self):
        """_locale_subscription_id=None 时跳过 unsubscribe。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, mock_i18n):
            tab._locale_subscription_id = None
            tab.will_unmount()
            mock_i18n.unsubscribe.assert_not_called()

    def test_update_theme_skips_when_no_page(self):
        """page=None 时 update_theme 不调用 update()。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, _):
            update_mock = MagicMock()
            tab.update = update_mock
            tab.update_theme()
            update_mock.assert_not_called()


class TestNotificationsTabNoPageRef:
    """page_ref=None 时事件入口应跳过 run_task 调度。"""

    def test_on_news_toggle_skips_when_no_page_ref(self):
        with _notifications_tab_cxt(page=None) as (tab, _, _):
            tab._page_ref = None
            tab.news_alerts_enabled.value = True
            tab.on_news_toggle(MagicMock())

    def test_on_interval_change_skips_when_no_page_ref(self):
        with _notifications_tab_cxt(page=None) as (tab, _, _):
            tab._page_ref = None
            tab.news_interval.value = "60"
            tab.on_interval_change(MagicMock())


class TestNotificationsTabNoSnackCallback:
    """show_snack=None 时 async handler 不应报错（覆盖 if self.show_snack False 分支）。"""

    @pytest.mark.asyncio
    async def test_do_news_toggle_async_no_snack_enable(self):
        """enabled=True + show_snack=None 不报错（覆盖 L629 分支）。"""
        page = _FakePage()
        with _notifications_tab_cxt(enable_news_alerts=False, page=page) as (tab, _, _):
            tab.show_snack = None
            tab.news_alerts_enabled.value = True
            await tab._do_news_toggle_async()

    @pytest.mark.asyncio
    async def test_do_news_toggle_async_no_snack_disable(self):
        """enabled=False + show_snack=None 不报错（覆盖 L631 elif 分支）。"""
        page = _FakePage()
        with _notifications_tab_cxt(enable_news_alerts=True, page=page) as (tab, _, _):
            tab.show_snack = None
            tab.news_alerts_enabled.value = False
            await tab._do_news_toggle_async()

    @pytest.mark.asyncio
    async def test_do_interval_change_async_no_snack(self):
        """show_snack=None 时 interval 变更不报错（覆盖 L651 分支）。"""
        page = _FakePage()
        with _notifications_tab_cxt(page=page) as (tab, _, _):
            tab.show_snack = None
            tab.news_interval.value = "60"
            await tab._do_interval_change_async()
