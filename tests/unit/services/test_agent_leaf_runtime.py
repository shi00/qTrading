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

import asyncio
import dataclasses
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    ConnectionAuthorizationRef,
    DataConsentBinding,
    DataConsentState,
    Mode,
    PlanNodeSpec,
    Purpose,
    data_consent_source_scope_digest,
    derive_round_id,
)
from core.model_management_types import ExecutionSnapshot
from services.agent_service.comparison import (
    ComparisonLaneSelection,
    ComparisonSharedInput,
    freeze_comparison_group,
    require_lane_parameters,
)
from services.agent_service.leaf_runtime import (
    AdmissionCountingExecutor,
    AdmissionCountingToolExecutor,
    DaoLeafTicketGate,
    FrozenLaneBinding,
    LeafDeadlineExhaustedError,
    LeafDeadlineUnavailableError,
    LeafRuntimeWiring,
    build_actual_input_binder,
    default_leaf_runtime,
    install_leaf_runtime,
    leaf_call_identity,
    require_leaf_turn_timeout,
    resolve_agent_model_binding,
    resolve_comparison_lane_freeze,
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
    async def test_binding_carries_frozen_absolute_deadline(self) -> None:
        """T-03：绑定携带派发冻结路由总时限换算的绝对期限（不臆造默认值）。"""

        before = asyncio.get_running_loop().time()
        resolved = await resolve_agent_model_binding(_context())

        assert resolved.deadline >= before + 30.0
        assert resolved.deadline <= asyncio.get_running_loop().time() + 30.0
        assert require_leaf_turn_timeout(resolved) > 0

    async def test_binding_profile_declares_explicit_window_and_no_vision(self) -> None:
        """§3.1-3：按冻结连接证据显式注入 Profile，窗口未知显式 None（R21）。"""

        resolved = await resolve_agent_model_binding(_context())

        profile = cast("Mapping[str, Any]", resolved.profile)
        assert profile["context_window"] is None  # 无路由/连接证据即未知，不猜默认窗口
        assert profile["supports_image_input"] is False  # 视觉能力未证实不放行
        assert profile["supported_image_media_types"] == ()
        assert profile["supports_inline_system_prompts"] is False

    async def test_missing_route_timeout_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _resolve(_scenario_id: str) -> ScenarioDispatch:
            return dataclasses.replace(_dispatch(), total_timeout_seconds=0.0)

        monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)
        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(_context())
        assert "total_timeout_seconds" in str(excinfo.value)

    async def test_turn_timeout_rejects_unverifiable_deadline(self) -> None:
        resolved = await resolve_agent_model_binding(_context())
        with pytest.raises(LeafDeadlineUnavailableError) as zero_exc:
            require_leaf_turn_timeout(dataclasses.replace(resolved, deadline=0.0))
        assert "verified absolute deadline" in str(zero_exc.value)
        with pytest.raises(LeafDeadlineUnavailableError) as nan_exc:
            require_leaf_turn_timeout(dataclasses.replace(resolved, deadline=float("nan")))
        assert "verified absolute deadline" in str(nan_exc.value)

    async def test_turn_timeout_rejects_exhausted_deadline(self) -> None:
        resolved = await resolve_agent_model_binding(_context())
        with pytest.raises(LeafDeadlineExhaustedError) as excinfo:
            require_leaf_turn_timeout(dataclasses.replace(resolved, deadline=asyncio.get_running_loop().time() - 1.0))
        assert "exhausted" in str(excinfo.value)

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


class _ComparisonDao(_FakeDao):
    """比较路冻结读取面：执行摘要 + 组 manifest（缺省如实返回）。"""

    def __init__(
        self,
        *,
        summary: Any,
        manifest: str | None,
        facts: _Facts | None = None,
        authority: Any | None = None,
    ) -> None:
        super().__init__(facts=facts, authority=authority)
        self.summary = summary
        self.manifest = manifest
        self.manifest_reads = 0

    async def get_execution_snapshot(self, execution_id: str, *, user_scope: str, limit: int = 50) -> Any:
        return SimpleNamespace(execution=self.summary)

    async def read_comparison_manifest(self, *, user_scope: str, group_ref: str) -> str | None:
        self.manifest_reads += 1
        return self.manifest


def _lane_group(
    revision: int = 1,
    *,
    confirm_lane: str = "conn-local",
) -> Any:
    return freeze_comparison_group(
        user_scope="user-1",
        session_id="session-1",
        source_module="ui",
        group_command_id="cmp-1",
        shared=ComparisonSharedInput(text="同一任务", mode=Mode.DISCUSS, purpose=Purpose.CONCEPT),
        lanes=(
            ComparisonLaneSelection(
                connection_id=confirm_lane,
                connection_revision=revision,
                target_fingerprint="fp-1",
                connection_purpose="agent_research",
                scope_version=1,
                display_name="本地连接",
            ),
            ComparisonLaneSelection(
                connection_id="conn-b",
                connection_revision=1,
                target_fingerprint="fp-b",
                connection_purpose="agent_research",
                scope_version=1,
                display_name="备用连接",
            ),
        ),
    )


