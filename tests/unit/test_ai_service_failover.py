"""
P1-12: 测试 AI 服务多供应商 fallback 功能（L4: 手写 failover 循环 → litellm.Router）
"""

import pytest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import httpx

from core.errors import AIConfigError
from services.ai_service import AIService, AIServiceUnavailableError
from services.ai_service.litellm_client import LiteLLMClient

pytestmark = pytest.mark.unit

FAKE_FAILOVER_CONFIG = {
    "primary": "deepseek/deepseek-v4-flash",
    "fallbacks": ["qwen/qwen-max"],
}

FAKE_LLM_CONFIG = {
    "provider": "deepseek",
    "model": "deepseek-v4-flash",
    "api_key": "sk-primary-key",
    "base_url": "https://api.deepseek.com",
}


def _cloud_ready_service() -> AIService:
    """构造云端可用状态的 AIService（单例由 conftest 自动重置隔离）。"""
    service = AIService()
    service._is_cloud_configured = True
    service._litellm_config = dict(FAKE_LLM_CONFIG)
    return service


@contextmanager
def _router_failover_env(service: AIService, router: Any):
    """为 _router_failover 测试搭建环境：门控链全部放行，注入假 Router。

    with 块内 yield egress audit mock；块结束时自动恢复全部 patch。
    """
    egress = AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=False))
        stack.enter_context(
            patch(
                "utils.config_handler.ConfigHandler.get_failover_config",
                return_value=dict(FAKE_FAILOVER_CONFIG),
            )
        )
        stack.enter_context(patch("services.ai_service._ensure_litellm_loaded", return_value=True))
        stack.enter_context(patch("services.ai_service._ensure_router_loaded", return_value=True))
        stack.enter_context(patch("services.ai_service._litellm_router", router))
        # _record_cloud_egress 是 LiteLLMClient 的方法（self 为 client 实例），patch 类方法
        stack.enter_context(patch.object(LiteLLMClient, "_record_cloud_egress", egress))
        yield egress


def _ok_resp(content: str, model: str | None = None, usage=None):
    """构造 Router.acompletion 的非流式成功响应（ModelResponse 同构）。"""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        model=model,
        usage=usage,
    )


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
        config_handler.ConfigHandler._clear_cache()

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
        config_handler.ConfigHandler._clear_cache()

        config = config_handler.ConfigHandler.get_failover_config()

        assert config["primary"] == "deepseek/deepseek-v4-flash"
        assert config["fallbacks"] == []


