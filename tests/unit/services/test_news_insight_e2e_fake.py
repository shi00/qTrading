"""E2EFakeAIService 单测（新闻风险解读 Phase E3 配套）。

覆盖：场景 env 解析（success_events / success_no_events / fail / delayed /
非法值回退 / 延迟毫秒解析）、各场景返回的 NewsInsightResult 字段正确性、
取消传播（delayed 场景 asyncio.sleep 抛 CancelledError）。

isolate：注入 dict 形态 env，不触真实 os.environ；构造 NewsInsightRequest 直接
喂入，避免依赖服务层证据加载。
"""

from __future__ import annotations

import asyncio
import datetime

import pytest

from services.news_insight_e2e_fake import E2EFakeAIService, _scenario_spec
from services.news_insight_models import (
    NEWS_RISK_PROMPT_VERSION,
    EvidenceDocument,
    NewsInsightRequest,
)

pytestmark = pytest.mark.unit


def _request() -> NewsInsightRequest:
    now = datetime.datetime(2026, 9, 17, tzinfo=datetime.UTC)
    return NewsInsightRequest(
        ts_code="000001.SZ",
        stock_name="平安银行",
        window_start=now - datetime.timedelta(days=30),
        window_end=now,
        evidence=[
            EvidenceDocument(news_id=1, title="公告1", quoteable_text="摘录1"),
            EvidenceDocument(news_id=2, title="新闻2", quoteable_text="摘录2"),
        ],
        coverage={"announcement": {"status": "ok"}},
        analysis_profile="cloud:provider/model",
    )


def _env(name: str = "success_events", delay: str = "0") -> dict:
    e = {"NEWS_AISERVICE_MOCK": name}
    if delay != "0":
        e["NEWS_AISERVICE_MOCK_DELAY_MS"] = delay
    return e


# --- 场景规格解析 ---


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("success_events", "success_events"),
        ("success_no_events", "success_no_events"),
        ("delayed", "delayed"),
        ("fail", "fail"),
        ("", "success_events"),  # 缺省回退成功场景
        ("bogus", "success_events"),  # 非法值回退
    ],
)
def test_scenario_spec_name(name: str, expected: str) -> None:
    assert _scenario_spec(_env(name)).name == expected


def test_scenario_spec_upper_case_normalized() -> None:
    assert _scenario_spec(_env("SUCCESS_EVENTS")).name == "success_events"


def test_scenario_spec_delay_parse() -> None:
    assert _scenario_spec(_env("delayed", "1500")).delay_ms == 1500
    assert _scenario_spec(_env("delayed", "abc")).delay_ms == 0  # 非法毫秒回退 0
    assert _scenario_spec(_env("delayed", "-5")).delay_ms == 0  # 负值截断为 0


# --- 各场景返回结果 ---


async def _analyze(name: str, delay: str = "0"):
    svc = E2EFakeAIService(_env(name, delay))
    return await svc.analyze_news_risk(_request())


async def test_success_events_result_fields() -> None:
    res = await _analyze("success_events")
    assert res.analysis_status == "analyzed_with_events"
    assert res.risk_level == "high"
    assert res.confidence == 80
    assert res.events, "成功事件场景应有事件清单"
    assert sorted(res.evidence_news_ids) == [1, 2], "evidence_news_ids 应引用 request.evidence"
    assert res.coverage == {"announcement": {"status": "ok"}}, "coverage 沿袭 request"
    assert res.analysis_profile == "cloud:provider/model"
    assert res.prompt_version == NEWS_RISK_PROMPT_VERSION
    assert res.events[0]["severity"] == "high"
    assert res.events[0]["event_type"] == "regulatory"


async def test_success_events_quotes_reference_evidence_ids() -> None:
    res = await _analyze("success_events")
    first_events_quotes = res.events[0].get("evidence_quotes") or []
    if first_events_quotes:
        assert first_events_quotes[0]["news_id"] in {1, 2}


async def test_success_no_events_result() -> None:
    res = await _analyze("success_no_events")
    assert res.analysis_status == "analyzed_no_event"
    assert res.risk_level is None, "无事件时不得伪装风险等级（R21）"
    assert res.confidence is None
    assert res.events == []
    assert res.evidence_news_ids == []
    assert res.summary


async def test_fail_result() -> None:
    res = await _analyze("fail")
    assert res.analysis_status == "failed"
    assert res.risk_level is None, "失败时不得伪装风险等级（R21）"
    assert res.events == []
    assert res.evidence_news_ids == []


async def test_delayed_honors_delay_and_still_succeeds() -> None:
    import time

    start = time.monotonic()
    res = await _analyze("delayed", "200")
    elapsed = time.monotonic() - start
    assert elapsed >= 0.19, f"delayed 场景应休眠约 200ms，实际 {elapsed:.3f}s"
    assert res.analysis_status == "analyzed_with_events"


async def test_delayed_cancel_propagates_cancelled_error() -> None:
    """取消传播（R2）：delayed 场景的 asyncio.sleep 被取消时应抛 CancelledError。"""
    svc = E2EFakeAIService(_env("delayed", "10000"))
    task = asyncio.create_task(svc.analyze_news_risk(_request()))
    with pytest.raises(asyncio.CancelledError) as excinfo:
        task.cancel()
        await task
    # R2 取消传播：异常必须为 CancelledError 且任务标记为已取消
    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert task.cancelled(), "任务应标记为已取消（R2 取消传播）"


# --- 无证据时成功事件场景补占位事件 ---


async def test_empty_evidence_falls_back_to_placeholder_event() -> None:
    svc = E2EFakeAIService(_env("success_events"))
    req = _request()
    req.evidence = []
    res = await svc.analyze_news_risk(req)
    assert res.analysis_status == "analyzed_with_events"
    assert res.events, "无证据时 success_events 应补一个占位事件保证可渲染"
    assert res.evidence_news_ids == []
