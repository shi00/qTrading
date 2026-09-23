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

from core.errors import AIConfigError, AIPolicyNotAcknowledgedError
from core.i18n import Message
from services.ai_service.pricing import estimate_cost
from services.ai_service.token_budget import _estimate_tokens, _get_model_context_window
from services.local_model_manager import LocalInferenceTimeoutError, LocalModelManager
from utils.egress_ack import is_egress_acknowledged
from utils.error_classifier import classify_error, classify_severity, log_classified
from utils.log_decorators import PerfThreshold, log_async_operation

if TYPE_CHECKING:
    from pydantic import BaseModel

    from services.ai_service import AIService

logger = logging.getLogger(__name__)

# === LiteLLM 全局配置 ===
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

# L4：litellm.Router 惰性加载全局。_litellm_router 缓存单例 Router 实例（跨请求复用
# 内部的鲁棒性健康检查/重试/fallback 状态），_router_import_attempted 为构造哨兵，
# 语义与 _litellm_import_attempted 一致。配置变更（reload_config）失效两全局，
# 下次调用重建。
_litellm_router: Any = None
_router_import_attempted = False


class AIServiceUnavailableError(Exception):
    """P1-12: 所有 LLM 供应商都不可用时抛出"""

    pass


def _resolve_effective_model(llm_config: dict, model_override: str | None) -> str:
    """解析本次真实发送的 effective model（SEC-03 审计 destination 单点来源）。

    - model_override（failover 传入，可带 provider/app 前缀如 ``qwen/qwen-max``）优先；
    - 否则回退配置 ``provider/model`` 拼接。

    与 ``_chat_completion_litellm`` 的 model 解析逻辑保持一致，抽为单点防止漂移：
    审计记录与真实请求必须指向同一个 model。
    """
    if model_override:
        return model_override
    provider = llm_config.get("provider", "")
    model_id = llm_config.get("model", "")
    return f"{provider}/{model_id}" if provider else model_id


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
        # OSS 检视 A1: litellm 无模块级 set_timeout/max_retries（Python 允许任意赋值
        # 导致死代码静默空转）。重试经 _build_litellm_params 以 per-request num_retries
        # 传递（completion 主路径仅读取请求参数；模块级 num_retries 不参与）。
        # 超时经 request_params["timeout"] = httpx.Timeout(...) 逐请求兜底。
        _lt.success_callback = []
        _lt.failure_callback = []
        _lt.modify_params = True
        _ai.litellm, _ai.acompletion = _lt, _ac
        _ai.LITELLM_AVAILABLE = True
        return True
    except Exception as exc:
        # 兼容不同 litellm 版本：import 或全局参数配置（如某版本缺失某属性抛
        # AttributeError）失败均视为"不可用"，优雅降级而非向上传播。
        # 注意 except Exception 不捕获 asyncio.CancelledError / KeyboardInterrupt (R2)。
        # 记录真实异常（R9：经 DataSanitizer 脱敏），避免 P0 类故障（如 tiktoken
        # 编码文件离线下载失败）在降级路径上无迹可循。
        _ai.LITELLM_AVAILABLE = False
        logger.warning(
            "[AIService] LiteLLM not available, cloud LLM features disabled: %s",
            _ai.DataSanitizer.sanitize_error(exc),
        )
        return False


