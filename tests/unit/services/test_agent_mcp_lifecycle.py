"""MCP 生命周期门面：测试回执绑定、核准范围、目录漂移、active_identity、断开失败保持状态。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    AgentErrorCode,
    AgentServiceError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPConnectionTestReceipt,
    MCPServerConfig,
    MCPToolDescriptor,
    MCPTransportConfig,
    catalog_digest,
    config_snapshot_digest,
)
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_grant import AppStateMCPGrantStore, MCPConnectGrant
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-life", config_version=1)
_SCHEMA = '{"type":"object","properties":{}}'
_CATALOG = (MCPToolDescriptor("search", _SCHEMA),)
_AUDIT_SCOPE = ("search",)


def _config(identity: MCPServiceIdentity = _IDENTITY, *, display_name: str = "svc-life") -> MCPServerConfig:
    return MCPServerConfig(
        identity=identity,
        display_name=display_name,
        transport=MCPTransportConfig(
            kind=MCPTransportKind.STDIO,
            command=sys.executable,
            cwd=str(Path(sys.executable).parent),
        ),
        budget=MCPBudgetProfile(**{name: 1024 for name in MCP_BUDGET_FIELDS}),
        audit=MCPReadOnlyAudit(
            service=identity,
            tool_scope=_AUDIT_SCOPE,
            implementation_fingerprint="a" * 64,
            evidence_source="manual-review",
            reviewed_at="2026-11-20T00:00:00.000000Z",
            invalidation_conditions=("impl-change",),
        ),
    )


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
        self, *, audit_scope: tuple[str, ...] = _AUDIT_SCOPE, catalog: tuple[MCPToolDescriptor, ...] = _CATALOG
    ):
        self.audit_scope = audit_scope
        self.catalog = catalog
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.disconnect_error: Exception | None = None
        self.test_generation_value: int | None = None
        self.residue_retries = 0

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

    def test_generation(self, identity: MCPServiceIdentity) -> int | None:
        return self.test_generation_value

    async def retry_test_residue_close(self, identity: MCPServiceIdentity) -> int:
        self.residue_retries += 1
        return 1


async def _lifecycle(
    manager: _Manager | None = None, *, configs: tuple[MCPServerConfig, ...] = (_config(),)
) -> tuple[MCPServiceLifecycle, _Manager, _Memory]:
    memory = _Memory()
    store = AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan)
    for config in configs:
        await store.save(config)
    concrete = manager or _Manager()
    lifecycle = MCPServiceLifecycle(
        manager=concrete,  # type: ignore[arg-type]  # 单测替身
        configs=store,
        grants=AppStateMCPGrantStore(read=memory.read, write=memory.write),
    )
    return lifecycle, concrete, memory


def _receipt(
    manager: _Manager,
    config: MCPServerConfig | None = None,
    *,
    generation: int = 1,
    closed: bool = True,
) -> MCPConnectionTestReceipt:
    """构造本轮测试回执：默认与 ``manager`` 当前目录及给定配置一致。"""

    manager.test_generation_value = generation
    target = config or _config()
    return MCPConnectionTestReceipt(
        identity=target.identity,
        tool_names=tuple(sorted(tool.name for tool in manager.catalog)),
        catalog_digest=catalog_digest(manager.catalog),
        research_calls=0,
        closed=closed,
        config_digest=config_snapshot_digest(target),
        generation=generation,
    )


class TestConnect:
    async def test_connect_freezes_grant_and_registers_identity(self) -> None:
        lifecycle, manager, _ = await _lifecycle()

        receipt = await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))

        assert receipt.tools == ("search",)
        assert receipt.catalog_digest == catalog_digest(_CATALOG)
        assert manager.connect_calls == 1
        assert lifecycle.active_identity() == _IDENTITY
        assert lifecycle.is_connected(_IDENTITY)
        assert lifecycle.tool_sets(_IDENTITY) is not None

    async def test_connect_requires_test_receipt_of_same_identity(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(
                _IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager, _config(other))
            )

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "connect requires the connection test receipt of this service"
        assert manager.connect_calls == 0

    async def test_connect_requires_closed_test(self) -> None:
        lifecycle, manager, _ = await _lifecycle()

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager, closed=False))

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert manager.connect_calls == 0

    async def test_connect_rejects_stale_test_generation(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        receipt = _receipt(manager, generation=1)
        manager.test_generation_value = 2  # 之后的又一次测试使旧回执失效

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=receipt)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "connection test receipt is stale; test again"
        assert manager.connect_calls == 0

    async def test_connect_rejects_config_changed_since_test(self) -> None:
        lifecycle, manager, memory = await _lifecycle()
        receipt = _receipt(manager)

        # 测试之后同版配置被改写（此处直接改存储，模拟保存/外部篡改）：摘要不符即拒绝。
        store = AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan)
        await store.save(_config(display_name="改名后的服务"))

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=receipt)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "changed since the connection test" in exc.value.sanitized_message
        assert manager.connect_calls == 0

    async def test_connect_rejects_catalog_changed_since_test(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        receipt = _receipt(manager)
        manager.catalog = (MCPToolDescriptor("search", '{"type":"object","properties":{"q":{}}}'),)

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=receipt)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert exc.value.sanitized_message == "catalog_changed_since_test"
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_connect_rejects_approved_tool_missing_from_live_catalog(self) -> None:
        lifecycle, manager, _ = await _lifecycle(manager=_Manager(audit_scope=("search", "deleted_tool")))
        receipt = _receipt(manager)

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["deleted_tool"], test_receipt=receipt)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert "not in the live tool catalog" in exc.value.sanitized_message
        assert manager.disconnect_calls == 1

    async def test_approved_tools_outside_audit_scope_disconnects(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        manager.catalog = (MCPToolDescriptor("search", _SCHEMA), MCPToolDescriptor("write_file", _SCHEMA))
        receipt = _receipt(manager)

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["write_file"], test_receipt=receipt)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_catalog_drift_requires_reapproval(self) -> None:
        lifecycle, manager, memory = await _lifecycle()
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
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))

        assert exc.value.sanitized_message == "grant_catalog_drift"
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_grant_save_failure_disconnects(self) -> None:
        memory = _Memory()
        store = AppStateMCPConfigStore(read=memory.read, write=memory.write, scan=memory.scan)
        await store.save(_config())

        async def _fail(key: str, value: str) -> None:
            raise RuntimeError("write failed")

        manager = _Manager()
        lifecycle = MCPServiceLifecycle(
            manager=manager,  # type: ignore[arg-type]
            configs=store,
            grants=AppStateMCPGrantStore(read=memory.read, write=_fail),
        )

        with pytest.raises(RuntimeError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))
        assert "write failed" in str(exc.value)
        assert manager.disconnect_calls == 1
        assert lifecycle.active_identity() is None

    async def test_retry_test_residue_close_delegates(self) -> None:
        lifecycle, manager, _ = await _lifecycle()

        closed = await lifecycle.retry_test_residue_close(_IDENTITY)

        assert closed == 1
        assert manager.residue_retries == 1


class TestActiveIdentity:
    async def test_zero_connected_returns_none(self) -> None:
        lifecycle, _, _ = await _lifecycle()
        assert lifecycle.active_identity() is None

    async def test_multiple_connected_returns_none(self) -> None:
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)
        lifecycle, manager, _ = await _lifecycle(configs=(_config(), _config(other)))
        await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))
        await lifecycle.connect(other, approved_tools=["search"], test_receipt=_receipt(manager, _config(other)))

        assert lifecycle.active_identity() is None
        assert len(lifecycle.connected_identities()) == 2


class TestDisconnect:
    async def test_success_clears_state(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))

        await lifecycle.disconnect(_IDENTITY)

        assert lifecycle.active_identity() is None
        assert lifecycle.tool_sets(_IDENTITY) is None

    async def test_failure_keeps_state_and_propagates(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))
        manager.disconnect_error = AgentServiceError(AgentErrorCode.POLICY_DENIED, "in flight", "id")

        with pytest.raises(AgentServiceError) as exc:
            await lifecycle.disconnect(_IDENTITY)
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

        assert lifecycle.active_identity() == _IDENTITY


class TestToolSets:
    async def test_drift_after_connect_yields_none(self) -> None:
        lifecycle, manager, _ = await _lifecycle()
        await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))

        manager.catalog = (MCPToolDescriptor("search", '{"type":"object","properties":{"q":{}}}'),)

        assert lifecycle.tool_sets(_IDENTITY) is None


class TestCancellation:
    async def test_connect_cancellation_propagates(self) -> None:
        class _Cancelling(_Manager):
            async def connect(self, identity: MCPServiceIdentity) -> None:
                raise asyncio.CancelledError

        manager = _Cancelling()
        lifecycle, _, _ = await _lifecycle(manager)
        with pytest.raises(asyncio.CancelledError) as exc:
            await lifecycle.connect(_IDENTITY, approved_tools=["search"], test_receipt=_receipt(manager))
        assert type(exc.value) is asyncio.CancelledError