class TestChatCompletionWithFailover:
    """测试 _router_failover（L4 Router 语义）"""

    @pytest.mark.asyncio
    async def test_primary_success_no_fallback(self):
        """primary 成功时正常返回，Router 收到 model_name=primary"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(
            return_value=_ok_resp(
                '{"score": 85, "recommendation": "buy"}',
                model="deepseek/deepseek-v4-flash",
            )
        )
        with _router_failover_env(service, router) as egress:
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
            )

        assert result["score"] == 85
        router.acompletion.assert_awaited_once()
        assert router.acompletion.call_args.kwargs["model"] == "deepseek/deepseek-v4-flash"
        # primary 实际生效：仅入口审计一次（不补记）
        egress.assert_awaited_once()
        assert egress.await_args.kwargs["model"] == "deepseek/deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_router_fallback_destination_audited(self):
        """Router 实际 fallback 到备选模型时，补记真实目的地并返回其 model"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(
            return_value=_ok_resp(
                '{"score": 75, "recommendation": "sell"}',
                model="qwen/qwen-max",
            )
        )
        with _router_failover_env(service, router) as egress:
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
            )

        assert result["score"] == 75
        assert result["model"] == "qwen/qwen-max"
        # 双层审计：入口 primary 意图 + 实际目的地补记
        assert egress.await_count == 2
        models_audited = [call.kwargs["model"] for call in egress.await_args_list]
        assert models_audited == ["deepseek/deepseek-v4-flash", "qwen/qwen-max"]

    @pytest.mark.asyncio
    async def test_all_transient_failures_raise_unavailable(self):
        """瞬态错误经 Router 重试/fallback 仍失败 → 收敛为 AIServiceUnavailableError"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=httpx.ConnectError("all providers down"))
        with _router_failover_env(service, router):
            with pytest.raises(AIServiceUnavailableError) as exc_info:
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

        assert "All LLM providers failed" in str(exc_info.value)
        assert "deepseek/deepseek-v4-flash" in str(exc_info.value)
        assert "qwen/qwen-max" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_invalid_json_raises_value_error(self):
        """json_mode 下非法 JSON → ValueError（与 _chat_completion 同源语义）"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_ok_resp("not a json", model="deepseek/deepseek-v4-flash"))
        with _router_failover_env(service, router):
            with pytest.raises(ValueError, match="Invalid JSON response"):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

    @pytest.mark.asyncio
    async def test_non_json_mode_returns_raw_content(self):
        """json_mode=False 时返回原始内容字典（content+元数据）"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_ok_resp("plain text", model="deepseek/deepseek-v4-flash"))
        with _router_failover_env(service, router):
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
                json_mode=False,
            )
        assert result["content"] == "plain text"
        assert result["model"] == "deepseek/deepseek-v4-flash"


class TestRouterFailoverEntryGates:
    """测试 _router_failover 入口门控链（cloud 可用性 / primary / Router 可用）"""

    @pytest.mark.asyncio
    async def test_cloud_not_configured_raises(self):
        """云端未配置（is_cloud_available False）→ ValueError"""
        service = AIService()
        service._is_cloud_configured = False
        with _router_failover_env(service, MagicMock()):
            with pytest.raises(ValueError, match="Cloud LLM not configured"):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

    @pytest.mark.asyncio
    async def test_missing_primary_raises_unavailable(self):
        """failover 配置缺 primary → AIServiceUnavailableError"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock()
        with ExitStack() as stack:
            stack.enter_context(patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=False))
            stack.enter_context(
                patch(
                    "utils.config_handler.ConfigHandler.get_failover_config",
                    return_value={"primary": "", "fallbacks": ["qwen/qwen-max"]},
                )
            )
            stack.enter_context(patch("services.ai_service._ensure_router_loaded", return_value=True))
            stack.enter_context(patch("services.ai_service._litellm_router", router))
            with pytest.raises(AIServiceUnavailableError, match="No primary LLM provider"):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )
        router.acompletion.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_router_unavailable_raises(self):
        """Router 构造失败 → AIServiceUnavailableError"""
        service = _cloud_ready_service()
        with ExitStack() as stack:
            stack.enter_context(patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=False))
            stack.enter_context(
                patch(
                    "utils.config_handler.ConfigHandler.get_failover_config",
                    return_value=dict(FAKE_FAILOVER_CONFIG),
                )
            )
            stack.enter_context(patch("services.ai_service._ensure_litellm_loaded", return_value=True))
            stack.enter_context(patch("services.ai_service._ensure_router_loaded", return_value=False))
            with pytest.raises(AIServiceUnavailableError, match="Failover router unavailable"):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )

    @pytest.mark.asyncio
    async def test_news_purpose_unacknowledged_raises_policy_error(self):
        """SEC-01 门控：purpose=news 且未外发知情确认 → AIPolicyNotAcknowledgedError"""
        from core.errors import AIPolicyNotAcknowledgedError

        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock()
        client = LiteLLMClient(service)
        with (
            patch("utils.config_handler.ConfigHandler.is_ai_local_only_mode", return_value=False),
            patch("services.ai_service._ensure_router_loaded", return_value=True),
            patch("services.ai_service._litellm_router", router),
            patch("utils.egress_ack.is_egress_acknowledged", return_value=False),
        ):
            with pytest.raises(AIPolicyNotAcknowledgedError) as exc_info:
                await client._router_failover(
                    messages=[{"role": "user", "content": "test"}],
                    purpose="news",
                )
            # 非交互降级语义：policy 阻断异常承载 i18n 提示 key（SEC-01）
            assert exc_info.value.to_error_info()["message_key"] == "ai_external_acknowledgment_prompt"
        router.acompletion.assert_not_awaited()


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
    """测试 _router_failover 中 model 参数正确传递到 Router（L4 语义）"""

    @pytest.mark.asyncio
    async def test_primary_model_passed_to_router(self):
        """primary 作为 Router 池内 model_name 传递"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_ok_resp('{"score": 85}', model="deepseek/deepseek-v4-flash"))
        with _router_failover_env(service, router):
            await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
            )

        router.acompletion.assert_awaited_once()
        assert router.acompletion.call_args.kwargs["model"] == "deepseek/deepseek-v4-flash"

    @pytest.mark.asyncio
    async def test_actual_fallback_model_in_result(self):
        """Router 响应携带 fallback model 时，result['model'] 为实际生效模型"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(return_value=_ok_resp('{"score": 75}', model="qwen/qwen-max"))
        with _router_failover_env(service, router):
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
            )

        assert result["model"] == "qwen/qwen-max"

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

        # 跨供应商 override：提供 qwen 专属 key（D8-1 禁止全局回退，无专属 key 将抛错）
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value={"api_key": "sk-qwen-key", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
        ):
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
    """测试 _router_failover 正确传播 CancelledError"""

    @pytest.mark.asyncio
    async def test_cancelled_error_propagates_not_swallowed(self):
        """CancelledError 应被立即 re-raise（R2），不收敛为 AIServiceUnavailableError"""
        import asyncio

        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=asyncio.CancelledError())
        with _router_failover_env(service, router):
            with pytest.raises(asyncio.CancelledError):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )
        router.acompletion.assert_awaited_once()


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
            patch(
                "services.ai_service._check_reasoning_support",
                side_effect=mock_check_reasoning,
            ),
            patch("services.ai_service.acompletion", return_value=mock_stream()),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
            # D8-1 跨供应商 override：提供 anthropic 专属 key（禁止全局回退，无专属 key 将抛错）
            patch(
                "services.ai_service.ConfigHandler.get_llm_config_for_provider",
                return_value={"api_key": "sk-anthropic-key", "base_url": "https://api.anthropic.com/v1"},
            ),
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
            patch(
                "services.ai_service._check_reasoning_support",
                side_effect=mock_check_reasoning,
            ),
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
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
            params = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        assert params["model"] == "qwen/qwen-max"
        assert params["api_key"] == "sk-qwen-from-credentials"
        assert params["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_cross_provider_without_credentials_raises(self):
        """跨供应商 failover 且无专属 key 时抛显式异常（D8-1：拒绝凭证跨域泄露）"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": None, "base_url": ""}
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
            with pytest.raises(AIConfigError) as exc_info:
                AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
            assert exc_info.value.to_error_info()["code"] == "ai_config_missing_credential"
            assert exc_info.value.to_error_info()["message_key"] == "ai_failover_missing_credential"

    def test_cross_provider_uses_credential_base_url(self):
        """跨供应商 failover 时使用凭证中的 base_url"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {
            "api_key": "sk-openai-key",
            "base_url": "https://api.openai.com",
        }
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
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
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
            params = AIService._build_litellm_params(llm_config, messages, model_override="qwen/qwen-max")
        assert params["api_key"] == "sk-qwen-from-credentials"
        assert params["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_no_credential_logs_debug(self):
        """跨供应商无专属 key 时显式抛 AIConfigError（D8-1：禁止静默继续，避免凭证跨域泄露）"""
        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "sk-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]

        mock_credential = {"api_key": None, "base_url": ""}
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
            with pytest.raises(AIConfigError) as exc_info:
                AIService._build_litellm_params(llm_config, messages, model_override="openai/gpt-4o")
        assert exc_info.value.to_error_info()["code"] == "ai_config_missing_credential"
        assert exc_info.value.to_error_info()["message_key"] == "ai_failover_missing_credential"


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
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
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
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
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
        with patch(
            "services.ai_service.ConfigHandler.get_llm_config_for_provider",
            return_value=mock_credential,
        ):
            params = AIService._build_litellm_params(llm_config, messages, model_override="custom/my-model")
        assert params["model"] == "custom/my-model"
        # custom 供应商在 LLM_PROVIDERS 中 base_url 为空，不应设置 api_base
        assert "api_base" not in params


class TestUnifiedFailoverErrorClassification:
    """测试 _router_failover 的错误收敛：非瞬态直抛、瞬态转 AIServiceUnavailableError"""

    @pytest.mark.asyncio
    async def test_permanent_auth_error_raises_immediately(self):
        """AuthenticationError (401) 保持原语义：直接上抛，不收敛为"所有供应商失败"。
        （Router 配置 AuthenticationErrorRetries=0 且同组部署数=1，401 不会触发 fallback。）"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=Exception("401 unauthorized: Invalid API key"))
        with _router_failover_env(service, router):
            with pytest.raises(Exception) as exc_info:
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )
        assert "401 unauthorized" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_transient_failure_converges_to_unavailable(self):
        """瞬态速率限制（429）经 Router 重试/fallback 仍失败 → AIServiceUnavailableError，
        Tried 列出 primary 与全部 fallback。"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=Exception("429 too many requests"))
        with _router_failover_env(service, router):
            with pytest.raises(AIServiceUnavailableError) as exc_info:
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )
        message = str(exc_info.value)
        assert "deepseek/deepseek-v4-flash" in message
        assert "qwen/qwen-max" in message

    @pytest.mark.asyncio
    async def test_empty_string_connect_error_converges_to_unavailable(self):
        """httpx.ConnectError("")（空字符串）经 classify_error 判定为瞬态 → 收敛"""
        service = _cloud_ready_service()
        router = MagicMock()
        router.acompletion = AsyncMock(side_effect=httpx.ConnectError(""))
        with _router_failover_env(service, router):
            with pytest.raises(AIServiceUnavailableError, match="All LLM providers failed"):
                await service._chat_completion_with_failover(
                    messages=[{"role": "user", "content": "test"}],
                )


