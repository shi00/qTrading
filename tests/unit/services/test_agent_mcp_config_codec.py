"""MCP 配置持久化编解码：往返、未知键/缺失键、非法值拒绝。"""

from __future__ import annotations

import os
import sys

import pytest

from core.agent_types import (
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_config_codec import decode_mcp_server_config, encode_mcp_server_config

pytestmark = pytest.mark.unit

_FINGERPRINT = sha256_hex(b"impl")


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=1024,
        decoded_bytes=2048,
        structure_depth=32,
        catalog_items=10,
        catalog_bytes=1024,
        schema_bytes=2048,
        single_result_bytes=1024,
        retention_bytes=2048,
    )


def _identity() -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id="svc-codec", config_version=2)


def _config(transport: MCPTransportConfig) -> MCPServerConfig:
    identity = _identity()
    audit = MCPReadOnlyAudit(
        service=identity,
        tool_scope=("search",),
        implementation_fingerprint=_FINGERPRINT,
        evidence_source="test",
        reviewed_at="2026-11-13T00:00:00.000000Z",
        invalidation_conditions=("executable",),
    )
    return MCPServerConfig(
        identity=identity,
        display_name="编解码服务",
        transport=transport,
        budget=_budget(),
        audit=audit,
    )


def _http_transport() -> MCPTransportConfig:
    return MCPTransportConfig(
        kind=MCPTransportKind.STREAMABLE_HTTP,
        url="https://mcp.example.test/tools",
        headers=(("Authorization", "env:MCP_TOKEN"),),
        http_risk_approved=True,
    )


def _stdio_transport() -> MCPTransportConfig:
    return MCPTransportConfig(
        kind=MCPTransportKind.STDIO,
        command=sys.executable,
        args=("-c", "pass"),
        env=(("MCP_LOG", "debug"),),
        secret_env=(("MCP_TOKEN", "keyring:mcp-token"),),
        cwd=os.path.dirname(sys.executable),
    )


def _stdio_config(*, env: tuple[tuple[str, str], ...], secret_env: tuple[tuple[str, str], ...]) -> MCPServerConfig:
    return _config(
        MCPTransportConfig(
            kind=MCPTransportKind.STDIO,
            command=sys.executable,
            args=("-c", "pass"),
            env=env,
            secret_env=secret_env,
            cwd=os.path.dirname(sys.executable),
        )
    )


class TestCodecRoundTrip:
    @pytest.mark.parametrize("transport", [_http_transport(), _stdio_transport()])
    def test_round_trip(self, transport: MCPTransportConfig) -> None:
        config = _config(transport)
        decoded = decode_mcp_server_config(encode_mcp_server_config(config))
        assert decoded == config

    def test_audit_null_round_trip(self) -> None:
        config = MCPServerConfig(
            identity=_identity(),
            display_name="无审核",
            transport=_http_transport(),
            budget=_budget(),
        )
        decoded = decode_mcp_server_config(encode_mcp_server_config(config))
        assert decoded.audit is None


