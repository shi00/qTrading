"""I18：MCP 配置准入校验与启连点重核。

覆盖：
- stdio 字段准确校验：cwd 必填/绝对、command 必须存在且可执行、拒绝 HTTP 专属字段；
- 显示名：空白与控制字符一律拒绝；
- HTTP：必须显式风险确认；Header 值只能是 ``env:`` / ``keyring:`` 安全引用，
  明文凭据与禁止注入的 Header 一律拒绝；
- 配置加载后在使用点重核：被篡改（缺 cwd / 空白名 / 未确认 HTTP / 命令不存在 /
  身份不符）的存储配置在启连点被拒，且**零启连**（未调用 factory.open）；
- ``FastMCPClientFactory.open`` 复用同一校验，未通过时不创建任何连接资源。
"""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service import mcp_fastmcp
from services.agent_service.mcp import (
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
    validate_mcp_server_config,
    validate_transport_config,
)
from services.agent_service.mcp_fastmcp import FastMCPClientFactory, MCPSessionDiagnostics, _resolve_http_headers

_FINGERPRINT = sha256_hex(b"mcp-local-implementation")
_SCHEMA = '{"type":"object","properties":{}}'
_ABS_CWD = str(Path(sys.executable).parent)


def _identity(config_version: int = 1) -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id="svc-local-fs", config_version=config_version)


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=1024,
        decoded_bytes=2048,
        structure_depth=32,
        catalog_items=100,
        catalog_bytes=65536,
        schema_bytes=131072,
        single_result_bytes=65536,
        retention_bytes=131072,
    )


def _audit(identity: MCPServiceIdentity | None = None) -> MCPReadOnlyAudit:
    return MCPReadOnlyAudit(
        service=identity or _identity(),
        tool_scope=("read_file",),
        implementation_fingerprint=_FINGERPRINT,
        evidence_source="local-executable-inspection",
        reviewed_at="2026-11-13T00:00:00.000000Z",
        invalidation_conditions=("executable", "entry_script"),
    )


def _stdio_transport(**overrides: object) -> MCPTransportConfig:
    values: dict[str, object] = {
        "kind": MCPTransportKind.STDIO,
        "command": sys.executable,
        "cwd": _ABS_CWD,
    }
    values.update(overrides)
    return MCPTransportConfig(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


def _http_transport(**overrides: object) -> MCPTransportConfig:
    values: dict[str, object] = {
        "kind": MCPTransportKind.STREAMABLE_HTTP,
        "url": "https://mcp.example.test/tools",
        "http_risk_approved": True,
    }
    values.update(overrides)
    return MCPTransportConfig(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


def _config(**overrides: object) -> MCPServerConfig:
    values: dict[str, object] = {
        "identity": _identity(),
        "display_name": "本地文件服务",
        "transport": _stdio_transport(),
        "budget": _budget(),
        "audit": _audit(),
    }
    values.update(overrides)
    return MCPServerConfig(**values)  # type: ignore[arg-type]  # 测试构造受控覆盖


class _Store:
    def __init__(self, config: MCPServerConfig | None) -> None:
        self.config = config
        self.saved: list[MCPServerConfig] = []

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None:
        # 故意不校验身份：模拟存储被篡改/串号，由使用点重核拒绝。
        return self.config

    async def save(self, config: MCPServerConfig) -> None:
        self.saved.append(config)
        self.config = config


class _Session:
    def __init__(self) -> None:
        self.list_calls = 0
        self.closed = False

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]:
        self.list_calls += 1
        return (MCPToolDescriptor("read_file", _SCHEMA),)

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]:
        return {"content": []}

    async def aclose(self) -> None:
        self.closed = True


class _Factory:
    def __init__(self) -> None:
        self.opened: list[MCPServerConfig] = []
        self.session = _Session()

    async def open(self, config: MCPServerConfig, *, guard: Any = None) -> _Session:
        self.opened.append(config)
        return self.session


class _Probe:
    async def probe(self, config: MCPServerConfig) -> str | None:
        return _FINGERPRINT


class _NoFrozenUsers:
    """无研究冻结该配置版（准入用例不涉及断开/改配置）。"""

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ()


def _manager(config: MCPServerConfig | None) -> tuple[SharedMCPConnectionManager, _Store, _Factory]:
    store = _Store(config)
    factory = _Factory()
    manager = SharedMCPConnectionManager(store=store, factory=factory, probe=_Probe(), usage=_NoFrozenUsers())
    return manager, store, factory


