"""W-11 股票分析单入口与两轨隔离回归。

覆盖验收：

- 单入口受理：``AgentService.analyze_stock`` 强制研究请求身份 ``agent_call``，
  缺省即 ``INPUT_INVALID``，不改用内部临时身份；
- 单次计费：同一 ``request_id`` 经单入口只真实执行一次（下游请求门去重），
  重复提交如实标记；
- 身份可查回：结果 dict 恒携带 ``stock_agent_call`` 身份投影（R21）；
- 撤权/准入阻断：明确抛出而不换模型重试（NFR-R08）；
- Prompt 分层：角色基础 Prompt / 摘要 Prompt 从 ``core.prompt_base`` 统一出口；
- 两轨隔离：新闻/情绪固定链不建聊天会话、不进入 Agent 单入口（AC29）。
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError
from core.model_management_types import LegacyDispatchBlockedError, StockAgentCall
from services.agent_service import AgentService, StockAnalysisRequest

pytestmark = pytest.mark.unit


def _call(call_id: str = "req-1") -> StockAgentCall:
    return StockAgentCall(
        operation_id="op-1",
        call_id=call_id,
        ts_code="000001.SZ",
        strategy_key="oversold",
        session_id="sess-1",
        turn_id="turn-1",
    )


def _request(**overrides: Any) -> StockAnalysisRequest:
    fields: dict[str, Any] = {
        "agent_call": _call(),
        "stock_info": {"ts_code": "000001.SZ", "name": "平安银行"},
        "tech_info": {"close": 10.0},
    }
    fields.update(overrides)
    return StockAnalysisRequest(**fields)


def _service() -> AgentService:
    return AgentService(cast(Any, MagicMock()))


@pytest.fixture(autouse=True)
def _legacy_admission_pending(monkeypatch):
    """旧链测试前提：分派/准入读取成功且为 pending（未开启迁移）。"""

    from services.ai_service import scenario_dispatch

    config_service = MagicMock()
    config_service.get_snapshot = AsyncMock(
        return_value=MagicMock(migration_state="pending", migration_transition=None, scenario_routes=())
    )
    monkeypatch.setattr(scenario_dispatch, "_new_config_service", lambda: config_service)


class TestSingleEntry:
    @pytest.mark.asyncio
    async def test_requires_research_identity(self):
        port = MagicMock()
        port.analyze_stock = AsyncMock()
        request = _request(agent_call=cast(Any, None))

        with pytest.raises(AgentServiceError) as excinfo:
            await _service().analyze_stock(request, port=port)

        assert excinfo.value.code == AgentErrorCode.INPUT_INVALID
        port.analyze_stock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_closed_service_rejects_entry(self):
        service = _service()
        await service.aclose()

        with pytest.raises(AgentServiceError) as excinfo:
            await service.analyze_stock(_request(), port=MagicMock())

        assert excinfo.value.code == AgentErrorCode.PERSISTENCE_UNAVAILABLE

    @pytest.mark.asyncio
    async def test_delegates_with_identity_and_returns_projection(self):
        call = _call()
        port = MagicMock()
        port.analyze_stock = AsyncMock(return_value={"score": 80, "ai_status": "ok"})

        result = await _service().analyze_stock(_request(agent_call=call), port=port)

        await_args = port.analyze_stock.await_args
        assert await_args is not None
        kwargs = await_args.kwargs
        assert kwargs["agent_call"] is call
        assert kwargs["stock_info"] == {"ts_code": "000001.SZ", "name": "平安银行"}
        assert result is not None
        assert result["score"] == 80
        assert result["stock_agent_call"] == call.as_metadata()

    @pytest.mark.asyncio
    async def test_defaults_to_shared_ai_service_port(self, monkeypatch):
        port = MagicMock()
        port.analyze_stock = AsyncMock(return_value={"score": 80})
        created: list[bool] = []

        def _fake_ai_service():
            created.append(True)
            return port

        monkeypatch.setattr("services.ai_service.AIService", _fake_ai_service)

        result = await _service().analyze_stock(_request())

        assert created == [True]
        assert port.analyze_stock.await_args is not None
        assert result is not None and result["score"] == 80

    @pytest.mark.asyncio
    async def test_same_request_id_executes_once_through_entry(self):
        from services.ai_service.stock_analysis import StockAnalysisService

        inner = cast(Any, StockAnalysisService(MagicMock()))
        inner._analyze_stock_request = AsyncMock(return_value={"score": 80})
        service = _service()
        call = _call()

        first = await service.analyze_stock(_request(agent_call=call), port=inner)
        second = await service.analyze_stock(_request(agent_call=call), port=inner)

        assert inner._analyze_stock_request.await_count == 1
        assert first is not None and second is not None
        assert first["stock_agent_call"] == call.as_metadata()
        assert second["duplicate_submission"] is True
        assert second["stock_agent_call"] == call.as_metadata()


class TestRevocationBlocks:
    @pytest.mark.asyncio
    async def test_blocked_admission_raises_without_model_retry(self, monkeypatch):
        from services.ai_service.stock_analysis import StockAnalysisService

        async def _blocked():
            raise LegacyDispatchBlockedError("refused")

        monkeypatch.setattr("services.ai_service.stock_analysis._resolve_stock_analysis_dispatch", _blocked)

        inner = cast(Any, StockAnalysisService(MagicMock()))
        failover = AsyncMock(side_effect=AssertionError("revocation must not trigger model invocation"))
        inner._service._chat_completion_with_failover = failover

        with pytest.raises(LegacyDispatchBlockedError) as excinfo:
            await _service().analyze_stock(_request(), port=inner)

        assert excinfo.value.admission == "refused"
        failover.assert_not_awaited()


class TestPromptLayering:
    def test_summary_instruction_referenced_by_universal_rules(self):
        from core import prompt_base

        assert prompt_base.ANALYSIS_SUMMARY_FIELD_INSTRUCTION in prompt_base._UNIVERSAL_RULES
        assert "{conclusion_label_choices}" not in prompt_base._UNIVERSAL_RULES

    def test_role_prompt_builder_appends_user_instructions(self):
        from core import prompt_base

        assert prompt_base.build_analysis_role_prompt(False) == prompt_base.ANALYSIS_ROLE_PROMPT
        with_custom = prompt_base.build_analysis_role_prompt(True)
        assert with_custom.startswith(prompt_base.ANALYSIS_ROLE_PROMPT)
        assert prompt_base.ANALYSIS_USER_INSTRUCTION_PROMPT in with_custom

    @pytest.mark.asyncio
    async def test_stock_analysis_consumes_role_prompt_from_prompt_base(self, monkeypatch):
        import services.ai_service as ai_mod

        ai_mod.AIService._reset_singleton()
        service = ai_mod.AIService()
        monkeypatch.setattr(ai_mod.ConfigHandler, "get_setting", staticmethod(lambda key, default=None: default))
        monkeypatch.setattr(ai_mod.ConfigHandler, "get_ai_system_prompt", staticmethod(lambda: "SYSTEM"))
        monkeypatch.setattr(service, "is_cloud_available", lambda: True)
        monkeypatch.setattr(ai_mod, "validate_ai_analysis_response", lambda res: res)
        monkeypatch.setattr(
            "core.prompt_base.build_analysis_role_prompt",
            lambda include_user_custom_instructions: "ROLE_FROM_PROMPT_BASE",
        )
        captured: dict[str, list] = {}

        async def fake_failover(messages, *_args, **_kwargs):
            captured["messages"] = messages
            return {
                "conclusion_label": "strong_buy",
                "score": 88,
                "confidence": 70,
                "uncertainty_factors": "无",
                "thinking": "思考",
                "summary": "买入",
            }

        monkeypatch.setattr(service, "_chat_completion_with_failover", fake_failover)

        await service.analyze_stock(
            stock_info={"ts_code": "000001.SZ", "name": "平安银行"},
            tech_info={"close": 10.0},
            news_list=[],
            strategy_key="oversold",
        )

        system_contents = [m["content"] for m in captured["messages"] if m.get("role") == "system"]
        assert any("ROLE_FROM_PROMPT_BASE" in content for content in system_contents)


class TestTwoTrackIsolation:
    def test_news_and_sentiment_chains_never_enter_agent_service(self):
        services_dir = Path(__file__).resolve().parents[3] / "services"
        for filename in (
            "news_insight_service.py",
            "news_sentiment_index_service.py",
            "news_sentiment_scorer.py",
        ):
            source = (services_dir / filename).read_text(encoding="utf-8")
            assert "AgentService" not in source
            assert "agent_dao" not in source

    def test_agent_package_does_not_export_news_chain(self):
        import services.agent_service as agent_pkg

        assert not any("news" in name.lower() for name in agent_pkg.__all__)
        assert inspect.ismodule(agent_pkg)
