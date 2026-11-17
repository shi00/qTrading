"""多模型独立分析生产实现单测（F37/SQ-08，DD-F37）。

覆盖验收：
- 同输入一次冻结（同一 manifest/摘要，排队中资料/设置变化不混入）；
- 重复连接去重、同名不同 ID 不混；
- 逐路固定连接，运行期替换被拒绝（M15 精确授权语义）；
- D05 全局总位 + 同会话单总执行逐路排队；
- B 单独重试不跑 A；
- 共同源停止时组内一致停止；
- 未知结案不伪造成功/失败（R21）；
- 只读比较/导出零模型调用、零新派发。
"""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import (
    AgentValidationError,
    CommitState,
    CommandRef,
    Mode,
    Purpose,
    SourceKind,
    SourceVersionRef,
    SubmissionReceipt,
    derive_accept_identity,
    json_doc,
)
from data.persistence.daos.agent_dao import AgentDao
from services.agent_service.comparison import (
    COMPARISON_SESSION_SLOTS,
    COMPARISON_TOTAL_SLOTS,
    STOP_REASON_SHARED_SOURCE,
    ComparisonLaneObservation,
    ComparisonLaneSelection,
    ComparisonReadQuery,
    ComparisonSharedInput,
    ComparisonSubmissionRequest,
    FrozenComparisonGroup,
    build_comparison_view,
    build_lane_accept_request,
    comparison_export_rows,
    decode_comparison_manifest,
    drive_comparison_group,
    freeze_comparison_group,
    observations_from_executions,
    plan_group_stop,
    plan_lane_queue,
    plan_lane_retry,
    require_lane_connection,
)
from services.agent_service.service import AgentService

pytestmark = pytest.mark.unit


def _digest(char: str = "a") -> str:
    return char * 64


def _selection(connection_id: str, *, revision: int = 3, name: str = "提供方-连接") -> ComparisonLaneSelection:
    return ComparisonLaneSelection(
        connection_id=connection_id,
        connection_revision=revision,
        target_fingerprint=f"fp-{connection_id}",
        connection_purpose="research",
        scope_version=1,
        display_name=name,
    )


def _source(source_id: str = "src-1") -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.USER_MESSAGE,
        source_id=source_id,
        source_version="1",
        source_digest=_digest(source_id[-1] if source_id[-1] in "abcdef" else "b"),
    )


def _shared(*, text: str = "分析同一任务", source_id: str = "src-1") -> ComparisonSharedInput:
    return ComparisonSharedInput(
        text=text,
        mode=Mode.DISCUSS,
        purpose=Purpose.CONCEPT,
        cutoff="2026-11-16",
        source_refs=(_source(source_id),),
        visible_history_message_ids=("m-1",),
        visible_history_artifact_refs=(("art-1", "2"),),
        role_prompt_refs=(("role-main", "1"),),
        skill_refs=(("skill-1", "3"),),
        mcp_scope_digest=_digest("c"),
        output_requirements="给出结论与依据",
    )


def _group(
    lanes: tuple[ComparisonLaneSelection, ...] | None = None,
    *,
    shared: ComparisonSharedInput | None = None,
    group_command_id: str = "cmp-1",
) -> FrozenComparisonGroup:
    return freeze_comparison_group(
        user_scope="u1",
        session_id="s1",
        source_module="ui",
        group_command_id=group_command_id,
        shared=shared if shared is not None else _shared(),
        lanes=lanes if lanes is not None else (_selection("conn-a"), _selection("conn-b")),
    )