class TestStdioFieldAdmission:
    def test_cwd_is_required(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(cwd=None))
        assert exc.value.code == "invalid_transport"
        assert ".cwd" in str(exc.value)

    def test_cwd_must_be_absolute(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(cwd="relative/dir"))
        assert exc.value.code == "invalid_transport"
        assert ".cwd" in str(exc.value)

    def test_cwd_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(cwd=str(tmp_path / "missing-dir")))
        assert exc.value.code == "invalid_transport"
        assert ".cwd" in str(exc.value)

    def test_cwd_must_be_a_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "not-a-dir"
        target.write_text("x", encoding="utf-8")
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(cwd=str(target)))
        assert exc.value.code == "invalid_transport"
        assert ".cwd" in str(exc.value)

    def test_cwd_must_be_accessible(self, tmp_path: Path) -> None:
        directory = tmp_path / "locked-dir"
        directory.mkdir()
        directory.chmod(0o000)
        try:
            if os.access(directory, os.R_OK | os.X_OK):
                pytest.skip("permission bits not enforced (root/superuser or non-POSIX)")
            with pytest.raises(AgentValidationError) as exc:
                validate_transport_config(_stdio_transport(cwd=str(directory)))
            assert "not accessible" in str(exc.value)
        finally:
            directory.chmod(0o700)

    def test_command_must_exist(self, tmp_path: Path) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(command=str(tmp_path / "missing.exe")))
        assert exc.value.code == "invalid_transport"

    def test_command_must_be_a_file(self, tmp_path: Path) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(command=str(tmp_path)))
        assert exc.value.code == "invalid_transport"

    @pytest.mark.skipif(os.name == "nt", reason="Windows 的 os.access(X_OK) 不反映执行位")
    def test_command_must_be_executable(self, tmp_path: Path) -> None:
        target = tmp_path / "tool"
        target.write_text("#!/bin/sh\n", encoding="utf-8")
        target.chmod(0o644)
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_stdio_transport(command=str(target)))
        assert "not executable" in str(exc.value)

    def test_stdio_rejects_http_only_fields(self) -> None:
        with pytest.raises(AgentValidationError) as risk_exc:
            validate_transport_config(_stdio_transport(http_risk_approved=True))
        assert risk_exc.value.code == "invalid_transport"
        assert "http-only" in str(risk_exc.value)
        with pytest.raises(AgentValidationError) as header_exc:
            validate_transport_config(_stdio_transport(headers=(("authorization", "env:MCP_TEST_TOKEN"),)))
        assert header_exc.value.code == "invalid_transport"
        assert "http-only" in str(header_exc.value)


class TestDisplayNameAdmission:
    def test_blank_display_name_is_rejected(self) -> None:
        for name in ("", "   ", "\t"):
            with pytest.raises(AgentValidationError) as exc:
                validate_mcp_server_config(_config(display_name=name))
            assert exc.value.code in {"invalid_identity", "invalid_display_name"}

    def test_control_character_display_name_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_mcp_server_config(_config(display_name="本地\n注入"))
        assert exc.value.code == "invalid_display_name"

    def test_audit_bound_to_other_service_is_rejected(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_mcp_server_config(_config(audit=_audit(_identity(config_version=9))))
        assert exc.value.code == "invalid_audit_binding"

    def test_valid_config_is_admitted(self) -> None:
        validate_mcp_server_config(_config())


class TestHttpRiskAdmission:
    def test_plain_http_requires_explicit_risk_confirmation(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_http_transport(url="http://mcp.example.test/tools", http_risk_approved=False))
        assert exc.value.code == "http_not_approved"
        validate_transport_config(_http_transport(url="http://mcp.example.test/tools"))

    def test_https_does_not_require_plaintext_http_confirmation(self) -> None:
        # TLS 已承载机密性：不得要求用户确认一个与其配置不符的「明文 HTTP」风险。
        validate_transport_config(_http_transport(http_risk_approved=False))

    def test_sse_requires_compat_and_risk_approval(self) -> None:
        sse = MCPTransportConfig(
            kind=MCPTransportKind.SSE,
            url="http://mcp.example.test/sse",
            sse_compat_approved=True,
            http_risk_approved=True,
        )
        validate_transport_config(sse)
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(replace(sse, http_risk_approved=False))
        assert exc.value.code == "http_not_approved"
        # SSE over https 仍只需显式核准兼容传输，不需确认明文风险。
        validate_transport_config(replace(sse, url="https://mcp.example.test/sse", http_risk_approved=False))

    def test_plaintext_header_value_is_rejected(self) -> None:
        headers = (("authorization", "Bearer plaintext-value"),)
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_http_transport(headers=headers))
        assert exc.value.code == "invalid_transport"
        assert "secure reference" in str(exc.value)

    def test_forbidden_header_name_is_rejected(self) -> None:
        for name in ("cookie", "host", "last-event-id", "proxy-authorization"):
            with pytest.raises(AgentValidationError) as exc:
                validate_transport_config(_http_transport(headers=((name, "env:MCP_TEST_TOKEN"),)))
            assert exc.value.code == "invalid_transport"

    def test_duplicate_header_name_is_rejected(self) -> None:
        headers = (("authorization", "env:MCP_TEST_TOKEN"), ("Authorization", "env:MCP_TEST_TOKEN_2"))
        with pytest.raises(AgentValidationError) as exc:
            validate_transport_config(_http_transport(headers=headers))
        assert "duplicate" in str(exc.value)

    def test_secure_reference_headers_are_admitted(self) -> None:
        headers = (("authorization", "env:MCP_TEST_TOKEN"), ("x-mcp-tenant", "keyring:mcp-tenant"))
        validate_transport_config(_http_transport(headers=headers))


