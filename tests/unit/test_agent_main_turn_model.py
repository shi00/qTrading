"""主回合生产模型端口单测（受治理调用参数 + 零派发事实）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ClarificationAnswerFact,
    Mode,
    SourceKind,
    SourceVersionRef,
    decode_json_doc,
)
from services.agent_service.loop import LoopAction, LoopIdentity, LoopObservation, TurnContext
from services.agent_service.main_turn import MainTurnRequest
from services.agent_service.main_turn_model import (
    MAIN_TURN_AGENT_NAME,
    _AnswerDraft,
    _ClarificationDraft,
    _PlanDraft,
    _PlanStepDraft,
    _TurnEnvelope,
    _answer_decision,
    _default_model_factory,
    _envelope_decision,
    _invoke_agent,
    _plan_decision,
    _resolve_route,
    _turn_prompt,
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
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="银行股要看息差与资产质量")))
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
    assert invoker.calls[0]["output_type"] is _TurnEnvelope
    assert invoker.calls[0]["prompt"] == "银行股怎么看"


@pytest.mark.asyncio
async def test_plan_turn_returns_readable_plan_and_structured_steps() -> None:
    route = _RouteRecorder(SimpleNamespace(executor=object(), snapshot=object()))
    invoker = _Invoker(
        _TurnEnvelope(
            plan=_PlanDraft(
                summary="先看行业再看个股",
                steps=[
                    _PlanStepDraft(id="s1", content="梳理行业景气"),
                    _PlanStepDraft(id="s2", content="对比龙头估值", depends_on=["s1"]),
                ],
            )
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
    assert invoker.calls[0]["output_type"] is _TurnEnvelope


@pytest.mark.asyncio
async def test_missing_route_fails_closed_before_any_model_call() -> None:
    route = _RouteRecorder(None)
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="x")))
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
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="x")))
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
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))
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


_ROUTE = SimpleNamespace(executor=object(), snapshot=object())


class _RaisingInvoker:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc
        self.calls = 0

    async def __call__(self, model: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.exc


class _RecordingAgent:
    def __init__(self, model: Any, **kwargs: Any) -> None:
        self.model = model
        self.kwargs = kwargs
        self.runs: list[str] = []
        self.output: Any = _AnswerDraft(answer="ok")

    async def run(self, prompt: str) -> Any:
        self.runs.append(prompt)
        return SimpleNamespace(output=self.output)


@pytest.mark.asyncio
async def test_execute_mode_answers_directly_with_discuss_instructions() -> None:
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="直答")))
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.EXECUTE, prompt_text="直接回答")

    decision = await run_governed_turn(
        request,
        _CONTEXT,
        resolve_route=_RouteRecorder(_ROUTE),
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert decision.action is LoopAction.ANSWER
    assert decision.text == "直答"
    assert invoker.calls[0]["output_type"] is _TurnEnvelope
    # 执行模式的直答仍是「只回答、不行动」指令（执行动作由派发链负责）。
    assert "不取数" in invoker.calls[0]["instructions"]
    assert "不调用任何外部服务" in invoker.calls[0]["instructions"]


@pytest.mark.asyncio
async def test_observations_are_inlined_into_prompt_without_granting_actions() -> None:
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))
    context = TurnContext(
        mode=Mode.DISCUSS,
        turn=2,
        observations=(
            LoopObservation(kind="tool_result", ref="r1", text="已读公告A"),
            LoopObservation(kind="tool_result", ref="r2", text=None),
            LoopObservation(kind="tool_result", ref="r3", text=""),
        ),
    )

    await run_governed_turn(
        MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="原始问题"),
        context,
        resolve_route=_RouteRecorder(_ROUTE),
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert invoker.calls[0]["prompt"] == "原始问题\n\n已获资料观测：\n已读公告A"
    assert invoker.calls[0]["output_type"] is _TurnEnvelope


@pytest.mark.asyncio
async def test_blank_observations_do_not_alter_prompt() -> None:
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))
    context = TurnContext(
        mode=Mode.DISCUSS,
        turn=2,
        observations=(LoopObservation(kind="tool_result", ref="r1", text=None),),
    )

    await run_governed_turn(
        MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="原始问题"),
        context,
        resolve_route=_RouteRecorder(_ROUTE),
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert invoker.calls[0]["prompt"] == "原始问题"


def test_clarification_envelope_yields_clarify_decision() -> None:
    output = _TurnEnvelope(
        clarification=_ClarificationDraft(
            question="你关注行业还是个股？",
            options=["行业", "个股"],
            impact="决定分析口径",
        )
    )

    decision = _envelope_decision(_IDENTITY, output, Mode.DISCUSS)

    assert decision.action is LoopAction.CLARIFY
    assert decision.text == "你关注行业还是个股？"
    assert decode_json_doc(decision.structured_fields or "{}") == {
        "question": "你关注行业还是个股？",
        "options": ["行业", "个股"],
        "impact": "决定分析口径",
    }


def test_plan_mode_clarification_is_allowed_with_empty_option_defaults() -> None:
    decision = _envelope_decision(
        _IDENTITY,
        _TurnEnvelope(clarification=_ClarificationDraft(question="研究哪段时间范围？")),
        Mode.PLAN,
    )

    assert decision.action is LoopAction.CLARIFY
    decoded = decode_json_doc(decision.structured_fields or "{}")
    assert isinstance(decoded, dict)
    assert decoded["options"] == []
    assert decoded["impact"] == ""


def test_plan_mode_answer_payload_is_rejected_not_fabricated() -> None:
    with pytest.raises(AgentServiceError) as excinfo:
        _envelope_decision(_IDENTITY, _TurnEnvelope(answer=_AnswerDraft(answer="直接结论")), Mode.PLAN)

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN


def test_envelope_with_multiple_payloads_is_rejected() -> None:
    output = _TurnEnvelope(
        answer=_AnswerDraft(answer="答"),
        clarification=_ClarificationDraft(question="问"),
    )

    with pytest.raises(AgentServiceError) as excinfo:
        _envelope_decision(_IDENTITY, output, Mode.DISCUSS)

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN


def test_empty_envelope_is_rejected() -> None:
    with pytest.raises(AgentServiceError) as excinfo:
        _envelope_decision(_IDENTITY, _TurnEnvelope(), Mode.DISCUSS)

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN


def test_turn_prompt_injects_consumed_clarification_answers_once() -> None:
    request = MainTurnRequest(
        identity=_IDENTITY,
        mode=Mode.DISCUSS,
        prompt_text="按刚才的答复继续",
        clarification_answers=(
            ClarificationAnswerFact(
                decision_id="decision-1",
                question_text="关注行业还是个股？",
                answer_text="行业景气",
                consumed_at="2026-01-01T00:00:00.000000Z",
            ),
        ),
    )

    prompt = _turn_prompt(request)

    assert prompt.prompt == "按刚才的答复继续\n\n已澄清信息（以此为准）：\n用户已澄清：关注行业还是个股？ → 行业景气"
    assert prompt.output is _TurnEnvelope


@pytest.mark.asyncio
async def test_cancelled_error_propagates_without_fabricated_decision() -> None:
    invoker = _RaisingInvoker(asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await run_governed_turn(
            MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题"),
            _CONTEXT,
            resolve_route=_RouteRecorder(_ROUTE),
            build_model=_ModelFactory(),
            invoke_agent=invoker,
        )

    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert invoker.calls == 1


@pytest.mark.asyncio
async def test_typed_service_error_propagates_unchanged() -> None:
    original = AgentServiceError(AgentErrorCode.BUDGET_BLOCKED, "budget blocked", "execution-1")
    invoker = _RaisingInvoker(original)

    with pytest.raises(AgentServiceError) as excinfo:
        await run_governed_turn(
            MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题"),
            _CONTEXT,
            resolve_route=_RouteRecorder(_ROUTE),
            build_model=_ModelFactory(),
            invoke_agent=invoker,
        )

    assert excinfo.value is original
    assert invoker.calls == 1


@pytest.mark.asyncio
async def test_unexpected_model_error_is_wrapped_and_sanitized() -> None:
    original = ValueError("token=abc123secret must not leak")
    invoker = _RaisingInvoker(original)

    with pytest.raises(AgentServiceError) as excinfo:
        await run_governed_turn(
            MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题"),
            _CONTEXT,
            resolve_route=_RouteRecorder(_ROUTE),
            build_model=_ModelFactory(),
            invoke_agent=invoker,
        )

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
    assert excinfo.value.identity == "execution-1"
    assert excinfo.value.sanitized_message == "main turn model failed: ValueError"
    assert "abc123secret" not in str(excinfo.value)
    assert excinfo.value.__cause__ is original
    assert invoker.calls == 1


@pytest.mark.asyncio
async def test_turn_without_injected_model_uses_gateway_and_binds_only_scenario(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class _FakeGateway:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            created.append((args, kwargs))

    monkeypatch.setattr("services.agent_service.model.GatewayModel", _FakeGateway)
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))

    decision = await run_governed_turn(
        MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="问题"),
        _CONTEXT,
        resolve_route=_RouteRecorder(_ROUTE),
        build_model=None,
        invoke_agent=invoker,
    )

    assert decision.action is LoopAction.ANSWER
    assert decision.text == "答"
    assert len(created) == 1
    args, kwargs = created[0]
    # 主回合绑定只写场景与节点：无资料许可键即不放行任何资料外发。
    assert args[1] == {"scenario_id": "agent_research", "task_id": "root"}
    assert "require_data_consent" not in args[1]
    assert "data_consent" not in args[1]
    assert kwargs["tool_schema_version"] == "v1"
    assert kwargs["call_identity"].startswith("main_turn_")
    assert invoker.calls[0]["model"].__class__ is _FakeGateway


def test_default_model_factory_constructs_gateway_model(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _FakeGateway:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr("services.agent_service.model.GatewayModel", _FakeGateway)

    result = _default_model_factory("executor", {"scenario_id": "agent_research"}, "snapshot")

    assert isinstance(result, _FakeGateway)
    assert captured["args"] == ("executor", {"scenario_id": "agent_research"}, "snapshot")
    assert captured["kwargs"] == {}


@pytest.mark.asyncio
async def test_resolve_route_uses_agent_research_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _fake_resolve(scenario_id: str) -> Any:
        calls.append(scenario_id)
        return _ROUTE

    monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _fake_resolve)

    assert await _resolve_route() is _ROUTE
    assert calls == ["agent_research"]


@pytest.mark.asyncio
async def test_invoke_agent_builds_single_no_tool_governed_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[_RecordingAgent] = []

    def _fake_agent(model: Any, **kwargs: Any) -> _RecordingAgent:
        agent = _RecordingAgent(model, **kwargs)
        created.append(agent)
        return agent

    monkeypatch.setattr("pydantic_ai.Agent", _fake_agent)

    output = await _invoke_agent(
        "model-x",
        instructions="只回答",
        prompt="问题文本",
        output_type=_AnswerDraft,
    )

    assert isinstance(output, _AnswerDraft)
    assert len(created) == 1
    agent = created[0]
    assert agent.model == "model-x"
    assert agent.kwargs["name"] == MAIN_TURN_AGENT_NAME
    assert agent.kwargs["output_type"] is _AnswerDraft
    assert agent.kwargs["defer_model_check"] is True
    assert agent.kwargs["retries"] == 0
    assert agent.kwargs["instructions"] == "只回答"
    assert agent.kwargs["tools"] == ()
    assert agent.runs == ["问题文本"]


def test_invalid_mode_rejected_before_any_prompt_build() -> None:
    request = cast(Any, SimpleNamespace(mode="discuss", prompt_text="问题", identity=_IDENTITY))

    with pytest.raises(AgentServiceError) as excinfo:
        _turn_prompt(request)

    assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
    assert excinfo.value.identity == "execution-1"


@pytest.mark.parametrize("answer", ["", "   ", 123])
def test_answer_decision_rejects_missing_answer(answer: Any) -> None:
    with pytest.raises(AgentServiceError) as excinfo:
        _answer_decision(_IDENTITY, SimpleNamespace(answer=answer))

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
    assert excinfo.value.sanitized_message == "empty main turn answer"


@pytest.mark.parametrize(
    "step",
    [
        SimpleNamespace(id="", content="有内容", depends_on=[]),
        SimpleNamespace(id="s1", content="   ", depends_on=[]),
        SimpleNamespace(id=3, content="有内容", depends_on=[]),
    ],
)
def test_plan_decision_rejects_invalid_step(step: Any) -> None:
    with pytest.raises(AgentServiceError) as excinfo:
        _plan_decision(_IDENTITY, SimpleNamespace(summary="", steps=[step]))

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
    assert excinfo.value.sanitized_message == "invalid plan step at index 0"


def test_plan_decision_rejects_empty_steps() -> None:
    with pytest.raises(AgentServiceError) as excinfo:
        _plan_decision(_IDENTITY, SimpleNamespace(summary="摘要", steps=[]))

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
    assert excinfo.value.sanitized_message == "empty plan steps"


def test_plan_decision_drops_non_text_summary_and_non_text_dependencies() -> None:
    output = SimpleNamespace(
        summary=42,
        steps=[SimpleNamespace(id="s1", content="梳理行业", depends_on=["s0", 3, ""])],
    )

    decision = _plan_decision(_IDENTITY, output)

    assert decision.action is LoopAction.PLAN
    assert decision.text == "1. 梳理行业"
    decoded = decode_json_doc(decision.structured_fields or "{}")
    assert isinstance(decoded, dict)
    assert decoded["steps"] == [{"id": "s1", "content": "梳理行业", "depends_on": ["s0"]}]


# ---------------------------------------------------------------------------
# 会话模型选择（首选连接）透传路由解析
# ---------------------------------------------------------------------------


class _PreferredRouteRecorder:
    """记录首选连接的零副作用路由替身（仅支持关键字调用形态）。"""

    def __init__(self, route: Any) -> None:
        self.route = route
        self.preferred: list[str | None] = []

    async def __call__(self, *, preferred_connection_id: str | None = None) -> Any:
        self.preferred.append(preferred_connection_id)
        return self.route


@pytest.mark.asyncio
async def test_request_preferred_connection_is_threaded_to_route_resolution() -> None:
    route = _PreferredRouteRecorder(_ROUTE)
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))
    request = MainTurnRequest(
        identity=_IDENTITY,
        mode=Mode.DISCUSS,
        prompt_text="问题",
        preferred_connection_id="conn-local",
    )

    decision = await run_governed_turn(
        request,
        _CONTEXT,
        resolve_route=cast(Any, route),
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert decision.action is LoopAction.ANSWER
    assert route.preferred == ["conn-local"]


@pytest.mark.asyncio
async def test_request_preferred_connection_is_single_source_of_truth() -> None:
    """生效模型选择唯一取请求冻结值（无第二个关键字来源可覆盖）。"""

    route = _PreferredRouteRecorder(_ROUTE)
    invoker = _Invoker(_TurnEnvelope(answer=_AnswerDraft(answer="答")))
    request = MainTurnRequest(
        identity=_IDENTITY,
        mode=Mode.DISCUSS,
        prompt_text="问题",
        preferred_connection_id="conn-override",
    )

    await run_governed_turn(
        request,
        _CONTEXT,
        resolve_route=cast(Any, route),
        build_model=_ModelFactory(),
        invoke_agent=invoker,
    )

    assert route.preferred == ["conn-override"]


@pytest.mark.asyncio
async def test_resolve_route_threads_preferred_connection_to_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[str] = []

    async def _fake_resolve(scenario_id: str, *, preferred_connection_id: str | None = None) -> Any:
        captured.append(f"{scenario_id}|{preferred_connection_id}")
        return _ROUTE

    monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _fake_resolve)

    assert await _resolve_route("conn-local") is _ROUTE
    assert captured == ["agent_research|conn-local"]