class TestFrozenGroup:
    def test_single_manifest_shared_by_all_lanes(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b"), _selection("conn-c")))
        assert len(group.lanes) == 3
        assert len({lane.lane_ref for lane in group.lanes}) == 3
        assert len({lane.business_request_id for lane in group.lanes}) == 3
        requests = [
            build_lane_accept_request(
                group,
                lane,
                expected_revision=0,
                node_id="root",
                workflow_id="wf",
                source_display_name="用户消息",
            )
            for lane in group.lanes
        ]
        assert {request.manifest_payload for request in requests} == {group.manifest_payload}
        assert {request.comparison_group_ref for request in requests} == {group.group_ref}
        assert len({request.comparison_lane_ref for request in requests}) == 3
        assert all(request.text == _shared().text for request in requests)

    def test_duplicate_connection_deduped_but_same_name_different_id_kept(self) -> None:
        group = _group(
            (
                _selection("conn-a", name="同名提供方"),
                _selection("conn-a", name="同名提供方"),
                _selection("conn-b", name="同名提供方"),
            )
        )
        assert [lane.selection.connection_id for lane in group.lanes] == ["conn-a", "conn-b"]

    def test_single_lane_is_not_a_comparison(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group((_selection("conn-a"), _selection("conn-a")))
        assert exc.value.code == "insufficient_lanes"

    def test_frozen_input_is_immune_to_later_snapshot_changes(self) -> None:
        sources = [_source("src-1")]
        shared = ComparisonSharedInput(
            text="原任务",
            mode=Mode.DISCUSS,
            purpose=Purpose.CONCEPT,
            source_refs=tuple(sources),
        )
        group = _group((_selection("conn-a"), _selection("conn-b")), shared=shared)
        original_manifest = group.manifest_payload
        original_digest = group.manifest_digest
        # 排队期间资料/设置变化（调用方改原集合）不得进入已冻结输入。
        sources.append(_source("src-2"))
        changed = _shared(text="新任务", source_id="src-9")
        later = _group((_selection("conn-a"), _selection("conn-b")), shared=changed, group_command_id="cmp-2")
        assert group.manifest_payload == original_manifest
        assert group.manifest_digest == original_digest
        assert later.manifest_digest != original_digest
        assert group.input_digest != later.input_digest
        request = build_lane_accept_request(
            group,
            group.lanes[0],
            expected_revision=0,
            node_id="root",
            workflow_id="wf",
            source_display_name="用户消息",
        )
        assert request.text == "原任务"

    def test_manifest_roundtrip_preserves_identity(self) -> None:
        group = _group()
        decoded = decode_comparison_manifest(json.loads(group.manifest_payload))
        assert decoded.group_ref == group.group_ref
        assert decoded.manifest_payload == group.manifest_payload
        assert decoded.manifest_digest == group.manifest_digest
        assert decoded.input_digest == group.input_digest
        assert [lane.lane_ref for lane in decoded.lanes] == [lane.lane_ref for lane in group.lanes]

    def test_accept_request_requires_paired_comparison_refs(self) -> None:
        group = _group()
        request = build_lane_accept_request(
            group,
            group.lanes[0],
            expected_revision=0,
            node_id="root",
            workflow_id="wf",
            source_display_name="用户消息",
        )
        assert request.comparison_group_ref == group.group_ref
        assert request.comparison_lane_ref == group.lanes[0].lane_ref
        with pytest.raises(AgentValidationError) as exc:
            derive_accept_identity(replace(request, comparison_lane_ref=None))
        assert exc.value.code == "invalid_comparison_refs"
        with pytest.raises(AgentValidationError) as exc:
            derive_accept_identity(replace(request, comparison_group_ref=None))
        assert exc.value.code == "invalid_comparison_refs"

    def test_runtime_connection_substitution_rejected(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b")))
        lane = group.lanes[0]
        assert (
            require_lane_connection(
                group,
                lane.lane_ref,
                connection_id="conn-a",
                connection_revision=3,
            )
            is lane
        )
        with pytest.raises(AgentValidationError) as exc:
            require_lane_connection(group, lane.lane_ref, connection_id="conn-c", connection_revision=3)
        assert exc.value.code == "connection_mismatch"
        with pytest.raises(AgentValidationError) as exc:
            require_lane_connection(group, lane.lane_ref, connection_id="conn-a", connection_revision=4)
        assert exc.value.code == "connection_mismatch"


class TestRetryAndQueue:
    def test_lane_retry_targets_only_selected_lane(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b"), _selection("conn-c")))
        lane_b = group.lanes[1]
        retry = plan_lane_retry(group, lane_b.lane_ref, round_id="round-2")
        assert retry.lane_ref == lane_b.lane_ref
        assert retry.manifest_payload == group.manifest_payload
        assert retry.business_request_id != lane_b.business_request_id
        assert all(lane.lane_ref != retry.lane_ref for lane in group.lanes[:1] + group.lanes[2:])

    def test_queue_respects_session_and_global_slots(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b"), _selection("conn-c")))
        decisions = plan_lane_queue(group, active_total_executions=0, active_session_executions=0)
        assert [decision.decision for decision in decisions] == ["dispatch", "queued", "queued"]
        assert decisions[0].reason is None
        assert all(decision.reason == "d05_session_single_total_execution" for decision in decisions[1:])
        global_blocked = plan_lane_queue(
            group,
            active_total_executions=COMPARISON_TOTAL_SLOTS,
            active_session_executions=0,
        )
        assert all(decision.decision == "queued" for decision in global_blocked)
        assert all(decision.reason == "d05_total_execution_slots" for decision in global_blocked)
        assert COMPARISON_SESSION_SLOTS == 1

    @pytest.mark.asyncio
    async def test_drive_runs_next_lane_only_after_previous_completes(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b"), _selection("conn-c")))
        started: list[str] = []
        active: set[str] = set()
        max_active = 0

        async def start_lane(lane: Any) -> str:
            started.append(lane.lane_ref)
            active.add(lane.lane_ref)
            nonlocal max_active
            max_active = max(max_active, len(active))
            return f"wf-{lane.lane_ref}"

        async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            facts: dict[str, ComparisonLaneObservation] = {}
            for lane_ref, _workflow_id in children:
                facts[lane_ref] = ComparisonLaneObservation(
                    lane_ref=lane_ref, display_state="complete", reliable_result=True
                )
                active.discard(lane_ref)
            return facts

        view = await drive_comparison_group(group, start_lane, observe)
        assert started == [lane.lane_ref for lane in group.lanes]
        assert max_active == 1
        assert view.group_state == "complete"
        assert view.goal_coverage == "full"


class TestConvergence:
    def test_pending_lane_keeps_group_unfinished(self) -> None:
        group = _group()
        view = build_comparison_view(
            group,
            (
                ComparisonLaneObservation(lane_ref=group.lanes[0].lane_ref, display_state="running"),
                ComparisonLaneObservation(lane_ref=group.lanes[1].lane_ref, display_state="queued"),
            ),
        )
        assert view.ended is False
        assert view.group_state == "running"
        assert view.goal_coverage == "unknown"

    def test_all_complete_is_full(self) -> None:
        group = _group()
        view = build_comparison_view(
            group,
            tuple(
                ComparisonLaneObservation(lane_ref=lane.lane_ref, display_state="complete", reliable_result=True)
                for lane in group.lanes
            ),
        )
        assert (view.group_state, view.goal_coverage, view.ended) == ("complete", "full", True)

    def test_partial_and_failed_are_not_merged(self) -> None:
        group = _group()
        view = build_comparison_view(
            group,
            (
                ComparisonLaneObservation(
                    lane_ref=group.lanes[0].lane_ref, display_state="complete", reliable_result=True
                ),
                ComparisonLaneObservation(
                    lane_ref=group.lanes[1].lane_ref, display_state="failed", reliable_result=False
                ),
            ),
        )
        assert (view.group_state, view.goal_coverage) == ("partial", "partial")
        assert view.complete_count == 1 and view.failed_count == 1

    def test_closed_unknown_never_fabricates_success_or_failure(self) -> None:
        group = _group()
        view = build_comparison_view(
            group,
            (
                ComparisonLaneObservation(
                    lane_ref=group.lanes[0].lane_ref, display_state="complete", reliable_result=True
                ),
                ComparisonLaneObservation(
                    lane_ref=group.lanes[1].lane_ref, display_state="closed_unknown", reliable_result=None
                ),
            ),
        )
        assert view.ended is True
        assert view.group_state == "closed_unknown"
        assert view.goal_coverage == "unknown"
        assert view.unconfirmed_count == 1
        assert view.failed_count == 0
        assert view.complete_count == 1
        rows = comparison_export_rows(view)
        assert {row["result_state"] for row in rows} == {"complete", "unknown"}

    def test_group_stop_is_consistent_for_shared_source(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b"), _selection("conn-c")))
        plan = plan_group_stop(
            group,
            (
                ComparisonLaneObservation(
                    lane_ref=group.lanes[0].lane_ref, display_state="complete", reliable_result=True
                ),
                ComparisonLaneObservation(lane_ref=group.lanes[1].lane_ref, display_state="running"),
            ),
            reason=STOP_REASON_SHARED_SOURCE,
        )
        assert plan.dispatch_closed is True
        assert len(plan.decisions) == len(group.lanes)
        actions = {decision.action for decision in plan.decisions}
        assert actions == {"preserve_result", "stop_new_dispatch", "withdraw"}
        assert all(decision.reason.startswith(STOP_REASON_SHARED_SOURCE) for decision in plan.decisions)


class _FakeReadDao:
    def __init__(self, manifest_payload: str, executions: tuple[object, ...]) -> None:
        self.manifest_payload = manifest_payload
        self.executions = executions
        self.calls: list[str] = []

    async def read_comparison_manifest(self, *, user_scope: str, group_ref: str) -> str | None:
        self.calls.append("read_comparison_manifest")
        assert user_scope == "u1"
        return self.manifest_payload

    async def get_session_read(self, query: Any, *, user_scope: str) -> Any:
        self.calls.append("get_session_read")
        return SimpleNamespace(executions=self.executions)

    async def accept_request(self, request: Any) -> Any:
        self.calls.append("accept_request")
        return SubmissionReceipt(
            command=CommandRef(
                user_scope=request.user_scope,
                source_module=request.source_module,
                business_request_id=request.business_request_id,
                command_id=request.client_command_id,
                expected_revision=request.expected_revision,
            ),
            command_record_id=f"cmd-{request.comparison_lane_ref}",
            session_id=request.session_id,
            execution_id=f"exec-{request.comparison_lane_ref}",
            workflow_id=request.workflow_id,
            intent_digest="0" * 64,
            commit_state=CommitState.COMMITTED,
            verified_at="2026-11-16T00:00:00.000000Z",
            next_action=None,
        )


class TestServiceComparison:
    @pytest.mark.asyncio
    async def test_read_and_export_only_touch_read_paths(self) -> None:
        group = _group()
        executions = tuple(
            SimpleNamespace(
                comparison_group_ref=group.group_ref,
                comparison_lane_ref=lane.lane_ref,
                display_state="complete",
                goal_coverage="full",
            )
            for lane in group.lanes
        )
        fake = _FakeReadDao(group.manifest_payload, executions)
        service = AgentService(cast(AgentDao, fake))
        query = ComparisonReadQuery(user_scope="u1", session_id="s1", group_ref=group.group_ref)
        view = await service.read_comparison(query)
        assert view.group_state == "complete"
        assert fake.calls == ["read_comparison_manifest", "get_session_read"]
        rows = await service.export_comparison(query)
        assert len(rows) == 2
        assert fake.calls == [
            "read_comparison_manifest",
            "get_session_read",
            "read_comparison_manifest",
            "get_session_read",
        ]
        assert all(call in {"read_comparison_manifest", "get_session_read"} for call in fake.calls)

    @pytest.mark.asyncio
    async def test_submit_queues_independent_full_tasks_with_fixed_connections(self) -> None:
        fake = _FakeReadDao("", ())
        service = AgentService(cast(AgentDao, fake))
        request = ComparisonSubmissionRequest(
            user_scope="u1",
            session_id="s1",
            source_module="ui",
            group_command_id="cmp-1",
            shared=_shared(),
            lanes=(_selection("conn-a"), _selection("conn-b")),
        )
        receipt = await service.submit_comparison(request)
        assert receipt.lane_count == 2
        assert receipt.all_accepted is True
        assert fake.calls == ["accept_request", "accept_request"]

    @pytest.mark.asyncio
    async def test_observations_from_executions_keep_unknown(self) -> None:
        group = _group()
        executions = (
            SimpleNamespace(
                comparison_group_ref=group.group_ref,
                comparison_lane_ref=group.lanes[0].lane_ref,
                display_state="closed_unknown",
            ),
            SimpleNamespace(
                comparison_group_ref=group.group_ref,
                comparison_lane_ref=group.lanes[1].lane_ref,
                display_state="failed",
            ),
        )
        observations = observations_from_executions(group, executions)
        by_lane = {observation.lane_ref: observation for observation in observations}
        assert by_lane[group.lanes[0].lane_ref].reliable_result is None
        assert by_lane[group.lanes[1].lane_ref].reliable_result is False
