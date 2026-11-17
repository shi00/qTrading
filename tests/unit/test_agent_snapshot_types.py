"""core.agent_types §4.2/§4.3 读取合同类型契约测试（C-03-2a）。

覆盖：SessionReadQuery 游标/边界、SessionSummary/ExecutionSummary/MessagePageItem/
ArtifactPageItem/DecisionSummary 行投影 strict decode、MessagePage/ArtifactPage/
SessionRead、SnapshotIdentity/SourceReadSummary/OperationSummary/UsageSummary/
ReadMarkEntry/Observation、ResearchSnapshot 19 字段逐行映射、to_wire_dict 泛型
encode 往返、以及值集常量与 ORM CHECK 约束的漂移守护。
"""

from __future__ import annotations

import re
from typing import Any

import pytest
import sqlalchemy as sa

from core.agent_types import (
    ARTIFACT_KINDS,
    ASSET_KINDS,
    DISPLAY_STATES,
    EXECUTION_KINDS,
    GOAL_COVERAGES,
    GRANT_STATES,
    OPERATION_PHASES,
    SOURCE_CONSUMER_KINDS,
    AgentValidationError,
    ArtifactPage,
    ArtifactPageItem,
    DecisionSummary,
    EffectState,
    EventKind,
    ExecutionSummary,
    MessagePage,
    MessagePageItem,
    MessageRole,
    Observation,
    RangeLimitation,
    ReadMarkEntry,
    ReadState,
    ResearchSnapshot,
    SessionRead,
    SessionReadQuery,
    SessionSummary,
    SnapshotIdentity,
    SourceReadSummary,
    UsageSummary,
    VerificationState,
    decode_artifact_page,
    decode_artifact_page_item,
    decode_decision_summary,
    decode_execution_summary,
    decode_json_doc,
    decode_message_page,
    decode_message_page_item,
    decode_observation,
    decode_operation_summary,
    decode_read_mark_entry,
    decode_research_snapshot,
    decode_session_read,
    decode_session_read_query,
    decode_session_summary,
    decode_snapshot_identity,
    decode_source_read_summary,
    decode_usage_summary,
    json_doc,
    to_wire_dict,
    validate_session_read_query,
)


# ---------------------------------------------------------------------------
# 测试夹具：wire dict 构造器（全键必填基线 + 单键覆盖）
# ---------------------------------------------------------------------------


def _ts(second: int = 0) -> str:
    return f"2026-11-12T08:00:{second:02d}.000000Z"


def _reject(
    decode: Any,
    value: object,
    code: str,
    field_path: str,
) -> None:
    """强断言 decode 失败的错误码与字段路径（weak-assertion 护栏；基路径统一 ``$``）。"""

    with pytest.raises(AgentValidationError) as exc_info:
        decode(value, "$")
    assert exc_info.value.code == code
    assert exc_info.value.field_path == field_path


def _query_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": "session_alpha",
        "execution_id": None,
        "round_id": None,
        "draft_revision": None,
        "message_cursor": None,
        "artifact_cursor": None,
        "limit": 50,
    }
    base.update(over)
    return base


def _summary_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_scope": "user:local",
        "session_id": "session_alpha",
        "title": "研究会话",
        "title_user_set": False,
        "pinned": False,
        "archived": False,
        "draft_revision": 1,
        "event_seq": 3,
        "created_at": _ts(0),
        "updated_at": _ts(1),
        "revision": 1,
        "draft_transfer_ref": None,
        "causal_head_ref": None,
        "source_module": "app://workbench",
        "origin_ref": None,
        "parent_session_id": None,
        "branch_metadata": None,
        "cleaned_at": None,
    }
    base.update(over)
    return base


def _execution_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_scope": "user:local",
        "session_id": "session_alpha",
        "execution_id": "execution_root_1",
        "execution_kind": "root",
        "node_id": "node_root",
        "display_state": "running",
        "goal_coverage": "unknown",
        "owner_epoch": 1,
        "dispatch_closed": False,
        "deletion_fenced": False,
        "model_attempt_count": 2,
        "tool_call_count": 5,
        "active_ms": 1500,
        "event_seq": 7,
        "created_at": _ts(0),
        "updated_at": _ts(2),
        "revision": 3,
        "source_module": "app://workbench",
        "business_request_id": "req-001",
        "intent_digest": "a" * 64,
        "parent_execution_id": None,
        "comparison_group_ref": None,
        "comparison_lane_ref": None,
        "current_round_id": "round_1",
        "last_verified_at": _ts(2),
        "ended_at": None,
        "unknown_refs": None,
    }
    base.update(over)
    return base


def _message_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "message_id": "message_m1",
        "session_id": "session_alpha",
        "event_id": "event_1",
        "session_seq": 1,
        "event_kind": "public_message_saved",
        "payload_kind": "public_message",
        "subject_id": "message_m1",
        "subject_revision": 1,
        "occurred_at": _ts(0),
        "committed_at": _ts(1),
        "execution_id": None,
        "round_id": None,
        "node_id": None,
        "execution_seq": None,
        "input_revision": None,
        "role": "user",
        "role_id": None,
        "role_version": None,
        "body_text": "你好",
        "body_ref": None,
        "payload": None,
        "native_fact_ref": None,
        "tool_call_id": None,
    }
    base.update(over)
    return base


