"""执行控制命令族（W-01）单测：状态机、重试范围、入参校验。

覆盖：暂停/继续/终止/未知结案/排队撤回的合法迁移与幂等点、跨轮/终态拒绝、
单任务重试的精确范围（目标 + 传递下游，独立兄弟保留）与不可重试拒绝。
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    ExecutionControlKind,
    ExecutionControlRequest,
    PlanNodeSpec,
    PlanSpecProposal,
    plan_control_transition,
    validate_control_request,
)
from services.agent_service.control import plan_retry
from services.agent_service.service import AgentService

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
