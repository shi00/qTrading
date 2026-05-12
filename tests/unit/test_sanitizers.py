from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest

from utils.sanitizers import DataSanitizer


class TestSanitizeToken:
    def test_none_token(self):
        assert DataSanitizer.sanitize_token(None) == "***"

    def test_empty_token(self):
        assert DataSanitizer.sanitize_token("") == "***"

    def test_non_string_token(self):
        assert DataSanitizer.sanitize_token(12345) == "***"

    def test_short_token(self):
        assert DataSanitizer.sanitize_token("abc") == "***"

    def test_long_token(self):
        result = DataSanitizer.sanitize_token("tushare_abc123xyz789")
        assert result.startswith("tus")
        assert result.endswith("789")
        assert "***" in result


class TestSanitizeDataframe:
    def test_none(self):
        assert DataSanitizer.sanitize_dataframe(None) == "None"

    def test_non_dataframe(self):
        assert DataSanitizer.sanitize_dataframe([1, 2, 3]) == "list"

    def test_empty_dataframe(self):
        df = pd.DataFrame()
        assert DataSanitizer.sanitize_dataframe(df) == "DataFrame(empty)"

    def test_normal_dataframe(self):
        df = pd.DataFrame({"a": [1], "b": [2]})
        result = DataSanitizer.sanitize_dataframe(df)
        assert "shape=" in result
        assert "cols=" in result

    def test_many_columns(self):
        df = pd.DataFrame({f"col_{i}": [i] for i in range(10)})
        result = DataSanitizer.sanitize_dataframe(df, max_cols=3)
        assert "..." in result


class TestSanitizeError:
    def test_simple_error(self):
        err = ValueError("something went wrong")
        result = DataSanitizer.sanitize_error(err)
        assert "something went wrong" in result

    def test_error_with_windows_path(self):
        err = ValueError("File not found: D:\\workspace\\test.py")
        result = DataSanitizer.sanitize_error(err)
        assert "D:\\workspace\\test.py" not in result
        assert "<PATH>" in result

    def test_error_with_unix_path(self):
        err = ValueError("File not found: /home/user/test.py")
        result = DataSanitizer.sanitize_error(err)
        assert "/home/user/test.py" not in result
        assert "<PATH>" in result

    def test_error_with_api_key_in_url(self):
        err = ValueError("Request failed: https://api.openai.com/v1/chat?api_key=sk-abc123xyz789")
        result = DataSanitizer.sanitize_error(err)
        assert "sk-abc123xyz789" not in result
        assert "***" in result

    def test_error_with_token_in_url(self):
        err = ValueError("Auth failed: https://example.com/api?token=secret_token_value")
        result = DataSanitizer.sanitize_error(err)
        assert "secret_token_value" not in result
        assert "***" in result

    def test_error_with_bearer_token(self):
        err = ValueError("HTTP 401: Bearer sk-proj-abc123def456ghi789")
        result = DataSanitizer.sanitize_error(err)
        assert "sk-proj-abc123def456ghi789" not in result
        assert "Bearer ***" in result

    def test_error_with_password_in_url(self):
        err = ValueError("Connection: postgres://user:mysecretpass@host/db")
        result = DataSanitizer.sanitize_error(err)
        assert "mysecretpass" not in result
        assert "***" in result
        assert "user" in result

    def test_error_with_access_token(self):
        err = ValueError("Failed: ?access_token=eyJhbGciOiJIUzI1NiJ9")
        result = DataSanitizer.sanitize_error(err)
        assert "eyJhbGciOiJIUzI1NiJ9" not in result
        assert "***" in result


class TestSanitizeDict:
    def test_normal_keys(self):
        data = {"name": "test", "value": 42}
        result = DataSanitizer.sanitize_dict(data)
        assert result["name"] == "test"
        assert result["value"] == 42

    def test_sensitive_keys(self):
        data = {"token": "abc123456789", "password": "secret123"}
        result = DataSanitizer.sanitize_dict(data)
        assert "***" in result["token"]
        assert "***" in result["password"]

    def test_api_key(self):
        data = {"api_key": "my_api_key_12345"}
        result = DataSanitizer.sanitize_dict(data)
        assert "***" in result["api_key"]

    def test_dataframe_value(self):
        df = pd.DataFrame({"a": [1]})
        data = {"result": df}
        result = DataSanitizer.sanitize_dict(data)
        assert "DataFrame" in result["result"]

    def test_non_string_sensitive_value(self):
        data = {"secret": 12345}
        result = DataSanitizer.sanitize_dict(data)
        assert result["secret"] == "***"

    def test_custom_sensitive_keys(self):
        data = {"custom_secret": "value12345678"}
        result = DataSanitizer.sanitize_dict(data, sensitive_keys=["custom"])
        assert "***" in result["custom_secret"]


