"""ResearchComparisonViewModel 单测（F37）。

覆盖：连接资格判定与拒绝、费用未知不填 0、单路重试、停止整组、共享 MCP
断线影响清单、耗时只读投影、取消传播（R2）。
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from core.agent_types import stable_id
from ui.viewmodels.research_comparison_view_model import (
    SHARED_MCP_DISCONNECT_REASON,
    VIEW_SIDE_BY_SIDE,
    VIEW_TABLE,
    ResearchComparisonViewModel,
    build_comparison_connection_options,
)


class _FakeCatalog:
    def __init__(self, snapshot: Any) -> None:
        self._snapshot = snapshot
        self.calls = 0

    async def get_snapshot(self) -> Any:
        self.calls += 1
        if isinstance(self._snapshot, Exception):
            raise self._snapshot
        return self._snapshot


class _FakeService:
    def __init__(
        self,
        *,
        view: Any = None,
        receipt: Any = None,
        read: Any = None,
        retry: Any = None,
        stop: Any = None,
        read_error: BaseException | None = None,
        session_error: Exception | None = None,
        submit_error: Exception | None = None,
        retry_error: Exception | None = None,
        stop_error: Exception | None = None,
    ) -> None:
        self.view = view
        self.receipt = receipt
        self.read = read
        self.retry = retry
        self.stop = stop
        self.read_error = read_error
        self.session_error = session_error
        self.submit_error = submit_error
        self.retry_error = retry_error
        self.stop_error = stop_error
        self.submitted: list[Any] = []
        self.retried: list[tuple[str, int]] = []
        self.stopped: list[str] = []
        self.read_queries: list[Any] = []
        self.run_calls = 0
        self.submit_calls = 0

    async def run_comparison(self, request: Any) -> Any:
        self.run_calls += 1
        self.submitted.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        return self.receipt

    async def submit_comparison(self, request: Any) -> Any:
        self.submit_calls += 1
        self.submitted.append(request)
        if self.submit_error is not None:
            raise self.submit_error
        return self.receipt

    async def read_comparison(self, query: Any) -> Any:
        self.read_queries.append(query)
        if self.read_error is not None:
            raise self.read_error
        return self.view

    async def retry_comparison_lane(
        self,
        query: Any,
        lane_ref: str,
        *,
        expected_revision: int,
        node_id: str = "root",
        source_display_name: str = "用户消息",
    ) -> Any:
        self.retried.append((lane_ref, expected_revision))
        if self.retry_error is not None:
            raise self.retry_error
        return self.retry

    async def stop_comparison_group(self, query: Any, *, reason: str = "") -> Any:
        self.stopped.append(reason)
        if self.stop_error is not None:
            raise self.stop_error
        return self.stop

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        if self.session_error is not None:
            raise self.session_error
        return self.read


def _snapshot(*connections: dict[str, Any], scope_version: int = 7) -> Any:
    return SimpleNamespace(connections=tuple(connections), security_scope_version=scope_version)


def _conn(
    connection_id: str,
    *,
    enabled: bool = True,
    adapter_kind: str = "litellm_chat",
    api_base: str | None = "https://api.example.com/v1",
    revision: int = 3,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "connection_id": connection_id,
        "name": name or connection_id,
        "model": "gpt-x",
        "enabled": enabled,
        "adapter_kind": adapter_kind,
        "api_base": api_base,
        "connection_revision": revision,
    }


def _lane(
    lane_ref: str,
    *,
    state: str | None = "complete",
    reliable: bool | None = True,
    reason: str = "complete",
    gap: str | None = None,
    cost: Any = None,
) -> Any:
    return SimpleNamespace(
        lane_ref=lane_ref,
        connection_id=f"c-{lane_ref}",
        connection_revision=1,
        display_name=f"模型 {lane_ref}",
        display_state=state,
        reliable_result=reliable,
        reason=reason,
        gap=gap,
        cost=cost,
    )


def _view(*lanes: Any, group_state: str = "partial", coverage: str = "partial") -> Any:
    return SimpleNamespace(
        group_ref="group-1",
        group_state=group_state,
        goal_coverage=coverage,
        ended=False,
        selected_count=len(lanes),
        complete_count=sum(1 for lane in lanes if lane.display_state == "complete"),
        partial_count=0,
        failed_count=0,
        pending_count=1,
        not_started_count=0,
        unconfirmed_count=0,
        lanes=tuple(lanes),
        cost_state="partial",
        known_cny=None,
        reserved_cny=Decimal("1.5"),
        unpriced_attempts=2,
    )


def _cost(*, state: str = "ok", known: Decimal | None = None, unpriced: int = 0) -> Any:
    return SimpleNamespace(
        read_state=state,
        known_cny=known,
        reserved_cny=None,
        unpriced_attempts=unpriced,
    )


def _vm(service: Any, *, session_id: str | None = "session-1") -> ResearchComparisonViewModel:
    return ResearchComparisonViewModel(
        service,
        catalog=_FakeCatalog(_snapshot(_conn("a"), _conn("b"))),
        session_id=session_id,
    )


async def _select_two(vm: ResearchComparisonViewModel) -> None:
    await vm.load_connections()
    vm.set_lane("a", "a")
    vm.set_lane("b", "b")
    vm.set_text(_SHARED_TEXT)


_SHARED_TEXT = "比较两家模型的结论"


def _expected_group_ref(session_id: str = "session-1") -> str:
    """与 VM 一致的稳定组身份（同一 group_command_id → 同一 group_ref）。"""

    command_id = stable_id(
        "comparison_group_command",
        ["local-desktop", session_id, _SHARED_TEXT, "a", "b"],
    )
    return stable_id("comparison_group", ["local-desktop", session_id, command_id])


# ---------------------------------------------------------------------------
# 连接目录与选择校验
# ---------------------------------------------------------------------------


def test_build_options_classifies_eligibility() -> None:
    snapshot = _snapshot(
        _conn("ok"),
        _conn("off", enabled=False),
        _conn("local", adapter_kind="gguf_chat"),
        _conn("no_target", api_base=None),
        _conn("no_revision", revision=0),
        {"connection_id": ""},
    )

    options = build_comparison_connection_options(snapshot)

    by_id = {option.connection_id: option for option in options}
    assert by_id["ok"].eligible is True
    assert by_id["ok"].reject_key is None
    assert by_id["ok"].target_fingerprint is not None
    assert by_id["off"].reject_key == "agent_comparison_connection_disabled"
    assert by_id["local"].reject_key == "agent_comparison_connection_local_unsupported"
    assert by_id["no_target"].reject_key == "agent_comparison_connection_no_target"
    assert by_id["no_revision"].reject_key == "agent_comparison_connection_unknown"
    assert len(options) == 5


@pytest.mark.asyncio
async def test_set_lane_rejects_ineligible_and_duplicate() -> None:
    vm = _vm(_FakeService())
    await vm.load_connections()

    vm.set_lane("a", "a")
    vm.set_lane("b", "a")
    assert vm.state.lane_a == "a"
    assert vm.state.lane_b is None
    assert vm.state.selection_error_key == "agent_comparison_lane_duplicate"

    vm.set_lane("b", "missing")
    assert vm.state.selection_error_key == "agent_comparison_connection_unknown"
    assert vm.state.lane_b is None


@pytest.mark.asyncio
async def test_load_connections_failure_reports_error() -> None:
    vm = ResearchComparisonViewModel(_FakeService(), catalog=_FakeCatalog(RuntimeError("boom")), session_id="s")

    await vm.load_connections()

    assert vm.state.connections == ()
    assert vm.state.error_key == "agent_comparison_connections_load_failed"


# ---------------------------------------------------------------------------
# 提交与只读投影
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_requires_session_two_lanes_and_text() -> None:
    service = _FakeService()
    unsaved = _vm(service, session_id=None)
    await _select_two(unsaved)
    await unsaved.submit()
    assert unsaved.state.selection_error_key == "agent_comparison_no_session"

    vm = _vm(service)
    await vm.load_connections()
    await vm.submit()
    assert vm.state.selection_error_key == "agent_comparison_text_required"

    vm.set_text(_SHARED_TEXT)
    await vm.submit()
    assert vm.state.selection_error_key == "agent_comparison_need_two_lanes"

    vm.set_lane("a", "a")
    vm.set_lane("b", "b")
    assert service.submitted == []


@pytest.mark.asyncio
async def test_submit_freezes_group_and_projects_costs() -> None:
    service = _FakeService(
        view=_view(
            _lane("lane-1", cost=_cost(known=Decimal("0.25"))),
            _lane("lane-2", state="failed", reliable=False, reason="failed_no_result", cost=_cost(state="failed")),
        ),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(
            summary=SimpleNamespace(draft_revision=4),
            executions=[
                SimpleNamespace(
                    comparison_group_ref=_expected_group_ref(), comparison_lane_ref="lane-1", active_ms=1200
                ),
                SimpleNamespace(comparison_group_ref=_expected_group_ref(), comparison_lane_ref="lane-2", active_ms=0),
            ],
        ),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    assert service.run_calls == 1
    assert service.submit_calls == 0
    assert len(service.submitted) == 1
    request = service.submitted[0]
    assert request.expected_revision == 4
    assert [lane.connection_id for lane in request.lanes] == ["a", "b"]
    assert request.lanes[0].scope_version == 7
    assert request.lanes[0].connection_purpose == "end_of_day_research"
    # 比较路冻结主角色与显式零外部能力集（已核准空集），否则派发侧无法解析确定性计划。
    assert request.shared.role_prompt_refs
    assert request.shared.role_prompt_refs[0][0] == "main"
    assert request.shared.skill_refs == ()
    assert request.shared.mcp_scope_digest is None
    assert vm.state.group_ref is not None
    assert vm.state.message_key == "agent_comparison_submitted"
    lanes = {lane.lane_ref: lane for lane in vm.state.lanes}
    assert lanes["lane-1"].known_cny == Decimal("0.25")
    assert lanes["lane-1"].active_ms == 1200
    assert lanes["lane-2"].active_ms == 0
    assert lanes["lane-2"].retryable is True
    assert lanes["lane-2"].cost_key == "agent_comparison_cost_state_failed"


@pytest.mark.asyncio
async def test_unknown_cost_stays_none() -> None:
    service = _FakeService(
        view=_view(
            _lane(
                "lane-1",
                state="running",
                reliable=None,
                reason="started_no_fact",
                gap="no_terminal_fact",
                cost=None,
            )
        ),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=0), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    lane = vm.state.lanes[0]
    assert lane.cost_state == "unread"
    assert lane.known_cny is None
    assert lane.unpriced_attempts is None
    assert lane.active_ms is None
    assert lane.gap_key == "agent_comparison_gap_no_terminal_fact"
    assert vm.state.group is not None
    assert vm.state.group.known_cny is None


# ---------------------------------------------------------------------------
# 单路重试 / 停止整组
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_targets_only_requested_lane() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=9), executions=[]),
        retry=SimpleNamespace(error_code=None),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()
    service.retried.clear()

    await vm.retry_lane("lane-2")

    assert service.retried == [("lane-2", 9)]
    assert vm.state.message_key == "agent_comparison_retry_submitted"
    assert vm.state.retrying_lane is None


@pytest.mark.asyncio
async def test_retry_rejection_surfaces_error_code() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
        retry=SimpleNamespace(error_code="lane_reliable_result"),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()

    await vm.retry_lane("lane-1")

    assert vm.state.message_key == "agent_comparison_retry_rejected"
    assert vm.state.error_code == "lane_reliable_result"


@pytest.mark.asyncio
async def test_retry_aborts_when_session_revision_unreadable() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        retry=SimpleNamespace(error_code=None),
        session_error=RuntimeError("session down"),
    )
    vm = _vm(service)
    vm._set_state(group_ref="group-1")

    await vm.retry_lane("lane-1")

    assert service.retried == []
    assert vm.state.retrying_lane is None
    assert vm.state.error_key == "agent_comparison_session_revision_unavailable"
    assert vm.state.error_code == "session_revision_unavailable"


@pytest.mark.asyncio
async def test_stop_group_reports_verification_state() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
        stop=SimpleNamespace(all_verified=False),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()

    await vm.stop_group()

    assert service.stopped == ["user_ended_group"]
    assert vm.state.message_key == "agent_comparison_stopped_unverified"
    assert vm.state.stopping is False


# ---------------------------------------------------------------------------
# 共享 MCP 断线影响清单
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mcp_impact_marks_only_affected_lanes() -> None:
    service = _FakeService(
        view=_view(_lane("placeholder-1"), _lane("placeholder-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()
    # 服务层视图的 lane_ref 一定来自同一冻结组；按组内真实 lane_ref 重建视图。
    group = vm._group
    assert group is not None
    lane_ref_a, lane_ref_b = (lane.lane_ref for lane in group.lanes)
    service.view = _view(
        _lane(lane_ref_a),
        _lane(lane_ref_b, state="running", reliable=None, reason="started_no_fact"),
    )
    await vm.refresh()

    vm.report_shared_mcp_disconnect()

    impacts = {lane.lane_ref: lane.mcp_impact for lane in vm.state.lanes}
    assert impacts[lane_ref_a] == "preserve_result"
    assert impacts[lane_ref_b] == "stop_new_dispatch"
    assert vm.state.message_key == "agent_comparison_mcp_reported"
    assert vm.state.mcp_reported is True
    assert SHARED_MCP_DISCONNECT_REASON == "shared_mcp_disconnected"
    # 未被误标：已完成路展示「未受影响」，在途路展示「停止新派发」。
    keys = {lane.lane_ref: lane.mcp_impact_key for lane in vm.state.lanes}
    assert keys[lane_ref_a] == "agent_comparison_impact_unaffected"
    assert keys[lane_ref_b] == "agent_comparison_impact_stop_new_dispatch"


@pytest.mark.asyncio
async def test_mcp_impact_unknown_group_is_not_reported_as_unaffected() -> None:
    service = _FakeService(
        view=_view(_lane("lane-9"), _lane("lane-10")),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
    )
    vm = _vm(service)
    await vm.open_group("session-1", "group-external")
    assert vm.state.group_ref == "group-external"

    vm.report_shared_mcp_disconnect()

    assert vm.state.message_key == "agent_comparison_mcp_unavailable"
    assert all(lane.mcp_impact is None for lane in vm.state.lanes)


# ---------------------------------------------------------------------------
# 取消传播 / 会话切换
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_propagates_cancelled_error() -> None:
    service = _FakeService(read_error=asyncio.CancelledError())
    vm = _vm(service)

    raised = False
    try:
        await vm.open_group("session-1", "group-1")
    except asyncio.CancelledError:
        raised = True

    assert raised is True
    assert vm.state.loading is False


@pytest.mark.asyncio
async def test_set_session_drops_previous_group() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=0), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()
    assert vm.state.group_ref is not None

    vm.set_session("session-2")

    assert vm.state.group_ref is None
    assert vm.state.lanes == ()
    assert vm.state.group is None


# ---------------------------------------------------------------------------
# 纯状态守卫与错误分支
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_command_guards_ignore_invalid_input() -> None:
    vm = _vm(_FakeService())
    await vm.load_connections()

    vm.set_lane("c", "a")
    assert vm.state.lane_a is None
    vm.set_lane("a", "a")
    vm.set_lane("a", None)
    assert vm.state.lane_a is None
    assert vm.state.selection_error_key is None

    vm.set_mode("bogus")
    assert vm.state.mode == "execute"
    vm.set_mode("plan")
    assert vm.state.mode == "plan"
    vm.set_view_mode("bogus")
    assert vm.state.view_mode == VIEW_SIDE_BY_SIDE
    vm.set_view_mode(VIEW_TABLE)
    assert vm.state.view_mode == VIEW_TABLE

    vm.set_session("session-1")
    assert vm.state.session_id == "session-1"


@pytest.mark.asyncio
async def test_ineligible_connection_rejected_by_reason() -> None:
    vm = ResearchComparisonViewModel(
        _FakeService(),
        catalog=_FakeCatalog(_snapshot(_conn("off", enabled=False), _conn("b"))),
    )
    await vm.load_connections()

    vm.set_lane("b", "off")

    assert vm.state.lane_b is None
    assert vm.state.selection_error_key == "agent_comparison_connection_disabled"


@pytest.mark.asyncio
async def test_submit_failure_reports_generic_error_code() -> None:
    service = _FakeService(
        submit_error=RuntimeError("network down"),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    assert vm.state.submitting is False
    assert vm.state.error_key == "agent_comparison_error_generic"
    assert vm.state.error_code == "RuntimeError"
    assert vm.state.group_ref is None


@pytest.mark.asyncio
async def test_unavailable_service_rejects_all_commands() -> None:
    vm = ResearchComparisonViewModel(None)

    await vm.submit()
    assert vm.state.error_key == "agent_comparison_not_wired"
    await vm.retry_lane("lane-1")
    assert vm.state.error_key == "agent_comparison_not_wired"
    await vm.stop_group()
    assert vm.state.error_key == "agent_comparison_not_wired"
    vm.report_shared_mcp_disconnect()
    assert vm.state.message_key == "agent_comparison_mcp_unavailable"


@pytest.mark.asyncio
async def test_retry_and_stop_failures_and_reentrancy_guards() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=1), executions=[]),
        retry_error=RuntimeError("retry down"),
        stop_error=RuntimeError("stop down"),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()

    await vm.retry_lane("lane-1")
    assert vm.state.error_key == "agent_comparison_error_generic"
    assert vm.state.error_code == "RuntimeError"
    await vm.stop_group()
    assert vm.state.error_code == "RuntimeError"

    vm._set_state(retrying_lane="lane-1")
    service.retried.clear()
    await vm.retry_lane("lane-2")
    assert service.retried == []
    vm._set_state(retrying_lane=None, stopping=True)
    service.stopped.clear()
    await vm.stop_group()
    assert service.stopped == []


@pytest.mark.asyncio
async def test_session_read_failure_aborts_submit_without_revision_guess() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        session_error=RuntimeError("session down"),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    # 读取失败不伪造 0 版受理：不调用编排，显式报错中止。
    assert service.submitted == []
    assert service.run_calls == 0
    assert vm.state.submitting is False
    assert vm.state.error_key == "agent_comparison_session_revision_unavailable"
    assert vm.state.error_code == "session_revision_unavailable"
    assert vm.state.group_ref is None


@pytest.mark.asyncio
async def test_invalid_draft_revision_aborts_submit_without_revision_guess() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=True), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    assert service.submitted == []
    assert vm.state.error_key == "agent_comparison_session_revision_unavailable"
    assert vm.state.error_code == "session_revision_unavailable"


@pytest.mark.asyncio
async def test_missing_draft_revision_aborts_submit_without_revision_guess() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    assert service.submitted == []
    assert vm.state.error_key == "agent_comparison_session_revision_unavailable"


@pytest.mark.asyncio
async def test_lane_durations_skip_other_group_and_invalid_rows() -> None:
    read = SimpleNamespace(
        summary=SimpleNamespace(draft_revision=5),
        executions=[
            SimpleNamespace(comparison_group_ref="other-group", comparison_lane_ref="lane-1", active_ms=10),
            SimpleNamespace(comparison_group_ref=_expected_group_ref(), comparison_lane_ref="lane-1", active_ms=True),
            SimpleNamespace(comparison_group_ref=_expected_group_ref(), comparison_lane_ref=None, active_ms=20),
            SimpleNamespace(comparison_group_ref=_expected_group_ref(), comparison_lane_ref="lane-2", active_ms=30),
        ],
    )
    service = _FakeService(
        view=_view(_lane("lane-1"), _lane("lane-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=read,
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    lanes = {lane.lane_ref: lane for lane in vm.state.lanes}
    assert lanes["lane-1"].active_ms is None
    assert lanes["lane-2"].active_ms == 30
    # 合法稿版按原值冻结为受理 CAS 基线。
    assert service.submitted[0].expected_revision == 5


@pytest.mark.asyncio
async def test_mcp_impact_validation_error_falls_back_to_unknown() -> None:
    service = _FakeService(
        view=_view(_lane("unrelated-1"), _lane("unrelated-2")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=0), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)
    await vm.submit()

    vm.report_shared_mcp_disconnect()

    assert vm.state.message_key == "agent_comparison_mcp_unavailable"
    assert all(lane.mcp_impact is None for lane in vm.state.lanes)


@pytest.mark.asyncio
async def test_unstarted_lane_state_uses_unstarted_key() -> None:
    service = _FakeService(
        view=_view(_lane("lane-1", state=None, reliable=None, reason="not_started")),
        receipt=SimpleNamespace(all_accepted=True),
        read=SimpleNamespace(summary=SimpleNamespace(draft_revision=0), executions=[]),
    )
    vm = _vm(service)
    await _select_two(vm)

    await vm.submit()

    assert vm.state.lanes[0].state_key == "agent_comparison_lane_state_unstarted"
