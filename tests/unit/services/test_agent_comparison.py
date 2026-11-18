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


class TestFrozenInputValidation:
    """冻结入口的字段校验：非法输入显式失败，不静默降级。"""

    def test_empty_text_rejected(self) -> None:
        shared = ComparisonSharedInput(text="", mode=Mode.DISCUSS, purpose=Purpose.CONCEPT)
        with pytest.raises(AgentValidationError) as exc:
            _group(shared=shared)
        assert exc.value.code == "invalid_text"
        assert exc.value.field_path == "$.shared.text"

    def test_invalid_mcp_scope_digest_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group(shared=replace(_shared(), mcp_scope_digest="not-a-digest"))
        assert exc.value.code == "invalid_digest"
        assert exc.value.field_path == "$.shared.mcp_scope_digest"

    def test_invalid_source_digest_rejected(self) -> None:
        bad_source = SourceVersionRef(
            source_kind=SourceKind.USER_MESSAGE,
            source_id="src-1",
            source_version="1",
            source_digest="bad",
        )
        with pytest.raises(AgentValidationError) as exc:
            _group(shared=replace(_shared(), source_refs=(bad_source,)))
        assert exc.value.code == "invalid_digest"
        assert exc.value.field_path == "$.shared.source_refs.source_digest"

    def test_lane_revision_must_be_positive(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group((_selection("conn-a", revision=0), _selection("conn-b")))
        assert exc.value.code == "invalid_revision"
        assert exc.value.field_path == "$.lanes.connection_revision"

    def test_lane_scope_version_must_be_non_negative(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group((replace(_selection("conn-a"), scope_version=-1), _selection("conn-b")))
        assert exc.value.code == "invalid_revision"
        assert exc.value.field_path == "$.lanes.connection_scope_version"

    def test_shared_mode_must_be_mode_tag(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group(shared=replace(_shared(), mode=cast(Any, "discuss")))
        assert exc.value.code == "invalid_tag"
        assert exc.value.field_path == "$.shared.mode"

    def test_shared_purpose_must_be_purpose_tag(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _group(shared=replace(_shared(), purpose=cast(Any, "concept")))
        assert exc.value.code == "invalid_tag"
        assert exc.value.field_path == "$.shared.purpose"


class TestManifestDecodeValidation:
    """已保存 manifest 的严格解码：缺字段/多字段/畸形 wire 都显式失败。"""

    def _payload(self) -> dict[str, Any]:
        return json.loads(_group().manifest_payload)

    def _decode_code(self, payload: object) -> str:
        with pytest.raises(AgentValidationError) as exc:
            decode_comparison_manifest(payload)
        return exc.value.code

    def test_payload_must_be_object(self) -> None:
        assert self._decode_code([]) == "invalid_type"

    def test_missing_key_reported(self) -> None:
        payload = self._payload()
        del payload["lanes"]
        assert self._decode_code(payload) == "missing_field"

    def test_extra_key_reported(self) -> None:
        payload = self._payload()
        payload["extra"] = 1
        assert self._decode_code(payload) == "unknown_field"

    def test_unsupported_schema_version_rejected(self) -> None:
        payload = self._payload()
        payload["schema_version"] = 2
        assert self._decode_code(payload) == "unsupported_version"

    def test_lanes_must_be_non_empty_list(self) -> None:
        payload = self._payload()
        payload["lanes"] = []
        assert self._decode_code(payload) == "invalid_type"

    def test_lane_must_be_object(self) -> None:
        payload = self._payload()
        payload["lanes"][0] = "not-a-lane"
        assert self._decode_code(payload) == "invalid_type"

    def test_shared_must_be_object(self) -> None:
        payload = self._payload()
        payload["shared"] = []
        assert self._decode_code(payload) == "invalid_type"

    def test_shared_mode_and_purpose_must_be_strings(self) -> None:
        payload = self._payload()
        payload["shared"]["mode"] = 1
        assert self._decode_code(payload) == "invalid_type"

        payload = self._payload()
        payload["shared"]["purpose"] = 1
        assert self._decode_code(payload) == "invalid_type"

    def test_shared_tag_values_rejected(self) -> None:
        payload = self._payload()
        payload["shared"]["mode"] = "bogus"
        assert self._decode_code(payload) == "invalid_tag"

        payload = self._payload()
        payload["shared"]["purpose"] = "bogus"
        assert self._decode_code(payload) == "invalid_tag"

    def test_shared_source_refs_and_message_ids_must_be_lists(self) -> None:
        payload = self._payload()
        payload["shared"]["source_refs"] = {}
        assert self._decode_code(payload) == "invalid_type"

        payload = self._payload()
        payload["shared"]["source_refs"] = []
        payload["shared"]["visible_history_message_ids"] = "m-1"
        assert self._decode_code(payload) == "invalid_type"

    def test_source_wire_shape_and_kind_validated(self) -> None:
        payload = self._payload()
        payload["shared"]["source_refs"] = ["not-a-source"]
        assert self._decode_code(payload) == "invalid_type"

        payload = self._payload()
        payload["shared"]["source_refs"][0]["source_kind"] = 1
        assert self._decode_code(payload) == "invalid_type"

        payload = self._payload()
        payload["shared"]["source_refs"][0]["source_kind"] = "bogus"
        assert self._decode_code(payload) == "invalid_tag"

    def test_tampered_input_digest_rejected(self) -> None:
        payload = self._payload()
        payload["shared"]["text"] = "被篡改的共同输入"
        assert self._decode_code(payload) == "digest_mismatch"


class TestLaneLookupValidation:
    def test_accept_request_rejects_foreign_lane(self) -> None:
        group = _group()
        other = _group(group_command_id="cmp-2")
        with pytest.raises(AgentValidationError) as exc:
            build_lane_accept_request(
                group,
                other.lanes[0],
                expected_revision=0,
                node_id="root",
                workflow_id="wf",
                source_display_name="用户消息",
            )
        assert exc.value.code == "lane_not_in_group"

    def test_connection_check_matches_second_lane_and_rejects_unknown(self) -> None:
        group = _group((_selection("conn-a"), _selection("conn-b")))
        lane = group.lanes[1]
        assert require_lane_connection(group, lane.lane_ref, connection_id="conn-b", connection_revision=3) is lane
        with pytest.raises(AgentValidationError) as exc:
            require_lane_connection(group, "lane-unknown", connection_id="conn-a", connection_revision=3)
        assert exc.value.code == "unknown_lane"

    def test_retry_unknown_lane_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            plan_lane_retry(_group(), "lane-unknown", round_id="round-2")
        assert exc.value.code == "unknown_lane"


class TestObservationValidation:
    def test_observation_must_be_typed(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            build_comparison_view(_group(), (cast(Any, "not-an-observation"),))
        assert exc.value.code == "invalid_type"

    def test_unknown_display_state_rejected(self) -> None:
        group = _group()
        observation = ComparisonLaneObservation(lane_ref=group.lanes[0].lane_ref, display_state="bogus")
        with pytest.raises(AgentValidationError) as exc:
            build_comparison_view(group, (observation,))
        assert exc.value.code == "invalid_display_state"

    def test_non_bool_reliable_result_rejected(self) -> None:
        group = _group()
        observation = ComparisonLaneObservation(
            lane_ref=group.lanes[0].lane_ref,
            display_state="complete",
            reliable_result=cast(Any, "yes"),
        )
        with pytest.raises(AgentValidationError) as exc:
            build_comparison_view(group, (observation,))
        assert exc.value.code == "invalid_type"
        assert exc.value.field_path == "$.observations[0].reliable_result"

    def test_empty_note_rejected(self) -> None:
        group = _group()
        observation = ComparisonLaneObservation(lane_ref=group.lanes[0].lane_ref, note="")
        with pytest.raises(AgentValidationError) as exc:
            build_comparison_view(group, (observation,))
        assert exc.value.code == "invalid_text"
        assert exc.value.field_path == "$.observations[0].note"


class TestLaneReasonProjection:
    def test_missing_observation_is_not_started(self) -> None:
        group = _group()
        view = build_comparison_view(group, ())

        assert view.ended is False
        assert view.group_state == "queued"
        assert view.goal_coverage == "unknown"
        assert all(lane.reason == "not_started" for lane in view.lanes)
        assert view.not_started_count == len(group.lanes)

    def test_partial_and_withdrawn_reasons_preserved(self) -> None:
        group = _group()
        view = build_comparison_view(
            group,
            (
                ComparisonLaneObservation(
                    lane_ref=group.lanes[0].lane_ref, display_state="partial", reliable_result=True
                ),
                ComparisonLaneObservation(
                    lane_ref=group.lanes[1].lane_ref, display_state="withdrawn", reliable_result=False
                ),
            ),
        )

        reasons = {lane.lane_ref: lane.reason for lane in view.lanes}
        assert reasons[group.lanes[0].lane_ref] == "partial_result"
        assert reasons[group.lanes[1].lane_ref] == "withdrawn"
        assert (view.group_state, view.goal_coverage) == ("partial", "partial")


class TestDriveEdgeCases:
    """逐路推进的边界：无容量、空观察、等待检查点、非法观察。"""

    @pytest.mark.asyncio
    async def test_no_capacity_queues_without_starting(self) -> None:
        group = _group()

        async def _start(_lane: Any) -> str:
            raise AssertionError("无容量时不应启动任何路")

        async def _observe(_children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            raise AssertionError("无容量时不应观察")

        view = await drive_comparison_group(
            group,
            _start,
            _observe,
            active_total_executions=COMPARISON_TOTAL_SLOTS,
        )

        assert view.ended is False
        assert view.group_state == "queued"
        assert view.not_started_count == len(group.lanes)

    @pytest.mark.asyncio
    async def test_empty_observations_without_checkpoint_end_batch(self) -> None:
        group = _group()
        started: list[str] = []

        async def start(lane: Any) -> str:
            started.append(lane.lane_ref)
            return f"wf-{lane.lane_ref}"

        async def observe(_children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            return {}

        view = await drive_comparison_group(group, start, observe, idle_observation_limit=1)

        assert len(started) == 1  # 会话单总执行：仅第一路在途
        assert view.ended is False
        assert view.group_state == "running"

    @pytest.mark.asyncio
    async def test_wait_checkpoint_declined_ends_batch(self) -> None:
        group = _group()

        async def start(_lane: Any) -> str:
            return "wf"

        async def observe(_children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            return {}

        async def checkpoint() -> bool:
            return False

        view = await drive_comparison_group(
            group,
            start,
            observe,
            idle_observation_limit=1,
            wait_checkpoint=checkpoint,
        )

        assert view.ended is False

    @pytest.mark.asyncio
    async def test_wait_checkpoint_accepted_continues_to_completion(self) -> None:
        group = _group()
        calls = {"observe": 0}

        async def start(lane: Any) -> str:
            return f"wf-{lane.lane_ref}"

        async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            calls["observe"] += 1
            if calls["observe"] == 1:
                return {}
            return {
                lane_ref: ComparisonLaneObservation(lane_ref=lane_ref, display_state="complete", reliable_result=True)
                for lane_ref, _workflow_id in children
            }

        async def checkpoint() -> bool:
            return True

        view = await drive_comparison_group(
            group,
            start,
            observe,
            idle_observation_limit=1,
            wait_checkpoint=checkpoint,
        )

        assert (view.group_state, view.goal_coverage) == ("complete", "full")

    @pytest.mark.asyncio
    async def test_unknown_lane_in_observation_rejected(self) -> None:
        group = _group()

        async def start(_lane: Any) -> str:
            return "wf"

        async def observe(_children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            return {
                "lane-ghost": ComparisonLaneObservation(
                    lane_ref=group.lanes[0].lane_ref, display_state="complete", reliable_result=True
                )
            }

        with pytest.raises(AgentValidationError) as exc:
            await drive_comparison_group(group, start, observe)

        assert exc.value.code == "observation_unknown_lane"

    @pytest.mark.asyncio
    async def test_observation_identity_mismatch_rejected(self) -> None:
        group = _group()

        async def start(_lane: Any) -> str:
            return "wf"

        async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            lane_ref, _workflow_id = children[0]
            return {
                lane_ref: ComparisonLaneObservation(
                    lane_ref="lane-other", display_state="complete", reliable_result=True
                )
            }

        with pytest.raises(AgentValidationError) as exc:
            await drive_comparison_group(group, start, observe)

        assert exc.value.code == "observation_identity_mismatch"

    @pytest.mark.asyncio
    async def test_running_observation_keeps_lane_in_flight(self) -> None:
        group = _group()
        calls = {"observe": 0}

        async def start(_lane: Any) -> str:
            return "wf"

        async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, ComparisonLaneObservation]:
            calls["observe"] += 1
            if calls["observe"] == 1:
                lane_ref, _workflow_id = children[0]
                return {lane_ref: ComparisonLaneObservation(lane_ref=lane_ref, display_state="running")}
            return {
                lane_ref: ComparisonLaneObservation(lane_ref=lane_ref, display_state="complete", reliable_result=True)
                for lane_ref, _workflow_id in children
            }

        view = await drive_comparison_group(group, start, observe)

        assert (view.group_state, view.goal_coverage) == ("complete", "full")
