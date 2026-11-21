"""E-02 §10.4 / HANDOFF §9 MCP 共享连接契约单元测试（静态可验证部分）。

覆盖：
- 共享 key = 稳定服务身份 + 配置版本（显示名不参与），严格 wire 解码；
- 连接/工具独立单 deadline：默认 30 秒、整数 1—120，bool/浮点/越界拒绝；
- 接收/解码/深度/目录/schema/单结果/留存预算：未全部冻结即不准入（无默认数值）；
- 工具白名单取服务/角色/请求/新鲜许可交集，空集合确实为空；
- 只读审核绑定实际实现指纹：同 schema 换载荷即失效、无法核定不准入。
"""

from __future__ import annotations

import pytest

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    MCP_DEFAULT_DEADLINE_S,
    MCP_MAX_DEADLINE_S,
    MCP_MIN_DEADLINE_S,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    decode_mcp_read_only_audit,
    decode_mcp_service_identity,
    encode_mcp_read_only_audit,
    evaluate_read_only_audit,
    resolve_tool_allowlist,
    sha256_hex,
    validate_mcp_deadline,
)

_AUDIT_FINGERPRINT = sha256_hex(b"mcp-local-implementation")


def expect_code(excinfo: pytest.ExceptionInfo[AgentValidationError], code: str) -> None:
    assert excinfo.value.code == code


