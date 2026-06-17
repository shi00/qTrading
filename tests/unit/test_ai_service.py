import pytest
import httpx
from unittest.mock import patch, AsyncMock, MagicMock

from services.ai_service import (
    AIService,
    LITELLM_AVAILABLE,
    _check_reasoning_support,
    _classify_api_error,
    STRATEGY_CONTEXT_MAX_LEN,
    VALID_RECOMMENDATIONS,
    validate_ai_analysis_response,
)


def _make_svc_with_cloud():
    """Factory: create AIService with cloud provider pre-configured.

    Merged from test_ai_service_coverage.py (P2-1). Used by deep-branch
    tests that need a clean cloud-configured service without the verbose
    inline @patch pattern. Singleton isolation is handled by the
    _reset_all_singletons autouse fixture in tests/unit/conftest.py.
    """
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


class TestAIServiceInit:
    @patch("services.ai_service.ConfigHandler")
    def test_init(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-5.4"
        svc = AIService()
        assert svc is not None


class TestAIServiceBuildLiteLLMParams:
    @patch("services.ai_service.ConfigHandler")
    def test_basic_params(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-5.4"
        mock_ch.get_ai_api_key.return_value = "test-key"
        mock_ch.get_ai_base_url.return_value = ""
        svc = AIService()
        llm_config = {
            "provider": "openai",
            "model": "gpt-5.4",
            "api_key": "test-key",
            "base_url": "",
        }
        params = svc._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hello"}],
        )
        assert "model" in params
        assert "messages" in params


class TestAIServiceClassifyNews:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_classify_news(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-5.4"
        mock_ch.get_ai_api_key.return_value = "test-key"
        mock_ch.get_ai_base_url.return_value = ""
        svc = AIService()
        svc._chat_completion = AsyncMock(
            return_value={
                "content": '{"emoji": "📊", "category": "Finance"}',
            }
        )
        result = await svc.classify_news("某公司发布财报")
        assert "emoji" in result or "category" in result


class TestAIServiceConstants:
    def test_litellm_available_is_bool(self):
        assert isinstance(LITELLM_AVAILABLE, bool)


class TestAIServiceSafeTruncate:
    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.LITELLM_AVAILABLE", False)
    def test_short_text(self, mock_ch):
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._safe_truncate("hello", 10) == "hello"

    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.LITELLM_AVAILABLE", False)
    def test_long_text_truncated(self, mock_ch):
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_setting.return_value = False
        svc = AIService()
        result = svc._safe_truncate("a" * 100, 10)
        assert len(result) > 10
        assert result.endswith("...(truncated)")

    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.LITELLM_AVAILABLE", False)
    def test_empty_text(self, mock_ch):
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._safe_truncate("", 10) == ""


class TestCheckReasoningSupport:
    def test_no_litellm(self):
        with patch("services.ai_service.LITELLM_AVAILABLE", False):
            assert _check_reasoning_support("deepseek-v4-pro") is False


class TestClassifyApiError:
    def test_returns_dict(self):
        result = _classify_api_error(Exception("test"))
        assert isinstance(result, dict)
        assert "code" in result
        assert "message_key" in result


class TestStrategyContextMaxLen:
    def test_positive(self):
        assert STRATEGY_CONTEXT_MAX_LEN > 0


class TestValidRecommendations:
    def test_contains_standard(self):
        assert "buy" in VALID_RECOMMENDATIONS
        assert "hold" in VALID_RECOMMENDATIONS
        assert "sell" in VALID_RECOMMENDATIONS
        assert "neutral" in VALID_RECOMMENDATIONS

    def test_contains_strong(self):
        assert "strong_buy" in VALID_RECOMMENDATIONS
        assert "strong_sell" in VALID_RECOMMENDATIONS


class TestValidateAiAnalysisResponse:
    def test_non_dict_returns_error(self):
        result = validate_ai_analysis_response("not a dict")
        assert "error" in result
        assert result["score"] == 0


class TestJsonParsingNoRfindFallback:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.LocalModelManager")
    async def test_raw_decode_extracts_first_json_object(self, mock_lmm, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-4"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_llm_config.return_value = {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_setting.return_value = False

        svc = AIService()
        svc._chat_completion_litellm = AsyncMock(
            return_value={"content": '{"category_L1": "finance", "sentiment": "Positive"} extra garbage'}
        )
        result = await svc._chat_completion(
            messages=[{"role": "user", "content": "test"}],
            provider="cloud",
            json_mode=True,
        )
        assert isinstance(result, dict)
        assert result["category_L1"] == "finance"
        assert result["sentiment"] == "Positive"

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.LocalModelManager")
    async def test_invalid_json_raises_value_error(self, mock_lmm, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-4"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_llm_config.return_value = {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_setting.return_value = False

        svc = AIService()
        svc._chat_completion_litellm = AsyncMock(return_value={"content": "{invalid json structure no closing brace"})
        with pytest.raises(ValueError, match="Invalid JSON response"):
            await svc._chat_completion(
                messages=[{"role": "user", "content": "test"}],
                provider="cloud",
                json_mode=True,
            )

    @pytest.mark.asyncio
    async def test_non_json_mode_returns_content_dict(self):
        """Non-json mode returns {"content": str} dict without parsing."""
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": "hello world"})):
            result = await svc._chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                provider="cloud",
                json_mode=False,
            )
            assert result == {"content": "hello world"}


class TestStreamInterruptPartialResult:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    @patch("services.ai_service.acompletion", new_callable=AsyncMock)
    async def test_stream_interrupt_returns_partial(self, mock_acomp, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-4"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_llm_config.return_value = {
            "provider": "openai",
            "model": "gpt-4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_setting.return_value = False

        chunks = [
            MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello "))]),
            MagicMock(choices=[MagicMock(delta=MagicMock(content="wor"))]),
        ]

        class BrokenStream:
            def __aiter__(self):
                return self._gen()

            async def _gen(self):
                for c in chunks:
                    yield c
                raise httpx.ReadTimeout("connection lost")

        mock_acomp.return_value = BrokenStream()

        received_chunks = []
        svc = AIService()
        result = await svc._chat_completion_litellm(
            messages=[{"role": "user", "content": "hi"}],
            on_chunk=lambda content, is_reasoning: received_chunks.append(content),
        )
        assert "content" in result
        assert "Hello wor" in result["content"]
        assert len(received_chunks) > 0
        assert "Hello wor" in "".join(received_chunks)


class TestValidateAiAnalysisResponseContinued:
    def test_valid_score_and_recommendation(self):
        result = validate_ai_analysis_response({"score": 75, "recommendation": "buy"})
        assert result["score"] == 75
        assert result["recommendation"] == "buy"

    def test_score_clamp_high(self):
        result = validate_ai_analysis_response({"score": 150, "recommendation": "hold"})
        assert result["score"] == 100

    def test_score_clamp_low(self):
        result = validate_ai_analysis_response({"score": -10, "recommendation": "hold"})
        assert result["score"] == 0

    def test_invalid_score_type(self):
        result = validate_ai_analysis_response({"score": "abc", "recommendation": "hold"})
        assert result["score"] == 0

    def test_invalid_recommendation(self):
        result = validate_ai_analysis_response({"score": 50, "recommendation": "unknown"})
        assert result["recommendation"] == "neutral"

    def test_recommendation_case_insensitive(self):
        result = validate_ai_analysis_response({"score": 50, "recommendation": "BUY"})
        assert result["recommendation"] == "buy"

    def test_none_score(self):
        result = validate_ai_analysis_response({"recommendation": "hold"})
        assert "score" not in result or result.get("score") is None

    def test_none_recommendation(self):
        result = validate_ai_analysis_response({"score": 50})
        assert "recommendation" not in result or result.get("recommendation") is None

    def test_float_score(self):
        result = validate_ai_analysis_response({"score": 75.5, "recommendation": "hold"})
        assert result["score"] == 75.5


class TestAIServiceIsCloudAvailable:
    @patch("services.ai_service.ConfigHandler")
    def test_available(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "test-key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc.is_cloud_available() is True

    @patch("services.ai_service.ConfigHandler")
    def test_not_available_no_key(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_ai_model.return_value = ""
        mock_ch.get_ai_api_key.return_value = ""
        mock_ch.get_ai_base_url.return_value = ""
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc.is_cloud_available() is False


class TestAIServiceSetupClientAzure:
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_azure_missing_resource_name(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "azure",
            "azure_resource_name": "",
            "azure_deployment_name": "deploy1",
        }
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is False

    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_azure_missing_deployment_name(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "azure",
            "azure_resource_name": "myresource",
            "azure_deployment_name": "",
        }
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is False

    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_azure_success(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "azure",
            "azure_resource_name": "myresource",
            "azure_deployment_name": "mydeploy",
        }
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is True

    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_no_api_key_disables_cloud(self, mock_ch):
        """When api_key is missing, _is_cloud_configured must be False."""
        mock_ch.get_llm_config.return_value = {"provider": "deepseek", "base_url": "http://api.test.com"}
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is False


class TestAIServiceSetupClientNoBaseUrl:
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_no_base_url_fails(self, mock_ch):
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "deepseek",
            "base_url": "",
        }
        mock_ch.get_setting.return_value = False
        svc = AIService()
        assert svc._is_cloud_configured is False


class TestAIServiceBuildLiteLLMParamsAzure:
    def test_azure_params(self):
        llm_config = {
            "provider": "azure",
            "model": "mydeploy",
            "api_key": "test-key",
            "azure_resource_name": "myresource",
            "api_version": "2024-02-01",
            "base_url": "",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "azure/mydeploy"
        assert "myresource" in params["api_base"]

    def test_azure_no_resource_name_uses_base_url(self):
        """Boundary: empty azure_resource_name falls back to base_url."""
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


class TestAIServiceBuildLiteLLMParamsProviderPrefix:
    def test_anthropic_prefix(self):
        llm_config = {
            "provider": "anthropic",
            "model": "claude-3",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "anthropic/claude-3"

    def test_google_prefix(self):
        llm_config = {
            "provider": "google",
            "model": "gemini-pro",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "gemini/gemini-pro"

    def test_deepseek_prefix(self):
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "deepseek/deepseek-v4-flash"

    def test_qwen_prefix(self):
        llm_config = {
            "provider": "qwen",
            "model": "qwen-turbo",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/qwen-turbo"

    def test_custom_prefix(self):
        llm_config = {
            "provider": "custom",
            "model": "my-model",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/my-model"

    def test_temperature_and_max_tokens(self):
        llm_config = {
            "provider": "openai",
            "model": "gpt-5.4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.5,
            max_tokens=100,
        )
        assert params["temperature"] == 0.5
        assert params["max_tokens"] == 100


class TestAIServiceParseNewsResult:
    @patch("services.ai_service.ConfigHandler")
    def test_with_l1_and_l2_codes(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("core.i18n.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, default=None: {
                "news_l1_finance": "金融核心",
                "news_l2_precious_metals": "贵金属",
            }.get(key, default if default is not None else key)
            result = svc._parse_news_result({"category_L1": "finance", "category_L2": "precious_metals"})
        assert result["category"] == "金融核心-贵金属"

    @patch("services.ai_service.ConfigHandler")
    def test_with_l1_only(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("core.i18n.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, default=None: {
                "news_l1_finance": "金融核心",
            }.get(key, default if default is not None else key)
            result = svc._parse_news_result({"category_L1": "finance"})
        assert result["category"] == "金融核心"

    @patch("services.ai_service.ConfigHandler")
    def test_defaults_emoji_sentiment(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("core.i18n.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, default=None: default if default is not None else key
            result = svc._parse_news_result({"category_L1": "Tech"})
        assert result["emoji"] == "📰"
        assert result["sentiment"] == "Neutral"

    @patch("services.ai_service.ConfigHandler")
    def test_unknown_code_falls_back_to_code(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("core.i18n.I18n") as mock_i18n:
            mock_i18n.get.side_effect = lambda key, default=None: default if default is not None else key
            result = svc._parse_news_result({"category_L1": "unknown_l1", "category_L2": "unknown_l2"})
        assert result["category"] == "unknown_l1-unknown_l2"


class TestAIServiceVerifyConnection:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_not_available(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_ai_model.return_value = ""
        mock_ch.get_ai_api_key.return_value = ""
        mock_ch.get_ai_base_url.return_value = ""
        mock_ch.get_setting.return_value = False
        svc = AIService()
        result = await svc.verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_success(self):
        """verify_connection returns True when _chat_completion_litellm succeeds."""
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(return_value={"content": "ok"})):
            result = await svc.verify_connection()
            assert result is True

    @pytest.mark.asyncio
    async def test_verify_exception_raises(self):
        """verify_connection re-raises when _chat_completion_litellm raises."""
        svc = _make_svc_with_cloud()
        with patch.object(svc, "_chat_completion_litellm", AsyncMock(side_effect=Exception("conn err"))):
            with pytest.raises(Exception, match="conn err"):
                await svc.verify_connection()


class TestAIServiceTestConnection:
    @pytest.mark.asyncio
    async def test_no_api_key(self):
        result = await AIService.test_connection(api_key="")
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("services.ai_service.LITELLM_AVAILABLE", False)
    async def test_no_litellm(self):
        result = await AIService.test_connection(api_key="key")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_no_model_returns_false(self):
        """Empty model returns failure."""
        result = await AIService.test_connection(api_key="key", model="")
        assert result["success"] is False

    @pytest.mark.asyncio
    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    async def test_success_with_usage(self):
        """Successful connection returns usage stats."""
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
        """Reasoning-capable model sets reasoning_supported flag."""
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
        """Connection failure returns structured error with error_code."""
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


class TestAIServiceReloadConfig:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_reload(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        await svc.reload_config()
        assert svc._local_model_loaded is False

    @pytest.mark.asyncio
    async def test_resets_local_model_and_semaphore(self):
        """reload_config resets _local_model_loaded flag."""
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


class TestAIServiceCleanupPromptDumps:
    @patch("services.ai_service.ConfigHandler")
    def test_disabled(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        svc._cleanup_prompt_dumps()

    @patch("services.ai_service.ConfigHandler")
    def test_enabled_no_dir(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.side_effect = lambda k, d=False: True if k == "ai_prompt_dump_enabled" else d
        svc = AIService()
        svc._cleanup_prompt_dumps()


class TestCheckReasoningSupportWithLitellm:
    def test_with_litellm_true(self):
        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            result = _check_reasoning_support("deepseek-v4-pro")
            assert isinstance(result, bool)


class TestBuildLiteLLMParamsBoundaryConditions:
    def test_empty_model_raises_value_error(self):
        llm_config = {
            "provider": "openai",
            "model": "",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        with pytest.raises(ValueError, match="Model ID is required"):
            AIService._build_litellm_params(
                llm_config=llm_config,
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_azure_empty_model_raises_value_error(self):
        llm_config = {
            "provider": "azure",
            "model": "",
            "api_key": "key",
            "azure_resource_name": "myresource",
            "base_url": "",
        }
        with pytest.raises(ValueError, match="Model ID is required"):
            AIService._build_litellm_params(
                llm_config=llm_config,
                messages=[{"role": "user", "content": "hi"}],
            )

    def test_unknown_provider_uses_openai_prefix(self):
        llm_config = {
            "provider": "unknown_provider",
            "model": "some-model",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/some-model"

    def test_mistral_prefix(self):
        llm_config = {
            "provider": "mistral",
            "model": "mistral-large-latest",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "mistral/mistral-large-latest"

    def test_deepseek_prefix(self):
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "deepseek/deepseek-v4-flash"


class TestTestConnectionBoundaryConditions:
    @pytest.mark.asyncio
    async def test_empty_model_returns_failure(self):
        result = await AIService.test_connection(
            provider="openai",
            model="",
            api_key="test-key",
            base_url="https://api.openai.com",
        )
        assert result["success"] is False
        assert "Model ID is empty" in result["message"]

    @pytest.mark.asyncio
    async def test_empty_api_key_returns_failure(self):
        result = await AIService.test_connection(
            provider="openai",
            model="gpt-5.4",
            api_key="",
            base_url="https://api.openai.com",
        )
        assert result["success"] is False
        assert "API Key is empty" in result["message"]


class TestReasoningModelFallbackList:
    def test_fallback_list_contains_current_models(self):
        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            with patch("services.ai_service.litellm.utils.supports_reasoning", side_effect=Exception("test")):
                assert _check_reasoning_support("deepseek-v4-pro") is True
                assert _check_reasoning_support("o3-pro") is True
                assert _check_reasoning_support("o4-mini") is True
                assert _check_reasoning_support("claude-opus-4-7") is True
                assert _check_reasoning_support("magistral-medium-latest") is True
                assert _check_reasoning_support("qwen3.6-max") is True
                assert _check_reasoning_support("glm-5") is True

    def test_fallback_list_excludes_non_reasoning(self):
        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            with patch("services.ai_service.litellm.utils.supports_reasoning", side_effect=Exception("test")):
                assert _check_reasoning_support("gpt-5.4") is False
                assert _check_reasoning_support("gpt-5.5") is False
                assert _check_reasoning_support("deepseek-v4-flash") is False
                assert _check_reasoning_support("mistral-small-latest") is False

    def test_litellm_supports_reasoning_true(self):
        """Positive branch: litellm.utils.supports_reasoning returns True directly (no fallback)."""
        with (
            patch("services.ai_service.LITELLM_AVAILABLE", True),
            patch("services.ai_service.litellm.utils.supports_reasoning", return_value=True),
        ):
            assert _check_reasoning_support("deepseek-v4-pro") is True


class TestAIServiceAnalyzeTimeoutHandling:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_builtin_timeout_error_caught(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=TimeoutError("read timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0


class TestAIServiceSemaphoreSeparation:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_separate_semaphore_factories_exist(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 5
        mock_ch.get_ai_news_max_concurrent.return_value = 1

        svc = AIService()
        analysis_sem = svc._get_analysis_semaphore()
        news_sem = svc._get_news_semaphore()
        assert analysis_sem is not news_sem

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_reload_config_invalidates_both_semaphores(self, mock_ch):
        from utils import loop_local

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 5
        mock_ch.get_ai_news_max_concurrent.return_value = 1

        svc = AIService()
        svc._get_analysis_semaphore()
        svc._get_news_semaphore()
        await svc.reload_config()
        assert loop_local.get_loop_local("ai_analysis_semaphore", lambda: "rebuilt") == "rebuilt"
        assert loop_local.get_loop_local("ai_news_semaphore", lambda: "rebuilt") == "rebuilt"


class TestAIServiceBuildLiteLLMParamsZhipuBoundary:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_httpx_timeout_exception_caught(self, mock_ch):
        import httpx

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=httpx.TimeoutException("connect timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_local_inference_timeout_error_caught(self, mock_ch):
        from services.local_model_manager import LocalInferenceTimeoutError

        mock_ch.get_ai_provider.return_value = "local"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "local",
            "base_url": "http://localhost",
        }
        mock_ch.get_ai_model.return_value = "local-model"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://localhost"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=LocalInferenceTimeoutError("local timeout 90s"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "Local model timeout"
        assert result["score"] == 0


class TestUniversalRulesSeparateSystemMessage:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_messages_contain_two_system_messages(self, mock_ch):
        from strategies.strategy_prompts import _UNIVERSAL_RULES

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 80, "recommendation": "buy"})
        with patch("strategies.strategy_prompts.get_base_prompt", return_value="Strategy prompt"):
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "name": "test"},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        messages = svc._chat_completion.await_args.args[0]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 2
        assert _UNIVERSAL_RULES in system_msgs[0]["content"]
        assert "<strategy_rules>" in system_msgs[1]["content"]
        assert "Strategy prompt" in system_msgs[1]["content"]

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_ui_override_does_not_merge_with_universal_rules(self, mock_ch):
        from strategies.strategy_prompts import _UNIVERSAL_RULES

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 60, "recommendation": "neutral"})
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="safe custom prompt"),
        ):
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="Custom analysis prompt",
            )
        messages = svc._chat_completion.await_args.args[0]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 2
        assert _UNIVERSAL_RULES in system_msgs[0]["content"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "<user_custom_instructions>" in user_msgs[0]["content"]
        assert "safe custom prompt" in user_msgs[0]["content"]

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_invalid_override_uses_base_prompt_without_rules(self, mock_ch):
        from strategies.strategy_prompts import _UNIVERSAL_RULES

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 40, "recommendation": "sell"})
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(False, "Injection detected")),
            patch("strategies.strategy_prompts.get_base_prompt", return_value="Fallback prompt"),
        ):
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="<script>evil</script>",
                strategy_key="oversold",
            )
        messages = svc._chat_completion.await_args.args[0]
        system_msgs = [m for m in messages if m["role"] == "system"]
        assert len(system_msgs) == 2
        assert _UNIVERSAL_RULES in system_msgs[0]["content"]
        assert "<strategy_rules>" in system_msgs[1]["content"]
        assert "Fallback prompt" in system_msgs[1]["content"]
        user_msgs = [m for m in messages if m["role"] == "user"]
        assert len(user_msgs) == 1
        assert "<user_custom_instructions>" not in user_msgs[0]["content"]


class TestAIServiceAnalyzeStockCloudNotAvailable:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_returns_none_when_not_configured(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_ai_model.return_value = ""
        mock_ch.get_ai_api_key.return_value = ""
        mock_ch.get_ai_base_url.return_value = ""
        mock_ch.get_setting.return_value = False
        svc = AIService()
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ"},
            tech_info={},
            news_list=[],
        )
        assert result is None


class TestAIServiceAnalyzeStockSuccess:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_with_all_contexts(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 80, "recommendation": "buy", "reason": "Good stock"})
        with (
            patch("strategies.strategy_prompts.get_base_prompt") as mock_resolve,
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="safe prompt"),
        ):
            mock_resolve.return_value = "Strategy prompt"
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "name": "test", "concepts": ["AI", "芯片"]},
                tech_info={"rsi_14": 25},
                news_list=[{"source": "CLS", "publish_time": "2026-05-09", "title": "利好消息"}],
                global_context="大盘上涨",
                strategy_context="超跌反弹策略",
                capital_flow_text="北向资金净流入",
                financials_text="ROE 15%",
                history_text="近5日下跌",
                strategy_key="oversold",
            )
        assert result["score"] == 80

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_with_empty_concepts(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("strategies.strategy_prompts.get_base_prompt") as mock_resolve:
            mock_resolve.return_value = "Strategy prompt"
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "concepts": []},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_with_none_concepts(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        with patch("strategies.strategy_prompts.get_base_prompt") as mock_resolve:
            mock_resolve.return_value = "Strategy prompt"
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ", "concepts": None},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
            )
        assert result["score"] == 50

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_with_ui_prompt_override(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 60, "recommendation": "neutral"})
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(True, "")),
            patch("utils.prompt_guard.sanitize_prompt", return_value="safe"),
        ):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="Custom analysis prompt",
            )
        assert result["score"] == 60

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_with_invalid_prompt_override(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(return_value={"score": 40, "recommendation": "sell"})
        with (
            patch("utils.prompt_guard.validate_prompt", return_value=(False, "Injection detected")),
            patch("strategies.strategy_prompts.get_base_prompt") as mock_resolve,
        ):
            mock_resolve.return_value = "Fallback prompt"
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                ui_prompt_override="<script>evil</script>",
                strategy_key="oversold",
            )
        assert result["score"] == 40

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_analyze_general_exception(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_system_prompt.return_value = "You are an analyst."
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=RuntimeError("API error"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] is not None
        assert result["score"] == 0


class TestAIServiceClassifyNewsFallback:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_local_fails_cloud_succeeds(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_news_prompt.return_value = "Classify this news"
        svc = AIService()
        call_count = [0]

        async def mock_chat_completion(messages, **kwargs):
            call_count[0] += 1
            if kwargs.get("provider") == "local":
                raise Exception("Local model not configured")
            return {"category_L1": "finance", "category_L2": "banking", "emoji": "📊", "sentiment": "Positive"}

        svc._chat_completion = AsyncMock(side_effect=mock_chat_completion)
        with patch("core.i18n.I18n.get", return_value="金融"):
            result = await svc.classify_news("央行降息")
        assert call_count[0] == 2
        assert "category" in result

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_all_providers_fail(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_news_prompt.return_value = "Classify this news"
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=Exception("All down"))
        result = await svc.classify_news("央行降息")
        assert result.get("error") is not None
        assert result.get("category") == "unknown"

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_classify_news_failure_returns_complete_structure(self, mock_ch):
        """E-P1-6: classify_news failure must return a dict with all expected keys."""
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_news_prompt.return_value = "Classify this news"
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=Exception("All down"))
        result = await svc.classify_news("央行降息")
        assert isinstance(result, dict), "E-P1-6: classify_news should always return a dict"
        assert "error" in result, "E-P1-6: failure result must contain 'error' key"
        assert "category" in result, "E-P1-6: failure result must contain 'category' key"
        assert result["category"] == "unknown", "E-P1-6: failure category should be 'unknown'"

    @pytest.mark.asyncio
    async def test_local_fails_with_not_installed_fallback_cloud(self):
        """Local 'not installed' branch falls back to cloud (call_count==2 confirms fallback)."""
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
        assert svc._chat_completion.call_count == 2  # local failed → cloud fallback happened


class TestAIServiceGetSemaphore:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_get_analysis_semaphore(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 3
        mock_ch.get_ai_news_max_concurrent.return_value = 1
        svc = AIService()
        sem = svc._get_analysis_semaphore()
        assert sem is not None


class TestAIServiceSetupLocalModel:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_setup_local_model_no_path(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {}
        mock_ch.get_ai_model.return_value = ""
        mock_ch.get_ai_api_key.return_value = ""
        mock_ch.get_ai_base_url.return_value = ""
        mock_ch.get_setting.side_effect = lambda k, d=False: None if k == "local_model_path" else d
        svc = AIService()
        with patch("services.ai_service.LocalModelManager") as mock_lmm:
            mock_lmm.get_instance = AsyncMock(
                return_value=MagicMock(get_loaded_model_path=MagicMock(return_value=None))
            )
            await svc._setup_local_model()

    @pytest.mark.asyncio
    async def test_loads_model_from_config(self):
        """Loads model from config path when not yet loaded."""
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
        """Skips load_model when manager already has a loaded model."""
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
        """Skips load_model when config has no local_model_path."""
        svc = _make_svc_with_cloud()
        mock_manager = MagicMock()
        mock_manager.get_loaded_model_path.return_value = None
        with (
            patch("services.ai_service.LocalModelManager.get_instance", AsyncMock(return_value=mock_manager)),
            patch("services.ai_service.ConfigHandler.get_setting", return_value=None),
        ):
            await svc._setup_local_model()
            mock_manager.load_model.assert_not_called()


class TestAIServiceBuildLiteLLMParamsResponseFormat:
    def test_response_format_included(self):
        llm_config = {
            "provider": "openai",
            "model": "gpt-5.4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        assert params["response_format"] == {"type": "json_object"}


class TestAIServiceBuildLiteLLMParamsZhipu:
    def test_zhipu_prefix(self):
        llm_config = {
            "provider": "zhipu",
            "model": "glm-4",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/glm-4"

    def test_moonshot_prefix(self):
        llm_config = {
            "provider": "moonshot",
            "model": "moonshot-v1",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/moonshot-v1"

    def test_minimax_prefix(self):
        llm_config = {
            "provider": "minimax",
            "model": "abab6",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "openai/abab6"

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_asyncio_timeout_error_caught(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=TimeoutError())
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_httpx_timeout_exception_caught(self, mock_ch):
        import httpx

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0

    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_httpx_connect_timeout_caught(self, mock_ch):
        import httpx

        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_llm_config.return_value = {
            "api_key": "key",
            "provider": "deepseek",
            "base_url": "http://api.test.com",
        }
        mock_ch.get_ai_model.return_value = "deepseek-v4-flash"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        mock_ch.get_failover_config.return_value = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
        }
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=httpx.ConnectTimeout("connect timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "All LLM providers unavailable"
        assert result["score"] == 0


# ============================================================================
# Merged from test_ai_service_coverage.py (P2-1: 一模块一测试文件)
# Unique-coverage cases retained; pure duplicates dropped.
# Factory helper: _make_svc_with_cloud() at top of file.
# ============================================================================


class TestAIServiceConfigureLitellm:
    """LiteLLM 全局参数配置 (drop_params)."""

    @patch("services.ai_service.LITELLM_AVAILABLE", True)
    @patch("services.ai_service.ConfigHandler")
    def test_configure_sets_drop_params(self, mock_ch):
        """_configure_litellm sets litellm.drop_params = True."""
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


class TestAIServiceChatCompletionLocal:
    """_chat_completion local provider 分支."""

    @pytest.mark.asyncio
    async def test_local_model_not_loaded_raises(self):
        """Local model not loaded raises ValueError."""
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
        """Local model inference returns parsed JSON."""
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


class TestAIServiceChatCompletionCloudNotAvailable:
    """_chat_completion cloud provider 未配置分支."""

    @pytest.mark.asyncio
    async def test_raises_value_error(self):
        """Cloud not configured raises ValueError."""
        svc = _make_svc_with_cloud()
        svc._is_cloud_configured = False
        svc._litellm_config = {}
        with pytest.raises(ValueError, match="Cloud LLM not configured"):
            await svc._chat_completion(
                messages=[{"role": "user", "content": "hello"}],
                provider="cloud",
            )


class TestAIServiceChatCompletionLitellmNonStream:
    """_chat_completion_litellm 非流式分支."""

    @pytest.mark.asyncio
    async def test_non_stream_response(self):
        """Non-stream path returns content + usage stats."""
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

    @pytest.mark.asyncio
    async def test_warns_on_large_prompt(self):
        """Large prompt (>80k estimated tokens) does not crash; returns content."""
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


class TestAIServiceChatCompletionLitellmStream:
    """_chat_completion_litellm 流式分支 (reasoning / no-reasoning / reasoning-only)."""

    @pytest.mark.asyncio
    async def test_stream_with_reasoning(self):
        """Stream with reasoning_content returns both content and reasoning_content."""
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
        """Stream without reasoning support returns content only."""
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
        """When only reasoning_content is present, content is filled from reasoning."""
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


class TestAIServiceAnalyzeStockDeepBranches:
    """analyze_stock 深层分支: concepts 异常、learning context、backtest 安全、strategy_key 缺失."""

    @pytest.mark.asyncio
    async def test_concepts_exception_fallback(self):
        """concepts.get raising Exception is caught; concepts key removed."""
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
        """ReviewManager raising Exception is caught; history_context falls back to empty."""
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
        """include_learning_context=False skips learning context fetch entirely."""
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
    async def test_fallback_learning_context_passes_non_none_as_of(self):
        """Live-mode fallback path passes non-None as_of to prevent lookahead bias."""
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})

        mock_rm = AsyncMock()
        mock_rm.get_learning_context = AsyncMock(return_value="<learning>test</learning>")

        with (
            patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
        ):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                strategy_key="oversold",
                include_learning_context=True,
            )
        assert result["score"] == 50
        mock_rm.get_learning_context.assert_called_once()
        call_kwargs = mock_rm.get_learning_context.call_args
        as_of_arg = call_kwargs.kwargs.get("as_of") if call_kwargs.kwargs else call_kwargs[1].get("as_of")
        assert as_of_arg is not None, "fallback path must pass non-None as_of to prevent lookahead bias"

    @pytest.mark.asyncio
    async def test_analyze_stock_fallback_raises_in_backtest_mode(self):
        """Backtest mode with history_context=None raises ValueError (lookahead bias guard)."""
        svc = _make_svc_with_cloud()
        with pytest.raises(ValueError, match="analyze_stock called with history_context=None in backtest mode"):
            await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_context=None,
                include_learning_context=True,
                is_backtest=True,
            )

    @pytest.mark.asyncio
    async def test_analyze_stock_fallback_works_in_live_mode(self):
        """Live mode with history_context=None fetches learning context successfully."""
        svc = _make_svc_with_cloud()
        svc._chat_completion = AsyncMock(return_value={"score": 50, "recommendation": "hold"})
        mock_rm = AsyncMock()
        mock_rm.get_learning_context = AsyncMock(return_value="<learning>test</learning>")

        with (
            patch("strategies.strategy_prompts.get_base_prompt", return_value="prompt"),
            patch("data.persistence.review_manager.ReviewManager", return_value=mock_rm),
        ):
            result = await svc.analyze_stock(
                stock_info={"ts_code": "000001.SZ"},
                tech_info={},
                news_list=[],
                history_context=None,
                include_learning_context=True,
                is_backtest=False,
            )
            assert result["score"] == 50
            mock_rm.get_learning_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_strategy_key_no_override(self):
        """No strategy_key and no ui_prompt_override uses ConfigHandler.get_ai_system_prompt."""
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
