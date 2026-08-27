"""AIService 新闻分类子模块（review01-A5b-2）。

自 ``services/ai_service.py`` 移出的 ``classify_news`` / ``_parse_news_result``
完整方法体及其专属常量（``NEWS_TEXT_MAX_LEN``）。

设计约定（测试兼容）：
- ``NewsClassifier`` 经构造参数持有 ``AIService`` 实例，经 ``self._service``
  访问共享状态（``_chat_completion``），本模块不 import ``services.ai_service``
  顶层符号，避免循环依赖。
- 跨子模块方法一律经组合根（``self._service``）调用，保证测试对 ``AIService``
  实例属性（如 ``svc._chat_completion = AsyncMock(...)``）的 monkeypatch 生效。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from utils.config_models import NEWS_CATEGORY_MAP
from utils.error_classifier import log_classified
from utils.log_decorators import PerfThreshold, log_async_operation

if TYPE_CHECKING:
    from services.ai_service import AIService

logger = logging.getLogger(__name__)

# classify_news 中外部新闻原文长度截断
NEWS_TEXT_MAX_LEN = 500


class NewsClassifier:
    """AIService 新闻分类子模块（review01-A5b-2）。

    承载 ``classify_news`` / ``_parse_news_result`` 完整方法体。持有 ``AIService``
    实例经 ``self._service`` 访问共享状态（``_chat_completion``），本模块不 import
    ``services.ai_service`` 顶层符号以避免循环依赖。
    """

    def __init__(self, service: AIService) -> None:
        self._service = service

    def _parse_news_result(self, raw_result: dict) -> dict:
        """
        Helper to normalize news classification result.
        Handles the L1/L2 category logic to provide a clean 'category' string for UI.
        L1/L2 codes are English enum values returned by the AI prompt,
        translated to locale-specific display names via I18n.

        防御性策略 (不信任 AI 响应):
        1. 输入归一化: strip + lower，应对 AI 大小写/空白波动
        2. 词典校验: L1 必须在 NEWS_CATEGORY_MAP，L2 必须在反向映射中
        3. 错位纠正: AI 将 L2 放到 L1 位置时，通过反向映射推导正确 L1
        4. L2 推导 L1: L1 无效但 L2 有效时，通过 L2 反推 L1
        5. 安全兜底: 任何无效层级不暴露英文编码，降级为本地化"资讯"
        """
        from core.i18n import I18n

        # 1. 输入归一化 (None 值经 `or ""` 转为空串，避免 str(None)="none" 被当作无效编码处理)
        l1_code = (raw_result.get("category_L1") or "").strip().lower()
        l2_code = (raw_result.get("category_L2") or "").strip().lower()

        # 2. 构建反向映射 (L2 -> L1)
        l2_to_l1_map: dict[str, str] = {}
        for l1, l2_list in NEWS_CATEGORY_MAP.items():
            for l2 in l2_list:
                l2_to_l1_map[l2] = l1

        is_valid_l1 = l1_code in NEWS_CATEGORY_MAP
        is_valid_l2 = l2_code in l2_to_l1_map

        # 3. 错位纠正: AI 错把 L2 作为 L1 输出 (例如 category_L1="macro_policy")
        if l1_code and l1_code in l2_to_l1_map and not is_valid_l1:
            if not l2_code:
                l2_code = l1_code
            l1_code = l2_to_l1_map[l1_code]
            is_valid_l1 = True
            is_valid_l2 = l2_code in l2_to_l1_map

        # 4. L2 推导 L1: L1 彻底错乱但 L2 合法
        if is_valid_l2 and not is_valid_l1:
            l1_code = l2_to_l1_map[l2_code]
            is_valid_l1 = True

        # 5. 翻译为本地化展示名
        l1_display = I18n.get(f"news_l1_{l1_code}", l1_code) if l1_code else ""
        l2_display = I18n.get(f"news_l2_{l2_code}", l2_code) if l2_code else ""

        # 6. 安全兜底隔离: L1 非法或缺少语言包退回原始英文 → 降级为"资讯"
        if not is_valid_l1 or (l1_code and l1_display == l1_code):
            l1_display = I18n.get("news_fallback_category", "Other")

        # 7. 安全兜底隔离: L2 非法或缺少语言包退回原始英文 → 完全剔除
        if not is_valid_l2 or (l2_code and l2_display == l2_code):
            l2_display = ""

        # 8. 拼接返回
        if l2_display and l1_display:
            final_category = f"{l1_display}-{l2_display}"
        elif l1_display:
            final_category = l1_display
        else:
            final_category = I18n.get("news_fallback_category", "Other")

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

        P3-6 设计取舍：外部新闻原文 ``text`` 仅做长度截断（``NEWS_TEXT_MAX_LEN``），
        未走 ``neutralize_external_text`` 中性化（与 ``analyze_stock`` 不一致）。
        残余展示层污染属已知简化，靠三层防御兜底：
        1. JSON mode：LLM 输出强制 JSON 结构，限制自由文本污染面；
        2. 白名单校验：``_parse_news_result`` 仅接受 ``NEWS_CATEGORY_MAP`` 内的
           L1/L2 枚举值，无效编码降级为本地化"资讯"；
        3. 兜底降级：解析失败回退默认 Neutral 语义，避免污染下游消费方。

        升级触发条件：若新增展示层字段直接 echo LLM 原文（如 reason 段落），
        必须补 ``neutralize_external_text`` 调用对齐 ``analyze_stock`` 范式。
        """
        # 经组合根模块属性访问 ConfigHandler/DataSanitizer：保证测试
        # patch("services.ai_service.ConfigHandler"/"DataSanitizer") 生效。
        import services.ai_service as _ai

        system_instruction = _ai.ConfigHandler.get_ai_news_prompt()
        # NOTE(lazy): classify_news 未走 neutralize_external_text, 仅长度截断. ceiling: 三层防御(JSON mode+白名单+兜底降级). upgrade: 新增展示层字段 echo LLM 原文时补 neutralize_external_text.
        messages = [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": text[:NEWS_TEXT_MAX_LEN]},
        ]

        # 1. Try Local Model
        try:
            raw_result = await self._service._chat_completion(
                messages,
                provider="local",
                json_mode=True,
            )
            result = self._parse_news_result(raw_result)
            logger.debug(
                "[AIService] Classify | Local ✅ %s / %s",
                result.get("category"),
                result.get("sentiment"),
            )
            return result
        except Exception as local_e:
            # Local failed (not configured, crash, etc.)
            # Log only if it wasn't just "not configured" (which is common)
            if "not installed" not in str(local_e) and "not configured" not in str(
                local_e,
            ):
                log_classified(
                    logger,
                    local_e,
                    "llm",
                    "[AIService] Classify | Local failed, falling back to cloud (%s): %s",
                    exc_info=True,
                )
            else:
                log_classified(
                    logger,
                    local_e,
                    "llm",
                    "[AIService] Classify | Local model unavailable, falling back to cloud (%s): %s",
                    exc_info=True,
                )

        # 2. Fallback to Cloud
        try:
            # Enforce global 5s timeout? The original code had per-call timeout.
            # _chat_completion has default 30s. classify used to wrap in wait_for 30s.
            # Inner cloud call had 30s timeout on client.
            # We will use 30s default.
            raw_result = await self._service._chat_completion(
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
            log_classified(
                logger,
                e,
                "llm",
                "[AIService] Classify | ❌ All providers failed (%s): %s",
                exc_info=True,
            )
            logger.debug("[AIService] Classify | All providers failed traceback:", exc_info=True)
            return {"category": "unknown", "sentiment": "neutral", "error": _ai.DataSanitizer.sanitize_error(e)}
