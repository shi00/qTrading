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