class TestBuildRouterModelList:
    """L4: build_router_model_list 纯函数 — primary+fallbacks → Router.model_list。

    构造期执行 D8-1 凭据跨域校验：跨供应商 fallback 缺专属 key 时**排除该项**并返回
    excluded 清单（不静默继续、不连坐 primary —— primary 永不因 fallback 配置缺陷中断）。
    """

    def _primary_cfg(self, provider="deepseek", model="deepseek-v4-flash"):
        return {
            "provider": provider,
            "model": model,
            "api_key": "sk-primary-key",
            "base_url": "https://api.deepseek.com",
        }

    def test_primary_only_when_no_fallbacks(self):
        from services.ai_service.litellm_client import build_router_model_list

        failover = {"primary": "deepseek/deepseek-v4-flash", "fallbacks": [], "primary_config": self._primary_cfg()}
        model_list, excluded = build_router_model_list(failover)
        assert excluded == []
        assert len(model_list) == 1
        item = model_list[0]
        assert item["model_name"] == "deepseek/deepseek-v4-flash"
        assert item["litellm_params"]["model"] == "deepseek/deepseek-v4-flash"
        assert item["litellm_params"]["api_key"] == "sk-primary-key"
        assert item["litellm_params"]["api_base"] == "https://api.deepseek.com"

    def test_same_provider_fallback_uses_primary_key(self):
        from services.ai_service.litellm_client import build_router_model_list

        failover = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": ["deepseek/deepseek-v4-pro"],
            "primary_config": self._primary_cfg(),
        }
        model_list, excluded = build_router_model_list(failover, failover_credentials={})
        assert excluded == []
        assert len(model_list) == 2
        assert model_list[1]["model_name"] == "deepseek/deepseek-v4-pro"
        # 同供应商 fallback 复用主配置 key（与 _build_litellm_params 语义一致）
        assert model_list[1]["litellm_params"]["api_key"] == "sk-primary-key"

    def test_cross_provider_fallback_uses_dedicated_key(self):
        from services.ai_service.litellm_client import build_router_model_list

        failover = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": ["qwen/qwen-max"],
            "primary_config": self._primary_cfg(),
        }
        creds = {"qwen": {"api_key": "sk-qwen-key", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}}
        model_list, excluded = build_router_model_list(failover, failover_credentials=creds)
        assert excluded == []
        assert len(model_list) == 2
        fb = model_list[1]
        assert fb["litellm_params"]["api_key"] == "sk-qwen-key"
        assert fb["litellm_params"]["api_base"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_cross_provider_missing_key_excluded_not_raise(self):
        """D8-1 迁移：跨供应商 fallback 缺专属 key 时排除该项，不抛错、不连坐 primary。"""
        from services.ai_service.litellm_client import build_router_model_list

        failover = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": ["qwen/qwen-max", "openai/gpt-4o"],
            "primary_config": self._primary_cfg(),
        }
        creds = {"qwen": {"api_key": "sk-qwen-key", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}}
        model_list, excluded = build_router_model_list(failover, failover_credentials=creds)
        # qwen 有 key 保留、openai 缺 key 被排除
        assert len(model_list) == 2
        assert [m["model_name"] for m in model_list] == ["deepseek/deepseek-v4-flash", "qwen/qwen-max"]
        assert excluded == ["openai/gpt-4o"]

    def test_primary_string_without_provider_prefix(self):
        from services.ai_service.litellm_client import build_router_model_list

        # primary 无 provider 前缀时，复用 _build_litellm_params 的 litellm_prefix 补全
        # （与 test_no_override_uses_primary_config 行为一致：model = "deepseek/deepseek-v4-flash"）
        failover = {"primary": "deepseek-v4-flash", "fallbacks": [], "primary_config": self._primary_cfg()}
        model_list, excluded = build_router_model_list(failover)
        assert excluded == []
        assert model_list[0]["litellm_params"]["model"] == "deepseek/deepseek-v4-flash"


