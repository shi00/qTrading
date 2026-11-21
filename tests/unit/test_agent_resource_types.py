"""Agent C-04 资源/源禁用/阅读批次 core 合同类型测试（设计正本 §5.4/§5.5/§9.11/§4.2 L436）。

- ResourceSlotKind：total/leaf/global_guard 三值，guard 不经认领入口（§9.11）；
- ResourceClaimRequest/ResourceTicket/ResourceRelease*：票据全键（§5.5），
  slot_id 保留键拒绝，owner_epoch ≥1，round_id 可选；
- UsageCounterUpdate/Fact：绝对累计 GREATEST 语义（R22），active_ms 为可靠下界；
- SourceStopCommand/SourceClaimRequest/SourceConsumersPage：源禁用与共同仲裁
  合同（§3.3.2/§5.4），claim_ref 为幂等重放键；
- ReadMarkCommand/ReadMarkReceipt：准确身份或经核连续水位二选一，scope digest
  同给同缺（§4.2 L436），不全局清零；
- DecisionBatchFact：批次/稍后水位分列（AC33/RR04）。
"""

import dataclasses
import json

import pytest

from core.agent_types import (
    AgentValidationError,
    DecisionBatchFact,
    ReadMarkCommand,
    ReadMarkReceipt,
    ResourceClaimRequest,
    ResourceReleaseReceipt,
    ResourceReleaseRequest,
    ResourceSlotKind,
    ResourceTicket,
    SourceClaimBinding,
    SourceClaimFact,
    SourceClaimRequest,
    SourceConsumerRef,
    SourceConsumersPage,
    SourceKind,
    SourceStopCommand,
    SourceStopReceipt,
    SourceVersionRef,
    UsageCounterFact,
    UsageCounterUpdate,
    validate_read_mark_command,
    validate_resource_claim_request,
)

_T = "2026-11-12T08:00:00.000000Z"
_DIGEST = "a" * 64


def _reject(fn, code: str, field_path: str) -> None:
    """强断言校验失败码与字段路径（weak-assertion 钩子合规）。"""

    with pytest.raises(ValueError) as excinfo:
        fn()
    err = excinfo.value
    assert isinstance(err, AgentValidationError)
    assert err.code == code, f"expect {code}, got {err.code}"
    assert err.field_path == field_path, f"expect {field_path}, got {err.field_path}"


def _claim_request(**overrides) -> ResourceClaimRequest:
    values: dict = {
        "user_scope": "u1",
        "slot_id": "slot-1",
        "slot_kind": ResourceSlotKind.TOTAL,
        "execution_id": "exec-1",
        "node_id": "root",
        "round_id": None,
        "owner_epoch": 1,
        "operation_refs": None,
    }
    values.update(overrides)
    return ResourceClaimRequest(**values)


# ------------------------------------------------------------------
# ResourceSlotKind 与资源认领校验
# ------------------------------------------------------------------


class TestResourceSlotKind:
    def test_wire_values(self) -> None:
        assert ResourceSlotKind.TOTAL.value == "total"
        assert ResourceSlotKind.LEAF.value == "leaf"
        assert ResourceSlotKind.GLOBAL_GUARD.value == "global_guard"


