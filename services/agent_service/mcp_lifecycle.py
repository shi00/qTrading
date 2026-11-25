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
    AgentValidationError,
    MCPServiceIdentity,
    RoleMCPAssignment,
    require_identity,
    validate_role_mcp_assignment,
)
from services.agent_service.mcp import (
    MCPConfigSaveReceipt,
    MCPConnectionTestReceipt,
    MCPServerConfig,
    SharedMCPConnectionManager,
    catalog_digest,
    config_snapshot_digest,
)
from services.agent_service.mcp_config_store import AppStateMCPConfigStore, MCPConfigListEntry
from services.agent_service.mcp_grant import AppStateMCPGrantStore, MCPConnectGrant, MCPToolSets
from services.agent_service.mcp_role import (
    AppStateMCPRoleAssignmentStore,
    validate_assignment_tools_against_configs,
)

logger = logging.getLogger(__name__)

__all__ = [
    "MCPConnectReceipt",
    "MCPServiceDeleteBlocked",
    "MCPServiceDeletionReceipt",
    "MCPServiceLifecycle",
]


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _credential_references(config: MCPServerConfig) -> tuple[str, ...]:
    """配置中引用的外部凭据（仅安全引用名，不含秘密值；供删除后人工撤销，QAC13）。"""

    return tuple(sorted({value for _name, value in config.transport.headers if value.startswith(("env:", "keyring:"))}))


@dataclass(frozen=True, slots=True)
class MCPConnectReceipt:
    """显式启连回执：身份 + 冻结目录摘要 + 本次核准启用的工具。"""

    identity: MCPServiceIdentity
    catalog_digest: str
    tools: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MCPServiceDeletionReceipt:
    """服务删除回执：删除事实 + 需人工撤销的凭据引用（不含秘密值）。"""

    identity: MCPServiceIdentity
    deleted_config: bool
    deleted_grant: bool
    credential_references: tuple[str, ...]


class MCPServiceDeleteBlocked(AgentServiceError):
    """服务仍被角色配置或研究会话引用（删除被拒，携带可定位标识）。"""

    def __init__(
        self,
        identity: MCPServiceIdentity,
        *,
        role_refs: tuple[str, ...] = (),
        research_ids: tuple[str, ...] = (),
    ) -> None:
        parts: list[str] = []
        if role_refs:
            parts.append(f"roles={','.join(role_refs)}")
        if research_ids:
            parts.append(f"researches={','.join(research_ids)}")
        super().__init__(
            AgentErrorCode.POLICY_DENIED,
            f"mcp service is still referenced: {';'.join(parts)}",
            identity.stable_key(),
        )
        self.role_refs = role_refs
        self.research_ids = research_ids


