"""Agent 服务层命令/回执合同测试（任务 C-02-1，设计正本 §4.1/§4.2、HANDOFF §4.1）。

- AgentErrorCode：HANDOFF §4.1 固定 12 码唯一 wire 拼写；
- DeliveryState/ConsumptionState：本轮工程冻结值（§5.1 语义约束 + §3.1 命名
  风格），与 C-01 检查点⑥ 的「待冻结列」一一对应；
- CommandRef/CommandReceipt/SubmissionReceipt：全键必填、禁额外键、错误
  tag/null/type/bool 拒绝（§3.3.1 语法基线）；
- 回执语义：保存/投递/消费事实分列（INV-06），不把保存成功显示成已生效。
"""

from dataclasses import replace

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    CommandReceipt,
    CommandRef,
    CommitState,
    ConsumptionState,
    DeliveryState,
    Mode,
    SubmissionReceipt,
    decode_command_receipt,
    decode_command_ref,
    decode_submission_receipt,
    encode_command_receipt,
    encode_command_ref,
    encode_submission_receipt,
)

_T = "2026-11-12T08:00:00.000000Z"


def _ref() -> CommandRef:
    return CommandRef(
        user_scope="u1",
        source_module="ui",
        business_request_id="req-1",
        command_id="client-1",
        expected_revision=1,
    )


def _ref_wire() -> dict[str, object]:
    return {
        "user_scope": "u1",
        "source_module": "ui",
        "business_request_id": "req-1",
        "command_id": "client-1",
        "expected_revision": 1,
    }


def _reject(fn, code: str, field_path: str) -> None:
    """强断言校验失败码与字段路径（weak-assertion 钩子合规）。"""

    with pytest.raises(ValueError) as excinfo:
        fn()
    err = excinfo.value
    assert isinstance(err, AgentValidationError)
    assert err.code == code
    assert err.field_path == field_path


class TestAgentErrorCode:
    """HANDOFF §4.1 固定 12 码。"""

    def test_exactly_twelve_fixed_codes(self):
        expected = (
            "INPUT_INVALID",
            "INPUT_CONFLICT",
            "REVISION_CONFLICT",
            "CONFIG_UNAVAILABLE",
            "CAPABILITY_UNVERIFIED",
            "POLICY_DENIED",
            "BUDGET_BLOCKED",
            "PERSISTENCE_UNAVAILABLE",
            "OPERATION_UNKNOWN",
            "SOURCE_DISABLED",
            "NATIVE_VERSION_MISMATCH",
            "CANCELLED",
        )
        assert tuple(m.value for m in AgentErrorCode) == expected

    def test_agent_service_error_carries_fields(self):
        err = AgentServiceError(
            AgentErrorCode.INPUT_CONFLICT,
            "same intent with different input",
            "u1/req-1/client-1",
            _T,
        )
        assert err.code is AgentErrorCode.INPUT_CONFLICT
        assert err.sanitized_message == "same intent with different input"
        assert err.identity == "u1/req-1/client-1"
        assert err.last_verified_at == _T
        assert "INPUT_CONFLICT" in str(err)


class TestDeliveryConsumptionStates:
    """本轮工程冻结值与 §3.1 命名风格一致。"""

    def test_delivery_state_values(self):
        assert tuple(m.value for m in DeliveryState) == (
            "not_delivered",
            "delivered",
            "delivery_unknown",
        )

    def test_consumption_state_values(self):
        assert tuple(m.value for m in ConsumptionState) == (
            "not_consumed",
            "consumed",
            "consumption_unknown",
        )


class TestCommandRef:
    def test_wire_roundtrip(self):
        ref = _ref()
        assert decode_command_ref(encode_command_ref(ref)) == ref

    def test_zero_expected_revision_allowed_for_create(self):
        wire = _ref_wire()
        wire["expected_revision"] = 0
        assert decode_command_ref(wire).expected_revision == 0

    def test_rejects_non_dict(self):
        _reject(lambda: decode_command_ref(["not", "a", "dict"]), "invalid_type", "$.command_ref")

    def test_rejects_missing_key(self):
        wire = _ref_wire()
        del wire["command_id"]
        _reject(lambda: decode_command_ref(wire), "missing_field", "$.command_ref.command_id")

    def test_rejects_extra_key(self):
        wire = _ref_wire()
        wire["target_id"] = "sneaky"
        _reject(lambda: decode_command_ref(wire), "unknown_field", "$.command_ref.target_id")

    def test_rejects_bool_revision(self):
        wire = _ref_wire()
        wire["expected_revision"] = True
        _reject(
            lambda: decode_command_ref(wire),
            "invalid_revision",
            "$.command_ref.expected_revision",
        )

    def test_rejects_negative_revision(self):
        wire = _ref_wire()
        wire["expected_revision"] = -1
        _reject(
            lambda: decode_command_ref(wire),
            "invalid_revision",
            "$.command_ref.expected_revision",
        )


def _receipt() -> CommandReceipt:
    return CommandReceipt(
        command=_ref(),
        target_id="session-1",
        current_revision=3,
        commit_state=CommitState.COMMITTED,
        delivery_state=DeliveryState.DELIVERED,
        consumption_state=ConsumptionState.NOT_CONSUMED,
        updated_at=_T,
        error_code=None,
        next_action="read_command(command_ref)",
    )


def _receipt_wire() -> dict[str, object]:
    return {
        "command": _ref_wire(),
        "target_id": "session-1",
        "current_revision": 3,
        "commit_state": "committed",
        "delivery_state": "delivered",
        "consumption_state": "not_consumed",
        "updated_at": _T,
        "error_code": None,
        "next_action": "read_command(command_ref)",
    }


