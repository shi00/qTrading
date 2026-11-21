"""``MCPFrozenUsagePort`` 的持久化适配（I59）。

只做转调：使用者清单必须来自持久化研究记录，不得用管理器内存计数代答。
DAO 异常原样抛出，由共享连接管理器按 fail-closed 拒绝断开/更新。
"""

from __future__ import annotations

from typing import Any

from core.agent_types import MCPServiceIdentity
from services.agent_service.mcp import MCPFrozenUsagePort

__all__ = ["DaoMCPFrozenUsage"]


class DaoMCPFrozenUsage(MCPFrozenUsagePort):
    """按 MCP 配置版查询仍未终结的研究（生产为 ``AgentDao``）。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        users: tuple[str, ...] = await self._dao.list_mcp_frozen_users(identity)
        return users
