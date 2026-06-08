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


class TestFailoverModelPropagation:
    """测试 failover 时 model 参数正确传递到 _chat_completion"""

    @pytest.mark.asyncio
    async def test_primary_model_passed_to_chat_completion(self):
        """主供应商的 model 应传递给 _chat_completion"""
        service = AIService()

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion:
            mock_completion.return_value = {"score": 85}

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

                mock_completion.assert_called_once()
                call_kwargs = mock_completion.call_args
                assert call_kwargs.kwargs.get("model") == "deepseek/deepseek-v4-flash" or (
                    len(call_kwargs.args) > 1 and "deepseek" in str(call_kwargs)
                )

    @pytest.mark.asyncio
    async def test_fallback_model_passed_on_primary_failure(self):
        """主供应商失败时，fallback model 应传递给 _chat_completion"""
        service = AIService()

        call_models = []

        async def mock_completion(*args, **kwargs):
            call_models.append(kwargs.get("model"))
            if len(call_models) == 1:
                raise TimeoutError("Primary timed out")
            return {"score": 75}

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
                assert call_models == ["deepseek/deepseek-v4-flash", "qwen/qwen-max"]

    @pytest.mark.asyncio
    async def test_model_override_propagates_to_litellm(self):
        """model_override 包含 / 时直接作为 litellm model 使用"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        params_default = AIService._build_litellm_params(llm_config, messages)
        assert params_default["model"] == "deepseek/deepseek-v4-flash"

        params_override = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        assert params_override["model"] == "qwen/qwen-max"

    @pytest.mark.asyncio
    async def test_model_override_none_uses_config_default(self):
        """model_override=None 时应使用配置中的默认 model"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        params = AIService._build_litellm_params(llm_config, messages, model_override=None)
        assert params["model"] == "deepseek/deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_model_override_empty_string_uses_config_default(self):
        """model_override='' 时应使用配置中的默认 model"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        params = AIService._build_litellm_params(llm_config, messages, model_override="")
        assert params["model"] == "deepseek/deepseek-v4-flash"


class TestFailoverCancelledError:
    """测试 _chat_completion_with_failover 正确传播 CancelledError"""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_not_swallowed(self):
        """CancelledError 应被立即 re-raise，不触发 fallback"""
        import asyncio

        service = AIService()

        async def mock_completion(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(service, "_chat_completion", new_callable=AsyncMock) as mock_completion_patch:
            mock_completion_patch.side_effect = mock_completion

            with patch("utils.config_handler.ConfigHandler.get_failover_config") as mock_config:
                mock_config.return_value = {
                    "primary": "deepseek/deepseek-v4-flash",
                    "fallbacks": ["qwen/qwen-max"],
                }

                with pytest.raises(asyncio.CancelledError):
                    await service._chat_completion_with_failover(
                        messages=[{"role": "user", "content": "test"}],
                    )

                mock_completion_patch.assert_called_once()


class TestReasoningCheckWithModelOverride:
    """测试 _chat_completion_litellm 中 reasoning 检查跟随 model_override"""

    @pytest.mark.asyncio
    async def test_reasoning_check_uses_model_override(self):
        """model_override 时应使用 override 的模型检查 reasoning 支持"""
        from unittest.mock import MagicMock, patch

        service = AIService()
        service._is_cloud_configured = True
        service._litellm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }

        checked_models = []

        def mock_check_reasoning(model: str) -> bool:
            checked_models.append(model)
            return "opus" in model.lower()

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        delta1 = MagicMock()
        delta1.content = "test response"
        chunk1.choices[0].delta = delta1

        async def mock_stream(**kwargs):
            yield chunk1

        with (
            patch("services.ai_service._check_reasoning_support", side_effect=mock_check_reasoning),
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await service._chat_completion_litellm(
                messages=[{"role": "user", "content": "test"}],
                model_override="anthropic/claude-opus-4-7",
                on_chunk=MagicMock(),
            )

        assert len(checked_models) == 1
        assert checked_models[0] == "anthropic/claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_reasoning_check_uses_config_without_override(self):
        """无 model_override 时应使用主配置的模型检查 reasoning 支持"""
        from unittest.mock import MagicMock, patch

        service = AIService()
        service._is_cloud_configured = True
        service._litellm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com",
        }

        checked_models = []

        def mock_check_reasoning(model: str) -> bool:
            checked_models.append(model)
            return False

        chunk1 = MagicMock()
        chunk1.choices = [MagicMock()]
        delta1 = MagicMock()
        delta1.content = "test response"
        chunk1.choices[0].delta = delta1

        async def mock_stream(**kwargs):
            yield chunk1

        with (
            patch("services.ai_service._check_reasoning_support", side_effect=mock_check_reasoning),
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await service._chat_completion_litellm(
                messages=[{"role": "user", "content": "test"}],
                on_chunk=MagicMock(),
            )

        assert len(checked_models) == 1
        assert checked_models[0] == "deepseek/deepseek-v4-flash"


class TestCrossProviderFailoverCredentials:
    """测试跨供应商 failover 时 api_key/api_base 的正确切换"""

    def test_same_provider_uses_primary_api_key(self):
        """同供应商 failover 使用主配置的 api_key"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-primary-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        params = AIService._build_litellm_params(llm_config, messages, model_override="deepseek/deepseek-v4-pro")
        assert params["api_key"] == "sk-primary-key"
        assert params["model"] == "deepseek/deepseek-v4-pro"

    def test_cross_provider_reads_from_provider_credentials(self):
        """跨供应商 failover 时从 ConfigHandler.get_llm_config_for_provider 读取凭证"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {
            "api_key": "sk-qwen-from-credentials",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        assert params["model"] == "qwen/qwen-max"
        assert params["api_key"] == "sk-qwen-from-credentials"
        assert params["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_cross_provider_without_credentials_omits_api_key(self):
        """跨供应商 failover 且无凭证配置时不设置 api_key，但使用默认 base_url"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": None, "base_url": ""}
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
        assert params["model"] == "openai/gpt-4o"
        assert "api_key" not in params
        # 修复后：无凭证 base_url 时回退到 LLM_PROVIDERS 默认值
        assert params["api_base"] == "https://api.openai.com"

    def test_cross_provider_uses_credential_base_url(self):
        """跨供应商 failover 时使用凭证中的 base_url"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": "sk-openai-key", "base_url": "https://api.openai.com"}
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
        assert params["model"] == "openai/gpt-4o"
        assert params["api_key"] == "sk-openai-key"
        assert params["api_base"] == "https://api.openai.com"

    def test_no_override_uses_primary_config(self):
        """无 model_override 时使用主配置的 api_key"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-primary-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        params = AIService._build_litellm_params(llm_config, messages)
        assert params["api_key"] == "sk-primary-key"
        assert params["model"] == "deepseek/deepseek-v4-flash"


