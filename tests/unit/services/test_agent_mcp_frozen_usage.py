"""``DaoMCPFrozenUsage``：只做持久化转调，异常如实传播。"""

from __future__ import annotations

import pytest

from core.agent_types import MCPServiceIdentity
from services.agent_service.mcp_usage import DaoMCPFrozenUsage

pytestmark = pytest.mark.unit

_IDENTITY = MCPServiceIdentity(service_id="svc-usage", config_version=4)


class _Dao:
    def __init__(self, users: tuple[str, ...] = (), error: Exception | None = None) -> None:
        self.users = users
        self.error = error
        self.calls: list[MCPServiceIdentity] = []

    async def list_mcp_frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        self.calls.append(identity)
        if self.error is not None:
            raise self.error
        return self.users


class TestDaoMCPFrozenUsage:
    async def test_passes_identity_and_returns_users(self) -> None:
        dao = _Dao(("execution-1", "execution-2"))
        users = await DaoMCPFrozenUsage(dao).frozen_users(_IDENTITY)

        assert users == ("execution-1", "execution-2")
        assert dao.calls == [_IDENTITY]

    async def test_dao_failure_propagates(self) -> None:
        dao = _Dao(error=RuntimeError("db down"))
        with pytest.raises(RuntimeError):
            await DaoMCPFrozenUsage(dao).frozen_users(_IDENTITY)
