"""组合根 MCP 接线：生命周期构造（零连接）与关闭前置逐个断开。"""

from __future__ import annotations

from typing import Any, cast

import pytest

from app.agent_mcp import build_mcp_lifecycle
from app.agent_wiring import AgentWiring
from core.agent_types import MCPServiceIdentity
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-wiring", config_version=1)


class _Lifecycle:
    def __init__(self) -> None:
        self.disconnected: list[MCPServiceIdentity] = []
        self.fail = False

    def connected_identities(self) -> tuple[MCPServiceIdentity, ...]:
        return (_IDENTITY,)

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        if self.fail:
            raise RuntimeError("disconnect failed")
        self.disconnected.append(identity)


class _Engine:
    url = "postgresql+asyncpg://user@localhost/qtrading"


class _Dao:
    async def list_mcp_frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return ()


def _wiring(lifecycle: _Lifecycle | None) -> AgentWiring:
    return AgentWiring(
        service=None,
        bridge=cast(Any, None),
        mcp_lifecycle=cast(MCPServiceLifecycle, lifecycle),
    )


class TestBuildMCPLifecycle:
    def test_construction_opens_no_connections(self) -> None:
        lifecycle = build_mcp_lifecycle(engine=_Engine(), dao=_Dao())
        assert lifecycle.active_identity() is None
        assert lifecycle.connected_identities() == ()
        assert lifecycle.manager.is_connected(_IDENTITY) is False


class TestPrepareCloseDisconnects:
    async def test_disconnects_known_identities(self) -> None:
        lifecycle = _Lifecycle()
        wiring = _wiring(lifecycle)

        assert await wiring.prepare_close() is None

        assert lifecycle.disconnected == [_IDENTITY]

    async def test_disconnect_failure_is_logged_not_raised(self) -> None:
        lifecycle = _Lifecycle()
        lifecycle.fail = True
        wiring = _wiring(lifecycle)

        assert await wiring.prepare_close() is None
        assert lifecycle.disconnected == []

    async def test_without_lifecycle_is_noop(self) -> None:
        wiring = _wiring(None)
        assert await wiring.prepare_close() is None
