"""AIService LiteLLM 云端调用子模块（review01-A5b-2）。

自 ``services/ai_service.py`` 移出的 LiteLLM 常量、模块级惰性加载全局、
辅助函数（惰性加载 / 推理支持检测 / API 错误分类）与云端调用方法。

设计约定（测试兼容）：
- 模块级可变全局（litellm / acompletion / LITELLM_AVAILABLE /
  _litellm_import_attempted）经 ``services.ai_service`` 模块属性读写
  （函数内 ``import services.ai_service as _ai``），保证既有测试
  ``patch("services.ai_service.acompletion")`` 等 patch 目标仍生效。
- ``ConfigHandler`` / ``DataSanitizer`` 同样经 ``_ai.ConfigHandler`` /
  ``_ai.DataSanitizer`` 访问，保证测试 ``patch("services.ai_service.ConfigHandler")`` /
  ``patch("services.ai_service.DataSanitizer")`` 生效（本模块不顶层 import 二者）。
- ``LiteLLMClient`` 经构造参数持有 ``AIService`` 实例以访问共享状态
  （``_litellm_config`` / ``_failover_credentials`` / semaphore 等），
  本模块不 import ``services.ai_service`` 顶层符号，避免循环依赖。
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import TYPE_CHECKING, Any

import httpx

from services.ai_service.token_budget import _estimate_tokens, _get_model_context_window
from services.local_model_manager import LocalInferenceTimeoutError, LocalModelManager
from utils.error_classifier import classify_error, classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation

if TYPE_CHECKING:
    from services.ai_service import AIService

logger = logging.getLogger(__name__)

# === LiteLLM 全局配置 ===
LITELLM_SET_TIMEOUT = 30.0
LITELLM_MAX_RETRIES = 2
# HTTP 客户端
DEFAULT_CLOUD_TIMEOUT = 30.0
CONNECT_TIMEOUT = 5.0

# 默认并发数
DEFAULT_ANALYSIS_CONCURRENCY = 5
DEFAULT_NEWS_CONCURRENCY = 1
# 默认超时（秒）
DEFAULT_ANALYSIS_TIMEOUT = 120.0
DEFAULT_VERIFY_TIMEOUT = 10.0
# 本地模型默认 max_tokens
DEFAULT_LOCAL_MAX_TOKENS = 256
# 错误消息截断长度
ERROR_MESSAGE_TRUNCATE_LEN = 100

# R16: LiteLLM 是重库（首次 import 可达 18s+），改为惰性加载，避免 import ai_service
# 时同步阻塞 UI 主循环。模块级符号 (litellm/acompletion/LITELLM_AVAILABLE) 保留以兼容
# 现有测试的 patch 与调用点。__init__ 链（_configure_litellm/_setup_client）不得触发
# import，仅首个真正需要 litellm 的调用点经 _ensure_litellm_loaded() 触发。
LITELLM_AVAILABLE = False
litellm: Any = None
acompletion: Any = None
# 独立 import 重试哨兵：避免以 litellm=False 作哨兵与 `litellm is not None` 判空冲突。
_litellm_import_attempted = False


class AIServiceUnavailableError(Exception):
    """P1-12: 所有 LLM 供应商都不可用时抛出"""

    pass


def _ensure_litellm_loaded() -> bool:
    """惰性加载 litellm 并返回是否可用（R16）。

    仅当真正需要调用 litellm（acompletion 调用点）时才触发 import，
    避免 import ai_service 或 AIService.__init__ 链同步阻塞 UI 主循环。
    加载失败后以 _litellm_import_attempted 阻止重复 import，litellm 保持 None，
    不破坏 `litellm is None` 判空 guard。
    首次成功加载时完成 LiteLLM 全局参数配置（原模块顶层 import 块的职责）。

    可变全局经 ``services.ai_service`` 模块属性读写，保证测试 patch 目标不变。
    经 ``sys.modules`` 而非 ``import`` 获取模块引用，避免触发 ``builtins.__import__``
    （测试以 ``patch("builtins.__import__")`` 验证 no-op/失败路径时不被误拦截）。
    """
    # Any 标注：sys.modules 返回 ModuleType（无已知属性），消除 pyright warning。
    _ai: Any = sys.modules["services.ai_service"]

    if _ai._litellm_import_attempted:
        return _ai.LITELLM_AVAILABLE
    _ai._litellm_import_attempted = True
    try:
        import litellm as _lt  # type: ignore[import-untyped]
        from litellm import acompletion as _ac  # type: ignore[import-untyped]

        _lt.suppress_debug_info = True
        _lt.set_verbose = False  # type: ignore[reportPrivateImportUsage]  # LiteLLM private API usage for logging suppression
        _lt.drop_params = True
        _lt.set_timeout = LITELLM_SET_TIMEOUT  # type: ignore[attr-defined]
        _lt.max_retries = LITELLM_MAX_RETRIES  # type: ignore[attr-defined]
        _lt.success_callback = []
        _lt.failure_callback = []
        _lt.modify_params = True
        _ai.litellm, _ai.acompletion = _lt, _ac
        _ai.LITELLM_AVAILABLE = True
        return True
    except Exception:
        # 兼容不同 litellm 版本：import 或全局参数配置（如某版本缺失某属性抛
        # AttributeError）失败均视为"不可用"，优雅降级而非向上传播。
        # 注意 except Exception 不捕获 asyncio.CancelledError / KeyboardInterrupt (R2)。
        _ai.LITELLM_AVAILABLE = False
        logger.warning("[AIService] LiteLLM not available, cloud LLM features disabled")
        return False


def _check_reasoning_support(model: str) -> bool:
    """检查模型是否支持推理增强 (reasoning_content)

    R16: litellm 惰性加载后，__init__ 链不得触发 import。此处仅当 litellm 已加载
    （模块级符号非 None）时才用 litellm.utils.supports_reasoning；未加载时直接走
    LLM_PROVIDERS fallback 判定，不触发 _ensure_litellm_loaded()。
    """
    import services.ai_service as _ai

    if _ai.litellm is not None:
        try:
            return _ai.litellm.utils.supports_reasoning(model=model)
        except Exception as exc:
            log_classified(
                logger,
                exc,
                "general",
                "[AIService] supports_reasoning check failed (%s) for %s: %s, using LLM_PROVIDERS fallback",
                model,
                exc_info=True,
            )

    from utils.llm_providers import LLM_PROVIDERS

    # Derive reasoning model IDs from LLM_PROVIDERS tags
    for provider_config in LLM_PROVIDERS.values():
        for m in provider_config.get("models", []):
            tag = m.get("tag", "")
            tags = tag if isinstance(tag, list) else [tag]
            if "reasoning" in tags:
                # F4-S-4: Exact match (conservative: avoid false-positive reasoning
                # support detection for variant model names like "qwen3.6-max-no-reasoning")
                model_lower = model.lower()
                model_id_lower = m["id"].lower()
                if model_lower == model_id_lower:
                    return True
    return False


class LiteLLMClient:
    """LiteLLM 云端 LLM 调用子模块（review01-A5b-2）。

    持有 ``AIService`` 实例经 ``self._service`` 访问共享状态（_litellm_config /
    _failover_credentials / 云端可用性 / semaphore / 本地模型加载），
    本模块不 import ``services.ai_service`` 顶层符号以避免循环依赖。
    """

    def __init__(self, service: AIService) -> None:
        self._service = service

    @staticmethod
    def _build_litellm_params(
        llm_config: dict,
        messages: list,
        model_override: str | None = None,
        failover_credentials: dict[str, dict] | None = None,
        **kwargs,
    ) -> dict:
        """
        构建 LiteLLM 请求参数 (静态方法，供 test_connection 复用)

        Args:
            llm_config: LLM 配置字典
            messages: 消息列表
            model_override: 覆盖 llm_config 中的 model 字段（用于 failover 切换供应商）
            failover_credentials: 预加载的跨供应商凭证缓存 {provider: config_dict}
            **kwargs: 其他参数

        Azure 特殊处理:
        - base_url: https://{resource_name}.openai.azure.com (不含 deployments 路径)
        - model: azure/{deployment_name}
        - api_version: 作为独立参数传递
        """
        # 经组合根模块属性访问 ConfigHandler：保证测试
        # patch("services.ai_service.ConfigHandler.get_llm_config_for_provider") 生效。
        import services.ai_service as _ai

        provider = llm_config.get("provider", "custom")
        model = model_override or llm_config.get("model", "")

        if not model:
            raise ValueError("Model ID is required but empty")

        request_params: dict = {
            "messages": messages,
        }

        model_has_prefix = "/" in model
        override_provider_prefix = model.split("/")[0] if model_has_prefix else None
        is_cross_provider = model_has_prefix and model_override is not None and override_provider_prefix != provider

        if provider == "azure" and not model_has_prefix:
            request_params["model"] = f"azure/{model}"
            request_params["api_key"] = llm_config.get("api_key")
            azure_resource_name = llm_config.get("azure_resource_name", "")
            if azure_resource_name:
                request_params["api_base"] = f"https://{azure_resource_name}.openai.azure.com"
            else:
                request_params["api_base"] = llm_config.get("base_url", "")
            from utils.llm_providers import AZURE_DEFAULT_API_VERSION

            request_params["api_version"] = llm_config.get("api_version", AZURE_DEFAULT_API_VERSION)
        elif model_has_prefix:
            request_params["model"] = model
            if is_cross_provider:
                override_provider = model.split("/")[0]
                # Use pre-loaded failover credentials cache to avoid keyring calls on hot path
                override_llm_config = (failover_credentials or {}).get(
                    override_provider
                ) or _ai.ConfigHandler.get_llm_config_for_provider(override_provider)
                if override_llm_config.get("api_key"):
                    request_params["api_key"] = override_llm_config["api_key"]
                else:
                    logger.debug(
                        "[AIService] Cross-provider failover to '%s' has no dedicated API key, using primary key (may fail)",
                        override_provider,
                    )
                # Prefer credential's base_url, fallback to LLM_PROVIDERS default
                override_base_url = override_llm_config.get("base_url")
                if override_base_url:
                    request_params["api_base"] = override_base_url
                else:
                    # Fallback to default base_url from LLM_PROVIDERS configuration
                    from utils.llm_providers import LLM_PROVIDERS

                    default_base_url = LLM_PROVIDERS.get(override_provider, {}).get("base_url", "")
                    if default_base_url:
                        request_params["api_base"] = default_base_url
            else:
                request_params["api_key"] = llm_config.get("api_key")
                request_params["api_base"] = llm_config.get("base_url", "")
        else:
            from utils.llm_providers import LLM_PROVIDERS

            provider_config = LLM_PROVIDERS.get(provider, {})
            prefix = provider_config.get("litellm_prefix", "openai")
            request_params["model"] = f"{prefix}/{model}"
            request_params["api_key"] = llm_config.get("api_key")
            request_params["api_base"] = llm_config.get("base_url", "")

        if "temperature" in kwargs:
            request_params["temperature"] = kwargs["temperature"]
        if "max_tokens" in kwargs:
            request_params["max_tokens"] = kwargs["max_tokens"]
        if "response_format" in kwargs:
            request_params["response_format"] = kwargs["response_format"]
        if "tools" in kwargs:
            request_params["tools"] = kwargs["tools"]

        timeout_val = kwargs.get("timeout", DEFAULT_CLOUD_TIMEOUT)
        request_params["timeout"] = httpx.Timeout(timeout_val, connect=CONNECT_TIMEOUT)

        return request_params

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def _chat_completion_litellm(
        self,
        messages: list,
        on_chunk=None,
        model_override: str | None = None,
        **kwargs,
    ) -> dict:
        """
        LiteLLM 1.82+ 版本的云端调用

        Args:
            messages: 消息列表
            on_chunk: 流式回调函数 (content, is_reasoning)
            model_override: 覆盖配置中的 model（用于 failover 切换供应商）
            **kwargs: 其他参数

        Returns:
            {"content": str, "usage": dict, "reasoning_content": str}
        """
        llm_config = self._service._litellm_config
        request_params = self._build_litellm_params(
            llm_config,
            messages,
            model_override=model_override,
            failover_credentials=self._service._failover_credentials,
            **kwargs,
        )

        total_tokens = sum(_estimate_tokens(m.get("content")) for m in messages)
        context_window = _get_model_context_window(llm_config, model_override)
        if total_tokens > context_window:
            logger.warning(
                "[AIService] Cloud | Prompt may exceed context window: ~%d tokens (window %d)",
                total_tokens,
                context_window,
            )

        # S1-4 fix: Real-time reasoning support check for model switching
        # （须在 _ensure_litellm_loaded 之后，litellm 已加载时才能用精确 supports_reasoning 判定）
        from utils.proxy_manager import ProxyManager

        # R16: 首个真正需要 litellm 的调用点才触发惰性加载（用户主动 AI 调用）。
        # 辅助函数经组合根模块 (services.ai_service) 调用，保证既有测试对
        # `services.ai_service._ensure_litellm_loaded` / `_check_reasoning_support`
        # 的 patch 目标生效。
        import services.ai_service as _ai

        if not _ai._ensure_litellm_loaded():
            raise RuntimeError("LiteLLM not installed, cloud LLM features disabled")

        if model_override:
            effective_model = model_override
        else:
            _provider = llm_config.get("provider", "")
            _model_id = llm_config.get("model", "")
            effective_model = f"{_provider}/{_model_id}" if _provider else _model_id
        supports_reasoning = _ai._check_reasoning_support(effective_model)

        stream = kwargs.get("stream", False) or on_chunk is not None

        with ProxyManager.litellm_env_context():
            if stream:
                if supports_reasoning:
                    request_params["stream_options"] = {"include_usage": True}

                response = await _ai.acompletion(stream=True, **request_params)
                response_content = ""
                reasoning_content = ""
                usage = None

                _CHUNK_BUFFER_CHARS = 50
                _content_buf: list[str] = []
                _reasoning_buf: list[str] = []

                def _flush_content_buf():
                    nonlocal _content_buf
                    if _content_buf and on_chunk:
                        on_chunk("".join(_content_buf), False)
                    _content_buf = []

                def _flush_reasoning_buf():
                    nonlocal _reasoning_buf
                    if _reasoning_buf and on_chunk:
                        on_chunk("".join(_reasoning_buf), True)
                    _reasoning_buf = []

                try:
                    async for chunk in response:  # type: ignore[reportGeneralTypeIssues]  # LiteLLM stream response type mismatch
                        if not chunk.choices:
                            if hasattr(chunk, "usage") and chunk.usage:
                                usage = {
                                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0),
                                    "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                                }
                            continue

                        delta = chunk.choices[0].delta

                        if supports_reasoning:
                            reasoning = getattr(delta, "reasoning_content", None)
                            if reasoning:
                                reasoning_content += reasoning
                                if on_chunk:
                                    _reasoning_buf.append(reasoning)
                                    if sum(len(s) for s in _reasoning_buf) >= _CHUNK_BUFFER_CHARS:
                                        _flush_reasoning_buf()

                        if delta.content:
                            response_content += delta.content
                            if on_chunk:
                                _content_buf.append(delta.content)
                                if sum(len(s) for s in _content_buf) >= _CHUNK_BUFFER_CHARS:
                                    _flush_content_buf()
                except (
                    httpx.ReadTimeout,
                    httpx.ConnectTimeout,
                    httpx.ReadError,
                    httpx.ConnectError,
                    ConnectionError,
                    ConnectionResetError,
                    BrokenPipeError,
                    OSError,
                    TimeoutError,
                ) as stream_err:
                    logger.warning(
                        "[AIService] Stream interrupted after %d chars: %s. Returning partial result.",
                        len(response_content),
                        _ai.DataSanitizer.sanitize_error(stream_err),
                    )

                try:
                    _flush_content_buf()
                    _flush_reasoning_buf()
                except Exception as flush_err:
                    log_classified(
                        logger,
                        flush_err,
                        "general",
                        "[AIService] Failed to flush chunk buffer after stream (%s): %s",
                        exc_info=True,
                    )

                if not response_content and reasoning_content:
                    response_content = reasoning_content

                result = {"content": response_content}
                if reasoning_content:
                    result["reasoning_content"] = reasoning_content
                if usage:
                    result["usage"] = usage

                return result
            else:
                response = await _ai.acompletion(**request_params)
                content = response.choices[0].message.content  # type: ignore[union-attr]
                result = {"content": content}

                if hasattr(response, "usage") and response.usage:  # type: ignore[union-attr]
                    result["usage"] = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),  # type: ignore[union-attr]
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0),  # type: ignore[union-attr]
                        "total_tokens": getattr(response.usage, "total_tokens", 0),  # type: ignore[union-attr]
                    }

                return result

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def _chat_completion(
        self,
        messages: list,
        model: str | None = None,
        provider: str = "cloud",
        temperature: float = 0.3,
        timeout: float = DEFAULT_CLOUD_TIMEOUT,
        json_mode: bool = True,
        on_chunk=None,
        purpose: str = "analysis",
        local_max_tokens: int = DEFAULT_LOCAL_MAX_TOKENS,
    ) -> dict:
        """
        Unified helper for Chat Completions (Cloud or Local).
        Args:
            messages: List of {"role":..., "content":...}
            model: Model name (optional, defaults to config)
            provider: 'cloud' or 'local'
            temperature: sampling temp
            timeout: timeout in seconds
            json_mode: whether to enforce JSON return
            local_max_tokens: max tokens for local model inference (default 256 for news classification)
        Returns:
            dict: Parsed JSON content (or raw dict if non-json)
        Raises:
            Exception: on failure (caller should handle fallback)
        """
        import services.ai_service as _ai

        response_content = ""

        # --- Local Provider ---
        if provider == "local":
            await self._service._setup_local_model()
            manager = await LocalModelManager.get_instance()

            system_prompt = next(
                (m["content"] for m in messages if m["role"] == "system"),
                "You are a helpful assistant.",
            )
            user_prompt = next(
                (m["content"] for m in messages if m["role"] == "user"),
                "",
            )

            if not manager.get_loaded_model_path():
                raise ValueError("Local model not loaded")

            response_content = await manager.run_inference(
                prompt=user_prompt,
                max_tokens=local_max_tokens,
                temperature=temperature,
                system_prompt=system_prompt,
            )

        # --- Cloud Provider ---
        else:
            if not self._service.is_cloud_available():
                raise ValueError("Cloud LLM not configured. Please set up API Key.")

            sem = self._service._get_news_semaphore() if purpose == "news" else self._service._get_analysis_semaphore()
            async with sem:
                logger.debug(
                    "[AIService] Cloud | Invoking LiteLLM (%d messages)",
                    len(messages),
                )

                # 经组合根 (self._service) 调用：保证测试对 AIService 实例属性
                # （如 `svc._chat_completion_litellm = AsyncMock(...)`）的 monkeypatch 生效。
                result = await self._service._chat_completion_litellm(
                    messages,
                    on_chunk=on_chunk,
                    model_override=model,
                    temperature=temperature,
                    timeout=timeout,
                    response_format={"type": "json_object"} if json_mode else None,
                )
                response_content = result["content"]

        # --- Post-Processing (JSON Parsing) ---
        if json_mode:
            try:
                # 1. Cleaner: Try direct parse
                return json.loads(response_content)
            except json.JSONDecodeError:
                pass

            # 2. Heuristic Extraction
            try:
                start = response_content.find("{")
                if start != -1:
                    try:
                        obj, idx = json.JSONDecoder().raw_decode(
                            response_content[start:],
                        )
                        return obj
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                log_classified(
                    logger,
                    e,
                    "general",
                    "[AIService] JSON heuristic extraction failed (%s): %s",
                    exc_info=True,
                )

            raise ValueError(f"Invalid JSON response: {_ai.DataSanitizer.sanitize_error(response_content[:100])}...")

        return {"content": response_content}

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def _chat_completion_with_failover(
        self,
        messages: list,
        timeout: float = DEFAULT_ANALYSIS_TIMEOUT,
        json_mode: bool = True,
        on_chunk=None,
    ) -> dict:
        """
        P1-12: 带多供应商 fallback 的云端分析

        当主供应商失败时，自动切换到备用供应商。
        仅对可恢复错误（RateLimitError, ServiceUnavailableError, Timeout）进行 fallback。
        永久错误（AuthenticationError, ContentPolicyViolationError）直接抛出。

        Args:
            messages: 消息列表
            timeout: 超时时间
            json_mode: 是否启用 JSON 模式
            on_chunk: 流式回调

        Returns:
            dict: 解析后的响应

        Raises:
            AIServiceUnavailableError: 所有供应商都失败时抛出
        """
        # 注意：failover 配置须经真实 ConfigHandler 局部导入读取（与 HEAD 一致）。
        # 既有测试 patch 目标为 ``utils.config_handler.ConfigHandler.get_failover_config``，
        # 且 analyze_stock 测试在 mock ``services.ai_service.ConfigHandler`` 时依赖真实
        # 单例返回非空 primary（若经组合根模块访问会被 MagicMock 吞掉导致 "Tried: []"）。
        # 仅错误日志路径的 DataSanitizer 经组合根模块属性访问。
        from utils.config_handler import ConfigHandler

        import services.ai_service as _ai

        failover_config = ConfigHandler.get_failover_config()
        primary = failover_config.get("primary", "")
        fallbacks = failover_config.get("fallbacks", [])

        models_to_try = [primary] + fallbacks
        last_error: Exception | None = None

        for i, model in enumerate(models_to_try):
            if not model:
                continue

            try:
                logger.debug(
                    "[AIService] Failover | Attempt %d/%d: %s",
                    i + 1,
                    len(models_to_try),
                    model,
                )

                # 经组合根 (self._service) 调用：保证测试对 AIService 实例属性
                # （如 `svc._chat_completion = AsyncMock(...)`）的 monkeypatch 生效。
                result = await self._service._chat_completion(
                    messages,
                    provider="cloud",
                    model=model,
                    timeout=timeout,
                    json_mode=json_mode,
                    on_chunk=on_chunk,
                    purpose="analysis",
                )

                if i > 0:
                    logger.info(
                        "[AIService] Failover | ✅ Succeeded on fallback model: %s",
                        model,
                    )

                return result

            except asyncio.CancelledError:
                logger.debug("[AIService] Failover | Cancelled during attempt %d/%d", i + 1, len(models_to_try))
                raise
            except Exception as e:
                last_error = e
                error_type = type(e).__name__

                # LocalInferenceTimeoutError 是本地模型超时，不属于云端 failover 范畴，直接抛出
                # 由 analyze_stock 的 except LocalInferenceTimeoutError 捕获并返回 {"error": "Local model timeout"}
                if isinstance(e, LocalInferenceTimeoutError):
                    raise

                error_info = classify_error(e, context="llm")
                severity = classify_severity(e, context="llm")

                # System-level errors (MemoryError, etc.) must propagate at CRITICAL
                if severity == "system":
                    logger.critical(
                        "[AIService] Failover | SYSTEM-LEVEL failure for %s: %s",
                        model,
                        _ai.DataSanitizer.sanitize_error(e),
                        exc_info=True,
                    )
                    raise

                is_transient = bool(error_info.get("should_retry", False))

                if is_transient:
                    # Truncate before sanitizing to avoid breaking sanitization markers
                    raw_msg = str(e)
                    truncated_raw = (
                        raw_msg[:ERROR_MESSAGE_TRUNCATE_LEN] if len(raw_msg) > ERROR_MESSAGE_TRUNCATE_LEN else raw_msg
                    )
                    logger.warning(
                        "[AIService] Failover | ⚠️ %s failed (%s: %s)",
                        model,
                        error_type,
                        _ai.DataSanitizer.sanitize_error(truncated_raw),
                    )
                    continue
                else:
                    logger.error(
                        "[AIService] Failover | ❌ Non-transient error (%s) for %s: %s",
                        error_info.get("code", "unknown"),
                        model,
                        error_type,
                    )
                    raise

        all_models_tried = ", ".join(m for m in models_to_try if m)
        raise AIServiceUnavailableError(f"All LLM providers failed. Tried: [{all_models_tried}]") from last_error

    @log_async_operation(
        operation_name="AIService.verify_connection",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def verify_connection(self) -> bool:
        """
        Verify API connection by sending a minimal request.
        """
        if not self._service.is_cloud_available():
            return False

        try:
            # 经组合根 (self._service) 调用：保证测试对 AIService 实例属性
            # （如 `svc._chat_completion_litellm = AsyncMock(...)`）的 monkeypatch 生效。
            await self._service._chat_completion_litellm(
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1,
                timeout=DEFAULT_VERIFY_TIMEOUT,
            )
            return True
        except Exception as e:
            log_classified(
                logger,
                e,
                "llm",
                "[AIService] Verify | ❌ Connection verification failed (%s): %s",
                exc_info=True,
            )
            logger.debug("[AIService] Verify | Connection verification traceback:", exc_info=True)
            raise

    @log_async_operation(
        operation_name="chat_with_web_search",
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def chat_with_web_search(
        self,
        messages: list[dict],
        search_domain_filter: list[str] | None = None,
        search_engine: str = "search_std",
        temperature: float = 0.3,
        timeout: float = 60.0,
    ) -> dict:
        """
        使用智谱 GLM web_search 工具进行带网络搜索的对话。

        封装 LiteLLM tools API，构造 web_search 工具调用。仅适用于支持
        web_search 工具的模型（如智谱 GLM-4 系列）。

        Args:
            messages: 消息列表 [{"role":..., "content":...}]
            search_domain_filter: 域名过滤列表，限制搜索范围（如财经网站）
            search_engine: 搜索引擎，"search_std"（标准）或 "search_pro"（增强）
            temperature: 采样温度
            timeout: 超时时间（秒）

        Returns:
            {"content": str, "usage": dict, "reasoning_content": str}

        Raises:
            ValueError: 云端 LLM 未配置时抛出
            asyncio.CancelledError: 任务被取消时传播（R2）
        """
        if not self._service.is_cloud_available():
            raise ValueError("Cloud LLM not configured. Please set up API Key.")

        web_search_config: dict = {
            "enable": True,
            "search_engine": search_engine,
        }
        if search_domain_filter:
            web_search_config["search_domain_filter"] = search_domain_filter

        tools = [{"type": "web_search", "web_search": web_search_config}]

        # 经组合根 (self._service) 调用：保证测试对 AIService 实例属性
        # （如 `svc._chat_completion_litellm = AsyncMock(...)`）的 monkeypatch 生效。
        return await self._service._chat_completion_litellm(
            messages,
            temperature=temperature,
            timeout=timeout,
            tools=tools,
        )

    @staticmethod
    @log_async_operation(
        operation_name="AIService.test_connection",
        threshold_ms=PerfThreshold.EXTERNAL_NETWORK,
    )
    async def test_connection(
        provider: str = "deepseek",
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        **kwargs,
    ) -> dict:
        """
        Static method to test connection with provided credentials (without saving).

        Args:
            provider: 供应商 ID
            model: 模型 ID
            base_url: API 基础 URL
            api_key: API Key
            **kwargs: 扩展字段 (如 Azure 的 azure_resource_name, api_version)

        Returns:
            {"success": bool, "message": str, "usage": dict}
        """
        if not api_key:
            return {"success": False, "message": "llm_test_need_key"}

        if not model:
            return {"success": False, "message": "llm_test_need_model"}

        # 经组合根模块属性访问：保证测试 patch services.ai_service._ensure_litellm_loaded /
        # _check_reasoning_support / acompletion 生效。sys.modules 而非 import 避免触发
        # builtins.__import__（与 _ensure_litellm_loaded 约定一致）。
        # Any 标注：sys.modules 返回 ModuleType（无已知属性），消除 pyright warning。
        _ai: Any = sys.modules["services.ai_service"]

        if not _ai._ensure_litellm_loaded():
            return {"success": False, "message": "llm_err_litellm_not_installed"}

        try:
            test_config = {
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                **kwargs,
            }

            litellm_model = f"{provider}/{model}" if provider else model
            supports_reasoning = _ai._check_reasoning_support(litellm_model)

            request_params = LiteLLMClient._build_litellm_params(
                test_config,
                [{"role": "user", "content": "Hi"}],
                max_tokens=1,
                timeout=DEFAULT_VERIFY_TIMEOUT,
            )

            from utils.proxy_manager import ProxyManager

            with ProxyManager.litellm_env_context():
                response = await _ai.acompletion(**request_params)

            result = {"success": True, "message": "Connection successful"}

            if hasattr(response, "usage") and response.usage:  # type: ignore[union-attr]
                result["usage"] = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),  # type: ignore[union-attr]
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),  # type: ignore[union-attr]
                    "total_tokens": getattr(response.usage, "total_tokens", 0),  # type: ignore[union-attr]
                }

            if supports_reasoning:
                result["reasoning_supported"] = True

            return result

        except Exception as e:
            error_info = log_classified(
                logger,
                e,
                "llm",
                "[AIService] TestConn | Test connection failed (%s): %s",
                exc_info=True,
            )
            return {
                "success": False,
                "message": error_info["message_key"],
                "error_code": error_info["code"],
            }
