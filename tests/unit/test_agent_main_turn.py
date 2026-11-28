"""P1-02 主回合/计划/角色能力生产闭环单测（零外部 IO）。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ClarificationAnswerFact,
    CommandRef,
    CommitState,
    EventFact,
    MCPServiceIdentity,
    MessageRole,
    Mode,
    PlanItemSnapshot,
    PlanNodeSpec,
    PlanProposal,
    PlanSpecProposal,
    Purpose,
    RecordEventRequest,
    ResearchResult,
    ResearchResultDraft,
    SessionAction,
    SubmissionReceipt,
    encode_mcp_service_identity,
    json_doc,
)
from services.agent_service.dispatch import (
    CAPABILITY_POLICY_UNAVAILABLE,
    DISPATCH_CLOSED,
    PLAN_ADMISSION_UNCONFIGURED,
    PLAN_UNAVAILABLE,
    DispatchOutcome,
    ResearchDispatcher,
    ResearchWorkflowResult,
)
from services.agent_service.workflows import ParentBindIdentity
from services.agent_service.loop import (
    DelegationRequest,
    DelegationResult,
    DelegationScope,
    DiscussionAnswerRecorder,
    LoopAction,
    LoopIdentity,
    ResearchResultProducer,
    ToolCall,
    TurnContext,
    TurnDecision,
)
from services.agent_service.main_turn import (
    FrozenSelectionPolicy,
    MainTurnIntake,
    _capture_frozen_role_prompt_snapshots,
    MainTurnRequest,
    MainTurnRunner,
    PlanReference,
    build_plan_spec_provider,
    build_role_capability_provider,
    decode_plan_reference,
    derive_frozen_selection_policy,
    plan_spec_from_proposal,
    plan_steps_from_structured_fields,
)
from services.agent_service.session_model import SessionModelStore

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


# ---------------------------------------------------------------------------
# 冻结手选集合 → 角色能力
# ---------------------------------------------------------------------------


def _manifest(**overrides: Any) -> str:
    base: dict[str, Any] = {
        "schema_version": 1,
        "text": "研究问题",
        "mode": "execute",
        "purpose": "concept",
        "cutoff": None,
        "source_refs": [],
        "role_prompt_refs": [["researcher", "v1"]],
        "skill_refs": [["skill-a", "2"]],
        "mcp_scope_digest": None,
    }
    base.update(overrides)
    return json_doc(base)


def test_policy_derives_role_and_skill_capability() -> None:
    policy = derive_frozen_selection_policy(_manifest())

    assert policy is not None
    assert policy.role_key == "researcher"
    assert policy.capabilities == frozenset({"skill:skill-a@2"})


def test_policy_includes_mcp_scope_digest_as_capability() -> None:
    policy = derive_frozen_selection_policy(_manifest(skill_refs=[], mcp_scope_digest="d" * 16))

    assert policy is not None
    assert policy.role_key == "researcher"
    assert policy.capabilities == frozenset({f"mcp_scope:{'d' * 16}"})


def test_base_manifest_freezes_mcp_selection_sorted_and_deduplicated() -> None:
    intake = object.__new__(MainTurnIntake)
    a = MCPServiceIdentity(service_id="svc-a", config_version=1)
    b = MCPServiceIdentity(service_id="svc-b", config_version=2)

    manifest = json.loads(
        intake._base_manifest(text="研究问题", mode=Mode.DISCUSS, purpose=Purpose.CONCEPT, mcp_selection=(b, a, b))
    )

    assert {frozenset(item.items()) for item in manifest["mcp_selection"]} == {
        frozenset(encode_mcp_service_identity(a).items()),
        frozenset(encode_mcp_service_identity(b).items()),
    }
    canonical = json.dumps(manifest["mcp_selection"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert manifest["mcp_scope_digest"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_base_manifest_empty_mcp_selection_keeps_digest_none() -> None:
    intake = object.__new__(MainTurnIntake)

    manifest = json.loads(intake._base_manifest(text="研究问题", mode=Mode.DISCUSS, purpose=Purpose.CONCEPT))

    assert manifest["mcp_selection"] == []
    assert manifest["mcp_scope_digest"] is None


def test_base_manifest_freezes_builtin_role_prompt_snapshot() -> None:
    intake = object.__new__(MainTurnIntake)

    manifest = json.loads(
        intake._base_manifest(
            text="研究问题",
            mode=Mode.DISCUSS,
            purpose=Purpose.CONCEPT,
            role_prompt_refs=(("main", "v-1"),),
            role_prompt_snapshots=({"role_id": "main", "version": "v-1", "text": "冻结正文"},),
        )
    )

    assert manifest["role_prompt_refs"] == [["main", "v-1"]]
    assert manifest["role_prompt_snapshots"] == [{"role_id": "main", "version": "v-1", "text": "冻结正文"}]


def test_capture_role_prompt_snapshot_only_builtin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("utils.config.llm.read_research_role_prompt", lambda role_key: (f"正文-{role_key}", "v-1"))

    snapshots = _capture_frozen_role_prompt_snapshots((("main", "v-1"), ("researcher", "v-1")), user_scope="u1")

    assert snapshots == ({"role_id": "main", "version": "v-1", "text": "正文-main"},)


def test_capture_role_prompt_snapshot_version_mismatch_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("utils.config.llm.read_research_role_prompt", lambda role_key: ("正文", "v-2"))

    with pytest.raises(AgentServiceError) as excinfo:
        _capture_frozen_role_prompt_snapshots((("main", "v-1"),), user_scope="u1")

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE


@pytest.mark.asyncio
async def test_intake_freezes_builtin_role_prompt_snapshot_into_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("utils.config.llm.read_research_role_prompt", lambda role_key: ("受理时正文", "v-1"))
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    await intake.start_question("执行研究", mode=Mode.EXECUTE, role_prompt_refs=(("main", "v-1"),))

    manifest = json.loads(facade.accepts[0].manifest_payload)
    assert manifest["role_prompt_refs"] == [["main", "v-1"]]
    assert manifest["role_prompt_snapshots"] == [{"role_id": "main", "version": "v-1", "text": "受理时正文"}]


def test_policy_empty_selection_is_fail_closed() -> None:
    assert derive_frozen_selection_policy(_manifest(role_prompt_refs=[])) is None


def test_policy_role_without_skills_or_mcp_is_approved_empty_capability_set() -> None:
    """已选定角色且显式未勾选 Skill/MCP：零能力是已核准集合，不是策略缺失。"""

    policy = derive_frozen_selection_policy(_manifest(skill_refs=[], mcp_scope_digest=None))

    assert policy is not None
    assert policy.role_key == "researcher"
    assert policy.capabilities == frozenset()


def test_policy_missing_selection_keys_is_unknown() -> None:
    """缺少 skill_refs/mcp_scope_digest 键：无法区分显式零选择与缺失，按未知处理。"""

    manifest = json_doc(
        {
            "schema_version": 1,
            "text": "研究问题",
            "mode": "execute",
            "purpose": "concept",
            "cutoff": None,
            "source_refs": [],
            "role_prompt_refs": [["researcher", "v1"]],
        }
    )

    assert derive_frozen_selection_policy(manifest) is None


def test_policy_multiple_roles_is_ambiguous() -> None:
    manifest = _manifest(role_prompt_refs=[["researcher", "v1"], ["analyst", "v1"]])

    assert derive_frozen_selection_policy(manifest) is None


def test_policy_reads_comparison_shared_section() -> None:
    manifest = json_doc(
        {
            "schema_version": 1,
            "shared": {
                "text": "q",
                "mode": "execute",
                "purpose": "concept",
                "cutoff": None,
                "source_refs": [],
                "role_prompt_refs": [["researcher", "v1"]],
                "skill_refs": [["skill-a", "2"]],
                "mcp_scope_digest": None,
            },
        }
    )

    policy = derive_frozen_selection_policy(manifest)

    assert policy is not None
    assert policy.role_key == "researcher"
    assert policy.capabilities == frozenset({"skill:skill-a@2"})


def test_policy_rejects_malformed_ref_pairs() -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        derive_frozen_selection_policy(_manifest(skill_refs=[["only-id"]]))

    assert excinfo.value.code == "invalid_type"
    assert excinfo.value.field_path.endswith("skill_refs[0]")


# ---------------------------------------------------------------------------
# 计划来源 / 计划引用 / 可执行节点表映射
# ---------------------------------------------------------------------------


def test_decode_plan_reference_absent_returns_none() -> None:
    assert decode_plan_reference(_manifest()) is None


def test_decode_plan_reference_accepts_valid_reference() -> None:
    reference = decode_plan_reference(_manifest(plan_ref={"execution_id": "execution-0", "plan_revision": 2}))

    assert reference == PlanReference(execution_id="execution-0", plan_revision=2)


@pytest.mark.parametrize(
    "plan_ref",
    [
        "not-a-dict",
        {"execution_id": "", "plan_revision": 1},
        {"execution_id": "e", "plan_revision": 0},
        {"execution_id": "e", "plan_revision": True},
        {"execution_id": "e", "plan_revision": 1, "extra": 1},
    ],
)
def test_decode_plan_reference_rejects_invalid(plan_ref: Any) -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        decode_plan_reference(_manifest(plan_ref=plan_ref))

    assert excinfo.value.code in {
        "invalid_type",
        "invalid_value",
        "invalid_revision",
        "invalid_keys_unknown",
    }
    assert "$.manifest_payload.plan_ref" in excinfo.value.field_path


def _policy() -> FrozenSelectionPolicy:
    return FrozenSelectionPolicy(role_key="researcher", capabilities=frozenset({"skill:skill-a@2"}))


def _proposal() -> PlanProposal:
    return PlanProposal(
        execution_id="execution-0",
        plan_revision=1,
        items=(
            PlanItemSnapshot(
                id="step-1",
                content="读取行情",
                status="pending",
                active_form="读取行情",
                parent_id=None,
                depends_on=(),
            ),
            PlanItemSnapshot(
                id="step-2",
                content="汇总结论",
                status="pending",
                active_form="汇总结论",
                parent_id=None,
                depends_on=("step-1",),
            ),
        ),
    )


def test_plan_spec_maps_steps_to_nodes_with_frozen_policy() -> None:
    spec = plan_spec_from_proposal(_proposal(), _policy())

    assert [node.node_id for node in spec.nodes] == ["step-1", "step-2"]
    assert [node.target for node in spec.nodes] == ["读取行情", "汇总结论"]
    assert spec.nodes[1].depends_on == ("step-1",)
    for node in spec.nodes:
        assert node.role_key == "researcher"
        assert node.capabilities == ("skill:skill-a@2",)


def test_plan_spec_rejects_empty_target() -> None:
    proposal = PlanProposal(
        execution_id="e",
        plan_revision=1,
        items=(
            PlanItemSnapshot(
                id="step-1",
                content="",
                status="pending",
                active_form="x",
                parent_id=None,
                depends_on=(),
            ),
        ),
    )

    with pytest.raises(AgentValidationError) as excinfo:
        plan_spec_from_proposal(proposal, _policy())

    assert excinfo.value.code == "invalid_value"
    assert excinfo.value.field_path == "$.plan_proposal.items[0].content"


def test_plan_steps_from_structured_fields_roundtrip() -> None:
    steps = plan_steps_from_structured_fields(
        json_doc({"steps": [{"id": "s1", "content": "步骤一", "depends_on": []}]})
    )

    assert steps is not None
    assert steps[0].id == "s1"
    assert steps[0].content == "步骤一"


@pytest.mark.parametrize(
    "payload",
    [
        {"steps": [{"id": "s1", "content": "x", "depends_on": [], "extra": 1}]},
        {"steps": [{"id": "", "content": "x", "depends_on": []}]},
        {"steps": "nope"},
        {"other": []},
    ],
)
def test_plan_steps_reject_unexpected_shapes(payload: dict[str, Any]) -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        plan_steps_from_structured_fields(json_doc(payload))

    assert excinfo.value.code in {"invalid_keys_unknown", "invalid_type", "invalid_value"}


class _ManifestDao:
    def __init__(
        self,
        manifest: str | None,
        proposal: PlanProposal | None = None,
        *,
        current_proposal: PlanProposal | None = None,
    ) -> None:
        self._manifest = manifest
        self._proposal = proposal
        self._current_proposal = current_proposal

    async def read_authoritative_manifest(self, *, user_scope: str, execution_id: str) -> str | None:
        return self._manifest

    async def read_plan_proposal(
        self, *, user_scope: str, execution_id: str, plan_revision: int
    ) -> PlanProposal | None:
        return self._proposal

    async def read_current_plan_proposal(self, *, user_scope: str, execution_id: str) -> PlanProposal | None:
        return self._current_proposal


@pytest.mark.asyncio
async def test_plan_provider_reads_confirmed_version_and_maps() -> None:
    dao = _ManifestDao(_manifest(plan_ref={"execution_id": "execution-0", "plan_revision": 1}), _proposal())

    spec = await build_plan_spec_provider(dao)("u1", "execution-1")

    assert spec is not None
    assert [node.node_id for node in spec.nodes] == ["step-1", "step-2"]
    assert [node.target for node in spec.nodes] == ["读取行情", "汇总结论"]


@pytest.mark.asyncio
async def test_plan_provider_returns_none_without_reference_or_proposal() -> None:
    assert await build_plan_spec_provider(_ManifestDao(_manifest()))("u1", "e") is None
    no_plan = _ManifestDao(_manifest(plan_ref={"execution_id": "e0", "plan_revision": 1}), None)
    assert await build_plan_spec_provider(no_plan)("u1", "e") is None
    assert await build_plan_spec_provider(_ManifestDao(None))("u1", "e") is None


@pytest.mark.asyncio
async def test_plan_provider_reads_auto_plan_for_execute_first_question() -> None:
    """执行模式首问：manifest 声明 plan_source=auto → 读本执行当前计划版映射。"""

    dao = _ManifestDao(_manifest(plan_source="auto"), current_proposal=_proposal())

    spec = await build_plan_spec_provider(dao)("u1", "execution-1")

    assert spec is not None
    assert [node.node_id for node in spec.nodes] == ["step-1", "step-2"]
    assert [node.target for node in spec.nodes] == ["读取行情", "汇总结论"]


@pytest.mark.asyncio
async def test_plan_provider_auto_plan_requires_saved_version_and_selection() -> None:
    """无保存计划版 / 无角色能力手选 → 不可得（fail-closed，不猜最新计划）。"""

    assert await build_plan_spec_provider(_ManifestDao(_manifest(plan_source="auto")))("u1", "e") is None
    no_selection = _ManifestDao(
        _manifest(plan_source="auto", role_prompt_refs=[], skill_refs=[]),
        current_proposal=_proposal(),
    )
    assert await build_plan_spec_provider(no_selection)("u1", "e") is None


@pytest.mark.asyncio
async def test_capability_provider_fail_closed_without_selection() -> None:
    assert await build_role_capability_provider(_ManifestDao(None))("u1", "e") is None
    empty = await build_role_capability_provider(_ManifestDao(_manifest(skill_refs=[])))("u1", "e")
    assert empty == {"researcher": frozenset()}
    policy = await build_role_capability_provider(_ManifestDao(_manifest()))("u1", "e")
    assert policy == {"researcher": frozenset({"skill:skill-a@2"})}


# ---------------------------------------------------------------------------
# 唯一循环：讨论/计划零研究动作
# ---------------------------------------------------------------------------


class _Store:
    def __init__(self) -> None:
        self.drafts: list[ResearchResultDraft] = []

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult:
        self.drafts.append(draft)
        return ResearchResult.from_draft(draft, artifact_id="artifact-1", artifact_version=1)


class _ScriptedTurn:
    def __init__(self, decisions: list[TurnDecision]) -> None:
        self._decisions = decisions
        self.contexts: list[TurnContext] = []

    async def __call__(self, context: TurnContext) -> TurnDecision:
        self.contexts.append(context)
        return self._decisions.pop(0)


class _RecordingTools:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def __call__(self, call: ToolCall) -> str:
        self.calls.append(call)
        return "tool-ok"


class _RecordingDelegate:
    def __init__(self) -> None:
        self.calls: list[tuple[DelegationRequest, DelegationScope]] = []

    async def __call__(self, request: DelegationRequest, scope: DelegationScope) -> DelegationResult:
        self.calls.append((request, scope))
        return DelegationResult(node_id=request.node_id, state="complete", summary="ok")


class _PlanStore:
    def __init__(self) -> None:
        self.items: list[Any] = []
        self.revision = 0

    async def set_items(self, items: list[Any]) -> None:
        self.items = items
        self.revision = 1


class _MessageStore:
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


@pytest.mark.asyncio
async def test_discuss_turn_answers_with_zero_tools_and_zero_delegation() -> None:
    tools = _RecordingTools()
    delegate = _RecordingDelegate()
    store = _Store()
    messages = _MessageStore()
    runner = MainTurnRunner(
        producer=ResearchResultProducer(store),
        tools=tools,
        delegate=delegate,
        answer_messages=DiscussionAnswerRecorder(messages),
    )
    request = MainTurnRequest(
        identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="研究问题", read_tool_names=frozenset({"read_source"})
    )
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="直接回答")])

    outcome = await runner.run(request, turn=turn)

    # 讨论直答不产出轮绑定成果：答复作为 assistant 公开消息入会话历史。
    assert outcome.outcome.result is None
    assert store.drafts == []
    assert [(r.role, r.body_text, r.execution_id, r.round_id) for r in messages.requests] == [
        (MessageRole.ASSISTANT, "直接回答", None, None)
    ]
    assert outcome.plan_reference is None
    assert tools.calls == []
    assert delegate.calls == []


@pytest.mark.asyncio
async def test_discuss_turn_rejects_delegation_without_calling_delegate() -> None:
    delegate = _RecordingDelegate()
    runner = MainTurnRunner(producer=ResearchResultProducer(_Store()), delegate=delegate)
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.DISCUSS, prompt_text="研究问题")
    turn = _ScriptedTurn(
        [
            TurnDecision(
                action=LoopAction.DELEGATE,
                delegation=DelegationRequest(node_id="leaf-1", target="t", role_key="researcher"),
            )
        ]
    )

    with pytest.raises(AgentServiceError) as excinfo:
        await runner.run(request, turn=turn)

    assert excinfo.value.code is AgentErrorCode.POLICY_DENIED
    assert delegate.calls == []


@pytest.mark.asyncio
async def test_plan_turn_saves_readable_plan_and_returns_reference() -> None:
    store = _PlanStore()
    runner = MainTurnRunner(
        producer=ResearchResultProducer(_Store()),
        plan_store_factory=lambda _identity: store,
    )
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.PLAN, prompt_text="研究问题")
    turn = _ScriptedTurn(
        [
            TurnDecision(
                action=LoopAction.PLAN,
                text="计划正文",
                structured_fields=json_doc({"steps": [{"id": "s1", "content": "步骤一", "depends_on": []}]}),
            )
        ]
    )

    outcome = await runner.run(request, turn=turn)

    assert outcome.plan_reference == PlanReference(execution_id="execution-1", plan_revision=1)
    assert len(store.items) == 1


@pytest.mark.asyncio
async def test_plan_turn_without_structured_steps_has_no_reference() -> None:
    store = _PlanStore()
    runner = MainTurnRunner(
        producer=ResearchResultProducer(_Store()),
        plan_store_factory=lambda _identity: store,
    )
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.PLAN, prompt_text="研究问题")
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.PLAN, text="只有正文")])

    outcome = await runner.run(request, turn=turn)

    assert outcome.plan_reference is None
    assert store.items == []


@pytest.mark.asyncio
async def test_plan_turn_with_empty_steps_does_not_save_version() -> None:
    """空步骤不得冒充「已确认计划」（R21，执行模式首问据此零派发）。"""

    store = _PlanStore()
    runner = MainTurnRunner(
        producer=ResearchResultProducer(_Store()),
        plan_store_factory=lambda _identity: store,
    )
    request = MainTurnRequest(identity=_IDENTITY, mode=Mode.PLAN, prompt_text="研究问题")
    turn = _ScriptedTurn(
        [TurnDecision(action=LoopAction.PLAN, text="计划正文", structured_fields=json_doc({"steps": []}))]
    )

    outcome = await runner.run(request, turn=turn)

    assert outcome.plan_reference is None
    assert store.items == []


# ---------------------------------------------------------------------------
# 派发准入：逐请求能力来源 / 非法图零执行
# ---------------------------------------------------------------------------


class _Gate:
    def dispatch_closed(self) -> bool:
        return False

    def close_dispatch(self) -> bool:
        return True


async def _binding(user_scope: str, execution_id: str) -> ParentBindIdentity:
    return ParentBindIdentity(
        user_scope=user_scope,
        session_id="s1",
        workflow_code_version="1.0.0",
        run_epoch=1,
        expected_input_revision=1,
    )


def _receipt(*, input_revision: int = 1) -> SubmissionReceipt:
    command = CommandRef(
        user_scope="u1",
        source_module="ui",
        business_request_id="b1",
        command_id="c1",
        expected_revision=0,
    )
    return SubmissionReceipt(
        command=command,
        command_record_id="record-1",
        session_id="s1",
        execution_id="execution-1",
        workflow_id="workflow-1",
        intent_digest="f" * 64,
        commit_state=CommitState.COMMITTED,
        verified_at="2026-01-01T00:00:00.000000Z",
        next_action=None,
        input_revision=input_revision,
    )


def _spec(*, cyclic: bool = False) -> PlanSpecProposal:
    nodes = (
        PlanNodeSpec(
            node_id="n1",
            target="t1",
            role_key="researcher",
            depends_on=("n2",) if cyclic else (),
            capabilities=("skill:skill-a@2",),
        ),
        PlanNodeSpec(
            node_id="n2",
            target="t2",
            role_key="researcher",
            depends_on=("n1",) if cyclic else (),
            capabilities=("skill:skill-a@2",),
        ),
    )
    return PlanSpecProposal(nodes=nodes)


@pytest.mark.asyncio
async def test_dispatch_uses_per_request_capability_provider() -> None:
    submitted: list[str] = []

    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        submitted.append(workflow_id)

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _spec()

    async def _caps(user_scope: str, execution_id: str) -> dict[str, frozenset[str]]:
        return {"researcher": frozenset({"skill:skill-a@2"})}

    dispatcher = ResearchDispatcher(
        gate=_Gate(),
        submit=_submit,
        plan_provider=_plan,
        capability_provider=_caps,
        binding_identity_provider=_binding,
    )

    outcome = await dispatcher.dispatch(_receipt(), user_scope="u1", mode=Mode.EXECUTE)

    assert outcome.submitted is True
    assert submitted == ["workflow-1"]


@pytest.mark.asyncio
async def test_dispatch_zero_when_capability_provider_returns_none() -> None:
    submitted: list[str] = []

    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        submitted.append(workflow_id)

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _spec()

    async def _caps(user_scope: str, execution_id: str) -> None:
        return None

    dispatcher = ResearchDispatcher(
        gate=_Gate(),
        submit=_submit,
        plan_provider=_plan,
        capability_provider=_caps,
    )

    outcome = await dispatcher.dispatch(_receipt(), user_scope="u1", mode=Mode.EXECUTE)

    assert outcome.submitted is False
    assert outcome.reason == PLAN_ADMISSION_UNCONFIGURED
    assert submitted == []


@pytest.mark.asyncio
async def test_dispatch_zero_when_capability_provider_fails() -> None:
    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        raise AssertionError("must not submit")

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _spec()

    async def _caps(user_scope: str, execution_id: str) -> None:
        raise RuntimeError("ledger down")

    dispatcher = ResearchDispatcher(
        gate=_Gate(),
        submit=_submit,
        plan_provider=_plan,
        capability_provider=_caps,
    )

    outcome = await dispatcher.dispatch(_receipt(), user_scope="u1", mode=Mode.EXECUTE)

    assert outcome.submitted is False
    assert outcome.reason == f"{CAPABILITY_POLICY_UNAVAILABLE}:RuntimeError"


@pytest.mark.asyncio
async def test_dispatch_zero_on_invalid_plan_graph() -> None:
    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        raise AssertionError("must not submit")

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _spec(cyclic=True)

    async def _caps(user_scope: str, execution_id: str) -> dict[str, frozenset[str]]:
        return {"researcher": frozenset({"skill:skill-a@2"})}

    dispatcher = ResearchDispatcher(
        gate=_Gate(),
        submit=_submit,
        plan_provider=_plan,
        capability_provider=_caps,
        binding_identity_provider=_binding,
    )

    outcome = await dispatcher.dispatch(_receipt(), user_scope="u1", mode=Mode.EXECUTE)

    assert outcome.submitted is False
    assert outcome.reason is not None and outcome.reason.startswith("plan_invalid:")


# ---------------------------------------------------------------------------
# 受理入口：首问 / 开始已审计划
# ---------------------------------------------------------------------------


class _IntakeFacade:
    def __init__(self, receipt: SubmissionReceipt) -> None:
        self.sessions: list[Any] = []
        self.requests: list[Any] = []
        self.accepts: list[Any] = []
        self.consumed: list[tuple[str, str]] = []
        self.restored: list[tuple[str, str, tuple[str, ...]]] = []
        self.clarification_requests: list[Any] = []
        self.clarification_facts: tuple[Any, ...] = ()
        self.saved_clarification_id = "decision-clarify-1"
        self._receipt = receipt

    async def change_session(self, command: Any) -> Any:
        self.sessions.append(command)
        return cast(Any, object())

    async def accept(self, request: Any, *, command: Any) -> Any:
        self.accepts.append(request)
        return self._receipt

    async def submit_and_dispatch(self, request: Any, *, command: Any) -> Any:
        self.requests.append(request)
        return SimpleNamespace(receipt=self._receipt, dispatch=None)

    async def consume_clarification(self, *, user_scope: str, session_id: str) -> tuple[Any, ...]:
        self.consumed.append((user_scope, session_id))
        return self.clarification_facts

    async def restore_clarification_consumption(
        self, *, user_scope: str, session_id: str, decision_ids: tuple[str, ...]
    ) -> int:
        self.restored.append((user_scope, session_id, decision_ids))
        return len(decision_ids)

    async def save_clarification_decision(self, request: Any) -> Any:
        self.clarification_requests.append(request)
        return SimpleNamespace(decision_id=self.saved_clarification_id)


class _SwitchGate:
    def __init__(self, *, closed: bool) -> None:
        self._closed = closed

    def dispatch_closed(self) -> bool:
        return self._closed

    def close_dispatch(self) -> bool:
        return True


def _recording_dispatcher(*, closed: bool) -> tuple[ResearchDispatcher, list[str]]:
    submitted: list[str] = []

    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        submitted.append(workflow_id)

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _spec()

    async def _read(workflow_id: str) -> ResearchWorkflowResult:
        return ResearchWorkflowResult(workflow_id=workflow_id, state="SUCCESS", output={"summary": "ok"})

    dispatcher = ResearchDispatcher(
        gate=_SwitchGate(closed=closed),
        submit=_submit,
        plan_provider=_plan,
        read_result=_read,
        role_capabilities={"researcher": frozenset({"skill:skill-a@2"})},
        binding_identity_provider=_binding,
    )
    return dispatcher, submitted


def _intake_receipt() -> SubmissionReceipt:
    return _receipt()


@pytest.mark.asyncio
async def test_intake_first_question_creates_session_and_submits_frozen_manifest() -> None:
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    result = await intake.start_question("研究一下银行股", mode=Mode.DISCUSS)

    assert result.session_id == facade.requests[0].session_id
    assert len(facade.sessions) == 1
    assert facade.sessions[0].action is SessionAction.CREATE
    request = facade.requests[0]
    assert request.mode is Mode.DISCUSS
    assert request.purpose is Purpose.CONCEPT
    assert request.expected_revision == 0
    policy = derive_frozen_selection_policy(request.manifest_payload)
    assert policy is None  # 首问无手选集合 → 执行 fail-closed


@pytest.mark.asyncio
async def test_intake_start_question_threads_revision_declaration_into_request() -> None:
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    await intake.start_question(
        "修订后的问题",
        mode=Mode.DISCUSS,
        revise_source_message_id="m-2",
        revise_causal_cutoff_message_id="m-1",
    )

    request = facade.requests[0]
    assert request.revise_source_message_id == "m-2"
    assert request.revise_causal_cutoff_message_id == "m-1"


@pytest.mark.asyncio
async def test_intake_execute_question_threads_revision_declaration_into_accept() -> None:
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    await intake.start_question(
        "执行模式修订",
        mode=Mode.EXECUTE,
        revise_source_message_id="m-5",
        revise_causal_cutoff_message_id=None,
    )

    request = facade.accepts[0]
    assert request.revise_source_message_id == "m-5"
    assert request.revise_causal_cutoff_message_id is None


@pytest.mark.asyncio
async def test_intake_start_plan_preserves_frozen_input_and_adds_reference() -> None:
    source = _manifest(plan_ref=None)
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(source), user_scope="u1")

    await intake.start_plan(
        PlanReference(execution_id="execution-0", plan_revision=3), session_id="s1", expected_revision=4
    )

    request = facade.requests[0]
    assert request.mode is Mode.EXECUTE
    assert request.session_id == "s1"
    assert request.expected_revision == 4
    assert request.text == "研究问题"
    reference = decode_plan_reference(request.manifest_payload)
    assert reference == PlanReference(execution_id="execution-0", plan_revision=3)
    policy = derive_frozen_selection_policy(request.manifest_payload)
    assert policy is not None and policy.role_key == "researcher"


@pytest.mark.asyncio
async def test_intake_start_plan_refuses_without_source_manifest() -> None:
    intake = MainTurnIntake(_IntakeFacade(_intake_receipt()), manifest_reader=_ManifestDao(None), user_scope="u1")

    with pytest.raises(AgentServiceError) as excinfo:
        await intake.start_plan(
            PlanReference(execution_id="missing", plan_revision=1), session_id="s1", expected_revision=0
        )

    assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN


class _MainTurnRunnerStub:
    def __init__(self, plan_reference: PlanReference | None = None, *, clarification: Any = None) -> None:
        self.requests: list[MainTurnRequest] = []
        self._plan_reference = plan_reference
        self._clarification = clarification

    async def run(self, request: MainTurnRequest, *, turn: Any = None) -> Any:
        self.requests.append(request)
        return SimpleNamespace(
            outcome=SimpleNamespace(result=SimpleNamespace(body_text="主回合回答")),
            plan_reference=self._plan_reference,
            clarification=self._clarification,
        )


@pytest.mark.asyncio
async def test_intake_plan_runs_production_main_turn_and_returns_reference() -> None:
    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    result = await intake.start_question("研究银行股", mode=Mode.PLAN)

    request = runner.requests[0]
    assert request.mode is Mode.PLAN
    assert request.prompt_text == "研究银行股"
    assert request.identity.execution_id == "execution-1"
    assert request.identity.input_revision == 1
    assert len(request.identity.actual_input_digest) == 64
    assert result.main_turn is not None
    assert result.plan_reference == PlanReference(execution_id="execution-1", plan_revision=1)


@pytest.mark.asyncio
async def test_intake_main_turn_identity_uses_authoritative_input_revision_not_session_revision() -> None:
    """新受理的权威输入版恒为 1：会话草稿版（expected_revision）不得混入回合身份。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    await intake.start_question("研究银行股", mode=Mode.PLAN, session_id="s1", expected_revision=7)

    assert facade.requests[0].expected_revision == 7
    assert runner.requests[0].identity.input_revision == 1


