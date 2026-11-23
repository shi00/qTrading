"""``MCPFrozenUsagePort`` / ``MCPDispatchGatePort`` 的持久化适配。

- ``DaoMCPFrozenUsage``（I59）：使用者清单必须来自持久化研究记录，不得用管理器
  内存计数代答；DAO 异常原样抛出，由共享连接管理器按 fail-closed 拒绝断开/更新。
- ``DaoMCPDispatchGate``（P1-10）：停用/恢复与工具 claim 共享同一 ``app_state``
  行锁，认领侧由 DAO 在 claim 事务内锁定同一行；停用先提交即拒绝新 claim。
"""

from __future__ import annotations

from typing import Any

from core.agent_types import MCPServiceIdentity
from services.agent_service.mcp import MCPDispatchGatePort, MCPFrozenUsagePort

__all__ = ["DaoMCPDispatchGate", "DaoMCPFrozenUsage"]


class DaoMCPFrozenUsage(MCPFrozenUsagePort):
    """按 MCP 配置版查询仍未终结的研究（生产为 ``AgentDao``）。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        users: tuple[str, ...] = await self._dao.list_mcp_frozen_users(identity)
        return users


class DaoMCPDispatchGate(MCPDispatchGatePort):
    """MCP 派发停用门（生产为 ``AgentDao`` 的 ``app_state`` 行锁事务）。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def stop(self, identity: MCPServiceIdentity) -> None:
        await self._dao.stop_mcp_dispatch_gate(identity)

    async def resume(self, identity: MCPServiceIdentity) -> None:
        await self._dao.resume_mcp_dispatch_gate(identity)