def _identity(**overrides: object) -> MCPServiceIdentity:
    values: dict[str, object] = {"service_id": "svc-local-fs", "config_version": 3}
    values.update(overrides)
    return MCPServiceIdentity(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


def _audit(**overrides: object) -> MCPReadOnlyAudit:
    values: dict[str, object] = {
        "service": _identity(),
        "tool_scope": ("read_file", "list_dir"),
        "implementation_fingerprint": _AUDIT_FINGERPRINT,
        "evidence_source": "local-executable-inspection",
        "reviewed_at": "2026-11-13T00:00:00.000000Z",
        "invalidation_conditions": ("executable", "entry_script", "dependency_version"),
    }
    values.update(overrides)
    return MCPReadOnlyAudit(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


class TestMCPServiceIdentity:
    def test_transport_kinds_are_fixed_wire_spellings(self) -> None:
        assert [kind.value for kind in MCPTransportKind] == ["stdio", "streamable_http", "sse"]

    def test_stable_key_uses_service_and_config_version_not_display_name(self) -> None:
        first = _identity(service_id="svc-a", config_version=1)
        second = _identity(service_id="svc-a", config_version=2)
        assert first.stable_key() != second.stable_key()
        assert first.stable_key() == _identity(service_id="svc-a", config_version=1).stable_key()
        assert "display" not in first.stable_key()

    def test_config_version_zero_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            _identity(config_version=0).stable_key()
        expect_code(exc, "invalid_range")

    def test_decode_rejects_extra_keys_and_null(self) -> None:
        value = {"service_id": "svc-a", "config_version": 1, "display_name": "本地文件"}
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_service_identity(value)
        expect_code(exc, "unknown_field")

        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_service_identity({"service_id": None, "config_version": 1})
        expect_code(exc, "invalid_type")

    def test_decode_round_trip(self) -> None:
        identity = decode_mcp_service_identity({"service_id": "svc-a", "config_version": 7})
        assert identity == _identity(service_id="svc-a", config_version=7)


class TestMCPDeadline:
    def test_defaults_to_thirty_seconds(self) -> None:
        assert MCP_DEFAULT_DEADLINE_S == 30
        assert validate_mcp_deadline(None) == 30

    def test_one_and_one_hundred_twenty_are_legal_bounds(self) -> None:
        assert validate_mcp_deadline(MCP_MIN_DEADLINE_S) == 1
        assert validate_mcp_deadline(MCP_MAX_DEADLINE_S) == 120

    @pytest.mark.parametrize("value", [0, 121, -1, True, False, 30.0, "30"])
    def test_out_of_range_or_non_integer_is_rejected(self, value: object) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_mcp_deadline(value)
        expect_code(exc, "invalid_deadline")


class TestMCPBudgetAdmission:
    def test_unset_profile_is_not_admitted(self) -> None:
        profile = MCPBudgetProfile()
        assert profile.unfrozen_fields() == MCP_BUDGET_FIELDS
        assert profile.is_admitted() is False

    def test_each_family_must_be_frozen(self) -> None:
        frozen: dict[str, int | None] = {name: 1024 for name in MCP_BUDGET_FIELDS}
        profile = MCPBudgetProfile(**frozen)
        assert profile.is_admitted() is True
        assert profile.unfrozen_fields() == ()

        missing_one = dict(frozen)
        missing_one["structure_depth"] = None
        assert MCPBudgetProfile(**missing_one).unfrozen_fields() == ("structure_depth",)
        assert MCPBudgetProfile(**missing_one).is_admitted() is False

    def test_budget_profile_has_no_baked_in_numbers(self) -> None:
        # 设计 §10.4 / HANDOFF §9：数值未批准，未冻结即不准入，不造默认 MB。
        assert all(getattr(MCPBudgetProfile(), name) is None for name in MCP_BUDGET_FIELDS)


class TestToolAllowlist:
    def test_intersection_of_service_role_request_and_permission(self) -> None:
        allowed = resolve_tool_allowlist(
            service_tools=("read_file", "write_file", "list_dir"),
            role_tools=("read_file", "list_dir"),
            request_tools=("read_file", "list_dir", "delete_file"),
            permission_tools=("read_file",),
        )
        assert allowed == ("read_file",)

    def test_empty_role_scope_is_vacuum(self) -> None:
        assert (
            resolve_tool_allowlist(
                service_tools=("read_file",),
                role_tools=(),
                request_tools=("read_file",),
                permission_tools=("read_file",),
            )
            == ()
        )

    def test_empty_permission_set_is_empty_even_if_discovered(self) -> None:
        assert (
            resolve_tool_allowlist(
                service_tools=("read_file",),
                role_tools=("read_file",),
                request_tools=("read_file",),
                permission_tools=(),
            )
            == ()
        )

    def test_default_request_selection_is_empty(self) -> None:
        assert (
            resolve_tool_allowlist(
                service_tools=("read_file",),
                role_tools=("read_file",),
                request_tools=(),
                permission_tools=("read_file",),
            )
            == ()
        )

    def test_duplicates_collapse_and_order_is_deterministic(self) -> None:
        allowed = resolve_tool_allowlist(
            service_tools=("b", "a", "b"),
            role_tools=("b", "a"),
            request_tools=("a", "b"),
            permission_tools=("b", "a"),
        )
        assert allowed == ("a", "b")


class TestReadOnlyAuditBinding:
    def test_matching_implementation_fingerprint_is_admitted(self) -> None:
        admission = evaluate_read_only_audit(_audit(), _AUDIT_FINGERPRINT)
        assert admission.admitted is True
        assert admission.reason is None

    def test_same_schema_with_replaced_payload_invalidates_audit(self) -> None:
        replaced = sha256_hex(b"replaced-executable-payload")
        admission = evaluate_read_only_audit(_audit(), replaced)
        assert admission.admitted is False
        assert admission.reason == "implementation_changed"

    def test_unknown_current_implementation_is_refused(self) -> None:
        admission = evaluate_read_only_audit(_audit(), None)
        assert admission.admitted is False
        assert admission.reason == "implementation_unverified"

    def test_missing_audit_is_refused(self) -> None:
        admission = evaluate_read_only_audit(None, _AUDIT_FINGERPRINT)
        assert admission.admitted is False
        assert admission.reason == "audit_missing"

    def test_audit_without_implementation_fingerprint_is_refused(self) -> None:
        admission = evaluate_read_only_audit(_audit(implementation_fingerprint=None), _AUDIT_FINGERPRINT)
        assert admission.admitted is False
        assert admission.reason == "implementation_unverified"

    def test_audit_wire_round_trip_and_strict_keys(self) -> None:
        audit = _audit()
        wire = encode_mcp_read_only_audit(audit)
        assert decode_mcp_read_only_audit(wire) == audit

        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_read_only_audit({**wire, "read_only_hint": True})
        expect_code(exc, "unknown_field")

    def test_reviewed_at_requires_utc_timestamp(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            encode_mcp_read_only_audit(_audit(reviewed_at="2026-11-13 00:00:00"))
        expect_code(exc, "invalid_timestamp")
