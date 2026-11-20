"""启连授权记录：构造校验、编解码、存储往返与损坏载荷。"""

from __future__ import annotations

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, AgentValidationError, MCPServiceIdentity, sha256_hex
from services.agent_service.mcp_grant import AppStateMCPGrantStore, MCPConnectGrant, MCPToolSets

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-grant", config_version=3)
_DIGEST = sha256_hex(b"catalog")


def _grant(*tools: str) -> MCPConnectGrant:
    return MCPConnectGrant(
        identity=_IDENTITY,
        tools=tools,
        catalog_digest=_DIGEST,
        approved_at="2026-11-20T00:00:00.000000Z",
    )


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def read(self, key: str) -> str | None:
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        self.values[key] = value


class TestMCPConnectGrant:
    def test_empty_tools_is_valid(self) -> None:
        assert _grant().tools == ()

    def test_invalid_digest_rejected(self) -> None:
        with pytest.raises(AgentValidationError):
            MCPConnectGrant(
                identity=_IDENTITY,
                tools=(),
                catalog_digest="not-a-digest",
                approved_at="2026-11-20T00:00:00.000000Z",
            )

    def test_invalid_tool_name_rejected(self) -> None:
        with pytest.raises(AgentValidationError):
            _grant("")

    def test_invalid_timestamp_rejected(self) -> None:
        with pytest.raises(AgentValidationError):
            MCPConnectGrant(
                identity=_IDENTITY,
                tools=(),
                catalog_digest=_DIGEST,
                approved_at="2026-11-20T00:00:00Z",
            )

    def test_codec_round_trip(self) -> None:
        grant = _grant("search")
        assert MCPConnectGrant.decode(grant.encode()) == grant

    def test_tool_sets_from_grant(self) -> None:
        sets = MCPToolSets.from_grant(_grant("search", "quote"))
        assert sets.role_tools == sets.request_tools == sets.permission_tools == ("search", "quote")


class TestAppStateMCPGrantStore:
    async def test_key_and_round_trip(self) -> None:
        memory = _Memory()
        store = AppStateMCPGrantStore(read=memory.read, write=memory.write)
        await store.save(_grant("search"))

        assert AppStateMCPGrantStore.key_for(_IDENTITY) == f"agent.mcp.grant.{_IDENTITY.stable_key()}"
        assert await store.load(_IDENTITY) == _grant("search")

    async def test_missing_returns_none(self) -> None:
        store = AppStateMCPGrantStore(read=_Memory().read, write=_Memory().write)
        assert await store.load(_IDENTITY) is None

    async def test_corrupt_payload_is_explicit_failure(self) -> None:
        memory = _Memory()
        memory.values[AppStateMCPGrantStore.key_for(_IDENTITY)] = "[]"
        store = AppStateMCPGrantStore(read=memory.read, write=memory.write)
        with pytest.raises(AgentServiceError) as exc:
            await store.load(_IDENTITY)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
