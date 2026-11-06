"""C02：封闭 payload（§5.9/§5.10/§9.3.2/§9.11）行为测试。"""

import pytest

from core.agent_types import AgentValidationError, json_doc, validate_payload, validate_return_ref

RESOURCE_ID = "10000000-0000-0000-0000-000000000001"
CONVERSATION_ID = "30000000-0000-0000-0000-000000000001"
PATH_ID = "30000000-0000-0000-0000-000000000002"
EXECUTION_ID = "40000000-0000-0000-0000-000000000001"
DECISION_ID = "50000000-0000-0000-0000-000000000001"
DIGEST = "a" * 64
UTC_TIME = "2026-04-30T00:00:00.000000Z"


def _metadata(**overrides):
    data = {
        "schema_version": 1,
        "name": "公告",
        "as_of": "2026-04-30",
        "published_at": None,
        "published_date": "2026-03-20",
        "time_precision": "date",
        "period": None,
        "effective_from": None,
        "effective_to": None,
        "units": {},
        "correction_of": None,
    }
    data.update(overrides)
    return data


def _read_range():
    return {"pages": [], "sheets": [], "row_start": None, "row_end": None, "locator": "全文"}


def _source_availability():
    return {
        "observed_at": UTC_TIME,
        "fresh": True,
        "sources": [{"ref": {"resource_id": RESOURCE_ID, "revision": 1}, "status": "available", "problem": None}],
    }


def _problem():
    return {"code": "source_unavailable", "message_key": "agent.source_unavailable", "params": "{}", "related_id": None}


def _delivery(**overrides):
    view = {
        "schema_version": 1,
        "operation_id": "60000000-0000-0000-0000-000000000001",
        "revision": 1,
        "artifact_id": "20000000-0000-0000-0000-000000000001",
        "artifact_revision": 1,
        "format": "md",
        "destination": "/tmp/agent-report.md",
        "expected_target_digest": None,
        "confirm_overwrite": False,
        "content_digest": DIGEST,
        "source_availability": None,
        "delivery_digest": None,
        "status": "preparing",
        "phase": "preparing",
        "cancel_requested": False,
        "problem": None,
    }
    view.update(overrides)
    return view


def _decision(**overrides):
    view = {
        "decision_id": DECISION_ID,
        "target": {"execution_id": EXECUTION_ID, "task_id": None, "attempt_id": None},
        "revision": 1,
        "native_request_id": "native-1",
        "continuation_ref": None,
        "question_key": "approve_scope",
        "question_params": "{}",
        "options": json_doc([{"id": "yes", "label": "同意"}, {"id": "no", "label": "拒绝"}]),
        "answer_schema": json_doc({"kind": "choice", "option_ids": ["yes"], "min_items": 1, "max_items": 1}),
        "scope_digest": DIGEST,
        "expires_at": None,
        "status": "pending",
        "answer_command_id": None,
        "answer": None,
        "answer_phase": "unanswered",
        "answer_problem": None,
    }
    view.update(overrides)
    return view


def test_source_metadata_accepts_date_precision_only_date():
    validate_payload("source_metadata", json_doc(_metadata()))

    with pytest.raises(AgentValidationError) as error:
        validate_payload("source_metadata", json_doc(_metadata(published_at=UTC_TIME)))
    assert error.value.field_path == "$.published_at"

    with pytest.raises(AgentValidationError) as error:
        validate_payload("source_metadata", json_doc(_metadata(time_precision="unknown", published_date="2026-03-20")))
    assert error.value.field_path == "$.published_at"


def test_source_metadata_rejects_reversed_effective_range_and_unknown_key():
    with pytest.raises(AgentValidationError) as error:
        validate_payload(
            "source_metadata",
            json_doc(_metadata(effective_from="2026-04-30", effective_to="2026-03-20")),
        )
    assert error.value.field_path == "$.effective_from"

    with pytest.raises(AgentValidationError) as error:
        validate_payload("source_metadata", json_doc(_metadata(extra=True)))
    assert error.value.field_path == "$.extra"