class TestCrossProviderFailoverLogWording:
    """Issue D5-9: 跨供应商 failover 无专属 key 时的警告日志与实际参数行为。"""

    def test_cross_provider_failover_without_dedicated_key_logs_warning_and_omits_api_key(self, caplog):
        """目标供应商无专属 API key 时，请求不带 api_key，记录 warning 且不再称 using primary key。"""
        import logging
        from unittest.mock import MagicMock
        from services.ai_service.litellm_client import LiteLLMClient

        mock_service = MagicMock()
        client = LiteLLMClient(mock_service)

        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "sk-primary-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]
        # 模拟目标供应商 openai 未配置 api_key
        failover_credentials = {"openai": {"api_key": "", "base_url": ""}}

        with caplog.at_level(logging.WARNING):
            # D8-1：目标供应商无专属 key 时显式抛 AIConfigError（拒绝凭证跨域泄露），
            # 而非静默省略 api_key 继续发送请求。
            with pytest.raises(AIConfigError) as exc_info:
                client._build_litellm_params(
                    llm_config,
                    messages,
                    model_override="openai/gpt-4o",
                    failover_credentials=failover_credentials,
                )

        # 验证显式失败：异常携带可操作的 i18n 提示
        assert exc_info.value.to_error_info()["code"] == "ai_config_missing_credential"
        assert exc_info.value.to_error_info()["message_key"] == "ai_failover_missing_credential"
        # 验证日志不再输出误导性的 "will not include api_key"
        assert "request will not include api_key" not in caplog.text

    def test_cross_provider_failover_with_dedicated_key_sets_key_and_no_warning(self, caplog):
        """目标供应商配置了专属 API key 时，正确使用该专属 key 且不触发警告。"""
        import logging
        from unittest.mock import MagicMock
        from services.ai_service.litellm_client import LiteLLMClient

        mock_service = MagicMock()
        client = LiteLLMClient(mock_service)

        llm_config = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "api_key": "sk-primary-deepseek-key",
            "base_url": "https://api.deepseek.com",
        }
        messages = [{"role": "user", "content": "test"}]
        failover_credentials = {
            "openai": {
                "api_key": "sk-dedicated-openai-key",
                "base_url": "https://api.openai.com/v1",
            }
        }

        with caplog.at_level(logging.WARNING):
            params = client._build_litellm_params(
                llm_config,
                messages,
                model_override="openai/gpt-4o",
                failover_credentials=failover_credentials,
            )

        assert params.get("api_key") == "sk-dedicated-openai-key"
        assert params["model"] == "openai/gpt-4o"
        assert "has no dedicated API key" not in caplog.text


