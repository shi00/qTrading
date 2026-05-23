"""ui/views/settings_tabs/ai_brain_tab.py 单元测试"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestAIBrainTabEventHandlers:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService"),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.ft.Column"),
        ):
            return AIBrainTab(show_snack_callback=MagicMock())

    def test_on_llm_config_saved(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()

        tab._on_llm_config_saved()
        tab.show_snack.assert_called_once()

    def test_on_local_model_saved(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()

        tab._on_local_model_saved()
        tab.show_snack.assert_called_once()

    def test_reset_news_prompt(self):
        from utils.config_models import DEFAULT_NEWS_PROMPT

        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.ai_news_prompt_input = MagicMock()
        tab._safe_update = MagicMock()

        tab._reset_news_prompt(None)
        tab.show_snack.assert_called_once()
        tab._safe_update.assert_called_once()
        assert tab.ai_news_prompt_input.value == DEFAULT_NEWS_PROMPT

    def test_reset_ai_prompt(self):
        from utils.config_models import DEFAULT_AI_PROMPT

        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab._safe_update = MagicMock()

        tab._reset_ai_prompt(None)
        tab.show_snack.assert_called_once()
        tab._safe_update.assert_called_once()
        assert tab.ai_prompt_input.value == DEFAULT_AI_PROMPT

    @pytest.mark.asyncio
    async def test_on_llm_test_connection(self):
        with patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            tab = self._make_tab()

            result = await tab._on_llm_test_connection(
                provider="openai",
                model="gpt-4",
                base_url="https://api.openai.com",
                api_key="test-key",
            )
            assert result == {"success": True}


class TestAIBrainTabSaveAISettings:
    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.patches = [
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_tab(self):
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService"),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.ft.Column"),
        ):
            return AIBrainTab(show_snack_callback=MagicMock())

    @pytest.mark.asyncio
    async def test_save_ai_settings_empty_fields(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = ""
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = ""
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = ""

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_max_candidates(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = ""
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = "99999"
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = "1000"

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_min_turnover(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = ""
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = "50"
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = "-100"

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_value_error(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = ""
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = "not_a_number"
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = "1000"

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_concurrency(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = ""
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = "50"
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = "1000"
        tab.ai_concurrency_input = MagicMock()
        tab.ai_concurrency_input.value = "999"

        with patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"):
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_with_api_key(self):
        tab = self._make_tab()
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = {"api_key": "test-key"}
        tab.llm_config_panel.save_current_config = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.save_config = MagicMock()
        tab.local_model_panel.get_current_config.return_value = {}
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = "test prompt"
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = "50"
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = "1000"
        tab.ai_concurrency_input = MagicMock()
        tab.ai_concurrency_input.value = "5"
        tab.ai_news_prompt_input = MagicMock()
        tab.ai_news_prompt_input.value = ""
        tab._safe_update = MagicMock()

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("utils.prompt_guard.validate_prompt", return_value=(True, None)),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            tab.llm_config_panel.save_current_config.assert_called_once()