@pytest.mark.asyncio
async def test_intake_main_turn_identity_consumes_authoritative_revised_input_revision() -> None:
    """同执行排队修订后的权威输入版（回执携带 N）必须原样进入回合身份。"""

    facade = _IntakeFacade(_receipt(input_revision=2))
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    await intake.start_question("修订后的问题", mode=Mode.PLAN, session_id="s1", expected_revision=1)

    assert runner.requests[0].identity.input_revision == 2


@pytest.mark.asyncio
async def test_intake_main_turn_identity_is_stable_across_replay() -> None:
    """重放同一受理事实得出同一输入版与轮身份（不随会话稿版漂移）。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    await intake.start_question("继续分析", mode=Mode.PLAN, session_id="s1", expected_revision=3)
    await intake.start_question("继续分析", mode=Mode.PLAN, session_id="s1", expected_revision=4)

    identities = [request.identity for request in runner.requests]
    assert [identity.input_revision for identity in identities] == [1, 1]
    assert identities[0].round_id == identities[1].round_id


@pytest.mark.asyncio
async def test_intake_discuss_runs_main_turn_and_returns_answer() -> None:
    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    result = await intake.start_question("解释一下量价背离", mode=Mode.DISCUSS)

    assert runner.requests[0].mode is Mode.DISCUSS
    assert result.main_turn is not None
    assert result.main_turn.outcome.result.body_text == "主回合回答"


@pytest.mark.asyncio
async def test_intake_discuss_clarification_is_persisted_and_reported() -> None:
    facade = _IntakeFacade(_intake_receipt())
    decision = TurnDecision(
        action=LoopAction.CLARIFY,
        text="关注行业还是个股？",
        structured_fields=json_doc(
            {"question": "关注行业还是个股？", "options": ["行业", "个股"], "impact": "决定分析口径"}
        ),
    )
    runner = _MainTurnRunnerStub(clarification=decision)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    result = await intake.start_question("怎么看", mode=Mode.DISCUSS)

    assert result.clarification_decision_id == "decision-clarify-1"
    request = facade.clarification_requests[0]
    assert request.question_text == "关注行业还是个股？"
    assert request.options == ("行业", "个股")
    assert request.impact == "决定分析口径"
    assert request.execution_id == result.receipt.execution_id
    expires_at = datetime.strptime(request.expires_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC)


@pytest.mark.asyncio
async def test_intake_injects_consumed_clarification_answers_into_main_turn() -> None:
    facade = _IntakeFacade(_intake_receipt())
    fact = ClarificationAnswerFact(
        decision_id="decision-prev",
        question_text="关注行业还是个股？",
        answer_text="行业景气",
        consumed_at="2026-01-01T00:00:00.000000Z",
    )
    facade.clarification_facts = (fact,)
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    result = await intake.start_question("继续分析", mode=Mode.DISCUSS)

    assert facade.consumed == [("u1", result.session_id)]
    assert runner.requests[0].clarification_answers == (fact,)
    assert result.clarification_decision_id is None


def _consumed_answer_fact() -> ClarificationAnswerFact:
    return ClarificationAnswerFact(
        decision_id="decision-prev",
        question_text="关注行业还是个股？",
        answer_text="行业景气",
        consumed_at="2026-01-01T00:00:00.000000Z",
    )


class _FailingRunner:
    def __init__(self, exception: BaseException) -> None:
        self._exception = exception

    async def run(self, request: MainTurnRequest, *, turn: Any = None) -> Any:
        raise self._exception


@pytest.mark.asyncio
async def test_intake_discuss_failure_restores_consumed_clarification_before_raising() -> None:
    facade = _IntakeFacade(_intake_receipt())
    fact = _consumed_answer_fact()
    facade.clarification_facts = (fact,)
    runner = _FailingRunner(RuntimeError("model unavailable"))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    with pytest.raises(RuntimeError) as excinfo:
        await intake.start_question("继续分析", mode=Mode.DISCUSS)

    assert str(excinfo.value) == "model unavailable"
    assert len(facade.restored) == 1
    user_scope, session_id, decision_ids = facade.restored[0]
    assert user_scope == "u1"
    assert session_id == facade.consumed[0][1]
    assert decision_ids == (fact.decision_id,)


@pytest.mark.asyncio
async def test_intake_discuss_cancellation_propagates_without_restore() -> None:
    facade = _IntakeFacade(_intake_receipt())
    facade.clarification_facts = (_consumed_answer_fact(),)
    runner = _FailingRunner(asyncio.CancelledError())
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
    )

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await intake.start_question("继续分析", mode=Mode.DISCUSS)

    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert facade.restored == []


@pytest.mark.asyncio
async def test_intake_execute_failure_restores_consumed_clarification() -> None:
    facade = _IntakeFacade(_intake_receipt())
    fact = _consumed_answer_fact()
    facade.clarification_facts = (fact,)
    runner = _FailingRunner(RuntimeError("planner unavailable"))
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    assert submitted == []
    assert result.dispatch is not None
    assert result.dispatch.reason_code == PLAN_UNAVAILABLE
    assert len(facade.restored) == 1
    assert facade.restored[0] == ("u1", result.session_id, (fact.decision_id,))


@pytest.mark.asyncio
async def test_intake_execute_clarification_zero_dispatch_without_plan_failure() -> None:
    facade = _IntakeFacade(_intake_receipt())
    decision = TurnDecision(
        action=LoopAction.CLARIFY,
        text="研究哪段时间范围？",
        structured_fields=json_doc({"question": "研究哪段时间范围？", "options": [], "impact": ""}),
    )
    runner = _MainTurnRunnerStub(clarification=decision)
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    assert submitted == []
    assert result.dispatch is None
    assert result.clarification_decision_id == "decision-clarify-1"
    assert result.main_turn is not None
    assert result.main_turn.clarification is decision


# ---------------------------------------------------------------------------
# 会话模型选择：首问写入会话键并进入执行链
# ---------------------------------------------------------------------------


class _SessionModelPort:
    """会话模型键的字典端口替身（记录读写以做精确断言）。"""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = dict(values or {})
        self.reads: list[str] = []
        self.writes: list[tuple[str, str]] = []

    async def read(self, key: str) -> str | None:
        self.reads.append(key)
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        self.writes.append((key, value))
        self.values[key] = value

    async def delete(self, key: str) -> bool:
        return self.values.pop(key, None) is not None


class _FailingSessionModelPort:
    """读取失败的端口替身（验证失败传播而非静默降级）。"""

    async def read(self, key: str) -> str | None:
        raise RuntimeError("session model store down")

    async def write(self, key: str, value: str) -> None:
        raise AssertionError("must not write")

    async def delete(self, key: str) -> bool:
        return False


def _session_models(port: Any) -> SessionModelStore:
    return SessionModelStore(read=port.read, write=port.write, delete=port.delete)


@pytest.mark.asyncio
async def test_intake_first_question_persists_explicit_model_selection() -> None:
    facade = _IntakeFacade(_intake_receipt())
    port = _SessionModelPort()
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        session_models=_session_models(port),
    )

    result = await intake.start_question("研究银行股", mode=Mode.DISCUSS, model_connection_id="conn-local")

    assert port.writes == [(f"agent.session.model.{result.session_id}", "conn-local")]
    assert runner.requests[0].preferred_connection_id == "conn-local"


@pytest.mark.asyncio
async def test_intake_without_selection_inherits_existing_session_model() -> None:
    facade = _IntakeFacade(_intake_receipt())
    port = _SessionModelPort({"agent.session.model.session-1": "conn-resumed"})
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        session_models=_session_models(port),
    )

    await intake.start_question("继续分析", mode=Mode.DISCUSS, session_id="session-1")

    assert port.writes == []
    assert port.reads == ["agent.session.model.session-1"]
    assert runner.requests[0].preferred_connection_id == "conn-resumed"


@pytest.mark.asyncio
async def test_intake_without_selection_and_without_session_value_keeps_inheritance() -> None:
    facade = _IntakeFacade(_intake_receipt())
    port = _SessionModelPort()
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        session_models=_session_models(port),
    )

    await intake.start_question("继续分析", mode=Mode.DISCUSS, session_id="session-1")

    assert port.writes == []
    assert runner.requests[0].preferred_connection_id is None


@pytest.mark.asyncio
async def test_intake_explicit_selection_without_store_fails_closed() -> None:
    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    with pytest.raises(AgentServiceError) as excinfo:
        await intake.start_question(
            "研究银行股", mode=Mode.DISCUSS, session_id="session-1", model_connection_id="conn-local"
        )

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
    assert excinfo.value.sanitized_message == "session model store not wired"
    assert facade.requests == []


@pytest.mark.asyncio
async def test_intake_explicit_selection_without_store_does_not_create_orphan_session() -> None:
    """新会话 + 模型存储未接线：必须在 CREATE 之前失败，不留下孤儿会话。"""

    facade = _IntakeFacade(_intake_receipt())
    intake = MainTurnIntake(facade, manifest_reader=_ManifestDao(None), user_scope="u1")

    with pytest.raises(AgentServiceError) as excinfo:
        await intake.start_question("研究银行股", mode=Mode.DISCUSS, session_id=None, model_connection_id="conn-x")

    assert excinfo.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
    assert excinfo.value.sanitized_message == "session model store not wired"
    assert facade.sessions == []
    assert facade.requests == []


@pytest.mark.asyncio
async def test_intake_session_model_read_failure_propagates() -> None:
    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub()
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        session_models=_session_models(_FailingSessionModelPort()),
    )

    with pytest.raises(RuntimeError) as excinfo:
        await intake.start_question("继续分析", mode=Mode.DISCUSS, session_id="session-1")

    assert str(excinfo.value) == "session model store down"
    assert facade.requests == []


@pytest.mark.asyncio
async def test_intake_execute_first_question_threads_model_selection_into_plan_turn() -> None:
    facade = _IntakeFacade(_intake_receipt())
    port = _SessionModelPort()
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        session_models=_session_models(port),
    )

    await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        session_id="session-1",
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
        model_connection_id="conn-local",
    )

    assert port.writes == [("agent.session.model.session-1", "conn-local")]
    assert runner.requests[0].preferred_connection_id == "conn-local"


def test_request_rejects_blank_preferred_connection_id() -> None:
    with pytest.raises(ValueError) as excinfo:
        MainTurnRequest(
            identity=_IDENTITY,
            mode=Mode.DISCUSS,
            prompt_text="问题",
            preferred_connection_id="   ",
        )

    assert str(excinfo.value) == "main turn preferred_connection_id must be non-empty text"


@pytest.mark.parametrize("invalid", [True, 123])
def test_request_rejects_non_text_preferred_connection_id(invalid: object) -> None:
    """布尔/数字不得冒充连接 id（精确 ValueError，不做隐式字符串化）。"""

    with pytest.raises(ValueError) as excinfo:
        MainTurnRequest(
            identity=_IDENTITY,
            mode=Mode.DISCUSS,
            prompt_text="问题",
            preferred_connection_id=cast(Any, invalid),
        )

    assert type(excinfo.value) is ValueError
    assert str(excinfo.value) == "main turn preferred_connection_id must be non-empty text"


@pytest.mark.asyncio
async def test_intake_execute_first_question_plans_then_dispatches_when_gate_open() -> None:
    """启门证据：门闩显式开启 + 假模型 + 假派发器 → 首问进入主回合并产生执行。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    # 先受理（不自动派发），再进入主回合产出计划，最后显式派发。
    assert [request.mode for request in facade.accepts] == [Mode.EXECUTE]
    assert facade.requests == []
    assert [request.mode for request in runner.requests] == [Mode.PLAN]
    assert runner.requests[0].prompt_text == "执行研究"
    assert submitted == ["workflow-1"]
    assert result.receipt.execution_id == "execution-1"
    assert result.dispatch is not None
    assert result.dispatch.submitted is True
    assert result.plan_reference == PlanReference(execution_id="execution-1", plan_revision=1)
    assert result.main_turn is None
    frozen = json.loads(facade.accepts[0].manifest_payload)
    assert frozen["plan_source"] == "auto"
    policy = derive_frozen_selection_policy(facade.accepts[0].manifest_payload)
    assert policy is not None
    assert policy.role_key == "researcher"


