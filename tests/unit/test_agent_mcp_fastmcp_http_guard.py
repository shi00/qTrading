"""I07：MCP HTTP 出站目标核验单元测试。

真实插入点是 ``httpx2.AsyncClient`` 的传输层：SDK（fastmcp/mcp）的每个请求
（含 initialize 握手、POST/GET/DELETE、SDK 自身重定向跟随）都经
``_GuardedHTTPTransport`` 再委托底层传输。本文件用注入的假传输证明：

1. 合法目标正常发送一次；
2. 跨源、未批准新路径、307/308 后续请求、恢复 GET（``Last-Event-ID``）零发送；
3. ``trust_env=False`` 生效（代理环境变量不介入），重定向不静默跟随。
"""

from __future__ import annotations

import httpx2
import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_fastmcp import (
    _ORIGIN_NOT_APPROVED,
    _PATH_NOT_APPROVED,
    _RESUMPTION_NOT_APPROVED,
    FastMCPClientFactory,
    MCPSessionDiagnostics,
    _ApprovedHTTPTarget,
    _guarded_http_client,
)

_APPROVED_URL = "https://mcp.example.com/rpc"


class _RecordingTransport(httpx2.AsyncBaseTransport):
    """记录每次真实出站请求；可模拟重定向响应。"""

    def __init__(self, *, status_code: int = 200, location: str | None = None) -> None:
        self.requests: list[httpx2.Request] = []
        self.closed = False
        self._status_code = status_code
        self._location = location

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        headers = {"location": self._location} if self._location else {}
        return httpx2.Response(self._status_code, headers=headers, request=request, content=b"{}")

    async def aclose(self) -> None:
        self.closed = True


def _client(inner: _RecordingTransport, url: str = _APPROVED_URL) -> tuple[httpx2.AsyncClient, MCPSessionDiagnostics]:
    diagnostics = MCPSessionDiagnostics()
    factory = _guarded_http_client(_ApprovedHTTPTarget.from_url(url), diagnostics, "svc-test", inner=inner)
    return factory(), diagnostics


def _budget() -> MCPBudgetProfile:
    return MCPBudgetProfile(
        receive_bytes=10_000,
        decoded_bytes=10_000,
        structure_depth=8,
        catalog_items=8,
        catalog_bytes=10_000,
        schema_bytes=10_000,
        single_result_bytes=10_000,
        retention_bytes=10_000,
    )


def _config(kind: MCPTransportKind, url: str = _APPROVED_URL) -> MCPServerConfig:
    return MCPServerConfig(
        identity=MCPServiceIdentity(service_id="svc-test", config_version=1),
        display_name="svc-test",
        transport=MCPTransportConfig(kind=kind, url=url, sse_compat_approved=True),
        budget=_budget(),
    )


class TestApprovedTarget:
    async def test_approved_target_is_sent_once(self) -> None:
        inner = _RecordingTransport()
        client, diagnostics = _client(inner)

        async with client:
            response = await client.post(_APPROVED_URL, json={})

        assert response.status_code == 200
        assert len(inner.requests) == 1
        assert diagnostics.protocol_errors == []
        assert inner.closed is True

    async def test_default_port_and_case_are_normalised(self) -> None:
        inner = _RecordingTransport()
        client, diagnostics = _client(inner)

        async with client:
            await client.post("https://MCP.Example.COM:443/rpc", json={})

        assert len(inner.requests) == 1
        assert diagnostics.protocol_errors == []

    async def test_cross_origin_is_refused_before_any_send(self) -> None:
        inner = _RecordingTransport()
        client, diagnostics = _client(inner)

        with pytest.raises(AgentServiceError) as error:
            await client.post("https://evil.example.net/rpc", json={})
        await client.aclose()

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert inner.requests == []
        assert diagnostics.protocol_errors == [_ORIGIN_NOT_APPROVED]

    async def test_unapproved_path_is_refused_before_any_send(self) -> None:
        inner = _RecordingTransport()
        client, diagnostics = _client(inner)

        with pytest.raises(AgentServiceError) as error:
            await client.post("https://mcp.example.com/other", json={})
        await client.aclose()

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert inner.requests == []
        assert diagnostics.protocol_errors == [_PATH_NOT_APPROVED]

    async def test_resumption_get_is_refused_before_any_send(self) -> None:
        inner = _RecordingTransport()
        client, diagnostics = _client(inner)

        with pytest.raises(AgentServiceError) as error:
            await client.get(_APPROVED_URL, headers={"Last-Event-ID": "7"})
        await client.aclose()

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert inner.requests == []
        assert diagnostics.protocol_errors == [_RESUMPTION_NOT_APPROVED]


class TestRedirectIsNotFollowed:
    @pytest.mark.parametrize("status_code", [307, 308])
    async def test_same_origin_redirect_target_is_not_sent(self, status_code: int) -> None:
        inner = _RecordingTransport(status_code=status_code, location="/rpc-v2")
        client, diagnostics = _client(inner)

        async with client:
            response = await client.post(_APPROVED_URL, json={})
            assert response.status_code == status_code
            assert len(inner.requests) == 1  # 客户端自身不跟随
            next_request = response.next_request
            assert next_request is not None
            with pytest.raises(AgentServiceError) as error:
                await client.send(next_request)

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert len(inner.requests) == 1
        assert diagnostics.protocol_errors == [_PATH_NOT_APPROVED]

    async def test_cross_origin_redirect_target_is_not_sent(self) -> None:
        inner = _RecordingTransport(status_code=307, location="https://evil.example.net/rpc")
        client, diagnostics = _client(inner)

        async with client:
            response = await client.post(_APPROVED_URL, json={})
            next_request = response.next_request
            assert next_request is not None
            with pytest.raises(AgentServiceError) as error:
                await client.send(next_request)

        assert error.value.code is AgentErrorCode.POLICY_DENIED
        assert len(inner.requests) == 1
        assert diagnostics.protocol_errors == [_ORIGIN_NOT_APPROVED]


class TestClientConstruction:
    async def test_trust_env_is_disabled_and_proxy_env_does_not_intercept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
        inner = _RecordingTransport()
        client, _ = _client(inner)

        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
            assert client.max_redirects == 0
            # 代理环境若生效，请求不会到达注入的假传输。
            response = await client.get(_APPROVED_URL)
            assert response.status_code == 200
            assert len(inner.requests) == 1
        finally:
            await client.aclose()

    @pytest.mark.parametrize("kind", [MCPTransportKind.STREAMABLE_HTTP, MCPTransportKind.SSE])
    async def test_http_transports_inject_guarded_factory(self, kind: MCPTransportKind) -> None:
        diagnostics = MCPSessionDiagnostics()
        transport = FastMCPClientFactory()._transport(_config(kind), None, diagnostics=diagnostics, label="svc-test")

        factory = transport.httpx_client_factory
        assert factory is not None
        client = factory(follow_redirects=True)
        try:
            # SDK 传 follow_redirects=True，工厂仍需恒为 False。
            assert client.trust_env is False
            assert client.follow_redirects is False
            assert client.max_redirects == 0
        finally:
            await client.aclose()