class TestLoadRecheckBeforeConnect:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "tampered",
        [
            _config(transport=_stdio_transport(cwd=None)),
            _config(display_name="   "),
            _config(transport=_http_transport(url="http://mcp.example.test/tools", http_risk_approved=False)),
            _config(transport=_stdio_transport(command=str(Path(sys.executable).parent / "missing.exe"))),
            _config(transport=_stdio_transport(cwd=str(Path(sys.executable).parent / "missing-cwd"))),
        ],
    )
    async def test_tampered_stored_config_is_refused_with_zero_open(self, tampered: MCPServerConfig) -> None:
        manager, _store, factory = _manager(tampered)

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())

        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert factory.opened == []

    @pytest.mark.asyncio
    async def test_identity_mismatch_is_refused_with_zero_open(self) -> None:
        other = _config(identity=_identity(config_version=2), audit=_audit(_identity(config_version=2)))
        manager, _store, factory = _manager(other)

        with pytest.raises(AgentServiceError) as exc:
            await manager.connect(_identity())

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert factory.opened == []

    @pytest.mark.asyncio
    async def test_connection_test_also_rechecks_loaded_config(self) -> None:
        manager, _store, factory = _manager(_config(display_name="\x00bad"))

        with pytest.raises(AgentServiceError) as exc:
            await manager.connection_test(_identity())

        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert factory.opened == []

    @pytest.mark.asyncio
    async def test_valid_loaded_config_still_connects(self) -> None:
        # I03：stdio 无入站限额前置插入点、在准入即被拒；此处验证「加载后仍可启连」
        # 的合同，故用具备真实插入点的 HTTP 传输。
        manager, _store, factory = _manager(_config(transport=_http_transport()))

        await manager.connect(_identity())

        assert len(factory.opened) == 1
        assert manager.is_connected(_identity()) is True

    @pytest.mark.asyncio
    async def test_save_refuses_invalid_transport_with_zero_open(self) -> None:
        manager, store, factory = _manager(None)

        with pytest.raises(AgentServiceError) as exc:
            await manager.save_config(_config(transport=_stdio_transport(cwd=None)))

        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert store.saved == []
        assert factory.opened == []


class TestFactoryReusesValidation:
    @pytest.mark.asyncio
    async def test_factory_refuses_tampered_config_before_creating_resources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("连接资源不得在配置未通过校验时创建")

        monkeypatch.setattr(mcp_fastmcp, "_SanitizedStderrDrain", _boom)
        monkeypatch.setattr(mcp_fastmcp, "Client", _boom)

        with pytest.raises(AgentServiceError) as exc:
            await FastMCPClientFactory().open(_config(transport=_stdio_transport(cwd=None)))

        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    @pytest.mark.asyncio
    async def test_factory_refuses_unconfirmed_http_before_any_send(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await FastMCPClientFactory().open(
                _config(transport=_http_transport(url="http://mcp.example.test/tools", http_risk_approved=False))
            )
        assert exc.value.code is AgentErrorCode.INPUT_INVALID


class TestSecureHeaderResolution:
    def test_env_reference_is_resolved(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_I18_TEST_TOKEN", "Bearer local-test-value")
        resolved = _resolve_http_headers((("authorization", "env:MCP_I18_TEST_TOKEN"),))
        assert resolved == {"authorization": "Bearer local-test-value"}

    def test_missing_reference_reports_reference_name_only(self) -> None:
        with pytest.raises(ValueError) as exc:
            _resolve_http_headers((("authorization", "env:MCP_I18_MISSING_TOKEN"),))
        message = str(exc.value)
        assert "MCP_I18_MISSING_TOKEN" in message
        assert "Bearer" not in message

    def test_resolved_headers_are_attached_to_http_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_I18_TEST_TOKEN", "Bearer local-test-value")
        config = _config(
            transport=_http_transport(headers=(("authorization", "env:MCP_I18_TEST_TOKEN"),)),
            audit=None,
        )
        headers = _resolve_http_headers(config.transport.headers)
        transport = FastMCPClientFactory()._transport(
            config, None, diagnostics=MCPSessionDiagnostics(), label="svc-test", headers=headers
        )
        assert transport.headers == {"authorization": "Bearer local-test-value"}
