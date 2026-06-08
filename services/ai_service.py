import asyncio
import config
import contextlib
import json
import logging
import os
import re
import threading
import time

import httpx
import pandas as pd

from core.i18n import I18n
from services.local_model_manager import LocalModelManager, LocalInferenceTimeoutError
from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

LITELLM_AVAILABLE = True

VALID_RECOMMENDATIONS = {"buy", "hold", "sell", "strong_buy", "strong_sell", "neutral"}
STRATEGY_CONTEXT_MAX_LEN = 1600

_AVAILABLE_DATA_LABEL_KEYS: set[str] = {
    "ai_label_quote_snapshot",
    "ai_label_tech",
    "ai_label_global",
    "ai_label_news",
    "ai_label_kline",
    "ai_label_learning",
    "ai_label_strategy_ctx",
    "ai_label_valuation",
    "ai_label_macro",
    "ai_label_roe_trend",
    "ai_label_gross_margin_trend",
    "ai_label_revenue_growth_trend",
    "ai_label_profit_growth_trend",
    "ai_label_cf_profit_ratio",
    "ai_label_goodwill_ratio",
    "ai_label_monetary_capital",
    "ai_label_accounts_receiv",
    "ai_label_audit",
    "ai_label_main_business",
    "ai_label_dividend",
    "ai_label_pledge",
    "ai_label_top_holder",
    "ai_label_holder_count",
    "ai_label_main_flow",
    "ai_label_top_list",
    "ai_label_northbound",
}

AVAILABLE_DATA_LABELS: frozenset[str] = frozenset(_AVAILABLE_DATA_LABEL_KEYS)


def build_available_data_block(labels: list[str]) -> str:
    """Render <available_data> block from label key strings.

    Design decision (deviates from issue #41 spec v5 §2.2):
    The spec defines AVAILABLE_DATA_LABELS as translated strings
    ``{I18n.get(k) for k in _AVAILABLE_DATA_LABEL_KEYS}``, but the
    actual pipeline uses **key strings** throughout (ai_mixin →
    ai_service → this function) and only translates at render time.
    This is intentionally better because:
    1. Keys are locale-independent — tests compare keys vs keys.
    2. Translation happens once at render, avoiding stale cached
       translations if locale ever changes at runtime.
    Do NOT change AVAILABLE_DATA_LABELS to translated strings unless
    the entire pipeline is updated accordingly.
    """
    if not labels:
        return ""

    header = I18n.get("ai_available_data_header")
    items = []
    for label_key in labels:
        if label_key not in _AVAILABLE_DATA_LABEL_KEYS:
            logger.warning("[AIService] Unknown label key '%s' not in AVAILABLE_DATA_LABELS, skipping", label_key)
            continue
        display_text = I18n.get(label_key)
        items.append(f"- {display_text}")
    if not items:
        return ""
    return f"<available_data>\n{header}\n" + "\n".join(items) + "\n</available_data>"


class AIServiceUnavailableError(Exception):
    """P1-12: 所有 LLM 供应商都不可用时抛出"""

    pass


def validate_ai_analysis_response(response: dict) -> dict:
    if not isinstance(response, dict):
        return {"error": "Invalid response type", "score": 0}

    score = response.get("score")
    if score is not None:
        try:
            score = float(score)
            if not (0 <= score <= 100):
                logger.warning("[AIService] Output validation: score out of range [0,100]: %s", score)
                score = max(0, min(100, score))
            response["score"] = score
        except (ValueError, TypeError):
            logger.warning("[AIService] Output validation: invalid score type: %s", score)
            response["score"] = 0

    recommendation = response.get("recommendation")
    if recommendation is not None:
        rec_lower = str(recommendation).lower().strip()
        if rec_lower not in VALID_RECOMMENDATIONS:
            logger.warning("[AIService] Output validation: unexpected recommendation: %s", recommendation)
            response["recommendation"] = "neutral"
        else:
            response["recommendation"] = rec_lower

    return response


try:
    import litellm  # type: ignore[import-untyped]
    from litellm import acompletion  # type: ignore[import-untyped]

    litellm.suppress_debug_info = True
    litellm.set_verbose = False  # type: ignore[reportPrivateImportUsage]  # LiteLLM private API usage for logging suppression

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("[AIService] LiteLLM not installed, cloud LLM features disabled")

