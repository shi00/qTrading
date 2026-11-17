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


class TestStockAgentCallIdentity:
    """F34/AC23：批次预存身份 + 两层 analyze_stock 透传的单股调用身份。"""

    def test_same_batch_same_stock_is_stable_and_unique_per_stock(self):
        from core.model_management_types import new_stock_agent_batch

        batch = new_stock_agent_batch(strategy_key="oversold", session_id="s-1", turn_id="t-1")
        a1 = batch.for_stock("000001.SZ")
        a2 = batch.for_stock("000001.SZ")
        b = batch.for_stock("000002.SZ")
        assert a1.call_id == a2.call_id  # 同批同股稳定（研究/费用链可回查）
        assert a1.call_id != b.call_id  # 每股唯一
        assert {a1.operation_id, b.operation_id} == {batch.operation_id}
        assert a1.session_id == "s-1" and a1.turn_id == "t-1"

    def test_new_turn_keeps_operation_and_creates_new_call(self):
        from core.model_management_types import new_stock_agent_batch

        batch = new_stock_agent_batch(strategy_key="oversold", turn_id="t-1")
        original = batch.for_stock("000001.SZ")
        retry = batch.for_stock("000001.SZ", new_turn=True)
        assert retry.operation_id == original.operation_id  # 关联原批次/原股
        assert retry.call_id != original.call_id  # 新轮次 = 新调用身份
        assert retry.turn_id != original.turn_id

    def test_missing_identity_rejected(self):
        from core.model_management_types import StockAgentCall, new_stock_agent_batch

        batch = new_stock_agent_batch()
        with pytest.raises(ValueError, match=r"\$\.ts_code"):
            batch.for_stock("  ")
        with pytest.raises(ValueError, match=r"\$\.operation_id"):
            StockAgentCall(operation_id="", call_id="c", ts_code="000001.SZ")

    def test_metadata_projection_keys_are_stable(self):
        from core.model_management_types import new_stock_agent_batch

        call = new_stock_agent_batch(strategy_key="k", session_id="s", turn_id="t").for_stock("000001.SZ")
        meta = call.as_metadata()
        assert meta == {
            "operation_id": call.operation_id,
            "call_id": call.call_id,
            "ts_code": "000001.SZ",
            "strategy_key": "k",
            "session_id": "s",
            "turn_id": "t",
        }

    def test_execution_metadata_carries_identity_and_omits_when_absent(self):
        from unittest.mock import MagicMock

        from core.model_management_types import new_stock_agent_batch
        from services.ai_service.stock_analysis import StockAnalysisService

        dispatch = MagicMock()
        dispatch.route_revision = 3
        dispatch.snapshot.config_revision = 7
        call = new_stock_agent_batch(session_id="s", turn_id="t").for_stock("000001.SZ")

        with_identity = StockAnalysisService._scenario_execution_metadata(
            call.call_id, dispatch, None, None, "unavailable", "failed", agent_call=call
        )
        assert with_identity["invocation_id"] == call.call_id
        assert with_identity["stock_agent_call"] == call.as_metadata()

        without_identity = StockAnalysisService._scenario_execution_metadata(
            "inv-x", dispatch, None, None, "unavailable", "failed"
        )
        assert "stock_agent_call" not in without_identity  # 旧兼容调用不伪造身份（R21）

    def test_attach_agent_call_keeps_identity_on_failure_result(self):
        from services.ai_service.stock_analysis import StockAnalysisService

        call = _call()
        failure = {"error": "Analysis timeout", "score": None, "ai_status": "failed"}
        result = StockAnalysisService._attach_agent_call(failure, call)
        assert isinstance(result, dict)
        assert result is failure
        assert result["stock_agent_call"] == call.as_metadata()
        # 无身份不写键
        without_identity = StockAnalysisService._attach_agent_call(
            {"error": "Analysis timeout", "score": None, "ai_status": "failed"}, None
        )
        assert isinstance(without_identity, dict)
        assert "stock_agent_call" not in without_identity


@pytest.mark.asyncio
async def test_execute_scenario_analysis_uses_call_identity_for_invocation_and_run_id():
    """新链：call_id → invocation_id、operation_id → request.run_id（研究/费用链同源）。"""
    from unittest.mock import AsyncMock, MagicMock

    from core.model_management_types import CandidateDecision, ScenarioExecutionResult
    from services.ai_service.scenario_executor import ScenarioOutput
    from services.ai_service.stock_analysis import StockAnalysisService

    call = _call()
    exec_result = ScenarioExecutionResult(
        scenario_id="stock_analysis",
        invocation_id=call.call_id,
        status="succeeded",
        outputs=(ScenarioOutput(output={"score": 80, "confidence": 60}, metadata={"actual_model": "m-1"}),),
        candidate_decisions=(
            CandidateDecision(
                task_id="analysis", candidate_index=0, connection_id="conn-1", decision="succeeded", reason="ok"
            ),
        ),
        actual_usage=(),
    )
    dispatch = MagicMock()
    dispatch.route_revision = 3
    dispatch.total_timeout_seconds = 30.0
    dispatch.snapshot.config_revision = 7
    dispatch.executor.execute = AsyncMock(return_value=exec_result)
    service = StockAnalysisService(MagicMock())

    result = await service._execute_scenario_analysis(
        dispatch=dispatch,
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
        learning_meta=None,
        agent_call=call,
    )

    request = dispatch.executor.execute.call_args[0][0]
    assert request.invocation_id == call.call_id
    assert request.run_id == call.operation_id
    assert result["execution_metadata"]["stock_agent_call"] == call.as_metadata()


def _call():
    from core.model_management_types import new_stock_agent_batch

    return new_stock_agent_batch(session_id="s-1", turn_id="t-1").for_stock("000001.SZ")