class TestCommandReceipt:
    """保存/投递/消费事实分列（INV-06）。"""

    def test_wire_roundtrip(self):
        receipt = _receipt()
        assert decode_command_receipt(encode_command_receipt(receipt)) == receipt

    def test_rejects_bad_commit_state(self):
        wire = _receipt_wire()
        wire["commit_state"] = "saved"
        _reject(lambda: decode_command_receipt(wire), "invalid_commit_state", "$.command_receipt.commit_state")

    def test_rejects_bad_delivery_state(self):
        wire = _receipt_wire()
        wire["delivery_state"] = "sent"
        _reject(lambda: decode_command_receipt(wire), "invalid_delivery_state", "$.command_receipt.delivery_state")

    def test_rejects_bad_error_code(self):
        wire = _receipt_wire()
        wire["error_code"] = "OOPS"
        _reject(lambda: decode_command_receipt(wire), "invalid_error_code", "$.command_receipt.error_code")

    def test_accepts_error_code_variant(self):
        wire = _receipt_wire()
        wire["error_code"] = "REVISION_CONFLICT"
        wire["commit_state"] = "not_committed"
        wire["delivery_state"] = "not_delivered"
        receipt = decode_command_receipt(wire)
        assert receipt.error_code is AgentErrorCode.REVISION_CONFLICT
        assert receipt.commit_state is CommitState.NOT_COMMITTED
        assert receipt.delivery_state is DeliveryState.NOT_DELIVERED

    def test_rejects_bad_timestamp(self):
        wire = _receipt_wire()
        wire["updated_at"] = "2026-11-12 08:00:00"
        _reject(lambda: decode_command_receipt(wire), "invalid_timestamp", "$.command_receipt.updated_at")

    def test_rejects_bool_current_revision(self):
        wire = _receipt_wire()
        wire["current_revision"] = False
        _reject(
            lambda: decode_command_receipt(wire),
            "invalid_revision",
            "$.command_receipt.current_revision",
        )


def _submission() -> SubmissionReceipt:
    return SubmissionReceipt(
        command=_ref(),
        command_record_id="cmd-record-1",
        session_id="session-1",
        execution_id="exec-1",
        workflow_id="wf-1",
        intent_digest="a" * 64,
        commit_state=CommitState.COMMITTED,
        verified_at=_T,
        next_action=None,
    )


def _submission_wire() -> dict[str, object]:
    return {
        "command": _ref_wire(),
        "command_record_id": "cmd-record-1",
        "session_id": "session-1",
        "execution_id": "exec-1",
        "workflow_id": "wf-1",
        "intent_digest": "a" * 64,
        "commit_state": "committed",
        "verified_at": _T,
        "next_action": None,
    }


class TestSubmissionReceipt:
    """同命令重传返回原回执（SQ-01/§3.3.2）。"""

    def test_wire_roundtrip(self):
        receipt = _submission()
        assert decode_submission_receipt(encode_submission_receipt(receipt)) == receipt
        assert "mode" not in encode_submission_receipt(receipt)

    def test_wire_roundtrip_keeps_authoritative_mode(self):
        """受理回执携带权威模式（P1-03）：派发据它决定是否启动研究。"""

        receipt = SubmissionReceipt(
            command=_submission().command,
            command_record_id="cmd-record-1",
            session_id="session-1",
            execution_id="exec-1",
            workflow_id="wf-1",
            intent_digest="a" * 64,
            commit_state=CommitState.COMMITTED,
            verified_at=_T,
            next_action=None,
            mode=Mode.DISCUSS,
        )
        assert decode_submission_receipt(encode_submission_receipt(receipt)) == receipt

    def test_legacy_wire_without_mode_decodes_to_unknown(self):
        """历史回执无 mode → None（不伪造默认值，R21）。"""

        assert decode_submission_receipt(_submission_wire()).mode is None

    def test_wire_roundtrip_keeps_authoritative_input_revision(self):
        """受理回执携带权威输入版（同执行排队修订后为 2、3…）。"""

        receipt = replace(_submission(), input_revision=2)
        wire = encode_submission_receipt(receipt)
        assert wire["input_revision"] == 2
        assert decode_submission_receipt(wire) == receipt

    def test_legacy_wire_without_input_revision_decodes_to_accept_authority(self):
        """历史受理回执无 input_revision → 1（受理命令 input_revision 恒为 1，唯一权威值）。"""

        assert decode_submission_receipt(_submission_wire()).input_revision == 1

    def test_rejects_non_positive_input_revision(self):
        wire = _submission_wire()
        wire["input_revision"] = 0
        _reject(
            lambda: decode_submission_receipt(wire),
            "invalid_revision",
            "$.submission_receipt.input_revision",
        )

    def test_rejects_unknown_mode(self):
        wire = _submission_wire()
        wire["mode"] = "modify"
        _reject(
            lambda: decode_submission_receipt(wire),
            "invalid_tag",
            "$.submission_receipt.mode",
        )

    def test_rejects_bad_intent_digest(self):
        wire = _submission_wire()
        wire["intent_digest"] = "not-a-digest"
        _reject(
            lambda: decode_submission_receipt(wire),
            "invalid_digest",
            "$.submission_receipt.intent_digest",
        )

    def test_rejects_extra_key(self):
        wire = _submission_wire()
        wire["accepted"] = True
        _reject(lambda: decode_submission_receipt(wire), "unknown_field", "$.submission_receipt.accepted")

    def test_rejects_bad_commit_state(self):
        wire = _submission_wire()
        wire["commit_state"] = "commit_unknown_typo"
        _reject(
            lambda: decode_submission_receipt(wire),
            "invalid_commit_state",
            "$.submission_receipt.commit_state",
        )
