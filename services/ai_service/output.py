"""AIService 输出校验子模块（review01-A5b-1）。

自 ``services/ai_service.py`` 移出的 LLM 自由文本输出清洗（SEC-002）与分析响应校验。
"""

from __future__ import annotations

import logging
import math
import re

from core.prompt_base import (
    CONCLUSION_LABEL_FIELD,
    CONCLUSION_LABELS,
    RECOMMENDATION_TO_CONCLUSION_LABEL,
)
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

VALID_RECOMMENDATIONS = {"buy", "hold", "sell", "strong_buy", "strong_sell", "neutral"}
# SEC-002: Free-text LLM output fields subject to length limit and control-char cleaning.
_FREE_TEXT_MAX_LEN = 1000
_FREE_TEXT_FIELDS = ("summary", "thinking", "ai_reason", "uncertainty_factors")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# R9: 畸形模型字段原值可能夹带个人信息/密钥，日志摘要先脱敏再截断。
_LOG_VALUE_MAX_LEN = 120


def _sanitize_logged_value(value: object) -> str:
    """R9：把模型字段原值转为可安全入日志的摘要（类型 + 脱敏后 repr，超长截断）。

    先脱敏再截断，避免截断把已注册 secret 切成半截绕过精确替换。
    """
    sanitized = DataSanitizer.sanitize_error(repr(value))
    if len(sanitized) > _LOG_VALUE_MAX_LEN:
        sanitized = f"{sanitized[:_LOG_VALUE_MAX_LEN]}...(truncated)"
    return f"{type(value).__name__} {sanitized}"


# ---------------------------------------------------------------------------
# 个股分析输出契约（D13-008 / P2-REV06-LLMOutput-SchemaValidation）
# ---------------------------------------------------------------------------
# 必需键：与 ``core/prompt_base._UNIVERSAL_RULES``【输出格式】「JSON 必须严格包含
# 以下键名」逐键对齐。缺任一键的响应（典型来源：输出被 max_tokens 截断后爬虫式
# 抢救出部分 JSON、或模型幻觉出别名字段）不得被当作有效分析结论。
ANALYSIS_REQUIRED_FIELDS: tuple[str, ...] = (
    CONCLUSION_LABEL_FIELD,
    "score",
    "confidence",
    "uncertainty_factors",
    "thinking",
    "summary",
)
# 可选键：v0 提示词/旧模型返回的读兼容字段（recommendation 为结论别名，
# ai_reason 为旧理由文本）。出现时合法，不出现也不违规。
ANALYSIS_OPTIONAL_FIELDS: tuple[str, ...] = ("recommendation", "ai_reason")
# litellm 适配层合并的计量/溯源元数据键（系统写入，非模型输出）：未知键策略必须
# 保留它们，否则 _accumulate_usage 的真实 usage/cost/model 计量会被丢弃（R21）。
ANALYSIS_METADATA_FIELDS: tuple[str, ...] = ("model", "usage", "cost", "reasoning_content")
ANALYSIS_ALLOWED_FIELDS: frozenset[str] = frozenset(
    ANALYSIS_REQUIRED_FIELDS + ANALYSIS_OPTIONAL_FIELDS + ANALYSIS_METADATA_FIELDS
)


def _is_numeric(value: object) -> bool:
    """数值口径校验：bool 是 int 子类，但 True/False 不是模型给出的分数。"""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return False
    try:
        float(value)
    except (TypeError, ValueError):
        return False
    return True


def validate_analysis_output_contract(response: dict) -> str | None:
    """校验个股分析响应是否满足 prompt 输出契约（不可信模型输入的边界）。

    返回违规原因（``None`` 表示通过）。策略：

    - **必需键**：结论/评分/置信度/不确定性/推理/摘要六键必须存在
      （``conclusion_label`` 允许由 v0 别名 ``recommendation`` 替代，读兼容）；
    - **类型**：``score``/``confidence`` 必须可转数值（bool 拒绝），结论字段必须
      是枚举值，``thinking``/``summary`` 必须是字符串，``uncertainty_factors``
      必须是字符串或列表；
    - **未知键**：不在本函数拒绝，由 :func:`discard_unknown_analysis_fields` 忽略
      并记录（避免幻觉键污染下游，同时不因无关附加键丢弃整条有效分析）。

    取值范围（如 score 越界）不在此判定：由 :func:`validate_ai_analysis_response`
    按 R21 置 None，下游按「未打分」处理，不把失控值伪装成业务结论。
    """
    if not isinstance(response, dict):
        return "non-dict payload"

    missing = [field for field in ANALYSIS_REQUIRED_FIELDS if field not in response]
    if CONCLUSION_LABEL_FIELD in missing and "recommendation" in response:
        missing.remove(CONCLUSION_LABEL_FIELD)
    if missing:
        return "missing required keys: " + ", ".join(missing)

    for field in ("score", "confidence"):
        if not _is_numeric(response[field]):
            return f"{field} is not numeric (type={type(response[field]).__name__})"

    raw_label = response.get(CONCLUSION_LABEL_FIELD)
    if raw_label is None:
        recommendation = response.get("recommendation")
        if (
            not isinstance(recommendation, str)
            or recommendation.lower().strip() not in RECOMMENDATION_TO_CONCLUSION_LABEL
        ):
            return "missing valid conclusion_label/recommendation"
    elif not isinstance(raw_label, str) or raw_label.lower().strip() not in CONCLUSION_LABELS:
        return f"invalid conclusion_label (type={type(raw_label).__name__})"

    uncertainty = response["uncertainty_factors"]
    if not isinstance(uncertainty, (str, list)):
        return f"uncertainty_factors must be str or list (type={type(uncertainty).__name__})"

    for field in ("thinking", "summary"):
        if not isinstance(response[field], str):
            return f"{field} must be str (type={type(response[field]).__name__})"

    return None


