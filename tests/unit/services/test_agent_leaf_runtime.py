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

from typing import Any

import pytest

from core.agent_types import (
    ConnectionAuthorizationRef,
    DataConsentBinding,
    DataConsentState,
    PlanNodeSpec,
    data_consent_source_scope_digest,
)
from core.model_management_types import ExecutionSnapshot
from services.agent_service.leaf_runtime import (
    LeafRuntimeWiring,
    default_leaf_runtime,
    leaf_call_identity,
    resolve_agent_model_binding,
    resolve_leaf_data_consent,
)
from services.agent_service.model import derive_model_call_id
from services.agent_service.workflows import LeafExecutionContext
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
    def __init__(self, user_scope: str = "user-1", owner_epoch: int = 3, run_epoch: int = 2) -> None:
        self.user_scope = user_scope
        self.owner_epoch = owner_epoch
        self.run_epoch = run_epoch


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

    async def read_leaf_dispatch_facts(self, execution_id: str, round_id: str) -> _Facts:
        self.fact_calls += 1
        return self.facts

    async def read_authoritative_data_consent(self, *, execution_id: str, round_id: str, node_id: str) -> Any | None:
        return self.authority


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
