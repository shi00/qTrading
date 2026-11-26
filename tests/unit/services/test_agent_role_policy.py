"""角色能力上限策略与委派校验端口：缺失即空集、越界派发原样拒绝。"""

from __future__ import annotations

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPServiceIdentity,
    Mode,
    ResearchResult,
    ResearchResultDraft,
    RoleMCPAssignment,
    RoleMCPServiceBinding,
    RoleSkillAssignment,
    SkillVersionRef,
    decode_role_definition,
)
from services.agent_service.loop import (
    DelegationRequest,
    DelegationResult,
    DelegationScope,
    ExecutionLoop,
    LoopAction,
    LoopIdentity,
    ResearchResultProducer,
    TurnContext,
    TurnDecision,
)
from services.agent_service.roles import RolePolicy, derive_role_policy

pytestmark = pytest.mark.unit

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


def _wire(**overrides: object) -> dict[str, object]:
    wire: dict[str, object] = {
        "schema_version": 1,
        "role_id": "risk_reviewer",
        "display_name": "风险复核",
        "base": "sub",
        "status": "active",
        "role_version": "r1",
        "prompt_body": "只核对风险。",
        "model_connection_id": None,
        "capability_mode": "explicit",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "updated_by": "tester",
    }
    wire.update(overrides)
    return wire


class _Store:
    def __init__(self) -> None:
        self.drafts: list[ResearchResultDraft] = []

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult:
        self.drafts.append(draft)
        return ResearchResult.from_draft(draft, artifact_id="artifact-1", artifact_version=1)


class _ScriptedTurn:
    def __init__(self, decisions: list[TurnDecision]) -> None:
        self._decisions = decisions

    async def __call__(self, context: TurnContext) -> TurnDecision:
        return self._decisions.pop(0)


class _RecordingDelegate:
    def __init__(self) -> None:
        self.calls: list[tuple[DelegationRequest, DelegationScope]] = []

    async def __call__(self, request: DelegationRequest, scope: DelegationScope) -> DelegationResult:
        self.calls.append((request, scope))
        return DelegationResult(node_id=request.node_id, state="complete", summary="subagent-ok")


class _RecordingGuard:
    def __init__(self, *, error: AgentServiceError | None = None) -> None:
        self.requests: list[DelegationRequest] = []
        self._error = error

    def __call__(self, request: DelegationRequest) -> None:
        self.requests.append(request)
        if self._error is not None:
            raise self._error


def _loop(
    *,
    turn: _ScriptedTurn,
    store: _Store,
    delegate: _RecordingDelegate,
    delegation_guard: _RecordingGuard | None = None,
) -> ExecutionLoop:
    return ExecutionLoop(
        identity=_IDENTITY,
        mode=Mode.EXECUTE,
        turn=turn,
        producer=ResearchResultProducer(store),
        delegate=delegate,
        delegation_guard=delegation_guard,
    )


def _delegate_decision() -> TurnDecision:
    return TurnDecision(
        action=LoopAction.DELEGATE,
        delegation=DelegationRequest(node_id="n1", target="梳理财务", role_key="risk_reviewer"),
    )


def test_derive_role_policy_uses_skill_refs_and_mcp_digest() -> None:
    definition = decode_role_definition(_wire())
    skills = RoleSkillAssignment(
        role_id="risk_reviewer",
        role_version="r1",
        enabled=(SkillVersionRef(skill_id="checklist", version=3),),
        defaults=(),
    )
    mcp_identity = MCPServiceIdentity(service_id="market", config_version=2)
    mcp = RoleMCPAssignment(
        role_id="risk_reviewer",
        role_version="r1",
        available=(RoleMCPServiceBinding(identity=mcp_identity),),
    )

    policy = derive_role_policy(definition, skills=skills, mcp=mcp)

    assert policy == RolePolicy(
        role_id="risk_reviewer",
        role_version="r1",
        allowed_skills=frozenset({"skill:checklist@3"}),
        allowed_mcp=frozenset({f"mcp_scope:{mcp_identity.stable_key()}"}),
        model_connection_id=None,
    )


def test_derive_role_policy_without_assignments_is_empty() -> None:
    policy = derive_role_policy(decode_role_definition(_wire()), skills=None, mcp=None)

    assert policy.allowed_skills == frozenset()
    assert policy.allowed_mcp == frozenset()
    assert policy.model_connection_id is None


def test_derive_role_policy_preserves_model_connection_id() -> None:
    definition = decode_role_definition(_wire(model_connection_id="conn-reasoning"))

    policy = derive_role_policy(definition, skills=None, mcp=None)

    assert policy.model_connection_id == "conn-reasoning"


@pytest.mark.asyncio
async def test_loop_rejects_delegation_outside_role_capabilities() -> None:
    request = DelegationRequest(node_id="n1", target="梳理财务", role_key="risk_reviewer")
    guard = _RecordingGuard(
        error=AgentServiceError(AgentErrorCode.POLICY_DENIED, "capability_exceeds_role", request.node_id)
    )
    delegate = _RecordingDelegate()
    store = _Store()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.DELEGATE, delegation=request)])

    with pytest.raises(AgentServiceError) as excinfo:
        await _loop(turn=turn, store=store, delegate=delegate, delegation_guard=guard).run()

    error = excinfo.value
    assert error.code is AgentErrorCode.POLICY_DENIED
    assert error.sanitized_message == "capability_exceeds_role"
    assert error.identity == "n1"
    assert guard.requests == [request]
    assert delegate.calls == []
    assert store.drafts == []


@pytest.mark.asyncio
async def test_loop_calls_guard_once_before_dispatch() -> None:
    request = DelegationRequest(node_id="n1", target="梳理财务", role_key="risk_reviewer")
    guard = _RecordingGuard()
    delegate = _RecordingDelegate()
    store = _Store()
    turn = _ScriptedTurn(
        [
            TurnDecision(action=LoopAction.DELEGATE, delegation=request),
            TurnDecision(action=LoopAction.ANSWER, text="done"),
        ]
    )

    outcome = await _loop(turn=turn, store=store, delegate=delegate, delegation_guard=guard).run()

    assert guard.requests == [request]
    assert delegate.calls == [(request, DelegationScope(depth=1, allow_delegation=False))]
    assert outcome.delegations == 1
    assert outcome.tool_calls == 0


@pytest.mark.asyncio
async def test_loop_without_guard_keeps_dispatch_behaviour() -> None:
    request = DelegationRequest(node_id="n1", target="梳理财务", role_key="risk_reviewer")
    delegate = _RecordingDelegate()
    store = _Store()
    turn = _ScriptedTurn(
        [
            TurnDecision(action=LoopAction.DELEGATE, delegation=request),
            TurnDecision(action=LoopAction.ANSWER, text="done"),
        ]
    )

    outcome = await _loop(turn=turn, store=store, delegate=delegate).run()

    assert delegate.calls == [(request, DelegationScope(depth=1, allow_delegation=False))]
    assert outcome.delegations == 1


@pytest.mark.asyncio
async def test_loop_does_not_call_guard_without_delegation() -> None:
    guard = _RecordingGuard()
    delegate = _RecordingDelegate()
    store = _Store()
    turn = _ScriptedTurn([TurnDecision(action=LoopAction.ANSWER, text="done")])

    outcome = await _loop(turn=turn, store=store, delegate=delegate, delegation_guard=guard).run()

    assert guard.requests == []
    assert delegate.calls == []
    assert outcome.delegations == 0