class TestCodecRejection:
    def test_unknown_top_key_rejected(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        wire["extra"] = 1
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_server_config(wire)
        assert exc.value.code == "invalid_mcp_config"

    def test_missing_top_key_rejected(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        del wire["budget"]
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_server_config(wire)
        assert exc.value.code == "invalid_mcp_config"

    def test_unknown_transport_kind_rejected(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        wire["transport"]["kind"] = "websocket"
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_server_config(wire)
        assert exc.value.code == "invalid_mcp_config"

    def test_bool_budget_rejected(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        wire["budget"]["receive_bytes"] = True
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_server_config(wire)
        assert exc.value.code == "invalid_budget"

    def test_plain_http_requires_risk_approval(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        wire["transport"]["url"] = "http://mcp.example.test/tools"
        wire["transport"]["http_risk_approved"] = False
        with pytest.raises(AgentValidationError) as exc:
            decode_mcp_server_config(wire)
        assert exc.value.code == "http_not_approved"

    def test_https_does_not_require_plaintext_risk_approval(self) -> None:
        wire = encode_mcp_server_config(_config(_http_transport()))
        wire["transport"]["http_risk_approved"] = False
        decoded = decode_mcp_server_config(wire)
        assert decoded.transport.http_risk_approved is False

    def test_plaintext_secret_env_is_rejected(self) -> None:
        """敏感字段只接受安全引用：明文秘密不得被编码/落库（R9/R10）。"""

        config = _stdio_config(env=(), secret_env=(("MCP_TOKEN", "synthetic-plaintext-secret"),))
        with pytest.raises(AgentValidationError) as exc:
            encode_mcp_server_config(config)
        assert exc.value.code == "invalid_transport"

    def test_secure_reference_in_plain_env_is_rejected(self) -> None:
        """普通字段不得承载安全引用：分类由字段显式决定，不混放。"""

        config = _stdio_config(env=(("MCP_TOKEN", "env:MCP_TOKEN"),), secret_env=())
        with pytest.raises(AgentValidationError) as exc:
            encode_mcp_server_config(config)
        assert exc.value.code == "invalid_transport"

    def test_duplicate_env_names_across_fields_are_rejected(self) -> None:
        config = _stdio_config(
            env=(("MCP_TOKEN", "debug"),),
            secret_env=(("MCP_TOKEN", "keyring:mcp-token"),),
        )
        with pytest.raises(AgentValidationError) as exc:
            encode_mcp_server_config(config)
        assert exc.value.code == "invalid_transport"

    def test_remote_transport_cannot_carry_secret_env(self) -> None:
        transport = MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            secret_env=(("MCP_TOKEN", "keyring:mcp-token"),),
            http_risk_approved=True,
        )
        with pytest.raises(AgentValidationError) as exc:
            encode_mcp_server_config(_config(transport))
        assert exc.value.code == "invalid_transport"


class TestLegacyEnvMigration:
    """旧 wire（无 ``secret_env``）一次性迁移：只按引用形态分流，不按变量名猜秘密。"""

    def test_legacy_references_move_to_secret_env_and_new_wire_has_no_plain_refs(self) -> None:
        wire = encode_mcp_server_config(_config(_stdio_transport()))
        wire["transport"].pop("secret_env")
        wire["transport"]["env"] = [["MCP_LOG", "debug"], ["MCP_TOKEN", "env:MCP_LOCAL_TOKEN"]]

        decoded = decode_mcp_server_config(wire)

        assert decoded.transport.env == (("MCP_LOG", "debug"),)
        assert decoded.transport.secret_env == (("MCP_TOKEN", "env:MCP_LOCAL_TOKEN"),)
        sealed = encode_mcp_server_config(decoded)["transport"]
        assert "secret_env" in sealed
        assert sealed["secret_env"] == [["MCP_TOKEN", "env:MCP_LOCAL_TOKEN"]]
        assert all(not value.startswith(("env:", "keyring:")) for _name, value in sealed["env"])

    def test_legacy_migration_does_not_classify_by_variable_name(self) -> None:
        """名为 TOKEN 的字面量不被提升为秘密字段：分类只看引用形态，不看变量名。"""

        wire = encode_mcp_server_config(_config(_stdio_transport()))
        wire["transport"].pop("secret_env")
        wire["transport"]["env"] = [["MCP_TOKEN", "synthetic-not-a-reference"]]

        decoded = decode_mcp_server_config(wire)

        assert decoded.transport.env == (("MCP_TOKEN", "synthetic-not-a-reference"),)
        assert decoded.transport.secret_env == ()

    def test_legacy_wire_without_secret_env_key_round_trips_after_migration(self) -> None:
        wire = encode_mcp_server_config(_config(_stdio_transport()))
        wire["transport"].pop("secret_env")

        decoded = decode_mcp_server_config(wire)
        again = decode_mcp_server_config(encode_mcp_server_config(decoded))

        assert again == decoded
