"""GGUF 情绪打分适配器（S2，ADR-0014 §6.1）：薄转换层。

复用 :class:`~services.news_sentiment_scorer.NewsSentimentScorer` 的
``score_for_connection`` 单连接入口，不另管进程、不选候选、不单独记费用；
把 ``ScoringOutcome`` 终态转译为统一 InferenceAdapter 契约：

- ``success`` / ``partial`` → :class:`AdapterResponse`（content 为可解析 JSON，
  usage 透传，metadata 携带 status / reason / scorer 运行元数据）；
- ``timeout`` → ``TimeoutError``；``unavailable`` / ``worker_error`` →
  ``RuntimeError``；``invalid_output`` → :class:`ModelOutputValidationError`——
  失败语义交由 S3 ScenarioExecutor 按失败表格收敛（候选切换/落账）；
- ``asyncio.CancelledError`` 始终传播（R2，scorer 内已保证不包装）。

S3 ScenarioExecutor 消费本模块作为 ``gguf_sentiment`` 连接的适配器入口
（``litellm_chat`` → ``LiteLLMClient.invoke_single_connection``，
``gguf_chat`` → ``LocalModelManager.invoke_for_connection``）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core.model_management_types import (
    AdapterResponse,
    ModelOutputValidationError,
    ScoredText,
    ScoringOutcome,
)
from services.local_model_manager import _model_tag_of
from services.news_sentiment_scorer import NewsSentimentScorer

if TYPE_CHECKING:
    from core.model_management_types import AttemptContext, InferenceRequest, ResolvedConnection


def _serialize_results(results: tuple[ScoredText, ...]) -> list[dict[str, Any]]:
    """ScoredText 元组 → JSON 安全 dict 列表（tuple → list；None 保持 null，R21）。"""
    return [
        {
            "text_id": r.text_id,
            "label": r.label,
            "probabilities": list(r.probabilities) if r.probabilities is not None else None,
            "score": r.score,
        }
        for r in results
    ]


def _outcome_to_response(outcome: ScoringOutcome, model_path: str) -> AdapterResponse:
    """ScoringOutcome 终态 → AdapterResponse / 失败异常（单点转译，纯函数）。"""
    if outcome.status == "timeout":
        raise TimeoutError(outcome.reason or "Sentiment scoring deadline exhausted.")
    if outcome.status == "unavailable":
        raise RuntimeError(f"Sentiment scorer unavailable: {outcome.reason or 'unknown reason'}")
    if outcome.status == "worker_error":
        raise RuntimeError(f"Sentiment scorer worker error: {outcome.reason or 'unknown reason'}")
    if outcome.status == "invalid_output":
        raise ModelOutputValidationError(outcome.reason or "Sentiment output validation failed.")

    # success / partial：content 承载可解析 JSON，结构化结果由业务侧消费。
    content = json.dumps({"status": outcome.status, "results": _serialize_results(outcome.results)}, ensure_ascii=False)
    return AdapterResponse(
        content=content,
        usage=outcome.usage,
        actual_model=_model_tag_of(model_path),
        metadata={"status": outcome.status, "reason": outcome.reason, "scoring_metadata": outcome.metadata},
    )


async def invoke(
    request: InferenceRequest,
    resolved_connection: ResolvedConnection,
    deadline: float,
    attempt_context: AttemptContext,
) -> AdapterResponse:
    """gguf_sentiment 连接的统一推理入口（InferenceAdapter 契约形状）。

    仅做校验与转译：进程生命周期 / 队列协议 / generation 隔离全部由
    scorer 单例内部维护；deadline / attempt_context 原样透传。

    Raises:
        ValueError: adapter_kind 非 gguf_sentiment / payload 缺 texts 或含非 str。
        TimeoutError / RuntimeError / ModelOutputValidationError: 见模块 docstring。
    """
    if resolved_connection.adapter_kind != "gguf_sentiment":
        raise ValueError(
            f"gguf_sentiment_adapter requires adapter_kind='gguf_sentiment', got {resolved_connection.adapter_kind!r}"
        )

    texts = request.payload.get("texts")
    if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
        raise ValueError("gguf_sentiment payload requires 'texts': list[str].")

    scorer = NewsSentimentScorer()
    outcome = await scorer.score_for_connection(list(texts), resolved_connection, deadline, attempt_context)
    return _outcome_to_response(outcome, resolved_connection.model)
