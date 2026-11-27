"""工具 runtime 装配与执行端口（DD §10.3 / §SQ-03）。

- 冻结目录 ∩ 角色/请求/许可 → 只装交集；非法 schema 不装、不降低校验；
- 无新鲜决定/许可 → ``denied`` 且零派发；
- 结果三态落库；结果保存失败 → ``tool_result_not_saved``；
- 预算超限按 unknown 落库后继续传播。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPServiceIdentity,
    OperationGrantScope,
    ToolGrantFact,
    ToolGrantState,
)
from services.agent_service.mcp import MCPToolDescriptor
from services.agent_service.mcp_budget import MCPBudgetExceeded
from services.agent_service.mcp_tools import (
    ToolBoundaryAttestation,
    ToolCallOutcome,
    ToolCallRequest,
    ToolCallState,
    audit_scope_digest,
    canonical_arguments_digest,
)
from services.agent_service.tool_runtime import (
    ToolApprovalCoordinator,
    ToolRunScope,
    ToolRuntime,
    build_tool_runtime,
)

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-1", config_version=2)
_SCOPE = ToolRunScope(
    user_scope="user-1",
    session_id="session-1",
    execution_id="execution-1",
    round_id="round-1",
    node_id="n1",
)


def _schema(description: str = "search tool") -> str:
    return json.dumps(
        {
            "type": "object",
            "description": description,
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }
    )


class _Manager:
    def __init__(self, catalog: tuple[MCPToolDescriptor, ...], scope: tuple[str, ...] = ("search",)) -> None:
        self.catalog = catalog
        self.scope = scope

    def frozen_catalog(self, identity: MCPServiceIdentity) -> tuple[MCPToolDescriptor, ...]:
        return self.catalog

    def connection_generation(self, identity: MCPServiceIdentity) -> int:
        return 3

    def audit_tool_scope(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return self.scope


class _Dao:
    def __init__(self, *, decision_error: Exception | None = None, result_error: Exception | None = None) -> None:
        self.decision_error = decision_error
        self.result_error = result_error
        self.decisions: list[Any] = []
        self.results: list[Any] = []

    async def save_tool_decision(self, request: Any) -> ToolGrantFact:
        self.decisions.append(request)
        if self.decision_error is not None:
            raise self.decision_error
        return ToolGrantFact(
            decision_id=request.decision_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state=ToolGrantState.GRANTED,
        )

    async def save_tool_result(self, request: Any) -> None:
        self.results.append(request)
        if self.result_error is not None:
            raise self.result_error

    async def read_operation_grant_scope(self, *, operation_id: str) -> Any:
        return None

    async def claim_tool_dispatch(self, request: Any) -> ToolGrantFact:
        raise AssertionError("claim must go through dispatcher")


class _Dispatcher:
    def __init__(self, outcome: ToolCallOutcome | Exception) -> None:
        self.outcome = outcome
        self.requests: list[ToolCallRequest] = []

    async def call(self, request: ToolCallRequest) -> ToolCallOutcome:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class _RacingDispatcher(_Dispatcher):
    """调用门拒绝前模拟并发撤销：验证按持久事实回判而非沿用 source_disabled。"""

    def __init__(self, outcome: Exception, on_call: Any) -> None:
        super().__init__(outcome)
        self._on_call = on_call

    async def call(self, request: ToolCallRequest) -> ToolCallOutcome:
        self._on_call()
        return await super().call(request)


_BOUNDARY_DIGEST = "f" * 64


class _Boundary:
    """研究边界事实替身：固定摘要与阻断原因（``None`` = 允许派发）。"""

    def __init__(self, *, digest: str = _BOUNDARY_DIGEST, blocked_reason: str | None = None) -> None:
        self.digest = digest
        self.blocked_reason = blocked_reason
        self.calls = 0

    async def attest(self) -> ToolBoundaryAttestation:
        self.calls += 1
        return ToolBoundaryAttestation(self.digest, self.blocked_reason)


def _runtime(
    dispatcher: _Dispatcher,
    dao: Any,
    boundary: _Boundary | None = None,
    *,
    approvals: ToolApprovalCoordinator | None = None,
    approval_timeout_s: float | None = None,
) -> ToolRuntime:
    values: dict[str, Any] = {
        "tools": (),
        "dispatcher": dispatcher,
        "catalog": (MCPToolDescriptor(name="search", schema_json=_schema()),),
        "connection_generation": 3,
        "identity": _IDENTITY,
        "scope": _SCOPE,
        "role_tools": ("search",),
        "request_tools": ("search",),
        "permission_tools": ("search",),
        "service_tools": ("search",),
        "audit_scope": ("search",),
        "dao": dao,
        "boundary": boundary or _Boundary(),
    }
    if approvals is not None:
        values["approvals"] = approvals
    if approval_timeout_s is not None:
        values["approval_timeout_s"] = approval_timeout_s
    return ToolRuntime(**values)  # type: ignore[arg-type]  # 单测替身，非真实管理器/DAO


class TestBuildToolRuntime:
    def test_installs_only_intersection(self) -> None:
        manager = _Manager(
            (
                MCPToolDescriptor(name="search", schema_json=_schema()),
                MCPToolDescriptor(name="quote", schema_json=_schema("quote tool")),
            )
        )

        runtime = build_tool_runtime(
            dao=_Dao(),  # type: ignore[arg-type]
            manager=manager,  # type: ignore[arg-type]
            identity=_IDENTITY,
            scope=_SCOPE,
            role_tools=("search", "quote"),
            request_tools=("search",),
            permission_tools=("search", "quote"),
            boundary=_Boundary(),
        )

        assert [tool.name for tool in runtime.tools] == ["search"]
        assert runtime.service_tools == ("quote", "search")
        assert runtime.audit_scope == ("search",)

    def test_empty_allowlist_installs_zero_tools(self) -> None:
        manager = _Manager((MCPToolDescriptor(name="search", schema_json=_schema()),))

        runtime = build_tool_runtime(
            dao=_Dao(),  # type: ignore[arg-type]
            manager=manager,  # type: ignore[arg-type]
            identity=_IDENTITY,
            scope=_SCOPE,
            role_tools=("search",),
            request_tools=(),
            permission_tools=("search",),
            boundary=_Boundary(),
        )

        assert runtime.tools == ()

    def test_incomplete_schema_tool_is_not_installed(self) -> None:
        incomplete = json.dumps({"type": "object", "properties": {"query": {"type": "string"}}})
        manager = _Manager(
            (
                MCPToolDescriptor(name="search", schema_json=_schema()),
                MCPToolDescriptor(name="loose", schema_json=incomplete),
            )
        )

        runtime = build_tool_runtime(
            dao=_Dao(),  # type: ignore[arg-type]
            manager=manager,  # type: ignore[arg-type]
            identity=_IDENTITY,
            scope=_SCOPE,
            role_tools=("search", "loose"),
            request_tools=("search", "loose"),
            permission_tools=("search", "loose"),
            boundary=_Boundary(),
        )

        assert [tool.name for tool in runtime.tools] == ["search"]


class TestToolRuntimeExecute:
    async def test_success_claims_then_saves_completed(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {"answer": 1}, "op-1"))
        dao = _Dao()
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.COMPLETED
        request = dispatcher.requests[0]
        assert request.arguments_digest == canonical_arguments_digest({"query": "x"})
        assert request.operation_id == dao.decisions[0].operation_id
        assert request.connection_generation == 3
        assert dao.decisions[0].audit_scope_digest == audit_scope_digest(("search",))
        payload = json.loads(dao.decisions[0].question_payload)
        assert payload["service_id"] == _IDENTITY.service_id
        assert payload["config_version"] == _IDENTITY.config_version
        # I43：授权时的研究边界事实摘要进入请求与持久化决定载荷。
        assert request.boundary_digest == _BOUNDARY_DIGEST
        assert payload["boundary_digest"] == _BOUNDARY_DIGEST
        # P1-10：决定必须有界有效期（未答/过期不可派发），不得缺省为无限期。
        decision = dao.decisions[0]
        assert decision.expires_at is not None
        expires_at = datetime.strptime(decision.expires_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
        assert datetime.now(UTC) < expires_at <= datetime.now(UTC) + timedelta(minutes=6)
        assert dao.results[-1].outcome_state == "completed"
        assert dao.results[-1].result_payload is not None

    async def test_disabled_source_blocks_before_decision_with_zero_dispatch(self) -> None:
        """I43：来源已禁用即零决定/零 claim/零派发（不把「读不到/已禁用」当作可用）。"""

        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _Dao()
        boundary = _Boundary(blocked_reason="source_disabled")
        runtime = _runtime(dispatcher, dao, boundary)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": AgentErrorCode.SOURCE_DISABLED.value}
        assert dao.decisions == []
        assert dao.results == []
        assert dispatcher.requests == []
        assert boundary.calls == 1

    async def test_missing_permission_is_denied_with_zero_dispatch(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _Dao(decision_error=AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "revoked", "id"))
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": AgentErrorCode.SOURCE_DISABLED.value}
        assert dispatcher.requests == []

    async def test_dispatcher_denial_is_recorded_as_failed(self) -> None:
        dispatcher = _Dispatcher(AgentServiceError(AgentErrorCode.POLICY_DENIED, "stale", "id"))
        dao = _Dao()
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert dao.results[-1].outcome_state == "failed"

    async def test_result_save_failure_is_explicit(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {"answer": 1}, "op-1"))
        dao = _Dao(result_error=RuntimeError("db down"))
        runtime = _runtime(dispatcher, dao)

        with pytest.raises(AgentServiceError) as excinfo:
            await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert excinfo.value.code is AgentErrorCode.OPERATION_UNKNOWN
        assert excinfo.value.sanitized_message == "tool_result_not_saved"

    async def test_budget_exceeded_records_unknown_then_raises(self) -> None:
        dispatcher = _Dispatcher(MCPBudgetExceeded("single_result_bytes", "id"))
        dao = _Dao()
        runtime = _runtime(dispatcher, dao)

        with pytest.raises(MCPBudgetExceeded) as exc:
            await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")
        assert exc.value.family == "single_result_bytes"

        assert dao.results[-1].outcome_state == "unknown"


class _ApprovalDao:
    """审批状态机替身：读出的持久许可状态由用例显式驱动（不直接返回 granted）。"""

    def __init__(
        self,
        *,
        state: ToolGrantState = ToolGrantState.PENDING,
        approval: bool | None = None,
        operation_phase: str = "prepared",
        effect_state: str = "not_dispatched",
        expires_in: float | None = 300.0,
    ) -> None:
        self.state = state
        self.approval = approval
        self.operation_phase = operation_phase
        self.effect_state = effect_state
        self.expires_at = None if expires_in is None else _wire(datetime.now(UTC) + timedelta(seconds=expires_in))
        self.decisions: list[Any] = []
        self.results: list[Any] = []

    async def save_tool_decision(self, request: Any) -> ToolGrantFact:
        self.decisions.append(request)
        return ToolGrantFact(
            decision_id=request.decision_id,
            grant_id="grant-1",
            grant_revision=1,
            grant_state=self.state,
        )

    async def save_tool_result(self, request: Any) -> None:
        self.results.append(request)
        phase, effect = {
            "completed": ("result_saved", "confirmed"),
            "failed": ("failed_confirmed", "not_dispatched"),
            "unknown": ("unknown", "unknown"),
        }[request.outcome_state]
        self.operation_phase = phase
        self.effect_state = effect

    async def read_operation_grant_scope(self, *, operation_id: str) -> OperationGrantScope:
        request = self.decisions[-1]
        return OperationGrantScope(
            user_scope=request.user_scope,
            operation_id=operation_id,
            execution_id=request.execution_id,
            round_id=request.round_id,
            grant_id="grant-1",
            decision_id=request.decision_id,
            grant_revision=1,
            grant_state=self.state,
            tool_call_id=request.tool_call_id,
            parameter_digest=request.parameter_digest,
            expires_at=self.expires_at,
            approval=self.approval,
            operation_phase=self.operation_phase,
            effect_state=self.effect_state,
            io_state="not_started",
        )

    async def claim_tool_dispatch(self, request: Any) -> ToolGrantFact:
        raise AssertionError("claim must go through dispatcher")


def _wire(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class _AutoAnswerCoordinator(ToolApprovalCoordinator):
    """登记等待后立刻落定答复（同事务语义的替身），验证消费后唤醒。"""

    def __init__(self, dao: _ApprovalDao, *, approve: bool) -> None:
        super().__init__()
        self._dao = dao
        self._approve = approve

    def prepare(self, *, decision_id: str) -> Any:
        ticket = super().prepare(decision_id=decision_id)
        self._dao.state = ToolGrantState.GRANTED if self._approve else ToolGrantState.REVOKED
        self._dao.approval = self._approve
        self.notify_consumed(decision_id)
        return ticket


class _CancellingTicket:
    async def wait(self, timeout_s: float) -> bool:
        raise asyncio.CancelledError()


class _CancellingCoordinator:
    def prepare(self, *, decision_id: str) -> _CancellingTicket:
        return _CancellingTicket()

    def release(self, ticket: _CancellingTicket) -> None:
        return None

    def notify_consumed(self, decision_id: str) -> None:
        return None


class TestToolApprovalWait:
    async def test_pending_waits_then_denies_with_approval_timeout(self) -> None:
        """PENDING 超时未答 → approval_timeout，零派发，如实落 failed/not_dispatched。"""

        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _ApprovalDao()
        runtime = _runtime(dispatcher, dao, approvals=ToolApprovalCoordinator(), approval_timeout_s=0.01)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": "approval_timeout"}
        assert dispatcher.requests == []
        assert dao.results[-1].outcome_state == "failed"

    async def test_user_rejection_denies_with_permission_rejected(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _ApprovalDao()
        runtime = _runtime(dispatcher, dao, approvals=_AutoAnswerCoordinator(dao, approve=False))

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": "permission_rejected"}
        assert dispatcher.requests == []
        assert dao.results[-1].outcome_state == "failed"

    async def test_approval_dispatches_once_and_replay_does_not_resend(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {"answer": 1}, "op-1"))
        dao = _ApprovalDao()
        runtime = _runtime(dispatcher, dao, approvals=_AutoAnswerCoordinator(dao, approve=True))

        first = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert first.state is ToolCallState.COMPLETED
        assert len(dispatcher.requests) == 1
        assert dao.results[-1].outcome_state == "completed"

        # 已认领/已入终态：同 tool_call_id 重放不得二次外发，也不得把已知结果改写为 failed。
        dao.state = ToolGrantState.CLAIMED
        dao.operation_phase = "result_saved"
        dao.effect_state = "confirmed"
        results_before = len(dao.results)

        replay = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert replay.state is ToolCallState.UNKNOWN
        assert len(dispatcher.requests) == 1
        assert len(dao.results) == results_before

    async def test_revocation_after_grant_denies_with_permission_revoked(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _ApprovalDao(state=ToolGrantState.REVOKED, approval=True)
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": "permission_revoked"}
        assert dispatcher.requests == []
        assert dao.results[-1].outcome_state == "failed"

    async def test_cancellation_while_waiting_propagates_with_zero_dispatch(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _ApprovalDao()
        runtime = _runtime(dispatcher, dao, approvals=_CancellingCoordinator())  # type: ignore[arg-type]

        with pytest.raises(asyncio.CancelledError):
            await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert dispatcher.requests == []
        assert dao.results == []

    async def test_admitted_but_expired_denies_with_approval_timeout(self) -> None:
        dispatcher = _Dispatcher(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))
        dao = _ApprovalDao(state=ToolGrantState.GRANTED, expires_in=-1.0)
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": "approval_timeout"}
        assert dispatcher.requests == []

    async def test_claim_race_revocation_is_reclassified_precisely(self) -> None:
        dispatcher = _RacingDispatcher(
            AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "revoked", "svc-1:2"),
            lambda: setattr(dao, "state", ToolGrantState.REVOKED),
        )
        dao = _ApprovalDao(state=ToolGrantState.GRANTED, approval=True)
        runtime = _runtime(dispatcher, dao)

        outcome = await runtime.execute(tool_name="search", arguments={"query": "x"}, tool_call_id="call-1")

        assert outcome.state is ToolCallState.FAILED
        assert outcome.result == {"state": "denied", "reason": "permission_revoked"}
        assert len(dispatcher.requests) == 1
        assert dao.results[-1].outcome_state == "failed"

    async def test_coordinator_notify_wakes_registered_waiter(self) -> None:
        coordinator = ToolApprovalCoordinator()
        ticket = coordinator.prepare(decision_id="decision-1")
        waiter = asyncio.create_task(ticket.wait(1.0))
        await asyncio.sleep(0)

        coordinator.notify_consumed("decision-1")

        assert await asyncio.wait_for(waiter, 1.0) is True
        coordinator.release(ticket)
