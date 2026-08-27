"""AIService Token 预算子模块（review01-A5b-1）。

自 ``services/ai_service.py`` 移出的 token 估算 / 模型上下文窗口解析 / 全局预算裁剪，
承载 tiktoken 模块级缓存（R7 测试隔离经 ``_reset_token_estimator``）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.ai_service import AIService

logger = logging.getLogger(__name__)

# === Issue #70: 模型上下文感知的 Token 化全局预算 ===
# 未知/自定义模型或无 provider 时的保守上下文窗口回退值。
DEFAULT_CONTEXT_WINDOW = 128_000
# 预算预留 token：覆盖 system_instruction + <strategy_rules> + 预期输出，并留安全余量。
# 注意：不覆盖预算外的 <user_custom_instructions>（该块不受预算截断）。
# 取值 8000：对常见 32k~128k 上下文模型，可为 user 内容保留足够余量。
CONTEXT_RESERVE_TOKENS = 8000
# 回退估算分母：无 tiktoken/离线时用 len(text)//1（保守，不低估 CJK）。
CHAR_FALLBACK_TOKENS_DIV = 1

_tiktoken_enc = None
_tiktoken_enc_error = False


def _reset_token_estimator() -> None:
    """清除 tiktoken 模块缓存（测试隔离用，R7 合规）。"""
    global _tiktoken_enc, _tiktoken_enc_error
    _tiktoken_enc = None
    _tiktoken_enc_error = False


def _estimate_tokens(text) -> int:
    """估算文本 token 数（Issue #70）。

    - None/空文本 → 0
    - 非 str（多模态 list content parts）递归求和其中 str 的 text 部分
    - 惰性初始化 tiktoken cl100k_base；异常/离线回退 len(text)//CHAR_FALLBACK_TOKENS_DIV
    """
    global _tiktoken_enc, _tiktoken_enc_error
    if text is None:
        return 0
    if isinstance(text, list):
        return sum(_estimate_tokens(part.get("text")) for part in text if isinstance(part, dict))
    if not isinstance(text, str):
        return 0
    if not text:
        return 0
    if not _tiktoken_enc_error:
        try:
            if _tiktoken_enc is None:
                import tiktoken

                _tiktoken_enc = tiktoken.get_encoding("cl100k_base")
            return len(_tiktoken_enc.encode(text))
        except Exception:
            _tiktoken_enc_error = True
    return len(text) // CHAR_FALLBACK_TOKENS_DIV


def _get_model_context_window(llm_config: dict, model_override: str | None = None) -> int:
    """取生效模型的 context 窗口（Issue #70）。

    解析优先级：
    1. llm_config["custom_model_contexts"][provider][model]（per-model 覆盖，P2-2）
    2. LLM_PROVIDERS 内置模型信息（get_model_info）
    3. 回退 DEFAULT_CONTEXT_WINDOW（未知 / context<=0 / 自定义未声明）

    处理 provider/model 前缀与 failover 生效模型（model_override 形如 "provider/model"）。
    """
    from utils.llm_providers import get_model_info

    provider = llm_config.get("provider", "")
    model = model_override or llm_config.get("model", "")
    if "/" in model:
        provider, model = model.split("/", 1)

    # 1) per-model context 覆盖（P2-2）：自定义/未知模型可显式声明，运行时以此为准。
    # 防御容错：provider 值若非 dict（如 list/str 等非法配置），忽略该覆盖不抛错。
    override_map = llm_config.get("custom_model_contexts") or {}
    provider_ctx = override_map.get(provider) or {}
    override_ctx = provider_ctx.get(model) if isinstance(provider_ctx, dict) else None
    if isinstance(override_ctx, int) and override_ctx > 0:
        return override_ctx

    # 2) 内置模型信息
    context = get_model_info(provider, model).get("context", 0)
    if isinstance(context, int) and context > 0:
        return context

    # 3) 保守回退
    return DEFAULT_CONTEXT_WINDOW


def _apply_context_budget(sections: list[tuple], budget_tokens: int) -> tuple[str, list[str]]:
    """全局 Token 预算分配（Issue #70）。

    sections: (name, priority, is_truncatable, text, max_chars, min_chars)
        - priority: 越小越重要；截断时优先裁 priority 数字大的可截断 section。
        - is_truncatable: 是否允许被预算迭代削减。
        - max_chars: 初始字符上限（None=不预截断）。
        - min_chars: 迭代削减下限（0=可整体丢弃）。
    返回 (join 后的有序文本, 存活 section 名列表)。不重写 XML 标签。
    有限终止：无可减少 section（全部达 min / 不可截断）时停并 logger.warning 接受超限。
    """
    cur: list[list] = []
    for name, priority, truncatable, text, max_chars, min_chars in sections:
        if not text:
            continue
        if max_chars is not None and len(text) > max_chars:
            text = text[:max_chars]
        cur.append([name, priority, truncatable, text, min_chars])

    def _total(secs: list[list]) -> int:
        return sum(_estimate_tokens(s[3]) for s in secs)

    if _total(cur) <= budget_tokens:
        return "\n\n".join(s[3] for s in cur), [s[0] for s in cur]

    # 迭代削减：优先裁优先级最低（priority 数字最大）的可截断 section；
    # 在同一优先级内再裁 token 最大的，避免大体积高优先级段被先掏空。
    while True:
        reducible = [s for s in cur if s[2] and len(s[3]) > s[4]]
        if not reducible:
            logger.warning(
                "[AIService] Budget still exceeded: no reducible section below min (accepting overage, ~%d tokens)",
                _total(cur),
            )
            break
        target = max(reducible, key=lambda s: (s[1], _estimate_tokens(s[3])))
        new_len = max(target[4], round(len(target[3]) * 0.5))
        target[3] = target[3][:new_len]
        if _total(cur) <= budget_tokens:
            break

    surviving = [s for s in cur if s[3]]
    return "\n\n".join(s[3] for s in surviving), [s[0] for s in surviving]


class TokenBudgetService:
    """AIService Token 预算子模块（review01-A5b-2）。

    承载 user 内容 token 预算计算（Issue #70）。持有 ``AIService`` 实例经
    ``self._service`` 访问共享配置（``_litellm_config``），本模块不 import
    ``services.ai_service`` 顶层符号以避免循环依赖。
    """

    def __init__(self, service: AIService) -> None:
        self._service = service

    def _compute_analysis_budget(self) -> int:
        """计算 user 内容 token 预算（Issue #70）。

        budget = max(1, primary_context - CONTEXT_RESERVE_TOKENS)。
        以主模型 context 为基准（P2-3 决策）：长上下文主模型不被短 fallback 拖小，
        真正解决 Issue #70"长上下文模型被过度裁剪"的痛点。
        注意：failover 切到更短 context 的 fallback 模型时，本预算不会重算，
        `_chat_completion_litellm` 仅就实际生效模型 context 记录溢出告警，不重裁。
        残余风险（已接受）：短 fallback 模型可能收到超窗 prompt，依 provider 行为处置。
        """
        # 经组合根模块属性访问 ConfigHandler/DataSanitizer：保证测试
        # patch("services.ai_service.ConfigHandler"/"DataSanitizer") 生效。
        import services.ai_service as _ai

        try:
            failover_config = _ai.ConfigHandler.get_failover_config()
        except Exception as exc:
            # 配置读取异常不应阻塞分析：回退保守预算（不截断）。R9 脱敏惯例对齐。
            logger.warning(
                "[AIService] Failover config read failed, using default context budget: %s",
                _ai.DataSanitizer.sanitize_error(exc),
            )
            failover_config = {"primary": "", "fallbacks": []}
        primary = failover_config.get("primary", "")
        # _litellm_config 可能未初始化（如测试以 AIService.__new__ 构造）：
        # 预算计算不应因此阻塞分析，缺失时按默认窗口处理。
        llm_config = getattr(self._service, "_litellm_config", None) or {}
        if primary:
            primary_context = _get_model_context_window(llm_config, model_override=primary)
        else:
            primary_context = DEFAULT_CONTEXT_WINDOW
        return max(1, primary_context - CONTEXT_RESERVE_TOKENS)
