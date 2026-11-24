"""共享 MCP 连接管理（E-02 / 详细设计 §10.4、HANDOFF §9）。

本模块只承载**静态可验证**的准入合同：保存不启连、连接测试只发现（0 研究工具
调用）、预算与只读审核证据不足拒启、显式断开且在途时拒停（后效未知的 operation
保留归属到 owner 真实退出被确认）、断线不自动重连。断开与更新共享配置前必须核
**持久化**引用该配置版（service_id + config_version）的未终结研究（排队/暂停/可恢复），
该核对不得以管理器内存计数代答（I59）。连接测试只持有并关闭自己的临时会话，
关闭失败保留可定位残留（身份 + 错误类型 + 句柄）。
研究启连时由连接 owner 完成一次发现并**冻结工具目录 + 连接代次**，工具调用门据此
服务端核验目录/审核范围/schema，不采信请求自报（I04）。审核证据不是一次性结论：
每次工具调用前重新加载配置、重新观测实现指纹并重判只读准入，未准入即零新调用（I06）。

真实 FastMCP 传输适配见 :mod:`services.agent_service.mcp_fastmcp`（E-02-5 实证：
stdio 插入点、逐 send 门、FD 级 stderr、四诊断通道）。HTTP 传输的入站限额绑定在
SDK 解析前的字节层（``httpx2`` 响应流 + SDK 的单 SSE 事件上限），stdio 无该插入点，
故带 ``receive_bytes`` / ``structure_depth`` 的 stdio 配置在准入时被拒
（``CAPABILITY_UNVERIFIED``），不以「预算有值」放行（I03）。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn, Protocol
from urllib.parse import urlsplit

from core.agent_types import (
    MCP_DEFAULT_DEADLINE_S,
    MCP_MIN_DEADLINE_S,
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
from services.agent_service.mcp_budget import MCPBudgetGuard, MCPDeadline, require_pre_parse_bindings

# ---------------------------------------------------------------------------
# 传输配置（stdio 绝对可执行/cwd 必填/不经 shell；HTTP 显式风险确认 + Header 安全引用）
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
    #: stdio：绝对工作目录（必填：不使用应用自身 cwd 解析资源）。
    cwd: str | None = None
    #: streamable_http / sse：目标 URL（不得内嵌用户名/密码）。
    url: str | None = None
    #: SSE 仅作显式兼容：须用户显式核准该传输。
    sse_compat_approved: bool = False
    #: HTTP：附加请求头（名称, 安全引用）。值只能是 ``env:VAR`` / ``keyring:item``，
    #: 明文秘密（含 ``Authorization: Bearer <明文>``）一律拒绝。
    headers: tuple[tuple[str, str], ...] = ()
    #: HTTP：显式风险确认（无内嵌凭据、Header 仅安全引用、不注入明文 Authorization）。
    http_risk_approved: bool = False


#: HTTP 附加 Header 允许的安全引用 scheme（其余一律视为明文/未知 scheme）。
_HEADER_REF_SCHEMES: tuple[str, ...] = ("env:", "keyring:")
#: HTTP Header 名称合法字符（RFC 9110 token）。
_HEADER_NAME_CHARS = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyz")
#: 客户端/SDK 自行管理或可被用于请求走私的 Header：配置侧不得注入。
_FORBIDDEN_HTTP_HEADERS: frozenset[str] = frozenset(
    {
        "host",
        "content-length",
        "transfer-encoding",
        "connection",
        "proxy-connection",
        "cookie",
        "set-cookie",
        "proxy-authorization",
        "last-event-id",
        "te",
        "trailer",
        "upgrade",
    }
)


def _fail_transport(message: str, path: str = "$.mcp.transport") -> NoReturn:
    raise AgentValidationError("invalid_transport", f"{path}: {message}")


def _valid_header_ref(value: str) -> bool:
    """Header 值只能是安全引用：``env:NAME`` / ``keyring:item``（非空、无控制字符）。"""

    for scheme in _HEADER_REF_SCHEMES:
        if value.startswith(scheme):
            name = value[len(scheme) :]
            return bool(name) and name == name.strip() and all(ch.isprintable() for ch in name)
    return False


def _validate_http_headers(headers: tuple[tuple[str, str], ...], path: str) -> None:
    """Header 白名单 + 安全引用：禁凭据/走私类 Header，禁明文值，禁重复名。"""

    seen: set[str] = set()
    for pair in headers:
        if not isinstance(pair, tuple) or len(pair) != 2 or not all(isinstance(part, str) and part for part in pair):
            _fail_transport("http headers must be non-empty string pairs", f"{path}.headers")
        name, value = pair
        lowered = name.lower()
        if not lowered or any(ch not in _HEADER_NAME_CHARS for ch in lowered):
            _fail_transport("http header name is not a valid token", f"{path}.headers")
        if lowered in _FORBIDDEN_HTTP_HEADERS:
            _fail_transport(f"http header not configurable: {lowered}", f"{path}.headers")
        if lowered in seen:
            _fail_transport("duplicate http header name", f"{path}.headers")
        seen.add(lowered)
        if not _valid_header_ref(value):
            _fail_transport("http header value must be a secure reference (env: / keyring:)", f"{path}.headers")


def validate_transport_config(config: MCPTransportConfig, path: str = "$.mcp.transport") -> None:
    """传输配置校验：只接受合同内形状，不做 shell 展开或隐式协议互换。

    静态可判定项在此一次查清（cwd 必填/绝对/存在且为可访问目录、可执行文件存在且可执行、
    Header 安全引用、HTTP 风险显式确认）；无效工作目录在派发前即拒绝，不等到 spawn
    失败才暴露。OS 仍可能拒绝启动坏程序，故本函数不主张「必成功启动」。
    """

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
        command_path = Path(config.command)
        if not command_path.is_file():
            _fail_transport("stdio command must be an existing file", f"{path}.command")
        if not os.access(command_path, os.X_OK):
            _fail_transport("stdio command is not executable", f"{path}.command")
        if config.url is not None:
            _fail_transport("stdio must not carry a url", f"{path}.url")
        if not isinstance(config.cwd, str) or not config.cwd:
            _fail_transport("stdio requires a working directory", f"{path}.cwd")
        cwd_path = Path(config.cwd)
        if not cwd_path.is_absolute():
            _fail_transport("stdio cwd must be an absolute path", f"{path}.cwd")
        if not cwd_path.is_dir():
            _fail_transport("stdio cwd must be an existing directory", f"{path}.cwd")
        if not os.access(cwd_path, os.R_OK | os.X_OK):
            _fail_transport("stdio cwd is not accessible", f"{path}.cwd")
        if config.headers or config.http_risk_approved:
            _fail_transport("stdio must not carry http-only fields", path)
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
    _validate_http_headers(config.headers, path)
    # 明文 HTTP 才需要显式风险确认：https 由 TLS 承载机密性，不得要求用户确认
    # 一个与其实际配置不符的「明文传输」风险（那会误拒合法 https 配置）。
    if parts.scheme == "http" and not config.http_risk_approved:
        raise AgentValidationError("http_not_approved", f"{path}.http_risk_approved")


# ---------------------------------------------------------------------------
# 调用派发证据
# ---------------------------------------------------------------------------


class MCPCallNotDispatched(AgentServiceError):
    """请求未出站即被拒绝（会话已失效 / 发送前 deadline 已过）：效果确定未发生。

    本类型是「确定未执行」的唯一可用证据，调用门据此判 ``FAILED``（可安全重试）；
    出站之后发生的失败（协议/解码失败、服务端错误响应、超时、断线）都不得使用本类型，
    否则会把「后效未知」伪装成「确定失败」（I15）。
    """


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


def validate_mcp_server_config(config: MCPServerConfig, path: str = "$.mcp") -> None:
    """服务配置完整校验：保存与启连点**共用同一入口**，不信任已存储值（I18）。

    覆盖身份/显示名（拒绝空白与控制字符）、传输合同（cwd/可执行/HTTP 风险确认/
    Header 安全引用）、期限、审核绑定（审核记录必须属于同一服务身份）。
    预算冻结属准入判定（``CAPABILITY_UNVERIFIED``），不在此函数内。
    """

    config.identity.stable_key()
    require_identity(config.display_name, f"{path}.display_name")
    if not config.display_name.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in config.display_name):
        raise AgentValidationError("invalid_display_name", f"{path}.display_name")
    validate_transport_config(config.transport, f"{path}.transport")
    validate_mcp_deadline(config.connect_deadline_s, f"{path}.connect_deadline_s")
    validate_mcp_deadline(config.tool_deadline_s, f"{path}.tool_deadline_s")
    if config.audit is not None:
        encode_mcp_read_only_audit(config.audit)
        if config.audit.service != config.identity:
            raise AgentValidationError("invalid_audit_binding", f"{path}.audit.service")


@dataclass(frozen=True, slots=True)
class MCPAuditEvidenceRecheck:
    """工具调用前的审核证据重核结论（不缓存，每次调用重新观测）。"""

    #: 证据仍覆盖当前可观测实现（``reason`` 为稳定原因码，``None`` 仅当准入）。
    admitted: bool
    reason: str | None
    #: 本机探针只观测本机实现；remote 传输的远端目标状态不可观测，
    #: 此时不得宣称「已验证」，调用结果须如实标注该信任限制（I06）。
    trust_limited: bool


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
    """连接测试回执：只含初始化/发现结果，``research_calls`` 恒为 0。

    ``config_digest`` / ``catalog_digest`` / ``generation`` 共同冻结「本轮测试看到的
    配置与目录」：启连必须拿同一份回执核对，测试与启连之间配置或目录发生漂移即拒绝，
    不得用旧测试发现的工具去核准新连接（fail-closed）。
    """

    identity: MCPServiceIdentity
    tool_names: tuple[str, ...]
    catalog_digest: str
    research_calls: int
    closed: bool
    #: 本次测试使用的持久化配置快照摘要（与启连时重新加载的配置比对）。
    config_digest: str = ""
    #: 本身份的测试代次（单调递增）；更新的一次测试会使旧回执失效。
    generation: int = 0


@dataclass(frozen=True, slots=True)
class MCPConnectionTestResidue:
    """连接测试关闭未确认的残留：身份 + 错误类型 + 仍持有的临时会话句柄。

    连接测试只管理自己的会话：关闭失败时不伪装已关闭、不把句柄交给共享连接状态，
    而是保留该句柄供重试关闭与定位（``error`` 只记异常类型名，不含异常文本，
    避免外部传输错误消息携带端点/凭据进入回执）。
    """

    identity: MCPServiceIdentity
    error: str
    session: MCPClientSessionPort


class MCPConfigStorePort(Protocol):
    """配置持久化端口（生产复用原配置管理域，不新建 Agent 表）。"""

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None: ...

    async def save(self, config: MCPServerConfig) -> None: ...

    async def delete(self, identity: MCPServiceIdentity) -> bool: ...


class MCPFrozenUsagePort(Protocol):
    """已冻结该配置版的研究查询端口（I59：必须读持久化记录，不得用内存计数代答）。

    实现须返回引用 ``identity``（service_id + config_version）且尚未终结的研究稳定
    标识，至少覆盖排队（queued）、暂停（paused）与可恢复（未知/等待）状态；这些
    研究在重启后依然持有该配置版，故只有持久化查询才能证明「无使用者」。
    """

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]: ...


class MCPDispatchGatePort(Protocol):
    """派发停用门端口（P1-10）：与工具 claim 共享同一持久化线性化点（行锁）。

    ``stop`` 必须在单事务内锁定门行、复核持久化冻结使用者并写 ``stopped``；
    使用者存在即拒绝（不写状态）。认领侧在同一行锁下读门状态：停用先提交 →
    零新 claim；任一方先胜即唯一合法顺序。
    """

    async def stop(self, identity: MCPServiceIdentity) -> None: ...

    async def resume(self, identity: MCPServiceIdentity) -> None: ...


class MCPClientSessionPort(Protocol):
    """目标版客户端会话端口（真实 FastMCP 接线为 needs-spike）。"""

    async def list_tools(self, *, deadline_s: int) -> tuple[MCPToolDescriptor, ...]: ...

    async def call_tool(self, name: str, arguments: dict[str, Any], *, deadline_s: int) -> dict[str, Any]: ...

    async def aclose(self) -> None: ...


class MCPClientFactoryPort(Protocol):
    """会话工厂端口：仅在用户显式启连/显式测试时被调用。

    ``guard`` 由调用方注入时，会话的入站（``receive_bytes`` / ``structure_depth``）与
    出站后计量（目录/schema/解码/单条/留存）必须累计在**同一**账本上；未注入（如
    连接测试的临时会话）时由工厂为该次连接自建，仍是单一实例。
    """

    async def open(self, config: MCPServerConfig, *, guard: MCPBudgetGuard | None = None) -> MCPClientSessionPort: ...


class MCPImplementationProbe(Protocol):
    """实际实现探测端口：返回本机实现指纹；无法核定返回 ``None``（不准入）。"""

    async def probe(self, config: MCPServerConfig) -> str | None: ...


def _identity_text(identity: MCPServiceIdentity) -> str:
    return identity.stable_key()


def _frozen_config_drifted(frozen: MCPServerConfig, loaded: MCPServerConfig) -> bool:
    """启连后存储值是否偏离冻结态（审核身份/工具范围、传输、预算、期限）。

    显示名等展示字段不参与判定；上述任一项变化都意味着按旧冻结态继续服务不再
    等价于「按存储配置服务」，须拒绝并要求重新启连。
    """

    return (
        frozen.audit != loaded.audit
        or frozen.transport != loaded.transport
        or frozen.budget != loaded.budget
        or frozen.connect_deadline_s != loaded.connect_deadline_s
        or frozen.tool_deadline_s != loaded.tool_deadline_s
    )


def catalog_digest(tools: tuple[MCPToolDescriptor, ...]) -> str:
    """目录摘要：按工具名排序后的 (name, schema) 规范 JSON 摘要。"""

    canonical = json.dumps(
        [[tool.name, tool.schema_json] for tool in sorted(tools, key=lambda item: item.name)],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_hex(canonical.encode("utf-8"))


def config_snapshot_digest(config: MCPServerConfig) -> str:
    """配置快照摘要：与持久化 wire 同形，供启连核对「本轮测试用的配置」。

    使用编解码器的规范编码（局部导入避免 ``mcp`` ↔ ``mcp_config_codec`` 的模块级循环），
    故保存/测试/启连三处对同一配置得到同一摘要；任何字段（含传输、预算、期限、审核、
    显示名）变化都会改变摘要。
    """

    from services.agent_service.mcp_config_codec import encode_mcp_server_config

    canonical = json.dumps(
        encode_mcp_server_config(config),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return sha256_hex(canonical.encode("utf-8"))


# ---------------------------------------------------------------------------
# 共享连接管理
# ---------------------------------------------------------------------------


class SharedMCPConnectionManager:
    """同一服务身份/配置版本共享一个连接；并发启连 single-flight，观察者退出不关持有者连接。"""

    def __init__(
        self,
        *,
        store: MCPConfigStorePort,
        factory: MCPClientFactoryPort,
        probe: MCPImplementationProbe,
        usage: MCPFrozenUsagePort,
        dispatch_gate: MCPDispatchGatePort | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = store
        self._factory = factory
        self._probe = probe
        #: 已冻结该配置版的研究查询端口（持久化，不能以内存计数替代；I59）。
        self._usage = usage
        #: 派发停用门（P1-10）：与工具 claim 共享行锁；None = 未接线（保留旧判定）。
        self._dispatch_gate = dispatch_gate
        #: 启连总期限的单调时钟（可注入以便确定性测试；不得回退到墙钟）。
        self._monotonic = monotonic
        self._sessions: dict[str, MCPClientSessionPort] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._guards: dict[str, MCPBudgetGuard] = {}
        self._in_flight: dict[str, set[str]] = {}
        #: 后效未知（``ToolCallState.UNKNOWN``）的在途 operation：归属保留到所属会话
        #: 真实退出被确认后才释放，期间不释放占用、不允许重连重发（I53）。
        self._unresolved: dict[str, set[str]] = {}
        #: 同一身份/版本的启连只 open 一次；其余调用者等待并共享该结果。
        self._connecting: dict[str, asyncio.Task[None]] = {}
        #: 关闭未确认的会话句柄（closing/unknown）：实际退出后才移除，不得先丢后关（I17）。
        self._closing: dict[str, list[MCPClientSessionPort]] = {}
        #: 从 ``_sessions`` 摘下的连接 owner 句柄：其关闭被确认时才释放在途归属（I53）。
        self._closing_owner: dict[str, MCPClientSessionPort] = {}
        #: 同一身份的关闭 single-flight：并发断开/重连不会对同一句柄重复关闭。
        self._closing_tasks: dict[str, asyncio.Task[None]] = {}
        #: 连接 owner 在启连时冻结的发现目录与连接代次（单调递增，不复用）。
        self._catalogs: dict[str, tuple[MCPToolDescriptor, ...]] = {}
        self._generations: dict[str, int] = {}
        self._generation_seq = 0
        #: 连接测试关闭未确认的临时会话（身份/错误/句柄）：只影响测试自身，不并入共享状态。
        self._test_residue: dict[str, list[MCPConnectionTestResidue]] = {}
        #: 本身份最近一次测试的代次（单调递增，不复用）；旧回执据此失效。
        self._test_seq = 0
        self._test_generations: dict[str, int] = {}

    # -- 配置 ---------------------------------------------------------------

    async def save_config(self, config: MCPServerConfig) -> MCPConfigSaveReceipt:
        """保存配置：校验合同并持久化，**不打开连接、不发现工具**。

        同一身份/版本的更新不得就地改写已被研究冻结或被活动连接持有的配置：存在
        未终结研究引用即拒绝（I59）；已被活动连接持有的版本若内容发生变化同样拒绝
        （该连接按冻结态服务，改写会让「已连接」不再等于「按存储配置服务」），须显式
        断开或改用新 ``config_version`` 保存（同版更新/协议切换的收尾闭环）。
        """

        identity = config.identity
        label = _identity_text(identity)
        self._admit_config(config, label)
        frozen = self._configs.get(label)
        if frozen is not None and _frozen_config_drifted(frozen, config):
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "config version is held by an active connection; disconnect or save a new config version",
                label,
            )
        await self._require_no_frozen_users(identity, label, "update")
        await self._store.save(config)
        return MCPConfigSaveReceipt(identity=identity, connections_opened=0)

    @staticmethod
    def _admit_config(config: MCPServerConfig, label: str) -> None:
        """合同 + 预算准入：保存与**每次加载后**的启连点共用（I18，不信任存储值）。

        预算冻结之外还要求「真实插入点」：需前置拒绝的族（大帧 / 深 JSON）若在该
        传输上无可观测插入点，即显式拒绝，而不是按「预算有值」放行（I03）。
        """

        try:
            validate_mcp_server_config(config)
        except AgentValidationError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, f"invalid mcp config: {exc.code}", label) from exc
        if not config.budget.is_admitted():
            unfrozen = ",".join(config.budget.unfrozen_fields())
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp receive budgets not frozen: {unfrozen}", label
            )
        require_pre_parse_bindings(config.transport.kind, config.budget, label)

    # -- 显式测试 -----------------------------------------------------------

    async def connection_test(self, identity: MCPServiceIdentity) -> MCPConnectionTestReceipt:
        """用户显式连接测试：只初始化/鉴权/发现，0 研究工具调用，结束必关闭。

        启动/初始化/鉴权（``open``）与发现（``list_tools``）共用**同一**总期限：
        总预算自 open 起按单调时钟计，发现阶段只拿到剩余额度，故两段串联不会
        各得一个完整 ``connect_deadline_s``（I13）。预算耗尽即以明确错误码拒绝。

        临时会话只归本测试所有：不登记/不替换/不关闭共享连接；关闭失败或取消时
        保留残留（身份 + 错误类型 + 句柄）供重试关闭与定位，不回执 ``closed=True``。

        回执携带本次测试的配置摘要与代次：代次在测试**开始时**推进，故任何失败/并发
        测试都会让旧回执失效，启连只能使用最近一次成功测试的回执。
        """

        config = await self._require_config(identity)
        label = _identity_text(identity)
        self._test_seq += 1
        generation = self._test_seq
        self._test_generations[label] = generation
        config_digest = config_snapshot_digest(config)
        deadline = self._start_connect_deadline(config)
        session = await self._factory.open(config)
        try:
            tools = await self._discover(session, deadline, label)
        except BaseException:
            # 主失败优先传播；关闭失败只登记残留，不覆盖发现阶段的原因。
            try:
                await self._close_test_session(identity, session)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # 残留已登记（`_test_residue`），主异常照常抛出。
            raise
        try:
            await self._close_test_session(identity, session)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "connection test session close is unconfirmed",
                label,
            ) from exc
        return MCPConnectionTestReceipt(
            identity=identity,
            tool_names=tuple(sorted(tool.name for tool in tools)),
            catalog_digest=catalog_digest(tools),
            research_calls=0,
            closed=True,
            config_digest=config_digest,
            generation=generation,
        )

    def test_generation(self, identity: MCPServiceIdentity) -> int | None:
        """本身份最近一次连接测试的代次；从未测试即 ``None``（不猜）。"""

        return self._test_generations.get(_identity_text(identity))

    async def retry_test_residue_close(self, identity: MCPServiceIdentity) -> int:
        """重试关闭连接测试未确认的临时会话（测试残留的唯一收尾入口）。

        返回本次确认关闭的数量（无残留即 0）；全部确认退出才清空残留，仍失败则保留
        （错误类型更新为本次原因）并显式拒绝，不伪装已收尾。取消继续传播，未处理完的
        残留保持登记。
        """

        label = _identity_text(identity)
        residue = self._test_residue.get(label)
        if not residue:
            return 0
        pending = list(residue)
        failures: list[MCPConnectionTestResidue] = []
        closed = 0
        for index, item in enumerate(pending):
            try:
                await item.session.aclose()
                closed += 1
            except asyncio.CancelledError:
                # 未处理完的（含本条）保持登记，下一轮继续收尾。
                self._test_residue[label] = pending[index:]
                raise
            except Exception as exc:
                failures.append(
                    MCPConnectionTestResidue(identity=item.identity, error=type(exc).__name__, session=item.session)
                )
        if failures:
            self._test_residue[label] = failures
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "connection test residue close is still unconfirmed",
                label,
            )
        self._test_residue.pop(label, None)
        return closed

    def connection_test_residue(self, identity: MCPServiceIdentity) -> tuple[MCPConnectionTestResidue, ...]:
        """连接测试未确认关闭的残留（身份/错误类型/句柄）；已确认关闭则不含该项。"""

        return tuple(self._test_residue.get(_identity_text(identity), ()))

    async def _close_test_session(self, identity: MCPServiceIdentity, session: MCPClientSessionPort) -> None:
        """关闭测试自有会话；失败/取消即登记残留（身份+错误+句柄）并按类型传播。"""

        label = _identity_text(identity)
        try:
            await session.aclose()
        except asyncio.CancelledError:
            self._record_test_residue(identity, label, session, "CancelledError")
            raise
        except Exception as exc:
            self._record_test_residue(identity, label, session, type(exc).__name__)
            raise

    def _record_test_residue(
        self, identity: MCPServiceIdentity, label: str, session: MCPClientSessionPort, error: str
    ) -> None:
        residue = self._test_residue.setdefault(label, [])
        if not any(item.session is session for item in residue):
            residue.append(MCPConnectionTestResidue(identity=identity, error=error, session=session))

    # -- 显式启连与断开 -----------------------------------------------------

    async def connect(self, identity: MCPServiceIdentity) -> None:
        """用户显式启连：预算全冻结 + 只读审核绑定当前实现，才允许建立共享连接。

        按身份/版本 single-flight：同一 key 的并发调用共享同一次准入/open/发现，
        实际 open 与存活 owner 各至多 1；单个调用者被取消不中断他人的等待。
        启连时由连接 owner 完成一次发现并冻结目录；发现失败不登记连接（关闭会话）。
        总期限（启动/初始化/鉴权/发现）由该次实际启连持有，并发等待者共享同一预算，
        不会各自获得一份新的 ``connect_deadline_s``（I13）。
        重连前必须先确认上次关闭已实际退出，未确认则重试关闭，仍失败即显式拒绝（I17）。
        已有存活会话时同样重核审核证据：审核撤回/实现变化后不得按旧证据继续服务（I06）。
        连接测试关闭未确认（残留）时拒绝新开：残留会话可能仍在运行，收尾未知不得
        用新连接覆盖（须先经 ``retry_test_residue_close`` 确认退出）。
        """

        label = _identity_text(identity)
        key = label
        if key in self._sessions:
            recheck = await self.recheck_audit_evidence(identity)
            if not recheck.admitted:
                raise AgentServiceError(
                    AgentErrorCode.CAPABILITY_UNVERIFIED,
                    f"read-only audit refused: {recheck.reason}",
                    label,
                )
            return
        if self._test_residue.get(label):
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "connection test residue close is unconfirmed; retry close before connecting",
                label,
            )
        await self._confirm_previous_close(label)
        pending = self._connecting.get(key)
        if pending is None:
            pending = asyncio.create_task(self._open_shared(identity, label))
            self._connecting[key] = pending

            def _finish(task: asyncio.Task[None]) -> None:
                if self._connecting.get(key) is task:
                    self._connecting.pop(key, None)
                if not task.cancelled():
                    task.exception()  # 消费异常，避免无人等待时告警；等待者仍会收到该异常。

            pending.add_done_callback(_finish)
        await asyncio.shield(pending)

    async def _open_shared(self, identity: MCPServiceIdentity, label: str) -> None:
        """single-flight 的实际启连：准入通过才 open，发现成功后才在同一同步段登记归属。"""

        config = await self._require_config(identity)
        if not config.budget.is_admitted():
            unfrozen = ",".join(config.budget.unfrozen_fields())
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED, f"mcp receive budgets not frozen: {unfrozen}", label
            )
        recheck = await self._audit_evidence(config)
        if not recheck.admitted:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                f"read-only audit refused: {recheck.reason}",
                label,
            )
        guard = MCPBudgetGuard(config.budget, identity=label)
        deadline = self._start_connect_deadline(config)
        # 每连接单一账本：入站限额（receive_bytes/structure_depth）与解析后各族
        # （目录/schema/解码/单条/留存）累计在同一 guard 上，由管理器注入会话。
        session = await self._factory.open(config, guard=guard)
        try:
            tools = await self._discover(session, deadline, label)
        except BaseException:
            await self._close_quietly(label, session)
            raise
        if label in self._sessions:  # 已有 owner：回收本次 client，绝不覆盖
            await self._close_quietly(label, session)
            return
        self._sessions[label] = session
        self._configs[label] = config
        self._guards[label] = guard
        self._catalogs[label] = tuple(tools)
        self._generation_seq += 1
        self._generations[label] = self._generation_seq

    async def disconnect(self, identity: MCPServiceIdentity) -> None:
        """显式断开：仍可能执行中的在途调用默认拒停；后效未知者保留到退出确认（I17/I53）。

        已判后效未知（UNKNOWN）的 operation 不阻塞断开（关闭是确认其真实退出的唯一
        途径），但其归属**不在此处释放**：只有 owner 句柄关闭成功后才一并清除，故
        关闭失败/取消期间仍按原 operation 核对，也不会为用户释放重连重发机会。

        断开前另须核对持久化引用该配置版的排队/暂停/可恢复研究（I59）：内存
        ``_in_flight`` 为 0 只说明此刻没有执行中的调用，不构成「无使用者」的证据。
        """

        label = _identity_text(identity)
        if self._executing_operations(label):
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "connection still has in-flight operations", label)
        # P1-10：停用门与工具 claim 共享同一行锁——停用先提交即拒绝新 claim。
        # 门实现内部在同事务复核持久化冻结使用者（存在即拒绝，零状态写）。
        if self._dispatch_gate is not None:
            await self._dispatch_gate.stop(identity)
        else:
            await self._require_no_frozen_users(identity, label, "disconnect")
        # 持久化查询含 await：其后必须重新做同步在途复核，且该复核到 pop 之间不得
        # 再 await，否则等待查询期间新登记的调用会在「检查通过 → 摘下会话」缝隙里
        # 被执行中关闭（I17）。
        if self._executing_operations(label):
            if self._dispatch_gate is not None:
                await self._dispatch_gate.resume(identity)
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "connection still has in-flight operations", label)
        session = self._sessions.pop(label, None)
        self._forget_connection_state(label)
        if session is not None:
            self._closing_owner[label] = session
            self._track_closing(label, session)
        try:
            await self._request_close(label)
        except BaseException:
            # 关闭未确认：服务仍可能在线，恢复派发门以免永久拒绝新 claim（R2 继续传播）。
            if self._dispatch_gate is not None:
                await self._dispatch_gate.resume(identity)
            raise

    async def frozen_users(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        """持久化引用该配置版且未终结的研究标识；查询不可用即拒绝（fail-closed）。"""

        label = _identity_text(identity)
        try:
            return tuple(sorted(await self._usage.frozen_users(identity)))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "mcp config frozen users are unverifiable",
                label,
            ) from exc

    async def delete_config(self, identity: MCPServiceIdentity) -> bool:
        """删除服务配置（不关闭连接、不删除授权）：调用方须先确认可安全删除。

        fail-closed 前置：仍连接（须先显式断开）、连接测试/关闭残留未确认（收尾未知
        不得用删除覆盖）、持久化仍有排队/暂停/可恢复研究引用，一律拒删；全部通过才
        删除配置，返回是否实际删除了一行（``False`` 仅当配置已不存在）。
        """

        label = _identity_text(identity)
        if label in self._sessions:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "disconnect the mcp service before deleting it",
                label,
            )
        if self._test_residue.get(label):
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "connection test residue close is unconfirmed; retry close before deleting",
                label,
            )
        if self._closing.get(label):
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "previous mcp session close is unconfirmed; confirm close before deleting",
                label,
            )
        await self._require_no_frozen_users(identity, label, "delete")
        return await self._store.delete(identity)

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

    def frozen_catalog(self, identity: MCPServiceIdentity) -> tuple[MCPToolDescriptor, ...]:
        """启连时冻结的发现目录；未启连即拒绝（调用门以此为准，不采信请求自报）。"""

        label = _identity_text(identity)
        tools = self._catalogs.get(label)
        if tools is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return tools

    def connection_generation(self, identity: MCPServiceIdentity) -> int:
        """当前连接的冻结目录代次；显式重连后单调递增，旧代次请求不可复用。"""

        label = _identity_text(identity)
        generation = self._generations.get(label)
        if generation is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return generation

    def audit_tool_scope(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        """启连时已通过只读审核的工具范围（审核记录随配置冻结）。"""

        label = _identity_text(identity)
        config = self._configs.get(label)
        if config is None or config.audit is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp connection is not established", label)
        return config.audit.tool_scope

    async def mark_session_lost(self, identity: MCPServiceIdentity) -> None:
        """断线明示：保留真实句柄并尝试关闭，不自动重连/重启；关闭未确认则保留待上报。

        在途/后效未知的归属同样保留到 owner 句柄真实退出被确认（I53）。
        """

        label = _identity_text(identity)
        session = self._sessions.pop(label, None)
        self._forget_connection_state(label)
        if session is not None:
            self._closing_owner[label] = session
            self._track_closing(label, session)
        await self._request_close(label)

    # -- 在途归属 -----------------------------------------------------------

    def register_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        """登记在途 operation；同一 operation 已在途即拒绝（不共享归属）。

        并发重复使用同一 ``operation_id`` 会让先到者的结果与后到者的 unknown 归属
        互相污染：任一方的后效未知都会把另一方的生命周期拖住。故登记是原子检查 +
        写入（调用点无 ``await``），重复即 ``INPUT_CONFLICT``、零派发。
        """

        label = _identity_text(identity)
        require_identity(operation_id, "$.mcp.operation_id")
        operations = self._in_flight.get(label)
        if operations is not None and operation_id in operations:
            raise AgentServiceError(
                AgentErrorCode.INPUT_CONFLICT,
                "operation is already in flight on this connection",
                f"{label}/{operation_id}",
            )
        self._in_flight.setdefault(label, set()).add(operation_id)

    def release_in_flight(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        label = _identity_text(identity)
        operations = self._in_flight.get(label)
        if operations is None:
            return
        operations.discard(operation_id)
        unresolved = self._unresolved.get(label)
        if unresolved is not None:
            unresolved.discard(operation_id)
            if not unresolved:
                self._unresolved.pop(label, None)
        if not operations:
            self._in_flight.pop(label, None)

    def mark_in_flight_unknown(self, identity: MCPServiceIdentity, operation_id: str) -> None:
        """记录后效未知：归属保留到所属会话真实退出被确认后再释放（I53）。

        未知退出（超时/断线/协议或服务端错误/结果不可编码）不构成「效果确定未发生」，
        故不得立即释放归属，也不得让断开/重连把仍在执行的原 operation 视为已结束。
        """

        label = _identity_text(identity)
        if operation_id not in self._in_flight.get(label, set()):
            # 归属已随 owner 退出确认被释放：此时再登记会留下永不清除的悬空项，
            # 并可能在新一代连接上遮蔽同 id 的在途 operation（I53）。
            return
        self._unresolved.setdefault(label, set()).add(operation_id)

    def in_flight_operations(self, identity: MCPServiceIdentity) -> tuple[str, ...]:
        return tuple(sorted(self._in_flight.get(_identity_text(identity), set())))

    # -- 内部 ---------------------------------------------------------------

    async def _require_no_frozen_users(self, identity: MCPServiceIdentity, label: str, action: str) -> None:
        """断开/更新共享配置前核持久化使用者；查询不可用时同样拒绝（fail-closed，I59）。

        ``action`` 只用于区分拒绝入口（``disconnect`` / ``update``），不改变判定。
        使用者标识来自持久化研究记录，故不依赖管理器内存计数。
        """

        try:
            users = await self._usage.frozen_users(identity)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                f"mcp config frozen users are unverifiable before {action}",
                label,
            ) from exc
        if users:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                f"mcp config is still frozen by queued/paused researches before {action}: {','.join(sorted(users))}",
                label,
            )

    def _forget_connection_state(self, label: str) -> None:
        """连接不再可用：清 config/guard/catalog/generation；会话句柄另行保留。"""

        self._configs.pop(label, None)
        self._guards.pop(label, None)
        self._catalogs.pop(label, None)
        self._generations.pop(label, None)

    def _executing_operations(self, label: str) -> set[str]:
        """仍可能正在执行的 operation（排除已判后效未知者）。"""

        return self._in_flight.get(label, set()) - self._unresolved.get(label, set())

    def _track_closing(self, label: str, session: MCPClientSessionPort) -> None:
        """登记关闭未确认句柄（closing/unknown）：保留准确引用直到实际退出。"""

        handles = self._closing.setdefault(label, [])
        if not any(handle is session for handle in handles):
            handles.append(session)

    async def _request_close(self, label: str) -> None:
        """关闭 single-flight：同一身份只跑一次关闭；失败保留句柄，等待者共享异常。"""

        if not self._closing.get(label):
            return
        pending = self._closing_tasks.get(label)
        if pending is None:
            pending = asyncio.create_task(self._close_tracked(label))
            self._closing_tasks[label] = pending

            def _finish(task: asyncio.Task[None]) -> None:
                if self._closing_tasks.get(label) is task:
                    self._closing_tasks.pop(label, None)
                if not task.cancelled():
                    task.exception()  # 消费异常，避免无人等待时告警；等待者仍会收到该异常。

            pending.add_done_callback(_finish)
        await asyncio.shield(pending)

    async def _close_tracked(self, label: str) -> None:
        """逐个关闭已登记句柄，仅实际退出成功后移除；失败/取消保留其余句柄（I17/I53）。"""

        while True:
            handles = self._closing.get(label)
            if not handles:
                self._closing.pop(label, None)
                return
            session = handles[0]
            await session.aclose()
            if self._closing_owner.get(label) is session:
                # owner 真实退出已确认：该连接上的在途/未知 operation 不可能再产生后效，
                # 在此之前归属一律保留（关闭失败/取消时不得提前释放，I53）。
                self._closing_owner.pop(label, None)
                self._in_flight.pop(label, None)
                self._unresolved.pop(label, None)
            remaining = self._closing.get(label)
            if remaining is not None:
                remaining[:] = [handle for handle in remaining if handle is not session]

    async def _close_quietly(self, label: str, session: MCPClientSessionPort) -> None:
        """关闭不登记会话；失败时保留句柄，交由重连前确认/上报（不伪装已关闭）。"""

        self._track_closing(label, session)
        try:
            await self._request_close(label)
        except asyncio.CancelledError:
            raise
        except Exception:
            # 句柄仍在 _closing：下一次 connect 必须先确认退出，否则显式拒绝（I17/R21）。
            return

    async def _confirm_previous_close(self, label: str) -> None:
        """启连前确认上次关闭已实际退出；未确认则重试关闭，仍失败即显式拒绝（I17/R21）。"""

        if not self._closing.get(label):
            return
        try:
            await self._request_close(label)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "previous mcp session close is unconfirmed",
                label,
            ) from exc

    def _start_connect_deadline(self, config: MCPServerConfig) -> MCPDeadline:
        """启连总期限：open（启动/初始化/鉴权）与发现共用同一单调时钟起点。"""

        return MCPDeadline(seconds=config.connect_deadline_s, monotonic=self._monotonic)

    @staticmethod
    async def _discover(
        session: MCPClientSessionPort, deadline: MCPDeadline, label: str
    ) -> tuple[MCPToolDescriptor, ...]:
        """在总预算剩余内发现工具：端口按整秒收预算，外层按真实剩余时长硬切。

        端口合同以整秒表达期限，故剩余不足 1 秒时取最小 1 秒而不是整数截断为 0
        ——否则合法的 ``connect_deadline_s=1`` 配置会在发现阶段被误拒（I13）。总期限
        仍由外层 ``asyncio.timeout`` 以**精确剩余秒数**约束，故 open 与发现两段串联
        不会各自获得一份完整 ``connect_deadline_s``，也不会超出总期限。
        """

        remaining = deadline.remaining()
        if remaining <= 0.0:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "mcp connect deadline exceeded before discovery",
                label,
            )
        budget_s = max(MCP_MIN_DEADLINE_S, int(remaining))
        async with asyncio.timeout(remaining):
            return await session.list_tools(deadline_s=budget_s)

    async def recheck_audit_evidence(self, identity: MCPServiceIdentity) -> MCPAuditEvidenceRecheck:
        """工具调用/重复启连前重核审核证据与失效条件：**不缓存**结论。

        重新加载并准入配置（I18：不信任存储值）后重新观测实现指纹，与审核记录比对。
        **已连接时另核对冻结态**：同一身份/版本的存储值若在启连后变化（审核记录、
        工具范围、传输、预算、期限），冻结态不再等于存储值，直接判不通过，不按旧
        冻结态继续服务。
        观测到指纹变化只说明「本机可观测项与审核记录不一致」，不足以断言运行中进程
        已被替换；因此未准入即拒绝，而非推断具体原因。remote 传输的远端目标状态
        本机不可观测，结论置 ``trust_limited``，调用方不得将其呈现为「已验证」。
        """

        config = await self._require_config(identity)
        evidence = await self._audit_evidence(config)
        if not evidence.admitted:
            # 审核撤回/指纹变化等更具体的原因优先透出（诊断与测试均依赖稳定原因码）。
            return evidence
        label = _identity_text(identity)
        frozen = self._configs.get(label)
        if frozen is not None and _frozen_config_drifted(frozen, config):
            return MCPAuditEvidenceRecheck(
                admitted=False,
                reason="config_changed_after_connect",
                trust_limited=evidence.trust_limited,
            )
        return evidence

    async def _audit_evidence(self, config: MCPServerConfig) -> MCPAuditEvidenceRecheck:
        """按配置重新观测实现指纹并判定只读准入（调用方负责在 claim/发送前拒绝）。"""

        fingerprint = await self._probe.probe(config)
        admission = evaluate_read_only_audit(config.audit, fingerprint)
        return MCPAuditEvidenceRecheck(
            admitted=admission.admitted,
            reason=admission.reason,
            trust_limited=config.transport.kind is not MCPTransportKind.STDIO,
        )

    async def _require_config(self, identity: MCPServiceIdentity) -> MCPServerConfig:
        """加载配置并在使用点重核：存储值一律不信任（I18）。"""

        label = _identity_text(identity)
        config = await self._store.load(identity)
        if config is None:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp service config not found", label)
        if config.identity != identity:
            raise AgentServiceError(AgentErrorCode.CONFIG_UNAVAILABLE, "mcp config identity mismatch", label)
        self._admit_config(config, label)
        return config
