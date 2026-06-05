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
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService"),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.ft.Column"),
        ):
            mock_ch.get_ai_max_candidates.return_value = 30
            mock_ch.get_strategy_min_turnover.return_value = 2.0
            mock_ch.get_ai_max_concurrent_analysis.return_value = 5
            mock_ch.get_ai_news_max_concurrent.return_value = 1
            mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
            mock_ch.get_ai_news_prompt.return_value = "Classify this news."
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

    def test_init_exception_sets_error_content(self):
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService"),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.I18n", self.mock_i18n),
            patch("ui.views.settings_tabs.ai_brain_tab.AppColors", self.mock_ac),
            patch.object(AIBrainTab, "_build_controls", side_effect=RuntimeError("boom")),
        ):
            tab = AIBrainTab(show_snack_callback=MagicMock())
            assert tab.content is not None
            assert "boom" in str(tab.content.value) or "Error" in str(tab.content.value)


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
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService"),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager"),
            patch("ui.views.settings_tabs.ai_brain_tab.ft.Column"),
        ):
            mock_ch.get_ai_max_candidates.return_value = 30
            mock_ch.get_strategy_min_turnover.return_value = 2.0
            mock_ch.get_ai_max_concurrent_analysis.return_value = 5
            mock_ch.get_ai_news_max_concurrent.return_value = 1
            mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
            mock_ch.get_ai_news_prompt.return_value = "Classify this news."
            return AIBrainTab(show_snack_callback=MagicMock())

    def _setup_save_mocks(
        self,
        tab,
        *,
        max_cand="50",
        min_turn="2.0",
        concurrency="5",
        news_concurrency="1",
        prompt="test prompt",
        news_prompt="news",
        api_key=None,
        local_model_config=None,
    ):
        tab.show_snack = MagicMock()
        tab.llm_config_panel = MagicMock()
        tab.llm_config_panel.get_current_config.return_value = (
            {"provider": "openai", "model": "gpt-4", "base_url": "https://api.openai.com", "api_key": api_key}
            if api_key
            else {"provider": "openai", "model": "gpt-4", "base_url": "https://api.openai.com", "api_key": ""}
        )
        tab.llm_config_panel._is_azure = False
        tab.llm_config_panel._api_key_modified = bool(api_key)
        tab.llm_config_panel._build_custom_models_update.return_value = None
        tab.llm_config_panel._remove_primary_from_failover = MagicMock()
        tab.llm_config_panel._sync_provider_credential_to_failover = MagicMock()
        tab.local_model_panel = MagicMock()
        tab.local_model_panel.get_current_config.return_value = (
            local_model_config if local_model_config is not None else {}
        )
        tab.ai_prompt_input = MagicMock()
        tab.ai_prompt_input.value = prompt
        tab.ai_max_candidates_input = MagicMock()
        tab.ai_max_candidates_input.value = max_cand
        tab.strategy_min_turnover_input = MagicMock()
        tab.strategy_min_turnover_input.value = min_turn
        tab.ai_concurrency_input = MagicMock()
        tab.ai_concurrency_input.value = concurrency
        tab.ai_news_concurrency_input = MagicMock()
        tab.ai_news_concurrency_input.value = news_concurrency
        tab.ai_news_prompt_input = MagicMock()
        tab.ai_news_prompt_input.value = news_prompt
        tab._safe_update = MagicMock()

    def _make_thread_pool_mock(self):
        """创建一个 ThreadPoolManager mock，使 run_async 直接执行函数"""
        mock_tpm = MagicMock()
        mock_tpm.run_async = AsyncMock(side_effect=lambda task_type, func, *args, **kwargs: func(*args, **kwargs))
        return mock_tpm

    @pytest.mark.asyncio
    async def test_save_ai_settings_empty_fields(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="", min_turn="")

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_max_candidates(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="99999", min_turn="2.0")

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_min_turnover(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="50", min_turn="-100")

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_value_error(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="not_a_number", min_turn="2.0")

        await tab._save_ai_settings(None)
        tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_concurrency(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="50", min_turn="2.0", concurrency="999")

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_concurrency_value_error(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="50", min_turn="2.0", concurrency="abc")

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_invalid_news_concurrency(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="50", min_turn="2.0", concurrency="5", news_concurrency="999")

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_news_concurrency_value_error(self):
        tab = self._make_tab()
        self._setup_save_mocks(tab, max_cand="50", min_turn="2.0", concurrency="5", news_concurrency="abc")

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_ai_settings_prompt_validation_fail(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab, max_cand="50", min_turn="2.0", concurrency="5", news_concurrency="1", prompt="bad prompt"
        )

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(False, "prompt_err_injection")),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()
            call_args = tab.show_snack.call_args[0][0]
            assert "prompt_err_injection" in call_args

    @pytest.mark.asyncio
    async def test_save_ai_settings_prompt_length_error(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab, max_cand="50", min_turn="2.0", concurrency="5", news_concurrency="1", prompt="x" * 9000
        )

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(False, "prompt_err_length")),
            patch("utils.prompt_guard.MAX_PROMPT_LENGTH", 8000),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()
            call_args = tab.show_snack.call_args[0][0]
            assert "prompt_err_length" in call_args

    @pytest.mark.asyncio
    async def test_save_ai_settings_news_prompt_validation_fail(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            news_prompt="bad news prompt",
        )

        call_count = [0]

        def mock_validate(prompt, **kwargs):
            call_count[0] += 1
            # AI prompt (first call) passes, news prompt (second call) fails
            if call_count[0] == 1:
                return (True, None)
            return (False, "prompt_err_injection")

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler"),
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", side_effect=mock_validate),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()
            call_args = tab.show_snack.call_args[0][0]
            assert "prompt_err_injection" in call_args

    @pytest.mark.asyncio
    async def test_save_success_no_local_model(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            news_prompt="news prompt",
        )

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            # 验证直接调用 ConfigHandler.save_llm_config（而非 panel.save_current_config）
            mock_ch.save_llm_config.assert_called_once()
            mock_ch.save_local_ai_config.assert_called_once()
            mock_ch.save_config.assert_any_call(
                {
                    "ai_max_candidates": 50,
                    "strategy_min_turnover": 2.0,
                    "ai_max_concurrent_analysis": 5,
                    "ai_news_max_concurrent": 1,
                }
            )
            mock_ch.save_ai_system_prompt.assert_called_once_with("valid prompt")
            mock_ch.set_ai_news_prompt.assert_called_once_with("news prompt")
            mock_ai.return_value.reload_config.assert_awaited_once()
            tab.show_snack.assert_called()

    @pytest.mark.asyncio
    async def test_save_success_with_api_key(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            api_key="sk-test",
        )

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=self._make_thread_pool_mock()),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            await tab._save_ai_settings(None)
            # 验证 save_llm_config 被调用且 api_key 传入（因为 _api_key_modified=True）
            mock_ch.save_llm_config.assert_called_once()
            call_kwargs = mock_ch.save_llm_config.call_args
            assert call_kwargs.kwargs.get("api_key") == "sk-test" or call_kwargs[1].get("api_key") == "sk-test"
            # 验证 _api_key_modified 被重置
            assert tab.llm_config_panel._api_key_modified is False

    @pytest.mark.asyncio
    async def test_save_success_local_model_file_not_found(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            local_model_config={"model_path": "/nonexistent/model.gguf"},
        )

        # 创建 mock 使 run_async 调用 os.path.exists 时返回 False
        mock_tpm = MagicMock()

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=mock_tpm),
        ):
            mock_ai.return_value.reload_config = AsyncMock()

            async def mock_run_async(task_type, func, *args, **kwargs):
                func_name = getattr(func, "__name__", "")
                if func_name in ("exists", "_path_exists"):
                    return False
                return func(*args, **kwargs)

            mock_tpm.run_async = AsyncMock(side_effect=mock_run_async)

            await tab._save_ai_settings(None)
            mock_ch.save_config.assert_any_call(
                {
                    "ai_max_candidates": 50,
                    "strategy_min_turnover": 2.0,
                    "ai_max_concurrent_analysis": 5,
                    "ai_news_max_concurrent": 1,
                }
            )
            tab.show_snack.assert_called()
            last_call_arg = tab.show_snack.call_args_list[-1][0][0]
            assert "ai_model_file_not_found" in last_call_arg

    @pytest.mark.asyncio
    async def test_save_success_local_model_changed_md5(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            local_model_config={"model_path": "/path/to/model.gguf"},
        )

        mock_local_mgr = MagicMock()
        mock_local_mgr.get_loaded_model_md5.return_value = "old_md5"

        mock_tpm = MagicMock()

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager") as mock_lmm,
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=mock_tpm),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            mock_lmm.get_instance = AsyncMock(return_value=mock_local_mgr)

            async def mock_run_async(task_type, func, *args, **kwargs):
                func_name = getattr(func, "__name__", "")
                if func_name in ("exists", "_path_exists"):
                    return True
                if func is mock_lmm.calculate_file_md5:
                    return "new_md5"
                return func(*args, **kwargs)

            mock_tpm.run_async = AsyncMock(side_effect=mock_run_async)

            await tab._save_ai_settings(None)
            mock_ch.save_config.assert_any_call(
                {
                    "ai_max_candidates": 50,
                    "strategy_min_turnover": 2.0,
                    "ai_max_concurrent_analysis": 5,
                    "ai_news_max_concurrent": 1,
                }
            )
            tab.show_snack.assert_called()
            last_call_arg = tab.show_snack.call_args_list[-1][0][0]
            assert "ai_local_model_changed" in last_call_arg

    @pytest.mark.asyncio
    async def test_save_success_local_model_same_md5(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            local_model_config={"model_path": "/path/to/model.gguf"},
        )

        mock_local_mgr = MagicMock()
        mock_local_mgr.get_loaded_model_md5.return_value = "same_md5"

        mock_tpm = MagicMock()

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager") as mock_lmm,
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=mock_tpm),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            mock_lmm.get_instance = AsyncMock(return_value=mock_local_mgr)

            async def mock_run_async(task_type, func, *args, **kwargs):
                func_name = getattr(func, "__name__", "")
                if func_name in ("exists", "_path_exists"):
                    return True
                if func is mock_lmm.calculate_file_md5:
                    return "same_md5"
                return func(*args, **kwargs)

            mock_tpm.run_async = AsyncMock(side_effect=mock_run_async)

            await tab._save_ai_settings(None)
            mock_ch.save_config.assert_any_call(
                {
                    "ai_max_candidates": 50,
                    "strategy_min_turnover": 2.0,
                    "ai_max_concurrent_analysis": 5,
                    "ai_news_max_concurrent": 1,
                }
            )
            tab.show_snack.assert_called()
            last_call_arg = tab.show_snack.call_args_list[-1][0][0]
            assert "settings_snack_ai_saved" in last_call_arg

    @pytest.mark.asyncio
    async def test_save_success_local_model_no_loaded_md5(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab,
            max_cand="50",
            min_turn="2.0",
            concurrency="5",
            news_concurrency="1",
            prompt="valid prompt",
            local_model_config={"model_path": "/path/to/model.gguf"},
        )

        mock_local_mgr = MagicMock()
        mock_local_mgr.get_loaded_model_md5.return_value = None

        mock_tpm = MagicMock()

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.AIService") as mock_ai,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.LocalModelManager") as mock_lmm,
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=mock_tpm),
        ):
            mock_ai.return_value.reload_config = AsyncMock()
            mock_lmm.get_instance = AsyncMock(return_value=mock_local_mgr)

            async def mock_run_async(task_type, func, *args, **kwargs):
                func_name = getattr(func, "__name__", "")
                if func_name in ("exists", "_path_exists"):
                    return True
                if func is mock_lmm.calculate_file_md5:
                    return "some_md5"
                return func(*args, **kwargs)

            mock_tpm.run_async = AsyncMock(side_effect=mock_run_async)

            await tab._save_ai_settings(None)
            mock_ch.save_config.assert_any_call(
                {
                    "ai_max_candidates": 50,
                    "strategy_min_turnover": 2.0,
                    "ai_max_concurrent_analysis": 5,
                    "ai_news_max_concurrent": 1,
                }
            )
            tab.show_snack.assert_called()
            last_call_arg = tab.show_snack.call_args_list[-1][0][0]
            assert "settings_snack_ai_saved" in last_call_arg

    @pytest.mark.asyncio
    async def test_save_general_exception(self):
        tab = self._make_tab()
        self._setup_save_mocks(
            tab, max_cand="50", min_turn="2.0", concurrency="5", news_concurrency="1", prompt="valid prompt"
        )

        # 创建 mock 使 run_async 直接执行函数（会在执行时抛出异常）
        mock_tpm = MagicMock()

        async def mock_run_async(task_type, func, *args, **kwargs):
            return func(*args, **kwargs)

        mock_tpm.run_async = AsyncMock(side_effect=mock_run_async)

        with (
            patch("ui.views.settings_tabs.ai_brain_tab.ConfigHandler") as mock_ch,
            patch("ui.views.settings_tabs.ai_brain_tab.validate_prompt", return_value=(True, None)),
            patch("ui.views.settings_tabs.ai_brain_tab.ThreadPoolManager", return_value=mock_tpm),
        ):
            mock_ch.save_ai_system_prompt.side_effect = RuntimeError("disk full")
            await tab._save_ai_settings(None)
            tab.show_snack.assert_called()
            last_call_arg = tab.show_snack.call_args_list[-1][0][0]
            assert "settings_snack_ai_error" in last_call_arg