# Import litellm exceptions separately — they may not exist in older versions or mock environments
_LITELLM_EXCEPTIONS_AVAILABLE = False
if LITELLM_AVAILABLE:
    try:
        from litellm.exceptions import (  # type: ignore[import-untyped]
            AuthenticationError as LitellmAuthenticationError,
            ContentPolicyViolationError as LitellmContentPolicyViolationError,
            InternalServerError as LitellmInternalServerError,
            RateLimitError as LitellmRateLimitError,
            ServiceUnavailableError as LitellmServiceUnavailableError,
        )

        _LITELLM_EXCEPTIONS_AVAILABLE = True
    except ImportError:
        pass


def _check_reasoning_support(model: str) -> bool:
    """检查模型是否支持推理增强 (reasoning_content)"""
    if not LITELLM_AVAILABLE:
        return False
    try:
        return litellm.utils.supports_reasoning(model=model)
    except Exception as exc:
        logger.debug(
            "[AIService] supports_reasoning check failed for %s: %s, using LLM_PROVIDERS fallback",
            model,
            DataSanitizer.sanitize_error(exc),
        )
        from utils.llm_providers import LLM_PROVIDERS

        # Derive reasoning model IDs from LLM_PROVIDERS tags
        for provider_config in LLM_PROVIDERS.values():
            for m in provider_config.get("models", []):
                tag = m.get("tag", "")
                tags = tag if isinstance(tag, list) else [tag]
                if "reasoning" in tags:
                    # Bidirectional substring match: "qwen3.6-max" matches "qwen3.6-max-preview"
                    model_lower = model.lower()
                    model_id_lower = m["id"].lower()
                    if model_lower in model_id_lower or model_id_lower in model_lower:
                        return True
        return False


def _classify_api_error(e: Exception) -> dict:
    """
    Classify API errors into structured error info with i18n keys.

    Returns:
        {"code": str, "message_key": str} where message_key can be
        translated via I18n.get() or get_error_message() in the UI layer.
    """
    from utils.error_classifier import classify_error

    return classify_error(e, context="llm")


from utils.singleton_registry import register_singleton


