"""MCP 服务删除阻删与角色分配（F14 残余 / F15 / J25）。"""

from __future__ import annotations

import json
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
    RoleMCPAssignment,
    RoleMCPServiceBinding,
    decode_role_mcp_assignment,
)
from services.agent_service.mcp import MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_grant import AppStateMCPGrantStore
from services.agent_service.mcp_lifecycle import MCPServiceDeleteBlocked, MCPServiceLifecycle
from services.agent_service.mcp_role import AppStateMCPRoleAssignmentStore

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-delete", config_version=1)


def _config(
    *,
    headers: tuple[tuple[str, str], ...] = (),
    env: tuple[tuple[str, str], ...] = (),
    secret_env: tuple[tuple[str, str], ...] = (),
) -> MCPServerConfig:
    if headers:
        transport = MCPTransportConfig(
            kind=MCPTransportKind.STREAMABLE_HTTP,
            url="https://mcp.example.invalid/rpc",
            headers=headers,
        )
    else:
        transport = MCPTransportConfig(
            kind=MCPTransportKind.STDIO,
            command=sys.executable,
            cwd=str(Path(sys.executable).parent),
            env=env,
            secret_env=secret_env,
        )
    return MCPServerConfig(
        identity=_IDENTITY,
        display_name="svc-delete",
        transport=transport,
        budget=MCPBudgetProfile(**{name: 1024 for name in MCP_BUDGET_FIELDS}),
        audit=MCPReadOnlyAudit(
            service=_IDENTITY,
            tool_scope=("search",),
            implementation_fingerprint="a" * 64,
            evidence_source="manual-review",
            reviewed_at="2026-11-20T00:00:00.000000Z",
            invalidation_conditions=("impl-change",),
        ),
    )


class _Memory:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def read(self, key: str) -> str | None:
        return self.values.get(key)

    async def write(self, key: str, value: str) -> None:
        self.values[key] = value

    async def scan(self, prefix: str) -> dict[str, str]:
        return {key: value for key, value in self.values.items() if key.startswith(prefix)}

    async def delete(self, key: str) -> bool:
        self.deleted.append(key)
        return self.values.pop(key, None) is not None


class _Manager:
    def __init__(self, store: AppStateMCPConfigStore) -> None:
        self._store = store
        self.frozen: tuple[str, ...] = ()
        self.config_deleted = False

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return self.frozen

    async def delete_config(self, identity: MCPServiceIdentity) -> bool:
        self.config_deleted = True
        return await self._store.delete(identity)


class _World:
    def __init__(self, *, roles_wired: bool = True) -> None:
        self.memory = _Memory()
        self.configs = AppStateMCPConfigStore(
            read=self.memory.read,
            write=self.memory.write,
            scan=self.memory.scan,
            delete=self.memory.delete,
        )
        self.grants = AppStateMCPGrantStore(read=self.memory.read, write=self.memory.write, delete=self.memory.delete)
        self.roles = AppStateMCPRoleAssignmentStore(
            read=self.memory.read,
            write=self.memory.write,
            scan=self.memory.scan,
            delete=self.memory.delete,
        )
        self.manager = _Manager(self.configs)
        self.lifecycle = MCPServiceLifecycle(
            manager=self.manager,  # type: ignore[arg-type]  # 单测替身
            configs=self.configs,
            grants=self.grants,
            roles=self.roles if roles_wired else None,
        )

    async def save_config(
        self,
        *,
        headers: tuple[tuple[str, str], ...] = (),
        env: tuple[tuple[str, str], ...] = (),
        secret_env: tuple[tuple[str, str], ...] = (),
    ) -> None:
        await self.configs.save(_config(headers=headers, env=env, secret_env=secret_env))


def _assignment(tools: tuple[str, ...] = ("search",)) -> RoleMCPAssignment:
    return RoleMCPAssignment(
        role_id="main",
        role_version="1",
        available=(RoleMCPServiceBinding(identity=_IDENTITY, tools=tools),),
        default_service=_IDENTITY,
    )


