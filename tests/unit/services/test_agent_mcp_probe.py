"""stdio 实现指纹探针：同内容同指纹、内容变化即变化、不可读/远端返回 None。"""

from __future__ import annotations

import os
import sys

import pytest

from core.agent_types import MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_probe import StdioImplementationProbe

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-probe", config_version=1)


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=1,
        decoded_bytes=1,
        structure_depth=1,
        catalog_items=1,
        catalog_bytes=1,
        schema_bytes=1,
        single_result_bytes=1,
        retention_bytes=1,
    )


def _config(transport: MCPTransportConfig) -> MCPServerConfig:
    return MCPServerConfig(identity=_IDENTITY, display_name="探针服务", transport=transport, budget=_budget())


def _stdio_config(command: str, cwd: str, args: tuple[str, ...] = ()) -> MCPServerConfig:
    return _config(MCPTransportConfig(kind=MCPTransportKind.STDIO, command=command, args=args, cwd=cwd))


class TestStdioImplementationProbe:
    async def test_same_content_same_fingerprint(self, tmp_path) -> None:
        script = tmp_path / "server.py"
        script.write_text("print('v1')", encoding="utf-8")
        probe = StdioImplementationProbe()
        config = _stdio_config(command=sys.executable, cwd=str(tmp_path), args=(str(script),))

        assert await probe.probe(config) == await probe.probe(config)

    async def test_content_change_changes_fingerprint(self, tmp_path) -> None:
        script = tmp_path / "server.py"
        script.write_text("print('v1')", encoding="utf-8")
        probe = StdioImplementationProbe()
        config = _stdio_config(command=sys.executable, cwd=str(tmp_path), args=(str(script),))
        before = await probe.probe(config)

        script.write_text("print('v2')", encoding="utf-8")
        assert await probe.probe(config) != before

    async def test_env_keys_change_fingerprint(self, tmp_path) -> None:
        script = tmp_path / "server.py"
        script.write_text("print('v1')", encoding="utf-8")
        probe = StdioImplementationProbe()
        base = _stdio_config(command=sys.executable, cwd=str(tmp_path), args=(str(script),))
        with_env = _config(
            MCPTransportConfig(
                kind=MCPTransportKind.STDIO,
                command=sys.executable,
                args=(str(script),),
                env=(("MCP_MODE", "readonly"),),
                cwd=str(tmp_path),
            )
        )

        assert await probe.probe(base) != await probe.probe(with_env)

    async def test_unreadable_command_returns_none(self, tmp_path) -> None:
        probe = StdioImplementationProbe()
        config = _stdio_config(command=str(tmp_path / "missing"), cwd=str(tmp_path))
        assert await probe.probe(config) is None

    async def test_remote_transport_returns_none(self) -> None:
        probe = StdioImplementationProbe()
        config = _config(
            MCPTransportConfig(
                kind=MCPTransportKind.STREAMABLE_HTTP,
                url="https://mcp.example.test/tools",
                http_risk_approved=True,
            )
        )
        assert await probe.probe(config) is None

    async def test_relative_script_resolved_against_cwd(self, tmp_path) -> None:
        script = tmp_path / "server.py"
        script.write_text("print('v1')", encoding="utf-8")
        probe = StdioImplementationProbe()
        config = _stdio_config(command=sys.executable, cwd=str(tmp_path), args=("server.py",))
        first = await probe.probe(config)
        script.write_text("print('v2')", encoding="utf-8")
        assert first is not None
        assert await probe.probe(config) != first

    async def test_cwd_change_changes_fingerprint(self, tmp_path) -> None:
        left = tmp_path / "left"
        right = tmp_path / "right"
        left.mkdir()
        right.mkdir()
        probe = StdioImplementationProbe()
        assert await probe.probe(_stdio_config(sys.executable, str(left))) != await probe.probe(
            _stdio_config(sys.executable, str(right))
        )
        assert os.path.isdir(left)