class TestEnsureRouterLoaded:
    """L4: _ensure_router_loaded 惰性构造 litellm.Router（哨兵 + 失败降级）。"""

    def _base_env(self):
        return [
            patch("services.ai_service._router_import_attempted", False),
            patch("services.ai_service._litellm_router", None),
            patch("services.ai_service._ensure_litellm_loaded", return_value=True),
        ]

    def test_constructs_router_with_fallback_maps(self):
        """Model_list/fallbacks/context_window_fallbacks 按 list-of-dicts 形式传给 Router 构造。"""
        from services.ai_service.litellm_client import _ensure_router_loaded

        captured = {}

        def router_ctor(**kwargs):
            captured.update(kwargs)
            return "ROUTER_INSTANCE"

        failover_config = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": ["deepseek/deepseek-v4-pro"],
            "primary_config": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "sk-primary-key",
                "base_url": "https://api.deepseek.com",
            },
        }
        litellm_mock = MagicMock()
        litellm_mock.Router.side_effect = router_ctor
        # 模块级副作用断言须在 patch 作用域内进行（退出 ExitStack 后 patch 恢复原值）
        import services.ai_service as _ai

        with ExitStack() as stack:
            for p in self._base_env():
                stack.enter_context(p)
            stack.enter_context(
                patch("utils.config_handler.ConfigHandler.get_failover_config", return_value=failover_config)
            )
            stack.enter_context(patch("services.ai_service.litellm", litellm_mock))

            assert _ensure_router_loaded() is True
            # 成功构造后缓存实例，供 _router_failover 复用
            assert _ai._litellm_router == "ROUTER_INSTANCE"

        # 构造参数契约
        assert captured["num_retries"] == 2
        assert captured["fallbacks"] == [{"deepseek/deepseek-v4-flash": ["deepseek/deepseek-v4-pro"]}]
        assert captured["context_window_fallbacks"] == [{"deepseek/deepseek-v4-flash": ["deepseek/deepseek-v4-pro"]}]
        assert captured["cooldown_time"] == 30.0
        assert captured["retry_policy"] is not None

    def test_constructor_failure_degrades_to_false(self, caplog):
        """Router 构造异常 → 返回 False 且记录 warning（不向上抛）。"""
        from services.ai_service.litellm_client import _ensure_router_loaded

        failover_config = {
            "primary": "deepseek/deepseek-v4-flash",
            "fallbacks": [],
            "primary_config": {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "sk-primary-key",
                "base_url": "https://api.deepseek.com",
            },
        }

        def router_ctor(**kwargs):
            raise ValueError("invalid deployment")

        import logging

        litellm_mock = MagicMock()
        litellm_mock.Router.side_effect = router_ctor
        with caplog.at_level(logging.WARNING), ExitStack() as stack:
            for p in self._base_env():
                stack.enter_context(p)
            stack.enter_context(
                patch("utils.config_handler.ConfigHandler.get_failover_config", return_value=failover_config)
            )
            stack.enter_context(patch("services.ai_service.litellm", litellm_mock))

            assert _ensure_router_loaded() is False
            import services.ai_service as _ai

            assert _ai._litellm_router is None

        assert "Router construction failed" in caplog.text

    def test_litellm_not_loaded_returns_false(self):
        """litellm 未加载 → Router 不可构造，返回 False。"""
        from services.ai_service.litellm_client import _ensure_router_loaded

        with ExitStack() as stack:
            stack.enter_context(patch("services.ai_service._router_import_attempted", False))
            stack.enter_context(patch("services.ai_service._ensure_litellm_loaded", return_value=False))
            assert _ensure_router_loaded() is False

    def test_sentinel_skips_rebuild_when_loaded(self):
        """已构造（哨兵置位 + 实例非 None）→ 直接复用，不重建 Router。"""
        from services.ai_service.litellm_client import _ensure_router_loaded

        with ExitStack() as stack:
            stack.enter_context(patch("services.ai_service._router_import_attempted", True))
            stack.enter_context(patch("services.ai_service._litellm_router", "ALREADY_READY"))
            stack.enter_context(patch("services.ai_service.litellm", MagicMock()))

            assert _ensure_router_loaded() is True


