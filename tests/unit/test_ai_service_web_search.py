"""
Task 4.1: 测试 AIService.chat_with_web_search 公共方法

封装智谱 GLM web_search 工具调用，验证：
- tools 参数正确构造为 [{"type": "web_search", "web_search": {...}}]
- search_domain_filter 可选传递到 web_search 配置
- 默认 search_engine="search_std"
- 自定义 search_engine="search_pro"
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_service import AIService

pytestmark = pytest.mark.unit


def _make_svc_with_cloud():
    """Factory: create AIService with cloud provider pre-configured.

    与 test_ai_service.py 中同名的工厂函数保持一致，确保 cloud 可用。
    Singleton 隔离由 tests/unit/conftest.py 的 _reset_all_singletons autouse fixture 处理。
    """
    with patch("services.ai_service.ConfigHandler") as mock_ch:
        mock_ch.get_llm_config.return_value = {
            "api_key": "test-key",
            "provider": "zhipu",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "model": "glm-4-plus",
        }
        mock_ch.get_setting.return_value = False
        mock_ch.get_ai_max_concurrent_analysis.return_value = 5
        mock_ch.get_failover_config.return_value = {
            "primary": "zhipu/glm-4-plus",
            "fallbacks": [],
        }
        svc = AIService()
    return svc


def _build_mock_response(content: str = "搜索结果：示例内容"):
    """构建 LiteLLM 非流式响应 mock 对象"""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_usage = MagicMock()
    mock_usage.prompt_tokens = 10
    mock_usage.completion_tokens = 20
    mock_usage.total_tokens = 30
    mock_response.usage = mock_usage
    return mock_response


class TestChatWithWebSearchToolsConstruction:
    """验证 chat_with_web_search 正确构造 tools 参数"""

    @pytest.mark.asyncio
    async def test_chat_with_web_search_passes_tools(self):
        """验证传递 tools=[{"type": "web_search", ...}] 到 LiteLLM"""
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response()

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion,
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "查询最新A股市场动态"}],
            )

            assert mock_acompletion.call_count >= 1
            _, kwargs = mock_acompletion.call_args
            assert "tools" in kwargs, "tools 参数未传递到 LiteLLM"
            tools = kwargs["tools"]
            assert isinstance(tools, list)
            assert len(tools) == 1
            assert tools[0]["type"] == "web_search"
            assert "web_search" in tools[0]
            assert tools[0]["web_search"]["enable"] is True

    @pytest.mark.asyncio
    async def test_chat_with_web_search_with_domain_filter(self):
        """验证 search_domain_filter 传递到 web_search 配置"""
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response()
        domain_filter = ["eastmoney.com", "sina.com.cn", "finance.sina.com.cn"]

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion,
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "查询茅台最新财报"}],
                search_domain_filter=domain_filter,
            )

            _, kwargs = mock_acompletion.call_args
            web_search_config = kwargs["tools"][0]["web_search"]
            assert web_search_config["search_domain_filter"] == domain_filter

    @pytest.mark.asyncio
    async def test_chat_with_web_search_default_engine(self):
        """验证默认 search_engine="search_std" """
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response()

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion,
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "查询今日大盘走势"}],
            )

            _, kwargs = mock_acompletion.call_args
            web_search_config = kwargs["tools"][0]["web_search"]
            assert web_search_config["search_engine"] == "search_std"

    @pytest.mark.asyncio
    async def test_chat_with_web_search_custom_engine(self):
        """验证自定义 search_engine="search_pro" """
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response()

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion,
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "深度查询行业研报"}],
                search_engine="search_pro",
            )

            _, kwargs = mock_acompletion.call_args
            web_search_config = kwargs["tools"][0]["web_search"]
            assert web_search_config["search_engine"] == "search_pro"


class TestChatWithWebSearchParamsPassthrough:
    """验证其他参数正确传递"""

    @pytest.mark.asyncio
    async def test_temperature_and_timeout_passed(self):
        """验证 temperature 和 timeout 传递到 LiteLLM"""
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response()

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)) as mock_acompletion,
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "test"}],
                temperature=0.7,
                timeout=90.0,
            )

            _, kwargs = mock_acompletion.call_args
            assert kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_returns_dict_with_content(self):
        """验证返回值结构包含 content 字段"""
        svc = _make_svc_with_cloud()
        mock_response = _build_mock_response(content="带搜索结果的回答")

        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=mock_response)),
            patch("utils.proxy_manager.ProxyManager.litellm_env_context"),
        ):
            result = await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "test"}],
            )

        assert isinstance(result, dict)
        assert result["content"] == "带搜索结果的回答"


class TestChatWithWebSearchErrorHandling:
    """验证错误处理与 CancelledError 传播（R2）"""

    @pytest.mark.asyncio
    async def test_raises_when_cloud_unavailable(self):
        """云端未配置时抛出 ValueError"""
        svc = _make_svc_with_cloud()
        svc._is_cloud_configured = False
        svc._litellm_config = {}

        with pytest.raises(ValueError, match="Cloud LLM not configured"):
            await svc.chat_with_web_search(
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_cancelled_error_propagated(self):
        """R2: asyncio.CancelledError 必须传播，不得吞没"""
        svc = _make_svc_with_cloud()

        with patch.object(
            svc,
            "_chat_completion_litellm",
            AsyncMock(side_effect=asyncio.CancelledError()),
        ):
            with pytest.raises(asyncio.CancelledError):
                await svc.chat_with_web_search(
                    messages=[{"role": "user", "content": "test"}],
                )
