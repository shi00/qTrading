"""MCP 服务生命周期生产组装（组合根，§10.4 / E-02）。

只做对象图构造，不做任何连接动作：

- 配置与启连授权存于既有 ``app_state``（键前缀 ``agent.mcp.``），不新建表；
- 连接由 :class:`~services.agent_service.mcp.SharedMCPConnectionManager` 管理，
  传输实现为真实 :class:`~services.agent_service.mcp_fastmcp.FastMCPClientFactory`；
- 实现指纹探针只对 stdio 可观测，remote 明确不可核定（返回 ``None``，不伪造已验证）；
- 冻结使用者来自持久化工具决定载荷（``DaoMCPFrozenUsage``），读取失败即拒绝断开/更新。

构造成功**不代表已连接**：本进程未显式启连时没有任何进程/网络出站。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from data.persistence.app_state_service import get_app_state, list_app_state, set_app_state
from services.agent_service.mcp import SharedMCPConnectionManager
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_fastmcp import FastMCPClientFactory
from services.agent_service.mcp_grant import AppStateMCPGrantStore
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle
from services.agent_service.mcp_probe import StdioImplementationProbe
from services.agent_service.mcp_usage import DaoMCPDispatchGate, DaoMCPFrozenUsage

__all__ = ["build_mcp_lifecycle"]


def build_mcp_lifecycle(*, engine: Any, dao: Any) -> MCPServiceLifecycle:
    """按共享引擎/账本构造 MCP 生命周期门面（构造期零连接）。"""

    configs = AppStateMCPConfigStore(
        read=partial(get_app_state, engine),
        write=partial(set_app_state, engine, strict=True),
        scan=partial(list_app_state, engine),
    )
    grants = AppStateMCPGrantStore(
        read=partial(get_app_state, engine),
        write=partial(set_app_state, engine, strict=True),
    )
    manager = SharedMCPConnectionManager(
        store=configs,
        factory=FastMCPClientFactory(),
        probe=StdioImplementationProbe(),
        usage=DaoMCPFrozenUsage(dao),
        dispatch_gate=DaoMCPDispatchGate(dao),
    )
    return MCPServiceLifecycle(manager=manager, configs=configs, grants=grants)
