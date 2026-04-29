import asyncio
import contextlib
import json
import logging
import threading

import httpx

from services.local_model_manager import LocalModelManager
from utils.config_handler import ConfigHandler
from utils.loop_local import get_loop_local
from utils.log_decorators import PerfThreshold, log_async_operation

logger = logging.getLogger(__name__)

LITELLM_AVAILABLE = True

VALID_RECOMMENDATIONS = {"buy", "hold", "sell", "strong_buy", "strong_sell", "neutral"}
STRATEGY_CONTEXT_MAX_LEN = 1600


def validate_ai_analysis_response(response: dict) -> dict:
    if not isinstance(response, dict):
        return {"error": "Invalid response type", "score": 0}

    score = response.get("score")
    if score is not None:
        try:
            score = float(score)
            if not (0 <= score <= 100):
                logger.warning(f"[AIService] Output validation: score out of range [0,100]: {score}")
                score = max(0, min(100, score))
            response["score"] = score
        except (ValueError, TypeError):
            logger.warning(f"[AIService] Output validation: invalid score type: {score}")
            response["score"] = 0

    recommendation = response.get("recommendation")
    if recommendation is not None:
        rec_lower = str(recommendation).lower().strip()
        if rec_lower not in VALID_RECOMMENDATIONS:
            logger.warning(f"[AIService] Output validation: unexpected recommendation: {recommendation}")
            response["recommendation"] = "neutral"
        else:
            response["recommendation"] = rec_lower

    return response


try:
    import litellm  # pyright: ignore[reportMissingImports]
    from litellm import acompletion  # pyright: ignore[reportMissingImports]

    litellm.suppress_debug_info = True
    litellm.set_verbose = False  # type: ignore[reportPrivateImportUsage]

    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False
    logger.warning("[AIService] LiteLLM not installed, cloud LLM features disabled")


def _check_reasoning_support(model: str) -> bool:
    """检查模型是否支持推理增强 (reasoning_content)"""
    if not LITELLM_AVAILABLE:
        return False
    try:
        return litellm.utils.supports_reasoning(model=model)
    except Exception:
        logger.debug(f"[AIService] supports_reasoning check failed for {model}, using fallback list")
        reasoning_models = {
            "deepseek-reasoner",
            "deepseek-r1",
            "o1",
            "o1-mini",
            "o1-preview",
            "o3-mini",
            "claude-3.7-sonnet",
            "claude-4-opus",
            "claude-4-sonnet",
        }
        model_lower = model.lower()
        return any(rm in model_lower for rm in reasoning_models)


