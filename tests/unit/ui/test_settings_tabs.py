import contextlib
import logging
from unittest.mock import MagicMock, patch, AsyncMock

import flet as ft
import pytest

from tests.unit.ui.conftest import set_page, wrap_mock_page

pytestmark = pytest.mark.unit


class TestAutomationTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.automation_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.automation_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.automation_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.automation_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.automation_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.automation_tab.SettingRow", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.automation_tab import AutomationTab

        return AutomationTab(show_snack_callback=MagicMock())

    def test_on_schedule_toggle_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        self.mock_ch.save_config.assert_called_with({"auto_update_enabled": True})

    def test_on_schedule_toggle_disables_time(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = False
        tab.on_schedule_toggle(None)
        assert tab.schedule_time.disabled is True

    def test_on_schedule_toggle_enables_time(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        assert tab.schedule_time.disabled is False

    def test_on_schedule_toggle_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        snack.assert_called_once_with("settings_snack_auto_on")

    def test_on_schedule_time_change_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_time.value = "16:30"
        tab.on_schedule_time_change(None)
        self.mock_ch.save_config.assert_called_with({"auto_update_time": "16:30"})

    def test_on_schedule_time_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab.schedule_time.value = "16:30"
        tab.on_schedule_time_change(None)
        snack.assert_called_once_with("settings_snack_time_set")

    def test_on_doubao_toggle_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = True
        tab.on_doubao_toggle(None)
        self.mock_ch.set_doubao_schedule_enabled.assert_called_with(True)

    def test_on_doubao_toggle_disables_time(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = False
        tab.on_doubao_toggle(None)
        assert tab.doubao_time.disabled is True

    def test_on_doubao_time_change_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_time.value = "20:00"
        tab.on_doubao_time_change(None)
        self.mock_ch.set_doubao_schedule_time.assert_called_with("20:00")

    def test_did_mount_subscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab.did_mount()
        self.mock_i18n.subscribe.assert_called_once()
        assert tab._locale_subscription_id == "sub_id"

    def test_will_unmount_unsubscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = "sub_id"
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")

    def test_did_mount_idempotent(self, mock_page):
        tab = self._make_tab()
        tab._mounted = True
        tab.did_mount()
        self.mock_i18n.subscribe.assert_not_called()

    def test_build_time_options_returns_six_options(self, mock_page):
        tab = self._make_tab()
        options = tab._build_time_options()
        assert len(options) == 6
        assert options[0].key == "15:30"
        assert options[-1].key == "20:00"

    def test_build_time_options_uses_i18n(self, mock_page):
        tab = self._make_tab()
        options = tab._build_time_options()
        for opt in options:
            self.mock_i18n.get.assert_any_call(opt.key.replace(":", "") and f"settings_opt_{opt.key.replace(':', '')}")

    def test_on_locale_change_updates_labels(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("zh_CN")
        self.mock_i18n.get.assert_any_call("settings_auto_update")
        self.mock_i18n.get.assert_any_call("settings_update_time")

    def test_on_locale_change_rebuilds_content(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("zh_CN")
        assert tab.txt_title_main is not None
        assert tab.card_main is not None

    def test_on_locale_change_updates_schedule_status(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab._on_locale_change("zh_CN")
        self.mock_i18n.get.assert_any_call("settings_status_auto_on")

    def test_on_locale_change_updates_doubao_status(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = False
        tab._on_locale_change("zh_CN")
        self.mock_i18n.get.assert_any_call("settings_status_auto_off")

    def test_on_locale_change_rebuilds_time_options(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("zh_CN")
        expected_keys = [opt.key for opt in tab._build_time_options()]
        actual_keys = [opt.key for opt in (tab.schedule_time.options or [])]
        assert actual_keys == expected_keys

    def test_update_theme_sets_input_colors(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.schedule_time.bgcolor == self.mock_ac.INPUT_BG
        assert tab.schedule_time.color == self.mock_ac.INPUT_TEXT
        assert tab.schedule_time.border_color == self.mock_ac.INPUT_BORDER

    def test_update_theme_sets_status_color_enabled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab.update_theme()
        assert tab.schedule_status.color == self.mock_ac.SUCCESS

    def test_update_theme_sets_status_color_disabled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = False
        tab.update_theme()
        assert tab.schedule_status.color == ft.Colors.ON_SURFACE_VARIANT

    def test_update_theme_sets_doubao_input_colors(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()
        assert tab.doubao_time.bgcolor == self.mock_ac.INPUT_BG
        assert tab.doubao_time.color == self.mock_ac.INPUT_TEXT
        assert tab.doubao_time.border_color == self.mock_ac.INPUT_BORDER

    def test_update_theme_sets_doubao_status_color(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = True
        tab.update_theme()
        assert tab.doubao_status.color == self.mock_ac.SUCCESS

    def test_update_theme_calls_update_when_page_set(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab.update_theme()
        page.update.assert_called_once()

    def test_will_unmount_clears_subscription(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = "sub_id"
        tab.will_unmount()
        assert tab._locale_subscription_id is None
        assert tab._mounted is False

    def test_will_unmount_no_subscription(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = None
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_not_called()

    def test_get_schedule_status_text_enabled(self, mock_page):
        tab = self._make_tab()
        tab._get_schedule_status_text(True)
        self.mock_i18n.get.assert_called_with("settings_status_auto_on")

    def test_get_schedule_status_text_disabled(self, mock_page):
        tab = self._make_tab()
        tab._get_schedule_status_text(False)
        self.mock_i18n.get.assert_called_with("settings_status_auto_off")

    def test_on_doubao_toggle_enables_time(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = True
        tab.on_doubao_toggle(None)
        assert tab.doubao_time.disabled is False

    def test_on_doubao_toggle_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab.doubao_enabled.value = True
        tab.on_doubao_toggle(None)
        snack.assert_called_once_with("settings_snack_auto_on")

    def test_on_doubao_time_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab.doubao_time.value = "20:00"
        tab.on_doubao_time_change(None)
        snack.assert_called_once_with("settings_snack_time_set")

    def test_safe_update_with_page(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        set_page(tab, page)
        tab._safe_update()
        page.update.assert_called_once()

    def test_safe_update_without_page(self, mock_page):
        tab = self._make_tab()
        tab._Control__page = None
        tab._safe_update()

    def test_build_content_creates_inner_container(self, mock_page):
        tab = self._make_tab()
        assert tab.inner_container is not None
        assert tab.card_main is not None
        assert tab.card_doubao is not None

    def test_build_content_creates_title_and_desc(self, mock_page):
        tab = self._make_tab()
        assert tab.txt_title_main is not None
        assert tab.txt_desc_main is not None

    def test_build_content_creates_hint_text(self, mock_page):
        tab = self._make_tab()
        assert tab.txt_hint_bg is not None

    def test_build_content_creates_rows(self, mock_page):
        tab = self._make_tab()
        assert tab.row_schedule is not None
        assert tab.row_time is not None
        assert tab.row_doubao_schedule is not None
        assert tab.row_doubao_time is not None

    def test_on_schedule_toggle_no_snack_callback(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = None
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        self.mock_ch.save_config.assert_called_with({"auto_update_enabled": True})

    def test_on_schedule_toggle_updates_status_text(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        self.mock_i18n.get.assert_any_call("settings_status_auto_on")

    def test_on_schedule_toggle_disabled_status_color(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = False
        tab.on_schedule_toggle(None)
        assert tab.schedule_status.color == ft.Colors.ON_SURFACE_VARIANT

    def test_on_schedule_toggle_enabled_status_color(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.schedule_enabled.value = True
        tab.on_schedule_toggle(None)
        assert tab.schedule_status.color == self.mock_ac.SUCCESS

    def test_on_schedule_time_change_no_snack_callback(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = None
        tab.schedule_time.value = "17:00"
        tab.on_schedule_time_change(None)
        self.mock_ch.save_config.assert_called_with({"auto_update_time": "17:00"})

    def test_on_doubao_toggle_no_snack_callback(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = None
        tab.doubao_enabled.value = True
        tab.on_doubao_toggle(None)
        self.mock_ch.set_doubao_schedule_enabled.assert_called_with(True)

    def test_on_doubao_toggle_updates_status_text(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = False
        tab.on_doubao_toggle(None)
        self.mock_i18n.get.assert_any_call("settings_status_auto_off")

    def test_on_doubao_toggle_disabled_status_color(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = False
        tab.on_doubao_toggle(None)
        assert tab.doubao_status.color == ft.Colors.ON_SURFACE_VARIANT

    def test_on_doubao_toggle_enabled_status_color(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = True
        tab.on_doubao_toggle(None)
        assert tab.doubao_status.color == self.mock_ac.SUCCESS

    def test_on_doubao_time_change_no_snack_callback(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = None
        tab.doubao_time.value = "16:00"
        tab.on_doubao_time_change(None)
        self.mock_ch.set_doubao_schedule_time.assert_called_with("16:00")

    def test_on_locale_change_exception_handled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        self.mock_i18n.get.side_effect = RuntimeError("boom")
        tab._on_locale_change("zh_CN")

    def test_safe_update_exception_handled(self, mock_page):
        tab = self._make_tab()
        page = MagicMock()
        page.update.side_effect = RuntimeError("update failed")
        set_page(tab, page)
        tab._safe_update()

    def test_update_theme_without_page(self, mock_page):
        tab = self._make_tab()
        tab._Control__page = None
        tab.update_theme()
        assert tab.schedule_time.bgcolor == self.mock_ac.INPUT_BG

    def test_update_theme_doubao_status_disabled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.doubao_enabled.value = False
        tab.update_theme()
        assert tab.doubao_status.color == ft.Colors.ON_SURFACE_VARIANT

    def test_on_locale_change_updates_doubao_labels(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("zh_CN")
        self.mock_i18n.get.assert_any_call("settings_doubao_update")

    def test_schedule_time_initially_disabled_when_off(self, mock_page):
        self.mock_ch.is_auto_update_enabled.return_value = False
        tab = self._make_tab()
        assert tab.schedule_time.disabled is True

    def test_schedule_time_initially_enabled_when_on(self, mock_page):
        self.mock_ch.is_auto_update_enabled.return_value = True
        tab = self._make_tab()
        assert tab.schedule_time.disabled is False

    def test_doubao_time_initially_disabled_when_off(self, mock_page):
        self.mock_ch.is_doubao_schedule_enabled.return_value = False
        tab = self._make_tab()
        assert tab.doubao_time.disabled is True

    def test_doubao_time_initially_enabled_when_on(self, mock_page):
        self.mock_ch.is_doubao_schedule_enabled.return_value = True
        tab = self._make_tab()
        assert tab.doubao_time.disabled is False

    def test_schedule_status_initial_color_enabled(self, mock_page):
        self.mock_ch.is_auto_update_enabled.return_value = True
        tab = self._make_tab()
        assert tab.schedule_status.color == self.mock_ac.SUCCESS

    def test_schedule_status_initial_color_disabled(self, mock_page):
        self.mock_ch.is_auto_update_enabled.return_value = False
        tab = self._make_tab()
        assert tab.schedule_status.color == self.mock_ac.TEXT_HINT

    def test_doubao_status_initial_color_enabled(self, mock_page):
        self.mock_ch.is_doubao_schedule_enabled.return_value = True
        tab = self._make_tab()
        assert tab.doubao_status.color == self.mock_ac.SUCCESS

    def test_doubao_status_initial_color_disabled(self, mock_page):
        self.mock_ch.is_doubao_schedule_enabled.return_value = False
        tab = self._make_tab()
        assert tab.doubao_status.color == self.mock_ac.TEXT_HINT


class TestAIBrainTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.ai_brain_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.ai_brain_tab.LLMConfigPanel", MagicMock()),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelConfigPanel", MagicMock()),
            patch("ui.views.settings_tabs.ai_brain_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.ai_brain_tab.SectionHeader", MagicMock()),
            patch("services.ai_service.AIService"),
            patch("services.local_model_manager.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager"),
            patch(
                "ui.views.settings_tabs.ai_brain_tab.DEFAULT_AI_PROMPT",
                "default_prompt",
            ),
            patch(
                "ui.views.settings_tabs.ai_brain_tab.DEFAULT_NEWS_PROMPT",
                "default_news",
            ),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        return AIBrainTab(show_snack_callback=MagicMock())

    def test_reset_ai_prompt(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._reset_ai_prompt(None)
        assert tab.ai_prompt_input.value == "default_prompt"

    def test_reset_ai_prompt_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab._reset_ai_prompt(None)
        snack.assert_called_once_with("settings_snack_prompt_reset")

    def test_reset_news_prompt(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._reset_news_prompt(None)
        assert tab.ai_news_prompt_input.value == "default_news"

    def test_reset_news_prompt_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab._reset_news_prompt(None)
        snack.assert_called_once_with("settings_snack_prompt_reset")

    @pytest.mark.asyncio
    async def test_save_ai_settings_empty_fields(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = ""
        tab.strategy_min_turnover_input.value = ""
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once_with("ai_snack_fields_empty", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_max_candidates(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "999"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "prompt"
        tab.ai_news_prompt_input.value = "news"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, ""), create=True):
            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once_with("ai_snack_invalid_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_turnover(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "200"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "prompt"
        tab.ai_news_prompt_input.value = "news"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, ""), create=True):
            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once_with("ai_snack_invalid_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_concurrency(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "99"
        tab.ai_prompt_input.value = "prompt"
        tab.ai_news_prompt_input.value = "news"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, ""), create=True):
            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_ai_settings_prompt_validation_fails(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "prompt"
        tab.ai_news_prompt_input.value = "news"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        with patch(
            "utils.prompt_guard.validate_prompt",
            return_value=(False, "prompt_err_length"),
            create=True,
        ):
            with patch("utils.prompt_guard.MAX_PROMPT_LENGTH", 5000, create=True):
                await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()

    def test_did_mount_subscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab.llm_config_panel = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.did_mount()
        self.mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_unsubscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = "sub_id"
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")

    def test_init_exception_handled(self, mock_page):
        with patch(
            "ui.views.settings_tabs.ai_brain_tab.LLMConfigPanel",
            side_effect=Exception("init error"),
        ):
            tab = self._make_tab()
            assert tab.content is not None

    def test_update_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.update_theme()

    def test_safe_update(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._safe_update()

    def test_safe_update_no_page(self, mock_page):
        tab = self._make_tab()
        tab._safe_update()

    @pytest.mark.asyncio
    async def test_save_ai_settings_exception(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "test prompt"
        tab.ai_news_prompt_input.value = "news prompt"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.get_current_config.return_value = {"model_path": ""}
        self.mock_ch.set_ai_max_candidates.side_effect = Exception("save error")
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, "")):
            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_ai_settings_with_local_model_not_found(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "test prompt"
        tab.ai_news_prompt_input.value = "news prompt"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.get_current_config.return_value = {"model_path": "/nonexistent/model.gguf"}
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, "")):
            with patch("os.path.exists", return_value=False):
                with patch("services.ai_service.AIService") as mock_ai:
                    mock_ai.return_value.reload_config = AsyncMock()
                    await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_ai_settings_with_local_model_changed(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "test prompt"
        tab.ai_news_prompt_input.value = "news prompt"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.get_current_config.return_value = {"model_path": "/path/to/model.gguf"}
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, "")):
            with patch("os.path.exists", return_value=True):
                with patch("services.ai_service.AIService") as mock_ai:
                    mock_ai.return_value.reload_config = AsyncMock()
                    with patch("services.local_model_manager.LocalModelManager.get_instance") as mock_lmm:
                        mock_lmm_instance = MagicMock()
                        mock_lmm_instance.get_loaded_model_md5 = MagicMock(return_value="old_md5")
                        mock_lmm_instance.calculate_file_md5 = AsyncMock(return_value="new_md5")
                        mock_lmm.return_value = mock_lmm_instance
                        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
                            mock_tpm_instance = MagicMock()
                            mock_tpm_instance.run_async = AsyncMock(return_value="new_md5")
                            mock_tpm.return_value = mock_tpm_instance
                            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_ai_settings_with_local_model_same_md5(self, mock_page):
        tab = self._make_tab()
        set_page(tab, wrap_mock_page(mock_page))
        tab.ai_max_candidates_input.value = "30"
        tab.strategy_min_turnover_input.value = "2.0"
        tab.ai_concurrency_input.value = "5"
        tab.ai_prompt_input.value = "test prompt"
        tab.ai_news_prompt_input.value = "news prompt"
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.get_current_config.return_value = {"model_path": "/path/to/model.gguf"}
        with patch("utils.prompt_guard.validate_prompt", return_value=(True, "")):
            with patch("os.path.exists", return_value=True):
                with patch("services.ai_service.AIService") as mock_ai:
                    mock_ai.return_value.reload_config = AsyncMock()
                    with patch("services.local_model_manager.LocalModelManager.get_instance") as mock_lmm:
                        mock_lmm_instance = MagicMock()
                        mock_lmm_instance.get_loaded_model_md5 = MagicMock(return_value="same_md5")
                        mock_lmm_instance.calculate_file_md5 = AsyncMock(return_value="same_md5")
                        mock_lmm.return_value = mock_lmm_instance
                        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
                            mock_tpm_instance = MagicMock()
                            mock_tpm_instance.run_async = AsyncMock(return_value="same_md5")
                            mock_tpm.return_value = mock_tpm_instance
                            await tab._save_ai_settings(None)
        tab.show_snack.assert_called_once()


class TestSystemTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.patches = [
            patch("ui.views.settings_tabs.system_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.system_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.system_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.system_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.system_tab.ThemeName"),
            patch("ui.views.settings_tabs.system_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.system_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.system_tab import SystemTab

        return SystemTab(show_snack_callback=MagicMock())

    def test_on_theme_change_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme"):
            tab.on_theme_change(None)
        self.mock_ch.set_theme_name.assert_called_with("dark")

    def test_on_theme_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme"):
            tab.on_theme_change(None)
        snack.assert_called_once_with("settings_snack_theme_updated")

    def test_on_log_level_change_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.log_level_dropdown.value = "DEBUG"
        with patch("utils.logger.update_log_level"):
            tab.on_log_level_change(None)
        self.mock_ch.set_log_level.assert_called_with("DEBUG")

    def test_save_concurrency_valid(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.concurrency_input.value = "4"
        tab.save_concurrency(None)
        self.mock_ch.set_sync_max_concurrent_heavy.assert_called_with(4)

    def test_save_concurrency_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.concurrency_input.value = "0"
        tab.save_concurrency(None)
        snack.assert_called_once_with("sys_snack_concurrency_range", color=self.mock_ac.ERROR)

    def test_save_concurrency_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.concurrency_input.value = "64"
        tab.save_concurrency(None)
        snack.assert_called_once_with("sys_snack_concurrency_range", color=self.mock_ac.ERROR)

    def test_save_concurrency_invalid_format(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.concurrency_input.value = "abc"
        tab.save_concurrency(None)
        snack.assert_called_once_with("sys_snack_num_fmt", color=self.mock_ac.ERROR)

    def test_save_rate_limit_valid(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.value = "200"
        tab.save_rate_limit(None)
        self.mock_ch.set_tushare_api_limit.assert_called_with(200)

    def test_save_rate_limit_empty_disables(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.value = ""
        tab.save_rate_limit(None)
        self.mock_ch.set_tushare_api_limit.assert_called_with(0)

    def test_save_rate_limit_zero_disables(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.rate_limit_input.value = "0"
        tab.save_rate_limit(None)
        self.mock_ch.set_tushare_api_limit.assert_called_with(0)

    def test_save_rate_limit_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.rate_limit_input.value = "5"
        tab.save_rate_limit(None)
        snack.assert_called_once_with("sys_snack_limit_min")

    def test_save_rate_limit_invalid_format(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.rate_limit_input.value = "abc"
        tab.save_rate_limit(None)
        snack.assert_called_once_with("sys_snack_num_fmt", color=self.mock_ac.ERROR)

    def test_save_no_proxy_domains(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost, 127.0.0.1"
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager"),
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
        self.mock_ch.set_no_proxy_domains.assert_called_with(["localhost", "127.0.0.1"])

    def test_save_no_proxy_domains_empty(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.no_proxy_input.value = ""
        with (
            patch("utils.proxy_manager.ProxyManager"),
            patch("utils.thread_pool.ThreadPoolManager"),
            patch("utils.thread_pool.TaskType"),
        ):
            tab.save_no_proxy_domains(None)
        self.mock_ch.set_no_proxy_domains.assert_called_with([])

    def test_save_db_pool_settings_valid(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        self.mock_ch.set_db_connection_pool_size.assert_called_with(5)
        self.mock_ch.set_db_max_overflow.assert_called_with(10)
        self.mock_ch.set_db_pool_timeout.assert_called_with(30)

    def test_save_db_pool_settings_pool_size_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "0"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("sys_snack_pool_range", color=self.mock_ac.ERROR)

    def test_on_theme_change_exception(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.theme_dropdown.value = "dark"
        with patch("ui.theme.apply_page_theme", side_effect=Exception("theme error")):
            tab.on_theme_change(None)
        snack.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_valid(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "4"
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm_instance = MagicMock()
            mock_tpm_instance.reload_config = MagicMock()
            mock_tpm.return_value = mock_tpm_instance
            await tab.save_thread_pool_settings(None)
        self.mock_ch.set_max_io_workers.assert_called_with(8)
        self.mock_ch.set_max_cpu_workers.assert_called_with(4)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_empty(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = ""
        tab.cpu_workers_input.value = "4"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_threads_empty", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_io_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "2"
        tab.cpu_workers_input.value = "4"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_io_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_io_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "999"
        tab.cpu_workers_input.value = "4"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_io_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_cpu_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "0"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_cpu_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_cpu_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "999"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_cpu_range", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_invalid_format(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "abc"
        tab.cpu_workers_input.value = "4"
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_num_fmt", color=self.mock_ac.ERROR)

    @pytest.mark.asyncio
    async def test_save_thread_pool_settings_exception(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.io_workers_input.value = "8"
        tab.cpu_workers_input.value = "4"
        self.mock_ch.set_max_io_workers.side_effect = Exception("save error")
        await tab.save_thread_pool_settings(None)
        snack.assert_called_once_with("sys_snack_save_err", color=self.mock_ac.ERROR)

    def test_save_no_proxy_domains_exception(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        set_page(tab, mock_page)
        tab.no_proxy_input.value = "localhost"
        self.mock_ch.set_no_proxy_domains.side_effect = Exception("save error")
        tab.save_no_proxy_domains(None)
        snack.assert_called_once()

    def test_save_db_pool_settings_overflow_too_high(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "100"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("settings_db_overflow: 0-50", color=self.mock_ac.ERROR)

    def test_save_db_pool_settings_timeout_too_low(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "5"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "1"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("settings_db_timeout: 5-300", color=self.mock_ac.ERROR)

    def test_save_db_pool_settings_invalid_format(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab.pool_size_input.value = "abc"
        tab.db_overflow_input.value = "10"
        tab.db_timeout_input.value = "30"
        tab.save_db_pool_settings(None)
        snack.assert_called_once_with("sys_snack_num_fmt", color=self.mock_ac.ERROR)

    def test_on_language_change_calls_set_locale(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.language_dropdown.value = "en_US"
        tab.on_language_change(None)
        self.mock_i18n.set_locale.assert_called_with("en_US")
        self.mock_ch.set_locale.assert_called_with("en_US")

    def test_on_language_change_calls_snack(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab.language_dropdown.value = "en_US"
        tab.on_language_change(None)
        snack.assert_called_once_with("settings_language_changed")

    def test_on_language_change_exception_handled(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        self.mock_i18n.set_locale.side_effect = Exception("test error")
        tab.language_dropdown.value = "en_US"
        tab.on_language_change(None)
        snack.assert_called_once()

    def test_on_language_change_persist_failure_skips_i18n_set(self, mock_page):
        """ConfigHandler.set_locale 返回 False 时，不切换 I18n，回滚 dropdown，显示失败提示。"""
        snack = MagicMock()
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.show_snack = snack
        tab._safe_update = MagicMock()
        self.mock_ch.set_locale.return_value = False
        self.mock_i18n.current_locale.return_value = "zh_CN"
        tab.language_dropdown.value = "en_US"

        tab.on_language_change(None)

        self.mock_i18n.set_locale.assert_not_called()
        assert tab.language_dropdown.value == "zh_CN"
        snack.assert_called_once_with("settings_language_save_failed", color=self.mock_ac.ERROR)

    def test_did_mount_subscribes_i18n(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.did_mount()
        self.mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_unsubscribes_i18n(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._locale_subscription_id = "test_id"
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_with("test_id")

    def test_language_dropdown_initial_value(self, mock_page):
        self.mock_ch.get_locale.return_value = "zh_CN"
        tab = self._make_tab()
        assert tab.language_dropdown.value == "zh_CN"

    def test_language_dropdown_has_two_options(self, mock_page):
        tab = self._make_tab()
        assert len(tab.language_dropdown.options) == 2

    def test_on_locale_change_updates_labels(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_locale_change("en_US")
        self.mock_i18n.get.assert_called()  # 多次调用预期 (多个标签翻译)

    def test_on_locale_change_exception_handled(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        self.mock_i18n.get.side_effect = Exception("locale error")
        tab._on_locale_change("en_US")


class TestDataSourceTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        from ui.components.settings_widgets import ActionChip, MetricCard

        self.patches = [
            patch("ui.views.settings_tabs.data_source_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.data_source_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.data_source_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.data_source_tab.DataSourceViewModel"),
            patch("ui.views.settings_tabs.data_source_tab.TaskManager"),
            patch("ui.views.settings_tabs.data_source_tab.TushareConfigPanel", MagicMock()),
            patch("ui.views.settings_tabs.data_source_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.data_source_tab.SectionHeader", MagicMock()),
            patch("ui.views.settings_tabs.data_source_tab.SettingRow", MagicMock()),
            patch("ui.views.settings_tabs.data_source_tab.ActionChip", ActionChip),
            patch("ui.views.settings_tabs.data_source_tab.MetricCard", MetricCard),
            patch("ui.views.settings_tabs.data_source_tab.UILogger"),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.data_source_tab import DataSourceTab

        return DataSourceTab(show_snack_callback=MagicMock())

    def test_on_tushare_save_delegates_to_vm(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_tushare_save({"token": "abc123"})
        tab.vm.save_tushare_token.assert_called_with("abc123")

    def test_on_tushare_save_empty_token_skips(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab._on_tushare_save({"token": "  "})
        tab.vm.save_tushare_token.assert_not_called()

    def test_on_history_years_change_delegates_to_vm(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        e = MagicMock()
        e.control.value = "3"
        tab.on_history_years_change(e)
        tab.vm.set_history_years.assert_called_with(3)

    def test_vm_sync_busy_changes_buttons(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        # Simulate the callback directly
        tab._on_vm_sync_busy_changed(True, "daily_sync")
        assert tab.action_full_sync.disabled is True or tab.action_full_sync.opacity == 0.5

    def test_did_mount_binds_vm(self, mock_page):
        tab = self._make_tab()
        tab.tushare_panel = MagicMock()
        tab._tm = MagicMock()
        tab._on_mount()
        tab.vm.bind.assert_called_once()
        self.mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_disposes_vm(self, mock_page):
        tab = self._make_tab()
        tab._tm = MagicMock()
        tab._on_unmount()
        tab.vm.dispose.assert_called_once()
        self.mock_i18n.unsubscribe.assert_called_once()

    def test_vm_recover_stale_state_called_on_mount(self, mock_page):
        tab = self._make_tab()
        tab.tushare_panel = MagicMock()
        tab._tm = MagicMock()
        tab._on_mount()
        tab.vm.recover_stale_state.assert_called_once()

    def test_update_theme(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.metric_sync = MagicMock()
        tab.metric_coverage = MagicMock()
        tab.metric_health = MagicMock()
        tab.metric_storage = MagicMock()
        tab.update_theme()
        tab.metric_sync.update_theme.assert_called_once()


class TestDatabaseTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.database_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.database_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.database_tab.DatabaseConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.database_tab import DatabaseTab

        return DatabaseTab(show_snack_callback=MagicMock())

    def test_instantiation_creates_config_panel(self, mock_page):
        tab = self._make_tab()
        assert tab.config_panel is not None

    def test_on_save_calls_show_snack_with_success(self, mock_page):
        snack = MagicMock()
        tab = self._make_tab()
        tab.show_snack = snack
        tab._on_save({"host": "localhost"})
        snack.assert_called_once_with("Database configuration saved", "success")

    def test_on_test_success_logs_debug(self, mock_page, caplog):
        tab = self._make_tab()
        with caplog.at_level(logging.DEBUG, logger="ui.views.settings_tabs.database_tab"):
            tab._on_test_success({"host": "localhost", "port": 5432, "database": "test"})
        assert any("Database connection test successful" in r.message for r in caplog.records)

    def test_on_mount_calls_reload_config(self, mock_page):
        tab = self._make_tab()
        tab._on_mount()
        tab.config_panel.reload_config.assert_called_once()


class TestNotificationsTab:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles, mock_config_handler):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = mock_config_handler
        self.mock_ch.get_config.side_effect = lambda key, default=None: default
        self.patches = [
            patch("ui.views.settings_tabs.automation_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.automation_tab.AppColors", self.mock_ac),
            patch("ui.views.settings_tabs.automation_tab.AppStyles", self.mock_as),
            patch("ui.views.settings_tabs.automation_tab.ConfigHandler", self.mock_ch),
            patch("ui.views.settings_tabs.automation_tab.DashboardCard", MagicMock()),
            patch("ui.views.settings_tabs.automation_tab.SettingRow", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.automation_tab import NotificationsTab

        return NotificationsTab(show_snack_callback=MagicMock(), page_ref=None)

    def test_instantiation_creates_ui_elements(self, mock_page):
        tab = self._make_tab()
        assert tab.news_alerts_enabled is not None
        assert tab.news_interval is not None

    def test_toggle_notification_saves_config(self, mock_page):
        tab = self._make_tab()
        set_page(tab, mock_page)
        tab.news_alerts_enabled.value = True
        tab.on_news_toggle(None)
        self.mock_ch.save_config.assert_called_with({"enable_news_alerts": True})

    def test_did_mount_subscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab.did_mount()
        self.mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_unsubscribes_i18n(self, mock_page):
        tab = self._make_tab()
        tab._locale_subscription_id = "sub_id"
        tab.will_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
