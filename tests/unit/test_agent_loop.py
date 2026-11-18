"""R2-I08 唯一执行循环单测：模式裁剪 / 一层委派 / 成果可靠保存（零外部 IO）。"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    Mode,
    ResearchResult,
    ResearchResultDraft,
    json_doc,
)
from services.agent_service.loop import (
    DelegationRequest,
    DelegationResult,
    DelegationScope,
    ExecutionLoop,
    LoopAction,
    LoopIdentity,
    LoopLimits,
    LoopRunState,
    ResearchResultProducer,
    ToolCall,
    TurnContext,
    TurnDecision,
    mode_policy,
)

_IDENTITY = LoopIdentity(
    user_scope="u1",
    session_id="s1",
    execution_id="execution-1",
    round_id="round-1",
    node_id="root",
    input_revision=1,
    actual_input_digest="a" * 64,
    actual_model_ref="model-ref",
)


class _Store:
    def __init__(self, *, error: BaseException | None = None, unconfirmed: bool = False) -> None:
        self.drafts: list[ResearchResultDraft] = []
        self._error = error
        self._unconfirmed = unconfirmed

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult:
        self.drafts.append(draft)
        if self._error is not None:
            raise self._error
        if self._unconfirmed:
            return cast(ResearchResult, object())
        return ResearchResult.from_draft(draft, artifact_id="artifact-1", artifact_version=1)


class _ScriptedTurn:
    def __init__(self, decisions: list[TurnDecision]) -> None:
        self._decisions = decisions
        self.contexts: list[TurnContext] = []

    async def __call__(self, context: TurnContext) -> TurnDecision:
        self.contexts.append(context)
        return self._decisions.pop(0)


class _RecordingTools:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[ToolCall] = []
        self._error = error

    async def __call__(self, call: ToolCall) -> str:
        self.calls.append(call)
        if self._error is not None:
            raise self._error
        return "tool-ok"


class _RecordingDelegate:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[DelegationRequest, DelegationScope]] = []
        self._error = error

    async def __call__(self, request: DelegationRequest, scope: DelegationScope) -> DelegationResult:
        self.calls.append((request, scope))
        if self._error is not None:
            raise self._error
        return DelegationResult(node_id=request.node_id, state="complete", summary="subagent-ok")


def _loop(
    *,
    mode: Mode,
    turn: _ScriptedTurn,
    store: _Store,
    tools: _RecordingTools | None = None,
    delegate: _RecordingDelegate | None = None,
    limits: LoopLimits | None = None,
    read_tool_names: frozenset[str] = frozenset(),
) -> ExecutionLoop:
    return ExecutionLoop(
        identity=_IDENTITY,
        mode=mode,
        turn=turn,
        producer=ResearchResultProducer(store),
        tools=tools,
        delegate=delegate,
        limits=limits,
        read_tool_names=read_tool_names,
    )


def _tool(name: str = "read_source") -> TurnDecision:
    return TurnDecision(action=LoopAction.TOOL_CALL, tool_call=ToolCall(name, json_doc({"q": "x"})))


def _delegate_decision() -> TurnDecision:
    return TurnDecision(
        action=LoopAction.DELEGATE,
        delegation=DelegationRequest(node_id="n1", target="梳理财务", role_key="analyst"),
    )


# --- 模式策略 ---------------------------------------------------------------


def test_mode_policy_binds_modes_to_loop_capabilities() -> None:
    answer = mode_policy(Mode.ANSWER)
    assert answer.actions == frozenset({LoopAction.ANSWER})
    assert answer.tool_names == frozenset()
    assert answer.allow_delegation is False

    discuss = mode_policy(Mode.DISCUSS, read_tool_names=frozenset({"read_source"}))
    assert discuss.actions == frozenset({LoopAction.ANSWER, LoopAction.TOOL_CALL})
    assert discuss.tool_names == frozenset({"read_source"})
    assert discuss.allow_delegation is False

    plan = mode_policy(Mode.PLAN)
    assert plan.actions == frozenset({LoopAction.PLAN, LoopAction.TOOL_CALL})
    assert plan.tool_names == frozenset({"write_plan", "read_plan"})
    assert plan.allow_delegation is False

    execute = mode_policy(Mode.EXECUTE)
    assert execute.actions == frozenset(
        {LoopAction.ANSWER, LoopAction.RESULT, LoopAction.TOOL_CALL, LoopAction.DELEGATE}
    )
    assert execute.tool_names is None
    assert execute.allow_delegation is True


def test_answer_mode_directly_answers_without_tools_or_delegation() -> None:
    store = _Store()
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接回答")])

    outcome = asyncio.run(_loop(mode=Mode.ANSWER, turn=turn, store=store, tools=tools, delegate=delegate).run())

    assert outcome.state is LoopRunState.ANSWERED
    assert outcome.result.result_kind == "explanation"
    assert outcome.result.body_text == "直接回答"
    assert outcome.result.final_output_saved is True
    assert tools.calls == [] and delegate.calls == []
    assert len(turn.contexts) == 1


def test_discuss_mode_only_reads_approved_sources_then_answers() -> None:
    store = _Store()
    tools = _RecordingTools()
    turn = _ScriptedTurn([_tool("read_source"), TurnDecision(action=LoopAction.ANSWER, text="理解结论")])

    outcome = asyncio.run(
        _loop(
            mode=Mode.DISCUSS,
            turn=turn,
            store=store,
            tools=tools,
            read_tool_names=frozenset({"read_source"}),
        ).run()
    )

    assert outcome.state is LoopRunState.ANSWERED
    assert [call.tool_name for call in tools.calls] == ["read_source"]
    assert turn.contexts[1].observations[0].kind == "tool"


def test_discuss_mode_rejects_research_tool_before_execution() -> None:
    store = _Store()
    tools = _RecordingTools()
    turn = _ScriptedTurn([_tool("mcp_research")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(
            _loop(
                mode=Mode.DISCUSS,
                turn=turn,
                store=store,
                tools=tools,
                read_tool_names=frozenset({"read_source"}),
            ).run()
        )

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert tools.calls == []
    assert store.drafts == []


def test_plan_mode_delivers_plan_and_never_auto_executes() -> None:
    store = _Store()
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.PLAN, text="先核对财报，再比较估值")])

    outcome = asyncio.run(_loop(mode=Mode.PLAN, turn=turn, store=store, tools=tools, delegate=delegate).run())

    assert outcome.state is LoopRunState.PLANNED
    assert outcome.result.result_kind == "plan"
    assert outcome.result.execution_state == "planned"
    assert len(turn.contexts) == 1
    assert tools.calls == [] and delegate.calls == []
    assert store.drafts[0].result_kind == "plan"
    assert store.drafts[0].convergence_confirmed is False


def test_plan_mode_rejects_answer_instead_of_plan() -> None:
    store = _Store()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接给结论")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.PLAN, turn=turn, store=store).run())

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert store.drafts == []


# --- 执行 / 一层委派 ---------------------------------------------------------


def test_execute_mode_runs_tools_and_one_layer_delegation() -> None:
    store = _Store()
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    turn = _ScriptedTurn(
        [
            _tool("market_data"),
            _delegate_decision(),
            TurnDecision(
                action=LoopAction.RESULT,
                text="研究结论",
                result_kind="stock_final",
                goal_coverage="full",
                convergence_confirmed=True,
            ),
        ]
    )

    outcome = asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store, tools=tools, delegate=delegate).run())

    assert outcome.state is LoopRunState.RESEARCHED
    assert outcome.tool_calls == 1 and outcome.delegations == 1
    assert outcome.result.execution_state == "complete"
    assert [call.tool_name for call in tools.calls] == ["market_data"]
    request, scope = delegate.calls[0]
    assert request.node_id == "n1"
    assert scope == DelegationScope(depth=1, allow_delegation=False)
    assert turn.contexts[2].observations[-1].state == "complete"


def test_non_execute_mode_rejects_delegation_before_dispatch() -> None:
    store = _Store()
    delegate = _RecordingDelegate()
    turn = _ScriptedTurn([_delegate_decision()])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.DISCUSS, turn=turn, store=store, delegate=delegate).run())

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert delegate.calls == []
    assert store.drafts == []


def test_subagent_loop_cannot_delegate_again() -> None:
    store = _Store()
    delegate = _RecordingDelegate()
    sub_turn = _ScriptedTurn([_delegate_decision()])
    sub_loop = ExecutionLoop(
        identity=LoopIdentity(
            user_scope="u1",
            session_id="s1",
            execution_id="execution-1",
            round_id="round-1",
            node_id="n1",
            input_revision=1,
            actual_input_digest="a" * 64,
            actual_model_ref="model-ref",
        ),
        mode=Mode.EXECUTE,
        turn=sub_turn,
        producer=ResearchResultProducer(store),
        delegate=delegate,
        depth=1,
    )

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(sub_loop.run())

    # depth=1 的子循环即使策略允许委派也被结构性拒绝（不递归）。
    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert delegate.calls == []


def test_delegation_failure_propagates_and_saves_nothing() -> None:
    store = _Store()
    delegate = _RecordingDelegate(error=RuntimeError("subagent crashed"))
    turn = _ScriptedTurn([_delegate_decision()])

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store, delegate=delegate).run())

    assert str(excinfo.value) == "subagent crashed"
    assert store.drafts == []


# --- 保存 / 失败 / 取消 / 并发 ------------------------------------------------


def test_save_failure_never_returns_success() -> None:
    store = _Store(error=AgentServiceError(AgentErrorCode.PERSISTENCE_UNAVAILABLE, "db down", "x"))
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="答案")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store).run())

    assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


def test_unconfirmed_save_is_rejected() -> None:
    store = _Store(unconfirmed=True)
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="答案")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store).run())

    assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


def test_tool_cancelled_error_propagates_without_save() -> None:
    store = _Store()
    tools = _RecordingTools(error=asyncio.CancelledError())
    turn = _ScriptedTurn([_tool("market_data")])

    with pytest.raises(asyncio.CancelledError) as excinfo:
        asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store, tools=tools).run())

    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert store.drafts == []


def test_model_unavailable_propagates_without_save() -> None:
    store = _Store()

    async def _turn(_context: TurnContext) -> TurnDecision:
        raise RuntimeError("model unavailable")

    loop = ExecutionLoop(
        identity=_IDENTITY,
        mode=Mode.EXECUTE,
        turn=_turn,
        producer=ResearchResultProducer(store),
    )

    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(loop.run())

    assert str(excinfo.value) == "model unavailable"
    assert store.drafts == []


def test_turn_limit_is_explicitly_blocked() -> None:
    store = _Store()
    tools = _RecordingTools()
    turn = _ScriptedTurn([_tool("market_data"), _tool("market_data")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(
            _loop(
                mode=Mode.EXECUTE,
                turn=turn,
                store=store,
                tools=tools,
                limits=LoopLimits(max_turns=1),
            ).run()
        )

    assert excinfo.value.code is AgentErrorCode.BUDGET_BLOCKED
    assert store.drafts == []


def test_concurrent_run_is_rejected() -> None:
    store = _Store()
    started = asyncio.Event()
    release = asyncio.Event()

    async def _turn(_context: TurnContext) -> TurnDecision:
        started.set()
        await release.wait()
        return TurnDecision(action=LoopAction.ANSWER, text="答案")

    loop = ExecutionLoop(
        identity=_IDENTITY,
        mode=Mode.EXECUTE,
        turn=_turn,
        producer=ResearchResultProducer(store),
    )

    async def _scenario() -> None:
        first = asyncio.create_task(loop.run())
        await started.wait()
        with pytest.raises(AgentServiceError) as excinfo:
            await loop.run()
        assert excinfo.value.code is AgentErrorCode.INPUT_INVALID
        release.set()
        await first

    asyncio.run(_scenario())


# --- 成果合同（R21） ---------------------------------------------------------


def _draft(**overrides: object) -> ResearchResultDraft:
    values: dict[str, object] = {
        "user_scope": "u1",
        "session_id": "s1",
        "execution_id": "execution-1",
        "round_id": "round-1",
        "node_id": "root",
        "input_revision": 1,
        "actual_input_digest": "a" * 64,
        "actual_model_ref": "model-ref",
        "result_kind": "explanation",
        "summary": None,
        "key_points": (),
        "body_text": "答案",
        "structured_fields": None,
        "object_rows": (),
        "evidence_links": (),
        "source_available_state": "unknown",
        "coverage": None,
        "missing_fields": (),
        "failed_signal_dates": (),
        "data_warnings": (),
        "limitations": (),
        "verification_state": "unverified",
        "execution_state": "running",
        "goal_coverage": "unknown",
        "convergence_confirmed": False,
        "unknown_operation_refs": (),
        "ledger_refs": (),
        "plan_version": None,
        "parent_artifact_refs": (),
        "source_refs": (),
    }
    values.update(overrides)
    return ResearchResultDraft(**values)  # type: ignore[arg-type]


def test_empty_result_is_rejected_instead_of_faked_success() -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        _draft(body_text=None, summary=None, key_points=(), structured_fields=None, object_rows=())

    assert excinfo.value.code == "empty_result"


def test_missing_quality_fields_are_preserved_not_filled() -> None:
    draft = _draft(missing_fields=("score",), data_warnings=("source unavailable",))

    assert draft.summary is None
    assert draft.goal_coverage == "unknown"
    assert draft.missing_fields == ("score",)
    assert draft.convergence_confirmed is False
