"""可靠事件统一包络合同测试（C-03-1，设计 §4.4/§9.6）。

覆盖：EventKind/MessageRole 枚举 wire 拼写、ResearchEvent 18 键 strict codec
（JSONB 重排键重读保同身份）、derive_event_identity 组合派生、执行事件身份
一致性（execution_seq/round/node 同存同缺）、validate_event_identity、
RecordEventRequest/EventFact/EventReadQuery/EventPage 基线约束。
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from typing import Any

import pytest

from core.agent_types import (
    AgentValidationError,
    EventKind,
    EventPage,
    EventReadQuery,
    EventFact,
    MessageRole,
    RecordEventRequest,
    ResearchEvent,
    decode_json_doc,
    decode_research_event,
    derive_event_identity,
    encode_research_event,
    json_doc,
    validate_event_identity,
    validate_research_event,
)

_USER = "synthetic-user"
_SESSION = "s-evt"
_EXEC = "exec-evt"
_ROUND = "round-evt"
_NODE = "root"


def _event_kwargs(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "schema_version": 1,
        "event_id": derive_event_identity(
            execution_id=_EXEC, round_id=_ROUND, node_id=_NODE, native_event_id="native-1"
        ),
        "user_scope": _USER,
        "session_id": _SESSION,
        "execution_id": _EXEC,
        "node_id": _NODE,
        "round_id": _ROUND,
        "input_revision": 1,
        "session_seq": 1,
        "execution_seq": 1,
        "occurred_at": "2026-06-01T12:00:00.000000Z",
        "committed_at": "2026-06-01T12:00:00.000001Z",
        "event_kind": EventKind.PUBLIC_MESSAGE_SAVED,
        "subject_id": "m-1",
        "subject_revision": 1,
        "payload_kind": "public_message_saved.v1",
        "payload_canonical_json": json_doc({"message_id": "m-1", "role": "assistant"}),
        "native_fact_ref": None,
    }
    values.update(overrides)
    return values


def _event(**overrides: Any) -> ResearchEvent:
    return ResearchEvent(**_event_kwargs(**overrides))


def _session_event(**overrides: Any) -> ResearchEvent:
    values = _event_kwargs(
        event_id=derive_event_identity(execution_id=None, round_id=None, node_id=None, native_event_id="native-2"),
        execution_id=None,
        node_id=None,
        round_id=None,
        input_revision=None,
        execution_seq=None,
        event_kind=EventKind.SESSION_CHANGED,
        payload_kind="session_changed.v1",
        subject_id=_SESSION,
    )
    values.update(overrides)
    return ResearchEvent(**values)


class TestEventKinds:
    def test_wire_spelling_fixed(self) -> None:
        assert [k.value for k in EventKind] == [
            "session_changed",
            "draft_saved",
            "execution_accepted",
            "input_bound",
            "execution_changed",
            "node_changed",
            "public_message_saved",
            "source_read_saved",
            "source_disabled",
            "tool_call_recorded",
            "tool_result_saved",
            "decision_required",
            "command_changed",
            "artifact_saved",
            "usage_changed",
            "read_mark_saved",
            "cleanup_changed",
        ]

    def test_message_role_spelling(self) -> None:
        assert [r.value for r in MessageRole] == ["user", "assistant", "tool", "public_status"]


class TestEventIdentity:
    def test_execution_event_identity_combination(self) -> None:
        first = derive_event_identity(execution_id=_EXEC, round_id=_ROUND, node_id=_NODE, native_event_id="n1")
        second = derive_event_identity(execution_id=_EXEC, round_id=_ROUND, node_id=_NODE, native_event_id="n1")
        assert first == second
        # 同一原生 ID 在不同轮 → 不同身份（原生 ID 不跨 round 当全局 ID）
        other_round = derive_event_identity(execution_id=_EXEC, round_id="round-2", node_id=_NODE, native_event_id="n1")
        assert first != other_round

    def test_session_event_identity_empty_segments(self) -> None:
        session_event = derive_event_identity(execution_id=None, round_id=None, node_id=None, native_event_id="n1")
        exec_event = derive_event_identity(execution_id=_EXEC, round_id=_ROUND, node_id=_NODE, native_event_id="n1")
        assert session_event != exec_event

    def test_empty_native_event_id_rejected(self) -> None:
        _reject(
            lambda: derive_event_identity(execution_id=None, round_id=None, node_id=None, native_event_id=""),
            code="invalid_identity",
        )


class TestResearchEventCodec:
    def test_encode_decode_roundtrip(self) -> None:
        wire = encode_research_event(_event())
        assert len(wire) == 18
        decoded = decode_research_event(wire, "$")
        assert json_doc(encode_research_event(decoded)) == json_doc(wire)
        assert decoded.event_kind is EventKind.PUBLIC_MESSAGE_SAVED

    def test_session_event_roundtrip(self) -> None:
        wire = encode_research_event(_session_event())
        decoded = decode_research_event(wire, "$")
        assert decoded.execution_id is None
        assert decoded.execution_seq is None
        assert json_doc(encode_research_event(decoded)) == json_doc(wire)

    def test_jsonb_key_reordering_preserves_identity(self) -> None:
        wire = encode_research_event(_event())
        reordered = dict(reversed(list(wire.items())))
        assert json_doc(reordered) == json_doc(wire)
        decoded = decode_research_event(reordered, "$")
        assert decoded.event_id == _event().event_id

    def test_strictness(self) -> None:
        wire = encode_research_event(_event())
        _reject(lambda: decode_research_event({**wire, "extra": 1}, "$"), code="unknown_field")
        stripped = {k: v for k, v in wire.items() if k != "subject_id"}
        _reject(lambda: decode_research_event(stripped, "$"), code="missing_field")
        _reject(lambda: decode_research_event({**wire, "schema_version": 2}, "$"), code="invalid_schema_version")
        _reject(lambda: decode_research_event({**wire, "event_kind": "text_delta"}, "$"), code="invalid_event_kind")
        _reject(lambda: decode_research_event({**wire, "session_seq": 0}, "$"), code="invalid_revision")
        _reject(lambda: decode_research_event({**wire, "session_seq": True}, "$"), code="invalid_revision")
        _reject(lambda: decode_research_event({**wire, "subject_revision": 0}, "$"), code="invalid_revision")
        _reject(lambda: decode_research_event({**wire, "occurred_at": "2026-06-01"}, "$"), code="invalid_timestamp")

    def test_execution_event_requires_round_and_node(self) -> None:
        """解码与 validator 同判据：执行事件缺 round/node 必须拒绝（检视 R19）。"""

        wire = encode_research_event(_event())
        _reject(lambda: decode_research_event({**wire, "round_id": None}, "$"), code="identity_mismatch")
        _reject(lambda: decode_research_event({**wire, "node_id": None}, "$"), code="identity_mismatch")

    def test_session_event_rejects_execution_identity(self) -> None:
        """纯 session 事件携 round/node/execution_seq 必须拒绝（validator 同判据）。"""

        wire = encode_research_event(_session_event())
        _reject(lambda: decode_research_event({**wire, "round_id": _ROUND}, "$"), code="identity_mismatch")
        _reject(lambda: decode_research_event({**wire, "node_id": _NODE}, "$"), code="identity_mismatch")
        _reject(lambda: decode_research_event({**wire, "execution_seq": 1}, "$"), code="identity_mismatch")

    def test_payload_must_be_decodeable_json(self) -> None:
        """坏载荷（坏语法/重复键/非有限数）必须拒绝，不冒认合法事件（R21）。"""

        wire = encode_research_event(_event())
        _reject(lambda: decode_research_event({**wire, "payload_canonical_json": "{broken"}, "$"), code="invalid_json")
        _reject(
            lambda: decode_research_event({**wire, "payload_canonical_json": '{"a":1,"a":2}'}, "$"),
            code="invalid_json",
        )
        _reject(
            lambda: decode_research_event({**wire, "payload_canonical_json": '{"a":NaN}'}, "$"),
            code="invalid_json",
        )

    def test_real_revision_event_roundtrip(self) -> None:
        """真实修订 M2 事件（纯 session 公开消息）往返不回归（检视 R19）。

        生产行形态（``revise_queued``）：execution/round/node/execution_seq 全 None，
        载荷为 public_message 结构；收紧后仍必须可编码、可解码、再编码稳定。
        """

        message_id = "user_message_revise_1"
        wire = encode_research_event(
            _session_event(
                event_kind=EventKind.PUBLIC_MESSAGE_SAVED,
                subject_id=message_id,
                payload_kind="public_message",
                payload_canonical_json=json_doc(
                    {
                        "schema_version": 1,
                        "message_id": message_id,
                        "role": "user",
                        "source_id": "message_source_revise_1",
                        "source_version": "1",
                    }
                ),
            )
        )
        decoded = decode_research_event(decode_json_doc(json_doc(wire)), "$")
        validate_research_event(decoded)
        assert decoded.execution_id is None
        assert decoded.round_id is None
        assert decoded.node_id is None
        assert decoded.execution_seq is None
        payload = decode_json_doc(decoded.payload_canonical_json)
        assert isinstance(payload, dict)
        assert payload["message_id"] == message_id
        assert json_doc(encode_research_event(decoded)) == json_doc(wire)


class TestValidateResearchEvent:
    def test_valid_execution_event(self) -> None:
        validate_research_event(_event())

    def test_valid_session_event(self) -> None:
        validate_research_event(_session_event())

    def test_execution_event_requires_round_node_seq(self) -> None:
        _reject(lambda: validate_research_event(_event(round_id=None)), code="identity_mismatch")
        _reject(lambda: validate_research_event(_event(node_id=None)), code="identity_mismatch")
        _reject(lambda: validate_research_event(_event(execution_seq=None)), code="identity_mismatch")

    def test_execution_seq_requires_execution(self) -> None:
        _reject(lambda: validate_research_event(_session_event(execution_seq=1)), code="identity_mismatch")
        _reject(lambda: validate_research_event(_session_event(round_id=_ROUND)), code="identity_mismatch")

    def test_execution_seq_bounds(self) -> None:
        _reject(lambda: validate_research_event(_event(execution_seq=0)), code="invalid_revision")
        _reject(lambda: validate_research_event(_event(execution_seq=True)), code="invalid_revision")

    def test_input_revision_bounds(self) -> None:
        _reject(lambda: validate_research_event(_event(input_revision=0)), code="invalid_revision")
        _reject(lambda: validate_research_event(_event(input_revision=True)), code="invalid_revision")

    def test_payload_must_be_canonical_json(self) -> None:
        _reject(
            lambda: validate_research_event(_event(payload_canonical_json="{not json")),
            code="invalid_json",
        )
        # 重复键拒绝
        _reject(
            lambda: validate_research_event(_event(payload_canonical_json='{"a":1,"a":2}')),
            code="duplicate_key",
        )

    def test_empty_identity_rejected(self) -> None:
        _reject(lambda: validate_research_event(_event(subject_id="")), code="invalid_identity")
        _reject(lambda: validate_research_event(_event(payload_kind="")), code="invalid_identity")


class TestValidateEventIdentity:
    def test_matching_native_id_passes(self) -> None:
        validate_event_identity(_event(), "native-1")

    def test_mismatching_native_id_rejected(self) -> None:
        _reject(lambda: validate_event_identity(_event(), "native-9"), code="identity_mismatch")

    def test_session_event_identity(self) -> None:
        validate_event_identity(_session_event(), "native-2")
        _reject(lambda: validate_event_identity(_session_event(), "native-9"), code="identity_mismatch")


class TestQueryAndFactBaselines:
    def test_read_query_defaults(self) -> None:
        query = EventReadQuery(user_scope=_USER, session_id=_SESSION, after_seq=0)
        assert query.limit == 50
        assert query.after_seq == 0

    def test_page_fields(self) -> None:
        page = EventPage(events=(), last_seq=0, has_more=False)
        assert page.events == ()


def _reject(action, *, code: str) -> None:
    with pytest.raises(AgentValidationError) as exc:
        action()
    assert exc.value.code == code, f"expected {code}, got {exc.value.code}"
    assert exc.value.field_path