def _artifact_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "artifact_id": "artifact_a1",
        "artifact_version": 1,
        "session_id": "session_alpha",
        "execution_id": "execution_root_1",
        "round_id": "round_1",
        "artifact_kind": "explanation",
        "input_revision": 1,
        "actual_input_digest": "b" * 64,
        "actual_model_ref": "qtrading:bound/deepseek-v3",
        "result_digest": "c" * 64,
        "source_available_state": "available",
        "created_at": _ts(0),
        "updated_at": _ts(2),
        "revision": 1,
        "summary": "分析结论",
        "body_text": None,
        "body_ref": "body_ref_a1",
        "result_payload": None,
        "published_at": _ts(2),
        "cleaned_at": None,
    }
    base.update(over)
    return base


def _decision_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "decision_id": "decision_d1",
        "session_id": "session_alpha",
        "execution_id": "execution_root_1",
        "round_id": "round_1",
        "node_id": "node_root",
        "decision_kind": "tool_grant",
        "presentation_batch_seq": 1,
        "created_at": _ts(0),
        "updated_at": _ts(1),
        "revision": 1,
        "tool_call_id": "call_1",
        "question_payload": None,
        "answer_schema": None,
        "parameter_digest": "d" * 64,
        "source_scope_digest": None,
        "service_ref": None,
        "tool_ref": None,
        "tool_schema_version": None,
        "expires_at": None,
        "answer_consumed_at": None,
        "answer_command_id": None,
        "grant_id": "grant_1",
        "grant_revision": 0,
        "grant_state": "granted",
        "dispatch_operation_id": None,
        "data_consent_payload": None,
    }
    base.update(over)
    return base


def _identity_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "user_scope": "user:local",
        "session_id": "session_alpha",
        "execution_id": "execution_root_1",
        "node_id": "node_root",
        "round_id": "round_1",
        "input_revision": 1,
        "workflow_id": "workflow_w1",
        "run_epoch": 1,
    }
    base.update(over)
    return base


def _source_read_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "link_id": "link_l1",
        "source_id": "source_s1",
        "source_version": "v1",
        "source_kind": "user_message",
        "display_name": "daily_quotes.csv",
        "digest": "e" * 64,
        "read_state": "ready",
        "verification_state": "program_verified",
        "consumer_kind": "execution",
        "consumer_id": "execution_root_1",
        "consumer_version": "1",
        "relation_kind": "origin",
        "disabled_inherited": False,
        "tombstoned": False,
        "available_at": _ts(0),
        "disabled_at": None,
        "locator_payload": None,
        "claim_ref": None,
    }
    base.update(over)
    return base


def _operation_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "operation_id": "operation_o1",
        "phase": "result_saved",
        "effect_state": "confirmed",
        "io_state": "succeeded",
        "owner_epoch": 1,
        "run_epoch": 1,
        "active_ms": 200,
        "elapsed_ms": 250,
        "session_id": "session_alpha",
        "execution_id": "execution_root_1",
        "round_id": "round_1",
        "tool_call_id": "call_1",
        "grant_id": "grant_1",
        "grant_revision": 1,
        "result_ref": "result_ref_o1",
        "error_code": None,
        "error_category": None,
        "deadline_at": None,
        "dispatched_at": _ts(1),
        "exited_at": _ts(2),
        "verified_at": _ts(2),
        "artifact_id": None,
        "artifact_version": None,
    }
    base.update(over)
    return base


def _usage_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "model_attempt_count": 2,
        "tool_call_count": 5,
        "active_ms": 1500,
        "cost_state": "unknown",
    }
    base.update(over)
    return base


def _read_mark_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "marker_id": "marker_1",
        "marker_kind": "session",
        "target_id": "session_alpha",
        "last_seq": 3,
        "batch_seq": 2,
        "postponed_through_seq": 1,
        "session_id": "session_alpha",
        "read_targets": ["message_m1"],
    }
    base.update(over)
    return base


