"""叶运行链资料许可装配（I43）。

覆盖：

- 无许可决定 / 未接线 provider → binding 写 ``require_data_consent`` 且不写
  ``data_consent``（执行器零外发拒绝，不放行「无资料发送」）；
- 有决定 → binding 写 15 键 ``data_consent`` + operation 归属作用域
  （``operation_id`` 为叶首回合派生身份占位，真实身份由执行器逐调用覆盖）；
- 装配期 fail-closed：键缺失 / 多余键（含 ``attempt_id``）/ 脏摘要 / 作用域
  不一致一律 ValueError（零模型外发）；
- provider 默认实现从 DAO 权威事实组装 15 键上下文（无决定 → None）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_types import (
    ConnectionAuthorizationRef,
    DataConsentBinding,
    DataConsentState,
    PlanNodeSpec,
    data_consent_source_scope_digest,
    derive_round_id,
)
from core.model_management_types import ExecutionSnapshot
from services.agent_service.leaf_runtime import (
    AdmissionCountingExecutor,
    AdmissionCountingToolExecutor,
    DaoLeafTicketGate,
    LeafRuntimeWiring,
    build_actual_input_binder,
    default_leaf_runtime,
    install_leaf_runtime,
    leaf_call_identity,
    resolve_agent_model_binding,
    resolve_leaf_data_consent,
)
from services.agent_service.model import derive_model_call_id
from services.agent_service.workflows import (
    ActualInputBinderRegistry,
    LeafExecutionContext,
    LeafTicketGateRegistry,
    leaf_ticket_slot_id,
)
from services.ai_service.scenario_dispatch import ScenarioDispatch

pytestmark = pytest.mark.unit

_SNAPSHOT = ExecutionSnapshot(
    snapshot_id="snap-1",
    run_id=None,
    config_revision=1,
    routes=(),
    connections=(),
    business_contract_versions={},
    security_scope_version=1,
    created_at="2026-11-18T00:00:00.000000Z",
)


def _context(node_id: str = "n1") -> LeafExecutionContext:
    return LeafExecutionContext(
        execution_id="execution-1",
        round_id="round-1",
        plan_digest="b" * 64,
        node=PlanNodeSpec(node_id=node_id, target="梳理公司业务", role_key="analyst"),
    )


def _dispatch() -> ScenarioDispatch:
    return ScenarioDispatch(
        executor=object(),
        snapshot=_SNAPSHOT,
        route_revision=1,
        total_timeout_seconds=30.0,
        task_routes={},
    )


@pytest.fixture(autouse=True)
def _patch_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _resolve(_scenario_id: str) -> ScenarioDispatch | None:
        return _dispatch()

    monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)


def _binding(
    *,
    user_scope: str = "user-1",
    execution_id: str = "execution-1",
    round_id: str = "round-1",
    connection_id: str = "conn-local",
    revision: int = 1,
) -> DataConsentBinding:
    selections: tuple[Any, ...] = ()
    return DataConsentBinding(
        schema_version=1,
        consent_id="dec-1",
        consent_revision=revision,
        state=DataConsentState.APPROVED,
        approver_ref="user-1",
        user_scope=user_scope,
        execution_id=execution_id,
        round_id=round_id,
        valid_from="2026-11-18T00:00:00.000000Z",
        expires_at=None,
        requires_egress_grant=False,
        connection_authorization_ref=ConnectionAuthorizationRef(
            connection_id=connection_id,
            target_fingerprint="fp-1",
            purpose="agent_research",
            scope_version=1,
            verified_connection_grant_id=None,
        ),
        connection_revision=1,
        security_revision=0,
        credential_version=None,
        target_fingerprint="fp-1",
        purpose="agent_research",
        scope_version=1,
        source_selections=selections,
        source_scope_digest=data_consent_source_scope_digest(selections),
    )


def _consent_payload(binding: DataConsentBinding, *, owner_epoch: int = 3, run_epoch: int = 2) -> dict[str, Any]:
    return {
        "user_scope": binding.user_scope,
        "operation_id": "placeholder-op",
        "consent_id": binding.consent_id,
        "expected_consent_revision": binding.consent_revision,
        "connection_id": binding.connection_authorization_ref.connection_id,
        "connection_revision": binding.connection_revision,
        "security_revision": binding.security_revision,
        "credential_version": binding.credential_version,
        "purpose": binding.purpose,
        "target_fingerprint": binding.target_fingerprint,
        "scope_version": binding.scope_version,
        "source_selections": binding.source_selections,
        "source_scope_digest": binding.source_scope_digest,
        "owner_epoch": owner_epoch,
        "run_epoch": run_epoch,
    }


class _Facts:
    def __init__(
        self, user_scope: str = "user-1", owner_epoch: int = 3, run_epoch: int = 2, input_revision: int = 1
    ) -> None:
        self.user_scope = user_scope
        self.owner_epoch = owner_epoch
        self.run_epoch = run_epoch
        self.input_revision = input_revision
        self.session_id = "session-1"


class _Authority:
    def __init__(self, binding: DataConsentBinding, revision: int = 1) -> None:
        self.consent_id = binding.consent_id
        self.revision = revision
        self.binding = binding


class _FakeDao:
    def __init__(self, *, facts: _Facts | None = None, authority: Any | None = None) -> None:
        self.facts = facts or _Facts()
        self.authority = authority
        self.fact_calls = 0
        self.bind_requests: list[Any] = []
        self.claim_requests: list[Any] = []
        self.release_requests: list[Any] = []
        self.admissions: list[Any] = []
        self.bind_receipt: Any | None = None

    async def read_leaf_dispatch_facts(self, execution_id: str, round_id: str) -> _Facts:
        self.fact_calls += 1
        return self.facts

    async def read_authoritative_data_consent(self, *, execution_id: str, round_id: str, node_id: str) -> Any | None:
        return self.authority

    async def bind_actual_input(self, request: Any) -> Any:
        self.bind_requests.append(request)
        assert self.bind_receipt is not None
        return self.bind_receipt

    async def claim_resource(self, request: Any) -> Any:
        self.claim_requests.append(request)
        return SimpleNamespace(slot_id=request.slot_id, owner_epoch=request.owner_epoch)

    async def release_resource(self, request: Any) -> None:
        self.release_requests.append(request)

    async def admit_usage(self, request: Any) -> Any:
        self.admissions.append(request)
        return SimpleNamespace(execution_id=request.execution_id)


class TestResolveAgentModelBinding:
    async def test_no_provider_requires_consent_without_data_consent(self) -> None:
        resolved = await resolve_agent_model_binding(_context())
        assert resolved.binding["require_data_consent"] is True
        assert "data_consent" not in resolved.binding
        assert "data_operation_scope" not in resolved.binding

    async def test_provider_none_requires_consent(self) -> None:
        async def _none(_context: LeafExecutionContext) -> None:
            return None

        resolved = await resolve_agent_model_binding(_context(), data_consent_provider=_none)
        assert resolved.binding["require_data_consent"] is True
        assert "data_consent" not in resolved.binding

    async def test_provider_payload_freezes_consent_and_operation_scope(self) -> None:
        payload = _consent_payload(_binding())

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        context = _context()
        resolved = await resolve_agent_model_binding(context, data_consent_provider=_provide)
        assert "require_data_consent" not in resolved.binding
        assert resolved.binding["data_consent"] == payload
        assert resolved.binding["data_operation_scope"] == {
            "execution_id": "execution-1",
            "round_id": "round-1",
            "node_id": "n1",
        }

    async def test_missing_key_fails_closed(self) -> None:
        payload = _consent_payload(_binding())
        payload.pop("consent_id")

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(_context(), data_consent_provider=_provide)
        assert "consent_id" in str(excinfo.value)

    async def test_extra_attempt_id_fails_closed(self) -> None:
        payload = _consent_payload(_binding())
        payload["attempt_id"] = "att-1"

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(_context(), data_consent_provider=_provide)
        assert "attempt_id" in str(excinfo.value)

    async def test_dirty_digest_fails_closed(self) -> None:
        payload = _consent_payload(_binding())
        payload["source_scope_digest"] = "0" * 64

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(_context(), data_consent_provider=_provide)
        assert "source_scope_digest" in str(excinfo.value)

    def test_default_wiring_carries_provider(self) -> None:
        wiring = default_leaf_runtime()
        assert isinstance(wiring, LeafRuntimeWiring)
        assert wiring.data_consent_provider is resolve_leaf_data_consent


class TestResolveLeafDataConsent:
    async def test_no_authority_returns_none(self) -> None:
        dao = _FakeDao(authority=None)
        assert await resolve_leaf_data_consent(_context(), dao=dao) is None

    async def test_assembles_fifteen_key_context_from_authority(self) -> None:
        binding = _binding(revision=2)
        dao = _FakeDao(authority=_Authority(binding, revision=2))
        context = _context()
        payload = await resolve_leaf_data_consent(context, dao=dao)
        assert payload is not None
        expected_operation_id = derive_model_call_id(leaf_call_identity(context), 0)
        assert payload["operation_id"] == expected_operation_id
        assert payload["consent_id"] == "dec-1"
        assert payload["expected_consent_revision"] == 2
        assert payload["owner_epoch"] == 3
        assert payload["run_epoch"] == 2
        assert payload["user_scope"] == "user-1"
        assert "attempt_id" not in payload

    async def test_scope_mismatch_fails_closed(self) -> None:
        dao = _FakeDao(authority=_Authority(_binding(execution_id="other-exec")))
        with pytest.raises(ValueError) as excinfo:
            await resolve_leaf_data_consent(_context(), dao=dao)
        assert "scope does not match" in str(excinfo.value)


class _CountingExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_chat_turn(
        self, request: Any, snapshot: Any, *, binding: dict[str, Any], on_event: Any = None
    ) -> str:
        self.calls += 1
        return "ok"


class _CountingToolRuntime:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, *, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> str:
        self.calls.append(tool_name)
        return "tool-ok"


class TestUsageAdmission:
    async def test_model_turn_admitted_before_execution(self) -> None:
        dao = _FakeDao()
        executor = _CountingExecutor()
        counting = AdmissionCountingExecutor(executor, dao=dao, execution_id="execution-1", round_id="round-1")

        assert await counting.execute_chat_turn({}, _SNAPSHOT, binding={}) == "ok"
        assert executor.calls == 1
        assert len(dao.admissions) == 1
        assert dao.admissions[0].model_attempts == 1
        assert dao.admissions[0].user_scope == "user-1"

    async def test_tool_call_admitted_before_execution(self) -> None:
        dao = _FakeDao()
        runtime = _CountingToolRuntime()
        counting = AdmissionCountingToolExecutor(runtime, dao=dao, user_scope="user-1", execution_id="execution-1")

        assert await counting.execute(tool_name="search", arguments={}, tool_call_id="call-1") == "tool-ok"
        assert runtime.calls == ["search"]
        assert dao.admissions[0].tool_calls == 1


class TestActualInputBinder:
    async def test_binds_round_and_freezes_leaf_identities(self) -> None:
        dao = _FakeDao()
        round_id = derive_round_id(user_scope="user-1", execution_id="execution-1", workflow_id="wf-1")
        dao.bind_receipt = SimpleNamespace(round_id=round_id, input_revision=1)
        binder = build_actual_input_binder(dao)
        request = {
            "user_scope": "user-1",
            "session_id": "session-1",
            "execution_id": "execution-1",
            "round_id": round_id,
            "plan_digest": "b" * 64,
            "workflow_id": "wf-1",
            "workflow_code_version": "1.0.0",
            "run_epoch": 1,
            "expected_input_revision": 1,
            "frozen_manifest": "{}",
            "actual_input": "{}",
            "input_digests": {"n1": "1" * 64},
        }

        wire = await binder(request)

        assert wire["input_revision"] == 1
        assert wire["owner_epoch"] == 3
        assert wire["leaves"][0]["node_id"] == "n1"
        assert dao.bind_requests[0].node_id == "n1"

    async def test_rejects_round_identity_mismatch(self) -> None:
        dao = _FakeDao()
        binder = build_actual_input_binder(dao)
        request = {
            "user_scope": "user-1",
            "session_id": "session-1",
            "execution_id": "execution-1",
            "round_id": "round-other",
            "plan_digest": "b" * 64,
            "workflow_id": "wf-1",
            "workflow_code_version": "1.0.0",
            "run_epoch": 1,
            "expected_input_revision": 1,
            "frozen_manifest": "{}",
            "actual_input": "{}",
            "input_digests": {"n1": "1" * 64},
        }
        with pytest.raises(ValueError) as excinfo:
            await binder(request)
        assert "round identity" in str(excinfo.value)
        assert dao.bind_requests == []


class TestLeafTicketGate:
    async def test_claim_and_release_use_dao_resources(self) -> None:
        dao = _FakeDao()
        gate = DaoLeafTicketGate(dao)

        lease = await gate.claim(execution_id="execution-1", round_id="round-1", node_id="n1")
        assert lease is not None
        assert lease.slot_id == leaf_ticket_slot_id("execution-1", "round-1", "n1")
        assert lease.user_scope == "user-1"
        assert dao.claim_requests[0].slot_kind.value == "leaf"

        await gate.release(lease, exit_verified=False)
        assert dao.release_requests[0].exit_verified is False
        assert dao.release_requests[0].slot_id == lease.slot_id


class TestInstallLeafRuntime:
    def test_install_registers_binder_and_ticket_gate(self) -> None:
        dao = _FakeDao()
        ActualInputBinderRegistry._reset_singleton()
        LeafTicketGateRegistry._reset_singleton()
        wiring = default_leaf_runtime(dao)
        assert callable(wiring.actual_input_binder)
        assert isinstance(wiring.leaf_ticket_gate, DaoLeafTicketGate)
        try:
            install_leaf_runtime(wiring)
            assert ActualInputBinderRegistry.require() is wiring.actual_input_binder
            assert LeafTicketGateRegistry.require() is wiring.leaf_ticket_gate
        finally:
            ActualInputBinderRegistry._reset_singleton()
            LeafTicketGateRegistry._reset_singleton()
