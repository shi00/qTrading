"""个股分析输出契约单测（D13-008 / P2-REV06-LLMOutput-SchemaValidation）。

覆盖 prompt 输出契约（``core/prompt_base._UNIVERSAL_RULES``【输出格式】）在服务侧
边界的三类约束：

1. 必需键缺失 → 违规原因（由 stock_analysis 边界转为显式失败）；
2. 类型错误（非数值评分/置信度、非法结论枚举、自由文本非字符串）→ 违规原因；
3. 契约外未知键 → 忽略并记录（程序字段/幻觉键不得透传），计量元数据保留。

取值越界（如 score=150）不属契约违规：由 ``validate_ai_analysis_response`` 按 R21
置 None，本文件同时锁定该职责边界。
"""

from __future__ import annotations

import re

import pytest

from services.ai_service.output import (
    ANALYSIS_ALLOWED_FIELDS,
    ANALYSIS_REQUIRED_FIELDS,
    discard_unknown_analysis_fields,
    validate_ai_analysis_response,
    validate_analysis_output_contract,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _free_text_limit(monkeypatch):
    """契约通过后若进入归一化，自由文本清洗会读配置；固定之。"""
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.get_ai_free_text_max_len",
        staticmethod(lambda: 1000),
    )


def _valid_payload() -> dict:
    """v1 契约（conclusion_label 为权威结论字段）的完整合法响应。"""
    return {
        "conclusion_label": "strong_buy",
        "score": 82,
        "confidence": 70,
        "uncertainty_factors": ["估值偏高"],
        "thinking": "推理过程简洁说明",
        "summary": "逢低买入",
    }


class TestRequiredKeys:
    def test_full_v1_payload_passes(self):
        assert validate_analysis_output_contract(_valid_payload()) is None

    def test_required_keys_match_prompt_output_format(self):
        """防契约漂移：必需键集合必须与 prompt【输出格式】编号列表逐键一致。"""
        from core.prompt_base import _UNIVERSAL_RULES

        block = _UNIVERSAL_RULES.split("【输出格式】", 1)[1]
        prompt_keys = set(re.findall(r"^\d+\.\s*(\w+)：", block, flags=re.MULTILINE))

        assert prompt_keys == set(ANALYSIS_REQUIRED_FIELDS)

    def test_legacy_v0_recommendation_alias_passes(self):
        """合法多版本：v0 以 recommendation 作为结论别名，不要求 conclusion_label。"""
        payload = _valid_payload()
        payload.pop("conclusion_label")
        payload["recommendation"] = "buy"

        assert validate_analysis_output_contract(payload) is None

    @pytest.mark.parametrize(
        "missing",
        ["score", "confidence", "uncertainty_factors", "thinking", "summary"],
    )
    def test_missing_required_display_keys_rejected(self, missing):
        payload = _valid_payload()
        payload.pop(missing)

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "missing required keys" in reason
        assert missing in reason

    def test_missing_conclusion_label_without_alias_rejected(self):
        """conclusion_label 缺失且无 recommendation 别名 → 无结论字段，拒绝。"""
        payload = _valid_payload()
        payload.pop("conclusion_label")

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "missing required keys" in reason
        assert "conclusion_label" in reason

    def test_non_dict_payload_rejected(self):
        assert validate_analysis_output_contract(["score"]) == "non-dict payload"


class TestFieldTypes:
    @pytest.mark.parametrize("bad_score", ["abc", None, True, {"v": 1}, [80]])
    def test_non_numeric_score_rejected(self, bad_score):
        payload = _valid_payload()
        payload["score"] = bad_score

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "score is not numeric" in reason

    @pytest.mark.parametrize("bad_confidence", ["high", None, False, {}])
    def test_non_numeric_confidence_rejected(self, bad_confidence):
        payload = _valid_payload()
        payload["confidence"] = bad_confidence

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "confidence is not numeric" in reason

    @pytest.mark.parametrize("label", ["maybe", "", 1, ["strong_buy"]])
    def test_invalid_conclusion_label_rejected(self, label):
        payload = _valid_payload()
        payload["conclusion_label"] = label

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "conclusion_label" in reason

    def test_invalid_recommendation_alias_rejected(self):
        payload = _valid_payload()
        payload.pop("conclusion_label")
        payload["recommendation"] = "maybe"

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "conclusion_label/recommendation" in reason

    @pytest.mark.parametrize("bad_uncertainty", [None, {"risk": 1}, 3])
    def test_uncertainty_type_error_rejected(self, bad_uncertainty):
        payload = _valid_payload()
        payload["uncertainty_factors"] = bad_uncertainty

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert "uncertainty_factors" in reason

    @pytest.mark.parametrize("field", ["thinking", "summary"])
    def test_free_text_type_error_rejected(self, field):
        payload = _valid_payload()
        payload[field] = 123

        reason = validate_analysis_output_contract(payload)

        assert reason is not None
        assert field in reason

    def test_out_of_range_score_is_not_contract_violation(self):
        """越界属 R21 归一化职责（置 None），不在此判违规——避免职责重叠。"""
        payload = _valid_payload()
        payload["score"] = 150

        assert validate_analysis_output_contract(payload) is None
        assert validate_ai_analysis_response(payload)["score"] is None


class TestUnknownKeys:
    def test_unknown_keys_do_not_fail_contract(self):
        payload = _valid_payload()
        payload["reason"] = "模型自造字段"

        assert validate_analysis_output_contract(payload) is None

    def test_unknown_keys_discarded_and_metadata_preserved(self):
        payload = _valid_payload()
        payload.update(
            {
                "reason": "模型自造字段",
                "ai_status": "failed",
                "error": "伪造错误",
                "model": "glm-4-air",
                "usage": {"total_tokens": 12},
                "cost": 0.001,
                "reasoning_content": "草稿",
            }
        )

        dropped = discard_unknown_analysis_fields(payload)

        assert set(dropped) == {"reason", "ai_status", "error"}
        assert "reason" not in payload and "ai_status" not in payload and "error" not in payload
        # 计量/溯源元数据必须保留（R21：真实 usage/cost/model 不得被未知键策略丢弃）
        assert payload["model"] == "glm-4-air"
        assert payload["usage"] == {"total_tokens": 12}
        assert payload["cost"] == 0.001
        assert payload["reasoning_content"] == "草稿"

    def test_allowed_fields_are_contract_union(self):
        assert {"conclusion_label", "recommendation", "ai_reason"} <= ANALYSIS_ALLOWED_FIELDS
        assert "reason" not in ANALYSIS_ALLOWED_FIELDS