def discard_unknown_analysis_fields(response: dict) -> list[str]:
    """移除契约外键（未知键策略：忽略并记录），返回被移除的键名。

    模型幻觉出的键（如 ``reason``、伪造的 ``ai_status``/``error`` 程序字段）不得
    透传到结果行/UI；litellm 计量元数据键（``ANALYSIS_METADATA_FIELDS``）保留。
    """
    unknown = [key for key in response if key not in ANALYSIS_ALLOWED_FIELDS]
    for key in unknown:
        response.pop(key, None)
    return unknown


def _sanitize_free_text(value: str, max_len: int | None = None) -> str:
    """SEC-002: Strip ASCII control chars (except \\t\\n\\r), neutralize angle brackets,
    and truncate free-text LLM output.

    AI-03 写入侧纵深防御：与读取侧（neutralize_external_text）同款尖括号转义，让
    ``<``/``>`` 标签不得入库存/入 UI。控制字符剥离与截断语义保持不变。仅转义尖括号、
    不做 PII 脱敏——本清洗对象是展示给用户的自由文本，不引入 sanitize_pii 的显示语义变化。

    UX-2.2: max_len 可配置。None 时读 ConfigHandler.get_ai_free_text_max_len()。
    """
    if not isinstance(value, str):
        return value
    if max_len is None:
        # 延迟导入避免循环依赖
        from utils.config_handler import ConfigHandler

        max_len = ConfigHandler.get_ai_free_text_max_len()
    cleaned = _CONTROL_CHARS_RE.sub("", value)
    cleaned = cleaned.replace("<", "‹").replace(">", "›")
    if len(cleaned) > max_len:
        logger.warning(
            "[AIService] Output validation: free-text field truncated from %d to %d chars",
            len(cleaned),
            max_len,
        )
        cleaned = cleaned[:max_len]
    return cleaned


