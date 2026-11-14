"""草稿持久契约单测（F-03-1；DD §4.2 DraftSaveCommand/DraftReceipt、§9.3）。

覆盖：全键必填/禁额外键、bool 与 0 预期版拒绝、命令引用与预期版一致、
集合规范化（去重/排序）、自定义字段与基线摘要成对约束、截止日校验、
不透明片段拒绝 float 与凭据/批准键、回执坏枚举 fail-closed、输入摘要稳定。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentValidationError,
    CommandRef,
    CommitState,
    DraftPayload,
    DraftReceipt,
    DraftSaveCommand,
    MCPServiceIdentity,
    Mode,
    Purpose,
    SkillVersionRef,
    SourceKind,
    SourceVersionRef,
    decode_draft_payload,
    decode_draft_receipt,
    decode_draft_save_command,
    draft_save_input_digest,
    encode_draft_payload,
    encode_draft_receipt,
    encode_draft_save_command,
    json_doc,
    validate_draft_save_command,
)

_DIGEST = "a" * 64
_BASELINE = "b" * 64


def _source(source_id: str = "src-1", version: str = "1") -> SourceVersionRef:
    return SourceVersionRef(
        source_kind=SourceKind.USER_MESSAGE,
        source_id=source_id,
        source_version=version,
        source_digest=_DIGEST,
    )


def _payload(**overrides: Any) -> DraftPayload:
    values: dict[str, Any] = {
        "schema_version": 1,
        "text": "研究三家公司",
        "mode": Mode.DISCUSS,
        "model_selection": json_doc({"scenario_id": "agent_research", "connection_id": None}),
        "source_refs": (_source(),),
        "skill_versions": (SkillVersionRef(skill_id="sk-1", version=2),),
        "mcp_selection": (MCPServiceIdentity(service_id="mcp-1", config_version=1),),
        "purpose": Purpose.CONCEPT,
        "cutoff": "2026-11-13",
        "send_target": json_doc({"kind": "new_question"}),
        "object_context": json_doc({"source_module": "ui", "object_ref": "stock:600519"}),
        "role_prompt_selection": json_doc({"role_id": "analyst"}),
        "customized_fields": ("mode",),
        "defaults_baseline_digest": _BASELINE,
    }
    values.update(overrides)
    return DraftPayload(**values)


def _command(**overrides: Any) -> DraftSaveCommand:
    ref = CommandRef(
        user_scope="u1",
        source_module="ui",
        business_request_id="req-1",
        command_id="cmd-1",
        expected_revision=3,
    )
    values: dict[str, Any] = {
        "command": ref,
        "session_id": "s1",
        "expected_draft_revision": 3,
        "payload": _payload(),
    }
    values.update(overrides)
    return DraftSaveCommand(**values)


def _expect_error(excinfo: pytest.ExceptionInfo[AgentValidationError], code: str, fragment: str) -> None:
    assert excinfo.value.code == code, f"expect {code}, got {excinfo.value.code}"
    assert fragment in excinfo.value.field_path


class TestDraftPayloadCodec:
    def test_round_trip_preserves_all_fields(self) -> None:
        wire = encode_draft_payload(_payload())
        assert set(wire) == {
            "schema_version",
            "text",
            "mode",
            "model_selection",
            "source_refs",
            "skill_versions",
            "mcp_selection",
            "purpose",
            "cutoff",
            "send_target",
            "object_context",
            "role_prompt_selection",
            "customized_fields",
            "defaults_baseline_digest",
        }
        assert decode_draft_payload(wire) == _payload()

    def test_empty_text_and_no_selection_allowed(self) -> None:
        payload = _payload(
            text="",
            model_selection=None,
            source_refs=(),
            skill_versions=(),
            mcp_selection=(),
            purpose=None,
            cutoff=None,
            send_target=None,
            object_context=None,
            role_prompt_selection=None,
            customized_fields=(),
            defaults_baseline_digest=None,
        )
        assert decode_draft_payload(encode_draft_payload(payload)) == payload

    def test_extra_key_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["extra"] = 1
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "unknown_field", "$.payload")

    def test_missing_key_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        del wire["cutoff"]
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "missing_field", "$.payload")

    def test_bool_schema_version_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["schema_version"] = True
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "invalid_schema_version", "$.payload.schema_version")

    def test_invalid_mode_tag_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["mode"] = "chat"
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "invalid_tag", "$.payload.mode")

    def test_invalid_cutoff_rejected(self) -> None:
        for cutoff in ("2026-13-01", "2026-1-1", "20261113"):
            wire = encode_draft_payload(_payload())
            wire["cutoff"] = cutoff
            with pytest.raises(AgentValidationError) as excinfo:
                decode_draft_payload(wire)
            _expect_error(excinfo, "invalid_cutoff", "$.payload.cutoff")

    def test_customized_fields_require_baseline_digest(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(_payload(customized_fields=("mode",), defaults_baseline_digest=None))
        _expect_error(excinfo, "missing_field", "$.payload.defaults_baseline_digest")

    def test_empty_customized_fields_must_not_carry_baseline(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(_payload(customized_fields=(), defaults_baseline_digest=_BASELINE))
        _expect_error(excinfo, "invalid_value", "$.payload.defaults_baseline_digest")

    def test_duplicate_customized_fields_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(_payload(customized_fields=("mode", "mode")))
        _expect_error(excinfo, "invalid_range", "$.payload.customized_fields")

    def test_customized_fields_sorted_on_decode(self) -> None:
        wire = encode_draft_payload(_payload(customized_fields=("text", "mode")))
        assert wire["customized_fields"] == ["mode", "text"]

    def test_source_refs_sorted_and_deduplicated(self) -> None:
        payload = _payload(source_refs=(_source("src-b"), _source("src-a")))
        decoded = decode_draft_payload(encode_draft_payload(payload))
        assert [ref.source_id for ref in decoded.source_refs] == ["src-a", "src-b"]

    def test_duplicate_source_refs_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(_payload(source_refs=(_source(), _source())))
        _expect_error(excinfo, "duplicate_source_ref", "$.payload.source_refs")

    def test_duplicate_skill_and_mcp_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(
                _payload(skill_versions=(SkillVersionRef(skill_id="sk", version=1), SkillVersionRef("sk", 1)))
            )
        _expect_error(excinfo, "duplicate_skill_version", "$.payload.skill_versions")
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_payload(
                _payload(
                    mcp_selection=(
                        MCPServiceIdentity(service_id="m", config_version=1),
                        MCPServiceIdentity(service_id="m", config_version=1),
                    )
                )
            )
        _expect_error(excinfo, "duplicate_mcp_service", "$.payload.mcp_selection")

    def test_skill_version_zero_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["skill_versions"] = [{"skill_id": "sk", "version": 0}]
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "invalid_range", "$.payload.skill_versions[0].version")

    def test_opaque_fragment_must_be_object(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["model_selection"] = ["bound"]
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "invalid_type", "$.payload.model_selection")

    def test_opaque_fragment_rejects_metadata_credentials(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["model_selection"] = {"scenario_id": "agent_research", "api_key": "sk-live"}
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "draft_secret_field_forbidden", "$.payload.model_selection.api_key")
        wire = encode_draft_payload(_payload())
        wire["role_prompt_selection"] = {"nested": [{"approval": True}]}
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "draft_secret_field_forbidden", "$.payload.role_prompt_selection.nested[0].approval")

    def test_object_context_requires_source_module(self) -> None:
        wire = encode_draft_payload(_payload())
        wire["object_context"] = {"object_ref": "stock:600519"}
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "missing_field", "$.payload.object_context.source_module")

    def test_float_in_opaque_fragment_rejected(self) -> None:
        wire = encode_draft_payload(_payload())
        # 不透明片段同样禁止 float（数值须先经 agent-number-v1 精确编码）。
        wire["model_selection"] = {"confidence": 0.5}
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_payload(wire)
        _expect_error(excinfo, "invalid_type", "$.payload.model_selection.confidence")


class TestDraftSaveCommandCodec:
    def test_round_trip(self) -> None:
        command = _command()
        wire = encode_draft_save_command(command)
        assert decode_draft_save_command(wire) == command

    def test_expected_revision_zero_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_save_command(_command(expected_draft_revision=0))
        _expect_error(excinfo, "invalid_revision", "$.expected_draft_revision")

    def test_bool_expected_revision_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_save_command(_command(expected_draft_revision=True))  # type: ignore[arg-type]  # 合同测试：bool 端点必须拒绝
        _expect_error(excinfo, "invalid_revision", "$.expected_draft_revision")

    def test_command_ref_revision_must_agree(self) -> None:
        command = _command(expected_draft_revision=4)
        with pytest.raises(AgentValidationError) as excinfo:
            encode_draft_save_command(command)
        _expect_error(excinfo, "invalid_revision", "$.command.expected_revision")

    def test_decode_rejects_mismatched_command_ref(self) -> None:
        wire = encode_draft_save_command(_command())
        wire["expected_draft_revision"] = 4
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_save_command(wire)
        _expect_error(excinfo, "invalid_revision", "$.draft_save_command.command.expected_revision")

    def test_extra_key_rejected(self) -> None:
        wire = encode_draft_save_command(_command())
        wire["extra"] = 1
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_save_command(wire)
        _expect_error(excinfo, "unknown_field", "$.draft_save_command")

    def test_validate_helper_rejects_invalid_payload(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            validate_draft_save_command(_command(payload=_payload(cutoff="bad")))
        _expect_error(excinfo, "invalid_cutoff", "$.payload.cutoff")


class TestDraftInputDigest:
    def test_same_input_same_digest(self) -> None:
        assert draft_save_input_digest(_command()) == draft_save_input_digest(_command())

    def test_text_change_changes_digest(self) -> None:
        assert draft_save_input_digest(_command()) != draft_save_input_digest(
            _command(payload=_payload(text="研究两家公司"))
        )

    def test_expected_revision_change_changes_digest(self) -> None:
        other = _command(
            expected_draft_revision=4,
            command=replace(_command().command, expected_revision=4),
        )
        assert draft_save_input_digest(_command()) != draft_save_input_digest(other)


class TestDraftReceiptCodec:
    def _receipt(self) -> DraftReceipt:
        return DraftReceipt(
            session_id="s1",
            command_record_id="cmd-rec-1",
            commit_state=CommitState.COMMITTED,
            draft_revision=4,
            verified_at="2026-11-14T01:02:03.000004Z",
            error=None,
        )

    def test_round_trip(self) -> None:
        assert decode_draft_receipt(encode_draft_receipt(self._receipt())) == self._receipt()

    def test_error_code_preserved(self) -> None:
        receipt = replace(self._receipt(), error=AgentErrorCode.REVISION_CONFLICT)
        assert decode_draft_receipt(encode_draft_receipt(receipt)).error is AgentErrorCode.REVISION_CONFLICT

    def test_bad_commit_state_rejected(self) -> None:
        wire = encode_draft_receipt(self._receipt())
        wire["commit_state"] = "maybe"
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_receipt(wire)
        _expect_error(excinfo, "invalid_commit_state", "$.draft_receipt.commit_state")

    def test_non_utc_timestamp_rejected(self) -> None:
        wire = encode_draft_receipt(self._receipt())
        wire["verified_at"] = "2026-11-14T01:02:03+08:00"
        with pytest.raises(AgentValidationError) as excinfo:
            decode_draft_receipt(wire)
        _expect_error(excinfo, "invalid_timestamp", "$.draft_receipt.verified_at")