def _snapshot_wire(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "identity": _identity_wire(),
        "revision": 3,
        "execution_watermark": 7,
        "session_watermark": 9,
        "updated_at": _ts(2),
        "execution": _execution_wire(),
        "nodes": [],
        "frozen_scope": None,
        "actual_input": None,
        "messages_page": {"items": [_message_wire()], "has_more": False, "next_cursor": None},
        "artifacts_page": {"items": [_artifact_wire()], "has_more": False, "next_cursor": None},
        "source_reads": [_source_read_wire()],
        "decisions": [_decision_wire()],
        "operation_summaries": [_operation_wire()],
        "usage": _usage_wire(),
        "goal_coverage": "unknown",
        "limitations": [],
        "read_marks": [_read_mark_wire()],
        "observation": {"reliable": True, "last_verified_at": _ts(2)},
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# SessionReadQuery（§4.2 L427：limit 1—50 / 草稿修订 / 游标语法）
# ---------------------------------------------------------------------------


def test_session_read_query_decode_full() -> None:
    query = decode_session_read_query(_query_wire(), "$")
    assert query.session_id == "session_alpha"
    assert query.execution_id is None
    assert query.round_id is None
    assert query.draft_revision is None
    assert query.message_cursor is None
    assert query.artifact_cursor is None
    assert query.limit == 50


def test_session_read_query_constructor_defaults() -> None:
    query = SessionReadQuery(session_id="session_alpha")
    assert query.limit == 50
    assert query.message_cursor is None
    validate_session_read_query(query)


def test_session_read_query_non_dict_rejected() -> None:
    _reject(decode_session_read_query, ["x"], "invalid_type", "$")


def test_session_read_query_missing_and_unknown_keys() -> None:
    wire = _query_wire()
    del wire["limit"]
    _reject(decode_session_read_query, wire, "missing_field", "$.limit")
    _reject(decode_session_read_query, {**_query_wire(), "latest": True}, "unknown_field", "$.latest")


@pytest.mark.parametrize("bad_limit", [0, -1, 51, True, "50", None])
def test_session_read_query_limit_rejected(bad_limit: object) -> None:
    wire = _query_wire(limit=bad_limit)
    with pytest.raises(AgentValidationError) as exc_info:
        decode_session_read_query(wire, "$")
    assert exc_info.value.code in {"invalid_type", "invalid_range"}
    assert exc_info.value.field_path == "$.limit"


@pytest.mark.parametrize("bad_draft_revision", [0, -1, True, "1"])
def test_session_read_query_draft_revision_rejected(bad_draft_revision: object) -> None:
    wire = _query_wire(draft_revision=bad_draft_revision)
    with pytest.raises(AgentValidationError) as exc_info:
        decode_session_read_query(wire, "$")
    assert exc_info.value.code in {"invalid_type", "invalid_range"}
    assert exc_info.value.field_path == "$.draft_revision"


@pytest.mark.parametrize(
    "bad_cursor",
    ["", "x:1", "m:", "m:01", "m:1.5", "m:1 ", "session_seq"],
)
def test_session_read_query_message_cursor_rejected(bad_cursor: str) -> None:
    _reject(decode_session_read_query, _query_wire(message_cursor=bad_cursor), "invalid_cursor", "$.message_cursor")


@pytest.mark.parametrize("cursor", ["m:0", "m:12"])
def test_session_read_query_message_cursor_accepted(cursor: str) -> None:
    query = decode_session_read_query(_query_wire(message_cursor=cursor), "$")
    validate_session_read_query(query)
    assert query.message_cursor == cursor


@pytest.mark.parametrize(
    "bad_cursor",
    [
        "a:2026-11-12T08:00:00.000000Z",
        "a:2026-11-12T08:00:00.000000Z:artifact_a1",
        "a:2026-11-12T08:00:00.000000Z::1",
        "a:2026-11-12T08:00:00.000000Z:artifact_a1:0",
        "a:2026-11-12T08:00:00.000000Z:artifact_a1:01",
        "a:2026-11-12T08:00:00Z:artifact_a1:1",
        "a:not-a-time:artifact_a1:1",
    ],
)
def test_session_read_query_artifact_cursor_rejected(bad_cursor: str) -> None:
    _reject(decode_session_read_query, _query_wire(artifact_cursor=bad_cursor), "invalid_cursor", "$.artifact_cursor")


def test_session_read_query_artifact_cursor_accepted() -> None:
    cursor = "a:2026-11-12T08:00:00.000000Z:artifact_a1:3"
    query = decode_session_read_query(_query_wire(artifact_cursor=cursor), "$")
    validate_session_read_query(query)
    assert query.artifact_cursor == cursor


# ---------------------------------------------------------------------------
# SessionSummary（agent_session 行投影）
# ---------------------------------------------------------------------------


def test_session_summary_decode_roundtrip() -> None:
    wire = _summary_wire(branch_metadata=json_doc({"branch": True}))
    summary = decode_session_summary(decode_json_doc(json_doc(wire)), "$")
    assert summary.title == "研究会话"
    assert summary.draft_revision == 1
    assert summary.branch_metadata == '{"branch":true}'
    assert to_wire_dict(summary) == wire
    again = decode_session_summary(decode_json_doc(json_doc(to_wire_dict(summary))), "$")
    assert again == summary


def test_session_summary_missing_and_unknown_keys() -> None:
    wire = _summary_wire()
    del wire["event_seq"]
    _reject(decode_session_summary, wire, "missing_field", "$.event_seq")
    _reject(decode_session_summary, {**_summary_wire(), "draft_payload": "{}"}, "unknown_field", "$.draft_payload")


def test_session_summary_value_guards() -> None:
    _reject(decode_session_summary, _summary_wire(draft_revision=0), "invalid_range", "$.draft_revision")
    _reject(decode_session_summary, _summary_wire(event_seq=-1), "invalid_range", "$.event_seq")
    _reject(decode_session_summary, _summary_wire(revision=0), "invalid_range", "$.revision")
    _reject(
        decode_session_summary,
        _summary_wire(branch_metadata="not-json"),
        "invalid_json",
        "$.branch_metadata",
    )
    _reject(decode_session_summary, _summary_wire(title=42), "invalid_type", "$.title")


# ---------------------------------------------------------------------------
# ExecutionSummary（agent_execution 行投影：display_state 15 值/goal_coverage 4 值）
# ---------------------------------------------------------------------------


def test_execution_summary_decode_roundtrip() -> None:
    wire = _execution_wire(unknown_refs=json_doc(["unknown_obj_1"]))
    summary = decode_execution_summary(decode_json_doc(json_doc(wire)), "$")
    assert summary.display_state == "running"
    assert summary.model_attempt_count == 2
    assert summary.unknown_refs == '["unknown_obj_1"]'
    assert to_wire_dict(summary) == wire
    again = decode_execution_summary(decode_json_doc(json_doc(to_wire_dict(summary))), "$")
    assert again == summary


@pytest.mark.parametrize(
    "state",
    sorted(DISPLAY_STATES),
)
def test_execution_summary_display_states_accepted(state: str) -> None:
    summary = decode_execution_summary(_execution_wire(display_state=state), "$")
    assert summary.display_state == state


def test_execution_summary_display_state_rejected() -> None:
    _reject(
        decode_execution_summary, _execution_wire(display_state="success"), "invalid_display_state", "$.display_state"
    )


@pytest.mark.parametrize("coverage", sorted(GOAL_COVERAGES))
def test_execution_summary_goal_coverage_accepted(coverage: str) -> None:
    summary = decode_execution_summary(_execution_wire(goal_coverage=coverage), "$")
    assert summary.goal_coverage == coverage


def test_execution_summary_goal_coverage_rejected() -> None:
    _reject(
        decode_execution_summary,
        _execution_wire(goal_coverage="partial_full"),
        "invalid_goal_coverage",
        "$.goal_coverage",
    )


@pytest.mark.parametrize("field", ["model_attempt_count", "tool_call_count", "active_ms", "event_seq"])
def test_execution_summary_counter_negative_rejected(field: str) -> None:
    _reject(decode_execution_summary, _execution_wire(**{field: -1}), "invalid_range", f"$.{field}")


def test_execution_summary_intent_digest_rejected() -> None:
    _reject(decode_execution_summary, _execution_wire(intent_digest="zz"), "invalid_digest", "$.intent_digest")


def test_execution_summary_missing_key() -> None:
    wire = _execution_wire()
    del wire["node_id"]
    _reject(decode_execution_summary, wire, "missing_field", "$.node_id")


# ---------------------------------------------------------------------------
# MessagePageItem（agent_message 首屏投影）
# ---------------------------------------------------------------------------


def test_message_page_item_decode_roundtrip() -> None:
    wire = _message_wire(payload=json_doc({"text": "hi"}))
    item = decode_message_page_item(decode_json_doc(json_doc(wire)), "$")
    assert item.event_kind == EventKind.PUBLIC_MESSAGE_SAVED
    assert item.role == MessageRole.USER
    assert item.body_text == "你好"
    assert item.payload == '{"text":"hi"}'
    assert to_wire_dict(item) == wire
    again = decode_message_page_item(decode_json_doc(json_doc(to_wire_dict(item))), "$")
    assert again == item


@pytest.mark.parametrize("kind", ["message_saved", "PUBLIC_MESSAGE_SAVED", 1, None])
def test_message_page_item_event_kind_rejected(kind: object) -> None:
    _reject(decode_message_page_item, _message_wire(event_kind=kind), "invalid_event_kind", "$.event_kind")


@pytest.mark.parametrize("role", ["system", "human", 1])
def test_message_page_item_role_rejected(role: object) -> None:
    _reject(decode_message_page_item, _message_wire(role=role), "invalid_role", "$.role")


def test_message_page_item_execution_pair_rules() -> None:
    _reject(
        decode_message_page_item,
        _message_wire(execution_id="execution_root_1", round_id=None, execution_seq=1),
        "identity_mismatch",
        "$.round_id",
    )
    _reject(
        decode_message_page_item,
        _message_wire(execution_id=None, round_id="round_1", execution_seq=None),
        "identity_mismatch",
        "$.round_id",
    )
    _reject(
        decode_message_page_item,
        _message_wire(execution_id="execution_root_1", round_id="round_1", execution_seq=None),
        "identity_mismatch",
        "$.execution_seq",
    )


def test_message_page_item_body_exclusive_rejected() -> None:
    _reject(
        decode_message_page_item,
        _message_wire(body_text="x", body_ref="body_ref_1"),
        "body_conflict",
        "$.body_ref",
    )


def test_message_page_item_seq_guards() -> None:
    _reject(decode_message_page_item, _message_wire(session_seq=0), "invalid_range", "$.session_seq")
    _reject(decode_message_page_item, _message_wire(subject_revision=0), "invalid_range", "$.subject_revision")


def test_message_page_item_missing_key() -> None:
    wire = _message_wire()
    del wire["committed_at"]
    _reject(decode_message_page_item, wire, "missing_field", "$.committed_at")


# ---------------------------------------------------------------------------
# ArtifactPageItem（agent_artifact 首屏投影）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", sorted(ARTIFACT_KINDS))
def test_artifact_page_item_kinds_accepted(kind: str) -> None:
    item = decode_artifact_page_item(_artifact_wire(artifact_kind=kind), "$")
    assert item.artifact_kind == kind


def test_artifact_page_item_decode_roundtrip() -> None:
    wire = _artifact_wire(result_payload=json_doc({"title": "结论"}))
    item = decode_artifact_page_item(decode_json_doc(json_doc(wire)), "$")
    assert item.artifact_kind == "explanation"
    assert item.result_payload == '{"title":"结论"}'
    assert to_wire_dict(item) == wire
    again = decode_artifact_page_item(decode_json_doc(json_doc(to_wire_dict(item))), "$")
    assert again == item


def test_artifact_page_item_value_guards() -> None:
    _reject(decode_artifact_page_item, _artifact_wire(artifact_version=0), "invalid_range", "$.artifact_version")
    _reject(decode_artifact_page_item, _artifact_wire(input_revision=0), "invalid_range", "$.input_revision")
    _reject(
        decode_artifact_page_item, _artifact_wire(artifact_kind="report"), "invalid_artifact_kind", "$.artifact_kind"
    )
    _reject(
        decode_artifact_page_item, _artifact_wire(actual_input_digest="xy"), "invalid_digest", "$.actual_input_digest"
    )
    _reject(
        decode_artifact_page_item,
        _artifact_wire(body_text="x", body_ref="body_ref_1"),
        "body_conflict",
        "$.body_ref",
    )


def test_artifact_page_item_missing_key() -> None:
    wire = _artifact_wire()
    del wire["result_digest"]
    _reject(decode_artifact_page_item, wire, "missing_field", "$.result_digest")


# ---------------------------------------------------------------------------
# DecisionSummary（agent_decision 行投影；载荷 CanonicalJson 文本承载）
# ---------------------------------------------------------------------------


def test_decision_summary_decode_roundtrip() -> None:
    wire = _decision_wire(
        question_payload=json_doc({"prompt": "允许读取该数据源？"}),
        answer_schema=json_doc({"type": "object"}),
    )
    decision = decode_decision_summary(decode_json_doc(json_doc(wire)), "$")
    assert decision.decision_kind == "tool_grant"
    assert decision.grant_state == "granted"
    assert decision.question_payload == '{"prompt":"允许读取该数据源？"}'
    assert to_wire_dict(decision) == wire
    again = decode_decision_summary(decode_json_doc(json_doc(to_wire_dict(decision))), "$")
    assert again == decision


def test_decision_summary_value_guards() -> None:
    _reject(decode_decision_summary, _decision_wire(grant_state="approved"), "invalid_grant_state", "$.grant_state")
    _reject(decode_decision_summary, _decision_wire(grant_revision=-1), "invalid_range", "$.grant_revision")
    _reject(decode_decision_summary, _decision_wire(question_payload="not-json"), "invalid_json", "$.question_payload")
    _reject(decode_decision_summary, _decision_wire(expires_at="2026-11-12"), "invalid_timestamp", "$.expires_at")


def test_decision_summary_missing_key() -> None:
    wire = _decision_wire()
    del wire["presentation_batch_seq"]
    _reject(decode_decision_summary, wire, "missing_field", "$.presentation_batch_seq")


# ---------------------------------------------------------------------------
# MessagePage / ArtifactPage / SessionRead（§4.2 五要素 + 分页上界）
# ---------------------------------------------------------------------------


def test_message_page_decode() -> None:
    page = decode_message_page(
        {"items": [_message_wire()], "has_more": True, "next_cursor": "m:1"},
        "$",
    )
    assert isinstance(page, MessagePage)
    assert len(page.items) == 1
    assert page.has_more is True
    assert page.next_cursor == "m:1"


def test_message_page_has_more_requires_cursor() -> None:
    _reject(
        decode_message_page, {"items": [], "has_more": True, "next_cursor": None}, "invalid_cursor", "$.next_cursor"
    )


def test_artifact_page_decode() -> None:
    page = decode_artifact_page(
        {"items": [_artifact_wire()], "has_more": False, "next_cursor": None},
        "$",
    )
    assert isinstance(page, ArtifactPage)
    assert page.has_more is False


def test_session_read_decode_roundtrip() -> None:
    wire = {
        "summary": _summary_wire(),
        "executions": [_execution_wire()],
        "messages": {"items": [_message_wire()], "has_more": True, "next_cursor": "m:1"},
        "artifacts": {"items": [_artifact_wire()], "has_more": False, "next_cursor": None},
        "decisions": [_decision_wire()],
    }
    read = decode_session_read(decode_json_doc(json_doc(wire)), "$")
    assert isinstance(read, SessionRead)
    assert read.summary.session_id == "session_alpha"
    assert len(read.executions) == 1
    assert read.messages.has_more is True
    assert read.messages.next_cursor == "m:1"
    assert len(read.decisions) == 1
    assert to_wire_dict(read) == wire
    again = decode_session_read(decode_json_doc(json_doc(to_wire_dict(read))), "$")
    assert again == read


def test_session_read_missing_key() -> None:
    wire = {
        "summary": _summary_wire(),
        "executions": [],
        "messages": {"items": [], "has_more": False, "next_cursor": None},
        "artifacts": {"items": [], "has_more": False, "next_cursor": None},
    }
    _reject(decode_session_read, wire, "missing_field", "$.decisions")


# ---------------------------------------------------------------------------
# SnapshotIdentity / SourceReadSummary / OperationSummary / UsageSummary
# ---------------------------------------------------------------------------


def test_snapshot_identity_decode_roundtrip() -> None:
    wire = _identity_wire()
    identity = decode_snapshot_identity(decode_json_doc(json_doc(wire)), "$")
    assert identity.execution_id == "execution_root_1"
    assert identity.workflow_id == "workflow_w1"
    assert to_wire_dict(identity) == wire
    again = decode_snapshot_identity(decode_json_doc(json_doc(to_wire_dict(identity))), "$")
    assert again == identity


def test_snapshot_identity_queued_execution_without_round() -> None:
    identity = decode_snapshot_identity(
        _identity_wire(round_id=None, input_revision=None, workflow_id=None, run_epoch=None), "$"
    )
    assert identity.round_id is None
    assert identity.workflow_id is None


def test_snapshot_identity_round_coupling_rejected() -> None:
    _reject(
        decode_snapshot_identity,
        _identity_wire(round_id=None, input_revision=None, workflow_id="workflow_w1"),
        "identity_mismatch",
        "$.round_id",
    )
    _reject(
        decode_snapshot_identity,
        _identity_wire(round_id=None, workflow_id=None, run_epoch=1),
        "identity_mismatch",
        "$.round_id",
    )
    _reject(
        decode_snapshot_identity,
        _identity_wire(input_revision=None),
        "identity_mismatch",
        "$.input_revision",
    )


def test_snapshot_identity_missing_key() -> None:
    wire = _identity_wire()
    del wire["node_id"]
    _reject(decode_snapshot_identity, wire, "missing_field", "$.node_id")


def test_source_read_summary_decode_roundtrip() -> None:
    wire = _source_read_wire()
    summary = decode_source_read_summary(decode_json_doc(json_doc(wire)), "$")
    assert summary.read_state == ReadState.READY
    assert summary.verification_state == VerificationState.PROGRAM_VERIFIED
    assert to_wire_dict(summary) == wire
    again = decode_source_read_summary(decode_json_doc(json_doc(to_wire_dict(summary))), "$")
    assert again == summary


def test_source_read_summary_value_guards() -> None:
    _reject(decode_source_read_summary, _source_read_wire(source_kind="file"), "invalid_source_kind", "$.source_kind")
    # asset_kind 的 "source" 不是 SourceKind，禁止冒充通用来源（检视 D30）。
    _reject(decode_source_read_summary, _source_read_wire(source_kind="source"), "invalid_source_kind", "$.source_kind")
    assert decode_source_read_summary(_source_read_wire(source_kind="business_snapshot"), "$").source_kind == (
        "business_snapshot"
    )
    # 已清理资产的类别不可知：显式 None（R21），不猜默认值
    assert decode_source_read_summary(_source_read_wire(source_kind=None), "$").source_kind is None
    _reject(decode_source_read_summary, _source_read_wire(read_state="done"), "invalid_read_state", "$.read_state")
    _reject(
        decode_source_read_summary,
        _source_read_wire(verification_state="verified"),
        "invalid_verification_state",
        "$.verification_state",
    )
    _reject(
        decode_source_read_summary,
        _source_read_wire(consumer_kind="workflow"),
        "invalid_consumer_kind",
        "$.consumer_kind",
    )


def test_operation_summary_decode_roundtrip() -> None:
    wire = _operation_wire()
    summary = decode_operation_summary(decode_json_doc(json_doc(wire)), "$")
    assert summary.phase == "result_saved"
    assert summary.effect_state == EffectState.CONFIRMED
    assert summary.result_ref == "result_ref_o1"
    assert to_wire_dict(summary) == wire
    again = decode_operation_summary(decode_json_doc(json_doc(to_wire_dict(summary))), "$")
    assert again == summary


@pytest.mark.parametrize("phase", sorted(OPERATION_PHASES))
def test_operation_summary_phases_accepted(phase: str) -> None:
    summary = decode_operation_summary(_operation_wire(phase=phase), "$")
    assert summary.phase == phase


def test_operation_summary_value_guards() -> None:
    _reject(decode_operation_summary, _operation_wire(phase="running"), "invalid_phase", "$.phase")
    _reject(decode_operation_summary, _operation_wire(effect_state="done"), "invalid_effect_state", "$.effect_state")
    _reject(
        decode_operation_summary,
        _operation_wire(artifact_id="artifact_a1", artifact_version=None),
        "identity_mismatch",
        "$.artifact_version",
    )
    _reject(decode_operation_summary, _operation_wire(active_ms=-1), "invalid_range", "$.active_ms")


def test_usage_summary_decode_and_fixed_cost_state() -> None:
    usage = decode_usage_summary(_usage_wire(), "$")
    assert usage.cost_state == "unknown"
    assert to_wire_dict(usage) == _usage_wire()
    _reject(decode_usage_summary, _usage_wire(cost_state="known"), "invalid_cost_state", "$.cost_state")
    _reject(decode_usage_summary, _usage_wire(model_attempt_count=-1), "invalid_range", "$.model_attempt_count")


# ---------------------------------------------------------------------------
# ReadMarkEntry / Observation
# ---------------------------------------------------------------------------


def test_read_mark_entry_decode_roundtrip() -> None:
    wire = _read_mark_wire(read_targets=["message_m1", "artifact_a1:1"])
    entry = decode_read_mark_entry(decode_json_doc(json_doc(wire)), "$")
    assert entry.read_targets == ("message_m1", "artifact_a1:1")
    assert to_wire_dict(entry) == wire
    again = decode_read_mark_entry(decode_json_doc(json_doc(to_wire_dict(entry))), "$")
    assert again == entry


def test_read_mark_entry_value_guards() -> None:
    _reject(decode_read_mark_entry, _read_mark_wire(last_seq=-1), "invalid_range", "$.last_seq")
    _reject(decode_read_mark_entry, _read_mark_wire(read_targets=[1]), "invalid_identity", "$.read_targets[0]")


def test_read_mark_entry_read_targets_null_allowed() -> None:
    entry = decode_read_mark_entry(_read_mark_wire(read_targets=None), "$")
    assert entry.read_targets is None


def test_observation_decode_roundtrip() -> None:
    wire = {"reliable": False, "last_verified_at": None}
    observation = decode_observation(decode_json_doc(json_doc(wire)), "$")
    assert observation.reliable is False
    assert observation.last_verified_at is None
    assert to_wire_dict(observation) == wire
    _reject(decode_observation, {"reliable": 1, "last_verified_at": None}, "invalid_type", "$.reliable")


# ---------------------------------------------------------------------------
# ResearchSnapshot（§4.3 完整结构逐行映射）
# ---------------------------------------------------------------------------


def test_research_snapshot_decode_roundtrip() -> None:
    wire = _snapshot_wire(
        frozen_scope=json_doc({"purpose": "end_of_day_research"}),
        actual_input=json_doc({"prior_refs": ["artifact_a1:1"]}),
        nodes=[
            _execution_wire(
                execution_id="execution_leaf_1",
                execution_kind="leaf",
                parent_execution_id="execution_root_1",
                source_module=None,
                business_request_id=None,
                intent_digest=None,
            )
        ],
        limitations=[{"code": "data_missing", "message": "部分来源不可得"}],
    )
    snapshot = decode_research_snapshot(decode_json_doc(json_doc(wire)), "$")
    assert isinstance(snapshot, ResearchSnapshot)
    assert snapshot.execution_watermark == 7
    assert snapshot.session_watermark == 9
    assert len(snapshot.nodes) == 1
    assert snapshot.nodes[0].execution_kind == "leaf"
    assert snapshot.frozen_scope == '{"purpose":"end_of_day_research"}'
    assert snapshot.actual_input == '{"prior_refs":["artifact_a1:1"]}'
    assert snapshot.limitations == (RangeLimitation(code="data_missing", message="部分来源不可得"),)
    assert snapshot.usage.cost_state == "unknown"
    assert snapshot.observation.reliable is True
    assert to_wire_dict(snapshot) == wire
    again = decode_research_snapshot(decode_json_doc(json_doc(to_wire_dict(snapshot))), "$")
    assert again == snapshot


def test_research_snapshot_value_guards() -> None:
    _reject(decode_research_snapshot, _snapshot_wire(goal_coverage="total"), "invalid_goal_coverage", "$.goal_coverage")
    _reject(decode_research_snapshot, _snapshot_wire(revision=0), "invalid_range", "$.revision")
    _reject(decode_research_snapshot, _snapshot_wire(execution_watermark=-1), "invalid_range", "$.execution_watermark")
    _reject(decode_research_snapshot, _snapshot_wire(frozen_scope="not-json"), "invalid_json", "$.frozen_scope")
    _reject(decode_research_snapshot, _snapshot_wire(updated_at="2026-11-12"), "invalid_timestamp", "$.updated_at")


def test_research_snapshot_object_identity_guards() -> None:
    """identity 与对象图子行分叉必须拒绝（检视 R19：错对象身份不得静默接受）。"""

    # execution 行与 identity 不同对象（跨执行/跨会话/跨节点）。
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(execution_id="execution_other")),
        "identity_mismatch",
        "$.execution",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(session_id="session_other")),
        "identity_mismatch",
        "$.execution",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(node_id="node_other")),
        "identity_mismatch",
        "$.execution",
    )
    # identity 无 round（queued）时 execution 不得已归轮。
    _reject(
        decode_research_snapshot,
        _snapshot_wire(identity=_identity_wire(round_id=None, input_revision=None, workflow_id=None, run_epoch=None)),
        "identity_mismatch",
        "$.execution.current_round_id",
    )
    # 同源重复投影分叉（revision/执行水位/覆盖/更新时间）。
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(revision=4)),
        "identity_mismatch",
        "$.revision",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(event_seq=8)),
        "identity_mismatch",
        "$.execution_watermark",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(goal_coverage="partial")),
        "identity_mismatch",
        "$.goal_coverage",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(execution=_execution_wire(updated_at=_ts(3))),
        "identity_mismatch",
        "$.updated_at",
    )
    # 一层叶子必须挂在本执行下。
    _reject(
        decode_research_snapshot,
        _snapshot_wire(nodes=[_execution_wire(execution_kind="leaf", parent_execution_id="execution_other")]),
        "identity_mismatch",
        "$.nodes[0]",
    )
    # 消息/决定/操作子行必须属于该会话与执行（及选定轮）。
    _reject(
        decode_research_snapshot,
        _snapshot_wire(
            messages_page={"items": [_message_wire(session_id="session_other")], "has_more": False, "next_cursor": None}
        ),
        "identity_mismatch",
        "$.messages_page.items[0]",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(
            messages_page={
                "items": [_message_wire(execution_id="execution_other", round_id="round_1", execution_seq=1)],
                "has_more": False,
                "next_cursor": None,
            }
        ),
        "identity_mismatch",
        "$.messages_page.items[0]",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(
            messages_page={
                "items": [_message_wire(execution_id="execution_root_1", round_id="round_other", execution_seq=1)],
                "has_more": False,
                "next_cursor": None,
            }
        ),
        "identity_mismatch",
        "$.messages_page.items[0]",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(decisions=[_decision_wire(execution_id="execution_other")]),
        "identity_mismatch",
        "$.decisions[0]",
    )
    _reject(
        decode_research_snapshot,
        _snapshot_wire(operation_summaries=[_operation_wire(execution_id="execution_other")]),
        "identity_mismatch",
        "$.operation_summaries[0]",
    )