def test_source_registration_covers_business_tool_and_excerpt():
    business = {
        "schema_version": 1,
        "kind": "business",
        "provider_key": "stock",
        "source_object_key": "000001.SZ",
        "source_revision": "2026Q1",
        "as_of": "2026-04-30",
    }
    validate_payload("source_registration", json_doc(business))

    tool = {
        "schema_version": 1,
        "kind": "tool",
        "target": {"execution_id": EXECUTION_ID, "task_id": EXECUTION_ID, "attempt_id": EXECUTION_ID},
        "effect_key": "call-1",
        "as_of": "2026-04-30",
    }
    validate_payload("source_registration", json_doc(tool))

    excerpt = {"schema_version": 1, "kind": "user_excerpt", "metadata": _metadata(), "text": "用户摘录"}
    validate_payload("source_registration", json_doc(excerpt))

    business["provider_key"] = "unknown"
    with pytest.raises(AgentValidationError) as error:
        validate_payload("source_registration", json_doc(business))
    assert error.value.field_path == "$.provider_key"

    tool["target"]["task_id"] = None
    with pytest.raises(AgentValidationError) as error:
        validate_payload("source_registration", json_doc(tool))
    assert error.value.field_path == "$.target.task_id"


def test_prepared_source_requires_partial_limitations_and_canonical_json_text():
    prepared = {
        "schema_version": 1,
        "metadata": _metadata(),
        "media_type": "text/plain",
        "text": "正文",
        "read_range": _read_range(),
        "extraction": "complete",
        "limitations": [],
    }
    validate_payload("prepared_source", json_doc(prepared))

    prepared["extraction"] = "partial"
    with pytest.raises(AgentValidationError) as error:
        validate_payload("prepared_source", json_doc(prepared))
    assert error.value.field_path == "$.limitations"

    prepared["extraction"] = "complete"
    prepared["media_type"] = "application/json"
    prepared["text"] = '{"b":1, "a":2}'
    with pytest.raises(AgentValidationError) as error:
        validate_payload("prepared_source", json_doc(prepared))
    assert error.value.code == "non_canonical_json"


def test_delivery_view_joint_status_phase_and_digest():
    validate_payload("delivery_view", json_doc(_delivery()))

    published = _delivery(
        status="published",
        phase="settled",
        source_availability=_source_availability(),
        delivery_digest=DIGEST,
    )
    validate_payload("delivery_view", json_doc(published))

    with pytest.raises(AgentValidationError) as error:
        validate_payload("delivery_view", json_doc(_delivery(status="published", phase="settled")))
    assert error.value.field_path in {"$.source_availability", "$.delivery_digest"}

    with pytest.raises(AgentValidationError) as error:
        validate_payload("delivery_view", json_doc(_delivery(status="failed", phase="settled")))
    assert error.value.field_path == "$.problem"

    unknown = _delivery(
        status="unknown",
        phase="writing",
        source_availability=_source_availability(),
        problem=_problem(),
    )
    validate_payload("delivery_view", json_doc(unknown))

    with pytest.raises(AgentValidationError) as error:
        validate_payload("delivery_view", json_doc(_delivery(status="unknown", phase="writing")))
    assert error.value.field_path == "$.problem"


def test_decision_view_accepts_unanswered_pending_and_rejects_answer_without_command():
    validate_payload("decision_view", json_doc(_decision()))

    with pytest.raises(AgentValidationError) as error:
        validate_payload(
            "decision_view",
            json_doc(
                _decision(
                    answer_command_id="70000000-0000-0000-0000-000000000001",
                    answer=json_doc({"kind": "choice", "option_ids": ["yes"]}),
                )
            ),
        )
    assert error.value.field_path in {"$.answer", "$.answer_command_id"}

    with pytest.raises(AgentValidationError) as error:
        validate_payload("decision_view", json_doc(_decision(status="answered", answer_phase="recorded")))
    assert error.value.field_path == "$.answer"


