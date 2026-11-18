"""A-01-b DataConsent v1 编码合同测试（详细设计 §9.8 R04 v1 编码 + §10.2）。

正本约束：

- ``DataConsentBinding`` 必含全部 20 键，无可省略键/额外键；可空矩阵按 §9.8
  「v1外层类型与可空矩阵」；state→(approver_ref, valid_from, expires_at) 编码
  形态矩阵按 §9.8 表格（pending 双 null；approved S+T；revoked/expired 终态
  同空或同非空；expired 的 expires_at 必须非 null）；
- 源项 10 键沿 §3.3.1 类型；``source_scope_digest=sha256(CanonicalJson(
  {"schema_version":1,"source_selections":规范源项数组}))``，源项按各项
  CanonicalJson 的 UTF-8 字节排序，重复项拒绝（[A,A] 必须拒绝，不先去重）；
- decode 重算 selected_range_digest / source_scope_digest，不信调用方 digest；
- AR-09 本地目标映射：仅 ``gguf_chat``；规范字节向量与三个文档 digest 向量；
- 规范字节向量（§9.8 文档）：ALL / ROWS_2_3 / [A,B] 组合三个 source_scope_digest。

本测试只见证文档编码数学，不证明生产 codec/owner/worker 接线通过（§9.8）。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from core.agent_types import (
    AgentValidationError,
    CanonicalJson,
    CommandRef,
    ConnectionAuthorizationRef,
    DataAttemptAuthorization,
    DataAttemptOutcome,
    DataConsentClaim,
    DataConsentCommand,
    DataConsentRevokeCommand,
    data_consent_source_scope_digest,
    decode_data_consent_binding,
    decode_data_consent_claim,
    decode_data_consent_source_item,
    derive_data_consent_command_identity,
    derive_data_consent_revoke_identity,
    encode_data_consent_binding,
    encode_data_consent_claim,
    evaluate_data_attempt,
    json_doc,
    local_target_fingerprint,
    validate_data_attempt_authorization,
    validate_data_consent_claim,
)

# ---------------------------------------------------------------------------
# §9.8 文档规范字节向量
# ---------------------------------------------------------------------------

SRC_A_DIGEST = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
SRC_B_DIGEST = "0c7519a734cd41ac2c8fcc599c713091d0dddb2ccd90ecad7f30e4d9575aed40"
ALL_SELECTED_RANGE_DIGEST = "548d12d686601e50452f7cd140e44b26138e8c0f41dc52a68a32081cf8e34f31"
ALL_SCOPE_DIGEST = "29aa96ad9f423ef236d5e2b8b06f566cbc69ac5385ef10d6dcd6e36dc44f83fa"
ROWS_SELECTED_RANGE_DIGEST = "25dd99892404b7e940dd256a4b2d2c05737411aa4a00866776ae636dada463ce"
ROWS_SCOPE_DIGEST = "79b2f71b7320fa6af4be2a36045fc09e9c82ecd2df482a330be1dcab28776775"
COMBINED_SCOPE_DIGEST = "b98716ba8497323346bd3b42fcbd7b6dbfbe41e390fa55a178d358a5a1fe34d7"

#: AR-09 规范字节向量（无 BOM/末尾换行的准确 UTF-8 字节参与 SHA-256）。
LOCAL_FP_A_R2 = "local-model-v1:cbd93ad05f977479f840e79852cd2bfb9b123c327ac6470ccd4cc5bd9f3d18ca"
LOCAL_FP_B_R2 = "local-model-v1:f8aa6d98cab4db2001c0b311993bdc043370aec814509e30d19c0ad15c100f28"
LOCAL_FP_A_R3 = "local-model-v1:0a74c35fa4e2cc7bc772fc7658d755f930b4a25cafa64e6fb3a10955c779d80c"

#: 源项 wire 形态（§9.8 文档 JSON，逐键照录）。
ALL_ITEM: dict[str, Any] = {
    "locator": {
        "locator_kind": "source_range",
        "origin_ref": {
            "source_digest": SRC_A_DIGEST,
            "source_id": "资料-A",
            "source_kind": "attachment",
            "source_version": "v1",
        },
        "selection": {"kind": "all"},
    },
    "provenance_refs": [],
    "representation_digest": SRC_A_DIGEST,
    "representation_version": "utf8-v1",
    "selected_range": {
        "accepted_limited_version": None,
        "limitations": [],
        "read_ranges": [{"kind": "all"}],
        "selection": {"kind": "all"},
        "stats": {"chars": 3, "pages": None, "rows": None, "sheets": None, "source_bytes": 3},
        "unread_ranges": [],
    },
    "selected_range_digest": ALL_SELECTED_RANGE_DIGEST,
    "source_digest": SRC_A_DIGEST,
    "source_id": "资料-A",
    "source_kind": "attachment",
    "source_version": "v1",
}

ROWS_ITEM: dict[str, Any] = {
    "locator": {
        "locator_kind": "source_range",
        "origin_ref": {
            "source_digest": SRC_B_DIGEST,
            "source_id": "资料-B",
            "source_kind": "attachment",
            "source_version": "v1",
        },
        "selection": {
            "kind": "limited",
            "ranges": [{"end": 3, "kind": "rows", "sheet": None, "start": 2}],
        },
    },
    "provenance_refs": [],
    "representation_digest": "54351fcc23bd5dd695c78e8efb7c6fdbf87418a15547eb00f94463cc76472207",
    "representation_version": "utf8-v1",
    "selected_range": {
        "accepted_limited_version": None,
        "limitations": [],
        "read_ranges": [{"end": 3, "kind": "rows", "sheet": None, "start": 2}],
        "selection": {"kind": "limited", "ranges": [{"end": 3, "kind": "rows", "sheet": None, "start": 2}]},
        "stats": {"chars": 7, "pages": None, "rows": 2, "sheets": None, "source_bytes": 11},
        "unread_ranges": [],
    },
    "selected_range_digest": ROWS_SELECTED_RANGE_DIGEST,
    "source_digest": SRC_B_DIGEST,
    "source_id": "资料-B",
    "source_kind": "attachment",
    "source_version": "v1",
}


def _binding_payload(overrides: dict[str, Any] | None = None, source_selections: list | None = None) -> dict[str, Any]:
    """完整合法 binding payload（approved + 本地免外发路径，AR-09 语义）。"""

    payload: dict[str, Any] = {
        "schema_version": 1,
        "consent_id": "dec-consent-1",
        "consent_revision": 1,
        "state": "approved",
        "approver_ref": "user:local-owner",
        "user_scope": "synthetic-user",
        "execution_id": "exec-1",
        "round_id": "round-1",
        "valid_from": "2026-01-01T00:00:00.000000Z",
        "expires_at": None,
        "requires_egress_grant": False,
        "connection_authorization_ref": {
            "connection_id": "synthetic-local-A",
            "target_fingerprint": LOCAL_FP_A_R2,
            "purpose": "agent_research",
            "scope_version": 1,
            "verified_connection_grant_id": None,
        },
        "connection_revision": 2,
        "security_revision": 0,
        "credential_version": None,
        "target_fingerprint": LOCAL_FP_A_R2,
        "purpose": "agent_research",
        "scope_version": 1,
        "source_selections": [ALL_ITEM] if source_selections is None else source_selections,
        "source_scope_digest": ALL_SCOPE_DIGEST if source_selections is None else None,
    }
    if source_selections is None:
        payload["source_scope_digest"] = ALL_SCOPE_DIGEST
    if overrides:
        payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# AR-09 本地目标映射
# ---------------------------------------------------------------------------


def _reject(fn: Callable[[], object], *, code: str) -> None:
    """强断言 helper：调用必须拒绝且稳定错误码精确匹配（weak-assertion 形态）。"""

    with pytest.raises(AgentValidationError) as exc:
        fn()
    assert exc.value.code == code
    assert exc.value.field_path


class TestLocalTargetFingerprint:
    def test_document_vector_a_revision_2(self) -> None:
        assert local_target_fingerprint("synthetic-user", "synthetic-local-A", 2, "gguf_chat") == LOCAL_FP_A_R2

    def test_document_vector_b_revision_2(self) -> None:
        assert local_target_fingerprint("synthetic-user", "synthetic-local-B", 2, "gguf_chat") == LOCAL_FP_B_R2

    def test_document_vector_a_revision_3(self) -> None:
        assert local_target_fingerprint("synthetic-user", "synthetic-local-A", 3, "gguf_chat") == LOCAL_FP_A_R3

    def test_revision_variants_do_not_match_each_other(self) -> None:
        # 只换 connection_id / 只换 revision 均不得匹配前一许可（§9.8 AR-09）
        assert LOCAL_FP_A_R2 != LOCAL_FP_B_R2
        assert LOCAL_FP_A_R2 != LOCAL_FP_A_R3

    def test_bool_revision_rejected(self) -> None:
        # 拒 bool：True/False 是 int 子类但不是合法修订
        _reject(lambda: local_target_fingerprint("synthetic-user", "conn", True, "gguf_chat"), code="invalid_revision")

    def test_zero_and_negative_revision_rejected(self) -> None:
        _reject(lambda: local_target_fingerprint("synthetic-user", "conn", 0, "gguf_chat"), code="invalid_revision")
        _reject(lambda: local_target_fingerprint("synthetic-user", "conn", -1, "gguf_chat"), code="invalid_revision")

    def test_non_chat_adapter_kind_rejected(self) -> None:
        # 非聊天 gguf_sentiment 及云端 litellm_chat 不走此编码
        _reject(
            lambda: local_target_fingerprint("synthetic-user", "conn", 2, "gguf_sentiment"), code="invalid_adapter_kind"
        )
        _reject(
            lambda: local_target_fingerprint("synthetic-user", "conn", 2, "litellm_chat"), code="invalid_adapter_kind"
        )

    def test_empty_scope_or_connection_rejected(self) -> None:
        _reject(lambda: local_target_fingerprint("", "conn", 2, "gguf_chat"), code="invalid_identity")
        _reject(lambda: local_target_fingerprint("user", "", 2, "gguf_chat"), code="invalid_identity")


# ---------------------------------------------------------------------------
# source_scope_digest（§9.8 规范向量）
# ---------------------------------------------------------------------------


class TestSourceScopeDigest:
    def test_all_document_vector(self) -> None:
        item = decode_data_consent_source_item(ALL_ITEM, "$")
        assert data_consent_source_scope_digest([item]) == ALL_SCOPE_DIGEST

    def test_rows_document_vector(self) -> None:
        item = decode_data_consent_source_item(ROWS_ITEM, "$")
        assert data_consent_source_scope_digest([item]) == ROWS_SCOPE_DIGEST

    def test_combined_order_independent(self) -> None:
        a = decode_data_consent_source_item(ALL_ITEM, "$")
        b = decode_data_consent_source_item(ROWS_ITEM, "$")
        assert data_consent_source_scope_digest([a, b]) == COMBINED_SCOPE_DIGEST
        assert data_consent_source_scope_digest([b, a]) == COMBINED_SCOPE_DIGEST

    def test_duplicate_items_rejected_not_deduped(self) -> None:
        a = decode_data_consent_source_item(ALL_ITEM, "$")
        # [A,A] 必须拒绝，不先去重后成功（§9.8）
        _reject(lambda: data_consent_source_scope_digest([a, a]), code="duplicate_source_item")

    def test_unknown_source_item_key_rejected(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        bad["locator"]["selection"]["ranges"][0]["stop"] = 3
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="unknown_field")

    def test_unknown_provenance_key_rejected(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        bad["provenance_refs"] = [
            {
                "source": bad["locator"]["origin_ref"],
                "locator": bad["locator"],
                "selected_range_digest": ROWS_SELECTED_RANGE_DIGEST,
                "representation_version": "utf8-v1",
                "representation_digest": bad["representation_digest"],
                "grant": True,
            }
        ]
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="unknown_field")


# ---------------------------------------------------------------------------
# 源项公共入口逐值闭合（I30）
# ---------------------------------------------------------------------------


class TestSourceItemClosure:
    """源项 codec：冗余承载逐值一致、all/limited 分叉、接受版本与定位类型拒绝。"""

    def test_locator_wrong_type_rejected_as_validation_error(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        bad["locator"] = "locator"
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="invalid_type")

    def test_missing_selected_range_rejected(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        del bad["selected_range"]
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="missing_field")

    @pytest.mark.parametrize(
        ("item", "selection"),
        [
            (ALL_ITEM, {"kind": "limited", "ranges": [{"kind": "pages", "start": 1, "end": 1}]}),
            (ROWS_ITEM, {"kind": "all"}),
        ],
        ids=["all-selected-limited-locator", "limited-selected-all-locator"],
    )
    def test_all_and_limited_selection_fork_rejected(self, item: dict[str, Any], selection: dict[str, Any]) -> None:
        bad = copy.deepcopy(item)
        bad["locator"]["selection"] = selection
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="kind_mismatch")

    def test_wrong_accepted_version_rejected(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        bad["selected_range"]["accepted_limited_version"] = "v2"
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="invalid_accepted_version")

    def test_top_level_source_identity_fork_rejected(self) -> None:
        bad = copy.deepcopy(ROWS_ITEM)
        bad["source_digest"] = "d" * 64
        _reject(lambda: decode_data_consent_source_item(bad, "$"), code="identity_mismatch")


# ---------------------------------------------------------------------------
# DataConsentBinding 严格 decode
# ---------------------------------------------------------------------------


class TestBindingDecodeStrictness:
    def test_valid_payload_decodes(self) -> None:
        binding = decode_data_consent_binding(_binding_payload(), "$")
        assert binding.consent_id == "dec-consent-1"
        assert binding.state.value == "approved"
        assert binding.requires_egress_grant is False
        assert binding.security_revision == 0
        assert binding.credential_version is None

    def test_missing_key_rejected(self) -> None:
        payload = _binding_payload()
        del payload["connection_revision"]
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="missing_field")

    def test_extra_key_rejected(self) -> None:
        payload = _binding_payload({"note": "extra"})
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="unknown_field")

    def test_schema_version_only_integer_one(self) -> None:
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"schema_version": 2}), "$"),
            code="invalid_schema_version",
        )
        # 拒 bool
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"schema_version": True}), "$"),
            code="invalid_schema_version",
        )

    def test_requires_egress_grant_strict_bool(self) -> None:
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"requires_egress_grant": 1}), "$"),
            code="invalid_type",
        )
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"requires_egress_grant": "true"}), "$"),
            code="invalid_type",
        )

    def test_security_revision_zero_valid(self) -> None:
        # 原 owner 计数器边界：security_revision 初始 0、下界 0（§9.8）
        binding = decode_data_consent_binding(_binding_payload({"security_revision": 0}), "$")
        assert binding.security_revision == 0

    def test_security_revision_negative_or_bool_rejected(self) -> None:
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"security_revision": -1}), "$"),
            code="invalid_revision",
        )
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"security_revision": None}), "$"),
            code="invalid_revision",
        )
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"security_revision": False}), "$"),
            code="invalid_revision",
        )

    def test_scope_version_zero_or_bool_rejected(self) -> None:
        # scope_version 在外层与 connection_authorization_ref 内层重复出现，
        # 变体须两处一致（否则内外一致性先拒绝），验证值域本身用一致 fixture。
        def _with_scope(version: object) -> dict[str, Any]:
            payload = _binding_payload({"scope_version": version})
            payload["connection_authorization_ref"] = {
                **payload["connection_authorization_ref"],
                "scope_version": version,
            }
            return payload

        _reject(lambda: decode_data_consent_binding(_with_scope(0), "$"), code="invalid_range")
        _reject(lambda: decode_data_consent_binding(_with_scope(None), "$"), code="invalid_type")
        # bool 拒绝走 invalid_type（_require_p 先查 bool 再查范围）
        _reject(lambda: decode_data_consent_binding(_with_scope(True), "$"), code="invalid_type")

    def test_connection_authorization_ref_inner_must_match_outer(self) -> None:
        payload = _binding_payload(
            {
                "connection_authorization_ref": {
                    "connection_id": "synthetic-local-A",
                    "target_fingerprint": LOCAL_FP_A_R2,
                    "purpose": "stock_analysis",  # 与外层 purpose 不一致
                    "scope_version": 1,
                    "verified_connection_grant_id": None,
                }
            }
        )
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="identity_mismatch")

    def test_connection_authorization_ref_extra_key_rejected(self) -> None:
        payload = _binding_payload()
        payload["connection_authorization_ref"] = {
            **payload["connection_authorization_ref"],
            "grant": "x",
        }
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="unknown_field")

    def test_verified_connection_grant_id_str_or_null(self) -> None:
        payload = _binding_payload()
        payload["connection_authorization_ref"] = {
            **payload["connection_authorization_ref"],
            "verified_connection_grant_id": 123,
        }
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="invalid_type")

    def test_empty_source_selections_is_empty_not_wildcard(self) -> None:
        # [] 只表示无资料，不是通配（§9.8）；digest 须与空数组重算一致
        payload = _binding_payload(source_selections=[])
        payload["source_scope_digest"] = data_consent_source_scope_digest([])
        binding = decode_data_consent_binding(payload, "$")
        assert binding.source_selections == ()

    def test_scope_digest_mismatch_rejected(self) -> None:
        # decode 重算 digest，不信调用方值（§9.8「不一致摘要拒绝」）
        payload = _binding_payload(source_selections=[ALL_ITEM, ROWS_ITEM])
        payload["source_scope_digest"] = ALL_SCOPE_DIGEST  # 实际应为 COMBINED
        _reject(lambda: decode_data_consent_binding(payload, "$"), code="digest_mismatch")


class TestBindingStateMatrix:
    """state→(approver_ref, valid_from, expires_at) 编码形态矩阵（§9.8）。"""

    def test_pending_requires_both_null(self) -> None:
        ok = _binding_payload({"state": "pending", "approver_ref": None, "valid_from": None})
        assert decode_data_consent_binding(ok, "$").state.value == "pending"
        _reject(
            lambda: decode_data_consent_binding(
                _binding_payload({"state": "pending", "approver_ref": "user:local-owner", "valid_from": None}),
                "$",
            ),
            code="state_matrix",
        )

    def test_approved_requires_approver_and_valid_from(self) -> None:
        _reject(lambda: decode_data_consent_binding(_binding_payload({"approver_ref": None}), "$"), code="state_matrix")
        _reject(lambda: decode_data_consent_binding(_binding_payload({"valid_from": None}), "$"), code="state_matrix")

    def test_revoked_terminal_same_empty_or_same_nonempty(self) -> None:
        # 终态 approver_ref/valid_from 必须同空或同非空
        ok = _binding_payload({"state": "revoked", "approver_ref": None, "valid_from": None})
        assert decode_data_consent_binding(ok, "$").state.value == "revoked"
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"state": "revoked", "approver_ref": None}), "$"),
            code="state_matrix",
        )

    def test_expired_requires_expires_at(self) -> None:
        _reject(lambda: decode_data_consent_binding(_binding_payload({"state": "expired"}), "$"), code="state_matrix")
        ok = _binding_payload(
            {
                "state": "expired",
                "expires_at": "2026-02-01T00:00:00.000000Z",
            }
        )
        assert decode_data_consent_binding(ok, "$").state.value == "expired"

    def test_valid_from_must_be_before_expires_at(self) -> None:
        bad = _binding_payload(
            {
                "expires_at": "2025-12-31T00:00:00.000000Z",  # 早于 valid_from
            }
        )
        _reject(lambda: decode_data_consent_binding(bad, "$"), code="invalid_period")

    def test_timestamp_format_enforced(self) -> None:
        # 非 UTC 微秒 Z 形态拒绝
        for bad_ts in ("2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00Z", "2026-01-01"):
            _reject(
                lambda ts=bad_ts: decode_data_consent_binding(_binding_payload({"valid_from": ts}), "$"),
                code="invalid_timestamp",
            )

    def test_credential_version_str_or_null(self) -> None:
        ok = _binding_payload({"credential_version": "cred-v1"})
        assert decode_data_consent_binding(ok, "$").credential_version == "cred-v1"
        _reject(
            lambda: decode_data_consent_binding(_binding_payload({"credential_version": 3}), "$"),
            code="invalid_identity",
        )


# ---------------------------------------------------------------------------
# encode ↔ decode 往返
# ---------------------------------------------------------------------------


class TestBindingRoundtrip:
    def test_encode_decode_roundtrip_preserves_identity(self) -> None:
        binding = decode_data_consent_binding(_binding_payload(), "$")
        wire = encode_data_consent_binding(binding)
        again = decode_data_consent_binding(wire, "$")
        # JSONB 重排键后重读保同身份（§10.2）：规范字节相等
        assert json_doc(wire) == json_doc(encode_data_consent_binding(again))
        assert isinstance(json_doc(wire), str)

    def test_encode_covers_all_keys(self) -> None:
        wire = encode_data_consent_binding(decode_data_consent_binding(_binding_payload(), "$"))
        expected_keys = {
            "schema_version",
            "consent_id",
            "consent_revision",
            "state",
            "approver_ref",
            "user_scope",
            "execution_id",
            "round_id",
            "valid_from",
            "expires_at",
            "requires_egress_grant",
            "connection_authorization_ref",
            "connection_revision",
            "security_revision",
            "credential_version",
            "target_fingerprint",
            "purpose",
            "scope_version",
            "source_selections",
            "source_scope_digest",
        }
        assert set(wire) == expected_keys

    def test_jsonb_key_reordering_preserves_canonical_bytes(self) -> None:
        wire = encode_data_consent_binding(decode_data_consent_binding(_binding_payload(), "$"))
        reordered = dict(reversed(list(wire.items())))
        assert json_doc(reordered) == json_doc(wire)
        assert json_doc(reordered) == CanonicalJson(json_doc(wire))


# ---------------------------------------------------------------------------
# C-02-4a：R04 owner 命令/核权合同（§10.2 L1773 + §9.8）
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)


def _decoded_binding(overrides: dict[str, Any] | None = None) -> Any:
    return decode_data_consent_binding(_binding_payload(overrides), "$")


def _decoded_source_item() -> Any:
    return decode_data_consent_source_item(ALL_ITEM, "$")


def _command_ref(**overrides: Any) -> CommandRef:
    values: dict[str, Any] = {
        "user_scope": "synthetic-user",
        "source_module": "ui",
        "business_request_id": "req-1",
        "command_id": "consent-cmd-1",
        "expected_revision": 0,
    }
    values.update(overrides)
    return CommandRef(**values)


def _save_command(**overrides: Any) -> DataConsentCommand:
    values: dict[str, Any] = {
        "command": _command_ref(),
        "consent_id": "dec-consent-1",
        "session_id": "s-1",
        "execution_id": "exec-1",
        "round_id": "round-1",
        "node_id": "root",
        "binding": _decoded_binding(),
    }
    values.update(overrides)
    return DataConsentCommand(**values)


def _revoke_command(**overrides: Any) -> DataConsentRevokeCommand:
    values: dict[str, Any] = {
        "command": _command_ref(expected_revision=1),
        "consent_id": "dec-consent-1",
    }
    values.update(overrides)
    return DataConsentRevokeCommand(**values)


def _authorization(**overrides: Any) -> DataAttemptAuthorization:
    values: dict[str, Any] = {
        "user_scope": "synthetic-user",
        "operation_id": "op-1",
        "attempt_id": "attempt-1",
        "consent_id": "dec-consent-1",
        "expected_consent_revision": 1,
        "connection_id": "synthetic-local-A",
        "connection_revision": 2,
        "security_revision": 0,
        "credential_version": None,
        "purpose": "agent_research",
        "target_fingerprint": LOCAL_FP_A_R2,
        "scope_version": 1,
        "source_selections": (_decoded_source_item(),),
        "source_scope_digest": ALL_SCOPE_DIGEST,
        "owner_epoch": 1,
        "run_epoch": 1,
    }
    values.update(overrides)
    return DataAttemptAuthorization(**values)


class TestDataConsentCommandIdentity:
    def test_create_command_derives_stable_identity(self) -> None:
        first = derive_data_consent_command_identity(_save_command())
        second = derive_data_consent_command_identity(_save_command())
        assert first.command_record_id == second.command_record_id
        assert first.input_digest == second.input_digest
        assert len(first.input_digest) == 64

    def test_same_command_key_different_binding_conflicts_by_digest(self) -> None:
        base = derive_data_consent_command_identity(_save_command())
        other_binding = _decoded_binding({"approver_ref": "user:other"})
        other = derive_data_consent_command_identity(_save_command(binding=other_binding))
        assert base.input_digest != other.input_digest

    def test_expected_revision_participates_in_digest(self) -> None:
        # 同一绑定在更新命令（expected=1, revision=2）下与建行命令摘要不同
        updated_binding = _decoded_binding({"consent_revision": 2})
        update_cmd = derive_data_consent_command_identity(
            _save_command(command=_command_ref(expected_revision=1), binding=updated_binding)
        )
        create_cmd = derive_data_consent_command_identity(_save_command())
        assert update_cmd.input_digest != create_cmd.input_digest

    def test_binding_user_scope_mismatch_rejected(self) -> None:
        bad_binding = _decoded_binding({"user_scope": "other-scope"})
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_command_identity(_save_command(binding=bad_binding))
        assert exc.value.code == "identity_mismatch"

    def test_binding_round_mismatch_rejected(self) -> None:
        bad_binding = _decoded_binding({"round_id": "round-9"})
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_command_identity(_save_command(binding=bad_binding))
        assert exc.value.code == "identity_mismatch"

    def test_create_requires_consent_revision_one(self) -> None:
        bad_binding = _decoded_binding({"consent_revision": 2})
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_command_identity(_save_command(binding=bad_binding))
        assert exc.value.code == "invalid_revision"

    def test_update_requires_consent_revision_expected_plus_one(self) -> None:
        bad_binding = _decoded_binding({"consent_revision": 3})
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_command_identity(
                _save_command(command=_command_ref(expected_revision=1), binding=bad_binding)
            )
        assert exc.value.code == "invalid_revision"

    def test_update_path_valid(self) -> None:
        updated_binding = _decoded_binding({"consent_revision": 2})
        identity = derive_data_consent_command_identity(
            _save_command(command=_command_ref(expected_revision=1), binding=updated_binding)
        )
        assert identity.command_record_id

    def test_empty_consent_id_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_command_identity(_save_command(consent_id=""))
        assert exc.value.code == "invalid_identity"


class TestDataConsentRevokeIdentity:
    def test_valid_revoke_command(self) -> None:
        identity = derive_data_consent_revoke_identity(_revoke_command())
        assert identity.input_digest
        assert identity.command_record_id

    def test_expected_revision_participates_in_digest(self) -> None:
        first = derive_data_consent_revoke_identity(_revoke_command())
        second = derive_data_consent_revoke_identity(_revoke_command(command=_command_ref(expected_revision=2)))
        assert first.input_digest != second.input_digest

    def test_expected_revision_zero_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            derive_data_consent_revoke_identity(_revoke_command(command=_command_ref(expected_revision=0)))
        assert exc.value.code == "invalid_revision"


class TestValidateDataAttemptAuthorization:
    def test_valid_request_returns_recomputed_digest(self) -> None:
        assert validate_data_attempt_authorization(_authorization()) == ALL_SCOPE_DIGEST

    def test_request_digest_mismatch_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_data_attempt_authorization(_authorization(source_scope_digest="0" * 64))
        assert exc.value.code == "digest_mismatch"

    def test_non_tuple_selections_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_data_attempt_authorization(_authorization(source_selections=[_decoded_source_item()]))
        assert exc.value.code == "invalid_type"

    def test_revision_bounds_rejected(self) -> None:
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(expected_consent_revision=0)),
            code="invalid_revision",
        )
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(connection_revision=0)),
            code="invalid_revision",
        )
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(security_revision=-1)),
            code="invalid_revision",
        )
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(scope_version=0)),
            code="invalid_revision",
        )
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(owner_epoch=True)),
            code="invalid_revision",
        )
        _reject(
            lambda: validate_data_attempt_authorization(_authorization(run_epoch=-1)),
            code="invalid_revision",
        )

    def test_empty_identity_rejected(self) -> None:
        _reject(lambda: validate_data_attempt_authorization(_authorization(operation_id="")), code="invalid_identity")
        _reject(lambda: validate_data_attempt_authorization(_authorization(attempt_id="")), code="invalid_identity")
        _reject(lambda: validate_data_attempt_authorization(_authorization(purpose="")), code="invalid_identity")


class TestEvaluateDataAttempt:
    def test_matching_request_allowed(self) -> None:
        binding = _decoded_binding({"expires_at": "2030-01-01T00:00:00.000000Z"})
        assert evaluate_data_attempt(binding, _authorization(), _NOW) is None

    def test_pending_state_denied(self) -> None:
        binding = _decoded_binding({"state": "pending", "approver_ref": None, "valid_from": None})
        assert evaluate_data_attempt(binding, _authorization(), _NOW) == "state_not_approved"

    def test_revision_mismatch_denied(self) -> None:
        assert evaluate_data_attempt(_decoded_binding(), _authorization(expected_consent_revision=2), _NOW) == (
            "revision_mismatch"
        )

    def test_identity_field_mismatches_denied(self) -> None:
        cases: list[tuple[DataAttemptAuthorization, str]] = [
            (_authorization(purpose="stock_analysis"), "purpose_mismatch"),
            (_authorization(target_fingerprint=LOCAL_FP_B_R2), "target_mismatch"),
            (_authorization(scope_version=2), "scope_version_mismatch"),
            (_authorization(connection_id="synthetic-local-B"), "connection_mismatch"),
            (_authorization(connection_revision=3), "connection_revision_mismatch"),
            (_authorization(security_revision=1), "security_revision_mismatch"),
            (_authorization(credential_version="cred-v1"), "credential_mismatch"),
        ]
        for request, expected_reason in cases:
            assert evaluate_data_attempt(_decoded_binding(), request, _NOW) == expected_reason

    def test_scope_digest_mismatch_denied(self) -> None:
        rows_item = decode_data_consent_source_item(ROWS_ITEM, "$")
        # 双源集合与单源绑定摘要必然不同（请求侧摘要自洽，与绑定分叉）
        request = _authorization(
            source_selections=(_decoded_source_item(), rows_item),
            source_scope_digest=data_consent_source_scope_digest([_decoded_source_item(), rows_item]),
        )
        assert evaluate_data_attempt(_decoded_binding(), request, _NOW) == "scope_mismatch"

    def test_expired_denied(self) -> None:
        binding = _decoded_binding(
            {"valid_from": "2025-01-01T00:00:00.000000Z", "expires_at": "2026-01-01T00:00:00.000000Z"}
        )
        assert evaluate_data_attempt(binding, _authorization(), _NOW) == "expired"

    def test_not_yet_valid_denied(self) -> None:
        binding = _decoded_binding({"valid_from": "2027-01-01T00:00:00.000000Z"})
        assert evaluate_data_attempt(binding, _authorization(), _NOW) == "not_yet_valid"

    def test_null_expiry_denied(self) -> None:
        # §9.8 L1539：expires_at=null 表示「期限未核定，不表示永久」——必须 deny，
        # 不能自动补期限或放行（检视 B-1 回归锚）。
        assert evaluate_data_attempt(_decoded_binding({"expires_at": None}), _authorization(), _NOW) == (
            "expiry_unverified"
        )


class TestDataConsentClaimCodec:
    def _claim(self, **overrides: Any) -> DataConsentClaim:
        values: dict[str, Any] = {
            "schema_version": 1,
            "consent_id": "dec-consent-1",
            "consent_revision": 1,
            "connection_authorization_ref": ConnectionAuthorizationRef(
                connection_id="synthetic-local-A",
                target_fingerprint=LOCAL_FP_A_R2,
                purpose="agent_research",
                scope_version=1,
                verified_connection_grant_id=None,
            ),
            "security_revision": 0,
            "credential_version": None,
            "scope_version": 1,
            "source_scope_digest": ALL_SCOPE_DIGEST,
            "purpose": "agent_research",
            "target_fingerprint": LOCAL_FP_A_R2,
            "attempt_id": "attempt-1",
            "operation_id": "op-1",
            "owner_epoch": 1,
            "run_epoch": 1,
            "authorized_at": "2026-06-01T12:00:00.000000Z",
            "outcome": DataAttemptOutcome.ALLOW,
        }
        values.update(overrides)
        return DataConsentClaim(**values)

    def test_encode_decode_roundtrip(self) -> None:
        wire = encode_data_consent_claim(self._claim())
        assert set(wire) == {
            "schema_version",
            "consent_id",
            "consent_revision",
            "connection_authorization_ref",
            "security_revision",
            "credential_version",
            "scope_version",
            "source_scope_digest",
            "purpose",
            "target_fingerprint",
            "attempt_id",
            "operation_id",
            "owner_epoch",
            "run_epoch",
            "authorized_at",
            "outcome",
        }
        decoded = decode_data_consent_claim(wire, "$")
        assert json_doc(encode_data_consent_claim(decoded)) == json_doc(wire)

    def test_jsonb_key_reordering_preserves_identity(self) -> None:
        wire = encode_data_consent_claim(self._claim())
        reordered = dict(reversed(list(wire.items())))
        assert json_doc(reordered) == json_doc(wire)

    def test_strictness(self) -> None:
        wire = encode_data_consent_claim(self._claim())
        _reject(lambda: decode_data_consent_claim({**wire, "extra": 1}, "$"), code="unknown_field")
        stripped = {k: v for k, v in wire.items() if k != "run_epoch"}
        _reject(lambda: decode_data_consent_claim(stripped, "$"), code="missing_field")
        _reject(lambda: decode_data_consent_claim({**wire, "schema_version": 2}, "$"), code="invalid_schema_version")
        _reject(lambda: decode_data_consent_claim({**wire, "schema_version": True}, "$"), code="invalid_schema_version")
        _reject(lambda: decode_data_consent_claim({**wire, "outcome": "maybe"}, "$"), code="invalid_outcome")
        _reject(lambda: decode_data_consent_claim({**wire, "security_revision": -1}, "$"), code="invalid_revision")
        _reject(lambda: decode_data_consent_claim({**wire, "owner_epoch": True}, "$"), code="invalid_revision")
        _reject(
            lambda: decode_data_consent_claim({**wire, "authorized_at": "2026-06-01"}, "$"), code="invalid_timestamp"
        )

    def test_claim_matches_authorization(self) -> None:
        claim = self._claim()
        request = _authorization()
        validate_data_consent_claim(claim, request, recomputed_digest=ALL_SCOPE_DIGEST)

    def test_claim_mismatch_rejected(self) -> None:
        request = _authorization()
        _reject(
            lambda: validate_data_consent_claim(
                self._claim(consent_id="dec-other"), request, recomputed_digest=ALL_SCOPE_DIGEST
            ),
            code="identity_mismatch",
        )
        _reject(
            lambda: validate_data_consent_claim(
                self._claim(consent_revision=2), request, recomputed_digest=ALL_SCOPE_DIGEST
            ),
            code="identity_mismatch",
        )
        _reject(
            lambda: validate_data_consent_claim(
                self._claim(operation_id="op-9"), request, recomputed_digest=ALL_SCOPE_DIGEST
            ),
            code="identity_mismatch",
        )
        _reject(
            lambda: validate_data_consent_claim(
                self._claim(source_scope_digest="0" * 64), request, recomputed_digest=ALL_SCOPE_DIGEST
            ),
            code="identity_mismatch",
        )
        _reject(
            lambda: validate_data_consent_claim(
                self._claim(security_revision=1), request, recomputed_digest=ALL_SCOPE_DIGEST
            ),
            code="identity_mismatch",
        )
