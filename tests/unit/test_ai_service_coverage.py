import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock

from services.ai_service import (
    AIService,
    _check_reasoning_support,
)


@pytest.fixture(autouse=True)
def reset_ai_singleton():
    AIService._reset_singleton()
    yield
    AIService._reset_singleton()


def _make_svc_with_cloud():
    with patch("services.ai_service.ConfigHandler") as mock_ch:
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
            "model": "deepseek-v4-flash",
        }
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 5
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
    return svc


class TestCheckReasoningSupportFallback:
    def test_fallback_list_match(self):
        with (
            patch("services.ai_service.LITELLM_AVAILABLE", True),
            patch("services.ai_service.litellm.utils.supports_reasoning", side_effect=Exception("err")),
        ):
            assert _check_reasoning_support("deepseek-v4-pro") is True

    def test_fallback_list_no_match(self):
        with (
            patch("services.ai_service.LITELLM_AVAILABLE", True),
            patch("services.ai_service.litellm.utils.supports_reasoning", side_effect=Exception("err")),
        ):
            assert _check_reasoning_support("some-random-model") is False

    def test_litellm_supports_reasoning_true(self):
        with (
            patch("services.ai_service.LITELLM_AVAILABLE", True),
            patch("services.ai_service.litellm.utils.supports_reasoning", return_value=True),
        ):
            assert _check_reasoning_support("deepseek-v4-pro") is True


class TestConfigureLitellm:
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_configure_sets_params(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
            "model": "deepseek-v4-flash",
        }
        mock_ch.get_setting.return_value = False
        with patch("services.ai_service.litellm") as mock_litellm:
            AIService()
            assert mock_litellm.drop_params is True


class TestSetupClientDeep:
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_no_api_key(self, mock_ch):
        mock_ch.get_llm_config.return_value = {"provider": "deepseek", "base_url": "http://api.test.com"}
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is False

    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_azure_success_with_resource(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "azure",
            "azure_resource_name": "myres",
            "azure_deployment_name": "mydeploy",
        }
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is True
        assert "myres" in svc._litellm_config.get("base_url", "")


class TestBuildLiteLLMParamsAzureFallback:
    def test_azure_no_resource_name_uses_base_url(self):
        llm_config = {
            "provider": "azure",
            "model": "mydeploy",
            "api_key": "key",
            "base_url": "http://custom.azure.com",
            "azure_resource_name": "",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["api_base"] == "http://custom.azure.com"


class TestBuildLiteLLMParamsResponseFormat:
    def test_response_format(self):
        llm_config = {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        assert params["response_format"] == {"type": "json_object"}


class TestChatCompletionLocal:
    @pytest.mark.asyncio
    async def test_local_model_not_loaded_raises(self):
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = None
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch.object(svc, "_setup_local_model", AsyncMock()),
        ):
            with pytest.raises(ValueError, match="Local model not loaded"):
                await svc._chat_completion(
                    messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}],
                    provider="local",
                    json_mode=False,
                )

    @pytest.mark.asyncio
    async def test_local_model_success(self):
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = "/path/to/model"
        mock_manager.run_inference = AsyncMock(return_value='{"category": "tech"}')
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch.object(svc, "_setup_local_model", AsyncMock()),
        ):
            result = await svc._chat_completion(
                messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}],
                provider="local",
                json_mode=True,
            )
            assert result["category"] == "tech"


class TestChatCompletionCloudNotAvailable:
    @pytest.mark.asyncio
    async def test_raises_value_error(self):
        svc = _make_svc_with_cloud()
        svc._is_cloud_configured = False
        svc._litellm_config = {}
        with pytest.raises(ValueError, match="Cloud LLM not configured"):
            await svc._chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                provider="cloud",
            )


class TestChatCompletionJsonParsing:
    @pytest.mark.asyncio
    async def test_json_heuristic_extraction(self):
        svc = _make_svc_with_cloud()
        raw = 'Some text before {"score": 80, "rec": "buy"} some after'
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": raw})):
            result = await svc._chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                provider="cloud",
                json_mode=True,
            )
            assert result["score"] == 80

    @pytest.mark.asyncio
    async def test_json_invalid_raises_value_error(self):
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": "no json here"})):
            with pytest.raises(ValueError, match="Invalid JSON response"):
                await svc._chat_completion(
                    messages=[{"role": "user", "content": "hello"}],
                    provider="cloud",
                    json_mode=True,
                )

    @pytest.mark.asyncio
    async def test_non_json_mode_returns_content_dict(self):
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": "hello world"})):
            result = await svc._chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                provider="cloud",
                json_mode=False,
            )
            assert result == {"content": "hello world"}