def test_decision_view_answer_phase_unknown_requires_problem():
    answered = _decision(
        status="answered",
        answer_command_id="70000000-0000-0000-0000-000000000001",
        answer=json_doc({"kind": "choice", "option_ids": ["yes"]}),
        answer_phase="unknown",
    )
    with pytest.raises(AgentValidationError) as error:
        validate_payload("decision_view", json_doc(answered))
    assert error.value.field_path == "$.answer_problem"

    answered["answer_problem"] = _problem()
    validate_payload("decision_view", json_doc(answered))


def test_decision_view_answer_must_match_saved_schema():
    answered = _decision(
        status="answered",
        answer_command_id="70000000-0000-0000-0000-000000000001",
        answer_schema=json_doc({"kind": "text", "min_length": 1, "max_length": 20000}),
        answer=json_doc({"kind": "choice", "text": "同意"}),
        answer_phase="recorded",
    )
    with pytest.raises(AgentValidationError) as error:
        validate_payload("decision_view", json_doc(answered))
    assert error.value.field_path == "$.answer.kind"


def test_role_payload_rejects_default_outside_enabled():
    role = {
        "schema_version": 1,
        "role_key": "main",
        "description": "主会话",
        "prompt_ref": {"resource_id": RESOURCE_ID, "revision": 1},
        "model_choice": {"kind": "explicit", "connection_id": "connection-A", "scenario_id": None},
        "enabled_skills": [{"resource_id": RESOURCE_ID, "revision": 1}],
        "default_skills": [{"resource_id": RESOURCE_ID, "revision": 1}],
        "default_mcp": [],
        "enabled": True,
    }
    validate_payload("role", json_doc(role))

    role["default_skills"] = [{"resource_id": "10000000-0000-0000-0000-000000000002", "revision": 1}]
    with pytest.raises(AgentValidationError) as error:
        validate_payload("role", json_doc(role))
    assert error.value.field_path == "$.default_skills"


def test_source_payload_failed_extraction_keeps_no_content_ref():
    source = {
        "schema_version": 1,
        "origin": "file",
        "media_type": "text/plain",
        "name": "年报",
        "content_ref": None,
        "published_at": None,
        "published_date": "2026-03-20",
        "time_precision": "date",
        "period": None,
        "effective_from": None,
        "effective_to": None,
        "as_of": "2026-04-30",
        "units": {},
        "read_range": _read_range(),
        "extraction": "failed",
        "limitations": ["解析失败"],
        "correction_of": None,
    }
    validate_payload("source", json_doc(source))

    source["content_ref"] = {"kind": "managed_blob", "relative_path": "blob/1", "digest": DIGEST}
    with pytest.raises(AgentValidationError) as error:
        validate_payload("source", json_doc(source))
    assert error.value.field_path == "$.content_ref"


def test_return_ref_conversation_shape_is_joint():
    ref = {
        "schema_version": 1,
        "kind": "conversation",
        "conversation_id": CONVERSATION_ID,
        "path_id": PATH_ID,
        "path_revision": 1,
        "message_id": None,
        "message_revision": None,
        "execution_id": None,
        "source_module": "agent",
        "source_object_key": None,
        "view_state": None,
    }
    validate_return_ref(json_doc(ref))

    ref["message_id"] = "80000000-0000-0000-0000-000000000001"
    with pytest.raises(AgentValidationError) as error:
        validate_return_ref(json_doc(ref))
    assert error.value.field_path == "$.message_id"


def test_unknown_payload_kind_is_unsupported():
    with pytest.raises(AgentValidationError) as error:
        validate_payload("unknown", json_doc({}))
    assert error.value.code == "unsupported_schema"


def test_uuid_and_digest_formats_are_strict():
    view = _delivery(content_digest="A" * 64)
    with pytest.raises(AgentValidationError) as error:
        validate_payload("delivery_view", json_doc(view))
    assert error.value.field_path == "$.content_digest"