class MCPServiceLifecycle:
    """组合根持有的 MCP 服务生命周期门面。"""

    def __init__(
        self,
        *,
        manager: SharedMCPConnectionManager,
        configs: AppStateMCPConfigStore,
        grants: AppStateMCPGrantStore,
        roles: AppStateMCPRoleAssignmentStore | None = None,
    ) -> None:
        self._manager = manager
        self._configs = configs
        self._grants = grants
        #: 角色分配存储（删除服务时据此阻删）；未接线时删除显式拒绝（fail-closed）。
        self._roles = roles
        #: 本进程经 ``connect`` 成功建立的连接（身份与授权缓存）：工具工厂只认这里，
        #: 保证「未显式启连 = 零工具」，且工厂保持同步。
        self._connected: dict[str, MCPServiceIdentity] = {}
        self._grant_cache: dict[str, MCPConnectGrant] = {}

    # -- 配置 ---------------------------------------------------------------

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        """保存配置（不启连、不发现）；准入与持久化由管理器负责。"""

        return await self._manager.save_config(config)

    async def list_services(self) -> tuple[MCPConfigListEntry, ...]:
        """逐行列出已保存配置（单条失效不使整批失败）；扫描端口不可用时明确失败。"""

        return await self._configs.list_configs()

    async def delete_service(
        self,
        identity: MCPServiceIdentity,
        *,
        confirmation: bool = False,
    ) -> MCPServiceDeletionReceipt:
        """删除服务配置与启连授权（被角色配置/研究会话引用即阻删）。

        顺序：确认（调用方显式确认）→ 读取配置（不存在即拒绝，不静默幂等）→ 角色
        分配引用检查（fail-closed：存储未接线也拒绝）→ 持久化研究引用检查 → 共享
        连接/测试与关闭残留检查（由管理器执行）→ 删除配置 → 删除授权记录。

        删除前不关闭任何活动连接（须由用户显式断开）；返回回执含配置里引用的外部
        凭据名（``env:`` / ``keyring:``），供删除后人工撤销（QAC13），不回显秘密值。
        """

        label = identity.stable_key()
        if not confirmation:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "mcp service deletion requires explicit confirmation",
                label,
            )
        config = await self._configs.load(identity)
        if config is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp service config not found", label)
        role_refs = await self._role_references(identity)
        if role_refs:
            raise MCPServiceDeleteBlocked(identity, role_refs=role_refs)
        research_ids = await self._manager.frozen_users(identity)
        if research_ids:
            raise MCPServiceDeleteBlocked(identity, research_ids=research_ids)
        deleted_config = await self._manager.delete_config(identity)
        deleted_grant = await self._grants.delete(identity)
        self._connected.pop(label, None)
        self._grant_cache.pop(label, None)
        return MCPServiceDeletionReceipt(
            identity=identity,
            deleted_config=deleted_config,
            deleted_grant=deleted_grant,
            credential_references=_credential_references(config),
        )

    # -- 角色分配（F14 残余 / J25） -----------------------------------------

    async def save_role_assignment(self, assignment: RoleMCPAssignment) -> RoleMCPAssignment:
        """保存角色可用/默认服务与工具白名单，并做跨配置一致性校验。

        校验：核心合同（默认⊆可用、服务/工具名唯一）→ 每个服务必须已配置 → 每个
        白名单工具必须存在于该服务**声明**的只读工具范围（``audit.tool_scope``，
        未声明即空集）。任一不满足即拒绝且不落库（不静默裁剪）。
        """

        try:
            validate_role_mcp_assignment(assignment)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", "$.role.mcp"
            ) from exc
        if self._roles is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE, "mcp role assignments are not wired", "$.role.mcp"
            )
        declared: dict[str, tuple[str, ...]] = {}
        for binding in assignment.available:
            key = binding.identity.stable_key()
            config = await self._configs.load(binding.identity)
            if config is None:
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"mcp service is not configured: {key}", key)
            declared[key] = config.audit.tool_scope if config.audit is not None else ()
        try:
            validate_assignment_tools_against_configs(assignment, declared)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"{exc.code}: {exc.field_path}", "$.role.mcp"
            ) from exc
        await self._roles.save(assignment)
        return assignment

    async def list_role_assignments(self) -> tuple[RoleMCPAssignment, ...]:
        """列出全部角色 MCP 分配；未接线即显式拒绝（不返回空冒充无分配）。"""

        if self._roles is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE, "mcp role assignments are not wired", "$.role.mcp"
            )
        return await self._roles.list_assignments()

    async def _role_references(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        """引用该服务身份的角色版本标识（未接线存储即拒绝删除）。"""

        if self._roles is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp role assignments are unverifiable before deletion",
                identity.stable_key(),
            )
        assignments = await self._roles.list_assignments()
        refs = [
            f"{assignment.role_id}@{assignment.role_version}"
            for assignment in assignments
            if assignment.default_service == identity
            or any(binding.identity == identity for binding in assignment.available)
        ]
        return tuple(sorted(refs))

    # -- 连接测试与启连 -----------------------------------------------------

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        """用户显式测试：仅初始化/发现，0 研究调用，结束必关闭。"""

        return await self._manager.connection_test(identity)

    async def retry_test_residue_close(self, identity: MCPServiceIdentity) -> int:
        """重试关闭测试残留会话：成功才清空，仍失败即显式拒绝（不伪装已收尾）。

        返回本次确认关闭的数量（无残留即 0），供界面如实报告。
        """

        return await self._manager.retry_test_residue_close(identity)

    async def connect(
        self,
        identity: MCPServiceIdentity,
        *,
        approved_tools: Iterable[str],
        test_receipt: MCPConnectionTestReceipt,
    ) -> MCPConnectReceipt:
        """用户显式启连并冻结本次核准的工具集。

        顺序：核对本轮测试回执（身份/代次/关闭确认/配置摘要）→ 建立共享连接 →
        取冻结目录并与回执目录摘要比对 → 核准集必须落在**当前目录**与只读审核
        范围内 → 既有授权记录的目录摘要必须一致（schema/目录变化要求重新核准）
        → 写授权。任一步失败即断开本次连接并传播原因，不留下「已连接但无授权」的
        悬空状态。

        回执绑定是 fail-closed 的关键：测试与启连之间配置或目录若发生漂移，旧回执
        不再代表实际连接发现的内容，必须重新测试，不得用旧勾选核准新连接。
        """

        label = identity.stable_key()
        if test_receipt.identity != identity:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "connect requires the connection test receipt of this service",
                label,
            )
        if not test_receipt.closed:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "connection test closed unconfirmed; retry close before connecting",
                label,
            )
        if self._manager.test_generation(identity) != test_receipt.generation:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "connection test receipt is stale; test again",
                label,
            )
        stored = await self._configs.load(identity)
        if stored is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp service config not found", label)
        if config_snapshot_digest(stored) != test_receipt.config_digest:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "mcp config changed since the connection test; test again",
                label,
            )
        approved = tuple(sorted({require_identity(name, "$.mcp.approved_tools") for name in approved_tools}))
        await self._manager.connect(identity)
        try:
            catalog = self._manager.frozen_catalog(identity)
            digest = catalog_digest(catalog)
            if digest != test_receipt.catalog_digest:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    "catalog_changed_since_test",
                    label,
                )
            catalog_names = {tool.name for tool in catalog}
            missing = tuple(name for name in approved if name not in catalog_names)
            if missing:
                raise AgentServiceError(
                    AgentErrorCode.POLICY_DENIED,
                    f"approved tools are not in the live tool catalog: {','.join(missing)}",
                    label,
                )
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
