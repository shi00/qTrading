"""R2-I08 唯一执行循环单测：模式裁剪 / 一层委派 / 成果可靠保存（零外部 IO）。"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    EventFact,
    MessageRole,
    Mode,
    RecordEventRequest,
    ResearchResult,
    ResearchResultDraft,
    json_doc,
)
from services.agent_service.loop import (
    DelegationRequest,
    DelegationResult,
    DelegationScope,
    DiscussionAnswerRecorder,
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


class _RoundBoundStore:
    """生产语义替身：轮归属核定失败即 ``INPUT_INVALID``（§9.7 ``save_research_result``）。"""

    def __init__(self, *, bound_rounds: frozenset[str] = frozenset()) -> None:
        self.drafts: list[ResearchResultDraft] = []
        self._bound_rounds = bound_rounds

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult:
        self.drafts.append(draft)
        if draft.round_id not in self._bound_rounds:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "round does not belong to execution",
                draft.round_id,
            )
        return ResearchResult.from_draft(draft, artifact_id="artifact-1", artifact_version=1)


class _MessageStore:
    """会话公开消息写入端口替身（记录请求，不落库）。"""

    def __init__(self) -> None:
        self.requests: list[RecordEventRequest] = []

    async def record_event(self, request: RecordEventRequest) -> EventFact:
        self.requests.append(request)
        return EventFact(
            message_id=request.message_id,
            event_id="event-1",
            session_seq=1,
            execution_seq=None,
            session_event_seq=1,
            execution_event_seq=None,
            committed_at="2026-06-01T12:00:00.000000Z",
            replayed=False,
        )


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
    store: _RoundBoundStore | _Store,
    tools: _RecordingTools | None = None,
    delegate: _RecordingDelegate | None = None,
    limits: LoopLimits | None = None,
    read_tool_names: frozenset[str] = frozenset(),
    answer_messages: DiscussionAnswerRecorder | None = None,
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
        answer_messages=answer_messages,
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
    discuss = mode_policy(Mode.DISCUSS, read_tool_names=frozenset({"read_source"}))
    assert discuss.actions == frozenset({LoopAction.ANSWER, LoopAction.CLARIFY, LoopAction.TOOL_CALL})
    assert discuss.tool_names == frozenset({"read_source"})
    assert discuss.allow_delegation is False

    plan = mode_policy(Mode.PLAN)
    assert plan.actions == frozenset({LoopAction.PLAN, LoopAction.CLARIFY, LoopAction.TOOL_CALL})
    assert plan.tool_names == frozenset({"write_plan", "read_plan"})
    assert plan.allow_delegation is False

    execute = mode_policy(Mode.EXECUTE)
    assert execute.actions == frozenset(
        {LoopAction.ANSWER, LoopAction.CLARIFY, LoopAction.RESULT, LoopAction.TOOL_CALL, LoopAction.DELEGATE}
    )
    assert execute.tool_names is None
    assert execute.allow_delegation is True


def test_execute_mode_can_answer_directly_without_tools_or_delegation() -> None:
    """直答是动作而非模式：执行模式下简单目标可直接作答，零工具零委派。"""

    store = _Store()
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接回答")])

    outcome = asyncio.run(_loop(mode=Mode.EXECUTE, turn=turn, store=store, tools=tools, delegate=delegate).run())

    assert outcome.state is LoopRunState.ANSWERED
    assert outcome.result.result_kind == "explanation"
    assert outcome.result.body_text == "直接回答"
    assert outcome.result.final_output_saved is True
    assert tools.calls == [] and delegate.calls == []
    assert len(turn.contexts) == 1


def test_discuss_mode_only_reads_approved_sources_then_answers() -> None:
    store = _Store()
    messages = _MessageStore()
    tools = _RecordingTools()
    turn = _ScriptedTurn([_tool("read_source"), TurnDecision(action=LoopAction.ANSWER, text="理解结论")])

    outcome = asyncio.run(
        _loop(
            mode=Mode.DISCUSS,
            turn=turn,
            store=store,
            tools=tools,
            read_tool_names=frozenset({"read_source"}),
            answer_messages=DiscussionAnswerRecorder(messages),
        ).run()
    )

    assert outcome.state is LoopRunState.ANSWERED
    assert [call.tool_name for call in tools.calls] == ["read_source"]
    assert turn.contexts[1].observations[0].kind == "tool"
    assert [request.body_text for request in messages.requests] == ["理解结论"]
    assert store.drafts == []


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


def test_discuss_answer_is_not_a_round_bound_research_result() -> None:
    """讨论直答不是研究轮成果：讨论模式不建轮，不得走轮绑定成果保存（§9.7）。"""

    store = _RoundBoundStore()
    messages = _MessageStore()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="理解结论")])

    outcome = asyncio.run(
        _loop(
            mode=Mode.DISCUSS,
            turn=turn,
            store=store,
            answer_messages=DiscussionAnswerRecorder(messages),
        ).run()
    )

    assert outcome.state is LoopRunState.ANSWERED
    assert outcome.result is None
    assert store.drafts == []
    assert [
        (request.role, request.body_text, request.execution_id, request.round_id, request.session_id)
        for request in messages.requests
    ] == [(MessageRole.ASSISTANT, "理解结论", None, None, "s1")]


def test_discuss_answer_outcome_carries_persisted_answer_text() -> None:
    """讨论直答终态显式携带已确认落库的答复文本（VM 结论区权威来源，不伪造）。"""

    store = _RoundBoundStore()
    messages = _MessageStore()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="讨论结论正文")])

    outcome = asyncio.run(
        _loop(
            mode=Mode.DISCUSS,
            turn=turn,
            store=store,
            answer_messages=DiscussionAnswerRecorder(messages),
        ).run()
    )

    assert outcome.result is None
    assert [request.body_text for request in messages.requests] == ["讨论结论正文"]
    # 结论区文本必须来自同一份已落库的公开消息，而非另一份内存副本。
    assert outcome.discussion_answer_text == messages.requests[0].body_text


def test_non_discuss_outcomes_carry_no_discussion_answer_text() -> None:
    """非讨论终态不携带讨论答复文本（讨论/非讨论区分，避免模式串话）。"""

    execute = asyncio.run(
        _loop(
            mode=Mode.EXECUTE,
            turn=_ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="执行直答")]),
            store=_Store(),
        ).run()
    )
    assert execute.result is not None and execute.result.body_text == "执行直答"
    assert execute.discussion_answer_text is None

    clarify = asyncio.run(
        _loop(
            mode=Mode.DISCUSS,
            turn=_ScriptedTurn(
                [
                    TurnDecision(
                        action=LoopAction.CLARIFY,
                        text="关注行业还是个股？",
                        structured_fields=json_doc(
                            {"question": "关注行业还是个股？", "options": ["行业", "个股"], "impact": "口径"}
                        ),
                    )
                ]
            ),
            store=_Store(),
        ).run()
    )
    assert clarify.state is LoopRunState.CLARIFIED
    assert clarify.discussion_answer_text is None


def test_discuss_answer_without_message_store_fails_closed() -> None:
    """未接线消息端口即失败：不以内存文本冒充已保存答复（R21）。"""

    store = _RoundBoundStore()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="理解结论")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.DISCUSS, turn=turn, store=store).run())

    assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE
    assert store.drafts == []


def test_execute_direct_answer_still_saves_round_bound_result() -> None:
    """执行模式直答保持原行为：仍走轮绑定成果保存，不写会话消息。"""

    store = _RoundBoundStore(bound_rounds=frozenset({"round-1"}))
    messages = _MessageStore()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接回答")])

    outcome = asyncio.run(
        _loop(
            mode=Mode.EXECUTE,
            turn=turn,
            store=store,
            answer_messages=DiscussionAnswerRecorder(messages),
        ).run()
    )

    assert outcome.state is LoopRunState.ANSWERED
    assert outcome.result is not None
    assert outcome.result.final_output_saved is True
    assert [(draft.round_id, draft.result_kind) for draft in store.drafts] == [("round-1", "explanation")]
    assert messages.requests == []


def test_plan_mode_delivers_plan_and_never_auto_executes() -> None:
    """计划模式零派发不建轮：计划步骤随终态交付，不写轮绑定成果（§9.7）。"""

    store = _RoundBoundStore()
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    fields = json_doc({"steps": [{"id": "step-1", "content": "核对财报", "depends_on": []}]})
    turn = _ScriptedTurn(
        [TurnDecision(action=LoopAction.PLAN, text="先核对财报，再比较估值", structured_fields=fields)]
    )

    outcome = asyncio.run(_loop(mode=Mode.PLAN, turn=turn, store=store, tools=tools, delegate=delegate).run())

    assert outcome.state is LoopRunState.PLANNED
    assert outcome.result is None
    assert outcome.unsaved_structured_fields == fields
    assert len(turn.contexts) == 1
    assert tools.calls == [] and delegate.calls == []
    assert store.drafts == []


def test_plan_mode_rejects_answer_instead_of_plan() -> None:
    store = _Store()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接给结论")])

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(_loop(mode=Mode.PLAN, turn=turn, store=store).run())

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert store.drafts == []


def test_discuss_mode_clarify_returns_clarified_state_without_saving_result() -> None:
    store = _Store()
    turn = _ScriptedTurn(
        [
            TurnDecision(
                action=LoopAction.CLARIFY,
                text="你关注的是行业景气还是个股估值？",
                structured_fields=json_doc(
                    {
                        "question": "你关注的是行业景气还是个股估值？",
                        "options": ["行业", "个股"],
                        "impact": "决定后续分析口径",
                    }
                ),
            )
        ]
    )

    outcome = asyncio.run(_loop(mode=Mode.DISCUSS, turn=turn, store=store).run())

    assert outcome.state is LoopRunState.CLARIFIED
    assert outcome.result is None
    assert store.drafts == []
    assert outcome.clarification is not None
    assert outcome.clarification.action is LoopAction.CLARIFY
    assert outcome.clarification.text == "你关注的是行业景气还是个股估值？"


def test_plan_mode_clarify_does_not_produce_plan_result() -> None:
    store = _Store()
    turn = _ScriptedTurn(
        [
            TurnDecision(
                action=LoopAction.CLARIFY,
                text="研究哪段时间范围？",
                structured_fields=json_doc({"question": "研究哪段时间范围？", "options": [], "impact": ""}),
            )
        ]
    )

    outcome = asyncio.run(_loop(mode=Mode.PLAN, turn=turn, store=store).run())

    assert outcome.state is LoopRunState.CLARIFIED
    assert outcome.result is None
    assert store.drafts == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"text": "", "structured_fields": json_doc({"question": "q"})},
        {"text": "问题", "structured_fields": None},
    ],
)
def test_clarify_requires_question_text_and_structured_fields(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError) as excinfo:
        TurnDecision(action=LoopAction.CLARIFY, **kwargs)  # type: ignore[arg-type]  # 参数化验证构造校验

    assert "CLARIFY" in str(excinfo.value)


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
