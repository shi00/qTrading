"""工具 runtime 工厂：未连接/无授权/漂移一律零工具，绝不自动启连。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.agent_types import MCPServiceIdentity
from services.agent_service.mcp import MCPToolDescriptor
from services.agent_service.mcp_grant import MCPToolSets
from services.agent_service.tool_runtime import ToolRunScope, build_tool_runtime_factory

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-factory", config_version=1)
_SCOPE = ToolRunScope(
    user_scope="user-1",
    session_id="session-1",
    execution_id="execution-1",
    round_id="round-1",
    node_id="n1",
)


def _schema() -> str:
    return json.dumps(
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        }
    )


class _Manager:
    def frozen_catalog(self, identity: MCPServiceIdentity) -> tuple[MCPToolDescriptor, ...]:
        return (MCPToolDescriptor("search", _schema()),)

    def connection_generation(self, identity: MCPServiceIdentity) -> int:
        return 1

    def audit_tool_scope(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ("search",)


class _Lifecycle:
    def __init__(
        self,
        *,
        identity: MCPServiceIdentity | None = _IDENTITY,
        connected: bool = True,
        sets: MCPToolSets | None = None,
    ) -> None:
        self._identity = identity
        self._connected = connected
        self._sets = sets if sets is not None else MCPToolSets(("search",), ("search",), ("search",))
        self.manager = _Manager()

    def active_identity(self) -> MCPServiceIdentity | None:
        return self._identity

    def is_connected(self, identity: MCPServiceIdentity) -> bool:
        return self._connected

    def tool_sets(self, identity: MCPServiceIdentity) -> MCPToolSets | None:
        return self._sets if self._connected else None


class _Dao:
    async def save_tool_decision(self, request: Any) -> Any:
        raise AssertionError("factory must not persist")

    async def save_tool_result(self, request: Any) -> None:
        raise AssertionError("factory must not persist")

    async def read_operation_grant_scope(self, *, operation_id: str) -> Any:
        return None

    async def claim_tool_dispatch(self, request: Any) -> Any:
        raise AssertionError("factory must not claim")


class TestBuildToolRuntimeFactory:
    def test_no_connected_service_returns_none(self) -> None:
        factory = build_tool_runtime_factory(
            dao=_Dao(),  # type: ignore[arg-type]
            lifecycle=_Lifecycle(identity=None),  # type: ignore[arg-type]
        )
        assert factory(_SCOPE) is None

    def test_disconnected_returns_none(self) -> None:
        factory = build_tool_runtime_factory(
            dao=_Dao(),  # type: ignore[arg-type]
            lifecycle=_Lifecycle(connected=False),  # type: ignore[arg-type]
        )
        assert factory(_SCOPE) is None

    def test_missing_grant_returns_none(self) -> None:
        lifecycle = _Lifecycle()
        lifecycle._sets = None  # type: ignore[assignment]
        factory = build_tool_runtime_factory(
            dao=_Dao(),  # type: ignore[arg-type]
            lifecycle=lifecycle,  # type: ignore[arg-type]
        )
        assert factory(_SCOPE) is None

    def test_connected_with_grant_builds_tools(self) -> None:
        factory = build_tool_runtime_factory(
            dao=_Dao(),  # type: ignore[arg-type]
            lifecycle=_Lifecycle(),  # type: ignore[arg-type]
        )

        runtime = factory(_SCOPE)

        assert runtime is not None
        assert [tool.name for tool in runtime.tools] == ["search"]
        assert runtime.identity == _IDENTITY
        assert runtime.connection_generation == 1

    def test_empty_grant_builds_zero_tools(self) -> None:
        factory = build_tool_runtime_factory(
            dao=_Dao(),  # type: ignore[arg-type]
            lifecycle=_Lifecycle(sets=MCPToolSets((), (), ())),  # type: ignore[arg-type]
        )

        runtime = factory(_SCOPE)

        assert runtime is not None
        assert runtime.tools == ()
