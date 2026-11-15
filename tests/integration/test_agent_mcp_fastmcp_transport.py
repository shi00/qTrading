"""E-02-5 needs-spike 实证：真实 FastMCP stdio 传输。

验证的是本仓库适配层的可证事实（不重复测试 FastMCP 本身）：

1. 传输插入点：``FastMCPClientFactory`` 以绝对命令 + 最小环境 + ``log_file``
   启动受管子进程，能被共享连接管理器当作 ``MCPClientSessionPort`` 使用；
2. 真实发现/调用：``list_tools`` 得到完整 schema 文本，``call_tool`` 返回结构化字典；
3. 逐 send 门：关闭后拒绝发送；deadline 已过拒绝发送（都在 SDK 请求之前）；
4. FD 级 stderr：子进程 stderr 写入文件描述符，关闭后可按尾部读回；
5. 启动失败映射为 ``CONFIG_UNAVAILABLE``，不泄露进程输出细节。

残留（不得据本文件宣称）：原始接收字节计量、子进程退出码可观测性。
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPServiceIdentity,
    MCPTransportKind,
)
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_budget import MCPDeadline
from services.agent_service.mcp_fastmcp import FastMCPClientFactory

pytestmark = [pytest.mark.integration, pytest.mark.no_db, pytest.mark.asyncio]

_SERVER_SOURCE = """
import sys

from fastmcp import FastMCP

print("spike-server-boot", file=sys.stderr, flush=True)

mcp = FastMCP("spike")


@mcp.tool
def echo(text: str) -> dict:
    "Echo text back."
    return {"echo": text, "len": len(text)}


if __name__ == "__main__":
    mcp.run()
"""


@pytest.fixture()
def server_script(tmp_path: Path) -> Path:
    path = tmp_path / "spike_server.py"
    path.write_text(textwrap.dedent(_SERVER_SOURCE), encoding="utf-8")
    return path


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=4_000_000,
        decoded_bytes=4_000_000,
        structure_depth=64,
        catalog_items=64,
        catalog_bytes=1_000_000,
        schema_bytes=1_000_000,
        single_result_bytes=1_000_000,
        retention_bytes=4_000_000,
    )


def _config(script: Path, *, connect_deadline_s: int = 30, tool_deadline_s: int = 30) -> MCPServerConfig:
    return MCPServerConfig(
        identity=MCPServiceIdentity(service_id="spike", config_version=1),
        display_name="spike",
        transport=MCPTransportConfig(
            kind=MCPTransportKind.STDIO,
            command=sys.executable,
            args=(str(script),),
        ),
        budget=_budget(),
        connect_deadline_s=connect_deadline_s,
        tool_deadline_s=tool_deadline_s,
    )


class TestRealStdioTransport:
    async def test_discover_and_call_real_server(self, server_script: Path) -> None:
        factory = FastMCPClientFactory()
        session = await factory.open(_config(server_script))
        try:
            tools = await session.list_tools(deadline_s=30)
            assert [tool.name for tool in tools] == ["echo"]
            schema = json.loads(tools[0].schema_json)
            assert schema["properties"]["text"]["type"] == "string"

            result = await session.call_tool("echo", {"text": "hi"}, deadline_s=30)
            assert result == {"echo": "hi", "len": 2}
            assert session.gate.sends == 2
        finally:
            await session.aclose()

    async def test_stderr_is_captured_at_fd_level(self, server_script: Path) -> None:
        factory = FastMCPClientFactory()
        session = await factory.open(_config(server_script))
        await session.list_tools(deadline_s=30)
        await session.aclose()

        assert "spike-server-boot" in session.diagnostics.stderr_tail

    async def test_closed_session_refuses_to_send(self, server_script: Path) -> None:
        factory = FastMCPClientFactory()
        session = await factory.open(_config(server_script))
        await session.aclose()

        with pytest.raises(AgentServiceError) as error:
            await session.call_tool("echo", {"text": "x"}, deadline_s=30)
        assert error.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        # 门在 SDK 请求之前拦截：只记一次尝试，不产生真实发送
        assert session.gate.sends == 1

    async def test_expired_deadline_blocks_before_send(self, server_script: Path) -> None:
        ticks = iter([0.0, 100.0, 100.0, 100.0])

        def _monotonic() -> float:
            return next(ticks, 100.0)

        factory = FastMCPClientFactory(deadline_factory=lambda seconds: MCPDeadline(seconds=1, monotonic=_monotonic))
        session = await factory.open(_config(server_script))
        try:
            with pytest.raises(AgentServiceError) as error:
                await session.list_tools(deadline_s=30)
            assert error.value.code is AgentErrorCode.POLICY_DENIED
            assert session.diagnostics.protocol_errors == []
        finally:
            await session.aclose()

    async def test_startup_failure_maps_to_config_unavailable(self, tmp_path: Path) -> None:
        factory = FastMCPClientFactory()
        with pytest.raises(AgentServiceError) as error:
            await factory.open(_config(tmp_path / "missing_server.py", connect_deadline_s=5))
        assert error.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert "missing_server" not in error.value.sanitized_message