def _classify_api_error(e: Exception) -> dict:
    """
    Classify API errors into user-friendly i18n messages.

    Returns:
        {"code": str, "message": str} where message is translated i18n text
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

        self._configure_litellm()
        self._setup_client()

        self._initialized = True

    def _configure_litellm(self):
        """配置 LiteLLM 全局参数 (1.82+ 优化)"""
        if not LITELLM_AVAILABLE:
            return

        litellm.set_verbose = False  # type: ignore[reportPrivateImportUsage]
        litellm.drop_params = True
        litellm.set_timeout = 30.0
        litellm.max_retries = 2
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

        logger.info(
            f"[AIService] Init | ✅ Cloud client ready. provider={provider}, reasoning={self._supports_reasoning}"
        )

    def is_cloud_available(self) -> bool:
        """检查云端 LLM 是否可用 (替代 if not self.client)"""
        return self._is_cloud_configured and bool(self._litellm_config.get("api_key"))

    @staticmethod
    def _build_litellm_params(llm_config: dict, messages: list, **kwargs) -> dict:
        """
        构建 LiteLLM 请求参数 (静态方法，供 test_connection 复用)

        Azure 特殊处理:
        - base_url: https://{resource_name}.openai.azure.com (不含 deployments 路径)
        - model: azure/{deployment_name}
        - api_version: 作为独立参数传递
        """
        provider = llm_config.get("provider", "custom")
        model = llm_config.get("model", "")

        request_params = {
            "messages": messages,
            "api_key": llm_config.get("api_key"),
        }

        if provider == "azure":
            request_params["model"] = f"azure/{model}"
            azure_resource_name = llm_config.get("azure_resource_name", "")
            if azure_resource_name:
                request_params["api_base"] = f"https://{azure_resource_name}.openai.azure.com"
            else:
                request_params["api_base"] = llm_config.get("base_url", "")
            from utils.llm_providers import AZURE_DEFAULT_API_VERSION

            request_params["api_version"] = llm_config.get("api_version", AZURE_DEFAULT_API_VERSION)
        else:
            prefix_map = {
                "openai": "openai",
                "anthropic": "anthropic",
                "google": "gemini",
                "deepseek": "deepseek",
                "mistral": "mistral",
                "qwen": "openai",
                "zhipu": "openai",
                "moonshot": "openai",
                "minimax": "openai",
                "custom": "openai",
            }
            prefix = prefix_map.get(provider, "openai")
            request_params["model"] = f"{prefix}/{model}"
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

    async def _get_semaphore(self):
        """Get or create semaphore for current event loop"""

        def _factory():
            raw_val = ConfigHandler.get_ai_max_concurrent_analysis()
            concurrency = max(1, int(raw_val)) if raw_val else 5
            return asyncio.Semaphore(concurrency)

        return get_loop_local("ai_semaphore", _factory)

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
        # S1-1 fix: Reset semaphore so new concurrency limit takes effect
        from utils.loop_local import del_loop_local

        del_loop_local("ai_semaphore")

    async def _chat_completion_litellm(
        self,
        messages: list,
        on_chunk=None,
        **kwargs,
    ) -> dict:
        """
        LiteLLM 1.82+ 版本的云端调用

        Args:
            messages: 消息列表
            on_chunk: 流式回调函数 (content, is_reasoning)
            **kwargs: 其他参数

        Returns:
            {"content": str, "usage": dict, "reasoning_content": str}
        """
        llm_config = self._litellm_config
        request_params = self._build_litellm_params(llm_config, messages, **kwargs)

        # S1-4 fix: Real-time reasoning support check for model switching
        model_id = llm_config.get("model", "")
        provider = llm_config.get("provider", "")
        litellm_model = f"{provider}/{model_id}" if provider else model_id
        supports_reasoning = _check_reasoning_support(litellm_model)

        stream = kwargs.get("stream", False) or on_chunk is not None

        if stream:
            if supports_reasoning:
                request_params["stream_options"] = {"include_usage": True}

            response = await acompletion(stream=True, **request_params)
            response_content = ""
            reasoning_content = ""
            usage = None

            async for chunk in response:  # type: ignore[reportGeneralTypeIssues]
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
                            on_chunk(reasoning, True)

                if delta.content:
                    response_content += delta.content
                    if on_chunk:
                        on_chunk(delta.content, False)

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
            content = response.choices[0].message.content
            result = {"content": content}

            if hasattr(response, "usage") and response.usage:
                result["usage"] = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }

            return result

    async def _chat_completion(
        self,
        messages: list,
        model: str = None,  # type: ignore
        provider: str = "cloud",
        temperature: float = 0.3,
        timeout: float = 30.0,
        json_mode: bool = True,
        on_chunk=None,
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
                max_tokens=256,  # News classification only needs a small JSON (~60 tokens)
                temperature=temperature,
                system_prompt=system_prompt,
            )

        # --- Cloud Provider ---
        else:
            if not self.is_cloud_available():
                raise ValueError("Cloud LLM not configured. Please set up API Key.")

            async with await self._get_semaphore():
                logger.debug(
                    f"[AIService] Cloud | Invoking LiteLLM ({len(messages)} messages)",
                )

                result = await self._chat_completion_litellm(
                    messages,
                    on_chunk=on_chunk,
                    temperature=temperature,
                    timeout=timeout,
                    response_format={"type": "json_object"} if json_mode and not on_chunk else None,
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
                    # Use raw_decode to extract ONLY the first valid JSON object
                    # ignoring trailing garbage (like "Extra data")
                    try:
                        obj, idx = json.JSONDecoder().raw_decode(
                            response_content[start:],
                        )
                        return obj
                    except json.JSONDecodeError:
                        pass

                # 3. Fallback: Last Resort (rfind approach, but risky)
                end = response_content.rfind("}") + 1
                if end > start:
                    try:
                        json_str = response_content[start:end]
                        return json.loads(json_str)
                    except json.JSONDecodeError:
                        pass
            except Exception as e:
                logger.debug(f"[AIService] JSON heuristic extraction failed: {e}")

            raise ValueError(f"Invalid JSON response: {response_content[:100]}...")

        return {"content": response_content}

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
        history_context: str = None,  # type: ignore
        strategy_key: str = None,  # type: ignore
        include_global_context: bool = True,
        include_learning_context: bool = True,
        ui_prompt_override: str = None,  # type: ignore
    ) -> dict:
        """
        Analyze a single stock using the LLM (Cloud default, can support others).
        Requires 'llm_model' to be configured.
        """
        if not self.is_cloud_available():
            return None  # type: ignore

        # Build Prompt
        import pandas as pd

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
            logger.warning(f"[AIService] Analyze | ⚠️ Concepts processing failed: {e}")
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
            try:
                from data.persistence.review_manager import ReviewManager

                rm = ReviewManager()
                history_context = await rm.get_learning_context()
            except Exception as e:
                logger.warning(
                    f"[AIService] Analyze | ⚠️ Learning context fetch failed: {e}",
                )
                history_context = ""
        elif history_context is None:
            history_context = ""

        # Load System Prompt
        from strategies.strategy_prompts import _UNIVERSAL_RULES, resolve_prompt

        if ui_prompt_override and ui_prompt_override.strip():
            system_prompt = ui_prompt_override.strip()
            if _UNIVERSAL_RULES not in system_prompt:
                system_prompt += "\n\n" + _UNIVERSAL_RULES
        elif strategy_key:
            system_prompt = resolve_prompt(strategy_key)
        else:
            system_prompt = ConfigHandler.get_ai_system_prompt() or ""
            if _UNIVERSAL_RULES not in system_prompt:
                system_prompt += "\n\n" + _UNIVERSAL_RULES

        # Capital flow and financials: use real data or fallback
        capital_flow_content = capital_flow_text if capital_flow_text else "(Data not available yet, assume neutral)"
        financials_content = financials_text if financials_text else "(Data not available yet, assume neutral)"

        # 倒金字塔结构：核心策略指令置于最末尾，贴近生成区
        # 解决 "Lost in the Middle" 注意力衰减问题
        user_prompt_parts = []

        # 1. 基础信息 (Top - 锚定分析实体)
        user_prompt_parts.append(f"<stock_info>\n{stock_xml}\n</stock_info>")

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
        if capital_flow_content and "Data not available" not in capital_flow_content:
            user_prompt_parts.append(f"<capital_flow>\n{capital_flow_content}\n</capital_flow>")

        # 4. 历史价格序列 (Bottom-Mid)
        if history_text:
            user_prompt_parts.append(f"<recent_price_action>\n{history_text}\n</recent_price_action>")

        # 5. Few-Shot 学习样例
        if history_context and include_learning_context:
            user_prompt_parts.append(self._safe_truncate(history_context, 3000))

        # 6. 绝对核心：策略指令与提问 (Absolute Bottom - 紧贴生成区触发思考)
        if strategy_context:
            user_prompt_parts.append(
                f"<strategy_context>\n{self._safe_truncate(strategy_context, STRATEGY_CONTEXT_MAX_LEN)}\n</strategy_context>"
            )

        user_prompt = "\n\n".join(user_prompt_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        # Prompt dumps are debug-only and opt-in because they may contain sensitive strategy context.
        if logger.isEnabledFor(logging.DEBUG) and ConfigHandler.get_setting("ai_prompt_dump_enabled", False):
            try:
                import os
                import re
                import time

                import config
                from utils.time_utils import get_now

                dump_dir = os.path.join(config.APP_ROOT, "logs", "ai_prompts")
                os.makedirs(dump_dir, exist_ok=True)
                cutoff_ts = time.time() - 24 * 60 * 60
                for name in os.listdir(dump_dir):
                    file_path = os.path.join(dump_dir, name)
                    if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff_ts:
                        with contextlib.suppress(OSError):
                            os.remove(file_path)

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
                    f.write(f"# System Prompt\n```text\n{system_prompt}\n```\n\n")
                    f.write(f"# User Prompt\n```xml\n{user_prompt}\n```\n")

                logger.debug(
                    f"[AIService] Analyze | Prepared LLM Context. Full payload saved to: {dump_file}",
                )
            except Exception as e:
                logger.debug(
                    f"[AIService] Analyze | Failed to dump prompt to file: {e}",
                )

        try:
            # Analyze Stock uses Cloud by default as it requires high reasoning capability
            res = await self._chat_completion(
                messages,
                provider="cloud",
                timeout=120.0,
                json_mode=True,
                on_chunk=on_chunk,
            )
            return validate_ai_analysis_response(res)

        except TimeoutError:
            logger.error(
                "[AIService] Analyze | ❌ Timeout (120s exceeded)",
                exc_info=True,
            )
            return {"error": "Analysis timeout", "score": 0}
        except Exception as e:
            logger.error(
                f"[AIService] Analyze | ❌ Top-level failure: {e}",
                exc_info=True,
            )
            return {"error": str(e), "score": 0}

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
        """
        # We combine L1 and L2 for category to display "金融核心-贵金属"
        l1 = raw_result.get("category_L1", "")
        l2 = raw_result.get("category_L2", "")
        # Prefer L2 if available, or L1-L2 combo
        final_category = f"{l2}" if l2 else l1

        # Store structured data back
        raw_result["category"] = final_category
        # Ensure emoji/sentiment exist
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
                    f"[AIService] Classify | ⚠️ Local failed, falling back to cloud: {local_e}",
                )
            else:
                logger.warning(
                    f"[AIService] Classify | ⚠️ Local model unavailable, falling back to cloud: {local_e}",
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
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                f"[AIService] Classify | Cloud ✅ {result.get('category')} / {result.get('sentiment')}",
            )
            return result
        except Exception as e:
            logger.error(
                f"[AIService] Classify | ❌ All providers failed: {e}",
                exc_info=True,
            )
            return None  # type: ignore

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
            logger.error(
                f"[AIService] Verify | ❌ Connection verification failed: {e}",
                exc_info=True,
            )
            raise e

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

            response = await acompletion(**request_params)

            result = {"success": True, "message": "Connection successful"}

            if hasattr(response, "usage") and response.usage:
                result["usage"] = {
                    "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                    "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                    "total_tokens": getattr(response.usage, "total_tokens", 0),
                }

            if supports_reasoning:
                result["reasoning_supported"] = True

            return result

        except Exception as e:
            logger.error(f"[AIService] TestConn | ❌ Test connection failed: {e}")
            error_info = _classify_api_error(e)
            return {
                "success": False,
                "message": error_info["message"],
                "error_code": error_info["code"],
            }
