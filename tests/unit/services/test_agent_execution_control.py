"""执行控制命令族（W-01）单测：状态机、重试范围、入参校验。

覆盖：暂停/继续/终止/未知结案/排队撤回的合法迁移与幂等点、跨轮/终态拒绝、
单任务重试的精确范围（目标 + 传递下游，独立兄弟保留）与不可重试拒绝。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ExecutionControlKind,
    ExecutionControlRequest,
    PlanItemSnapshot,
    PlanNodeSpec,
    PlanProposal,
    PlanSpecProposal,
    RetryCommandRecord,
    RetryExecutionReceipt,
    RetryExecutionRequest,
    json_doc,
    plan_control_transition,
    stable_id,
    validate_control_request,
)
from services.agent_service.control import plan_retry
from services.agent_service.service import AgentService
from services.agent_service.workflows import (
    ParentBindIdentity,
    admit_research_plan,
    decode_research_payload_full,
)

pytestmark = pytest.mark.unit


def _control_request(**overrides: Any) -> ExecutionControlRequest:
    values: dict[str, Any] = {
        "user_scope": "u",
        "session_id": "s",
        "execution_id": "e",
        "client_command_id": "c",
        "expected_execution_revision": 1,
        "kind": ExecutionControlKind.PAUSE,
        "round_id": "r",
        "reason": None,
    }
    values.update(overrides)
    return cast(Any, ExecutionControlRequest)(**values)


class TestTerminalAndUnknownKind:
    def test_pause_terminal_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.PAUSE,
                "complete",
                current_round_id="r-1",
                dispatch_closed=True,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT
        assert info.value.sanitized_message == "terminal_state"

    def test_resume_terminal_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.RESUME,
                "ended",
                current_round_id="r-1",
                dispatch_closed=True,
                round_stop_reason="user_termination",
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT
        assert info.value.sanitized_message == "terminal_state"

    def test_close_unknown_twice_is_idempotent(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.CLOSE_UNKNOWN,
            "closed_unknown",
            current_round_id="r-1",
            dispatch_closed=True,
            round_stop_reason="closed_unknown",
            identity="u/e",
        )
        assert decision.applied is False
        assert decision.reason == "already_closed_unknown"
        assert decision.next_state == "closed_unknown"

    def test_unknown_control_kind_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                cast(Any, "bogus"),
                "running",
                current_round_id="r-1",
                dispatch_closed=False,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.INPUT_INVALID
        assert info.value.sanitized_message == "unknown control kind"


class TestControlRequestValidation:
    @pytest.mark.parametrize(
        ("overrides", "fragment"),
        [
            ({"user_scope": ""}, "user_scope must be non-empty text"),
            ({"expected_execution_revision": 0}, "expected_execution_revision must be an int >= 1"),
            ({"expected_execution_revision": True}, "expected_execution_revision must be an int >= 1"),
            ({"kind": "pause"}, "kind must be ExecutionControlKind"),
            ({"round_id": ""}, "round_id must be non-empty text"),
            ({"reason": ""}, "reason must be non-empty text"),
        ],
    )
    def test_invalid_requests_are_rejected_with_input_invalid(self, overrides: dict[str, Any], fragment: str) -> None:
        with pytest.raises(AgentServiceError) as info:
            validate_control_request(_control_request(**overrides))
        assert info.value.code is AgentErrorCode.INPUT_INVALID
        assert fragment in info.value.sanitized_message

    def test_valid_request_passes(self) -> None:
        validate_control_request(_control_request())


class _ControlDao:
    def __init__(self) -> None:
        self.control_requests: list[Any] = []
        self.receipt = object()

    async def apply_execution_control(self, request: Any) -> Any:
        self.control_requests.append(request)
        return self.receipt

    async def revise_queued(self, request: Any) -> str:
        return "revised"


@pytest.mark.asyncio
class TestServiceControlDispatch:
    async def test_control_routes_execution_control_request_to_dao(self) -> None:
        dao = _ControlDao()
        service = AgentService(cast(Any, dao))
        request = _control_request()

        result = await service.control(request)

        assert result is dao.receipt
        assert dao.control_requests == [request]

    async def test_control_rejects_unknown_request_type(self) -> None:
        service = AgentService(cast(Any, _ControlDao()))

        with pytest.raises(AgentServiceError) as info:
            await service.control(cast(Any, object()))

        assert info.value.code is AgentErrorCode.INPUT_INVALID
        assert info.value.sanitized_message == "unsupported control request type"


def _plan() -> PlanSpecProposal:
    return PlanSpecProposal(
        nodes=(
            PlanNodeSpec(node_id="n1", target="任务一", role_key="analyst"),
            PlanNodeSpec(node_id="n2", target="任务二", role_key="analyst", depends_on=("n1",)),
            PlanNodeSpec(node_id="n3", target="任务三（独立）", role_key="analyst"),
        )
    )


class TestPauseResume:
    def test_pause_running_closes_dispatch_only(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.PAUSE,
            "running",
            current_round_id="r-1",
            dispatch_closed=False,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is True
        assert decision.next_state == "paused"
        assert decision.dispatch_closed is True
        assert decision.ends is False
        assert decision.round_stop_reason is None

    def test_pause_is_idempotent_on_paused(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.PAUSE,
            "paused",
            current_round_id="r-1",
            dispatch_closed=True,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is False
        assert decision.reason == "already_paused"

    def test_pause_queued_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.PAUSE,
                "queued",
                current_round_id=None,
                dispatch_closed=False,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_pause_without_round_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.PAUSE,
                "running",
                current_round_id=None,
                dispatch_closed=False,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_resume_reopens_dispatch(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.RESUME,
            "paused",
            current_round_id="r-1",
            dispatch_closed=True,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is True
        assert decision.next_state == "running"
        assert decision.dispatch_closed is False

    def test_resume_unknown_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.RESUME,
                "unknown",
                current_round_id="r-1",
                dispatch_closed=True,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_resume_running_is_idempotent(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.RESUME,
            "running",
            current_round_id="r-1",
            dispatch_closed=False,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is False
        assert decision.reason == "already_running"


class TestTerminateAndUnknown:
    def test_terminate_ends_with_user_reason(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.TERMINATE,
            "running",
            current_round_id="r-1",
            dispatch_closed=False,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is True
        assert decision.next_state == "ended"
        assert decision.round_stop_reason == "user_termination"
        assert decision.ends is True

    def test_terminate_repeat_is_idempotent(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.TERMINATE,
            "ended",
            current_round_id="r-1",
            dispatch_closed=True,
            round_stop_reason="user_termination",
            identity="u/e",
        )
        assert decision.applied is False
        assert decision.reason == "already_terminated"

    def test_terminate_natural_end_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.TERMINATE,
                "complete",
                current_round_id="r-1",
                dispatch_closed=True,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_close_unknown_never_marks_success(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.CLOSE_UNKNOWN,
            "running",
            current_round_id="r-1",
            dispatch_closed=False,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.next_state == "closed_unknown"
        assert decision.round_stop_reason == "closed_unknown"
        assert decision.applied is True

    def test_close_unknown_on_completed_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.CLOSE_UNKNOWN,
                "complete",
                current_round_id="r-1",
                dispatch_closed=True,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_terminate_requires_reason(self) -> None:
        request = ExecutionControlRequest(
            user_scope="u",
            session_id="s",
            execution_id="e",
            client_command_id="c",
            expected_execution_revision=1,
            kind=ExecutionControlKind.TERMINATE,
            round_id="r",
            reason=None,
        )
        with pytest.raises(AgentServiceError) as info:
            validate_control_request(request)
        assert info.value.code is AgentErrorCode.INPUT_INVALID


class TestWithdraw:
    def test_withdraw_queued(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.WITHDRAW_QUEUED,
            "queued",
            current_round_id=None,
            dispatch_closed=False,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is True
        assert decision.next_state == "withdrawn"
        assert decision.ends is True

    def test_withdraw_dispatched_is_rejected(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_control_transition(
                ExecutionControlKind.WITHDRAW_QUEUED,
                "running",
                current_round_id="r-1",
                dispatch_closed=False,
                round_stop_reason=None,
                identity="u/e",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_withdraw_twice_is_idempotent(self) -> None:
        decision = plan_control_transition(
            ExecutionControlKind.WITHDRAW_QUEUED,
            "withdrawn",
            current_round_id=None,
            dispatch_closed=True,
            round_stop_reason=None,
            identity="u/e",
        )
        assert decision.applied is False
        assert decision.reason == "already_withdrawn"


class TestRetryScope:
    def test_retry_only_target_and_downstream(self) -> None:
        retry = plan_retry(
            _plan(),
            targets=("n1",),
            node_states={"n1": "failed", "n2": "planned", "n3": "complete"},
            execution_id="e-1",
            new_round_id="r-2",
        )
        assert retry.scope.redo == ("n1", "n2")
        assert retry.scope.preserved == ("n3",)
        assert [outcome.node_id for outcome in retry.preserved_outcomes] == ["n3"]
        payload_nodes = cast(list[dict[str, Any]], retry.payload["nodes"])
        assert [node["node_id"] for node in payload_nodes] == ["n1", "n2"]

    def test_retry_rejects_completed_target(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_retry(
                _plan(),
                targets=("n1",),
                node_states={"n1": "complete"},
                execution_id="e-1",
                new_round_id="r-2",
            )
        assert info.value.code is AgentErrorCode.REVISION_CONFLICT

    def test_retry_rejects_unknown_target(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_retry(
                _plan(),
                targets=("missing",),
                node_states={"n1": "failed"},
                execution_id="e-1",
                new_round_id="r-2",
            )
        assert info.value.code is AgentErrorCode.INPUT_INVALID

    def test_retry_rejects_missing_preserved_predecessor(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_retry(
                _plan(),
                targets=("n2",),
                node_states={"n1": "failed", "n2": "failed"},
                execution_id="e-1",
                new_round_id="r-2",
            )
        assert info.value.code is AgentErrorCode.INPUT_INVALID

    def test_retry_rejects_duplicate_targets(self) -> None:
        with pytest.raises(AgentServiceError) as info:
            plan_retry(
                _plan(),
                targets=("n1", "n1"),
                node_states={"n1": "failed"},
                execution_id="e-1",
                new_round_id="r-2",
            )
        assert info.value.code is AgentErrorCode.INPUT_INVALID


# ---------------------------------------------------------------------------
# 服务层重试派发：落库成功 / 投递失败 / 投递未知 三态分列
# ---------------------------------------------------------------------------

_EXECUTION_ID = "exec-1"
_USER_SCOPE = "u1"
_CLIENT_COMMAND_ID = "cmd-retry-1"


def _manifest() -> str:
    return json_doc(
        {
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
    )


def _retry_proposal() -> PlanProposal:
    return PlanProposal(
        execution_id=_EXECUTION_ID,
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


class _RetryDao:
    """重试服务所需的最小 DAO 面（不触库；只验证范围/落库/派发编排）。"""

    def __init__(self, *, replay_receipt: bool = False) -> None:
        self.recorded: list[RetryCommandRecord] = []
        self._replay_receipt = replay_receipt

    async def read_authoritative_manifest(self, *, user_scope: str, execution_id: str) -> str:
        return _manifest()

    async def read_current_plan_proposal(self, *, user_scope: str, execution_id: str) -> PlanProposal:
        return _retry_proposal()

    async def get_session_read(self, query: Any, *, user_scope: str) -> Any:
        return SimpleNamespace(
            executions=(
                SimpleNamespace(node_id="step-1", parent_execution_id=_EXECUTION_ID, display_state="failed"),
                SimpleNamespace(node_id="step-2", parent_execution_id=_EXECUTION_ID, display_state="planned"),
            )
        )

    async def record_retry_command(self, request: RetryCommandRecord) -> RetryExecutionReceipt:
        self.recorded.append(request)
        return RetryExecutionReceipt(
            execution_id=request.execution_id,
            command_record_id=stable_id(
                "command", [request.user_scope, request.execution_id, request.client_command_id]
            ),
            round_id=request.round_id,
            new_round_id=request.new_round_id,
            redo=request.redo,
            preserved=request.preserved,
            plan_digest=request.plan_digest,
            payload_digest=request.payload_digest,
            applied=True,
            replayed=len(self.recorded) > 1 and self._replay_receipt,
            reason=None,
        )


class _RetryLauncherStub:
    def __init__(
        self,
        *,
        submitted: bool = True,
        reason: str | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.submitted = submitted
        self.reason = reason
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def launch(
        self,
        payload: dict[str, object],
        *,
        user_scope: str,
        execution_id: str,
        round_id: str,
    ) -> tuple[bool, str | None]:
        self.calls.append(
            {
                "payload": payload,
                "user_scope": user_scope,
                "execution_id": execution_id,
                "round_id": round_id,
            }
        )
        if self.error is not None:
            raise self.error
        return self.submitted, self.reason


def _retry_request() -> RetryExecutionRequest:
    return RetryExecutionRequest(
        user_scope=_USER_SCOPE,
        session_id="s1",
        execution_id=_EXECUTION_ID,
        client_command_id=_CLIENT_COMMAND_ID,
        expected_execution_revision=5,
        round_id="r-1",
        targets=("step-1",),
    )


def _expected_round_id() -> str:
    return stable_id("round", [_USER_SCOPE, _EXECUTION_ID, _CLIENT_COMMAND_ID])


_BINDING = ParentBindIdentity(
    user_scope=_USER_SCOPE,
    session_id="s1",
    workflow_code_version="1.0.0",
    run_epoch=2,
    expected_input_revision=3,
)


async def _binding_provider(user_scope: str, execution_id: str) -> ParentBindIdentity | None:
    return _BINDING


async def _missing_binding_provider(user_scope: str, execution_id: str) -> ParentBindIdentity | None:
    return None


def _service(
    dao: _RetryDao, launcher: _RetryLauncherStub | None = None, *, provider: Any = _binding_provider
) -> AgentService:
    return AgentService(
        cast(Any, dao),
        retry_launcher=None if launcher is None else cast(Any, launcher),
        retry_binding_identity_provider=None if provider is None else cast(Any, provider),
    )


@pytest.mark.asyncio
class TestServiceRetryDispatch:
    async def test_submitted_retry_launches_persisted_new_round(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub()
        service = _service(dao, launcher)

        receipt = await service.retry_execution(_retry_request())

        assert receipt.applied is True
        assert receipt.new_round_id == _expected_round_id()
        assert receipt.dispatch_state == "submitted"
        assert receipt.dispatch_reason is None
        assert dao.recorded[0].targets == ("step-1",)
        assert len(launcher.calls) == 1
        assert launcher.calls[0]["round_id"] == receipt.new_round_id
        call = launcher.calls[0]
        assert call["user_scope"] == _USER_SCOPE
        assert call["execution_id"] == _EXECUTION_ID
        assert call["round_id"] == receipt.new_round_id
        payload = cast(dict[str, Any], call["payload"])
        assert payload["round_id"] == receipt.new_round_id
        assert [node["node_id"] for node in payload["nodes"]] == ["step-1", "step-2"]
        # 载荷必须带父 workflow 准入所需的冻结策略与绑定身份，否则会被入口拒绝。
        assert payload["role_capabilities"] == {"researcher": ["skill:skill-a@2"]}
        assert payload["binding_identity"] == _BINDING.to_wire()
        decoded = decode_research_payload_full(payload)
        admit_research_plan(
            decoded.plan,
            plan_digest=decoded.plan_digest,
            role_capabilities=decoded.role_capabilities,
            preserved=decoded.preserved,
        )
        assert decoded.binding_identity == _BINDING

    async def test_refused_launch_keeps_persisted_fact_and_marks_failed(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub(submitted=False, reason="dispatch_closed")
        service = _service(dao, launcher)

        receipt = await service.retry_execution(_retry_request())

        # 命令已落库，但明确未投递：不冒充已重跑，也不标成未知。
        assert receipt.applied is True
        assert receipt.dispatch_state == "failed"
        assert receipt.dispatch_reason == "dispatch_closed"
        assert len(dao.recorded) == 1

    async def test_launch_exception_marks_dispatch_unknown(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub(error=RuntimeError("native client unavailable"))
        service = _service(dao, launcher)

        receipt = await service.retry_execution(_retry_request())

        # 落库成功 ≠ 已投递；异常时投递状态未知（可能在原生受理后被中断）。
        assert receipt.applied is True
        assert receipt.dispatch_state == "unknown"
        assert receipt.dispatch_reason is not None

    async def test_without_launcher_dispatch_stays_not_wired(self) -> None:
        service = _service(_RetryDao(), launcher=None)

        receipt = await service.retry_execution(_retry_request())

        assert receipt.applied is True
        assert receipt.dispatch_state == "not_wired"

    async def test_replay_keeps_stable_round_identity_and_redelivers_idempotently(self) -> None:
        dao = _RetryDao(replay_receipt=True)
        launcher = _RetryLauncherStub()
        service = _service(dao, launcher)

        first = await service.retry_execution(_retry_request())
        second = await service.retry_execution(_retry_request())

        assert second.replayed is True
        assert [call["round_id"] for call in launcher.calls] == [first.new_round_id, second.new_round_id]
        assert first.new_round_id == second.new_round_id == _expected_round_id()

    async def test_retry_launch_cancellation_propagates(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub(error=asyncio.CancelledError())
        service = _service(dao, launcher)

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await service.retry_execution(_retry_request())

        assert isinstance(exc_info.value, asyncio.CancelledError)
        # R2：取消不吞；落库事实保留，供重放时按同一命令键重投。
        assert len(dao.recorded) == 1

    async def test_missing_binding_identity_fails_closed_before_persisting(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub()
        service = _service(dao, launcher, provider=_missing_binding_provider)

        with pytest.raises(AgentServiceError) as info:
            await service.retry_execution(_retry_request())

        assert info.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert dao.recorded == [], "身份不可得时不落注定无法派发的重试事实"
        assert launcher.calls == []

    async def test_unwired_binding_identity_provider_fails_closed(self) -> None:
        dao = _RetryDao()
        launcher = _RetryLauncherStub()
        service = _service(dao, launcher, provider=None)

        with pytest.raises(AgentServiceError) as info:
            await service.retry_execution(_retry_request())

        assert info.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert dao.recorded == []
        assert launcher.calls == []