def test_research_snapshot_missing_and_unknown_keys() -> None:
    wire = _snapshot_wire()
    del wire["observation"]
    _reject(decode_research_snapshot, wire, "missing_field", "$.observation")
    _reject(decode_research_snapshot, {**_snapshot_wire(), "dependencies": []}, "unknown_field", "$.dependencies")


def test_research_snapshot_empty_pages_allowed() -> None:
    wire = _snapshot_wire(
        nodes=[],
        source_reads=[],
        decisions=[],
        operation_summaries=[],
        read_marks=[],
        messages_page={"items": [], "has_more": False, "next_cursor": None},
        artifacts_page={"items": [], "has_more": False, "next_cursor": None},
    )
    snapshot = decode_research_snapshot(decode_json_doc(json_doc(wire)), "$")
    assert snapshot.messages_page.items == ()
    assert snapshot.decisions == ()


# ---------------------------------------------------------------------------
# 值集常量 ↔ ORM CHECK 约束漂移守护（core 与 data/persistence/models.py 同步）
# ---------------------------------------------------------------------------


def _check_values(model: Any, constraint_name: str) -> set[str]:
    candidates = {constraint_name, f"ck_{model.__tablename__}_{constraint_name}"}
    for constraint in model.__table__.constraints:
        if isinstance(constraint, sa.CheckConstraint) and constraint.name in candidates:
            return set(re.findall(r"'([a-z0-9_]+)'", str(constraint.sqltext)))
    raise AssertionError(f"CHECK constraint not found: {model.__name__}.{constraint_name}")


