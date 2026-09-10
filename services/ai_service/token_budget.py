"""AIService Token 预算子模块（review01-A5b-1）。

自 ``services/ai_service.py`` 移出的 token 估算 / 模型上下文窗口解析 / 全局预算裁剪，
承载 tiktoken 模块级缓存（R7 测试隔离经 ``_reset_token_estimator``）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from core.errors import AIBudgetError
from core.i18n import Message

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
# 输出预留 token：为模型生成分析报告 JSON 结论预留基础空间（D5-4）。
OUTPUT_RESERVE_TOKENS = 4000
# 回退估算分母：tiktoken 不可用/离线时分段估算（CJK 1:1，非 CJK 4:1，Issue D5-8）。
CHAR_FALLBACK_NON_CJK_DIV = 4

# CJK 字符集区间定义（含基本汉字、扩展区、CJK标点与全角字符）
_CJK_RANGES = (
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0x3400, 0x4DBF),  # CJK Extension A
    (0x20000, 0x2A6DF),  # CJK Extension B
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x3000, 0x303F),  # CJK Symbols and Punctuation (全角标点)
    (0xFF00, 0xFFEF),  # Halfwidth and Fullwidth Forms (全角ASCII与标点)
)


def _estimate_tokens_fallback(text: str) -> int:
    """tiktoken 不可用时的分段保守估算（Issue D5-8）。

    cl100k_base 实测：
    - CJK 字符（含汉字与全角标点）：约 1 字符/token（高估约 1.08 倍，保守安全）。
    - 非 CJK（拉丁英文、数字、ASCII 符号）：约 4 字符/token。
    历史实现采用单一分母(=1)对纯英文高估 5.56 倍，导致 token 预算被过度裁剪。
    """
    if not text:
        return 0
    cjk_count = 0
    for ch in text:
        cp = ord(ch)
        if any(lo <= cp <= hi for lo, hi in _CJK_RANGES):
            cjk_count += 1
    non_cjk_count = len(text) - cjk_count
    non_cjk_tokens = (non_cjk_count + 3) // CHAR_FALLBACK_NON_CJK_DIV if non_cjk_count > 0 else 0
    return cjk_count + non_cjk_tokens


_tiktoken_enc = None
_tiktoken_enc_error = False


def _reset_token_estimator() -> None:
    """清除 tiktoken 模块缓存（测试隔离用，R7 合规）。"""
    global _tiktoken_enc, _tiktoken_enc_error
    _tiktoken_enc = None
    _tiktoken_enc_error = False


def _estimate_tokens(text) -> int:
    """估算文本 token 数（Issue #70, D5-8）。

    - None/空文本 → 0
    - 非 str（多模态 list content parts）递归求和其中 str 的 text 部分
    - 惰性初始化 tiktoken cl100k_base；异常/离线回退 _estimate_tokens_fallback
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
    return _estimate_tokens_fallback(text)


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


class BudgetResult(tuple):
    """Token 预算裁剪结果元组（兼容 (user_prompt, surviving_names) 双元素解包）。

    扩展提供 section_map 映射（{section_name: section_text}），
    供调用方实现平级 XML 容器组装（D5-2）。
    """

    section_map: dict[str, str]

    def __new__(cls, user_prompt: str, surviving_names: list[str], section_map: dict[str, str]):
        obj = super().__new__(cls, (user_prompt, surviving_names))
        obj.section_map = section_map
        return obj


