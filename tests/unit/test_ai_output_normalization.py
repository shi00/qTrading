"""股票 final 输出归一化契约（DD-F34 / HANDOFF §11.1）。

冻结语义（唯一归一化入口 ``validate_ai_analysis_response``）：

- raw → normalized 只做一次：``0.005`` 概率口径 → ``0.5`` 百分数，保存（JSON 往返）
  重读后仍为 ``0.5``，不得再次换算为 ``50``；
- ``1`` / ``1.0`` 视为提示词契约下的最小值，不换算；
- ``score`` 不做概率缩放（不放大、不钳位）；缺失 / ``bool`` / ``NaN`` / ``Inf`` 一律
  置 ``None``（R21：不把模型失控输出伪装成业务合法值）；
- ``confidence`` 同上：``bool`` / ``NaN`` / ``Inf`` / 越界置 ``None``；
- ``conclusion_label`` 为权威结论字段，``recommendation`` 仅读兼容别名。
"""

from __future__ import annotations

import json
import math

import pytest

from core.prompt_base import CONCLUSION_LABEL_FIELD
from services.ai_service.output import validate_ai_analysis_response


class TestConfidenceNormalizationOnce:
    """0.005 confidence 只换一次，JSON 往返后仍 0.5。"""

    def test_probability_fraction_scaled_exactly_once(self) -> None:
        result = validate_ai_analysis_response({"score": 88, "confidence": 0.005})
        assert result["confidence"] == 0.5

    def test_normalized_value_survives_json_roundtrip_unchanged(self) -> None:
        first = validate_ai_analysis_response({"score": 88, "confidence": 0.005})
        reread = json.loads(json.dumps(first))
        assert reread["confidence"] == 0.5
        assert reread["score"] == 88

    def test_one_is_not_rescaled(self) -> None:
        assert validate_ai_analysis_response({"score": 50, "confidence": 1})["confidence"] == 1.0
        assert validate_ai_analysis_response({"score": 50, "confidence": 1.0})["confidence"] == 1.0
        assert validate_ai_analysis_response({"score": 50, "confidence": "1.0"})["confidence"] == 1.0

    def test_probability_string_scaled_once(self) -> None:
        assert validate_ai_analysis_response({"score": 50, "confidence": "0.85"})["confidence"] == 85.0

    def test_percent_value_unchanged(self) -> None:
        assert validate_ai_analysis_response({"score": 50, "confidence": 85})["confidence"] == 85.0


class TestScoreNotScaledOrClamped:
    def test_score_preserved_without_probability_scaling(self) -> None:
        result = validate_ai_analysis_response({"score": 88.5})
        assert result["score"] == 88.5

    @pytest.mark.parametrize("raw", [150, -10])
    def test_out_of_range_score_is_none_not_clamped(self, raw: int) -> None:
        assert validate_ai_analysis_response({"score": raw})["score"] is None


class TestMissingBoolNanInfRejected:
    """bool / NaN / Inf / 不可解析一律置 None（R21）。"""

    @pytest.mark.parametrize("raw", [True, False])
    def test_bool_score_rejected(self, raw: bool) -> None:
        assert validate_ai_analysis_response({"score": raw})["score"] is None

    @pytest.mark.parametrize("raw", [True, False])
    def test_bool_confidence_rejected(self, raw: bool) -> None:
        assert validate_ai_analysis_response({"score": 50, "confidence": raw})["confidence"] is None

    @pytest.mark.parametrize("raw", [math.nan, math.inf, -math.inf, "nan", "inf", "-inf"])
    def test_non_finite_score_rejected(self, raw: float | str) -> None:
        assert validate_ai_analysis_response({"score": raw})["score"] is None

    @pytest.mark.parametrize("raw", [math.nan, math.inf, -math.inf, "nan", "inf", "-inf"])
    def test_non_finite_confidence_rejected(self, raw: float | str) -> None:
        assert validate_ai_analysis_response({"score": 50, "confidence": raw})["confidence"] is None

    def test_missing_score_stays_none(self) -> None:
        assert validate_ai_analysis_response({}).get("score") is None

    def test_missing_confidence_stays_none(self) -> None:
        assert validate_ai_analysis_response({"score": 50}).get("confidence") is None


class TestConclusionLabelAuthority:
    def test_conclusion_label_wins_over_recommendation_alias(self) -> None:
        result = validate_ai_analysis_response(
            {"score": 60, CONCLUSION_LABEL_FIELD: "strong_buy", "recommendation": "sell"}
        )
        assert result[CONCLUSION_LABEL_FIELD] == "strong_buy"
        assert result["recommendation"] == "sell"

    def test_invalid_conclusion_label_is_none_even_if_alias_valid(self) -> None:
        result = validate_ai_analysis_response(
            {"score": 60, CONCLUSION_LABEL_FIELD: "strong_moon", "recommendation": "buy"}
        )
        assert result[CONCLUSION_LABEL_FIELD] is None

    def test_alias_maps_when_label_absent(self) -> None:
        result = validate_ai_analysis_response({"score": 60, "recommendation": "BUY"})
        assert result[CONCLUSION_LABEL_FIELD] == "strong_buy"

    def test_alias_hold_maps_to_watchlist(self) -> None:
        result = validate_ai_analysis_response({"score": 60, "recommendation": "hold"})
        assert result[CONCLUSION_LABEL_FIELD] == "watchlist"