@pytest.mark.asyncio
async def test_intake_execute_first_question_default_closed_zero_dispatch() -> None:
    """默认关闭：计划可产出但零派发，且原因为结构化可解释状态（非自由文本）。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    dispatcher, submitted = _recording_dispatcher(closed=True)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    assert [request.mode for request in runner.requests] == [Mode.PLAN]
    assert submitted == []
    assert result.dispatch == DispatchOutcome(
        execution_id="execution-1",
        workflow_id="workflow-1",
        submitted=False,
        reason=DISPATCH_CLOSED,
        reason_code=DISPATCH_CLOSED,
    )


@pytest.mark.asyncio
async def test_intake_execute_without_main_turn_returns_plan_unavailable() -> None:
    facade = _IntakeFacade(_intake_receipt())
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        dispatcher=dispatcher,
    )

    result = await intake.start_question("执行研究", mode=Mode.EXECUTE)

    assert submitted == []
    assert result.dispatch is not None
    assert result.dispatch.submitted is False
    assert result.dispatch.reason_code == PLAN_UNAVAILABLE
    assert result.dispatch.reason_params == (("cause", "main_turn_not_wired"),)


@pytest.mark.asyncio
async def test_intake_execute_without_selection_returns_plan_unavailable() -> None:
    """无角色能力手选 → 无派发准入判据：零派发、不耗模型、原因可解释。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question("执行研究", mode=Mode.EXECUTE)

    assert runner.requests == []
    assert submitted == []
    assert result.dispatch is not None
    assert result.dispatch.reason_code == PLAN_UNAVAILABLE
    assert result.dispatch.reason_params == (("cause", "role_policy_missing"),)


