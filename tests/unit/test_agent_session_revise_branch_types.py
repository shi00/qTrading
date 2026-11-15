"""会话修订/分支契约单测（F-03-2b；DD §4.2 L436、F29、§9.3 session 列）。

覆盖：revise/branch 纳入支持集、8 键 wire 往返、源消息身份/版本必填、
因果截点可选、branch 必须新建（预期版 0）且需要标题、revise 不接受标题、
其他动作携带源字段即拒绝、坏版本与空身份拒绝、输入摘要覆盖新增字段。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import (
    AgentValidationError,
    CommandRef,
    SessionAction,
    SessionChangeCommand,
    SUPPORTED_SESSION_ACTIONS,
    decode_session_change_command,
    encode_session_change_command,
    session_change_input_digest,
    validate_session_change_command,
)

_SESSION_CHANGE_WIRE_KEYS = {
    "command",
    "action",
    "session_id",
    "expected_session_revision",
    "title",
    "source_message_id",
    "source_message_revision",
    "causal_cutoff_message_id",
}


def _ref(expected: int = 1) -> CommandRef:
    return CommandRef(
        user_scope="u1",
        source_module="ui",
        business_request_id="req-1",
        command_id="cmd-1",
        expected_revision=expected,
    )


def _command(**overrides: Any) -> SessionChangeCommand:
    values: dict[str, Any] = {
        "command": _ref(1),
        "action": SessionAction.REVISE,
        "session_id": "s1",
        "expected_session_revision": 1,
        "title": None,
        "source_message_id": "m3",
        "source_message_revision": 2,
        "causal_cutoff_message_id": None,
    }
    values.update(overrides)
    return SessionChangeCommand(**values)


def _expect_error(excinfo: pytest.ExceptionInfo[AgentValidationError], code: str, field: str) -> None:
    assert excinfo.value.code == code, f"expect {code}, got {excinfo.value.code}"
    assert excinfo.value.field_path == field


class TestReviseBranchCodec:
    def test_revise_and_branch_are_supported(self) -> None:
        assert SessionAction.REVISE in SUPPORTED_SESSION_ACTIONS
        assert SessionAction.BRANCH in SUPPORTED_SESSION_ACTIONS

    def test_revise_round_trip(self) -> None:
        command = _command(causal_cutoff_message_id="m2")
        wire = encode_session_change_command(command)

        assert set(wire) == _SESSION_CHANGE_WIRE_KEYS
        assert wire["source_message_id"] == "m3"
        assert wire["source_message_revision"] == 2
        assert wire["causal_cutoff_message_id"] == "m2"
        assert decode_session_change_command(wire) == command

    def test_branch_round_trip_requires_new_session(self) -> None:
        command = _command(
            command=_ref(0),
            action=SessionAction.BRANCH,
            session_id="s-branch",
            expected_session_revision=0,
            title="分支会话",
            causal_cutoff_message_id="m2",
        )
        wire = encode_session_change_command(command)

        assert wire["action"] == "branch"
        assert wire["expected_session_revision"] == 0
        assert decode_session_change_command(wire) == command

    def test_revise_requires_source_message_identity(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(source_message_id=None))
        _expect_error(excinfo, "invalid_identity", "$.source_message_id")

    def test_revise_requires_source_message_version(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(source_message_revision=None))
        _expect_error(excinfo, "invalid_revision", "$.source_message_revision")

    @pytest.mark.parametrize("value", [0, True, 2.0])
    def test_source_message_version_rejects_bad_values(self, value: Any) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(source_message_revision=value))
        _expect_error(excinfo, "invalid_revision", "$.source_message_revision")

    def test_revise_rejects_title(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(title="不应有标题"))
        _expect_error(excinfo, "invalid_value", "$.title")

    def test_branch_requires_title(self) -> None:
        command = _command(
            command=_ref(0),
            action=SessionAction.BRANCH,
            session_id="s-branch",
            expected_session_revision=0,
            title=None,
        )
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(command)
        _expect_error(excinfo, "missing_field", "$.title")

    def test_branch_must_create_new_session(self) -> None:
        command = _command(
            command=_ref(1),
            action=SessionAction.BRANCH,
            expected_session_revision=1,
            title="分支会话",
        )
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(command)
        _expect_error(excinfo, "invalid_revision", "$.expected_session_revision")

    @pytest.mark.parametrize(
        "action",
        [SessionAction.CREATE, SessionAction.RENAME, SessionAction.PIN, SessionAction.ARCHIVE, SessionAction.UNARCHIVE],
    )
    def test_other_actions_reject_source_fields(self, action: SessionAction) -> None:
        expected = 0 if action is SessionAction.CREATE else 1
        title = "标题" if action in (SessionAction.CREATE, SessionAction.RENAME) else None
        command = _command(
            command=_ref(expected),
            action=action,
            expected_session_revision=expected,
            title=title,
            source_message_id="m3",
        )
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(command)
        _expect_error(excinfo, "invalid_value", "$.source_message_id")

    def test_other_actions_reject_cutoff_only(self) -> None:
        command = _command(
            action=SessionAction.PIN,
            source_message_id=None,
            source_message_revision=None,
            causal_cutoff_message_id="m2",
        )
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(command)
        _expect_error(excinfo, "invalid_value", "$.causal_cutoff_message_id")

    def test_decode_round_trips_and_rejects_bad_types(self) -> None:
        wire = encode_session_change_command(_command())
        wire["source_message_revision"] = "2"
        with pytest.raises(AgentValidationError) as excinfo:
            decode_session_change_command(wire)
        _expect_error(excinfo, "invalid_type", "$.session_change_command.source_message_revision")

    def test_missing_new_key_rejected(self) -> None:
        wire = encode_session_change_command(_command())
        wire.pop("source_message_id")
        with pytest.raises(AgentValidationError) as excinfo:
            decode_session_change_command(wire)
        _expect_error(excinfo, "missing_field", "$.session_change_command.source_message_id")

    def test_validate_helper_covers_source_fields(self) -> None:
        validate_session_change_command(_command())
        with pytest.raises(AgentValidationError) as excinfo:
            validate_session_change_command(_command(session_id=""))
        _expect_error(excinfo, "invalid_identity", "$.session_id")


class TestReviseBranchDigest:
    def test_same_input_same_digest(self) -> None:
        assert session_change_input_digest(_command()) == session_change_input_digest(_command())

    def test_source_identity_and_cutoff_change_digest(self) -> None:
        base = session_change_input_digest(_command())
        assert session_change_input_digest(_command(source_message_id="m4")) != base
        assert session_change_input_digest(_command(source_message_revision=3)) != base
        assert session_change_input_digest(_command(causal_cutoff_message_id="m1")) != base