@register_singleton
class AIService:
    """
    AI Service - 基于 LiteLLM 1.82+ 的统一 LLM 网关

    设计原则:
    1. Cloud Provider: 使用 LiteLLM 统一调用各厂商 API
    2. Local Provider: 绝对隔离，不经过 LiteLLM，直接调用 LocalModelManager
    3. 状态机管理: 使用 _is_cloud_configured 替代 self.client
    4. 异步安全: 使用懒加载动态锁，避免跨事件循环崩溃

    LiteLLM 1.82+ 特性利用:
    - reasoning_content 标准化提取
    - stream_options 获取 usage 统计
    - supports_reasoning 模型能力检测
    - drop_params 自动丢弃不支持的参数

    重要: 异步锁必须在运行时动态创建，绑定到当前事件循环
    禁止在类级别或 __init__ 中直接创建 asyncio.Lock/Semaphore
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    @classmethod
    def _reset_singleton(cls):
        """Reset singleton for testing only. NEVER call in production."""
        with cls._lock:
            cls._instance = None
            cls._initialized = False

    def __init__(self):
        if self._initialized:
            return

        self._is_cloud_configured = False
        self._litellm_config = {}
        self._local_model_loaded = False
        self._supports_reasoning = False
        self._failover_credentials: dict[str, dict] = {}

        self._configure_litellm()
        self._setup_client()
        self._cleanup_prompt_dumps()

        self._initialized = True

    @staticmethod
    def _get_prompt_dump_dir() -> str:
        return os.path.join(config.APP_ROOT, "logs", "ai_prompts")

    def _cleanup_prompt_dumps(self) -> None:
        """Cleanup old prompt dump files; run outside analyze hot path."""
        if not ConfigHandler.get_setting("ai_prompt_dump_enabled", False):
            return
        try:
            dump_dir = self._get_prompt_dump_dir()
            if not os.path.isdir(dump_dir):
                return
            cutoff_ts = time.time() - 24 * 60 * 60
            for name in os.listdir(dump_dir):
                file_path = os.path.join(dump_dir, name)
                if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff_ts:
                    with contextlib.suppress(OSError):
                        os.remove(file_path)
        except Exception as e:
            logger.debug("[AIService] Prompt dump cleanup skipped: %s", DataSanitizer.sanitize_error(e))

    def _configure_litellm(self):
        """配置 LiteLLM 全局参数 (1.82+ 优化)"""
        if not LITELLM_AVAILABLE:
            return

        litellm.set_verbose = False  # type: ignore[reportPrivateImportUsage]  # LiteLLM private API usage for logging suppression
        litellm.drop_params = True
        litellm.set_timeout = 30.0  # type: ignore[attr-defined]
        litellm.max_retries = 2  # type: ignore[attr-defined]
        litellm.success_callback = []
        litellm.failure_callback = []
        litellm.modify_params = True

        logger.debug("[AIService] LiteLLM 1.82+ configured")

    def _setup_client(self):
        """
        配置云端 LLM (LiteLLM 版本)

        重要: LiteLLM 是函数式调用，没有持久化的 Client 实例
        这里缓存配置供后续调用使用
        """
        if not LITELLM_AVAILABLE:
            logger.warning("[AIService] Config | ⚠️ LiteLLM not available. Cloud features disabled.")
            self._is_cloud_configured = False
            return

        llm_config = ConfigHandler.get_llm_config()

        api_key = llm_config.get("api_key")
        if not api_key:
            logger.warning("[AIService] Config | ⚠️ API Key not found. Cloud features disabled.")
            self._is_cloud_configured = False
            return

        provider = llm_config.get("provider", "")
        base_url = llm_config.get("base_url", "")

        if provider == "azure":
            resource_name = llm_config.get("azure_resource_name", "")
            deployment_name = llm_config.get("azure_deployment_name", "")
            if not resource_name:
                logger.warning("[AIService] Config | ⚠️ Azure resource name not found. Cloud features disabled.")
                self._is_cloud_configured = False
                return
            if not deployment_name:
                logger.warning("[AIService] Config | ⚠️ Azure deployment name not found. Cloud features disabled.")
                self._is_cloud_configured = False
                return
            base_url = f"https://{resource_name}.openai.azure.com"
            llm_config["base_url"] = base_url
            llm_config["model"] = deployment_name
        elif not base_url:
            logger.error("[AIService] Config | ❌ 'base_url' is mandatory for cloud LLM.")
            self._is_cloud_configured = False
            return

        self._litellm_config = llm_config
        self._is_cloud_configured = True

        model_id = llm_config.get("model", "")
        provider = llm_config.get("provider", "")
        litellm_model = f"{provider}/{model_id}" if provider else model_id
        self._supports_reasoning = _check_reasoning_support(litellm_model)

        # Pre-load failover credentials to avoid keyring calls on hot path
        self._failover_credentials = {}
        try:
            failover_config = ConfigHandler.get_failover_config()
            for model_str in failover_config.get("fallbacks", []):
                if "/" in model_str:
                    fb_provider = model_str.split("/")[0]
                    if fb_provider not in self._failover_credentials:
                        self._failover_credentials[fb_provider] = ConfigHandler.get_llm_config_for_provider(fb_provider)
        except Exception as e:
            logger.debug("[AIService] Failover credential pre-load skipped: %s", DataSanitizer.sanitize_error(e))

        logger.info(
            "[AIService] Init | Cloud client ready. provider=%s, reasoning=%s",
            provider,
            self._supports_reasoning,
        )

    def is_cloud_available(self) -> bool:
        """检查云端 LLM 是否可用 (替代 if not self.client)"""
        return self._is_cloud_configured and bool(self._litellm_config.get("api_key"))

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
                ) or ConfigHandler.get_llm_config_for_provider(override_provider)
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

        timeout_val = kwargs.get("timeout", 30.0)
        request_params["timeout"] = httpx.Timeout(timeout_val, connect=5.0)

        return request_params

    def _get_analysis_semaphore(self):
        """股票分析云端 LLM 调用信号量（loop-local，热生效）。"""

        def _factory():
            raw_val = ConfigHandler.get_ai_max_concurrent_analysis()
            concurrency = max(1, int(raw_val)) if raw_val else 5
            return asyncio.Semaphore(concurrency)

        return get_loop_local("ai_analysis_semaphore", _factory)

    def _get_news_semaphore(self):
        """新闻分类云端兜底信号量（loop-local，热生效）。"""

        def _factory():
            raw_val = ConfigHandler.get_ai_news_max_concurrent()
            concurrency = max(1, int(raw_val)) if raw_val else 1
            return asyncio.Semaphore(concurrency)

        return get_loop_local("ai_news_semaphore", _factory)

    def _safe_truncate(self, text: str, max_len: int) -> str:
        """Safely truncate text to avoid token overflow"""
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[:max_len] + "...(truncated)"

    async def reload_config(self):
        """Reload config when settings change"""
        self._setup_client()
        self._local_model_loaded = False
        # M-4: _cleanup_prompt_dumps moved out of hot path; only runs at init
        from utils.loop_local import del_loop_local

        del_loop_local("ai_analysis_semaphore")
        del_loop_local("ai_news_semaphore")

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
        llm_config = self._litellm_config
        request_params = self._build_litellm_params(
            llm_config,
            messages,
            model_override=model_override,
            failover_credentials=self._failover_credentials,
            **kwargs,
        )

        total_chars = sum(len(m.get("content", "")) for m in messages)
        estimated_tokens = total_chars // 3
        if estimated_tokens > 80000:
            logger.warning(
                "[AIService] Cloud | Prompt may exceed context window: ~%d tokens (%d chars)",
                estimated_tokens,
                total_chars,
            )

        # S1-4 fix: Real-time reasoning support check for model switching
        if model_override:
            effective_model = model_override
        else:
            _provider = llm_config.get("provider", "")
            _model_id = llm_config.get("model", "")
            effective_model = f"{_provider}/{_model_id}" if _provider else _model_id
        supports_reasoning = _check_reasoning_support(effective_model)

        from utils.proxy_manager import ProxyManager

        stream = kwargs.get("stream", False) or on_chunk is not None

        with ProxyManager.litellm_env_context():
            if stream:
                if supports_reasoning:
                    request_params["stream_options"] = {"include_usage": True}

                response = await acompletion(stream=True, **request_params)
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
                        stream_err,
                    )

                try:
                    _flush_content_buf()
                    _flush_reasoning_buf()
                except Exception as flush_err:
                    logger.debug("[AIService] Failed to flush chunk buffer after stream: %s", flush_err)

                if not response_content and reasoning_content:
                    response_content = reasoning_content

                result = {"content": response_content}
                if reasoning_content:
                    result["reasoning_content"] = reasoning_content
                if usage:
                    result["usage"] = usage

                return result
            else:
                response = await acompletion(**request_params)
                content = response.choices[0].message.content  # type: ignore[union-attr]
                result = {"content": content}

                if hasattr(response, "usage") and response.usage:  # type: ignore[union-attr]
                    result["usage"] = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),  # type: ignore[union-attr]
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0),  # type: ignore[union-attr]
                        "total_tokens": getattr(response.usage, "total_tokens", 0),  # type: ignore[union-attr]
                    }

                return result

    async def _chat_completion(
        self,
        messages: list,
        model: str | None = None,
        provider: str = "cloud",
        temperature: float = 0.3,
        timeout: float = 30.0,
        json_mode: bool = True,
        on_chunk=None,
        purpose: str = "analysis",
        local_max_tokens: int = 256,
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
        response_content = ""

        # --- Local Provider ---
        if provider == "local":
            await self._setup_local_model()
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
            if not self.is_cloud_available():
                raise ValueError("Cloud LLM not configured. Please set up API Key.")

            sem = self._get_news_semaphore() if purpose == "news" else self._get_analysis_semaphore()
            async with sem:
                logger.debug(
                    "[AIService] Cloud | Invoking LiteLLM (%d messages)",
                    len(messages),
                )

                result = await self._chat_completion_litellm(
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
                logger.debug("[AIService] JSON heuristic extraction failed: %s", DataSanitizer.sanitize_error(e))

            raise ValueError(f"Invalid JSON response: {DataSanitizer.sanitize_error(response_content[:100])}...")

        return {"content": response_content}

    async def _chat_completion_with_failover(
        self,
        messages: list,
        timeout: float = 120.0,
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
        from utils.config_handler import ConfigHandler

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

                result = await self._chat_completion(
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

                is_transient = False

                if _LITELLM_EXCEPTIONS_AVAILABLE:
                    if isinstance(e, LitellmAuthenticationError):
                        logger.error(
                            "[AIService] Failover | ❌ Authentication error for %s, not retrying",
                            model,
                        )
                        raise
                    if isinstance(e, LitellmContentPolicyViolationError):
                        logger.error(
                            "[AIService] Failover | ❌ Content policy violation for %s, not retrying",
                            model,
                        )
                        raise

                    is_transient = isinstance(
                        e,
                        (
                            LitellmRateLimitError,
                            LitellmServiceUnavailableError,
                            LitellmInternalServerError,
                        ),
                    )

                is_transient = is_transient or isinstance(
                    e,
                    (
                        TimeoutError,
                        httpx.TimeoutException,
                        httpx.ConnectError,
                        httpx.ReadError,
                        ConnectionError,
                        OSError,
                    ),
                )

                if is_transient:
                    # Truncate before sanitizing to avoid breaking sanitization markers
                    raw_msg = str(e)
                    truncated_raw = raw_msg[:100] if len(raw_msg) > 100 else raw_msg
                    logger.warning(
                        "[AIService] Failover | ⚠️ %s failed (%s: %s)",
                        model,
                        error_type,
                        DataSanitizer.sanitize_error(truncated_raw),
                    )
                    continue
                else:
                    logger.error(
                        "[AIService] Failover | ❌ Non-transient error for %s: %s",
                        model,
                        error_type,
                    )
                    raise

        all_models_tried = ", ".join(m for m in models_to_try if m)
        raise AIServiceUnavailableError(f"All LLM providers failed. Tried: [{all_models_tried}]") from last_error

    @log_async_operation(
        operation_name="analyze_stock",
        log_args=False,
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def analyze_stock(
        self,
        stock_info: dict,
        tech_info: dict,
        news_list: list,
        global_context="",
        strategy_context: str = "",
        capital_flow_text: str = "",
        financials_text: str = "",
        history_text: str = "",
        on_chunk=None,
        history_context: str | None = None,
        strategy_key: str | None = None,
        include_global_context: bool = True,
        include_learning_context: bool = True,
        ui_prompt_override: str | None = None,
        is_backtest: bool = False,
        *,
        financial_labels: list[str] | None = None,
        capital_labels: list[str] | None = None,
        history_labels: list[str] | None = None,
    ) -> dict | None:
        """
        Analyze a single stock using the LLM (Cloud default, can support others).
        Requires 'llm_model' to be configured.

        ⚠️ Backtest safety: When called in a backtest context, ``history_context``
        MUST be pre-fetched via ``AIStrategyMixin.run_ai_analysis()`` so that the
        learning context is filtered by the correct ``as_of`` date.  Calling this
        method directly with ``history_context=None`` in a backtest will use the
        current date as the ``as_of`` cutoff, which may introduce look-ahead bias.
        """
        if not self.is_cloud_available():
            return None

        # Build Prompt
        from core.i18n import I18n

        # Format news
        news_text = "\n".join(
            [f"- [{n.get('source', '')}] {n.get('publish_time', '')[:10]} {n.get('title', '')}" for n in news_list[:5]],
        )
        if not news_list:
            news_text = "No recent news found."

        # Process Concepts (Used cached if available)
        try:
            # Check if concepts are already injected by Strategy (Preferred)
            injected_concepts = stock_info.get("concepts")

            if injected_concepts and isinstance(injected_concepts, list) and len(injected_concepts) > 0:
                # Use injected
                concepts_str = ", ".join(injected_concepts[:8])
                stock_info["concepts"] = concepts_str
            elif isinstance(injected_concepts, list) and len(injected_concepts) == 0:
                # If it's literally an empty list `[]`, nuke the key entirely so it doesn't appear in XML
                stock_info.pop("concepts", None)
            elif not injected_concepts:
                # If it's None or empty string, remove it entirely
                stock_info.pop("concepts", None)

        except Exception as e:
            logger.warning("[AIService] Analyze | Concepts processing failed: %s", DataSanitizer.sanitize_error(e))
            stock_info.pop("concepts", None)

        # Convert dicts to XML-like string, filtering out Pandas artifacts and private injected keys like `_23` or `_rsi_period`
        def is_valid_value(val):
            if isinstance(val, list) and len(val) == 0:
                return False
            try:
                # pandas isna throws ValueError on multi-element numpy arrays
                if pd.isna(val):
                    return False
            except ValueError:
                pass
            return True

        clean_stock_info = {k: v for k, v in stock_info.items() if not str(k).startswith("_") and is_valid_value(v)}

        stock_xml = "\n".join([f"  {k}: {v}" for k, v in clean_stock_info.items()])

        # Fetch Learning Context (Few-Shot) — skip if caller pre-fetched
        if history_context is None and include_learning_context:
            if is_backtest:
                raise ValueError(
                    "analyze_stock called with history_context=None in backtest mode. "
                    "Learning context must be pre-fetched via AIStrategyMixin.run_ai_analysis() "
                    "to prevent look-ahead bias."
                )
            try:
                import datetime

                from data.constants import SAFE_LIVE_LEARNING_OFFSET_DAYS
                from data.persistence.review_manager import ReviewManager
                from utils.time_utils import get_now

                rm = ReviewManager()
                safe_as_of = get_now().date() - datetime.timedelta(days=SAFE_LIVE_LEARNING_OFFSET_DAYS)
                history_context = await rm.get_learning_context(as_of=safe_as_of)
            except Exception as e:
                logger.warning(
                    f"[AIService] Analyze | ⚠️ Learning context fetch failed: {DataSanitizer.sanitize_error(e)}",
                )
                history_context = ""
        elif history_context is None:
            history_context = ""

        # Load System Prompt
        from strategies.strategy_prompts import _UNIVERSAL_RULES, get_base_prompt
        from utils.prompt_guard import validate_prompt, sanitize_prompt

        if ui_prompt_override and ui_prompt_override.strip():
            raw_prompt = ui_prompt_override.strip()
            is_valid, warning = validate_prompt(raw_prompt)
            if not is_valid:
                logger.warning("[AIService] Prompt override rejected: %s", warning)
                sanitized_override = None
                if strategy_key:
                    base_prompt = get_base_prompt(strategy_key)
                else:
                    base_prompt = ConfigHandler.get_ai_system_prompt() or ""
            else:
                sanitized_override = sanitize_prompt(raw_prompt)
                base_prompt = (
                    get_base_prompt(strategy_key) if strategy_key else ConfigHandler.get_ai_system_prompt() or ""
                )
        elif strategy_key:
            base_prompt = get_base_prompt(strategy_key)
            sanitized_override = None
        else:
            base_prompt = ConfigHandler.get_ai_system_prompt() or ""
            sanitized_override = None

        # Capital flow, financials, and history: use real data or fallback
        _capital_flow_sentinel = I18n.get("ai_capital_flow_fetch_failed")
        capital_flow_content = (
            capital_flow_text
            if capital_flow_text and capital_flow_text != _capital_flow_sentinel
            else "(Data not available yet, assume neutral)"
        )
        _financial_sentinels = {I18n.get("ai_financial_insufficient"), I18n.get("ai_financial_fetch_failed")}
        financials_content = (
            financials_text
            if financials_text and financials_text not in _financial_sentinels
            else "(Data not available yet, assume neutral)"
        )
        _history_sentinels = {I18n.get("ai_history_insufficient"), I18n.get("ai_history_extract_error")}
        history_content = history_text if history_text and history_text not in _history_sentinels else ""

        # 倒金字塔结构：核心策略指令置于最末尾，贴近生成区
        # 解决 "Lost in the Middle" 注意力衰减问题
        user_prompt_parts = []

        # 1. 基础信息 (Top - 锚定分析实体)
        user_prompt_parts.append(f"<stock_info>\n{stock_xml}\n</stock_info>")

        # 1.5 可用数据清单 (运行时注入，与各块同一入选条件派生)
        labels: list[str] = []
        if stock_xml:
            labels.append("ai_label_quote_snapshot")
        if tech_info:
            labels.append("ai_label_tech")
        if global_context and include_global_context:
            labels.append("ai_label_global")
        if news_text and news_text != "No recent news found.":
            labels.append("ai_label_news")

        # 2. 技术指标 (重要参考)
        user_prompt_parts.append(
            f"<technical_indicators>\n{json.dumps(tech_info, ensure_ascii=False, indent=2, default=str)}\n</technical_indicators>"
        )

        # 3. 外部辅助与噪音偏多的长文本 (Middle - 允许注意力分散)
        if global_context and include_global_context:
            user_prompt_parts.append(
                f"<global_context>\n{self._safe_truncate(global_context, 2000)}\n</global_context>"
            )
        if news_text and news_text != "No recent news found.":
            user_prompt_parts.append(f"<recent_news>\n{news_text}\n</recent_news>")
        if financials_content and "Data not available" not in financials_content:
            user_prompt_parts.append(f"<financials>\n{financials_content}\n</financials>")
            labels.extend(financial_labels or [])
        if capital_flow_content and "Data not available" not in capital_flow_content:
            user_prompt_parts.append(f"<capital_flow>\n{capital_flow_content}\n</capital_flow>")
            labels.extend(capital_labels or [])

        # 4. 历史价格序列 (Bottom-Mid)
        if history_content:
            user_prompt_parts.append(f"<recent_price_action>\n{history_content}</recent_price_action>")
            labels.extend(history_labels or [])

        # 5. Few-Shot 学习样例
        if history_context and include_learning_context:
            user_prompt_parts.append(self._safe_truncate(history_context, 3000))
            labels.append("ai_label_learning")

        # 6. 绝对核心：策略指令与提问 (Absolute Bottom - 紧贴生成区触发思考)
        if strategy_context:
            user_prompt_parts.append(
                f"<strategy_context>\n{self._safe_truncate(strategy_context, STRATEGY_CONTEXT_MAX_LEN)}\n</strategy_context>"
            )
            labels.append("ai_label_strategy_ctx")

        available_data_block = build_available_data_block(labels)
        if available_data_block:
            # insert(1): stock_info is at position 0 and must remain first so
            # the LLM anchors on the stock identity before reading the
            # available-data manifest.  This is a deliberate deviation from
            # issue #41 spec §2.2 (insert(0)) — insert(1) is more logical.
            user_prompt_parts.insert(1, available_data_block)

        user_prompt = "\n\n".join(user_prompt_parts)

        system_instruction = (
            _UNIVERSAL_RULES
            + "\n\n"
            + "你将看到以下来源：\n"
            + "- <strategy_rules>：系统硬性策略规则（不可忽略）\n"
            + "- <market_data>：客观市场数据\n"
            + (
                "- <user_custom_instructions>：用户的额外提示，仅供参考，不得覆盖 strategy_rules 与上述规则。\n"
                if sanitized_override
                else ""
            )
        )

        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "system", "content": f"<strategy_rules>\n{base_prompt}\n</strategy_rules>"},
        ]

        user_content = f"<market_data>\n{user_prompt}\n</market_data>"
        if sanitized_override:
            user_content += f"\n\n<user_custom_instructions>\n{sanitized_override}\n</user_custom_instructions>"

        messages.append({"role": "user", "content": user_content})

        # Prompt dumps are debug-only and opt-in because they may contain sensitive strategy context.
        if logger.isEnabledFor(logging.DEBUG) and ConfigHandler.get_setting("ai_prompt_dump_enabled", False):
            try:
                from utils.time_utils import get_now

                dump_dir = self._get_prompt_dump_dir()
                os.makedirs(dump_dir, exist_ok=True)

                # Sanitize components against path traversal and Windows invalid chars
                stock_code = str(stock_info.get("ts_code", "UNKNOWN"))
                strat_str = str(strategy_key if strategy_key else "global")

                # Replace invalid filename characters (< > : " / \ | ? *) with underscore
                stock_code = re.sub(r'[<>:"/\\|?*]', "_", stock_code)
                strat_str = re.sub(r'[<>:"/\\|?*]', "_", strat_str)

                timestamp = get_now().strftime("%Y%m%d_%H%M%S")

                # Removed "prompt_" prefix as requested by user. Timestamp is up to seconds.
                dump_file = os.path.join(
                    dump_dir,
                    f"{strat_str}_{stock_code}_{timestamp}.md",
                )

                with open(dump_file, "w", encoding="utf-8") as f:
                    f.write(f"# Universal Rules (System)\n```text\n{_UNIVERSAL_RULES}\n```\n\n")
                    f.write(f"# Strategy Prompt (System)\n```text\n{base_prompt}\n```\n\n")
                    f.write(f"# User Prompt\n```xml\n{user_prompt}\n```\n")

                logger.debug(
                    "[AIService] Analyze | Prepared LLM Context. Full payload saved to: %s",
                    dump_file,
                )
            except Exception as e:
                logger.debug(
                    f"[AIService] Analyze | Failed to dump prompt to file: {e}",
                )

        try:
            # P1-12: Analyze Stock uses Cloud with failover by default
            res = await self._chat_completion_with_failover(
                messages,
                timeout=120.0,
                json_mode=True,
                on_chunk=on_chunk,
            )
            return validate_ai_analysis_response(res)

        except AIServiceUnavailableError as ae:
            logger.error("[AIService] Analyze | ❌ All providers failed: %s", ae)
            logger.debug("[AIService] Analyze | All providers failed traceback:", exc_info=True)
            return {"error": "All LLM providers unavailable", "score": 0}
        except (TimeoutError, httpx.TimeoutException) as te:
            logger.error("[AIService] Analyze | ❌ Timeout (120s exceeded): %s", type(te).__name__)
            logger.debug("[AIService] Analyze | Timeout traceback:", exc_info=True)
            return {"error": "Analysis timeout", "score": 0}
        except LocalInferenceTimeoutError as lite:
            logger.error(
                f"[AIService] Analyze | ❌ Local model inference timeout: {lite}",
                exc_info=False,
            )
            return {"error": "Local model timeout", "score": 0}
        except Exception as e:
            logger.error("[AIService] Analyze | ❌ Top-level failure: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[AIService] Analyze | Top-level failure traceback:", exc_info=True)
            return {"error": DataSanitizer.sanitize_error(e), "score": 0}

    async def _get_setup_lock(self):
        """Lazy-initialize the async lock dynamically per event loop to avoid cross-loop binding deadlocks."""

        def _factory():
            return asyncio.Lock()

        return get_loop_local("ai_setup_lock", _factory)

    async def _setup_local_model(self):
        """
        Ensure local model is initialized via Manager.
        """
        lock = await self._get_setup_lock()
        async with lock:
            manager = await LocalModelManager.get_instance()

            # Ensure model is verified/loaded using config path
            config_path = ConfigHandler.get_setting("local_model_path")
            if config_path and not manager.get_loaded_model_path():
                await manager.load_model(config_path)

    def _parse_news_result(self, raw_result: dict) -> dict:
        """
        Helper to normalize news classification result.
        Handles the L1/L2 category logic to provide a clean 'category' string for UI.
        L1/L2 codes are English enum values returned by the AI prompt,
        translated to locale-specific display names via I18n.
        """
        from core.i18n import I18n

        l1_code = raw_result.get("category_L1", "")
        l2_code = raw_result.get("category_L2", "")

        l1_display = I18n.get(f"news_l1_{l1_code}", l1_code) if l1_code else ""
        l2_display = I18n.get(f"news_l2_{l2_code}", l2_code) if l2_code else ""

        if l2_display and l1_display:
            final_category = f"{l1_display}-{l2_display}"
        elif l2_display:
            final_category = l2_display
        elif l1_display:
            final_category = l1_display
        else:
            final_category = ""

        raw_result["category"] = final_category
        if "emoji" not in raw_result:
            raw_result["emoji"] = "📰"
        if "sentiment" not in raw_result:
            raw_result["sentiment"] = "Neutral"

        return raw_result

    @log_async_operation(
        operation_name="classify_news",
        threshold_ms=PerfThreshold.AI_INFERENCE,
    )
    async def classify_news(self, text: str) -> dict:
        """
        Classify news text using Local LLM (Preferred) or Cloud LLM (Fallback).
        """
        system_instruction = ConfigHandler.get_ai_news_prompt()
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": text[:500]},
        ]

        # 1. Try Local Model
        try:
            raw_result = await self._chat_completion(
                messages,
                provider="local",
                json_mode=True,
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                f"[AIService] Classify | Local ✅ {result.get('category')} / {result.get('sentiment')}",
            )
            return result
        except Exception as local_e:
            # Local failed (not configured, crash, etc.)
            # Log only if it wasn't just "not configured" (which is common)
            if "not installed" not in str(local_e) and "not configured" not in str(
                local_e,
            ):
                logger.warning(
                    "[AIService] Classify | Local failed, falling back to cloud: %s",
                    DataSanitizer.sanitize_error(local_e),
                )
            else:
                logger.warning(
                    "[AIService] Classify | Local model unavailable, falling back to cloud: %s",
                    DataSanitizer.sanitize_error(local_e),
                )

        # 2. Fallback to Cloud
        try:
            # Enforce global 5s timeout? The original code had per-call timeout.
            # _chat_completion has default 30s. classify used to wrap in wait_for 30s.
            # Inner cloud call had 30s timeout on client.
            # We will use 30s default.
            raw_result = await self._chat_completion(
                messages,
                provider="cloud",
                json_mode=True,
                purpose="news",
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                "[AIService] Classify | Cloud OK: %s / %s",
                result.get("category"),
                result.get("sentiment"),
            )
            return result
        except Exception as e:
            logger.error("[AIService] Classify | ❌ All providers failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[AIService] Classify | All providers failed traceback:", exc_info=True)
            return {"category": "unknown", "sentiment": "neutral", "error": DataSanitizer.sanitize_error(e)}

    async def verify_connection(self) -> bool:
        """
        Verify API connection by sending a minimal request.
        """
        if not self.is_cloud_available():
            return False

        try:
            await self._chat_completion_litellm(
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=1,
                timeout=10.0,
            )
            return True
        except Exception as e:
            logger.error("[AIService] Verify | ❌ Connection verification failed: %s", DataSanitizer.sanitize_error(e))
            logger.debug("[AIService] Verify | Connection verification traceback:", exc_info=True)
            raise

    @staticmethod
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
            return {"success": False, "message": "API Key is empty"}

        if not model:
            return {"success": False, "message": "Model ID is empty"}

        if not LITELLM_AVAILABLE:
            return {"success": False, "message": "LiteLLM not installed"}

        try:
            test_config = {
                "provider": provider,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
                **kwargs,
            }

            litellm_model = f"{provider}/{model}" if provider else model
            supports_reasoning = _check_reasoning_support(litellm_model)

            request_params = AIService._build_litellm_params(
                test_config,
                [{"role": "user", "content": "Hi"}],
                max_tokens=1,
                timeout=10.0,
            )

            from utils.proxy_manager import ProxyManager

            with ProxyManager.litellm_env_context():
                response = await acompletion(**request_params)

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
            logger.error("[AIService] TestConn | Test connection failed: %s", DataSanitizer.sanitize_error(e))
            error_info = _classify_api_error(e)
            return {
                "success": False,
                "message": error_info["message_key"],
                "error_code": error_info["code"],
            }
