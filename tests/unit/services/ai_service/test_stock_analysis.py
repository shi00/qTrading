"""stock_analysis 输出边界契约单测（D13-008）。

靶点为两条消费链共用的输出边界：

- 新链 ``_validate_analysis_output``（Executor 校验器）：非 JSON / 截断 JSON /
  非 dict / 缺必需键 / 类型错 → 抛 ``ModelOutputValidationError``，由 Executor
  切换候选，不把坏响应当成功；
- 旧链 ``_finalize_legacy_analysis_output``：同样违规 → 显式失败 dict
  （``error`` + ``ai_status="failed"``，score/confidence 保持 None），由
  ``_build_result_row`` 归入 failed；
- 契约外未知键忽略并记录，计量元数据（usage/cost/model）保留；
- 合法多版本（v1 conclusion_label / v0 recommendation 别名）均接受。
"""

from __future__ import annotations

import json

import pytest

from core.model_management_types import AdapterResponse, ModelOutputValidationError
from services.ai_service.stock_analysis import (
    _finalize_legacy_analysis_output,
    _validate_analysis_output,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _free_text_limit(monkeypatch):
    monkeypatch.setattr(
        "utils.config_handler.ConfigHandler.get_ai_free_text_max_len",
        staticmethod(lambda: 1000),
    )


def _payload() -> dict:
    return {
        "conclusion_label": "strong_buy",
        "score": 82,
        "confidence": 70,
        "uncertainty_factors": ["估值偏高"],
        "thinking": "推理过程",
        "summary": "逢低买入",
    }


def _response(content: str | None) -> AdapterResponse:
    return AdapterResponse(content=content, usage=None, actual_model="glm-4-air", metadata={"connection_id": "conn-1"})


class TestScenarioChainValidator:
    def test_valid_v1_output_passes(self):
        result = _validate_analysis_output(_response(json.dumps(_payload())))

        assert result.output["score"] == 82.0
        assert result.output["confidence"] == 70.0
        assert result.output["conclusion_label"] == "strong_buy"
        assert result.metadata["actual_model"] == "glm-4-air"
        assert result.metadata["connection_id"] == "conn-1"

    def test_legacy_recommendation_alias_accepted(self):
        payload = _payload()
        payload.pop("conclusion_label")
        payload["recommendation"] = "hold"

        result = _validate_analysis_output(_response(json.dumps(payload)))

        assert result.output["conclusion_label"] == "watchlist"

    def test_empty_content_rejected(self):
        with pytest.raises(ModelOutputValidationError, match="empty content"):
            _validate_analysis_output(_response(""))

    def test_truncated_json_rejected(self):
        truncated = (
            '{"conclusion_label": "strong_buy", "score": 82, "confidence": 70, '
            '"uncertainty_factors": ["估值偏高"], "thinking": "推理过程", "summary": "逢低'
        )

        with pytest.raises(ModelOutputValidationError, match="invalid json"):
            _validate_analysis_output(_response(truncated))

    def test_non_json_rejected(self):
        with pytest.raises(ModelOutputValidationError, match="invalid json"):
            _validate_analysis_output(_response("抱歉，我无法完成该请求。"))

    def test_non_dict_payload_rejected(self):
        with pytest.raises(ModelOutputValidationError, match="non-dict payload"):
            _validate_analysis_output(_response("[82, 70]"))

    def test_missing_required_key_rejected(self):
        payload = _payload()
        payload.pop("summary")

        with pytest.raises(ModelOutputValidationError, match="missing required keys"):
            _validate_analysis_output(_response(json.dumps(payload)))

    def test_type_error_rejected(self):
        payload = _payload()
        payload["score"] = "很高"

        with pytest.raises(ModelOutputValidationError, match="score is not numeric"):
            _validate_analysis_output(_response(json.dumps(payload)))

    def test_unknown_keys_dropped_from_output(self):
        payload = _payload()
        payload.update({"reason": "自造", "ai_status": "succeeded", "error": "伪造"})

        result = _validate_analysis_output(_response(json.dumps(payload)))

        assert "reason" not in result.output
        assert "ai_status" not in result.output
        assert "error" not in result.output
        assert result.output["score"] == 82.0

    def test_out_of_range_score_normalized_to_none(self):
        """R21：越界评分不是契约违规，但也不得成为业务结论。"""
        payload = _payload()
        payload["score"] = 150

        result = _validate_analysis_output(_response(json.dumps(payload)))

        assert result.output["score"] is None


class TestLegacyChainBoundary:
    def test_valid_output_normalized(self):
        result = _finalize_legacy_analysis_output(_payload())

        assert result["score"] == 82.0
        assert result["confidence"] == 70.0
        assert result["conclusion_label"] == "strong_buy"
        assert "error" not in result

    def test_truncated_partial_object_leads_to_explicit_failure(self):
        """抢救式提取出的部分对象（缺 summary/confidence 等）不得静默通过。"""
        partial = {"conclusion_label": "strong_buy", "score": 82}

        result = _finalize_legacy_analysis_output(partial)

        assert result["ai_status"] == "failed"
        assert result["score"] is None
        assert result["confidence"] is None
        assert "missing required keys" in result["error"]

    def test_rejected_output_keeps_usage_metadata(self):
        """拒绝坏输出不等于零成本：usage/cost/model 必须保留供 _accumulate_usage 记账。"""
        partial = {
            "score": 82,
            "usage": {"total_tokens": 99},
            "cost": None,
            "model": "glm-4-air",
        }

        result = _finalize_legacy_analysis_output(partial)

        assert result["ai_status"] == "failed"
        assert result["usage"] == {"total_tokens": 99}
        assert result["cost"] is None
        assert result["model"] == "glm-4-air"

    def test_type_error_leads_to_explicit_failure(self):
        payload = _payload()
        payload["confidence"] = "high"

        result = _finalize_legacy_analysis_output(payload)

        assert result["ai_status"] == "failed"
        assert result["score"] is None
        assert "confidence is not numeric" in result["error"]

    def test_unknown_keys_dropped_and_metadata_preserved(self):
        payload = _payload()
        payload.update(
            {
                "reason": "自造",
                "ai_status": "failed",
                "error": "伪造错误",
                "model": "qwen/qwen-max",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "cost": 0.002,
            }
        )

        result = _finalize_legacy_analysis_output(payload)

        assert result["score"] == 82.0
        assert "reason" not in result
        assert "error" not in result
        # 模型伪造的 ai_status 被丢弃后由消费侧按 score 重新判定（非 failed）
        assert "ai_status" not in result
        assert result["model"] == "qwen/qwen-max"
        assert result["usage"]["total_tokens"] == 15
        assert result["cost"] == 0.002
