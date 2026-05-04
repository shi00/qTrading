import pytest
from unittest.mock import patch, AsyncMock

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
        mock_ch.get_ai_model.return_value = "gpt-4"
        svc = AIService()
        assert svc is not None


class TestAIServiceBuildLiteLLMParams:
    @patch("services.ai_service.ConfigHandler")
    def test_basic_params(self, mock_ch):
        mock_ch.get_ai_provider.return_value = "cloud"
        mock_ch.get_ai_model.return_value = "gpt-4"
        mock_ch.get_ai_api_key.return_value = "test-key"
        mock_ch.get_ai_base_url.return_value = ""
        svc = AIService()
        llm_config = {
            "provider": "openai",
            "model": "gpt-4",
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
        mock_ch.get_ai_model.return_value = "gpt-4"
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
            assert _check_reasoning_support("deepseek-r1") is False


class TestClassifyApiError:
    def test_returns_dict(self):
        result = _classify_api_error(Exception("test"))
        assert isinstance(result, dict)
        assert "code" in result
        assert "message" in result


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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
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
            "model": "deepseek-chat",
            "api_key": "key",
            "base_url": "http://api.test.com",
        }
        params = AIService._build_litellm_params(
            llm_config=llm_config,
            messages=[{"role": "user", "content": "hi"}],
        )
        assert params["model"] == "deepseek/deepseek-chat"

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
            "model": "gpt-4",
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("ui.i18n.I18n") as mock_i18n:
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("ui.i18n.I18n") as mock_i18n:
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("ui.i18n.I18n") as mock_i18n:
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.return_value = False
        svc = AIService()
        with patch("ui.i18n.I18n") as mock_i18n:
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
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
        mock_ch.get_ai_model.return_value = "deepseek-chat"
        mock_ch.get_ai_api_key.return_value = "key"
        mock_ch.get_ai_base_url.return_value = "http://api.test.com"
        mock_ch.get_setting.side_effect = lambda k, d=False: True if k == "ai_prompt_dump_enabled" else d
        svc = AIService()
        svc._cleanup_prompt_dumps()


class TestCheckReasoningSupportWithLitellm:
    def test_with_litellm_true(self):
        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            result = _check_reasoning_support("deepseek-r1")
            assert isinstance(result, bool)
