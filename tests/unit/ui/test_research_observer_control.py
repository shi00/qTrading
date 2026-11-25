"""ResearchObserverViewModel 执行控制（W-01）单测：动作映射、命令提交与结果分列。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    ExecutionControlKind,
    ExecutionControlReceipt,
    RetryExecutionReceipt,
)
from ui.viewmodels.research_observer_view_model import (
    CONTROL_DISABLED_DISPATCHED,
    CONTROL_DISABLED_RETRY_ROOT,
    CONTROL_DISABLED_TERMINAL,
    ControlResultView,
    ResearchObserverViewModel,
    control_actions_for,
)

pytestmark = pytest.mark.unit


def _execution(
    execution_id: str,
    *,
    display_state: str = "running",
    parent_execution_id: str | None = None,
    node_id: str | None = None,
    current_round_id: str | None = "r-1",
    revision: int = 3,
    dispatch_closed: bool = False,
    execution_kind: str = "root",
) -> Any:
    return SimpleNamespace(
        execution_id=execution_id,
        display_state=display_state,
        parent_execution_id=parent_execution_id,
        model_attempt_count=1,
        tool_call_count=2,
        active_ms=100,
        goal_coverage="partial",
        node_id=node_id or execution_id,
        execution_kind=execution_kind,
        current_round_id=current_round_id,
        revision=revision,
        dispatch_closed=dispatch_closed,
        ended_at=None,
    )


def _read(executions: tuple[Any, ...]) -> Any:
    return SimpleNamespace(
        summary=SimpleNamespace(
            title="研究 A",
            updated_at="2026-11-24T02:00:00.000000Z",
            draft_revision=0,
            cleaned_at=None,
            revision=2,
            pinned=False,
            archived=False,
        ),
        executions=executions,
        messages=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        artifacts=SimpleNamespace(items=(), has_more=False, next_cursor=None),
        decisions=(),
    )


class _ControlService:
    def __init__(
        self,
        read: Any,
        *,
        control_error: BaseException | None = None,
        receipt: Any = None,
        retry_receipt: RetryExecutionReceipt | None = None,
        retry_error: BaseException | None = None,
        control_gate: asyncio.Event | None = None,
    ) -> None:
        self.read = read
        self.control_error = control_error
        self.control_gate = control_gate
        self.retry_error = retry_error
        self.receipt = receipt or ExecutionControlReceipt(
            execution_id="e-1",
            command_record_id="cmd-1",
            kind="pause",
            previous_display_state="running",
            display_state="paused",
            execution_revision=4,
            applied=True,
            replayed=False,
            reason=None,
            released_slot_ids=(),
            open_slot_ids=("slot-1",),
        )
        self.retry_receipt = retry_receipt or RetryExecutionReceipt(
            execution_id="e-1",
            command_record_id="cmd-r",
            round_id="r-1",
            new_round_id="r-2",
            redo=("n1", "n2"),
            preserved=("n3",),
            plan_digest="digest",
            payload_digest="payload",
            applied=True,
            replayed=False,
            reason=None,
            dispatch_state="not_wired",
        )
        self.control_requests: list[Any] = []
        self.retry_requests: list[Any] = []

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        return self.read

    async def control(self, request: Any) -> Any:
        self.control_requests.append(request)
        if self.control_gate is not None:
            await self.control_gate.wait()
        if self.control_error is not None:
            raise self.control_error
        return self.receipt

    async def retry_execution(self, request: Any) -> Any:
        self.retry_requests.append(request)
        if self.retry_error is not None:
            raise self.retry_error
        return self.retry_receipt


class _ServiceError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = SimpleNamespace(value=code)


@pytest.mark.asyncio
class TestControlActions:
    async def test_actions_reflect_node_states(self) -> None:
        service = _ControlService(
            _read(
                (
                    _execution("e-1", display_state="running"),
                    _execution(
                        "e-2",
                        display_state="failed",
                        parent_execution_id="e-1",
                        execution_kind="leaf",
                    ),
                )
            )
        )
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        actions = {action.kind: action for action in vm.control_actions("e-1")}
        assert actions["pause"].enabled is True
        assert actions["resume"].enabled is False
        assert actions["terminate"].enabled is True
        assert actions["withdraw_queued"].enabled is False
        assert actions["withdraw_queued"].disabled_reason_key == CONTROL_DISABLED_DISPATCHED

        leaf_actions = {action.kind: action for action in vm.control_actions("e-2")}
        assert leaf_actions["retry"].enabled is False  # 根 running 不可重试

    async def test_retry_enabled_only_when_root_failed(self) -> None:
        service = _ControlService(
            _read(
                (
                    _execution("e-1", display_state="failed"),
                    _execution(
                        "e-2",
                        display_state="failed",
                        parent_execution_id="e-1",
                        execution_kind="leaf",
                    ),
                )
            )
        )
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")
        leaf_actions = {action.kind: action for action in vm.control_actions("e-2")}
        assert leaf_actions["retry"].enabled is True

    async def test_terminal_node_actions_disabled(self) -> None:
        node = _execution("e-1", display_state="complete")
        from ui.viewmodels.research_observer_view_model import ExecutionNodeView

        projected = ExecutionNodeView(
            execution_id="e-1",
            parent_execution_id=None,
            display_state="complete",
            goal_coverage="full",
            model_attempt_count=0,
            tool_call_count=0,
            active_ms=0,
            depth=0,
            has_children=False,
        )
        actions = {action.kind: action for action in control_actions_for(projected, root=node)}
        assert all(not action.enabled for action in actions.values())
        assert actions["pause"].disabled_reason_key == CONTROL_DISABLED_TERMINAL


@pytest.mark.asyncio
class TestControlCommands:
    async def test_pause_only_targets_one_execution(self) -> None:
        service = _ControlService(
            _read(
                (
                    _execution("e-1", display_state="running"),
                    _execution("e-2", display_state="running"),
                )
            )
        )
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.pause_execution("e-1")

        assert len(service.control_requests) == 1
        request = service.control_requests[0]
        assert request.execution_id == "e-1"
        assert request.kind is ExecutionControlKind.PAUSE
        assert request.expected_execution_revision == 3
        assert request.round_id == "r-1"
        assert vm.state.control_result is not None
        assert vm.state.control_result.display_state == "paused"
        assert vm.state.control_result.open_slot_count == 1
        # 设计取向（B）：回执如实标「不影响在途调用」，不冒充已中止。
        assert vm.state.control_result.native_control_state == "not_wired"

    async def test_terminate_requires_reason_without_service_call(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.terminate_execution("e-1", reason="   ")

        assert service.control_requests == []
        assert vm.state.control_result is not None
        assert vm.state.control_result.error_code == "CONTROL_REASON_REQUIRED"

    async def test_conflict_recorded_as_error(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)), control_error=_ServiceError("REVISION_CONFLICT"))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.terminate_execution("e-1", reason="用户终止")

        assert vm.state.control_result is not None
        assert vm.state.control_result.applied is False
        assert vm.state.control_result.error_code == "REVISION_CONFLICT"

    async def test_retry_uses_root_revision_and_target_node(self) -> None:
        service = _ControlService(
            _read(
                (
                    _execution("e-1", display_state="failed", revision=7, current_round_id="r-9"),
                    _execution(
                        "e-2",
                        display_state="failed",
                        parent_execution_id="e-1",
                        node_id="node-x",
                        execution_kind="leaf",
                    ),
                )
            )
        )
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.retry_execution("e-1", "e-2")

        assert len(service.retry_requests) == 1
        request = service.retry_requests[0]
        assert request.execution_id == "e-1"
        assert request.targets == ("node-x",)
        assert request.expected_execution_revision == 7
        assert request.round_id == "r-9"
        assert vm.state.control_result is not None
        assert vm.state.control_result.kind == "retry"
        assert vm.state.control_result.native_control_state == "not_wired"

    async def test_duplicate_command_key_is_stable(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.pause_execution("e-1")
        first = service.control_requests[0].client_command_id

        # 同一状态重复提交派生同一命令键（重放而非新事实）。
        await vm.pause_execution("e-1")
        second = service.control_requests[1].client_command_id
        assert first == second


@pytest.mark.asyncio
class TestControlEdgeBranches:
    async def test_unknown_execution_has_no_node_or_actions(self) -> None:
        vm = ResearchObserverViewModel(cast(Any, _ControlService(_read((_execution("e-1"),)))))
        await vm.refresh("s-1")

        assert vm.execution_node("ghost") is None
        assert vm.control_actions("ghost") == ()

    async def test_each_command_maps_to_its_own_kind(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.resume_execution("e-1")
        await vm.close_unknown_execution("e-1", reason="结果未知")
        await vm.withdraw_execution("e-1")

        assert [request.kind for request in service.control_requests] == [
            ExecutionControlKind.RESUME,
            ExecutionControlKind.CLOSE_UNKNOWN,
            ExecutionControlKind.WITHDRAW_QUEUED,
        ]
        assert service.control_requests[1].reason == "结果未知"
        assert service.control_requests[2].reason is None

    async def test_prompt_reason_ignores_non_reason_kinds(self) -> None:
        vm = ResearchObserverViewModel(cast(Any, _ControlService(_read((_execution("e-1"),)))))
        await vm.refresh("s-1")

        vm.prompt_control_reason("e-1", "pause")
        assert vm.state.control_reason_execution_id is None

        vm.prompt_control_reason("e-1", "terminate")
        assert vm.state.control_reason_execution_id == "e-1"
        assert vm.state.control_reason_kind == "terminate"

    async def test_submit_reason_requires_prompt_and_non_empty_text(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.submit_control_reason()
        assert service.control_requests == [], "无原因输入态时不得提交"

        vm.prompt_control_reason("e-1", "terminate")
        await vm.submit_control_reason()
        assert vm.state.control_reason_error_key == "agent_research_control_reason_required"
        assert service.control_requests == []

        vm.update_control_reason("用户终止")
        await vm.submit_control_reason()
        assert service.control_requests[-1].kind is ExecutionControlKind.TERMINATE
        assert service.control_requests[-1].reason == "用户终止"
        assert vm.state.control_reason_execution_id is None
        assert vm.state.control_reason_kind is None

        vm.prompt_control_reason("e-1", "close_unknown")
        vm.update_control_reason("结果未知")
        await vm.submit_control_reason()
        assert service.control_requests[-1].kind is ExecutionControlKind.CLOSE_UNKNOWN
        assert service.control_requests[-1].reason == "结果未知"

    async def test_cancel_reason_clears_input_state(self) -> None:
        vm = ResearchObserverViewModel(cast(Any, _ControlService(_read((_execution("e-1"),)))))
        await vm.refresh("s-1")
        vm.prompt_control_reason("e-1", "terminate")
        vm.update_control_reason("半截原因")

        vm.cancel_control_reason()

        assert vm.state.control_reason_execution_id is None
        assert vm.state.control_reason_text == ""

    async def test_control_without_service_reports_unavailable(self) -> None:
        vm = ResearchObserverViewModel(cast(Any, None))

        await vm.pause_execution("e-1")
        assert vm.state.last_error_code == "CONTROL_UNAVAILABLE"

        await vm.retry_execution("e-1", "e-2")
        assert vm.state.last_error_code == "CONTROL_UNAVAILABLE"

    async def test_control_unknown_target_reports_target_unavailable(self) -> None:
        vm = ResearchObserverViewModel(cast(Any, _ControlService(_read((_execution("e-1"),)))))
        await vm.refresh("s-1")

        await vm.pause_execution("ghost")

        result = vm.state.control_result
        assert isinstance(result, ControlResultView)
        assert result.error_code == "CONTROL_TARGET_UNAVAILABLE"

    async def test_pending_control_blocks_duplicate_submission(self) -> None:
        gate = asyncio.Event()
        service = _ControlService(_read((_execution("e-1"),)), control_gate=gate)
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        first = asyncio.create_task(vm.pause_execution("e-1"))
        await asyncio.sleep(0)
        assert vm.state.control_pending_execution_id == "e-1"

        await vm.pause_execution("e-1")
        assert len(service.control_requests) == 1, "在途命令期间不重复提交"

        gate.set()
        await first
        assert vm.state.control_pending_execution_id is None

    async def test_control_cancellation_propagates_and_clears_pending(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)), control_error=asyncio.CancelledError())
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        with pytest.raises(asyncio.CancelledError) as info:
            await vm.pause_execution("e-1")

        assert info.value.args == ()
        assert vm.state.control_pending_execution_id is None

    async def test_untyped_receipt_is_not_treated_as_success(self) -> None:
        service = _ControlService(_read((_execution("e-1"),)), receipt=SimpleNamespace(applied=True))
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.pause_execution("e-1")

        result = vm.state.control_result
        assert isinstance(result, ControlResultView)
        assert result.error_code == "CONTROL_UNTYPED_RECEIPT"

    async def test_retry_unknown_target_and_missing_round_are_rejected(self) -> None:
        service = _ControlService(
            _read(
                (
                    _execution("e-1", display_state="failed", current_round_id=None),
                    _execution(
                        "e-2",
                        display_state="failed",
                        parent_execution_id="e-1",
                        execution_kind="leaf",
                    ),
                )
            )
        )
        vm = ResearchObserverViewModel(cast(Any, service))
        await vm.refresh("s-1")

        await vm.retry_execution("e-1", "ghost")
        assert vm.state.control_result is not None
        assert vm.state.control_result.error_code == "CONTROL_TARGET_UNAVAILABLE"

        await vm.retry_execution("e-1", "e-2")
        assert vm.state.control_result.error_code == "CONTROL_TARGET_UNAVAILABLE"
        assert service.retry_requests == []

    async def test_retry_cancel_propagates_and_error_is_recorded(self) -> None:
        cancel_service = _ControlService(
            _read((_execution("e-1", display_state="failed"),)), retry_error=asyncio.CancelledError()
        )
        cancel_vm = ResearchObserverViewModel(cast(Any, cancel_service))
        await cancel_vm.refresh("s-1")

        with pytest.raises(asyncio.CancelledError) as info:
            await cancel_vm.retry_execution("e-1", "e-1")
        assert info.value.args == ()
        assert cancel_vm.state.control_pending_execution_id is None

        error_service = _ControlService(
            _read((_execution("e-1", display_state="failed"),)),
            retry_error=_ServiceError("OPERATION_UNKNOWN"),
        )
        error_vm = ResearchObserverViewModel(cast(Any, error_service))
        await error_vm.refresh("s-1")

        await error_vm.retry_execution("e-1", "e-1")
        assert error_vm.state.control_result is not None
        assert error_vm.state.control_result.error_code == "OPERATION_UNKNOWN"

    async def test_root_lookup_handles_orphan_and_too_deep_chain(self) -> None:
        deep = (_execution("e0"),) + tuple(
            _execution(f"e{i}", parent_execution_id=f"e{i - 1}", execution_kind="leaf", display_state="failed")
            for i in range(1, 34)
        )
        orphan = _execution("o-1", parent_execution_id="ghost", execution_kind="leaf", display_state="failed")
        vm = ResearchObserverViewModel(cast(Any, _ControlService(_read(deep + (orphan,)))))
        await vm.refresh("s-1")

        deepest = {action.kind: action for action in vm.control_actions("e33")}
        assert deepest["retry"].disabled_reason_key == CONTROL_DISABLED_RETRY_ROOT

        orphaned = {action.kind: action for action in vm.control_actions("o-1")}
        assert orphaned["retry"].disabled_reason_key == CONTROL_DISABLED_RETRY_ROOT
