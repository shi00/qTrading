"""F03 集成回归：真实 ScenarioExecutor 成功出口依次送入三个消费者。

回归契约（F03 检视报告）：消费者测试预制对象不能证明真实成功出口——本文件
以**真实 ScenarioExecutor**（fake 适配器 / 账本 / 授权依赖为组装注入，execute
调用链不 mock）产出 ScenarioExecutionResult，直接送入 stock_analysis /
news_classifier / news_risk 三个消费者场景方法，断言：

1. 成功输出经 ScenarioOutput 公共类型读取，不再因属性访问失败丢结果；
2. 程序生成溯源（actual_model / connection_id / connection_revision 等）
   在消费端 execution_metadata 保留（R21：已知信息不伪装成「无信息」）。
"""

from __future__ import annotations

import asyncio
import datetime
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import (
    AdapterResponse,
    ExecutionSnapshot,
    PersistenceOutcome,
)
from services.ai_service import AIService
from services.ai_service.news_classifier import NewsClassifier
from services.ai_service.news_risk import NewsRiskAnalyzer
from services.ai_service.scenario_dispatch import ScenarioDispatch
from services.ai_service.scenario_executor import ScenarioExecutor
from services.ai_service.stock_analysis import StockAnalysisService
from services.ai_service.usage_recorder import ReservationOutcome
from services.news_insight_models import EvidenceDocument, NewsInsightRequest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 真实 Executor 组装（依赖注入为替身，execute 链路真实）
# ---------------------------------------------------------------------------


def _real_executor(adapter_response: AdapterResponse) -> ScenarioExecutor:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=adapter_response)
    config_service = MagicMock()
    config_service.authorize_attempt = AsyncMock(return_value=MagicMock(security_revision=1))
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.ensure_invocation = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.try_reserve = AsyncMock(
        return_value=ReservationOutcome(persisted=True, error_code=None, reservation=MagicMock())
    )
    recorder.mark_dispatched = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.begin_attempt = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    recorder.submit_terminal = AsyncMock(return_value=PersistenceOutcome(persisted=True, error_code=None))
    compatibility = MagicMock()
    compatibility.resolve_execution_params = MagicMock(
        return_value=MagicMock(compatible=True, reason=None, resolved_options={})
    )
    return ScenarioExecutor(
        config_service=config_service,
        recorder=recorder,
        compatibility=compatibility,
        adapters={"litellm_chat": adapter},
    )


def _dispatch(scenario_id: str, task_id: str, adapter_response: AdapterResponse) -> ScenarioDispatch:
    """真实快照（S2 mapping 形态连接定义）+ 真实 Executor 的分派组装。"""
    snapshot = ExecutionSnapshot(
        snapshot_id="snap-f03",
        run_id=None,
        config_revision=7,
        routes=(
            {
                "scenario_id": scenario_id,
                "route_revision": 1,
                "candidates": (
                    {
                        "candidate_index": 0,
                        "task_id": task_id,
                        "connection_id": "conn-1",
                        "target_fingerprint": "fp-1",
                        "purpose": "f03-regression",
                        "reservation_id": None,
                        "owner_session_id": "",
                        "owner_epoch": 0,
                    },
                ),
            },
        ),
        connections=(
            {
                "connection_id": "conn-1",
                "connection_revision": 1,
                "adapter_kind": "litellm_chat",
                "provider": "zhipu",
                "model": "glm-4-air",
                "concurrency_limit": 1,
                "credential_version": "v1",
            },
        ),
        business_contract_versions={},
        security_scope_version=1,
        created_at="2026-10-16T00:00:00Z",
    )
    return ScenarioDispatch(
        executor=_real_executor(adapter_response),
        snapshot=snapshot,
        route_revision=1,
        total_timeout_seconds=45.0,
        task_routes={},
    )


def _response(payload: dict) -> AdapterResponse:
    return AdapterResponse(content=json.dumps(payload), usage=None, actual_model="glm-4-air", metadata={})