def _apply_context_budget(sections: list[tuple], budget_tokens: int) -> BudgetResult:
    """全局 Token 预算分配（Issue #70）。

    sections: (name, priority, is_truncatable, text, max_chars, min_chars)
        - priority: 越小越重要；截断时优先裁 priority 数字大的可截断 section。
        - is_truncatable: 是否允许被预算迭代削减。
        - max_chars: 初始字符上限（None=不预截断）。
        - min_chars: 迭代削减下限（0=可整体丢弃）。
    返回 BudgetResult (join 后的有序文本, 存活 section 名列表, 存活映射)。不重写 XML 标签。
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
        return BudgetResult(
            "\n\n".join(s[3] for s in cur),
            [s[0] for s in cur],
            {s[0]: s[3] for s in cur},
        )

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
    return BudgetResult(
        "\n\n".join(s[3] for s in surviving),
        [s[0] for s in surviving],
        {s[0]: s[3] for s in surviving},
    )


class TokenBudgetService:
    """AIService Token 预算子模块（review01-A5b-2）。

    承载 user 内容 token 预算计算（Issue #70）。持有 ``AIService`` 实例经
    ``self._service`` 访问共享配置（``_litellm_config``），本模块不 import
    ``services.ai_service`` 顶层符号以避免循环依赖。
    """

    def __init__(self, service: AIService) -> None:
        self._service = service

    def _compute_analysis_budget(
        self,
        system_messages: list[dict] | None = None,
        fixed_blocks: str | list[str] | None = None,
        reserved_output_tokens: int = OUTPUT_RESERVE_TOKENS,
    ) -> int:
        """计算 user 内容 token 预算（Issue #70 / D5-4）。

        从模型窗口逐层扣减：窗口 − 输出预留 − system 消息 − 不可裁剪固定块。
        未显式传入 system_messages/fixed_blocks 时向后兼容回退至保守常数预留。
        以主模型 context 为基准（P2-3 决策）：长上下文主模型不被短 fallback 拖小。
        """
        # 经组合根模块属性访问 ConfigHandler/DataSanitizer：保证测试
        # patch("services.ai_service.ConfigHandler"/"DataSanitizer") 生效。
        import services.ai_service as _ai

        try:
            failover_config = _ai.ConfigHandler.get_failover_config()
        except Exception as exc:
            # 配置读取异常不应阻塞分析：回退保守预算（不截断）。R9 脱敏惯例对齐。
            logger.warning(
                "[AIService] Failover config read failed, falling back to active/default context budget: %s",
                _ai.DataSanitizer.sanitize_error(exc),
            )
            failover_config = {"primary": "", "fallbacks": []}
        primary = failover_config.get("primary", "")
        primary_override = primary.strip() if isinstance(primary, str) and primary.strip() else None
        # _litellm_config 可能未初始化（如测试以 AIService.__new__ 构造）：
        # 预算计算不应因此阻塞分析，缺失时按默认窗口处理。
        llm_config = getattr(self._service, "_litellm_config", None) or {}
        # 未配置 failover 时（primary 为空），以当前生效模型（llm_config）为准，而非硬编码默认窗口（D5-3）
        primary_context = _get_model_context_window(llm_config, model_override=primary_override)

        if system_messages is None and fixed_blocks is None:
            # 向后兼容：未提供固定块时，按原保守常数预留扣除
            return max(1, primary_context - CONTEXT_RESERVE_TOKENS)

        # D5-4：精准扣除 system 消息与不可裁剪固定块
        system_tokens = sum(
            _estimate_tokens(m.get("content", "")) for m in (system_messages or []) if isinstance(m, dict)
        )
        if isinstance(fixed_blocks, list):
            fixed_tokens = sum(_estimate_tokens(b) for b in fixed_blocks if b)
        elif isinstance(fixed_blocks, str):
            fixed_tokens = _estimate_tokens(fixed_blocks)
        else:
            fixed_tokens = 0

        deduct_tokens = system_tokens + fixed_tokens + max(0, reserved_output_tokens)
        budget = primary_context - deduct_tokens
        if budget < 1:
            # D5-4：固定提示词 + 输出预留超出模型上下文窗口时，显式抛可操作异常
            # （而非静默 capping 到 1）。用户自定义长提示词突破余量的情形下，静默
            # 降级会让 API 400 却无法归因；显式抛出携带"占用 token / 模型窗口"，
            # 向用户呈现"自定义提示词过长"的可操作信息。
            raise AIBudgetError(
                Message(
                    "ai_prompt_too_long",
                    {
                        "tokens": deduct_tokens,
                        "window": primary_context,
                    },
                ),
                detail=(
                    f"Fixed prompt + output reserve ({deduct_tokens} tokens) exceeds model "
                    f"context window ({primary_context} tokens)"
                ),
            )
        return budget