class TestValidateResourceClaimRequest:
    def test_valid_total_without_round(self) -> None:
        validate_resource_claim_request(_claim_request())

    def test_valid_leaf_with_round(self) -> None:
        validate_resource_claim_request(
            _claim_request(slot_kind=ResourceSlotKind.LEAF, execution_id="leaf-1", node_id="node-a", round_id="r1")
        )

    def test_guard_kind_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(slot_kind=ResourceSlotKind.GLOBAL_GUARD)),
            "guard_not_claimable",
            "$.slot_kind",
        )

    def test_reserved_slot_id_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(slot_id="global_guard")),
            "reserved_slot_id",
            "$.slot_id",
        )

    def test_empty_user_scope_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(user_scope="")), "invalid_identity", "$.user_scope"
        )

    def test_empty_slot_id_rejected(self) -> None:
        _reject(lambda: validate_resource_claim_request(_claim_request(slot_id="")), "invalid_identity", "$.slot_id")

    def test_owner_epoch_zero_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(owner_epoch=0)), "invalid_range", "$.owner_epoch"
        )

    def test_operation_refs_non_dict_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(operation_refs=["op-1"])),
            "invalid_type",
            "$.operation_refs",
        )

    def test_operation_refs_dict_rejected(self) -> None:
        # 唯一载形为 CanonicalJson 文本；结构化 dict 不再被接受（载形分叉负面用例）。
        _reject(
            lambda: validate_resource_claim_request(_claim_request(operation_refs={"operation_id": "op-1"})),
            "invalid_type",
            "$.operation_refs",
        )

    def test_operation_refs_non_empty_canonical_text_accepted(self) -> None:
        validate_resource_claim_request(_claim_request(operation_refs='{"operation_id":"op-1","tool_call_id":"tc-1"}'))

    def test_operation_refs_nested_canonical_text_accepted(self) -> None:
        validate_resource_claim_request(_claim_request(operation_refs='{"a":{"b":1},"c":[true,null]}'))

    def test_operation_refs_empty_object_accepted(self) -> None:
        validate_resource_claim_request(_claim_request(operation_refs="{}"))

    def test_operation_refs_round_trip_jsonb_object(self) -> None:
        # wire（CanonicalJson 文本）→ JSONB 对象：解析与原规范字节互复原。
        request = _claim_request(operation_refs='{"operation_id":"op-1","tool_call_id":"tc-1"}')
        validate_resource_claim_request(request)
        assert request.operation_refs is not None
        decoded = json.loads(request.operation_refs)
        assert decoded == {"operation_id": "op-1", "tool_call_id": "tc-1"}
        assert json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":")) == request.operation_refs

    @pytest.mark.parametrize("refs", ['["op-1"]', '"op-1"', "1", "null", "true"])
    def test_operation_refs_non_object_text_rejected(self, refs: str) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(operation_refs=refs)),
            "invalid_type",
            "$.operation_refs",
        )

    @pytest.mark.parametrize(
        "refs",
        [
            "{bad",  # 坏 JSON
            '{"n":NaN}',  # 非有限数
            '{"a":1,"a":1}',  # 重复键
            '{"a": 1}',  # 空白非规范
            '{"b":1,"a":2}',  # 键序非规范
            '{"n":1.50}',  # 数值词法非规范
        ],
    )
    def test_operation_refs_non_canonical_text_rejected(self, refs: str) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(operation_refs=refs)),
            "invalid_json",
            "$.operation_refs",
        )

    def test_empty_execution_id_rejected(self) -> None:
        _reject(
            lambda: validate_resource_claim_request(_claim_request(execution_id="")),
            "invalid_identity",
            "$.execution_id",
        )

    def test_empty_node_id_rejected(self) -> None:
        _reject(lambda: validate_resource_claim_request(_claim_request(node_id="")), "invalid_identity", "$.node_id")


# ------------------------------------------------------------------
# 票据/回执/计数 frozen 合同
# ------------------------------------------------------------------


class TestResourceTickets:
    def test_ticket_construction(self) -> None:
        ticket = ResourceTicket(
            slot_id="slot-1",
            slot_kind=ResourceSlotKind.LEAF,
            execution_id="leaf-1",
            node_id="node-a",
            round_id="r1",
            owner_epoch=2,
            run_epoch=1,
            claimed_at=_T,
        )
        assert ticket.slot_kind is ResourceSlotKind.LEAF
        assert ticket.round_id == "r1"

    def test_release_request_exit_verified_required_field(self) -> None:
        request = ResourceReleaseRequest(
            user_scope="u1",
            slot_id="slot-1",
            execution_id="exec-1",
            node_id="root",
            owner_epoch=1,
            exit_verified=False,
        )
        assert request.exit_verified is False

    def test_release_receipt_reserved_unknown(self) -> None:
        receipt = ResourceReleaseReceipt(
            slot_id="slot-1", released=False, release_verified=False, released_at=_T, owner_epoch=1
        )
        assert receipt.released is False
        assert receipt.release_verified is False

    def test_frozen(self) -> None:
        ticket = ResourceTicket(
            slot_id="s",
            slot_kind=ResourceSlotKind.TOTAL,
            execution_id="e",
            node_id="n",
            round_id=None,
            owner_epoch=1,
            run_epoch=1,
            claimed_at=_T,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):  # noqa: weak-assertion  # FrozenInstanceError 抛出点即完整语义，块外断言原值不变
            ticket.owner_epoch = 2  # type: ignore[misc]  # frozen 数据类拒绝就地修改
        assert ticket.owner_epoch == 1  # 拒绝修改后原值保持


class TestUsageCounters:
    def test_update_absolute_cumulative(self) -> None:
        update = UsageCounterUpdate(
            user_scope="u1", execution_id="exec-1", model_attempt_count=10, tool_call_count=5, active_ms=1200
        )
        assert update.model_attempt_count == 10

    def test_fact_landed_values(self) -> None:
        fact = UsageCounterFact(execution_id="exec-1", model_attempt_count=10, tool_call_count=5, active_ms=1200)
        assert fact.active_ms == 1200


# ------------------------------------------------------------------
# 源禁用与共同仲裁合同
# ------------------------------------------------------------------


