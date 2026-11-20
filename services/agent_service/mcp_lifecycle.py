"""MCP 连接生命周期服务：配置保存 / 显式测试 / 显式启连 / 断开 / 启连授权冻结。

职责边界：

- **不新建第二连接层**：连接、在途归属、UNKNOWN 拒停、审核重核全部由
  :class:`~services.agent_service.mcp.SharedMCPConnectionManager` 承担；
- 本模块只补三件管理器不知道的事：配置存哪（``app_state``）、用户核准了哪些工具
  （授权记录）、当前哪些连接是本进程显式建立的（供工具工厂与关闭链使用）；
- **绝不自动启连**：除 ``connect`` / ``connection_test`` 外任何方法都不打开进程/网络；
- ``CancelledError`` 原样传播（R2）；失败不伪装成功（R21）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    MCPServiceIdentity,
    require_identity,
)
from services.agent_service.mcp import (
    MCPConfigSaveReceipt,
    MCPConnectionTestReceipt,
    MCPServerConfig,
    SharedMCPConnectionManager,
    catalog_digest,
)
from services.agent_service.mcp_config_store import AppStateMCPConfigStore
from services.agent_service.mcp_grant import AppStateMCPGrantStore, MCPConnectGrant, MCPToolSets

logger = logging.getLogger(__name__)

__all__ = ["MCPConnectReceipt", "MCPServiceLifecycle"]


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


@dataclass(frozen=True, slots=True)
class MCPConnectReceipt:
    """显式启连回执：身份 + 冻结目录摘要 + 本次核准启用的工具。"""

    identity: MCPServiceIdentity
    catalog_digest: str
    tools: tuple[str, ...]


class MCPServiceLifecycle:
    """组合根持有的 MCP 服务生命周期门面。"""

    def __init__(
        self,
        *,
        manager: SharedMCPConnectionManager,
        configs: AppStateMCPConfigStore,
        grants: AppStateMCPGrantStore,
    ) -> None:
        self._manager = manager
        self._configs = configs
        self._grants = grants
        #: 本进程经 ``connect`` 成功建立的连接（身份与授权缓存）：工具工厂只认这里，
        #: 保证「未显式启连 = 零工具」，且工厂保持同步。
        self._connected: dict[str, MCPServiceIdentity] = {}
        self._grant_cache: dict[str, MCPConnectGrant] = {}

    # -- 配置 ---------------------------------------------------------------

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        """保存配置（不启连、不发现）；准入与持久化由管理器负责。"""

        return await self._manager.save_config(config)

    async def list_services(self) -> tuple[MCPServerConfig, ...]:
        """列出已保存配置；扫描端口不可用时明确失败（不返回空冒充无配置）。"""

        return await self._configs.list_configs()

    # -- 连接测试与启连 -----------------------------------------------------

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        """用户显式测试：仅初始化/发现，0 研究调用，结束必关闭。"""

        return await self._manager.connection_test(identity)

    async def connect(
        self,
        identity: MCPServiceIdentity,
        *,
        approved_tools: Iterable[str],
    ) -> MCPConnectReceipt:
        """用户显式启连并冻结本次核准的工具集。

        顺序：建立共享连接 → 取冻结目录 → 核准集必须落在只读审核范围内 →
        既有授权记录的目录摘要必须一致（schema/目录变化要求重新核准）→ 写授权。
        任一步失败即断开本次连接并传播原因，不留下「已连接但无授权」的悬空状态。
        """

        label = identity.stable_key()
        approved = tuple(sorted({require_identity(name, "$.mcp.approved_tools") for name in approved_tools}))
        await self._manager.connect(identity)
        try:
            catalog = self._manager.frozen_catalog(identity)
            digest = catalog_digest(catalog)
            reviewed = frozenset(self._manager.audit_tool_scope(identity))
            outside = tuple(name for name in approved if name not in reviewed)
            if outside:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    f"approved tools are outside the reviewed read-only scope: {','.join(outside)}",
                    label,
                )
            previous = await self._grants.load(identity)
            if previous is not None and previous.catalog_digest != digest:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    "grant_catalog_drift",
                    label,
                )
            grant = MCPConnectGrant(
                identity=identity,
                tools=approved,
                catalog_digest=digest,
                approved_at=_utc_now(),
            )
            await self._grants.save(grant)
        except asyncio.CancelledError:
            await self._disconnect_quietly(identity, label)
            raise
        except BaseException:
            await self._disconnect_quietly(identity, label)
            raise
        self._connected[label] = identity
        self._grant_cache[label] = grant
        return MCPConnectReceipt(identity=identity, catalog_digest=digest, tools=approved)

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        """显式断开：在途/冻结使用者由管理器拒停；失败保留状态并传播。"""

        label = identity.stable_key()
        await self._manager.disconnect(identity)
        self._connected.pop(label, None)
        self._grant_cache.pop(label, None)

    # -- 工厂与关闭链输入 ---------------------------------------------------

    @property
    def manager(self) -> SharedMCPConnectionManager:
        """共享连接管理器（工具工厂读取冻结目录/代次/预算的唯一来源）。"""

        return self._manager

    def is_connected(self, identity: MCPServiceIdentity) -> bool:
        """本进程是否经显式启连建立了该连接（不含管理器的外部登记）。"""

        return identity.stable_key() in self._connected and self._manager.is_connected(identity)

    def active_identity(self) -> MCPServiceIdentity | None:
        """当前唯一已连接服务；0 个或多个均返回 ``None``（不猜、不随机挑选）。"""

        if len(self._connected) != 1:
            return None
        return next(iter(self._connected.values()))

    def connected_identities(self) -> tuple[MCPServiceIdentity, ...]:
        """本进程经显式启连建立的连接（关闭链逐个断开用）。"""

        return tuple(sorted(self._connected.values(), key=lambda item: item.stable_key()))

    def tool_sets(self, identity: MCPServiceIdentity) -> MCPToolSets | None:
        """本次运行可用的工具三集合；未连接/无授权/目录漂移一律 ``None``（零工具）。"""

        label = identity.stable_key()
        if label not in self._connected:
            return None
        grant = self._grant_cache.get(label)
        if grant is None:
            return None
        try:
            digest = catalog_digest(self._manager.frozen_catalog(identity))
        except AgentServiceError:
            return None
        if digest != grant.catalog_digest:
            return None
        return MCPToolSets.from_grant(grant)

    # -- 内部 ---------------------------------------------------------------

    async def _disconnect_quietly(self, identity: MCPServiceIdentity, label: str) -> None:
        """失败路径的收尾断开：不让收尾异常覆盖主原因，但也不吞取消。"""

        try:
            await self._manager.disconnect(identity)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("[MCPLifecycle] cleanup disconnect failed: %s (%s)", type(e).__name__, label)
            return
        self._connected.pop(label, None)
        self._grant_cache.pop(label, None)
