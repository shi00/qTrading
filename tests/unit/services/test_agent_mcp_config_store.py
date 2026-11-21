"""``app_state`` MCP 配置存储：键格式、往返、损坏载荷、写失败传播、列表。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, MCPBudgetProfile, MCPServiceIdentity, MCPTransportKind
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_config_codec import encode_mcp_server_config
from services.agent_service.mcp_config_store import AppStateMCPConfigStore

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-store", config_version=1)


def _config(identity: MCPServiceIdentity = _IDENTITY, *, display_name: str = "存储服务") -> MCPServerConfig:
    return MCPServerConfig(
        identity=identity,
        display_name=display_name,
        transport=MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.test/tools",
            http_risk_approved=True,
        ),
        budget=MCPBudgetProfile(
            receive_bytes=1,
            decoded_bytes=1,
            structure_depth=1,
            catalog_items=1,
            catalog_bytes=1,
            schema_bytes=1,
            single_result_bytes=1,
            retention_bytes=1,
        ),
    )


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.write_error: Exception | None = None
        self.read_error: Exception | None = None

    async def read(self, key: str) -> str | None:
        if self.read_error is not None:
            raise self.read_error
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        if self.write_error is not None:
            raise self.write_error
        self.values[key] = value

    async def scan(self, prefix: str) -> Mapping[str, str]:
        return {key: value for key, value in self.values.items() if key.startswith(prefix)}


def _store(memory: _Memory, *, with_scan: bool = True) -> AppStateMCPConfigStore:
    return AppStateMCPConfigStore(
        read=memory.read,
        write=memory.write,
        scan=memory.scan if with_scan else None,
    )


class TestAppStateMCPConfigStore:
    async def test_key_format_and_round_trip(self) -> None:
        memory = _Memory()
        store = _store(memory)
        await store.save(_config())

        key = AppStateMCPConfigStore.key_for(_IDENTITY)
        assert key == f"agent.mcp.service.{_IDENTITY.stable_key()}"
        assert key in memory.values
        assert await store.load(_IDENTITY) == _config()

    async def test_missing_returns_none(self) -> None:
        store = _store(_Memory())
        assert await store.load(_IDENTITY) is None

    async def test_write_failure_propagates(self) -> None:
        memory = _Memory()
        memory.write_error = RuntimeError("db down")
        with pytest.raises(RuntimeError) as exc:
            await _store(memory).save(_config())
        assert "db down" in str(exc.value)

    async def test_corrupt_payload_is_explicit_failure(self) -> None:
        memory = _Memory()
        memory.values[AppStateMCPConfigStore.key_for(_IDENTITY)] = "{not-json"
        with pytest.raises(AgentServiceError) as exc:
            await _store(memory).load(_IDENTITY)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_identity_mismatch_rejected(self) -> None:
        memory = _Memory()
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)
        payload: Any = encode_mcp_server_config(_config(other))
        memory.values[AppStateMCPConfigStore.key_for(_IDENTITY)] = json.dumps(payload)
        with pytest.raises(AgentServiceError) as exc:
            await _store(memory).load(_IDENTITY)
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_list_configs_sorted(self) -> None:
        memory = _Memory()
        store = _store(memory)
        await store.save(_config(MCPServiceIdentity(service_id="svc-b", config_version=1), display_name="B 服务"))
        await store.save(_config(MCPServiceIdentity(service_id="svc-a", config_version=1), display_name="A 服务"))

        listed = await store.list_configs()
        assert [item.identity.service_id for item in listed] == ["svc-a", "svc-b"]

    async def test_list_without_scan_is_explicit_failure(self) -> None:
        store = _store(_Memory(), with_scan=False)
        with pytest.raises(AgentServiceError) as exc:
            await store.list_configs()
        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
