"""主回合生产模型端口单测（受治理调用参数 + 零派发事实）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    Mode,
    SourceKind,
    SourceVersionRef,
    decode_json_doc,
)
from services.agent_service.loop import LoopAction, LoopIdentity, TurnContext
from services.agent_service.main_turn import MainTurnRequest
from services.agent_service.main_turn_model import (
    _AnswerDraft,
    _PlanDraft,
    _PlanStepDraft,
    build_governed_turn_factory,
    run_governed_turn,
)

_IDENTITY = LoopIdentity(
    user_scope="u1",
    session_id="s1",
    execution_id="execution-1",
    round_id="round-1",
    node_id="root",
    input_revision=1,
    actual_input_digest="a" * 64,
    actual_model_ref="agent_research:governed",
)
_CONTEXT = TurnContext(mode=Mode.DISCUSS, turn=1, observations=())


class _RouteRecorder:
    def __init__(self, route: Any) -> None:
        self.route = route
        self.calls = 0

    async def __call__(self) -> Any:
        self.calls += 1
        return self.route


class _Invoker:
    def __init__(self, output: Any) -> None:
        self.output = output
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, model: Any, **kwargs: Any) -> Any:
        self.calls.append({"model": model, **kwargs})
        return self.output


class _ModelFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        return object()


@pytest.mark.asyncio
async def test_discuss_turn_answers_through_governed_route() -> None:
    route = _RouteRecorder(SimpleNamespace(executor=object(), snapshot=object()))
    invoker = _Invoker(_AnswerDraft(answer="银行股要看息差与资产质量"))
    models = _ModelFactory()
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="银行股怎么看")

    decision = await run_governed_turn(
        request,
        _CONTEXT,
        resolve_route=route,
        build_model=models,
        invoke_agent=invoker,
    )

    assert decision.action is LoopAction.ANSWER
    assert decision.text == "银行股要看息差与资产质量"
    assert route.calls == 1
    assert len(models.calls) == 1
    args, kwargs = models.calls[0]
    assert kwargs["tool_schema_version"] == "v1"
    assert kwargs["call_identity"].startswith("main_turn_")
    assert args[1] == {"scenario_id": "agent_research", "task_id": "root"}
    assert "require_data_consent" not in args[1]
    assert len(invoker.calls) == 1
    assert invoker.calls[0]["output_type"] is _AnswerDraft
    assert invoker.calls[0]["prompt"] == "银行股怎么看"


@pytest.mark.asyncio
async def test_plan_turn_returns_readable_plan_and_structured_steps() -> None:
    route = _RouteRecorder(SimpleNamespace(executor=object(), snapshot=object()))
    invoker = _Invoker(
        _PlanDraft(
            summary="先看行业再看个股",
            steps=[
                _PlanStepDraft(id="s1", content="梳理行业景气"),
                _PlanStepDraft(id="s2", content="对比龙头估值", depends_on=["s1"]),
            ],
        )
    )
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.PLAN, prompt_text="研究银行股")

    decision = await run_governed_turn(
        request,
        TurnContext(mode=Mode.PLAN, turn=1, observations=()),
        resolve_route=route,
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert decision.action is LoopAction.PLAN
    assert "先看行业再看个股" in (decision.text or "")
    assert "1. 梳理行业景气" in (decision.text or "")
    decoded = decode_json_doc(decision.structured_fields or "{}")
    assert isinstance(decoded, dict)
    steps = decoded["steps"]
    assert [step["id"] for step in steps] == ["s1", "s2"]
    assert steps[1]["depends_on"] == ["s1"]
    assert invoker.calls[0]["output_type"] is _PlanDraft


@pytest.mark.asyncio
async def test_missing_route_fails_closed_before_any_model_call() -> None:
    route = _RouteRecorder(None)
    invoker = _Invoker(_AnswerDraft(answer="x"))
    models = _ModelFactory()
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题")

    with pytest.raises(AgentServiceError) as excinfo:
        await run_governed_turn(
            request,
            _CONTEXT,
            resolve_route=route,
            build_model=models,
            invoke_agent=invoker,
        )

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
    assert models.calls == []
    assert invoker.calls == []


@pytest.mark.asyncio
async def test_attached_sources_fail_closed_without_consent_channel() -> None:
    route = _RouteRecorder(SimpleNamespace(executor=object(), snapshot=object()))
    invoker = _Invoker(_AnswerDraft(answer="x"))
    request = MainTurnRequest(
        identity=_IDENTITY,
        mode=Mode.DISCUSS,
        prompt_text="问题",
        source_refs=(
            SourceVersionRef(
                source_kind=SourceKind.ATTACHMENT,
                source_id="src-1",
                source_version="v1",
                source_digest="b" * 64,
            ),
        ),
    )

    with pytest.raises(AgentServiceError) as excinfo:
        await run_governed_turn(
            request,
            _CONTEXT,
            resolve_route=route,
            build_model=_ModelFactory(),
            invoke_agent=invoker,
        )

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
    assert route.calls == 0
    assert invoker.calls == []


@pytest.mark.asyncio
async def test_factory_binds_request_text_and_returns_agent_turn() -> None:
    route = _RouteRecorder(SimpleNamespace(executor=object(), snapshot=object()))
    invoker = _Invoker(_AnswerDraft(answer="答"))
    factory = build_governed_turn_factory(
        resolve_route=route,
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题文本")

    decision = await factory(request)(_CONTEXT)

    assert decision.action is LoopAction.ANSWER
    assert decision.text == "答"
    assert invoker.calls[0]["prompt"] == "问题文本"