def _route_snapshot(*candidates: dict[str, Any]) -> ExecutionSnapshot:
    from services.ai_service.scenario_dispatch import build_route_snapshot

    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=1,
        routes=(
            build_route_snapshot(
                {
                    "route_revision": 1,
                    "transient_retries": 0,
                    "fallback_enabled": False,
                    "total_timeout_seconds": 30.0,
                },
                scenario_id="agent_research",
                candidates=candidates,
            ),
        ),
        connections=(),
        business_contract_versions={},
        security_scope_version=1,
        created_at="2026-11-18T00:00:00.000000Z",
    )


def _candidate(connection_id: str, *, fingerprint: str, index: int) -> dict[str, Any]:
    return {
        "candidate_index": index,
        "task_id": "agent_research",
        "connection_id": connection_id,
        "budget_limit_cny": None,
        "budget_policy_snapshot": None,
        "target_fingerprint": fingerprint,
        "purpose": "agent_research",
    }


def _install_dispatch(monkeypatch: pytest.MonkeyPatch, snapshot: ExecutionSnapshot) -> None:
    async def _resolve(_scenario_id: str) -> ScenarioDispatch | None:
        return ScenarioDispatch(
            executor=object(),
            snapshot=snapshot,
            route_revision=1,
            total_timeout_seconds=30.0,
            task_routes={},
        )

    monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)


def _lane_provider(group: Any):
    parameters = require_lane_parameters(group, group.lanes[0].lane_ref)

    async def _provide(_context: LeafExecutionContext) -> FrozenLaneBinding:
        return FrozenLaneBinding(group_ref=group.group_ref, lane_ref=parameters.lane_ref, parameters=parameters)

    return _provide


class TestComparisonLaneBinding:
    @pytest.mark.asyncio
    async def test_lane_pins_frozen_connection_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(
                _candidate("conn-other", fingerprint="fp-other", index=0),
                _candidate("conn-local", fingerprint="fp-1", index=1),
            ),
        )
        payload = _consent_payload(_binding(connection_id="conn-local"))

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        resolved = await resolve_agent_model_binding(
            _context(),
            data_consent_provider=_provide,
            lane_freeze_provider=_lane_provider(group),
        )

        (route,) = resolved.snapshot.routes
        (candidate,) = route["candidates"]
        assert candidate["connection_id"] == "conn-local"
        assert candidate["candidate_index"] == 0

    @pytest.mark.asyncio
    async def test_missing_frozen_connection_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(_candidate("conn-other", fingerprint="fp-other", index=0)),
        )
        payload = _consent_payload(_binding(connection_id="conn-local"))

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(RuntimeError) as excinfo:
            await resolve_agent_model_binding(
                _context(),
                data_consent_provider=_provide,
                lane_freeze_provider=_lane_provider(group),
            )
        assert "frozen connection is unavailable" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_consent_connection_mismatch_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(_candidate("conn-local", fingerprint="fp-1", index=0)),
        )
        payload = _consent_payload(_binding(connection_id="conn-other"))

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(
                _context(),
                data_consent_provider=_provide,
                lane_freeze_provider=_lane_provider(group),
            )
        assert "does not match frozen lane" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_changed_connection_target_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(_candidate("conn-local", fingerprint="fp-changed", index=0)),
        )
        payload = _consent_payload(_binding(connection_id="conn-local"))

        async def _provide(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        with pytest.raises(RuntimeError) as excinfo:
            await resolve_agent_model_binding(
                _context(),
                data_consent_provider=_provide,
                lane_freeze_provider=_lane_provider(group),
            )
        assert "target changed since acceptance" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_lane_without_data_consent_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(_candidate("conn-local", fingerprint="fp-1", index=0)),
        )

        with pytest.raises(RuntimeError) as excinfo:
            await resolve_agent_model_binding(
                _context(),
                lane_freeze_provider=_lane_provider(group),
            )
        assert "requires a frozen data consent" in str(excinfo.value)