def test_display_states_match_db_check() -> None:
    from data.persistence.models import AgentExecution

    assert _check_values(AgentExecution, "display_state") == DISPLAY_STATES
    assert len(DISPLAY_STATES) == 15


def test_goal_coverage_matches_db_check() -> None:
    from data.persistence.models import AgentExecution

    assert _check_values(AgentExecution, "goal_coverage") == GOAL_COVERAGES


def test_execution_kinds_match_db_check() -> None:
    from data.persistence.models import AgentExecution

    assert _check_values(AgentExecution, "kind") == EXECUTION_KINDS


def test_artifact_kinds_match_db_check() -> None:
    from data.persistence.models import AgentArtifact

    assert _check_values(AgentArtifact, "kind") == ARTIFACT_KINDS
    assert len(ARTIFACT_KINDS) == 8


def test_asset_kinds_match_db_check() -> None:
    from data.persistence.models import AgentAssetVersion

    assert _check_values(AgentAssetVersion, "kind") == ASSET_KINDS
    assert len(ASSET_KINDS) == 8


def test_source_consumer_kinds_match_db_check() -> None:
    from data.persistence.models import AgentSourceLink

    assert _check_values(AgentSourceLink, "consumer_kind") == SOURCE_CONSUMER_KINDS


def test_operation_phases_match_db_check() -> None:
    from data.persistence.models import AgentOperation

    assert _check_values(AgentOperation, "phase") == OPERATION_PHASES
    assert len(OPERATION_PHASES) == 6


def test_read_state_enum_matches_db_check() -> None:
    from data.persistence.models import AgentAssetVersion

    assert {e.value for e in ReadState} == _check_values(AgentAssetVersion, "read_state")


def test_verification_state_enum_matches_db_check() -> None:
    from data.persistence.models import AgentAssetVersion

    assert {e.value for e in VerificationState} == _check_values(AgentAssetVersion, "verification_state")


def test_effect_state_enum_matches_db_check() -> None:
    from data.persistence.models import AgentOperation

    assert {e.value for e in EffectState} == _check_values(AgentOperation, "effect_state")


def test_grant_states_engineering_freeze() -> None:
    """grant_state 为 C-02-3 工程冻结（granted/claimed/revoked），无 DB CHECK。"""

    assert {"granted", "claimed", "revoked"} == GRANT_STATES
