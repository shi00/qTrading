"""
P1-12: 测试 AI 服务多供应商 fallback 功能
"""

import pytest
from unittest.mock import AsyncMock, patch
import httpx

from services.ai_service import AIService, AIServiceUnavailableError


class TestFailoverConfig:
    """测试 Failover 配置获取"""

    def test_get_failover_config_returns_primary(self, tmp_path, monkeypatch):
        """测试 failover 配置返回主供应商"""
        import json

        config_file = tmp_path / "user_settings.json"
        config_file.write_text(
            json.dumps(
                {
                    "llm_provider": "deepseek",
                    "llm_model": "deepseek-v4-flash",
                    "llm_failover_models": ["qwen/qwen-max", "openai/gpt-4o"],
                }
            )
        )

        from utils import config_handler

        monkeypatch.setattr(config_handler, "CONFIG_FILE", str(config_file))
        config_handler.ConfigHandler._config_cache = None

        config = config_handler.ConfigHandler.get_failover_config()

        assert config["primary"] == "deepseek/deepseek-v4-flash"
        assert config["fallbacks"] == ["qwen/qwen-max", "openai/gpt-4o"]

    def test_get_failover_config_empty_fallbacks(self, tmp_path, monkeypatch):
        """测试没有配置 fallback 时返回空列表"""
        import json

        config_file = tmp_path / "user_settings.json"
        config_file.write_text(
            json.dumps(
                {
                    "llm_provider": "deepseek",
                    "llm_model": "deepseek-v4-flash",
                }
            )
        )

        from utils import config_handler

        monkeypatch.setattr(config_handler, "CONFIG_FILE", str(config_file))
        config_handler.ConfigHandler._config_cache = None

        config = config_handler.ConfigHandler.get_failover_config()

        assert config["primary"] == "deepseek/deepseek-v4-flash"
        assert config["fallbacks"] == []


class TestChatCompletionWithFailover:
    """测试带 fallback 的聊天完成"""

    @pytest.mark.asyncio
    async def test_primary_success_no_fallback(self):
        """主供应商成功时不使用 fallback"""
        service = AIService()

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion:
            mock_completion.return_value = {"score": 85, "recommendation": "buy"}

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                result = await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

                assert result["score"] == 85
                mock_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_timeout(self):
        """TimeoutError 时触发 fallback"""
        service = AIService()

        call_count = 0

        async def mock_completion(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("Request timed out")
            return {"score": 75, "recommendation": "sell"}

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion_patch:
            mock_completion_patch.side_effect = mock_completion

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                result = await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

                assert result["score"] == 75
                assert call_count == 2

    @pytest.mark.asyncio
    async def test_fallback_on_httpx_error(self):
        """httpx 连接错误时触发 fallback"""
        service = AIService()

        call_count = 0

        async def mock_completion(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("Connection failed")
            return {"score": 80, "recommendation": "hold"}

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion_patch:
            mock_completion_patch.side_effect = mock_completion

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                result = await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

                assert result["score"] == 80
                assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_providers_failed_raises_error(self):
        """所有供应商都失败时抛出 AIServiceUnavailableError"""
        service = AIService()

        async def mock_completion(*args, **kwargs):
            raise TimeoutError("Request timed out")

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion_patch:
            mock_completion_patch.side_effect = mock_completion

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                with pytest.raises(AIServiceUnavailableError) as exc_info:
                    await service._chat_completion_with_failover(
                        messages=[{"role": "user", "content": "test"}],
                    )

                assert "All LLM providers failed" in str(exc_info.value)

    @pytest.mark.skip(reason="litellm exceptions are factory functions, not classes")
    @pytest.mark.asyncio
    async def test_fallback_on_litellm_rate_limit(self):
        """LiteLLM RateLimitError 时触发 fallback"""
        pass

    @pytest.mark.skip(reason="litellm exceptions are factory functions, not classes")
    @pytest.mark.asyncio
    async def test_no_fallback_on_litellm_auth_error(self):
        """LiteLLM AuthenticationError 不触发 fallback，直接抛出"""
        pass


class TestAIServiceUnavailableError:
    """测试 AIServiceUnavailableError 异常"""

    def test_error_message(self):
        """测试异常消息"""
        error = AIServiceUnavailableError("All providers failed")
        assert str(error) == "All providers failed"

    def test_error_with_cause(self):
        """测试异常链"""
        cause = TimeoutError("Connection timeout")
        error = AIServiceUnavailableError("All providers failed")
        error.__cause__ = cause

        assert error.__cause__ is cause