class TestCrossProviderCredentialFallback:
    """跨供应商 failover 凭证统一从 ConfigHandler.get_llm_config_for_provider 读取"""

    def test_reads_provider_credential_from_config_handler(self):
        """跨供应商 failover 从 ConfigHandler.get_llm_config_for_provider 读取凭证"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {
            "api_key": "sk-qwen-from-credentials",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        assert params["api_key"] == "sk-qwen-from-credentials"
        assert params["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_no_credential_logs_debug(self):
        """ConfigHandler 也无凭证时，不设置 api_key 并输出 debug 日志，但使用默认 base_url"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": None, "base_url": ""}
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
        assert "api_key" not in params
        # 修复后：无凭证 base_url 时回退到 LLM_PROVIDERS 默认值
        assert params["api_base"] == "https://api.openai.com"


class TestCrossProviderBaseUrlFallback:
    """测试跨供应商 failover 时 base_url 回退逻辑"""

    def test_credential_base_url_preferred_over_default(self):
        """凭证中的 base_url 应优先于 LLM_PROVIDERS 默认值"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        # 用户自定义了一个不同的 base_url
        mock_credential = {
            "api_key": "sk-openai-custom",
            "base_url": "https://custom-openai-proxy.example.com",
        }
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
        # 应使用用户自定义的 base_url，而非 LLM_PROVIDERS 默认值
        assert params["api_base"] == "https://custom-openai-proxy.example.com"

    def test_fallback_to_llm_providers_default_base_url(self):
        """无凭证 base_url 时回退到 LLM_PROVIDERS 中的默认值"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        # 凭证中没有 base_url
        mock_credential = {"api_key": "sk-qwen-key", "base_url": ""}
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        # 应使用 LLM_PROVIDERS 中 qwen 的默认 base_url
        assert params["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_custom_provider_no_base_url_fallback(self):
        """custom 供应商无默认 base_url，不应设置 api_base"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": "sk-custom-key", "base_url": ""}
        with patch("services.ai_service.ConfigHandler.get_llm_config_for_provider", return_value=mock_credential):
            params = AIService._build_litellm_params(llm_config, messages, model_override="custom/my-model")
        assert params["model"] == "custom/my-model"
        # custom 供应商在 LLM_PROVIDERS 中 base_url 为空，不应设置 api_base
        assert "api_base" not in params