class _AcceptedManifestDao:
    """读取最近一次受理的冻结 manifest（供派发侧能力来源复读同一份输入）。"""

    def __init__(self, facade: _IntakeFacade) -> None:
        self._facade = facade

    async def read_authoritative_manifest(self, *, user_scope: str, execution_id: str) -> str | None:
        return self._facade.accepts[0].manifest_payload if self._facade.accepts else None


@pytest.mark.asyncio
async def test_intake_execute_without_skills_dispatches_empty_capability_analysis() -> None:
    """零 Skill/MCP 的执行首问：已核准空能力放行，纯分析节点（capabilities=()）可派发。"""

    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=PlanReference(execution_id="execution-1", plan_revision=1))
    submitted: list[str] = []
    empty_spec = PlanSpecProposal(
        nodes=(
            PlanNodeSpec(node_id="n1", target="整理内部资料", role_key="researcher", depends_on=(), capabilities=()),
        )
    )

    async def _submit(workflow_id: str, payload: dict[str, Any]) -> None:
        submitted.append(workflow_id)

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return empty_spec

    async def _read(workflow_id: str) -> ResearchWorkflowResult:
        return ResearchWorkflowResult(workflow_id=workflow_id, state="SUCCESS", output={"summary": "ok"})

    dispatcher = ResearchDispatcher(
        gate=_SwitchGate(closed=False),
        submit=_submit,
        plan_provider=_plan,
        read_result=_read,
        capability_provider=build_role_capability_provider(cast(Any, _AcceptedManifestDao(facade))),
        binding_identity_provider=_binding,
    )
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "整理内部资料",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
    )

    frozen = json.loads(facade.accepts[0].manifest_payload)
    assert frozen["role_prompt_refs"] == [["researcher", "v1"]]
    assert frozen["skill_refs"] == []
    assert frozen["mcp_scope_digest"] is None
    assert [request.mode for request in runner.requests] == [Mode.PLAN]
    assert submitted == ["workflow-1"]
    assert result.dispatch is not None
    assert result.dispatch.submitted is True


