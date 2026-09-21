"""AIService 输出校验子模块（review01-A5b-1）。

自 ``services/ai_service.py`` 移出的 LLM 自由文本输出清洗（SEC-002）与分析响应校验。
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

VALID_RECOMMENDATIONS = {"buy", "hold", "sell", "strong_buy", "strong_sell", "neutral"}
# SEC-002: Free-text LLM output fields subject to length limit and control-char cleaning.
_FREE_TEXT_MAX_LEN = 1000
_FREE_TEXT_FIELDS = ("summary", "thinking", "ai_reason", "uncertainty_factors")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _sanitize_free_text(value: str, max_len: int | None = None) -> str:
    """SEC-002: Strip ASCII control chars (except \\t\\n\\r) and truncate free-text LLM output.

    UX-2.2: max_len 可配置。None 时读 ConfigHandler.get_ai_free_text_max_len()。
    """
    if not isinstance(value, str):
        return value
    if max_len is None:
        # 延迟导入避免循环依赖
        from utils.config_handler import ConfigHandler

        max_len = ConfigHandler.get_ai_free_text_max_len()
    cleaned = _CONTROL_CHARS_RE.sub("", value)
    if len(cleaned) > max_len:
        logger.warning(
            "[AIService] Output validation: free-text field truncated from %d to %d chars",
            len(cleaned),
            max_len,
        )
        cleaned = cleaned[:max_len]
    return cleaned


def validate_ai_analysis_response(response: dict) -> dict:
    if not isinstance(response, dict):
        return {"error": "Invalid response type", "score": 0}

    score = response.get("score")
    if score is not None:
        try:
            score = float(score)
            if not (0 <= score <= 100):
                # R21：越界评分是模型失控信号，不是"极端看好/看空"。钳位会把违规输出
                # 变成最强买入/否决信号，与不可解析分支同样置 None 交由下游按"未打分"处理。
                logger.warning("[AIService] Output validation: score out of range [0,100]: %s", score)
                response["score"] = None
            else:
                response["score"] = score
        except (ValueError, TypeError):
            # R21：不可解析的分数不是"0 分否决"，置 None 让下游按"未打分"处理。
            logger.warning("[AIService] Output validation: invalid score type: %s", score)
            response["score"] = None

    # AI-02: 模型能力信息——缺失 confidence 记 debug 日志，供排查某模型是否持续不返回置信度。
    if response.get("confidence") is None:
        logger.debug("[AIService] Output validation: response missing 'confidence' field (%s)", response.get("score"))

    recommendation = response.get("recommendation")
    if recommendation is not None:
        rec_lower = str(recommendation).lower().strip()
        if rec_lower not in VALID_RECOMMENDATIONS:
            # R21：不认识的推荐值是模型失控/新词，不是"中性"结论。置 None 交由
            # 下游按"未给出建议"处理，不把"模型说了句听不懂的话"伪装成业务结论。
            logger.warning("[AIService] Output validation: unexpected recommendation: %s", recommendation)
            response["recommendation"] = None
        else:
            response["recommendation"] = rec_lower

    # SEC-002: sanitize free-text fields (length limit + control-char cleaning)
    # UX-2.2: 读一次配置避免每字段重复读
    from utils.config_handler import ConfigHandler

    free_text_max_len = ConfigHandler.get_ai_free_text_max_len()
    for field in _FREE_TEXT_FIELDS:
        val = response.get(field)
        if isinstance(val, str):
            response[field] = _sanitize_free_text(val, max_len=free_text_max_len)

    return response