@pytest.fixture(autouse=True)
def _config_free_text_len(monkeypatch):
    """真实校验器链触发的自由文本清洗读取配置（无关本回归靶点，固定之）。"""
    monkeypatch.setattr("utils.config_handler.ConfigHandler.get_ai_free_text_max_len", staticmethod(lambda: 1000))


# ---------------------------------------------------------------------------
# stock_analysis：真实出口 → _execute_scenario_analysis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_executor_output_feeds_stock_analysis():
    payload = {"score": 82, "recommendation": "buy", "confidence": 70}
    dispatch = _dispatch("stock_analysis", "analysis", _response(payload))
    analysis = StockAnalysisService(AIService.__new__(AIService))

    result = await analysis._execute_scenario_analysis(
        dispatch=dispatch,
        messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "u"}],
        learning_meta=None,
    )

    # 业务结果不再因属性访问失败丢失（F03 主缺陷）
    assert result["score"] == 82
    # 程序生成溯源保留（模型身份 / 连接身份来自 Executor 真实出口 metadata）
    meta = result["execution_metadata"]
    assert meta["actual_model"] == "glm-4-air"
    assert meta["connection_id"] == "conn-1"
    assert meta["connection_revision"] == 1
    assert meta["ai_status"] == "succeeded"


# ---------------------------------------------------------------------------
# news_classifier：真实出口 → _classify_bundled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_executor_output_feeds_news_classifier(monkeypatch):
    monkeypatch.setattr("services.ai_service.ConfigHandler.get_ai_news_prompt", staticmethod(lambda: "TOPIC_PROMPT"))
    payload = {"category_L1": "finance", "category_L2": "precious_metals", "sentiment": "Positive"}
    dispatch = _dispatch("news_annotation", "topic", _response(payload))
    classifier = NewsClassifier(MagicMock(_chat_completion=AsyncMock()))

    loop = asyncio.get_running_loop()
    result = await classifier._classify_bundled(
        "央行宣布降准", dispatch, uuid.uuid4().hex, loop.time() + dispatch.total_timeout_seconds
    )

    assert result["sentiment"] == "Positive"
    assert result["sentiment_status"] == "success"
    assert result["sentiment_source"] == "bundled"
    meta = result["execution_metadata"]
    assert meta["actual_model"] == "glm-4-air"
    assert meta["connection_id"] == "conn-1"


# ---------------------------------------------------------------------------
# news_risk：真实出口 → _execute_scenario_risk
# ---------------------------------------------------------------------------


def _risk_request() -> NewsInsightRequest:
    evidence = EvidenceDocument(news_id=1, quoteable_text="公司发布公告称经营正常", source_kind="announcement")
    return NewsInsightRequest(
        ts_code="000001.SZ",
        stock_name="平安银行",
        window_start=datetime.datetime(2026, 8, 17, 16, 0, 0),
        window_end=datetime.datetime(2026, 9, 16, 15, 59, 59),
        evidence=[evidence],
        coverage={"announcement": {"status": "ok"}},
        analysis_profile="cloud:deepseek/chat",
    )


@pytest.mark.asyncio
async def test_real_executor_output_feeds_news_risk():
    payload = {
        "summary": "风险提示",
        "events": [
            {
                "event_type": "litigation",
                "severity": "high",
                "company_role": "direct",
                "event_status": "ongoing",
                "impact_horizon": "short_term",
                "fact": "公司涉诉",
                "impact_reasoning": "诉讼进展影响经营",
                "confidence": 80,
                "evidence_quotes": [{"news_id": 1, "quote": "公告"}],
            }
        ],
    }
    dispatch = _dispatch("news_risk", "risk", _response(payload))
    analyzer = NewsRiskAnalyzer(AIService.__new__(AIService))

    result = await analyzer._execute_scenario_risk(dispatch, _risk_request())

    assert result.analysis_status == "analyzed_with_events"
    assert result.risk_level == "high"
    # model_id 兼容显示以 actual_model 优先（真实出口溯源，非 None 回退）
    assert result.model_id == "glm-4-air"
    meta = result.execution_metadata
    assert meta["actual_model"] == "glm-4-air"
    assert meta["connection_id"] == "conn-1"
    assert meta["ai_status"] == "succeeded"