def _check_reasoning_support(model: str) -> bool:
    """检查模型是否支持推理增强 (reasoning_content)

    R16: litellm 惰性加载后，__init__ 链不得触发 import。此处仅当 litellm 已加载
    （模块级符号非 None）时才用 litellm.utils.supports_reasoning；未加载或调用异常
    时保守返回 ``False``（不可判定即视为不支持，见 §3.1c-review #2）。
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
                "[AIService] supports_reasoning check failed (%s) for %s: %s, treating as not reasoning",
                model,
                exc_info=True,
            )
    return False


def _check_response_schema_support(model: str) -> bool:
    """检查模型是否支持 'response_schema' 结构化输出（OSS 检视 A4）。

    与 ``_check_reasoning_support`` 同一范式：litellm 惰性加载后才能用
    ``litellm.utils.supports_response_schema`` 精确判定；未加载或调用异常时
    保守返回 ``False``（不可判定即视为不支持，见 §3.1c-review #2），调用方
    保持既有 ``json_object`` 路径，行为退化为现状（不回归）。
    """
    import services.ai_service as _ai

    if _ai.litellm is not None:
        try:
            return _ai.litellm.utils.supports_response_schema(model=model)
        except Exception as exc:
            log_classified(
                logger,
                exc,
                "general",
                "[AIService] supports_response_schema check failed (%s) for %s: %s, treating as unsupported",
                model,
                exc_info=True,
            )
    return False


def build_router_model_list(
    failover_config: dict,
    failover_credentials: dict[str, dict] | None = None,
) -> tuple[list[dict], list[str]]:
    """primary + fallbacks → litellm.Router.model_list（L4，构造期 D8-1 校验）。

    复用 ``LiteLLMClient._build_litellm_params`` 的跨供应商凭据隔离逻辑作为单点解析
    （零漂移）：每个候选模型以 model_override 形式送入，取回 model/api_key/api_base。

    D8-1 迁移（R9 凭证跨域泄露防护）：
    - 跨供应商 fallback 缺专属 key 时该调用抛 ``AIConfigError``，此处捕获后**排除该项**，
      记入返回的 ``excluded`` 清单（绝不静默以全局 key 回退，也不连坐 primary —
      primary 永不因 fallback 配置缺陷中断）。
    - primary 构造失败（缺 model / 无 key）向上传播：云端口已由 ``is_cloud_available``
      前置把关，primary 缺失属程序错误，不可静默丢弃。

    Returns:
        (model_list, excluded):
        - model_list: ``[{"model_name": str, "litellm_params": {...}}]``，
          每项 litellm_params 含 model/api_key/api_base（Azure 含 api_version）。
        - excluded: 因缺专属 key 被排除的 "provider/model" 字符串列表（供调用方 warning）。
    """
    if failover_credentials is None:
        failover_credentials = {}

    primary = failover_config.get("primary", "")
    fallbacks = failover_config.get("fallbacks", []) or []
    primary_config = failover_config.get("primary_config") or {}

    model_list: list[dict] = []
    excluded: list[str] = []

    def _resolve(candidate: str, *, is_primary: bool) -> dict:
        """单个候选模型 → litellm_params（经 _build_litellm_params 单点解析）。"""
        # 空 messages：_build_litellm_params 只解析 provider/model/凭据，不消耗消息内容
        return LiteLLMClient._build_litellm_params(
            primary_config,
            messages=[],
            model_override=candidate if not is_primary else None,
            failover_credentials=failover_credentials,
        )

    if primary:
        params = _resolve(primary, is_primary=True)
        model_list.append(
            {
                "model_name": primary,
                "litellm_params": {
                    "model": params.get("model"),
                    "api_key": params.get("api_key"),
                    "api_base": params.get("api_base"),
                },
            }
        )

    for fb in fallbacks:
        if not fb:
            continue
        try:
            params = _resolve(fb, is_primary=False)
        except AIConfigError:
            # D8-1：缺专属 key → 排除该项，不静默回退全局 key；不阻断 primary 与其他 fallback
            excluded.append(fb)
            continue
        model_list.append(
            {
                "model_name": fb,
                "litellm_params": {
                    "model": params.get("model"),
                    "api_key": params.get("api_key"),
                    "api_base": params.get("api_base"),
                },
            }
        )

    return model_list, excluded


def _ensure_router_loaded(failover_credentials: dict[str, dict] | None = None) -> bool:
    """惰性构造 litellm.Router 并返回是否可用（L4）。

    与 ``_ensure_litellm_loaded`` 同一范式（构造哨兵 + 失败优雅降级）：
    - model_list 经 ``build_router_model_list`` 从最新 failover 配置单点构造——
      跨供应商缺专属 key 的 fallback 已排除（D8-1），排除项记 warning 不连坐 primary。
    - ``failover_credentials`` 复用 AIService._setup_client 预加载的跨供应商凭据缓存
      （_router_failover 传入），避免构造期在 hot path 触发同步 keyring 调用
      （检视 MINOR-3 修复）。
    - fallback 映射表（Router.fallbacks，{model_name: [fallback names]}）只登记
      model_list 中实际存在的 model_name：excluded 项不可引用，否则 Router 构造校验
      失败（引用未注册 model_name）。
    - 重试/冷却/fallback 表在 Router 构造级配置（retry_policy / context_window 等
      精调见 M3）。

    返回 False 含义：litellm 未加载 / failover 配置缺 primary / 构造异常。调用方
    （``_router_failover``）收敛为 AIServiceUnavailableError，不静默回退任何
    非 Router 路径。
    """
    # Any 标注：sys.modules 返回 ModuleType（与 _ensure_litellm_loaded 同风格）。
    _ai: Any = sys.modules["services.ai_service"]

    if _ai._router_import_attempted:
        return _ai._litellm_router is not None
    _ai._router_import_attempted = True

    # Router 是 litellm 子模块，须先完成 litellm 加载（_ensure_litellm_loaded 幂等）。
    if not _ai._ensure_litellm_loaded():
        return False

    from utils.config_handler import ConfigHandler

    try:
        failover_config = ConfigHandler.get_failover_config()
        model_list, excluded = build_router_model_list(failover_config, failover_credentials=failover_credentials)
        if excluded:
            logger.warning(
                "[AIService] Failover | %d fallback(s) excluded from Router (missing dedicated API key): %s",
                len(excluded),
                ", ".join(excluded),
            )

        primary = failover_config.get("primary", "")
        fallbacks = failover_config.get("fallbacks", []) or []
        available_names = {item["model_name"] for item in model_list}
        accessible_fallbacks = [fb for fb in fallbacks if fb in available_names]
        # litellm 1.102.1 校验 fallbacks/context_window_fallbacks 为 list-of-dicts
        # 形式 [{model_name: [fallback model_names]}]（validate_fallbacks 逐项要求 dict）。
        cross_model_fallbacks = [{primary: accessible_fallbacks}] if (primary and accessible_fallbacks) else []

        # 参数语义（M3，经 litellm 1.102.1 源码 + 构造验证）：
        # - num_retries：同供应商瞬时错误（429/5xx/超时）原地重试次数，与旧手写循环的
        #   LITELLM_MAX_RETRIES（per-request num_retries）对齐（A2 语义：瞬时可自动重试
        #   的错误交由 litellm 承担，Router 负责跨供应商切换）。
        # - retry_policy AuthenticationErrorRetries=0：401 认证错误契约保险——源码默认
        #   行为已在 should_retry_this_error 中因部署数=1 直接 raise（不重试不 fallback），
        #   显式置 0 防 litellm 版本演进悄悄改变该语义；其余字段留 None 走默认
        #   _should_retry(status_code) 判定（401→raise，429/5xx→重试）。
        # - context_window_fallbacks：超长请求（ContextWindowExceededError）专属 fallback
        #   表，与客户端超长预检（_router_failover 内 _estimate_tokens 标尺）构成双层防线。
        # - fallbacks：普通可重试错误（429/5xx/超时）的跨模型切换表（按序尝试；受
        #   max_fallbacks=5 与业务 fallback 数量自然双限）。
        # - cooldown_time：供应商连续失败后的冷却秒数（健康路由避免请求打到刚失败的供应商）。
        # - ContentPolicyViolationError 未配置专属 content_policy_fallbacks：Router 默认
        #   会尝试普通 fallback（行为增强，旧循环直抛语义不再保留——落在文档/ADR 中说明）。
        _ai._litellm_router = _ai.litellm.Router(
            model_list=model_list,
            num_retries=LITELLM_MAX_RETRIES,
            retry_policy=_ai.litellm.RetryPolicy(AuthenticationErrorRetries=0),
            fallbacks=cross_model_fallbacks,
            context_window_fallbacks=cross_model_fallbacks,
            cooldown_time=30.0,
        )
        return True
    except Exception as exc:
        # 兼容不同 litellm 版本 / 构造参数校验失败：Router 不可用即降级（不向上抛，
        # 避免把配置缺陷升级为全局崩溃；失败由 _router_failover 的
        # AIServiceUnavailableError 承载）。except Exception 不误捕
        # asyncio.CancelledError / KeyboardInterrupt（R2）。
        _ai._litellm_router = None
        logger.warning(
            "[AIService] Failover | Router construction failed, cloud failover disabled: %s",
            _ai.DataSanitizer.sanitize_error(exc),
        )
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
                    # D8-1: 跨供应商 failover 无专属 key 时显式失败，而非静默继续。
                    # 禁止以全局 key 回退：全局 ai_api_key 语义上属于当前主供应商，
                    # 复用会把 A 的凭证发往 B 的 endpoint（凭证跨域泄露）。此处
                    # 显式抛错使 failover 明确失败并可给出可操作提示。
                    raise AIConfigError(
                        Message(
                            "ai_failover_missing_credential",
                            {"provider": override_provider},
                        ),
                        detail=(
                            f"Cross-provider failover target '{override_provider}' has no "
                            "dedicated API key; refusing to reuse the global api_key"
                        ),
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

        # OSS 检视 A1: litellm 重试经 per-request num_retries 生效——completion 主路径
        # 只读取请求参数（num_retries 或 max_retries），模块级赋值不参与。同一供应商的
        # 瞬时错误（429/503/timeout）先在原地重试 num_retries+1 次，再交由 failover 切供应商
        # （A2 语义分层：瞬时重试由 litellm 承担，自研 failover 循环只负责跨供应商切换）。
        request_params["num_retries"] = LITELLM_MAX_RETRIES

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

        token_model = model_override or (llm_config.get("model") if isinstance(llm_config.get("model"), str) else "")
        total_tokens = sum(_estimate_tokens(m.get("content"), token_model) for m in messages)
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

        effective_model = _resolve_effective_model(llm_config, model_override)
        supports_reasoning = _ai._check_reasoning_support(effective_model)

        # OSS 检视 A4: 对 supports_response_schema 的模型，直接下传 pydantic 输出契约
        # （response_schema 由调用方提供）作为 response_format —— litellm 内部经
        # get_optional_params 的 get_json_schema_from_pydantic_object 转为 json_schema 交给
        # 供应商侧保证 schema 合规。此时 request_params["response_format"] 由
        # _build_litellm_params 从 json_object 兜底 dict 覆盖为 pydantic model；不支持或
        # 未提供契约的模型保持既有 {"type":"json_object"} 路径（启发式解析退化为纯兜底）。
        schema_override: type[BaseModel] | None = kwargs.get("response_schema")
        if schema_override is not None and _ai._check_response_schema_support(effective_model):
            request_params["response_format"] = schema_override

        stream = kwargs.get("stream", False) or on_chunk is not None

        with ProxyManager.litellm_env_context():
            if stream:
                # AI-01 计价链路（REVIEW-04 复核 D2）：流式调用无条件请求 usage
                # （末 chunk 携带），否则非 reasoning 模型的流式路径 usage 缺失 →
                # 零记账（连 unpriced 都不计）。litellm drop_params=True 兜底不支持
                # stream_options 的 provider（静默丢弃参数，行为退化为现状不报错）。
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
                                # `or 0`：litellm Usage 字段为 Optional，属性存在但值
                                # 可为 None（getattr 默认值仅对属性缺失生效）；None 传入
                                # estimate_cost 的 `input_tokens < 0` 会抛 TypeError，
                                # 把一次成功的流式调用误判为失败（REVIEW-04 复核 D2
                                # 无条件 include_usage 后暴露面覆盖所有流式调用）。
                                usage = {
                                    "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                                    "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                                    "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
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
                # Mi2（review-pr1073）：携带生效模型 id，供上层 unpriced 明细（model→calls）聚合。
                result["model"] = effective_model
                if reasoning_content:
                    result["reasoning_content"] = reasoning_content
                if usage:
                    result["usage"] = usage
                    result["cost"] = estimate_cost(
                        effective_model,
                        usage.get("prompt_tokens", 0),
                        usage.get("completion_tokens", 0),
                    )

                return result
            else:
                response = await _ai.acompletion(**request_params)
                content = response.choices[0].message.content  # type: ignore[union-attr]
                result = {"content": content}
                # Mi2（review-pr1073）：非流式路径亦携带生效模型 id（明细聚合用）。
                result["model"] = effective_model

                if hasattr(response, "usage") and response.usage:  # type: ignore[union-attr]
                    # `or 0` 归一化同流式分支：litellm Usage 字段为 Optional，
                    # 值可为 None，None 传入 estimate_cost 会抛 TypeError。
                    result["usage"] = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,  # type: ignore[union-attr]
                        "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,  # type: ignore[union-attr]
                        "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,  # type: ignore[union-attr]
                    }
                    result["cost"] = estimate_cost(
                        effective_model,
                        result["usage"]["prompt_tokens"],
                        result["usage"]["completion_tokens"],
                    )

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
        response_schema: type[BaseModel] | None = None,
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
            response_schema: pydantic 输出契约（OSS 检视 A4）。cloud + json_mode 下若
                模型 supports_response_schema，则以 pydantic model 作为 response_format
                下传（litellm 内部转 json_schema，供应商侧保证 schema 合规）；
                模型不支持或非 cloud 时忽略（走既有 json_object / 纯文本路径）。
        Returns:
            dict: Parsed JSON content (or raw dict if non-json)
        Raises:
            Exception: on failure (caller should handle fallback)
        """
        import services.ai_service as _ai

        response_content = ""

        # --- Local Provider ---
        # AI-01 计价链路（REVIEW-04 复核 D1）：cloud 分支暂存 litellm 层元数据
        # （model/usage/cost/reasoning_content，Mi2 引入），local 分支无计量保持空。
        llm_metadata: dict = {}

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

            # SEC-01：新闻分类通过 _chat_completion(provider="cloud", purpose="news")
            # 是独立于 run_ai_analysis 的云端外发通道，须先通过外发知情确认门控再发起
            # 云端请求（未确认绝不外发）。analysis 通道在入口由 run_ai_analysis 门控，
            # 此处仅需补 news（chat_with_web_search 同理，见该方法）。非交互降级：未确认
            # 即抛策略阻断异常，由调用方（news_classifier）降级为默认分类。
            if purpose == "news" and not is_egress_acknowledged():
                logger.warning(
                    "[AIService] Cloud | News egress policy not acknowledged — skipping cloud "
                    "classification (no external requests initiated)",
                )
                raise AIPolicyNotAcknowledgedError(
                    Message("ai_external_acknowledgment_prompt"),
                    detail="News cloud egress requires user acknowledgment (SEC-01); "
                    "no external request was initiated.",
                )

            sem = self._service._get_news_semaphore() if purpose == "news" else self._service._get_analysis_semaphore()
            async with sem:
                logger.debug(
                    "[AIService] Cloud | Invoking LiteLLM (%d messages)",
                    len(messages),
                )

                # SEC-03：集中出口元数据审计（共享 helper，单一事实源，见
                # _record_cloud_egress 注释）。审计失败不阻断 AI 主流程。
                await self._record_cloud_egress(messages, model=model, category=purpose)

                # 经组合根 (self._service) 调用：保证测试对 AIService 实例属性
                # （如 `svc._chat_completion_litellm = AsyncMock(...)`）的 monkeypatch 生效。
                result = await self._service._chat_completion_litellm(
                    messages,
                    on_chunk=on_chunk,
                    model_override=model,
                    temperature=temperature,
                    timeout=timeout,
                    response_format={"type": "json_object"} if json_mode else None,
                    response_schema=response_schema if (json_mode and response_schema is not None) else None,
                )
                response_content = result["content"]
                llm_metadata = {k: v for k, v in result.items() if k != "content"}

        # --- Post-Processing (JSON Parsing) ---
        # AI-01 计价链路（REVIEW-04 复核 D1）：litellm 层已携带 model/usage/cost/
        # reasoning_content 元数据（Mi2），JSON 解析后必须回填——下游 _accumulate_usage
        # 在本层之后消费 usage/cost/model，丢弃即把真实云端消耗呈现为零（R21 回归，
        # 预算软停/unpriced 确认全部空转）。
        if json_mode:
            try:
                # 1. Cleaner: Try direct parse
                parsed = json.loads(response_content)
                # 元数据键覆盖模型输出同名键（系统计量可信度高于模型输出，模型
                # 幻觉输出的 usage/cost 键不可覆盖真实计量）；非 dict 解析结果
                # （null/list 等）维持原语义返回，无从附加元数据。
                return {**parsed, **llm_metadata} if isinstance(parsed, dict) else parsed
            except json.JSONDecodeError:
                pass

            # 2. Heuristic Extraction
            try:
                start = response_content.find("{")
                if start != -1:
                    try:
                        obj, _idx = json.JSONDecoder().raw_decode(  # 保留剩余位置信息供调试，实际仅用 obj
                            response_content[start:],
                        )
                        return {**obj, **llm_metadata} if isinstance(obj, dict) else obj
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

        return {**llm_metadata, "content": response_content}

    async def _record_cloud_egress(
        self,
        messages: list,
        *,
        model: str | None,
        category: str,
    ) -> None:
        """SEC-03 集中云端外发元数据审计（各 cloud 出口路径共享的单一事实源）。

        记录时间/目的地/类别/载荷字节/条目数，**不记 prompt 内容本身**，避免二次泄露。
        destination 用真实 effective model（含 failover cross-provider 前缀，经
        ``_resolve_effective_model`` 单点解析），与本请求实际发送的 model 一致。

        仅 **cloud** 出口调用（local 分支数据不出本机，无需记录）。审计失败不阻断 AI
        主流程（R2：CancelledError 必须传播）。
        """
        try:
            from utils.egress_audit import EgressAudit

            llm_config = self._service._litellm_config
            effective_model = _resolve_effective_model(llm_config, model)
            await EgressAudit().record(
                destination=f"llm:{effective_model}",
                category=category,
                item_count=len(messages),
                # None/非 str content 兜底: 防 payload 计算抛错导致该次外发漏记
                payload_size_bytes=sum(len(m.get("content") or "") for m in messages),
            )
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            pass

    @log_async_operation(threshold_ms=PerfThreshold.AI_INFERENCE, log_args=False)
    async def _router_failover(
        self,
        messages: list,
        timeout: float = DEFAULT_ANALYSIS_TIMEOUT,
        json_mode: bool = True,
        on_chunk=None,
        purpose: str = "analysis",
    ) -> dict:
        """
        L4: 基于 litellm.Router 的带多供应商 fallback 的云端分析

        替换原手写 failover 循环（126 行）：同供应商瞬时错误重试 / 跨供应商切换 /
        冷却等语义交由 litellm.Router 承担，本层只负责入口门控、SEC-03 审计、
        流式解析与错误收敛。

        - SEC-01：failover 是独立云端出口通道，须先通过外发知情确认门控
          （purpose="news" 且未确认时抛 AIPolicyNotAcknowledgedError，调用方降级）。
        - SEC-03 双层审计：入口按 primary 意图记录一次；响应后若实际生效模型与
          primary 不一致（Router 已跨供应商 fallback），补记实际目的地一次。
        - 错误收敛：非瞬态错误（Auth / ContentPolicy 等）Router 不重试不 fallback
          直接抛出，保持原语义上抛原异常；瞬态错误经 Router 内部重试 + fallback
          仍失败 → AIServiceUnavailableError（Tried 从 model_list 构造）。

        Args:
            messages: 消息列表
            timeout: 超时时间
            json_mode: 是否启用 JSON 模式
            on_chunk: 流式回调
            purpose: 云端出口类别（SEC-01/SEC-03 审计用，默认 "analysis"）

        Returns:
            dict: 解析后的响应

        Raises:
            AIServiceUnavailableError: 所有供应商都失败时抛出
        """
        # 注意：failover 配置须经真实 ConfigHandler 局部导入读取（与旧循环一致，
        # 既有测试 patch 目标 ``utils.config_handler.ConfigHandler.get_failover_config``
        # 保持不变）。仅错误日志路径的 DataSanitizer 经组合根模块属性访问。
        from utils.config_handler import ConfigHandler

        import services.ai_service as _ai

        # SEC-01：与 _chat_completion 保持同一外发知情确认门控——news 通道未确认
        # 绝不外发（非交互降级为默认分类）。
        if purpose == "news" and not is_egress_acknowledged():
            logger.warning(
                "[AIService] Cloud | News egress policy not acknowledged — skipping cloud "
                "classification (no external requests initiated)",
            )
            raise AIPolicyNotAcknowledgedError(
                Message("ai_external_acknowledgment_prompt"),
                detail="News cloud egress requires user acknowledgment (SEC-01); no external request was initiated.",
            )

        failover_config = ConfigHandler.get_failover_config()
        primary = failover_config.get("primary", "")
        fallbacks = failover_config.get("fallbacks", []) or []

        # 入口门控链：cloud 可用性 → primary 存在 → litellm 加载 → Router 构造可用。
        # 任一不满足即显式失败（绝不静默回退到任何非 Router 路径）。
        if not self._service.is_cloud_available():
            raise ValueError("Cloud LLM not configured. Please set up API Key.")
        if not primary:
            raise AIServiceUnavailableError("No primary LLM provider configured for failover")
        if not _ai._ensure_litellm_loaded():
            raise AIServiceUnavailableError("LiteLLM not installed, cloud LLM features disabled")
        # 复用 AIService._setup_client 预加载的跨供应商凭据缓存，避免构造期 hot path
        # 触发同步 keyring 调用（检视 MINOR-3 修复）。
        if not _ai._ensure_router_loaded(self._service._failover_credentials):
            raise AIServiceUnavailableError("Failover router unavailable, cloud LLM features disabled")

        router = _ai._litellm_router
        llm_config = self._service._litellm_config
        sem = self._service._get_news_semaphore() if purpose == "news" else self._service._get_analysis_semaphore()

        async with sem:
            logger.debug(
                "[AIService] Failover | Router invoking via '%s' (%d messages, stream=%s)",
                primary,
                len(messages),
                on_chunk is not None,
            )

            # 客户端超长预检（方案 v2）：context_window_fallbacks 的兜底防线——请求前先按
            # 主模型上下文窗口估算 token，超长即 warning（语义与 _chat_completion_litellm
            # 的预检一致；真实拦截交由用户配置/供应商侧 ContextWindowExceededError 经
            # context_window_fallbacks 切换更大窗口模型）。预检不阻断请求（与既有语义一致）。
            # 窗口按 primary（override）查取：跨供应商 primary 时取该模型的窗口而非主配置
            # 模型（检视 MINOR-5 修复，与 _chat_completion_litellm 的 override 语义对齐）。
            token_model = primary.split("/")[-1] if "/" in primary else (llm_config.get("model") or "")
            total_tokens = sum(_estimate_tokens(m.get("content"), token_model) for m in messages)
            context_window = _get_model_context_window(llm_config, primary)
            if total_tokens > context_window:
                logger.warning(
                    "[AIService] Cloud | Prompt may exceed context window: ~%d tokens (window %d)",
                    total_tokens,
                    context_window,
                )

            # SEC-03 入口审计：记 primary 意图（一次）。审计失败不阻断 AI 主流程
            # （_record_cloud_egress 内部已吞异常，R2 CancelledError 除外）。
            await self._record_cloud_egress(messages, model=primary, category=purpose)

            stream = on_chunk is not None
            # Router 以池内 model_name 定位模型（build_router_model_list 的
            # model_name=primary）；timeout/temperature/response_format 与
            # _chat_completion_litellm 对齐（temperature=0.3 为分析默认，检视
            # MAJOR-2 修复——缺失会让 Router 走供应商默认采样温度，输出随机性回归）。
            router_params: dict = {
                "model": primary,
                "messages": messages,
                "stream": stream,
                "temperature": 0.3,
                "timeout": httpx.Timeout(timeout, connect=CONNECT_TIMEOUT),
            }
            if json_mode:
                router_params["response_format"] = {"type": "json_object"}

            try:
                if stream:
                    # AI-01 计价链路（与 _chat_completion_litellm 同源）：流式无条件
                    # include_usage（末 chunk 携带），否则非 reasoning 模型流式路径
                    # usage 缺失 → 零记账。drop_params=True 兜底不支持 stream_options
                    # 的 provider（静默丢弃，行为退化为现状）。
                    router_params["stream_options"] = {"include_usage": True}
                    result = await self._consume_router_stream(router, router_params, on_chunk, primary)
                else:
                    response = await router.acompletion(**router_params)
                    content = response.choices[0].message.content  # type: ignore[union-attr]
                    result = {"content": content}
                    # 非流式 response.model 为实际调用模型（Router fallback 后为备选模型）
                    result["model"] = getattr(response, "model", None) or primary

                    if hasattr(response, "usage") and response.usage:  # type: ignore[union-attr]
                        # `or 0`：litellm Usage 字段为 Optional，值可为 None（与
                        # _chat_completion_litellm 同源处理）。
                        result["usage"] = {
                            "prompt_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,  # type: ignore[union-attr]
                            "completion_tokens": getattr(response.usage, "completion_tokens", 0) or 0,  # type: ignore[union-attr]
                            "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,  # type: ignore[union-attr]
                        }
                        result["cost"] = estimate_cost(
                            result["model"],
                            result["usage"]["prompt_tokens"],
                            result["usage"]["completion_tokens"],
                        )

                # SEC-03 补记：实际生效模型与 primary 不一致 → Router 已跨供应商
                # fallback，补记真实目的地（双层审计落地）。
                actual_model = result.get("model") or primary
                if actual_model != primary:
                    await self._record_cloud_egress(messages, model=actual_model, category=purpose)
                    logger.info(
                        "[AIService] Failover | ✅ Succeeded on fallback model: %s",
                        actual_model,
                    )

                # --- JSON 解析（与 _chat_completion 同源语义，AI-01 计量元数据回填） ---
                # litellm 层元数据（model/usage/cost/reasoning_content）随 result 携带，
                # 解析后必须回填——下游 _accumulate_usage 在本层之后消费 usage/cost/model，
                # 丢弃即把真实云端消耗呈现为零（R21 回归）。
                llm_metadata = {k: v for k, v in result.items() if k != "content"}
                response_content = result["content"]
                if json_mode:
                    try:
                        parsed = json.loads(response_content)
                        # 元数据键覆盖模型输出同名键（系统计量可信度高于模型输出，模型
                        # 幻觉输出的 usage/cost 键不可覆盖真实计量）；非 dict 解析结果
                        # （null/list 等）维持原语义返回，无从附加元数据。
                        return {**parsed, **llm_metadata} if isinstance(parsed, dict) else parsed
                    except json.JSONDecodeError:
                        pass
                    # Heuristic Extraction（与 _chat_completion 同源：容忍包络文本）
                    try:
                        start = response_content.find("{")
                        if start != -1:
                            try:
                                obj, _idx = json.JSONDecoder().raw_decode(
                                    response_content[start:],
                                )
                                return {**obj, **llm_metadata} if isinstance(obj, dict) else obj
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
                    raise ValueError(
                        f"Invalid JSON response: {_ai.DataSanitizer.sanitize_error(response_content[:100])}..."
                    )
                return {**llm_metadata, "content": response_content}

            except asyncio.CancelledError:
                logger.debug("[AIService] Failover | Cancelled during Router invocation")
                raise
            except LocalInferenceTimeoutError:
                # 本地模型超时不属于云端 failover 范畴，直接抛出
                # （analyze_stock 的 except LocalInferenceTimeoutError 返回本地超时提示）
                raise
            except Exception as e:
                error_info = classify_error(e, context="llm")
                severity = classify_severity(e, context="llm")
                log_classified(
                    logger,
                    e,
                    "llm",
                    "[AIService] Failover | Router invocation failed (%s): %s",
                    exc_info=True,
                )
                # System-level errors (MemoryError, etc.) must propagate at CRITICAL
                if severity == "system":
                    raise
                # 非瞬态错误（认证/内容策略等）：Router 不重试不 fallback 直接抛出，
                # 保持旧循环"永久错误直接抛出"语义，由调用方按具体错误呈现。
                if not bool(error_info.get("should_retry", False)):
                    raise
                # 瞬态错误经 Router 内部重试+fallback 仍失败 → 所有供应商失败。
                # Router 日志已记录逐供应商失败过程，此处收敛为统一异常。
                # Tried 从 Router.model_list 实际注册的模型构造（不包含 D8-1 排除项，
                # 检视 MINOR-4 修复——excluded 项未实际尝试不应出现在错误消息）。
                available_models = [item["model_name"] for item in getattr(router, "model_list", [])]
                all_models_tried = ", ".join(m for m in (available_models or [primary, *fallbacks]) if m)
                raise AIServiceUnavailableError(f"All LLM providers failed. Tried: [{all_models_tried}]") from e

    async def _consume_router_stream(
        self,
        router: Any,
        router_params: dict,
        on_chunk,
        primary: str,
    ) -> dict:
        """消费 Router 流式响应，返回与 _chat_completion_litellm 同构的 result dict（L4）。

        与 _chat_completion_litellm 的流式处理同源同构（chunk 缓冲 flush / usage 采集 /
        流中断降级 / reasoning 回填），差异点：
        - reasoning_content 采用**动态字段检测**（每次 getattr），不依赖入口静态
          supports_reasoning 探测——fallback 模型与 primary 的推理支持可能不同，
          静态探测会错误跳过 fallback 模型的 reasoning 内容。
        - 实际生效模型经 chunk.model 动态收集（Router fallback 后的真实目的地），
          供 SEC-03 补记审计与计价使用；流 chunk 不含 model 时退化为 primary。
        """
        import services.ai_service as _ai

        response = await router.acompletion(**router_params)
        response_content = ""
        reasoning_content = ""
        usage = None
        actual_model = primary

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
                        # `or 0`：litellm Usage 字段为 Optional，属性存在但值可为 None
                        # （与 _chat_completion_litellm 同源处理）。
                        usage = {
                            "prompt_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                            "completion_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                            "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                        }
                    continue

                actual_model = getattr(chunk, "model", None) or actual_model
                delta = chunk.choices[0].delta

                # 动态字段检测（方案 v2）：不依赖入口静态 supports_reasoning 探测，
                # fallback 模型与 primary 推理能力不同时仍能正确收 reasoning_content。
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
        # Mi2 语义同源：携带生效模型 id，供上层 unpriced 明细（model→calls）聚合。
        result["model"] = actual_model
        if reasoning_content:
            result["reasoning_content"] = reasoning_content
        if usage:
            result["usage"] = usage
            result["cost"] = estimate_cost(
                actual_model,
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
            )
        return result

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

        # SEC-01：chat_with_web_search（概念 AI 标注等）是独立于 run_ai_analysis 的云端
        # 外发通道，须先通过外发知情确认门控再发起云端请求（未确认绝不外发）。非交互
        # 降级：未确认即抛策略阻断异常，由调用方（concept_sync）逐批失败降级处理。
        if not is_egress_acknowledged():
            logger.warning(
                "[AIService] WebSearch | Egress policy not acknowledged — skipping cloud "
                "web search (no external requests initiated)",
            )
            raise AIPolicyNotAcknowledgedError(
                Message("ai_external_acknowledgment_prompt"),
                detail="Web-search cloud egress requires user acknowledgment (SEC-01); "
                "no external request was initiated.",
            )

        web_search_config: dict = {
            "enable": True,
            "search_engine": search_engine,
        }
        if search_domain_filter:
            web_search_config["search_domain_filter"] = search_domain_filter

        tools = [{"type": "web_search", "web_search": web_search_config}]

        # SEC-03：概念同步等 web_search 云端出口同样记录审计。此前该路径直连
        # _chat_completion_litellm，绕过 _chat_completion 的审计点，导致此类云端外发
        # 对审计面板/状态栏「外发 N 次」计数不可见；经共享 helper 补记（单一事实源）。
        await self._record_cloud_egress(messages, model=None, category="web_search")

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