class TestRoleAssignment:
    async def test_save_and_reload_roundtrip(self) -> None:
        world = _World()
        await world.save_config()

        await world.lifecycle.save_role_assignment(_assignment())

        loaded = await world.lifecycle.list_role_assignments()
        assert len(loaded) == 1
        assert loaded[0].default_service == _IDENTITY
        assert loaded[0].available[0].tools == ("search",)
        # wire 可逆（持久化格式严格解码）
        key = AppStateMCPRoleAssignmentStore.key_for("main", "1")
        assert decode_role_mcp_assignment(json.loads(world.memory.values[key])) == loaded[0]

    async def test_rejects_tool_outside_declared_scope_and_does_not_persist(self) -> None:
        world = _World()
        await world.save_config()

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.save_role_assignment(_assignment(tools=("search", "write")))

        assert exc.value.code is AgentErrorCode.INPUT_INVALID
        assert await world.lifecycle.list_role_assignments() == ()

    async def test_rejects_unconfigured_service(self) -> None:
        world = _World()

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.save_role_assignment(_assignment())

        assert exc.value.code is AgentErrorCode.INPUT_INVALID

    async def test_rejects_default_outside_available(self) -> None:
        world = _World()
        await world.save_config()
        other = MCPServiceIdentity(service_id="svc-other", config_version=1)

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.save_role_assignment(
                RoleMCPAssignment(
                    role_id="main",
                    role_version="1",
                    available=(RoleMCPServiceBinding(identity=_IDENTITY, tools=()),),
                    default_service=other,
                )
            )

        assert exc.value.code is AgentErrorCode.INPUT_INVALID


class TestDeleteService:
    async def test_requires_explicit_confirmation(self) -> None:
        world = _World()
        await world.save_config()

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.delete_service(_IDENTITY)

        assert exc.value.code is AgentErrorCode.POLICY_DENIED
        assert not world.memory.deleted

    async def test_blocked_by_role_assignment(self) -> None:
        world = _World()
        await world.save_config()
        await world.lifecycle.save_role_assignment(_assignment())

        with pytest.raises(MCPServiceDeleteBlocked) as exc:
            await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert exc.value.role_refs == ("main@1",)
        assert exc.value.research_ids == ()
        assert not world.manager.config_deleted

    async def test_blocked_by_frozen_research(self) -> None:
        world = _World()
        await world.save_config()
        world.manager.frozen = ("session-1",)

        with pytest.raises(MCPServiceDeleteBlocked) as exc:
            await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert exc.value.research_ids == ("session-1",)
        assert exc.value.role_refs == ()
        assert not world.manager.config_deleted

    async def test_role_store_unwired_fails_closed(self) -> None:
        world = _World(roles_wired=False)
        await world.save_config()

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
        assert not world.manager.config_deleted

    async def test_deletes_config_and_grant_and_reports_credential_refs(self) -> None:
        world = _World()
        await world.save_config(headers=(("Authorization", "keyring:mcp-token"), ("X-Api-Key", "env:MCP_API_KEY")))

        receipt = await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert receipt.deleted_config is True
        assert receipt.deleted_grant is False
        assert receipt.credential_references == ("env:MCP_API_KEY", "keyring:mcp-token")
        assert await world.configs.load(_IDENTITY) is None
        assert world.memory.values.get(AppStateMCPGrantStore.key_for(_IDENTITY)) is None
        # 授权记录（若存在）与配置都被真正删除，而不是靠空值冒充
        assert world.memory.deleted

    async def test_stdio_secret_env_references_are_inventoried_on_delete(self) -> None:
        """删除 stdio 服务同样盘点凭据引用（供人工撤销），普通字面量不回显。"""

        world = _World()
        await world.save_config(
            env=(("LOG_LEVEL", "debug"),),
            secret_env=(("MCP_TOKEN", "keyring:mcp-token"), ("API_KEY", "env:MCP_API_KEY")),
        )

        receipt = await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert receipt.deleted_config is True
        assert receipt.credential_references == ("env:MCP_API_KEY", "keyring:mcp-token")
        assert "debug" not in receipt.credential_references

    async def test_missing_config_is_not_silently_idempotent(self) -> None:
        world = _World()

        with pytest.raises(AgentServiceError) as exc:
            await world.lifecycle.delete_service(_IDENTITY, confirmation=True)

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE
