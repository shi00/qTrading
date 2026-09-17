"""新闻风险解读 E2E fake AI 服务（仅测试环境启用）。

E2E 子进程是独立 Python 进程，测试进程的 unittest.mock 无法注入到该子进程，
故在服务装配处以 ``is_e2e_mode()`` + env 选型 fake（新闻风险解读 Phase E3）。

唯一注入点（``NewsInsightService.__init__``）：当 ``E2E_TESTING=true`` 且设置
``NEWS_AISERVICE_MOCK`` 时，用本模块的 ``E2EFakeAIService`` 替代真实 AIService。生产
构建不导入本模块（不满足 env 分支），零副作用。

场景由 ``NEWS_AISERVICE_MOCK`` 区分（默认 ``success_events``）：
- ``success_events``：返回 ``analyzed_with_events`` 成功结果（有事件清单），验收点 4
- ``success_no_events``：返回 ``analyzed_no_event`` 成功结果（无事件，R21 不显低风险），验收点 3
- ``fail``：返回 ``failed`` 结果，验收点（错误/降级态）
- ``delayed``：先休眠 ``NEWS_AISERVICE_MOCK_DELAY_MS`` 再返回成功结果，验收点 5 取消时序

实现约束：
- fake 需实现 ``async analyze_news_risk(request) -> NewsInsightResult``，直接构造
  ``NewsInsightResult`` 字段（绕过真实解析）。``events`` 为 dict 清单，``evidence_news_ids``
  引用 ``request.evidence`` 的 news_id，使子集复用/缓存命中断言真实有效。
- ``coverage`` / ``analysis_profile`` / ``prompt_version`` 沿袭 request，保证服务层缓存
  ``input_hash`` 指纹一致。
- ``delayed`` 用 ``await asyncio.sleep``，取消时 ``CancelledError`` 自然传播（R2），
  不经真实 litellm。
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

from services.news_insight_models import NEWS_RISK_PROMPT_VERSION, NewsInsightResult

# fake 生成的稳定 model_id / 摘要，便于 E2E 断言
_FAKE_MODEL_ID = "e2e-fake"
_FAKE_SUMMARY = "E2E 假 Ai 风险摘要：存在可评估的重大风险信号。"
_FAKE_NO_EVENT_SUMMARY = "未识别到重大风险。"


@dataclass
class _ScenarioSpec:
    """由 env 解析出的 fake 场景规格。"""

    name: str  # success_events / success_no_events / fail / delayed
    delay_ms: int  # delayed 场景的休眠毫秒；其余为 0


def _scenario_spec(env=None) -> _ScenarioSpec:
    """从 env 解析 fake 场景（env=None 用于注入默认场景，便于单测）。"""
    env = env if env is not None else os.environ
    name = (env.get("NEWS_AISERVICE_MOCK") or "success_events").strip().lower()
    if name not in ("success_events", "success_no_events", "fail", "delayed"):
        name = "success_events"  # 非法值回退到默认成功场景（保守，不遮蔽去向）
    try:
        delay_ms = int(env.get("NEWS_AISERVICE_MOCK_DELAY_MS") or "0")
    except (ValueError, TypeError):
        delay_ms = 0
    delay_ms = max(0, delay_ms)
    return _ScenarioSpec(name=name, delay_ms=delay_ms)


class E2EFakeAIService:
    """E2E 假 Ai 服务：按 env 场景返回确定性 NewsInsightResult。

    接口与 ``services.ai_service.AIService`` 的 ``analyze_news_risk`` 对齐：
    ``async analyze_news_risk(request: NewsInsightRequest) -> NewsInsightResult``。
    """

    def __init__(self, env=None) -> None:
        self._spec = _scenario_spec(env)
        self._delay_ms = self._spec.delay_ms

    async def analyze_news_risk(self, request) -> NewsInsightResult:
        spec = self._spec
        # delay_ms > 0 时任意场景都休眠（供 E2E analyzing 停留窗口与 XP5 取消时序）；
        # E2E 各场景池经 env 下发 NEWS_AISERVICE_MOCK_DELAY_MS，取消/未取消都必须经历
        # 该延迟，asyncio.sleep 被取消时 CancelledError 自然传播（R2）。
        if self._delay_ms > 0:
            await asyncio.sleep(self._delay_ms / 1000.0)
        if spec.name == "fail":
            return self._result(
                request,
                analysis_status="failed",
                risk_level=None,
                confidence=None,
                summary=None,
                events=[],
                evidence_news_ids=[],
            )
        if spec.name == "success_no_events":
            return self._result(
                request,
                analysis_status="analyzed_no_event",
                risk_level=None,
                confidence=None,
                summary=_FAKE_NO_EVENT_SUMMARY,
                events=[],
                evidence_news_ids=[],
            )
        # success_events（默认）
        evidence_news_ids = [ev.news_id for ev in (request.evidence or [])]
        events = [
            {
                "event_type": "regulatory",
                "severity": "high",
                "company_role": "direct",
                "event_status": "ongoing",
                "impact_horizon": "medium_term",
                "fact": "E2E 假 Ai：存在待评估的监管类风险事实。",
                "impact_reasoning": "E2E 假 Ai 影响推断：该事实可能影响公司经营预期。",
                "uncertainty": "这是一个用于 E2E 校验的确定性假事件。",
                "confidence": 80,
                "evidence_quotes": [
                    {"news_id": news_id, "quote": "来自已引用证据的确定性摘录。"} for news_id in evidence_news_ids[:1]
                ],
            }
            for _ in evidence_news_ids
        ]
        if not events:
            events = [
                {
                    "event_type": "regulatory",
                    "severity": "high",
                    "company_role": "direct",
                    "event_status": "ongoing",
                    "impact_horizon": "medium_term",
                    "fact": "E2E 假 Ai：无证据但仍需展示的占位事件。",
                    "impact_reasoning": "占位影响推断。",
                    "uncertainty": "占位不确定性。",
                    "confidence": 80,
                    "evidence_quotes": [],
                }
            ]
        return self._result(
            request,
            analysis_status="analyzed_with_events",
            risk_level="high",
            confidence=80,
            summary=_FAKE_SUMMARY,
            events=events,
            evidence_news_ids=evidence_news_ids,
        )

    @staticmethod
    def _result(request, **changes) -> NewsInsightResult:
        """按 request 构造 NewsInsightResult，并覆盖场景差异字段。"""
        base = {
            "risk_level": None,
            "confidence": None,
            "summary": None,
            "events": [],
            "evidence_news_ids": [],
            "coverage": (request.coverage if request is not None else {}),
            "model_id": _FAKE_MODEL_ID,
            "analysis_profile": (request.analysis_profile if request is not None else ""),
            "prompt_version": (request.prompt_version if request is not None else NEWS_RISK_PROMPT_VERSION),
        }
        base.update(changes)
        return NewsInsightResult(**base)
