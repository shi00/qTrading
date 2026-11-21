"""MCP 生命周期门面：核准范围、目录漂移、active_identity、断开失败保持状态。"""

from __future__ import annotations

import asyncio

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPServiceIdentity,
    sha256_hex,
)
from services.agent_service.mcp import MCPToolDescriptor, catalog_digest
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_grant import AppStateMCPGrantStore, MCPConnectGrant
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-life", config_version=1)
_SCHEMA = '{"type":"object","properties":{}}'
_CATALOG = (MCPToolDescriptor("search", _SCHEMA),)


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def read(self, key: str) -> str | None:
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        self.values[key] = value

    async def scan(self, prefix: str) -> dict[str, str]:
        return {key: value for key, value in self.values.items() if key.startswith(prefix)}


class _Manager:
    def __init__(
        self, *, audit_scope: tuple[str, ...] = ("search",), catalog: tuple[MCPToolDescriptor, ...] = _CATALOG
    ):
        self.audit_scope = audit_scope
        self.catalog = catalog
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.disconnect_error: Exception | None = None

    async def connect(self, identity: MCPServiceIdentity) -> None:
        self.connect_calls += 1
        self.connected = True

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        self.disconnect_calls += 1
        if self.disconnect_error is not None:
            raise self.disconnect_error
        self.connected = False

    def is_connected(self, identity: MCPServiceIdentity) -> bool:
        return self.connected

    def frozen_catalog(self, identity: MCPServiceIdentity) -> tuple[MCPToolDescriptor, ...]:
        if not self.connected:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "not connected", identity.stable_key())
        return self.catalog

    def audit_tool_scope(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return self.audit_scope


def _lifecycle(manager: _Manager | None = None) -> tuple[MCPServiceLifecycle, _Manager, _Memory]:
    memory = _Memory()
    concrete = manager or _Manager()
    lifecycle = MCPServiceLifecycle(
        manager=concrete,  # type: ignore[arg-type]  # 单测替身
        configs=AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan),
        grants=AppStateMCPGrantStore(read=memory.read, write=memory.write),
    )
    return lifecycle, concrete, memory


class TestConnect:
    async def test_connect_freezes_grant_and_registers_identity(self) -> None:
        lifecycle, manager, _ = _lifecycle()

        receipt = await lifecycle.connect(_IDENTITY, approved_tools=["search"])

        assert receipt.tools == ("search",)
        assert receipt.catalog_digest == catalog_digest(_CATALOG)
        assert manager.connect_calls == 1
        assert lifecycle.active_identity() == _IDENTITY
        assert lifecycle.is_connected(_IDENTITY)
        assert lifecycle.tool_sets(_IDENTITY) is not None

    async def test_approved_tools_outside_audit_scope_disconnects(self) -> None:
        lifecycle, manager, _ = _lifecycle()

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["write_file"])

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_catalog_drift_requires_reapproval(self) -> None:
        lifecycle, manager, memory = _lifecycle()
        grants = AppStateMCPGrantStore(read=memory.read, write=memory.write)
        await grants.save(
            MCPConnectGrant(
                identity=_IDENTITY,
                tools=("search",),
                catalog_digest=sha256_hex(b"old-catalog"),
                approved_at="2026-11-20T00:00:00.000000Z",
            )
        )

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"])

        assert exc.value.sanitized_message == "grant_catalog_drift"
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_grant_save_failure_disconnects(self) -> None:
        memory = _Memory()

        async def _fail(key: str, value: str) -> None:
            raise RuntimeError("write failed")

        manager = _Manager()
        lifecycle = MCPServiceLifecycle(
            manager=manager,  # type: ignore[arg-type]
            configs=AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan),
            grants=AppStateMCPGrantStore(read=memory.read, write=_fail),
        )

        with pytest.raises(RuntimeError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        assert "write failed" in str(exc.value)
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None


class TestActiveIdentity:
    async def test_zero_connected_returns_none(self) -> None:
        lifecycle, _, _ = _lifecycle()
        assert lifecycle.active_identity() is None

    async def test_multiple_connected_returns_none(self) -> None:
        lifecycle, _, _ = _lifecycle()
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)
        await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        await lifecycle.connect(other, approved_tools=["search"])

        assert lifecycle.active_identity() is None
        assert len(lifecycle.connected_identities()) == 2


class TestDisconnect:
    async def test_success_clears_state(self) -> None:
        lifecycle, _, _ = _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"])

        await lifecycle.disconnect(_IDENTITY)

        assert lifecycle.active_identity() is None
        assert lifecycle.tool_sets(_IDENTITY) is None

    async def test_failure_keeps_state_and_propagates(self) -> None:
        lifecycle, manager, _ = _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        manager.disconnect_error = AgentServiceError(AgentErrorCode.POLICY_DENIED, "in flight", "id")

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.disconnect(_IDENTITY)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

        assert lifecycle.active_identity() == _IDENTITY


class TestToolSets:
    async def test_drift_after_connect_yields_none(self) -> None:
        lifecycle, manager, _ = _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"])

        manager.catalog = (MCPToolDescriptor("search", '{"type":"object","properties":{"q":{}}}'),)

        assert lifecycle.tool_sets(_IDENTITY) is None


class TestCancellation:
    async def test_connect_cancellation_propagates(self) -> None:
        class _Cancelling(_Manager):
            async def connect(self, identity: MCPServiceIdentity) -> None:
                raise asyncio.CancelledError

        lifecycle, _, _ = _lifecycle(_Cancelling())
        with pytest.raises(asyncio.CancelledError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"])
        assert type(exc.value) is asyncio.CancelledError