@pytest.mark.asyncio
async def test_intake_execute_model_failure_returns_plan_unavailable() -> None:
    class _FailingRunner:
        async def run(self, request: MainTurnRequest, *, turn: Any = None) -> Any:
            raise AgentServiceError(AgentErrorCode.OPERATION_UNKNOWN, "model unavailable", "u1")

    facade = _IntakeFacade(_intake_receipt())
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, _FailingRunner()),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    assert submitted == []
    assert result.dispatch is not None
    assert result.dispatch.reason_code == PLAN_UNAVAILABLE
    assert result.dispatch.reason_params == (("cause", AgentErrorCode.OPERATION_UNKNOWN.value),)


@pytest.mark.asyncio
async def test_intake_execute_without_plan_reference_returns_plan_unavailable() -> None:
    facade = _IntakeFacade(_intake_receipt())
    runner = _MainTurnRunnerStub(plan_reference=None)
    dispatcher, submitted = _recording_dispatcher(closed=False)
    intake = MainTurnIntake(
        facade,
        manifest_reader=_ManifestDao(None),
        user_scope="u1",
        main_turn=cast(Any, runner),
        dispatcher=dispatcher,
    )

    result = await intake.start_question(
        "执行研究",
        mode=Mode.EXECUTE,
        role_prompt_refs=(("researcher", "v1"),),
        skill_refs=(("skill-a", "2"),),
    )

    assert submitted == []
    assert result.dispatch is not None
    assert result.dispatch.reason_code == PLAN_UNAVAILABLE
    assert result.dispatch.reason_params == (("cause", "plan_not_produced"),)
