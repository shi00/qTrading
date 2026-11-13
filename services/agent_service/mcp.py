"""共享 MCP 连接管理（E-02 / 详细设计 §10.4、HANDOFF §9）。

本模块只承载**静态可验证**的准入合同：保存不启连、连接测试只发现（0 研究工具
调用）、预算与只读审核证据不足拒启、显式断开且在途时拒停、断线不自动重连。

真实 FastMCP 传输内部插入点（逐 send 门、FD 级 stderr 收尾、四诊断通道、
各传输前置预算数值与早拒绝）仍为 needs-spike，由 :class:`MCPClientFactoryPort`
等端口隔离；本模块不声称已接入或已验证真实 SDK 行为。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Protocol
from urllib.parse import urlsplit

from core.agent_types import (
    MCP_DEFAULT_DEADLINE_S,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
    encode_mcp_read_only_audit,
    evaluate_read_only_audit,
    require_identity,
    sha256_hex,
    validate_mcp_deadline,
)
from services.agent_service.mcp_budget import MCPBudgetGuard

# ---------------------------------------------------------------------------
# 传输配置（stdio 绝对可执行/不经 shell；HTTP 无内嵌凭据；SSE 需显式核准）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MCPTransportConfig:
    """传输参数快照（不含凭据；凭据经安全存储/环境按需注入，不入历史）。"""

    kind: MCPTransportKind
    #: stdio：绝对可执行文件路径；不经 shell 解析。
    command: str | None = None
    args: tuple[str, ...] = ()
    #: stdio：已核安全引用后的最小环境（键值对，保序）；不含应用环境副本。
    env: tuple[tuple[str, str], ...] = ()
    #: stdio：绝对工作目录。
    cwd: str | None = None
    #: streamable_http / sse：目标 URL（不得内嵌用户名/密码）。
    url: str | None = None
    #: SSE 仅作显式兼容：须用户显式核准该传输。
    sse_compat_approved: bool = False


def _fail_transport(message: str, path: str = "$.mcp.transport") -> NoReturn:
    raise AgentValidationError("invalid_transport", f"{path}: {message}")


def validate_transport_config(config: MCPTransportConfig, path: str = "$.mcp.transport") -> None:
    """传输配置校验：只接受合同内形状，不做 shell 展开或隐式协议互换。"""

    if not isinstance(config.kind, MCPTransportKind):
        _fail_transport("unknown transport kind", path)

    for pair in config.env:
        if not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(part, str) and part for part in pair):
            _fail_transport("env entries must be non-empty string pairs", f"{path}.env")

    if config.kind is MCPTransportKind.STDIO:
        if not isinstance(config.command, str) or not config.command:
            _fail_transport("stdio requires an executable command", f"{path}.command")
        if not Path(config.command).is_absolute():
            _fail_transport("stdio command must be an absolute path", f"{path}.command")
        if config.url is not None:
            _fail_transport("stdio must not carry a url", f"{path}.url")
        if config.cwd is not None and not Path(config.cwd).is_absolute():
            _fail_transport("stdio cwd must be an absolute path", f"{path}.cwd")
        for arg in config.args:
            if not isinstance(arg, str):
                _fail_transport("stdio args must be strings", f"{path}.args")
        return

    # streamable_http / sse
    if config.command is not None or config.args or config.env or config.cwd is not None:
        _fail_transport("remote transports must not carry local process fields", path)
    if not isinstance(config.url, str) or not config.url:
        _fail_transport("remote transport requires a url", f"{path}.url")
    parts = urlsplit(config.url)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        _fail_transport("url must be an absolute http(s) url", f"{path}.url")
    if parts.username is not None or parts.password is not None:
        _fail_transport("url must not embed credentials", f"{path}.url")
    if config.kind is MCPTransportKind.SSE and not config.sse_compat_approved:
        raise AgentValidationError("sse_not_approved", f"{path}.sse_compat_approved")


# ---------------------------------------------------------------------------
# 配置与端口
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MCPServerConfig:
    """服务配置快照：稳定身份 + 显示名 + 传输 + 冻结预算 + 审核绑定。"""

    identity: MCPServiceIdentity
    display_name: str
    transport: MCPTransportConfig
    budget: MCPBudgetProfile
    connect_deadline_s: int = MCP_DEFAULT_DEADLINE_S
    tool_deadline_s: int = MCP_DEFAULT_DEADLINE_S
    audit: MCPReadOnlyAudit | None = None


@dataclass(frozen=True, slots=True)
class MCPConfigSaveReceipt:
    """保存回执：保存不等同启连（``connections_opened`` 恒为 0）。"""

    identity: MCPServiceIdentity
    connections_opened: int


@dataclass(frozen=True, slots=True)
class MCPToolDescriptor:
    """发现的工具：名称 + 完整 jsonschema 规范文本（发现不授予调用权）。"""

    name: str
    schema_json: str


@dataclass(frozen=True, slots=True)
class MCPConnectionTestReceipt:
    """连接测试回执：只含初始化/发现结果，``research_calls`` 恒为 0。"""

    identity: MCPServiceIdentity
    tool_names: tuple[str, ...]
    catalog_digest: str
    research_calls: int
    closed: bool


class MCPConfigStorePort(Protocol):
    """配置持久化端口（生产复用原配置管理域，不新建 Agent 表）。"""

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None: ...

    async def save(self, config: MCPServerConfig) -> None: ...


class MCPClientSessionPort(Protocol):
    """目标版客户端会话端口（真实 FastMCP 接线为 needs-spike）。"""

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class MCPClientFactoryPort(Protocol):
    """会话工厂端口：仅在用户显式启连/显式测试时被调用。"""

    async def open(self, config: MCPServerConfig) -> MCPClientSessionPort: ...


class MCPImplementationProbe(Protocol):
    """实际实现探测端口：返回本机实现指纹；无法核定返回 ``None``（不准入）。"""

    async def probe(self, config: MCPServerConfig) -> str | None: ...


def _identity_text(identity: MCPServiceIdentity) -> str:
    return identity.stable_key()


def catalog_digest(tools: tuple[MCPToolDescriptor, ...]) -> str:
    """目录摘要：按工具名排序后的 (name, schema) 规范 JSON 摘要。"""

    canonical = json.dumps(
        [[tool.name, tool.schema_json] for tool in sorted(tools, key=lambda item: item.name)],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# 共享连接管理
# ---------------------------------------------------------------------------


class SharedMCPConnectionManager:
    """同一服务身份/配置版本共享一个连接；观察者退出不关持有者连接。"""

    def __init__(
        self,
        *,
        store: MCPConfigStorePort,
        factory: MCPClientFactoryPort,
        probe: MCPImplementationProbe,
    ) -> None:
        self._store = store
        self._factory = factory
        self._probe = probe
        self._sessions: dict[str, MCPClientSessionPort] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._guards: dict[str, MCPBudgetGuard] = {}
        self._in_flight: dict[str, set[str]] = {}

    # -- 配置 ---------------------------------------------------------------

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        """保存配置：校验合同并持久化，**不打开连接、不发现工具**。"""

        identity = config.identity
        label = _identity_text(identity)
        try:
            validate_transport_config(config.transport)
            require_identity(config.display_name, "$.mcp.display_name")
            validate_mcp_deadline(config.connect_deadline_s, "$.mcp.connect_deadline_s")
            validate_mcp_deadline(config.tool_deadline_s, "$.mcp.tool_deadline_s")
            if config.audit is not None:
                encode_mcp_read_only_audit(config.audit)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"invalid mcp config: {exc.code}", label) from exc
        if config.audit is not None and config.audit.service != identity:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "read-only audit is bound to another service version", label
            )
        if not config.budget.is_admitted():
            unfrozen = ",".join(config.budget.unfrozen_fields())
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp receive budgets not frozen: {unfrozen}", label
            )
        await self._store.save(config)
        return MCPConfigSaveReceipt(identity=identity, connections_opened=0)

    # -- 显式测试 -----------------------------------------------------------

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        """用户显式连接测试：只初始化/鉴权/发现，0 研究工具调用，结束必关闭。"""

        config = await self._require_config(identity)
        session = await self._factory.open(config)
        try:
            tools = await session.list_tools(deadline_s=config.connect_deadline_s)
        finally:
            await session.aclose()
        return MCPConnectionTestReceipt(
            identity=identity,
            tool_names=tuple(sorted(tool.name for tool in tools)),
            catalog_digest=catalog_digest(tools),
            research_calls=0,
            closed=True,
        )

    # -- 显式启连与断开 -----------------------------------------------------

    async def connect(self, identity: MCPServiceIdentity) -> None:
        """用户显式启连：预算全冻结 + 只读审核绑定当前实现，才允许建立共享连接。"""

        label = _identity_text(identity)
        key = label
        if key in self._sessions:
            return
        config = await self._require_config(identity)
        if not config.budget.is_admitted():
            unfrozen = ",".join(config.budget.unfrozen_fields())
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp receive budgets not frozen: {unfrozen}", label
            )
        fingerprint = await self._probe.probe(config)
        admission = evaluate_read_only_audit(config.audit, fingerprint)
        if not admission.admitted:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                f"read-only audit refused: {admission.reason}",
                label,
            )
        self._sessions[key] = await self._factory.open(config)
        self._configs[key] = config
        self._guards[key] = MCPBudgetGuard(config.budget, identity=label)

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        """显式断开：有在途调用默认拒停（用户先暂停并核实际退出）。"""

        label = _identity_text(identity)
        if self._in_flight.get(label):
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "connection still has in-flight operations", label)
        session = self._sessions.pop(label, None)
        if session is not None:
            await session.aclose()
        self._configs.pop(label, None)
        self._guards.pop(label, None)
        self._in_flight.pop(label, None)

    def is_connected(self, identity: MCPServiceIdentity) -> bool:
        return _identity_text(identity) in self._sessions

    def session(self, identity: MCPServiceIdentity) -> MCPClientSessionPort:
        """已建立的共享会话；未启连即拒绝（不隐式建连）。"""

        label = _identity_text(identity)
        session = self._sessions.get(label)
        if session is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return session

    def tool_deadline_s(self, identity: MCPServiceIdentity) -> int:
        label = _identity_text(identity)
        config = self._configs.get(label)
        if config is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return config.tool_deadline_s

    def budget_guard(self, identity: MCPServiceIdentity) -> MCPBudgetGuard:
        label = _identity_text(identity)
        guard = self._guards.get(label)
        if guard is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return guard

    def mark_session_lost(self, identity: MCPServiceIdentity) -> None:
        """断线明示：移除会话，不自动重连/重启。"""

        label = _identity_text(identity)
        self._sessions.pop(label, None)
        self._configs.pop(label, None)
        self._guards.pop(label, None)

    # -- 在途归属 -----------------------------------------------------------

    def register_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        label = _identity_text(identity)
        require_identity(operation_id, "$.mcp.operation_id")
        self._in_flight.setdefault(label, set()).add(operation_id)

    def release_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        label = _identity_text(identity)
        operations = self._in_flight.get(label)
        if operations is None:
            return
        operations.discard(operation_id)
        if not operations:
            self._in_flight.pop(label, None)

    def in_flight_operations(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return tuple(sorted(self._in_flight.get(_identity_text(identity), set())))

    # -- 内部 ---------------------------------------------------------------

    async def _require_config(self, identity: MCPServiceIdentity) -> MCPServerConfig:
        label = _identity_text(identity)
        config = await self._store.load(identity)
        if config is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp service config not found", label)
        return config
