import pytest
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


@pytest.fixture(autouse=True)
def reset_ai_singleton():
    AIService._reset_singleton()
    yield
    AIService._reset_singleton()


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
    def setup_method(self):
        AIService._reset_singleton()

    def teardown_method(self):
        AIService._reset_singleton()

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
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=TimeoutError("read timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "Analysis timeout"
        assert result["score"] == 0


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
            patch("services.ai_service.resolve_prompt") as mock_resolve,
            patch("services.ai_service.validate_prompt", return_value=(True, "")),
            patch("services.ai_service.sanitize_prompt", return_value="safe prompt"),
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
        with patch("services.ai_service.resolve_prompt") as mock_resolve:
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
        with patch("services.ai_service.resolve_prompt") as mock_resolve:
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
            patch("services.ai_service.validate_prompt", return_value=(True, "")),
            patch("services.ai_service.sanitize_prompt", return_value="safe"),
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
            patch("services.ai_service.validate_prompt", return_value=(False, "Injection detected")),
            patch("services.ai_service.resolve_prompt") as mock_resolve,
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


class TestAIServiceGetSemaphore:
    @pytest.mark.asyncio
    @patch("services.ai_service.ConfigHandler")
    async def test_get_semaphore(self, mock_ch):
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
        svc = AIService()
        sem = await svc._get_semaphore()
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
        mock_ch.get_setting.return_value = lambda k, d=False: None if k == "local_model_path" else d
        svc = AIService()
        with patch("services.ai_service.LocalModelManager") as mock_lmm:
            mock_lmm.get_instance = AsyncMock(
                return_value=MagicMock(get_loaded_model_path=MagicMock(return_value=None))
            )
            await svc._setup_local_model()


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
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=TimeoutError())
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "Analysis timeout"
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
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=httpx.ReadTimeout("read timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "Analysis timeout"
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
        svc = AIService()
        svc._chat_completion = AsyncMock(side_effect=httpx.ConnectTimeout("connect timeout"))
        result = await svc.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "test"},
            tech_info={},
            news_list=[],
        )
        assert result["error"] == "Analysis timeout"
        assert result["score"] == 0
