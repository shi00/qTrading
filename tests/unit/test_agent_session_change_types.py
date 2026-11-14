"""会话元数据命令契约单测（F-03-2；DD §4.2 SessionChangeCommand、§9.3）。

覆盖：动作自身字段约束（title 仅 create/rename、其余携带即拒绝）、
create 预期版必须 0、非 create 必须 ≥1、命令引用与预期会话版一致、
revise/branch 未落地即显式拒绝、输入摘要稳定。
"""

from __future__ import annotations

from typing import Any

import pytest

from core.agent_types import (
    AgentValidationError,
    CommandRef,
    SessionAction,
    SessionChangeCommand,
    decode_session_change_command,
    encode_session_change_command,
    session_change_input_digest,
    validate_session_change_command,
)


def _ref(expected: int = 0) -> CommandRef:
    return CommandRef(
        user_scope="u1",
        source_module="ui",
        business_request_id="req-1",
        command_id="cmd-1",
        expected_revision=expected,
    )


def _command(**overrides: Any) -> SessionChangeCommand:
    values: dict[str, Any] = {
        "command": _ref(0),
        "action": SessionAction.CREATE,
        "session_id": "s1",
        "expected_session_revision": 0,
        "title": "新会话",
    }
    values.update(overrides)
    return SessionChangeCommand(**values)


def _expect_error(excinfo: pytest.ExceptionInfo[AgentValidationError], code: str, fragment: str) -> None:
    assert excinfo.value.code == code, f"expect {code}, got {excinfo.value.code}"
    assert fragment in excinfo.value.field_path


class TestSessionChangeCodec:
    @pytest.mark.parametrize(
        ("action", "title"),
        [
            (SessionAction.CREATE, "新会话"),
            (SessionAction.RENAME, "改名后"),
            (SessionAction.PIN, None),
            (SessionAction.ARCHIVE, None),
            (SessionAction.UNARCHIVE, None),
        ],
    )
    def test_round_trip_supported_actions(self, action: SessionAction, title: str | None) -> None:
        expected = 0 if action is SessionAction.CREATE else 3
        command = _command(command=_ref(expected), action=action, expected_session_revision=expected, title=title)
        assert decode_session_change_command(encode_session_change_command(command)) == command

    def test_create_requires_zero_expected_revision(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(command=_ref(1), expected_session_revision=1))
        _expect_error(excinfo, "invalid_revision", "$.expected_session_revision")

    @pytest.mark.parametrize("action", [SessionAction.RENAME, SessionAction.PIN, SessionAction.ARCHIVE])
    def test_non_create_requires_positive_expected_revision(self, action: SessionAction) -> None:
        title = "改名后" if action is SessionAction.RENAME else None
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(
                _command(command=_ref(0), action=action, expected_session_revision=0, title=title)
            )
        _expect_error(excinfo, "invalid_revision", "$.expected_session_revision")

    def test_command_ref_revision_must_agree(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(
                _command(command=_ref(2), expected_session_revision=1, action=SessionAction.RENAME)
            )
        _expect_error(excinfo, "invalid_revision", "$.command.expected_revision")

    def test_create_and_rename_require_title(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(title=None))
        _expect_error(excinfo, "missing_field", "$.title")
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(
                _command(command=_ref(1), action=SessionAction.RENAME, expected_session_revision=1, title=None)
            )
        _expect_error(excinfo, "missing_field", "$.title")

    @pytest.mark.parametrize("action", [SessionAction.PIN, SessionAction.ARCHIVE, SessionAction.UNARCHIVE])
    def test_non_title_actions_reject_title(self, action: SessionAction) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(
                _command(command=_ref(1), action=action, expected_session_revision=1, title="多余标题")
            )
        _expect_error(excinfo, "invalid_value", "$.title")

    def test_empty_title_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(_command(title=""))
        _expect_error(excinfo, "invalid_value", "$.title")

    @pytest.mark.parametrize("action", [SessionAction.REVISE, SessionAction.BRANCH])
    def test_revise_and_branch_explicitly_unsupported(self, action: SessionAction) -> None:
        command = _command(command=_ref(1), action=action, expected_session_revision=1, title=None)
        with pytest.raises(AgentValidationError) as excinfo:
            encode_session_change_command(command)
        _expect_error(excinfo, "unsupported_action", "$.action")

    def test_unknown_action_rejected(self) -> None:
        wire = encode_session_change_command(_command())
        wire["action"] = "delete"
        with pytest.raises(AgentValidationError) as excinfo:
            decode_session_change_command(wire)
        _expect_error(excinfo, "invalid_action", "$.session_change_command.action")

    def test_extra_key_rejected(self) -> None:
        wire = encode_session_change_command(_command())
        wire["extra"] = 1
        with pytest.raises(AgentValidationError) as excinfo:
            decode_session_change_command(wire)
        _expect_error(excinfo, "unknown_field", "$.session_change_command")

    def test_decode_enforces_action_fields(self) -> None:
        wire = encode_session_change_command(_command())
        wire["action"] = "pin"
        wire["expected_session_revision"] = 1
        wire["command"]["expected_revision"] = 1
        # create 的标题在 pin 上属越界字段：解码按 action 自身字段拒绝。
        with pytest.raises(AgentValidationError) as excinfo:
            decode_session_change_command(wire)
        _expect_error(excinfo, "invalid_value", "$.session_change_command.title")

    def test_validate_helper_delegates(self) -> None:
        validate_session_change_command(_command())
        with pytest.raises(AgentValidationError) as excinfo:
            validate_session_change_command(_command(title=None))
        _expect_error(excinfo, "missing_field", "$.title")


class TestSessionChangeDigest:
    def test_same_input_same_digest(self) -> None:
        assert session_change_input_digest(_command()) == session_change_input_digest(_command())

    def test_title_and_action_change_digest(self) -> None:
        base = session_change_input_digest(_command())
        assert session_change_input_digest(_command(title="另一标题")) != base
        assert (
            session_change_input_digest(
                _command(command=_ref(1), action=SessionAction.PIN, expected_session_revision=1, title=None)
            )
            != base
        )