class TestSanitizeArgs:
    def test_basic_args(self):
        result = DataSanitizer.sanitize_args("hello", 42)
        assert isinstance(result, tuple)


class TestAIServiceErrorSanitization:
    """S-P1-3: Verify ai_service.py uses DataSanitizer for all exception logging.

    These tests validate BEHAVIOR: trigger exceptions containing sensitive data
    (file paths, API keys) and verify the returned error messages are sanitized.
    """

    @pytest.mark.asyncio
    async def test_analyze_top_level_failure_sanitizes_error(self):
        from services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc._is_cloud_configured = True
        svc._litellm_config = {"api_key": "test-key"}
        svc._local_model_loaded = False
        svc._supports_reasoning = False
        svc._initialized = True
        sensitive_path = "D:\\workspace\\secret\\api_key_sk-abc123.py"
        svc._chat_completion = AsyncMock(side_effect=RuntimeError(sensitive_path))
        svc._get_prompt_dump_dir = lambda: "/tmp"

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer") as mock_sanitizer:
                mock_sanitizer.sanitize_error.side_effect = lambda e: "<SANITIZED>"
                with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                    mock_rm.return_value.get_learning_context = AsyncMock(return_value="")
                    result = await svc.analyze_stock(
                        stock_info={"ts_code": "000001.SZ", "name": "test"},
                        tech_info={},
                        news_list=[],
                        strategy_key="value",
                    )

        assert result["error"] == "<SANITIZED>", (
            "analyze_stock must sanitize exceptions via DataSanitizer.sanitize_error, "
            f"but got raw error: {result['error']}"
        )
        assert sensitive_path not in result["error"], f"Sensitive path leaked into error result: {result['error']}"

    @pytest.mark.asyncio
    async def test_classify_all_providers_failed_sanitizes_error(self):
        from services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc._is_cloud_configured = True
        svc._litellm_config = {"api_key": "test-key"}
        svc._local_model_loaded = False
        svc._supports_reasoning = False
        svc._initialized = True
        sensitive_token = "Bearer sk-proj-LEAKED-KEY-12345"
        svc._chat_completion = AsyncMock(side_effect=RuntimeError(sensitive_token))
        svc._get_prompt_dump_dir = lambda: "/tmp"

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer") as mock_sanitizer:
                mock_sanitizer.sanitize_error.side_effect = lambda e: "<SANITIZED>"
                result = await svc.classify_news(text="test news text")

        assert result["error"] == "<SANITIZED>", (
            "classify_news must sanitize exceptions via DataSanitizer.sanitize_error, "
            f"but got raw error: {result['error']}"
        )
        assert sensitive_token not in result["error"], f"Sensitive token leaked into error result: {result['error']}"

    @pytest.mark.asyncio
    async def test_no_raw_str_e_in_error_returns(self):
        from services.ai_service import AIService

        svc = AIService.__new__(AIService)
        svc._is_cloud_configured = True
        svc._litellm_config = {"api_key": "test-key"}
        svc._local_model_loaded = False
        svc._supports_reasoning = False
        svc._initialized = True
        sensitive_data = "api_key=sk-LEAKED123&token=secret_value"
        svc._chat_completion = AsyncMock(side_effect=RuntimeError(sensitive_data))
        svc._get_prompt_dump_dir = lambda: "/tmp"

        with patch("services.ai_service.ConfigHandler") as mock_cfg:
            mock_cfg.get_ai_system_prompt.return_value = ""
            mock_cfg.get_ai_news_prompt.return_value = ""
            mock_cfg.get_setting.return_value = False
            mock_cfg.get_ai_provider.return_value = "cloud"

            with patch("services.ai_service.DataSanitizer") as mock_sanitizer:
                mock_sanitizer.sanitize_error.side_effect = lambda e: "<SANITIZED>"
                with patch("data.persistence.review_manager.ReviewManager") as mock_rm:
                    mock_rm.return_value.get_learning_context = AsyncMock(return_value="")
                    result = await svc.analyze_stock(
                        stock_info={"ts_code": "000001.SZ", "name": "test"},
                        tech_info={},
                        news_list=[],
                        strategy_key="value",
                    )

        assert sensitive_data not in result["error"], (
            f"Raw str(e) leaked into error return. "
            f"Error methods must use DataSanitizer.sanitize_error(e), not str(e). "
            f"Leaked data: {result['error']}"
        )

    def test_kwargs_with_sensitive(self):
        result = DataSanitizer.sanitize_args(token="abc123456789")
        assert isinstance(result, tuple)