class TestRouterFailoverStream:
    """L4: _router_failover 流式路径（动态 reasoning 检测 / usage 采集 / 流中断降级）。"""

    @staticmethod
    def _chunk(content=None, reasoning=None, usage=None, model=None):
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=content, reasoning_content=reasoning))],
            usage=usage,
            model=model,
        )

    @pytest.mark.asyncio
    async def test_stream_collects_dynamic_reasoning_and_usage(self):
        """流式解析动态检测 reasoning_content + 采集 usage + chunk.model 生效模型。"""
        service = _cloud_ready_service()
        router = MagicMock()

        async def stream():
            yield self._chunk(content="part1", reasoning="thinking...", model="qwen/qwen-max")
            yield self._chunk(content="part2", model="qwen/qwen-max")
            yield SimpleNamespace(
                choices=[],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
                model=None,
            )

        router.acompletion = AsyncMock(return_value=stream())
        chunks = []

        def on_chunk(text, is_reasoning):
            chunks.append((text, is_reasoning))

        with _router_failover_env(service, router) as egress:
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
                on_chunk=on_chunk,
                json_mode=False,
            )

        assert result["content"] == "part1part2"
        assert result["reasoning_content"] == "thinking..."
        assert result["usage"] == {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        # chunk.model 动态收集到 fallback 生效模型 → SEC-03 补记审计触发
        assert result["model"] == "qwen/qwen-max"
        assert egress.await_count == 2
        assert on_chunk is not None and chunks
        # 缓冲 flush 语义：内容分块回调（与 _chat_completion_litellm 同构）
        assert any(is_reasoning for _text, is_reasoning in chunks)

    @pytest.mark.asyncio
    async def test_stream_interruption_returns_partial(self):
        """流中途网络中断 → 返回部分结果而非抛错（与 _chat_completion_litellm 同源降级）。"""
        service = _cloud_ready_service()
        router = MagicMock()

        async def stream():
            yield self._chunk(content="partial")
            raise httpx.ReadTimeout("stream cut")

        router.acompletion = AsyncMock(return_value=stream())
        with _router_failover_env(service, router):
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
                on_chunk=lambda text, is_reasoning: None,
                json_mode=False,
            )
        assert result["content"] == "partial"

    @pytest.mark.asyncio
    async def test_stream_reasoning_only_content_fills_content(self):
        """仅有 reasoning 且无 content 时，content 回填 reasoning（同 _chat_completion_litellm 语义）。"""
        service = _cloud_ready_service()
        router = MagicMock()

        async def stream():
            yield self._chunk(content=None, reasoning="only reasoning text")

        router.acompletion = AsyncMock(return_value=stream())
        with _router_failover_env(service, router):
            result = await service._chat_completion_with_failover(
                messages=[{"role": "user", "content": "test"}],
                on_chunk=lambda text, is_reasoning: None,
                json_mode=False,
            )
        assert result["content"] == "only reasoning text"