class TestChatCompletionLitellmNonStream:
    @pytest.mark.asyncio
    async def test_non_stream_response(self):
        svc = _make_svc_with_cloud()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"score": 90}'
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 10
        mock_usage.completion_tokens = 20
        mock_usage.total_tokens = 30
        mock_response.usage = mock_usage
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc._chat_completion_litellm(
                messages=[{"role": "user", "content": "hello"}],
            )
            assert result["content"] == '{"score": 90}'
            assert result["usage"]["total_tokens"] == 30


class TestChatCompletionLitellmStream:
    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self):
        svc = _make_svc_with_cloud()
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        delta1 = MagicMock()
        delta1.content = "Hello"
        delta1.reasoning_content = "thinking"
        chunk1.choices[0].delta = delta1
        chunk1.usage = None

        chunk2 = MagicMock()
        chunk2.choices = []
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 10
        mock_usage.total_tokens = 15
        chunk2.usage = mock_usage

        async def mock_stream(**kwargs):
            for c in [chunk1, chunk2]:
                yield c

        on_chunk = MagicMock()
        with (
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("services.ai_service._check_reasoning_support", return_value=True),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc._chat_completion_litellm(
                messages=[{"role": "user", "content": "hello"}],
                on_chunk=on_chunk,
            )
            assert "content" in result
            assert "reasoning_content" in result

    @pytest.mark.asyncio
    async def test_stream_no_reasoning(self):
        svc = _make_svc_with_cloud()
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        delta1 = MagicMock()
        delta1.content = "Response text"
        chunk1.choices[0].delta = delta1

        async def mock_stream(**kwargs):
            yield chunk1

        with (
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("services.ai_service._check_reasoning_support", return_value=False),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc._chat_completion_litellm(
                messages=[{"role": "user", "content": "hello"}],
                on_chunk=MagicMock(),
            )
            assert result["content"] == "Response text"

    @pytest.mark.asyncio
    async def test_stream_only_reasoning_fills_content(self):
        svc = _make_svc_with_cloud()
        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        delta1 = MagicMock()
        delta1.content = None
        delta1.reasoning_content = "deep thought"
        chunk1.choices[0].delta = delta1

        async def mock_stream(**kwargs):
            yield chunk1

        with (
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("services.ai_service._check_reasoning_support", return_value=True),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc._chat_completion_litellm(
                messages=[{"role": "user", "content": "hello"}],
                on_chunk=MagicMock(),
            )
            assert result["content"] == "deep thought"


class TestChatCompletionLitellmPromptTooLarge:
    @pytest.mark.asyncio
    async def test_warns_on_large_prompt(self):
        svc = _make_svc_with_cloud()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_response.usage = None
        long_msg = [{"role": "user", "content": "x" * 300000}]
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc._chat_completion_litellm(messages=long_msg)
            assert result["content"] == "ok"


class TestAnalyzeStockDeepBranches:
    @pytest.mark.asyncio
    async def test_concepts_none_removed(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "concepts": None},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_concepts_empty_list_removed(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "concepts": []},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_concepts_exception_fallback(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})

        class BadDict(dict):
            def get(self, key, default=None):
                if key == "concepts":
                    raise Exception("concepts error")
                return super().get(key, default)

        with patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"):
            result = await svc.analyze_stock(
                stock_info=BadDict({"ts_code": "000001.SZ"}),
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_learning_context_fetch_failed(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with (
            patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"),
            patch("data.persistence.review_manager.ReviewManager", side_effect=Exception("rm error")),
        ):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
                include_learning_context=True,
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_include_learning_context_false(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
                include_learning_context=False,
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_no_strategy_key_no_override(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("services.ai_service.ConfigHandler") as mock_ch:
            mock_ch.get_ai_system_prompt.return_value = "default prompt"
            mock_ch.get_setting.return_value = False
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    async def test_timeout_error_returns_error(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(side_effect=TimeoutError("timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0

    @pytest.mark.asyncio
    async def test_httpx_timeout_returns_error(self):
        import httpx

        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"

    @pytest.mark.asyncio
    async def test_general_exception_returns_error(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(side_effect=RuntimeError("boom"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ"},
            tech_info={},
            news_list=[],
        )
        assert result["score"] == 0
        assert result is not None and "error" in result


class TestClassifyNewsDeepBranches:
    @pytest.mark.asyncio
    async def test_local_fails_with_not_configured_fallback_cloud(self):
        svc = _make_svc_with_cloud()
        call_count = 0

        async def mock_chat(messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if kwargs.get("provider") == "local":
                raise ValueError("Local model not configured")
            return {"category_L1": "finance", "emoji": "📊", "sentiment": "Positive"}

        svc._chat_completion = AsyncMock(side_effect=mock_chat)
        with (
            patch("services.ai_service.ConfigHandler.get_ai_news_prompt", return_value="Classify news"),
            patch("core.i18n.I18n.get", side_effect=lambda k, d=None: d if d is not None else k),
        ):
            result = await svc.classify_news("Some news text")
        assert "category" in result

    @pytest.mark.asyncio
    async def test_local_fails_with_not_installed_fallback_cloud(self):
        svc = _make_svc_with_cloud()

        async def mock_chat(messages, **kwargs):
            if kwargs.get("provider") == "local":
                raise Exception("ollama not installed")
            return {"category_L1": "tech", "emoji": "💻", "sentiment": "Neutral"}

        svc._chat_completion = AsyncMock(side_effect=mock_chat)
        with (
            patch("services.ai_service.ConfigHandler.get_ai_news_prompt", return_value="Classify news"),
            patch("core.i18n.I18n.get", side_effect=lambda k, d=None: d if d is not None else k),
        ):
            result = await svc.classify_news("Tech news")
        assert "category" in result

    @pytest.mark.asyncio
    async def test_all_providers_failed(self):
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(side_effect=Exception("all fail"))
        with patch("services.ai_service.ConfigHandler.get_ai_news_prompt", return_value="Classify news"):
            result = await svc.classify_news("Some news")
        assert result["category"] == "unknown"
        assert "error" in result


class TestVerifyConnectionDeep:
    @pytest.mark.asyncio
    async def test_verify_success(self):
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": "ok"})):
            result = await svc.verify_connection()
            assert result is True

    @pytest.mark.asyncio
    async def test_verify_exception_raises(self):
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(side_effect=Exception("conn err"))):
            with pytest.raises(Exception, match="conn err"):
                await svc.verify_connection()


class TestTestConnectionDeep:
    @pytest.mark.asyncio
    async def test_no_model_returns_false(self):
        result = await AIService.test_connection(api_key="key", model="")
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    async def test_success_with_usage(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_usage = MagicMock()
        mock_usage.prompt_tokens = 5
        mock_usage.completion_tokens = 1
        mock_usage.total_tokens = 6
        mock_response.usage = mock_usage
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)),
            patch("services.ai_service._check_reasoning_support", return_value=False),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await AIService.test_connection(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="http://api.test.com",
                api_key="test-key",
            )
            assert result["success"] is True
            assert "usage" in result

    @pytest.mark.asyncio
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    async def test_success_with_reasoning(self):
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "ok"
        mock_response.usage = None
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)),
            patch("services.ai_service._check_reasoning_support", return_value=True),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await AIService.test_connection(
                provider="deepseek",
                model="deepseek-v4-pro",
                base_url="http://api.test.com",
                api_key="test-key",
            )
            assert result["success"] is True
            assert result.get("reasoning_supported") is True

    @pytest.mark.asyncio
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    async def test_exception_returns_error(self):
        with (
            patch("services.ai_service.acompletion", AsyncMock(side_effect=Exception("conn fail"))),
            patch("services.ai_service._check_reasoning_support", return_value=False),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await AIService.test_connection(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="http://api.test.com",
                api_key="test-key",
            )
            assert result["success"] is False
            assert "error_code" in result


class TestSetupLocalModel:
    @pytest.mark.asyncio
    async def test_loads_model_from_config(self):
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = None
        mock_manager.load_model = AsyncMock()
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch("services.ai_service.ConfigHandler.get_setting", return_value="/path/to/model"),
        ):
            await svc._setup_local_model()
            mock_manager.load_model.assert_called_once_with("/path/to/model")

    @pytest.mark.asyncio
    async def test_skips_if_already_loaded(self):
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = "/already/loaded"
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch("services.ai_service.ConfigHandler.get_setting", return_value="/path/to/model"),
        ):
            await svc._setup_local_model()
            mock_manager.load_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_config_path_skips(self):
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = None
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch("services.ai_service.ConfigHandler.get_setting", return_value=None),
        ):
            await svc._setup_local_model()
            mock_manager.load_model.assert_not_called()


class TestGetSemaphore:
    @pytest.mark.asyncio
    async def test_returns_semaphore(self):
        svc = _make_svc_with_cloud()
        with patch("services.ai_service.ConfigHandler.get_ai_max_concurrent_analysis", return_value=3):
            sem = await svc._get_semaphore()
            assert isinstance(sem, asyncio.Semaphore)


class TestReloadConfigDeep:
    @pytest.mark.asyncio
    async def test_resets_local_model_and_semaphore(self):
        svc = _make_svc_with_cloud()
        with patch("services.ai_service.ConfigHandler") as mock_ch:
            mock_ch.get_llm_config.return_value = {
                "api_key": "key",
                "provider": "deepseek",
                "base_url": "http://api.test.com",
                "model": "deepseek-v4-flash",
            }
            mock_ch.get_setting.return_value = False
            await svc.reload_config()
        assert svc._local_model_loaded is False