class TestSourceStopAndClaim:
    def test_stop_command_construction(self) -> None:
        command = SourceStopCommand(user_scope="u1", source_id="src-1", source_version="1", command_id="client-1")
        assert command.source_version == "1"

    def test_stop_receipt(self) -> None:
        receipt = SourceStopReceipt(
            source_id="src-1", source_version="1", disabled_revision=3, already_disabled=False, disabled_at=_T
        )
        assert receipt.disabled_revision == 3

    def test_claim_request_sources(self) -> None:
        request = SourceClaimRequest(
            user_scope="u1",
            claim_ref="claim-1",
            operation_id="op-1",
            sources=(
                SourceVersionRef(
                    source_id="src-1",
                    source_version="1",
                    source_kind=SourceKind.USER_MESSAGE,
                    source_digest="b" * 64,
                ),
            ),
        )
        assert len(request.sources) == 1

    def test_claim_fact_binding(self) -> None:
        fact = SourceClaimFact(
            claim_ref="claim-1",
            bindings=(
                SourceClaimBinding(
                    source_id="s",
                    source_version="1",
                    source_kind=SourceKind.USER_MESSAGE,
                    source_digest="b" * 64,
                    disabled_revision=0,
                ),
            ),
            replayed=False,
        )
        assert fact.replayed is False
        assert fact.bindings[0].disabled_revision == 0

    def test_consumers_page(self) -> None:
        consumer = SourceConsumerRef(
            link_id="link-1",
            source_id="src-1",
            source_version="1",
            consumer_kind="message",
            consumer_id="m-1",
            consumer_version="1",
            relation_kind="user_message_origin",
            disabled_inherited=True,
        )
        page = SourceConsumersPage(source_id="src-1", source_version="1", consumers=(consumer,), read_moment=_T)
        assert page.consumers[0].disabled_inherited is True


# ------------------------------------------------------------------
# 阅读批次合同
# ------------------------------------------------------------------


class TestReadMark:
    def test_exact_ids_only(self) -> None:
        command = ReadMarkCommand(
            user_scope="u1",
            session_id="s1",
            command_id="client-1",
            message_ids=("m-1", "m-2"),
            artifact_refs=(),
            observed_through_seq=None,
            observation_scope_digest=None,
        )
        validate_read_mark_command(command)

    def test_watermark_with_digest_valid(self) -> None:
        command = ReadMarkCommand(
            user_scope="u1",
            session_id="s1",
            command_id="client-1",
            message_ids=(),
            artifact_refs=("a-1@2",),
            observed_through_seq=42,
            observation_scope_digest=_DIGEST,
        )
        validate_read_mark_command(command)

    def test_seq_without_digest_rejected(self) -> None:
        _reject(
            lambda: validate_read_mark_command(
                ReadMarkCommand(
                    user_scope="u1",
                    session_id="s1",
                    command_id="client-1",
                    message_ids=(),
                    artifact_refs=(),
                    observed_through_seq=5,
                    observation_scope_digest=None,
                )
            ),
            "scope_pair_required",
            "$.observed_through_seq",
        )

    def test_digest_without_seq_rejected(self) -> None:
        _reject(
            lambda: validate_read_mark_command(
                ReadMarkCommand(
                    user_scope="u1",
                    session_id="s1",
                    command_id="client-1",
                    message_ids=(),
                    artifact_refs=(),
                    observed_through_seq=None,
                    observation_scope_digest=_DIGEST,
                )
            ),
            "scope_pair_required",
            "$.observed_through_seq",
        )

    def test_negative_seq_rejected(self) -> None:
        _reject(
            lambda: validate_read_mark_command(
                ReadMarkCommand(
                    user_scope="u1",
                    session_id="s1",
                    command_id="client-1",
                    message_ids=(),
                    artifact_refs=(),
                    observed_through_seq=-1,
                    observation_scope_digest=_DIGEST,
                )
            ),
            "invalid_range",
            "$.observed_through_seq",
        )

    def test_bad_digest_rejected(self) -> None:
        _reject(
            lambda: validate_read_mark_command(
                ReadMarkCommand(
                    user_scope="u1",
                    session_id="s1",
                    command_id="client-1",
                    message_ids=(),
                    artifact_refs=(),
                    observed_through_seq=5,
                    observation_scope_digest="not-a-digest",
                )
            ),
            "invalid_digest",
            "$.observation_scope_digest",
        )

    def test_empty_session_rejected(self) -> None:
        _reject(
            lambda: validate_read_mark_command(
                ReadMarkCommand(
                    user_scope="u1",
                    session_id="",
                    command_id="client-1",
                    message_ids=(),
                    artifact_refs=(),
                    observed_through_seq=None,
                    observation_scope_digest=None,
                )
            ),
            "invalid_identity",
            "$.session_id",
        )

    def test_receipt(self) -> None:
        receipt = ReadMarkReceipt(
            session_id="s1", command_id="client-1", last_seq=7, read_target_count=2, replayed=True, marked_at=_T
        )
        assert receipt.replayed is True


class TestDecisionBatchFact:
    def test_open_fact(self) -> None:
        fact = DecisionBatchFact(batch_seq=3, postponed_through_seq=None, replayed=False, recorded_at=_T)
        assert fact.postponed_through_seq is None

    def test_postpone_fact(self) -> None:
        fact = DecisionBatchFact(batch_seq=3, postponed_through_seq=3, replayed=False, recorded_at=_T)
        assert fact.postponed_through_seq == 3
