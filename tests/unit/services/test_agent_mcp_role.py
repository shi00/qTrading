"""`mcp_role` 存储与工具范围校验（角色 MCP 分配）。"""

from __future__ import annotations

import json

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPServiceIdentity,
    RoleMCPAssignment,
    RoleMCPServiceBinding,
)
from services.agent_service.mcp_role import (
    AppStateMCPRoleAssignmentStore,
    validate_assignment_tools_against_configs,
)

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-a", config_version=1)
_OTHER = MCPServiceIdentity(service_id="svc-b", config_version=2)


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


def _store(memory: _Memory, *, delete=None) -> AppStateMCPRoleAssignmentStore:
    return AppStateMCPRoleAssignmentStore(
        read=memory.read,
        write=memory.write,
        scan=memory.scan,
        delete=delete,
    )


def _assignment(*, default: MCPServiceIdentity | None = _IDENTITY) -> RoleMCPAssignment:
    return RoleMCPAssignment(
        role_id="main",
        role_version="1",
        available=(RoleMCPServiceBinding(identity=_IDENTITY, tools=("search",)),),
        default_service=default,
    )


class TestToolScopeValidation:
    def test_accepts_declared_subset(self) -> None:
        validate_assignment_tools_against_configs(_assignment(), {_IDENTITY.stable_key(): ("search", "read")})

    def test_rejects_service_without_declared_scope(self) -> None:
        """R21：查不到声明范围不得当作「无限制」。"""
        with pytest.raises(AgentValidationError) as exc:
            validate_assignment_tools_against_configs(_assignment(), {})

        assert exc.value.code == "mcp_service_not_configured"

    def test_rejects_tool_outside_declared_scope(self) -> None:
        with pytest.raises(AgentValidationError) as exc:
            validate_assignment_tools_against_configs(_assignment(), {_IDENTITY.stable_key(): ("read",)})

        assert exc.value.code == "mcp_tool_outside_declared_scope"


class TestStoreRoundTrip:
    async def test_save_then_load_round_trip(self) -> None:
        memory = _Memory()
        store = _store(memory)

        await store.save(_assignment())

        assert await store.load("main", "1") == _assignment()
        assert AppStateMCPRoleAssignmentStore.key_for("main", "1") in memory.values

    async def test_missing_value_returns_none(self) -> None:
        assert await _store(_Memory()).load("main", "1") is None

    async def test_repeated_save_is_idempotent(self) -> None:
        memory = _Memory()
        store = _store(memory)

        await store.save(_assignment())
        first = memory.values[AppStateMCPRoleAssignmentStore.key_for("main", "1")]
        await store.save(_assignment())

        assert memory.values[AppStateMCPRoleAssignmentStore.key_for("main", "1")] == first
        assert len(memory.values) == 1

    async def test_list_sorted_and_scoped_to_prefix(self) -> None:
        memory = _Memory()
        store = _store(memory)
        await store.save(_assignment())
        await store.save(
            RoleMCPAssignment(
                role_id="sub",
                role_version="2",
                available=(RoleMCPServiceBinding(identity=_OTHER, tools=()),),
            )
        )
        memory.values["unrelated.key"] = "{}"

        assignments = await store.list_assignments()

        assert [(item.role_id, item.role_version) for item in assignments] == [("main", "1"), ("sub", "2")]

    @pytest.mark.parametrize(
        "payload",
        [
            "not-json",
            json.dumps({"schema_version": 1}),
            json.dumps(
                {
                    "schema_version": 1,
                    "role_id": "main",
                    "role_version": "1",
                    "available": [
                        {"service": {"service_id": "svc-a", "config_version": 1}, "tools": []},
                        {"service": {"service_id": "svc-a", "config_version": 1}, "tools": []},
                    ],
                    "default_service": None,
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "role_id": "main",
                    "role_version": "1",
                    "available": [
                        {"service": {"service_id": "svc-a", "config_version": 1}, "tools": []},
                    ],
                    "default_service": {"service_id": "svc-z", "config_version": 1},
                }
            ),
            json.dumps(
                {
                    "schema_version": 999,
                    "role_id": "main",
                    "role_version": "1",
                    "available": [],
                    "default_service": None,
                }
            ),
            json.dumps(
                {
                    "schema_version": 1,
                    "role_id": "main",
                    "role_version": "1",
                    "available": [
                        {"service": {"service_id": "svc-a", "config_version": 1}, "tools": ["a", "a"]},
                    ],
                    "default_service": None,
                }
            ),
        ],
    )
    async def test_malformed_payloads_fail_closed(self, payload: str) -> None:
        """任何不可解码/不自洽的载荷都必须显式失败，不得伪装「未配置」。"""
        memory = _Memory()
        store = _store(memory)
        memory.values[AppStateMCPRoleAssignmentStore.key_for("main", "1")] = payload

        with pytest.raises(AgentServiceError) as exc:
            await store.load("main", "1")

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE


class TestStoreDelete:
    async def test_delete_unwired_fails_closed(self) -> None:
        with pytest.raises(AgentServiceError) as exc:
            await _store(_Memory()).delete("main", "1")

        assert exc.value.code is AgentErrorCode.CONFIG_UNAVAILABLE

    async def test_delete_wired_removes_key(self) -> None:
        memory = _Memory()
        store = _store(memory, delete=memory.delete)
        await store.save(_assignment())

        assert await store.delete("main", "1") is True
        assert await store.load("main", "1") is None
