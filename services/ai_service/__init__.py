"""AIService 组合根模块（review01-A5b-2）。

``AIService`` 由单一巨型类重构为「组合根 + 薄委托」，职责拆分为四个子模块：
- ``LiteLLMClient``（services/ai_service/litellm_client.py）：LiteLLM 云端调用
- ``TokenBudgetService``（services/ai_service/token_budget.py）：token 预算
- ``StockAnalysisService``（services/ai_service/stock_analysis.py）：股票分析
- ``NewsClassifier``（services/ai_service/news_classifier.py）：新闻分类

设计约定（测试兼容）：
- ``AIService`` 保留全部公共/私有方法签名转发到子模块（薄委托），既有测试对
  ``AIService._build_litellm_params`` / ``_chat_completion_litellm`` 等的调用面、
  以及对 ``patch("services.ai_service.acompletion")`` 等 patch 目标零改动。
- 模块级 LiteLLM 惰性加载全局（litellm/acompletion/LITELLM_AVAILABLE/
  _litellm_import_attempted）与常量在下方 re-export，供子模块经
  ``import services.ai_service as _ai`` 读写，以及既有测试 patch。
- ``ConfigHandler`` / ``DataSanitizer`` 亦在下方 re-export（组合根模块属性），
  子模块统一经 ``_ai.ConfigHandler`` / ``_ai.DataSanitizer`` 访问，保证测试
  ``patch("services.ai_service.ConfigHandler")`` / ``patch("services.ai_service.DataSanitizer")`` 生效。
- 子模块实例在 ``__init__`` 中创建；为兼容 ``AIService.__new__`` 构造的测试替身
  （未走 __init__），委托方法统一先调 ``_ensure_subservices()`` 惰性补齐。
"""

import asyncio
import config
import contextlib
import logging
import os
import threading
import time

from services.ai_service.labels import (
    AVAILABLE_DATA_LABELS as AVAILABLE_DATA_LABELS,
    build_available_data_block as build_available_data_block,
    filter_available_labels as filter_available_labels,
    get_strategy_min_tier as get_strategy_min_tier,
    validate_strategy_tier_coverage as validate_strategy_tier_coverage,
)
from services.ai_service.litellm_client import (
    AIServiceUnavailableError as AIServiceUnavailableError,
    CONNECT_TIMEOUT as CONNECT_TIMEOUT,
    DEFAULT_ANALYSIS_CONCURRENCY,
    DEFAULT_ANALYSIS_TIMEOUT as DEFAULT_ANALYSIS_TIMEOUT,
    DEFAULT_CLOUD_TIMEOUT as DEFAULT_CLOUD_TIMEOUT,
    DEFAULT_LOCAL_MAX_TOKENS as DEFAULT_LOCAL_MAX_TOKENS,
    DEFAULT_NEWS_CONCURRENCY,
    DEFAULT_VERIFY_TIMEOUT as DEFAULT_VERIFY_TIMEOUT,
    ERROR_MESSAGE_TRUNCATE_LEN as ERROR_MESSAGE_TRUNCATE_LEN,
    LITELLM_AVAILABLE as LITELLM_AVAILABLE,
    LITELLM_MAX_RETRIES,
    LITELLM_SET_TIMEOUT,
    LiteLLMClient,
    _check_reasoning_support,
    _classify_api_error as _classify_api_error,
    _ensure_litellm_loaded as _ensure_litellm_loaded,
    _litellm_import_attempted as _litellm_import_attempted,
    acompletion as acompletion,
    litellm,
)
from services.ai_service.news_classifier import (
    NEWS_TEXT_MAX_LEN as NEWS_TEXT_MAX_LEN,
    NewsClassifier,
)
from services.ai_service.output import (
    _FREE_TEXT_MAX_LEN as _FREE_TEXT_MAX_LEN,
    VALID_RECOMMENDATIONS as VALID_RECOMMENDATIONS,
    _sanitize_free_text as _sanitize_free_text,
    validate_ai_analysis_response as validate_ai_analysis_response,
)
from services.ai_service.stock_analysis import (
    CONCEPTS_LIMIT as CONCEPTS_LIMIT,
    GLOBAL_CONTEXT_MAX_LEN as GLOBAL_CONTEXT_MAX_LEN,
    HISTORY_CONTEXT_MAX_LEN as HISTORY_CONTEXT_MAX_LEN,
    NEWS_LIST_LIMIT as NEWS_LIST_LIMIT,
    STRATEGY_CONTEXT_MAX_LEN as STRATEGY_CONTEXT_MAX_LEN,
    StockAnalysisService,
)
from services.ai_service.token_budget import (
    CHAR_FALLBACK_TOKENS_DIV as CHAR_FALLBACK_TOKENS_DIV,
    CONTEXT_RESERVE_TOKENS as CONTEXT_RESERVE_TOKENS,
    DEFAULT_CONTEXT_WINDOW as DEFAULT_CONTEXT_WINDOW,
    TokenBudgetService,
    _apply_context_budget as _apply_context_budget,
    _estimate_tokens as _estimate_tokens,
    _get_model_context_window as _get_model_context_window,
    _reset_token_estimator as _reset_token_estimator,
)
from services.local_model_manager import LocalModelManager
from utils.config_handler import ConfigHandler
from utils.error_classifier import log_classified
from utils.loop_local import del_loop_local, get_loop_local
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer as DataSanitizer
from utils.singleton_registry import register_singleton

logger = logging.getLogger(__name__)

# Prompt dump 文件保留时长（小时）——组合根 init 链（_cleanup_prompt_dumps）使用
PROMPT_DUMP_RETENTION_HOURS = 24


@register_singleton
class AIService:
    """
    AI Service - 基于 LiteLLM 1.82+ 的统一 LLM 网关（组合根 + 薄委托）

    设计原则:
    1. Cloud Provider: 使用 LiteLLM 统一调用各厂商 API
    2. Local Provider: 绝对隔离，不经过 LiteLLM，直接调用 LocalModelManager
    3. 状态机管理: 使用 _is_cloud_configured 替代 self.client
    4. 异步安全: 使用懒加载动态锁，避免跨事件循环崩溃

    A5b-2 重构: 云端调用 / token 预算 / 股票分析 / 新闻分类分别委托到
    LiteLLMClient / TokenBudgetService / StockAnalysisService / NewsClassifier 子模块。
    共享状态（_litellm_config / _failover_credentials / loop-local semaphore）保留在
    本类，子模块经构造注入的 service 实例访问。

    重要: 异步锁必须在运行时动态创建，绑定到当前事件循环
    禁止在类级别或 __init__ 中直接创建 asyncio.Lock/Semaphore

    _atexit_cleanup: 不需要。LiteLLM 是函数式调用（无持久化客户端实例），
    httpx 客户端由 LiteLLM 内部管理，进程退出时自动释放。
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

        del_loop_local("ai_setup_lock")
        del_loop_local("ai_analysis_semaphore")
        del_loop_local("ai_news_semaphore")

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
        self._ensure_subservices()

        self._initialized = True

    def _ensure_subservices(self) -> None:
        """确保子模块实例存在（兼容 AIService.__new__ 构造的测试替身）。"""
        if not hasattr(self, "_litellm"):
            self._litellm = LiteLLMClient(self)
            self._budget = TokenBudgetService(self)
            self._stock_analysis = StockAnalysisService(self)
            self._news_classifier = NewsClassifier(self)

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
            cutoff_ts = time.time() - PROMPT_DUMP_RETENTION_HOURS * 60 * 60
            for name in os.listdir(dump_dir):
                file_path = os.path.join(dump_dir, name)
                if os.path.isfile(file_path) and os.path.getmtime(file_path) < cutoff_ts:
                    with contextlib.suppress(OSError):
                        os.remove(file_path)
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[AIService] Prompt dump cleanup skipped (%s): %s",
                exc_info=True,
            )

    def _configure_litellm(self):
        """配置 LiteLLM 全局参数 (1.82+ 优化)

        R16: 惰性加载后 __init__ 链不得触发 import。litellm 未加载（None）时跳过，
        全局参数配置交由首次 _ensure_litellm_loaded() 完成。
        """
        if litellm is None:
            return

        litellm.set_verbose = False  # type: ignore[reportPrivateImportUsage]  # LiteLLM private API usage for logging suppression
        litellm.drop_params = True
        litellm.set_timeout = LITELLM_SET_TIMEOUT  # type: ignore[attr-defined]
        litellm.max_retries = LITELLM_MAX_RETRIES  # type: ignore[attr-defined]
        litellm.success_callback = []
        litellm.failure_callback = []
        litellm.modify_params = True

        logger.debug("[AIService] LiteLLM 1.82+ configured")

    def _setup_client(self):
        """
        配置云端 LLM (LiteLLM 版本)

        重要: LiteLLM 是函数式调用，没有持久化的 Client 实例
        这里缓存配置供后续调用使用

        R16: 惰性加载后 __init__ 链不得触发 import。litellm 未加载（None）时按
        "云端未配置" 降级，不阻塞 UI。
        """
        if litellm is None:
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
            log_classified(
                logger,
                e,
                "general",
                "[AIService] Failover credential pre-load skipped (%s): %s",
                exc_info=True,
            )

        logger.info(
            "[AIService] Init | Cloud client ready. provider=%s, reasoning=%s",
            provider,
            self._supports_reasoning,
        )

    def is_cloud_available(self) -> bool:
        """检查云端 LLM 是否可用 (替代 if not self.client)"""
        return self._is_cloud_configured and bool(self._litellm_config.get("api_key"))

    def _get_analysis_semaphore(self):
        """股票分析云端 LLM 调用信号量（loop-local，热生效）。"""

        def _factory():
            raw_val = ConfigHandler.get_ai_max_concurrent_analysis()
            concurrency = max(1, int(raw_val)) if raw_val else DEFAULT_ANALYSIS_CONCURRENCY
            return asyncio.Semaphore(concurrency)

        return get_loop_local("ai_analysis_semaphore", _factory)

    def _get_news_semaphore(self):
        """新闻分类云端兜底信号量（loop-local，热生效）。"""

        def _factory():
            raw_val = ConfigHandler.get_ai_news_max_concurrent()
            concurrency = max(1, int(raw_val)) if raw_val else DEFAULT_NEWS_CONCURRENCY
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

    # ------------------------------------------------------------------
    # 薄委托：LiteLLMClient（云端调用）
    # ------------------------------------------------------------------

    @staticmethod
    def _build_litellm_params(
        llm_config: dict,
        messages: list,
        model_override: str | None = None,
        failover_credentials: dict[str, dict] | None = None,
        **kwargs,
    ) -> dict:
        """委托 LiteLLMClient._build_litellm_params（静态方法，测试兼容，保留显式签名）。"""
        return LiteLLMClient._build_litellm_params(
            llm_config,
            messages,
            model_override=model_override,
            failover_credentials=failover_credentials,
            **kwargs,
        )

    async def _chat_completion_litellm(
        self,
        messages: list,
        on_chunk=None,
        model_override: str | None = None,
        **kwargs,
    ) -> dict:
        """委托 LiteLLMClient._chat_completion_litellm（保留显式签名）。"""
        self._ensure_subservices()
        return await self._litellm._chat_completion_litellm(
            messages, on_chunk=on_chunk, model_override=model_override, **kwargs
        )

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
        """委托 LiteLLMClient._chat_completion（保留显式签名）。"""
        self._ensure_subservices()
        return await self._litellm._chat_completion(
            messages,
            model=model,
            provider=provider,
            temperature=temperature,
            timeout=timeout,
            json_mode=json_mode,
            on_chunk=on_chunk,
            purpose=purpose,
            local_max_tokens=local_max_tokens,
        )

    async def _chat_completion_with_failover(
        self,
        messages: list,
        timeout: float = DEFAULT_ANALYSIS_TIMEOUT,
        json_mode: bool = True,
        on_chunk=None,
    ) -> dict:
        """委托 LiteLLMClient._chat_completion_with_failover（保留显式签名）。"""
        self._ensure_subservices()
        return await self._litellm._chat_completion_with_failover(
            messages, timeout=timeout, json_mode=json_mode, on_chunk=on_chunk
        )

    async def verify_connection(self) -> bool:
        """委托 LiteLLMClient.verify_connection。"""
        self._ensure_subservices()
        return await self._litellm.verify_connection()

    async def chat_with_web_search(
        self,
        messages: list[dict],
        search_domain_filter: list[str] | None = None,
        search_engine: str = "search_std",
        temperature: float = 0.3,
        timeout: float = 60.0,
    ) -> dict:
        """委托 LiteLLMClient.chat_with_web_search（保留显式签名）。"""
        self._ensure_subservices()
        return await self._litellm.chat_with_web_search(
            messages,
            search_domain_filter=search_domain_filter,
            search_engine=search_engine,
            temperature=temperature,
            timeout=timeout,
        )

    @staticmethod
    async def test_connection(
        provider: str = "deepseek",
        model: str = "",
        base_url: str = "",
        api_key: str = "",
        **kwargs,
    ) -> dict:
        """委托 LiteLLMClient.test_connection（静态方法，测试兼容，保留显式签名）。"""
        return await LiteLLMClient.test_connection(
            provider=provider, model=model, base_url=base_url, api_key=api_key, **kwargs
        )

    # ------------------------------------------------------------------
    # 薄委托：TokenBudgetService（token 预算）
    # ------------------------------------------------------------------

    def _compute_analysis_budget(self) -> int:
        """委托 TokenBudgetService._compute_analysis_budget。"""
        self._ensure_subservices()
        return self._budget._compute_analysis_budget()

    # ------------------------------------------------------------------
    # 薄委托：StockAnalysisService（股票分析）
    # ------------------------------------------------------------------

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
        """委托 StockAnalysisService.analyze_stock（保留显式签名）。"""
        self._ensure_subservices()
        return await self._stock_analysis.analyze_stock(
            stock_info,
            tech_info,
            news_list,
            global_context,
            strategy_context,
            capital_flow_text,
            financials_text,
            history_text,
            on_chunk,
            history_context,
            strategy_key,
            include_global_context,
            include_learning_context,
            ui_prompt_override,
            is_backtest,
            financial_labels=financial_labels,
            capital_labels=capital_labels,
            history_labels=history_labels,
        )

    # ------------------------------------------------------------------
    # 薄委托：NewsClassifier（新闻分类）
    # ------------------------------------------------------------------

    def _parse_news_result(self, raw_result: dict) -> dict:
        """委托 NewsClassifier._parse_news_result。"""
        self._ensure_subservices()
        return self._news_classifier._parse_news_result(raw_result)

    async def classify_news(self, text: str) -> dict:
        """委托 NewsClassifier.classify_news。"""
        self._ensure_subservices()
        return await self._news_classifier.classify_news(text)

    # ------------------------------------------------------------------
    # 本地模型 / 设置锁（保留在组合根）
    # ------------------------------------------------------------------

    async def _get_setup_lock(self):
        """Lazy-initialize the async lock dynamically per event loop to avoid cross-loop binding deadlocks."""

        def _factory():
            return asyncio.Lock()

        return get_loop_local("ai_setup_lock", _factory)

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE)
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
