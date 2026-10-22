"""S2 gguf_sentiment_adapter 薄转换层测试（ADR-0014 §6.1）。

覆盖：
- ScoringOutcome → AdapterResponse 的六态转译（success/partial 返回响应；
  timeout → TimeoutError；unavailable/worker_error → RuntimeError；
  invalid_output → ModelOutputValidationError）；
- results JSON 序列化形状（probabilities tuple → list；None 保持 null，R21）；
- invoke 薄转换：payload texts 校验、adapter_kind 校验、参数透传 scorer、
  不另管进程 / 不选候选 / 不单独记费用（单例 scorer 复用断言）。
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.ai_service.gguf_sentiment_adapter import invoke
from core.model_management_types import (
    AdapterResponse,
    AttemptContext,
    InferenceRequest,
    ModelOutputValidationError,
    NormalizedLLMUsage,
    ResolvedConnection,
    ScoredText,
    ScoringOutcome,
)


def _attempt_context() -> AttemptContext:
    return AttemptContext(
        owner_session_id="sess-1",
        owner_epoch=1,
        invocation_id="inv-1",
        attempt_id="att-1",
    )


def _connection(**overrides):
    base = {
        "connection_id": "conn-sent",
        "connection_revision": 1,
        "adapter_kind": "gguf_sentiment",
        "provider": "local",
        "model": "models/finbert2-base.gguf",
        "api_base": None,
        "adapter_options": {},
        "context_window": None,
        "concurrency_limit": 1,
        "credential_ref": None,
        "credential_version": None,
    }
    base.update(overrides)
    return ResolvedConnection(**base)


def _request(texts: list[str] | None = None, deadline: float = 1000.0) -> InferenceRequest:
    return InferenceRequest(
        scenario_id="news_sentiment",
        task_id="sentiment-task",
        invocation_id="inv-1",
        payload={"texts": texts if texts is not None else ["利好消息", "利空消息"]},
        deadline=deadline,
        run_id=None,
    )


def _outcome(status: str = "success", **overrides) -> ScoringOutcome:
    defaults = {
        "status": status,
        "results": (ScoredText(text_id="0", label="利好", probabilities=(0.1, 0.2, 0.7), score=0.6),),
        "reason": None,
        "usage": None,
        "metadata": None,
    }
    defaults.update(overrides)
    return ScoringOutcome(**defaults)


# ---------------------------------------------------------------------------
# ScoringOutcome → AdapterResponse 六态转译
# ---------------------------------------------------------------------------
class TestOutcomeToResponse:
    def test_success_returns_adapter_response(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        usage = NormalizedLLMUsage(
            input_total_tokens=64,
            uncached_input_tokens=64,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=None,
        )
        outcome = _outcome(
            status="success",
            usage=usage,
            metadata={"processed": 1, "input_tokens": (64,), "truncated": (False,)},
        )
        response = _outcome_to_response(outcome, "models/finbert2-base.gguf")

        assert isinstance(response, AdapterResponse)
        assert response.usage is usage
        assert response.actual_model == "finbert2-base"
        assert response.metadata["status"] == "success"
        assert response.metadata["reason"] is None
        # content 为可解析 JSON，results 形状符合业务解析契约
        payload = json.loads(response.content)
        assert payload["status"] == "success"
        assert payload["results"][0] == {
            "text_id": "0",
            "label": "利好",
            "probabilities": [0.1, 0.2, 0.7],
            "score": 0.6,
            "confidence": 0.7,
        }

    def test_partial_keeps_status(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        outcome = _outcome(status="partial", reason="chunk budget exhausted")
        response = _outcome_to_response(outcome, "models/finbert2-base.gguf")
        assert response.metadata["status"] == "partial"
        assert response.metadata["reason"] == "chunk budget exhausted"

    def test_none_fields_serialize_as_null(self):
        """缺失业务字段序列化为 null（R21：缺失不伪装为合法值）。"""
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        outcome = _outcome(
            results=(
                ScoredText(text_id="0", label=None, probabilities=None, score=None),
                ScoredText(text_id="1", label="中性", probabilities=(0.5, 0.4, 0.1), score=0.0),
            )
        )
        response = _outcome_to_response(outcome, "models/finbert2-base.gguf")
        payload = json.loads(response.content)
        assert payload["results"][0] == {
            "text_id": "0",
            "label": None,
            "probabilities": None,
            "score": None,
            "confidence": None,
        }
        assert payload["results"][1]["probabilities"] == [0.5, 0.4, 0.1]
        assert payload["results"][1]["confidence"] == 0.5

    def test_timeout_raises_timeout_error(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        with pytest.raises(TimeoutError) as exc_info:
            _outcome_to_response(_outcome(status="timeout", reason="deadline exhausted"), "m.gguf")
        assert type(exc_info.value) is TimeoutError

    def test_unavailable_raises_runtime_error(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        with pytest.raises(RuntimeError, match="scorer files missing"):
            _outcome_to_response(_outcome(status="unavailable", reason="scorer files missing"), "m.gguf")

    def test_worker_error_raises_runtime_error(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        with pytest.raises(RuntimeError, match="worker crashed"):
            _outcome_to_response(_outcome(status="worker_error", reason="worker crashed"), "m.gguf")

    def test_invalid_output_raises_validation_error(self):
        from services.ai_service.gguf_sentiment_adapter import _outcome_to_response

        with pytest.raises(ModelOutputValidationError, match="bad vectors"):
            _outcome_to_response(_outcome(status="invalid_output", reason="bad vectors"), "m.gguf")


# ---------------------------------------------------------------------------
# invoke 薄转换（透传 + 校验）
# ---------------------------------------------------------------------------
class TestInvoke:
    async def test_passes_arguments_through_to_scorer(self):
        conn = _connection()
        req = _request(texts=["a", "b"])
        ctx = _attempt_context()
        outcome = _outcome()
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            MockScorer.return_value.score_for_connection = AsyncMock(return_value=outcome)
            response = await invoke(req, conn, req.deadline, ctx)

        MockScorer.return_value.score_for_connection.assert_awaited_once_with(["a", "b"], conn, req.deadline, ctx)
        assert isinstance(response, AdapterResponse)
        assert json.loads(response.content)["status"] == "success"

    async def test_rejects_non_sentiment_adapter_kind(self):
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            with pytest.raises(ValueError, match="adapter_kind"):
                await invoke(_request(), _connection(adapter_kind="gguf_chat"), 1000.0, _attempt_context())
        MockScorer.assert_not_called()

    async def test_rejects_missing_texts_payload(self):
        req = InferenceRequest(
            scenario_id="news_sentiment",
            task_id="sentiment-task",
            invocation_id="inv-1",
            payload={"prompt": "wrong shape"},
            deadline=1000.0,
        )
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            with pytest.raises(ValueError, match="texts"):
                await invoke(req, _connection(), 1000.0, _attempt_context())
        MockScorer.assert_not_called()

    async def test_rejects_non_string_texts(self):
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            with pytest.raises(ValueError, match="texts"):
                await invoke(_request(texts=["a", 123]), _connection(), 1000.0, _attempt_context())
        MockScorer.assert_not_called()

    async def test_timeout_propagates_from_scorer(self):
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            MockScorer.return_value.score_for_connection = AsyncMock(side_effect=TimeoutError("budget gone"))
            with pytest.raises(TimeoutError) as exc_info:
                await invoke(_request(), _connection(), 1000.0, _attempt_context())
        assert type(exc_info.value) is TimeoutError

    async def test_cancelled_error_propagates(self):
        """R2：scorer 抛 CancelledError 必须传播，不得包装成 unavailable。"""
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            MockScorer.return_value.score_for_connection = AsyncMock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError) as exc_info:
                await invoke(_request(), _connection(), 1000.0, _attempt_context())
        assert type(exc_info.value) is asyncio.CancelledError

    async def test_uses_singleton_scorer_instance(self):
        """薄转换层不另建实例生命周期：NewsSentimentScorer() 为注册单例取用。"""
        with patch("services.ai_service.gguf_sentiment_adapter.NewsSentimentScorer") as MockScorer:
            MockScorer.return_value.score_for_connection = AsyncMock(return_value=_outcome())
            await invoke(_request(), _connection(), 1000.0, _attempt_context())
            MockScorer.assert_called_once_with()
