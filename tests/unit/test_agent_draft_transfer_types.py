"""草稿转移契约单测（F-03-3；DD §4.2 DraftTransferCommand/DraftTransferReceipt）。覆盖：严格 wire（全键/禁额外键/坏标签 fail-closed）、transfer_id 沿原命令稳定派生且不含
payload 摘要、payload 摘要必须与实际载荷一致、确认位必须为 true、双方预期版
与命令引用一致、session_draft/business_preparation 的源身份约束、禁止移入
自身、空稿判定（正文空但有未发送选择不是空位）、输入摘要稳定性、回执坏枚举
拒绝。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from core.agent_types import (
    AgentValidationError,
    CommandRef,
    CommitState,
    DraftPayload,
    DraftTransferCommand,
    DraftTransferPhase,
    DraftTransferReceipt,
    DraftTransferSourceKind,
    Mode,
    Purpose,
    SendableOwner,
    decode_draft_transfer_command,
    decode_draft_transfer_receipt,
    derive_draft_transfer_id,
    draft_transfer_input_digest,
    draft_transfer_payload_digest,
    encode_draft_transfer_command,
    encode_draft_transfer_receipt,
    is_empty_draft_payload,
    json_doc,
    validate_draft_transfer_command,
)

_DIGEST = "a" * 64
_BASELINE = "b" * 64


def _payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 2,
        "text": "研究三家公司",
        "mode": Mode.DISCUSS,
        "model_selection": json_doc({"scenario_id": "agent_research"}),
        "source_refs": (),
        "skill_versions": (),
        "mcp_selection": (),
        "purpose": None,
        "research_date": None,
        "cutoff": None,
        "send_target": None,
        "object_context": json_doc({"source_module": "ui"}),
        "role_prompt_selection": None,
        "customized_fields": (),
        "defaults_baseline_digest": None,
    }
    values.update(overrides)
    return DraftPayload(**values)


def _command(**overrides: Any) -> DraftTransferCommand:
    payload = overrides.pop("payload", _payload())
    values: dict[str, Any] = {
        "command": CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id="req-transfer",
            command_id="cmd-transfer-1",
            expected_revision=1,
        ),
        "transfer_id": "transfer-pending",
        "source_kind": DraftTransferSourceKind.SESSION_DRAFT,
        "source_id": "s1",
        "source_session_id": "s1",
        "expected_source_draft_revision": 2,
        "target_session_id": "s2",
        "expected_target_draft_revision": 1,
        "payload": payload,
        "payload_digest": draft_transfer_payload_digest(payload),
        "confirmed": True,
    }
    values.update(overrides)
    command = DraftTransferCommand(**values)
    if "transfer_id" not in overrides:
        command = replace(command, transfer_id=derive_draft_transfer_id(command))
    return command


def _receipt(**overrides: Any) -> DraftTransferReceipt:
    values: dict[str, Any] = {
        "command_record_id": "command_abc",
        "transfer_id": "draft_transfer_abc",
        "source_kind": DraftTransferSourceKind.SESSION_DRAFT,
        "source_id": "s1",
        "source_session_id": "s1",
        "target_session_id": "s2",
        "expected_source_draft_revision": 2,
        "expected_target_draft_revision": 1,
        "payload_digest": _DIGEST,
        "commit_state": CommitState.COMMITTED,
        "phase": DraftTransferPhase.COMPLETED,
        "sendable_owner": SendableOwner.TARGET,
        "source_revision": 3,
        "target_revision": 2,
        "verified_at": "2026-11-14T00:00:00.000000Z",
        "next_action": None,
    }
    values.update(overrides)
    return DraftTransferReceipt(**values)


_TRANSFER_WIRE_KEYS = {
    "command",
    "transfer_id",
    "source_kind",
    "source_id",
    "source_session_id",
    "expected_source_draft_revision",
    "target_session_id",
    "expected_target_draft_revision",
    "payload",
    "payload_digest",
    "confirmed",
}

_RECEIPT_WIRE_KEYS = {
    "command_record_id",
    "transfer_id",
    "source_kind",
    "source_id",
    "source_session_id",
    "target_session_id",
    "expected_source_draft_revision",
    "expected_target_draft_revision",
    "payload_digest",
    "commit_state",
    "phase",
    "sendable_owner",
    "source_revision",
    "target_revision",
    "verified_at",
    "next_action",
}


def _reject(call: Any, code: str, field: str) -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        call()
    assert excinfo.value.code == code, f"expect {code}, got {excinfo.value.code}: {excinfo.value.field_path}"
    assert excinfo.value.field_path == field


class TestDraftTransferCommandCodec:
    def test_roundtrip_session_draft(self) -> None:
        command = _command()
        wire = encode_draft_transfer_command(command)

        assert set(wire) == _TRANSFER_WIRE_KEYS
        assert wire["confirmed"] is True
        assert wire["source_kind"] == "session_draft"
        assert decode_draft_transfer_command(wire) == command

    def test_transfer_id_is_stable_and_excludes_payload(self) -> None:
        command = _command()
        changed = replace(
            command,
            payload=_payload(text="另一份草稿"),
            payload_digest=draft_transfer_payload_digest(_payload(text="另一份草稿")),
        )
        assert derive_draft_transfer_id(changed) == command.transfer_id

    def test_transfer_id_mismatch_rejected(self) -> None:
        command = _command()
        _reject(
            lambda: encode_draft_transfer_command(replace(command, transfer_id="transfer-other")),
            "invalid_value",
            "$.transfer_id",
        )

    def test_payload_digest_must_match_payload(self) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(payload_digest=_DIGEST)),
            "invalid_digest",
            "$.payload_digest",
        )

    @pytest.mark.parametrize("confirmed", [False, 1, "true", None])
    def test_confirmed_must_be_true(self, confirmed: Any) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(confirmed=confirmed)),
            "unconfirmed_transfer",
            "$.confirmed",
        )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("expected_source_draft_revision", 0),
            ("expected_source_draft_revision", True),
            ("expected_source_draft_revision", 2.0),
        ],
    )
    def test_source_revision_rejected(self, field: str, value: Any) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(**{field: value})),
            "invalid_revision",
            f"$.{field}",
        )

    def test_command_expected_revision_must_equal_target_revision(self) -> None:
        ref = CommandRef(
            user_scope="u1",
            source_module="ui",
            business_request_id="req-transfer",
            command_id="cmd-transfer-1",
            expected_revision=5,
        )
        _reject(
            lambda: encode_draft_transfer_command(_command(command=ref)),
            "invalid_revision",
            "$.command.expected_revision",
        )

    def test_session_draft_requires_source_session_id(self) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(source_session_id=None)),
            "invalid_identity",
            "$.source_session_id",
        )

    def test_session_draft_source_session_must_equal_source_id(self) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(source_session_id="s9")),
            "invalid_value",
            "$.source_session_id",
        )

    def test_session_draft_cannot_target_itself(self) -> None:
        command = _command()
        self_target = replace(command, target_session_id="s1")
        _reject(
            lambda: encode_draft_transfer_command(
                replace(self_target, transfer_id=derive_draft_transfer_id(self_target))
            ),
            "invalid_value",
            "$.target_session_id",
        )

    def test_business_preparation_forbids_source_session_id(self) -> None:
        _reject(
            lambda: encode_draft_transfer_command(_command(source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION)),
            "invalid_value",
            "$.source_session_id",
        )

    def test_business_preparation_roundtrip_without_source_session(self) -> None:
        command = _command(
            source_kind=DraftTransferSourceKind.BUSINESS_PREPARATION,
            source_id="prep-1",
            source_session_id=None,
        )
        wire = encode_draft_transfer_command(command)
        assert wire["source_session_id"] is None
        assert decode_draft_transfer_command(wire) == command

    def test_decode_rejects_missing_and_extra_keys(self) -> None:
        wire = encode_draft_transfer_command(_command())
        missing = dict(wire)
        missing.pop("transfer_id")
        _reject(
            lambda: decode_draft_transfer_command(missing),
            "missing_field",
            "$.draft_transfer_command.transfer_id",
        )

        extra = dict(wire)
        extra["override"] = True
        _reject(
            lambda: decode_draft_transfer_command(extra),
            "unknown_field",
            "$.draft_transfer_command.override",
        )

    def test_decode_rejects_bad_source_kind(self) -> None:
        wire = encode_draft_transfer_command(_command())
        wire["source_kind"] = "browser_tab"
        _reject(
            lambda: decode_draft_transfer_command(wire),
            "invalid_tag",
            "$.draft_transfer_command.source_kind",
        )

    def test_validate_rejects_credentials_in_payload(self) -> None:
        payload = _payload(
            model_selection=json_doc({"api_key": "plain"}),
            customized_fields=("model_selection",),
            defaults_baseline_digest=_BASELINE,
        )
        _reject(
            lambda: validate_draft_transfer_command(_command(payload=payload)),
            "draft_secret_field_forbidden",
            "$.payload.model_selection.api_key",
        )

    def test_input_digest_covers_identities_versions_and_payload(self) -> None:
        command = _command()
        assert draft_transfer_input_digest(command) == draft_transfer_input_digest(_command())

        other_target = _command(target_session_id="s3")
        assert draft_transfer_input_digest(other_target) != draft_transfer_input_digest(command)

        other_payload = replace(
            command,
            payload=_payload(text="别的正文"),
            payload_digest=draft_transfer_payload_digest(_payload(text="别的正文")),
        )
        assert draft_transfer_input_digest(other_payload) != draft_transfer_input_digest(command)

        other_revision = _command(expected_source_draft_revision=7)
        assert draft_transfer_input_digest(other_revision) != draft_transfer_input_digest(command)


class TestDraftTransferReceiptCodec:
    def test_roundtrip(self) -> None:
        receipt = _receipt()
        wire = encode_draft_transfer_receipt(receipt)

        assert set(wire) == _RECEIPT_WIRE_KEYS
        assert wire["phase"] == "completed"
        assert wire["sendable_owner"] == "target"
        assert decode_draft_transfer_receipt(wire) == receipt

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("phase", "sent"),
            ("sendable_owner", "both"),
            ("commit_state", "saved"),
        ],
    )
    def test_bad_enums_fail_closed(self, field: str, value: str) -> None:
        wire = encode_draft_transfer_receipt(_receipt())
        wire[field] = value
        expected_codes = {
            "phase": "invalid_phase",
            "sendable_owner": "invalid_owner",
            "commit_state": "invalid_commit_state",
        }
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_transfer_receipt(wire)
        assert excinfo.value.code == expected_codes[field]
        assert excinfo.value.field_path == f"$.draft_transfer_receipt.{field}"

    def test_unknown_revisions_must_be_explicit_null(self) -> None:
        wire = encode_draft_transfer_receipt(
            _receipt(
                phase=DraftTransferPhase.UNKNOWN,
                sendable_owner=SendableOwner.NONE,
                source_revision=None,
                target_revision=None,
            )
        )
        assert wire["source_revision"] is None
        decoded = decode_draft_transfer_receipt(wire)
        assert decoded.source_revision is None and decoded.target_revision is None
        assert decoded.phase is DraftTransferPhase.UNKNOWN
        assert decoded.sendable_owner is SendableOwner.NONE

    def test_decode_rejects_missing_key(self) -> None:
        wire = encode_draft_transfer_receipt(_receipt())
        wire.pop("payload_digest")
        _reject(
            lambda: decode_draft_transfer_receipt(wire),
            "missing_field",
            "$.draft_transfer_receipt.payload_digest",
        )

    def test_receipt_rejects_non_digest_payload_digest(self) -> None:
        _reject(
            lambda: encode_draft_transfer_receipt(_receipt(payload_digest="not-a-digest")),
            "invalid_digest",
            "$.payload_digest",
        )


class TestEmptyDraftSemantics:
    def test_absent_payload_is_empty(self) -> None:
        assert is_empty_draft_payload(None) is True

    def test_text_empty_and_no_selection_is_empty(self) -> None:
        assert (
            is_empty_draft_payload(_payload(text="", model_selection=None, object_context=None, mode=Mode.EXECUTE))
            is True
        )

    def test_empty_text_but_unsent_selection_is_not_empty(self) -> None:
        # 模型/对象选择仍在：正文空不构成空位。
        assert is_empty_draft_payload(_payload(text="", mode=Mode.EXECUTE)) is False
        assert (
            is_empty_draft_payload(
                _payload(
                    text="",
                    mode=Mode.EXECUTE,
                    model_selection=None,
                    object_context=None,
                    purpose=Purpose.CONCEPT,
                )
            )
            is False
        )

    def test_user_touched_default_is_not_empty(self) -> None:
        payload = _payload(
            text="",
            model_selection=None,
            object_context=None,
            mode=Mode.EXECUTE,
            customized_fields=("mode",),
            defaults_baseline_digest=_BASELINE,
        )
        assert is_empty_draft_payload(payload) is False

    def test_non_default_mode_is_not_empty_even_without_customized_fields(self) -> None:
        assert (
            is_empty_draft_payload(_payload(text="", model_selection=None, object_context=None, mode=Mode.PLAN))
            is False
        )
        assert (
            is_empty_draft_payload(_payload(text="", model_selection=None, object_context=None, mode=Mode.DISCUSS))
            is False
        )

    def test_any_source_or_skill_or_mcp_selection_is_not_empty(self) -> None:
        from core.agent_types import MCPServiceIdentity, SkillVersionRef, SourceKind, SourceVersionRef

        base = dict(text="", model_selection=None, object_context=None, mode=Mode.EXECUTE)
        assert (
            is_empty_draft_payload(
                _payload(
                    **base,
                    source_refs=(
                        SourceVersionRef(
                            source_kind=SourceKind.USER_MESSAGE,
                            source_id="src-1",
                            source_version="1",
                            source_digest=_DIGEST,
                        ),
                    ),
                )
            )
            is False
        )
        assert (
            is_empty_draft_payload(_payload(**base, skill_versions=(SkillVersionRef(skill_id="sk-1", version=1),)))
            is False
        )
        assert (
            is_empty_draft_payload(
                _payload(**base, mcp_selection=(MCPServiceIdentity(service_id="mcp-1", config_version=1),))
            )
            is False
        )
        assert is_empty_draft_payload(_payload(**base, cutoff="2026-11-13")) is False
        assert is_empty_draft_payload(_payload(**base, send_target=json_doc({"kind": "new_question"}))) is False
        assert is_empty_draft_payload(_payload(**base, role_prompt_selection=json_doc({"role_id": "analyst"}))) is False
