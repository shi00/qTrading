"""I03：MCP 入站限额必须绑定真实插入点（解析前拒绝），无插入点即准入拒绝。

缺陷复核（修复前）：连接管理器只校验预算「有值」（``is_admitted``）就准入，而目录/
解码在 SDK 构造完整对象之后才计；``receive_bytes`` / ``structure_depth`` 的前置辅助
函数没有任何调用点。本文件证明修复后的合同：

1. 大帧（超限单条消息）在字节交付 SDK 之前拒绝；
2. 深 JSON 在完整帧到达之前拒绝（增量扫描，不做完整解析）；
3. progress 帧洪泛超出接收预算即拒绝（且不续期 deadline）；
4. 有真实插入点的传输（streamable HTTP / SSE）准入通过；
5. stdio 的前置限额族已绑定真实插入点（``_BudgetedLineReader.feed``），保存与启连均准入；
6. 取消沿入站包装层原样传播（不吞 ``CancelledError``）；
7. 压缩响应无法前置判定帧长/深度，显式拒绝而不是按「未限流」放行。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_budget import (
    MCP_INBOUND_LIMIT_BINDINGS,
    PRE_PARSE_LIMIT_FAMILIES,
    MCPBudgetExceeded,
    MCPBudgetGuard,
    missing_pre_parse_bindings,
)
from services.agent_service.mcp_fastmcp import (
    MCPSessionDiagnostics,
    _ApprovedHTTPTarget,
    _guarded_http_client,
)

_URL = "https://mcp.example.test/rpc"
_SCHEMA = '{"type":"object","properties":{}}'
_FINGERPRINT = sha256_hex(b"mcp-local-implementation")


def _budget(**overrides: int | None) -> MCPBudgetProfile:
    values: dict[str, int | None] = {
        "receive_bytes": 1000,
        "decoded_bytes": 2000,
        "structure_depth": 8,
        "catalog_items": 10,
        "catalog_bytes": 10_000,
        "schema_bytes": 10_000,
        "single_result_bytes": 10_000,
        "retention_bytes": 20_000,
    }
    values.update(overrides)
    return MCPBudgetProfile(**values)


def _guard(**overrides: int | None) -> MCPBudgetGuard:
    return MCPBudgetGuard(_budget(**overrides), identity="svc-test@1")


class _ChunkedBody(httpx2.AsyncByteStream):
    """按块产出响应的字节流；记录真正被底层拉取的块（证明「未送达即拒绝」）。"""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.pulled: list[bytes] = []
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.pulled.append(chunk)
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


class _StreamingTransport(httpx2.AsyncBaseTransport):
    """返回流式响应（不预读 content），使被包装的响应流成为唯一字节来源。"""

    def __init__(self, body: httpx2.AsyncByteStream, *, headers: dict[str, str] | None = None) -> None:
        self._body = body
        self._headers = headers or {}
        self.requests = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests += 1
        return httpx2.Response(200, headers=self._headers, stream=self._body, request=request)

    async def aclose(self) -> None:
        return None


def _client(
    body: httpx2.AsyncByteStream,
    *,
    guard: MCPBudgetGuard,
    headers: dict[str, str] | None = None,
) -> tuple[httpx2.AsyncClient, MCPSessionDiagnostics]:
    diagnostics = MCPSessionDiagnostics()
    factory = _guarded_http_client(
        _ApprovedHTTPTarget.from_url(_URL),
        diagnostics,
        "svc-test@1",
        inner=_StreamingTransport(body, headers=headers),
        inbound=guard,
    )
    return factory(), diagnostics


class TestPreParseLimits:
    async def test_single_frame_over_receive_budget_is_refused_before_delivery(self) -> None:
        """大帧：第 3 块即超出接收预算，后续块从未拉取，完整消息从未组装。"""

        body = _ChunkedBody([b"x" * 10] * 10)
        client, _diagnostics = _client(body, guard=_guard(receive_bytes=25))

        with pytest.raises(MCPBudgetExceeded) as exc:
            async with client:
                await client.get(_URL)

        assert exc.value.family == "receive_bytes"
        assert len(body.pulled) == 3

    async def test_deep_json_is_refused_before_the_frame_completes(self) -> None:
        """深 JSON：深度在第 3 块即超限，末块从未送达（判定先于完整帧/解析）。"""

        body = _ChunkedBody([b'{"a":', b'{"b":', b'{"c":{"d":{"e":1}', b"}}}}"])
        client, _diagnostics = _client(body, guard=_guard(receive_bytes=1000, structure_depth=3))

        with pytest.raises(MCPBudgetExceeded) as exc:
            async with client:
                await client.get(_URL)

        assert exc.value.family == "structure_depth"
        assert len(body.pulled) == 3
        # 拒绝类型是预算错误（不是 JSONDecodeError/ValidationError）：说明没有走完整解析。
        assert not isinstance(exc.value, ValueError)

    async def test_progress_frames_cannot_exceed_receive_budget(self) -> None:
        """progress 帧同样是入站字节：超出接收预算的第三帧不会被送达。"""

        frame = b'data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"progress":1}}\n\n'
        body = _ChunkedBody([frame] * 4)
        client, _diagnostics = _client(body, guard=_guard(receive_bytes=len(frame) * 2))

        with pytest.raises(MCPBudgetExceeded) as exc:
            async with client:
                await client.get(_URL)

        assert exc.value.family == "receive_bytes"
        assert len(body.pulled) == 3

    async def test_compressed_response_is_refused_as_unverifiable(self) -> None:
        """压缩响应无法前置判定帧长/深度：拒绝，且不拉取任何字节。"""

        body = _ChunkedBody([b"\x1f\x8bcompressed"])
        client, diagnostics = _client(body, guard=_guard(), headers={"content-encoding": "gzip"})

        with pytest.raises(AgentServiceError) as exc:
            async with client:
                await client.get(_URL)

        assert exc.value.code is AgentErrorCode.CAPABILITY_UNVERIFIED
        assert body.pulled == []
        assert body.closed is True
        assert diagnostics.protocol_errors == ["inbound_content_encoding_unverifiable"]

    async def test_cancellation_propagates_through_inbound_wrapper(self) -> None:
        """取消必须原样传播（R2）：包装层不捕获 BaseException。"""

        class _Cancelling(httpx2.AsyncByteStream):
            def __init__(self) -> None:
                self.yielded = 0

            async def __aiter__(self) -> AsyncIterator[bytes]:
                self.yielded += 1
                yield b'{"a":'
                raise asyncio.CancelledError()

            async def aclose(self) -> None:
                return None

        stream = _Cancelling()
        client, _diagnostics = _client(stream, guard=_guard())

        with pytest.raises(asyncio.CancelledError) as exc:
            async with client:
                await client.get(_URL)

        # 类型原样穿透（未被转译为 AgentServiceError），且包装层确实读到首个分片。
        assert type(exc.value) is asyncio.CancelledError
        assert stream.yielded == 1


def _identity() -> MCPServiceIdentity:
    return MCPServiceIdentity(service_id="svc-local-fs", config_version=1)


def _transport(kind: MCPTransportKind) -> MCPTransportConfig:
    if kind is MCPTransportKind.STDIO:
        import os
        import sys

        return MCPTransportConfig(kind=kind, command=sys.executable, cwd=os.path.dirname(sys.executable))
    return MCPTransportConfig(kind=kind, url=_URL, http_risk_approved=True, sse_compat_approved=True)


def _config(kind: MCPTransportKind, **overrides: Any) -> MCPServerConfig:
    values: dict[str, Any] = {
        "identity": _identity(),
        "display_name": "本地 MCP",
        "transport": _transport(kind),
        "budget": _budget(),
        "audit": MCPReadOnlyAudit(
            service=_identity(),
            tool_scope=("read_file",),
            implementation_fingerprint=_FINGERPRINT,
            evidence_source="local-executable-inspection",
            reviewed_at="2026-11-13T00:00:00.000000Z",
            invalidation_conditions=("executable", "entry_script"),
        ),
    }
    values.update(overrides)
    return MCPServerConfig(**values)


class _Store:
    def __init__(self, config: MCPServerConfig | None) -> None:
        self.config = config
        self.saved: list[MCPServerConfig] = []

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None:
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
        self.opened = 0
        self.session = _Session()

    async def open(self, config: MCPServerConfig, *, guard: MCPBudgetGuard | None = None) -> _Session:
        self.guard = guard
        self.opened += 1
        return self.session


class _Probe:
    async def probe(self, config: MCPServerConfig) -> str | None:
        return _FINGERPRINT


class _NoFrozenUsers:
    """无研究冻结该配置版（入站限额用例不涉及断开/改配置）。"""

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ()


def _manager(config: MCPServerConfig | None) -> tuple[SharedMCPConnectionManager, _Store, _Factory]:
    store = _Store(config)
    factory = _Factory()
    return (
        SharedMCPConnectionManager(store=store, factory=factory, probe=_Probe(), usage=_NoFrozenUsers()),
        store,
        factory,
    )


class TestInsertionPointAdmission:
    def test_http_transports_bind_both_pre_parse_families(self) -> None:
        for kind in (MCPTransportKind.STREAMABLE_HTTP, MCPTransportKind.SSE):
            bound = {binding.family for binding in MCP_INBOUND_LIMIT_BINDINGS[kind]}
            assert set(PRE_PARSE_LIMIT_FAMILIES) <= bound
            assert missing_pre_parse_bindings(kind, _budget()) == ()
            for binding in MCP_INBOUND_LIMIT_BINDINGS[kind]:
                assert binding.insertion_point and binding.source

    def test_stdio_binds_both_pre_parse_families(self) -> None:
        bound = {binding.family for binding in MCP_INBOUND_LIMIT_BINDINGS[MCPTransportKind.STDIO]}
        assert set(PRE_PARSE_LIMIT_FAMILIES) <= bound
        assert missing_pre_parse_bindings(MCPTransportKind.STDIO, _budget()) == ()
        for binding in MCP_INBOUND_LIMIT_BINDINGS[MCPTransportKind.STDIO]:
            assert binding.insertion_point.startswith("services.agent_service.mcp_stdio_transport.")
            assert binding.source

    async def test_transport_with_insertion_point_is_admitted(self) -> None:
        config = _config(MCPTransportKind.STREAMABLE_HTTP)
        manager, store, factory = _manager(config)

        await manager.save_config(config)
        await manager.connect(_identity())

        assert store.saved == [config]
        assert factory.opened == 1

    async def test_stdio_is_admitted_at_save(self) -> None:
        """stdio 前置限额族已有真实插入点：保存不再因 ``CAPABILITY_UNVERIFIED`` 被拒。"""

        config = _config(MCPTransportKind.STDIO)
        manager, store, factory = _manager(config)

        await manager.save_config(config)

        assert store.saved == [config]
        assert factory.opened == 0

    async def test_stdio_is_admitted_at_connect(self) -> None:
        """存储值不被信任：加载后的 stdio 配置在启连点重新过准入并通过。"""

        config = _config(MCPTransportKind.STDIO)
        manager, _store, factory = _manager(config)

        await manager.connect(_identity())

        assert factory.opened == 1