def validate_ai_analysis_response(response: dict) -> dict:
    """股票 final 输出归一化的**唯一入口**（DD-F34），可就地规范化后返回同一 dict。

    调用约束：**只可对模型原始响应调用一次**。归一化非幂等——``confidence`` 的
    ``0.5 → 50`` 换算无法在二次调用时区分「模型给的 0.5 比例」与「已归一化的 50
    分置信度」（数值信息已丢失），二次调用会把 0.5 再次放大为 50.0。重试/重放路径
    不得复用同一响应对象二次调用本函数；接链约束登记于 F-01-6。
    """
    if not isinstance(response, dict):
        # R21：非 dict 响应无任何可解析字段，score 必须为 None（未知），不得以 0
        # 伪装成"明确否决"这一业务上合法的评分值。
        return {"error": "Invalid response type", "score": None}

    score = response.get("score")
    if score is not None:
        # DD-F34：bool 是 int 的子类，float(True)=1.0 会让「模型输出 true」伪装成 1 分
        # 这一业务上合法的评分值（R21）。非有限数（NaN/±Inf，含 "nan"/"inf" 字符串）
        # 同样不是评分，必须显式置 None 而非依赖区间比较的偶然结果。
        if isinstance(score, bool):
            logger.warning("[AIService] Output validation: bool score is not a number: %s", score)
            response["score"] = None
        else:
            try:
                score = float(score)
            except (ValueError, TypeError):
                # R21：不可解析的分数不是"0 分否决"，置 None 让下游按"未打分"处理。
                logger.warning("[AIService] Output validation: invalid score type: %s", _sanitize_logged_value(score))
                response["score"] = None
            else:
                if not math.isfinite(score) or not (0 <= score <= 100):
                    # R21：越界/非有限评分是模型失控信号，不是"极端看好/看空"。钳位会把违规
                    # 输出变成最强买入/否决信号，与不可解析分支同样置 None 交由下游按"未打分"。
                    logger.warning(
                        "[AIService] Output validation: score not finite/in range [0,100]: %s",
                        _sanitize_logged_value(score),
                    )
                    response["score"] = None
                else:
                    response["score"] = score

    # AI-02: 模型能力信息——缺失 confidence 记 debug 日志，供排查某模型是否持续不返回置信度。
    raw_confidence = response.get("confidence")
    if raw_confidence is None:
        logger.debug("[AIService] Output validation: response missing 'confidence' field (%s)", response.get("score"))
    elif isinstance(raw_confidence, bool):
        # DD-F34：bool 不是置信度（True 不是 1%、False 不是 0%），置 None 不参与换算。
        logger.warning("[AIService] Output validation: bool confidence is not a number")
        response["confidence"] = None
    else:
        # AI-02 / MINOR-01（R21）：置信度归一化的唯一事实源。提示词约定为 1-100 百分数，但模型
        # 可能按 0~1 概率口径返回；对严格小于 1 的小数按比例换算为百分数（0.85 → 85）。边界值
        # 1 不换算：它在提示词契约下是"最小置信度"，换算成 100 会把合规的最小值放大为满置信度，
        # 与 R21"不把违规输出变成最强信号"的取向相悖。越界（<0 或 >100，或换算后越界）或不可解析
        # 视为模型失控信号，置 None 交由下游按"未给出置信度"处理，**不钳位**（与 score 同语义）。
        try:
            confidence = float(raw_confidence)
        except (ValueError, TypeError):
            logger.warning(
                "[AIService] Output validation: invalid confidence type: %s", _sanitize_logged_value(raw_confidence)
            )
            response["confidence"] = None
        else:
            if not math.isfinite(confidence):
                # DD-F34：NaN/±Inf 不是置信度；区间比较对 NaN 恒假，不能依赖其偶然落入
                # else 分支，显式拒绝（R21）。
                logger.warning("[AIService] Output validation: confidence not finite: %s", raw_confidence)
                response["confidence"] = None
            else:
                if 0 <= confidence < 1:
                    confidence *= 100
                if 0 <= confidence <= 100:
                    response["confidence"] = round(confidence, 1)
                else:
                    logger.warning("[AIService] Output validation: confidence out of range: %s", raw_confidence)
                    response["confidence"] = None

    recommendation = response.get("recommendation")
    if recommendation is not None:
        rec_lower = str(recommendation).lower().strip()
        if rec_lower not in VALID_RECOMMENDATIONS:
            # R21：不认识的推荐值是模型失控/新词，不是"中性"结论。置 None 交由
            # 下游按"未给出建议"处理，不把"模型说了句听不懂的话"伪装成业务结论。
            logger.warning(
                "[AIService] Output validation: unexpected recommendation: %s", _sanitize_logged_value(recommendation)
            )
            response["recommendation"] = None
        else:
            response["recommendation"] = rec_lower

    # MAJOR-01（输出契约统一）：conclusion_label 为权威结论字段，recommendation 仅作读兼容别名。
    # 有 conclusion_label 则以它为准（非法枚举置 None，R21：不把失控输出伪装成业务结论）；
    # 缺省/为 None 时从已规范化的 recommendation 别名映射兜底；两者皆无则显式置 None。
    raw_label = response.get(CONCLUSION_LABEL_FIELD)
    if raw_label is None:
        rec = response.get("recommendation")
        response[CONCLUSION_LABEL_FIELD] = RECOMMENDATION_TO_CONCLUSION_LABEL.get(rec) if isinstance(rec, str) else None
    else:
        label_lower = str(raw_label).lower().strip()
        if label_lower in CONCLUSION_LABELS:
            response[CONCLUSION_LABEL_FIELD] = label_lower
        else:
            logger.warning(
                "[AIService] Output validation: unexpected conclusion_label: %s", _sanitize_logged_value(raw_label)
            )
            response[CONCLUSION_LABEL_FIELD] = None

    # SEC-002: sanitize free-text fields (length limit + control-char cleaning)
    # UX-2.2: 读一次配置避免每字段重复读
    from utils.config_handler import ConfigHandler

    free_text_max_len = ConfigHandler.get_ai_free_text_max_len()
    for field in _FREE_TEXT_FIELDS:
        val = response.get(field)
        if isinstance(val, str):
            response[field] = _sanitize_free_text(val, max_len=free_text_max_len)

    return response