class TestPreferredConnectionSelection:
    """普通叶执行使用受理时冻结的会话/角色模型选择；比较路不被其覆盖。"""

    @pytest.mark.asyncio
    async def test_provider_threads_preferred_connection_to_route(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str | None] = []

        async def _resolve(_scenario_id: str, *, preferred_connection_id: str | None = None) -> ScenarioDispatch:
            captured.append(preferred_connection_id)
            return _dispatch()

        monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)

        async def _provide(_context: LeafExecutionContext) -> str:
            return "conn-session"

        await resolve_agent_model_binding(_context(), preferred_connection_provider=_provide)

        assert captured == ["conn-session"]

    @pytest.mark.asyncio
    async def test_explicit_connection_wins_over_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str | None] = []

        async def _resolve(_scenario_id: str, *, preferred_connection_id: str | None = None) -> ScenarioDispatch:
            captured.append(preferred_connection_id)
            return _dispatch()

        monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)
        provider_calls: list[int] = []

        async def _provide(_context: LeafExecutionContext) -> str:
            provider_calls.append(1)
            return "conn-session"

        await resolve_agent_model_binding(
            _context(), preferred_connection_id="conn-explicit", preferred_connection_provider=_provide
        )

        assert captured == ["conn-explicit"]
        assert provider_calls == []

    @pytest.mark.asyncio
    async def test_lane_freeze_ignores_session_preferred_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        group = _lane_group()
        _install_dispatch(
            monkeypatch,
            _route_snapshot(_candidate("conn-local", fingerprint="fp-1", index=0)),
        )
        payload = _consent_payload(_binding(connection_id="conn-local"))

        async def _consent(_context: LeafExecutionContext) -> dict[str, Any]:
            return payload

        provider_calls: list[int] = []

        async def _provide(_context: LeafExecutionContext) -> str:
            provider_calls.append(1)
            return "conn-session"

        resolved = await resolve_agent_model_binding(
            _context(),
            data_consent_provider=_consent,
            lane_freeze_provider=_lane_provider(group),
            preferred_connection_provider=_provide,
        )

        (route,) = resolved.snapshot.routes
        (candidate,) = route["candidates"]
        assert candidate["connection_id"] == "conn-local"
        assert provider_calls == []

    @pytest.mark.asyncio
    async def test_blank_provider_selection_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _provide(_context: LeafExecutionContext) -> str:
            return "   "

        with pytest.raises(ValueError) as excinfo:
            await resolve_agent_model_binding(_context(), preferred_connection_provider=_provide)
        assert "preferred model connection" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_default_wiring_forwards_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[str | None] = []

        async def _resolve(_scenario_id: str, *, preferred_connection_id: str | None = None) -> ScenarioDispatch:
            captured.append(preferred_connection_id)
            return _dispatch()

        monkeypatch.setattr("services.ai_service.scenario_dispatch.resolve_executor", _resolve)
        dao = _ComparisonDao(
            summary=SimpleNamespace(comparison_group_ref=None, comparison_lane_ref=None), manifest=None
        )

        async def _provide(_context: LeafExecutionContext) -> str:
            return "conn-session"

        wiring = default_leaf_runtime(dao, preferred_connection_provider=_provide)
        await wiring.model_binding(_context())

        assert captured == ["conn-session"]


class TestResolveComparisonLaneFreeze:
    @pytest.mark.asyncio
    async def test_non_comparison_execution_returns_none(self) -> None:
        dao = _ComparisonDao(
            summary=SimpleNamespace(comparison_group_ref=None, comparison_lane_ref=None),
            manifest=None,
        )
        assert await resolve_comparison_lane_freeze(_context(), dao=dao) is None
        assert dao.manifest_reads == 0

    @pytest.mark.asyncio
    async def test_lane_freeze_uses_persisted_group_manifest(self) -> None:
        group = _lane_group()
        lane = group.lanes[0]
        dao = _ComparisonDao(
            summary=SimpleNamespace(comparison_group_ref=group.group_ref, comparison_lane_ref=lane.lane_ref),
            manifest=group.manifest_payload,
        )

        frozen = await resolve_comparison_lane_freeze(_context(), dao=dao)

        assert frozen is not None
        assert frozen.group_ref == group.group_ref
        assert frozen.parameters.connection_id == "conn-local"
        assert frozen.parameters.connection_revision == 1

    @pytest.mark.asyncio
    async def test_missing_group_manifest_fails_closed(self) -> None:
        group = _lane_group()
        lane = group.lanes[0]
        dao = _ComparisonDao(
            summary=SimpleNamespace(comparison_group_ref=group.group_ref, comparison_lane_ref=lane.lane_ref),
            manifest=None,
        )
        with pytest.raises(ValueError) as excinfo:
            await resolve_comparison_lane_freeze(_context(), dao=dao)
        assert "manifest unavailable" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_group_without_lane_ref_fails_closed(self) -> None:
        dao = _ComparisonDao(
            summary=SimpleNamespace(comparison_group_ref="comparison_group_x", comparison_lane_ref=None),
            manifest="{}",
        )
        with pytest.raises(ValueError) as excinfo:
            await resolve_comparison_lane_freeze(_context(), dao=dao)
        assert "missing its lane ref" in str(excinfo.value)


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
