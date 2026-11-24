"""MCP 服务设置 ViewModel（设置页「MCP 服务」页签）。

职责（§1.8 UI 路由 + §3.2 MVVM）：

- 把 :class:`~services.agent_service.mcp_lifecycle.MCPServiceLifecycle` 的
  配置保存 / 连接测试 / 显式启连 / 显式断开映射为不可变状态快照与命令；
- **不自动启连**：只有用户点击「连接测试」「启连」才触发出站；``load`` 只读配置；
- **不宣称已打通**：操作结果如实反映回执，失败保留原因（R21）；
- 启连核准工具只能来自本次连接测试发现且落入只读审核范围的工具；启连把本轮测试
  回执交给服务层，由服务层核对配置摘要/测试代次/目录摘要，漂移即拒绝（须重新测试）；
- 测试残留关闭未确认时提供显式「重试关闭残留」命令，不用新连接覆盖未知收尾；
- VM 不 import flet、不持有控件、不感知 locale（只产出 i18n key/params）；
- ``CancelledError`` 原样传播（R2）；异常消息经 ``DataSanitizer`` 脱敏后进入状态（R9）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace

from core.agent_types import (
    MCP_BUDGET_FIELDS,
    MCPBudgetProfile,
    MCPReadOnlyAudit,
    MCPServiceIdentity,
    MCPTransportKind,
)
from services.agent_service.mcp import MCPConnectionTestReceipt, MCPServerConfig, MCPTransportConfig
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle
from ui.viewmodels import Message
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.log_decorators import PerfThreshold, log_async_operation
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

#: 生命周期未接线时的原因 key（View 按当前 locale 渲染）。
UNAVAILABLE_KEY = "mcp_unavailable"

STATUS_INFO = "info"
STATUS_SUCCESS = "success"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"

#: 可在表单中直接编辑的文本字段（白名单，避免 View 传入未知字段）。
DRAFT_TEXT_FIELDS = frozenset(
    {
        "service_id",
        "config_version",
        "display_name",
        "transport_kind",
        "command",
        "args_text",
        "env_text",
        "cwd",
        "url",
        "headers_text",
        "connect_deadline_s",
        "tool_deadline_s",
        "audit_tool_scope_text",
        "audit_implementation_fingerprint",
        "audit_evidence_source",
        "audit_reviewed_at",
        "audit_invalidation_text",
    }
)

#: 可在表单中直接切换的布尔字段。
DRAFT_FLAG_FIELDS = frozenset({"sse_compat_approved", "http_risk_approved"})


def _split_lines(text: str) -> tuple[str, ...]:
    """按行切分非空条目（保留条目内部空格）。"""

    return tuple(line.strip() for line in text.splitlines() if line.strip())


def _parse_pairs(text: str, field: str) -> tuple[tuple[str, str], ...]:
    """``NAME=VALUE`` 逐行解析；格式不符即报字段名（不猜值、不静默丢弃）。"""

    pairs: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        name, separator, value = line.partition("=")
        if not separator or not name.strip() or not value.strip():
            raise ValueError(field)
        pairs.append((name.strip(), value.strip()))
    return tuple(pairs)


def _parse_required_int(text: str, field: str, *, minimum: int = 1) -> int:
    """必填正整数解析；空串/非整数/越界均报字段名。"""

    raw = text.strip()
    if not raw:
        raise ValueError(field)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(field) from None
    if value < minimum:
        raise ValueError(field)
    return value


def _parse_optional_positive_int(text: str, field: str) -> int | None:
    """预算项：空串表示「未冻结」（``None``），非空必须是正整数。"""

    if not text.strip():
        return None
    try:
        value = int(text.strip())
    except ValueError:
        raise ValueError(field) from None
    if value <= 0:
        raise ValueError(field)
    return value


def _format_error(exc: BaseException) -> str:
    """异常 → 可展示文本（R9：一律脱敏，不直出原文）。"""

    return DataSanitizer.sanitize_error(exc if isinstance(exc, Exception) else str(exc))


@dataclass(frozen=True)
class MCPServiceRow:
    """服务列表投影（只读展示；不含凭据与环境细节）。"""

    identity_key: str
    service_id: str
    config_version: int
    display_name: str
    transport_kind: str
    connected: bool


@dataclass(frozen=True)
class MCPConfigDraft:
    """配置表单草稿（全部为字符串/布尔，解析在保存时一次完成）。"""

    service_id: str = ""
    config_version: str = "1"
    display_name: str = ""
    transport_kind: str = MCPTransportKind.STDIO.value
    command: str = ""
    args_text: str = ""
    env_text: str = ""
    cwd: str = ""
    url: str = ""
    headers_text: str = ""
    sse_compat_approved: bool = False
    http_risk_approved: bool = False
    connect_deadline_s: str = "30"
    tool_deadline_s: str = "30"
    #: 与 ``MCP_BUDGET_FIELDS`` 同序的 8 个预算输入；空串 = 未冻结。
    budget_values: tuple[str, ...] = ("",) * len(MCP_BUDGET_FIELDS)
    audit_tool_scope_text: str = ""
    audit_implementation_fingerprint: str = ""
    audit_evidence_source: str = ""
    audit_reviewed_at: str = ""
    audit_invalidation_text: str = ""


@dataclass(frozen=True)
class MCPServicesState:
    """「MCP 服务」页不可变状态快照。"""

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_KEY
    loaded: bool = False
    rows: tuple[MCPServiceRow, ...] = ()
    busy: bool = False
    draft: MCPConfigDraft = MCPConfigDraft()
    #: 草稿身份是否已保存（仅已保存配置可测试/启连）。
    draft_saved: bool = False
    #: 本次连接测试发现的工具（启连核准集的唯一来源）。
    discovered_tools: tuple[str, ...] = ()
    #: 用户勾选的核准工具（必须是 discovered_tools 的子集）。
    approved_tools: tuple[str, ...] = ()
    status: Message | None = None
    status_level: str = STATUS_INFO


class MCPServicesViewModel(ObservableViewModelMixin[MCPServicesState]):
    """设置页「MCP 服务」页签 ViewModel（通过 ``use_viewmodel(factory=)`` 消费）。"""

    def __init__(self, lifecycle: MCPServiceLifecycle | None) -> None:
        self._lifecycle = lifecycle
        self._configs: dict[str, MCPServerConfig] = {}
        #: 本轮连接测试回执（启连凭它核对配置/代次/目录，漂移即拒绝）。
        self._test_receipt: MCPConnectionTestReceipt | None = None
        self._state = MCPServicesState(available=lifecycle is not None)
        self._init_mixin_fields()

    # -- 读取 ---------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def load(self) -> None:
        """读取已保存配置与连接状态；未接线时保持不可用（不伪造空列表）。"""

        if self._lifecycle is None or self._state.busy:
            return
        self._set_state(busy=True)
        try:
            configs = await self._lifecycle.list_services()
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._configs = {config.identity.stable_key(): config for config in configs}
        self._set_state(
            rows=self._build_rows(configs),
            loaded=True,
            busy=False,
            draft_saved=self._is_draft_saved(),
        )

    def select_service(self, identity_key: str) -> None:
        """把已保存配置载入表单（不连接、不测试）。"""

        config = self._configs.get(identity_key)
        if config is None:
            return
        self._test_receipt = None
        self._set_state(
            draft=self._draft_from_config(config),
            draft_saved=True,
            discovered_tools=(),
            approved_tools=(),
            status=None,
            status_level=STATUS_INFO,
        )

    def begin_new(self) -> None:
        """清空表单进入新建态（不写库、不连接）。"""

        self._test_receipt = None
        self._set_state(
            draft=MCPConfigDraft(),
            draft_saved=False,
            discovered_tools=(),
            approved_tools=(),
            status=None,
            status_level=STATUS_INFO,
        )

    # -- 表单编辑 -----------------------------------------------------------

    def set_draft_text(self, field: str, value: str) -> None:
        """更新单个文本字段（未知字段忽略，避免拼写错误污染状态）。"""

        if field not in DRAFT_TEXT_FIELDS:
            return
        self._set_state(draft=replace(self._state.draft, **{field: value}), draft_saved=self._is_draft_saved())
        if field in {"transport_kind", "service_id", "config_version"}:
            self._test_receipt = None
            self._set_state(discovered_tools=(), approved_tools=())

    def set_draft_flag(self, field: str, value: bool) -> None:
        """更新显式核准开关（SSE / HTTP 风险确认）。"""

        if field not in DRAFT_FLAG_FIELDS:
            return
        self._set_state(draft=replace(self._state.draft, **{field: bool(value)}))

    def set_budget_value(self, index: int, value: str) -> None:
        """更新第 ``index`` 项预算（顺序即 ``MCP_BUDGET_FIELDS``）。"""

        if not 0 <= index < len(MCP_BUDGET_FIELDS):
            return
        values = list(self._state.draft.budget_values)
        values[index] = value
        self._set_state(draft=replace(self._state.draft, budget_values=tuple(values)))

    def toggle_approved_tool(self, name: str) -> None:
        """勾选/取消核准工具（仅限本次测试发现的工具）。"""

        if name not in self._state.discovered_tools:
            return
        approved = set(self._state.approved_tools)
        if name in approved:
            approved.discard(name)
        else:
            approved.add(name)
        self._set_state(approved_tools=tuple(sorted(approved)))

    # -- 操作 ---------------------------------------------------------------

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def save_config(self) -> None:
        """保存表单配置（不启连）；解析失败如实报字段，不吞错。"""

        if not self._guard():
            return
        lifecycle = self._lifecycle
        if lifecycle is None:
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            self._set_state(
                status=Message("mcp_status_invalid_draft", {"field": str(exc)}),
                status_level=STATUS_ERROR,
            )
            return
        self._set_state(busy=True, status=None)
        try:
            receipt = await lifecycle.save_config(config)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        identity = receipt.identity
        self._set_state(
            busy=False,
            status=Message(
                "mcp_status_saved",
                {"service_id": identity.service_id, "version": str(identity.config_version)},
            ),
            status_level=STATUS_SUCCESS,
        )
        await self.load()

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def connection_test(self) -> None:
        """对已保存配置执行连接测试（仅初始化/发现，0 研究调用）。"""

        if not self._guard():
            return
        lifecycle = self._lifecycle
        identity = self._require_saved_identity()
        if lifecycle is None or identity is None:
            return
        self._set_state(busy=True, status=None, discovered_tools=(), approved_tools=())
        self._test_receipt = None
        try:
            receipt = await lifecycle.connection_test(identity)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._test_receipt = receipt
        self._set_state(
            busy=False,
            discovered_tools=tuple(receipt.tool_names),
            approved_tools=(),
            status=Message(
                "mcp_status_tested",
                {
                    "count": str(len(receipt.tool_names)),
                    "closed": "true" if receipt.closed else "false",
                },
            ),
            status_level=STATUS_SUCCESS if receipt.closed else STATUS_WARNING,
        )

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def connect(self) -> None:
        """显式启连并冻结核准工具集（未测试/未保存时明确拒绝，不猜）。

        启连携带本轮测试回执：服务层据此核对配置摘要/测试代次/目录摘要，测试与启连
        之间发生漂移即拒绝（必须重新测试），不把旧勾选当新连接的核准。
        """

        if not self._guard():
            return
        lifecycle = self._lifecycle
        identity = self._require_saved_identity()
        if lifecycle is None or identity is None:
            return
        # 以「本轮测试回执存在」判定测试通过；测试成功但目录为空是合法回执，
        # 不得用工具列表非空代替成功（N19）。
        if self._test_receipt is None:
            self._set_state(status=Message("mcp_status_need_test"), status_level=STATUS_WARNING)
            return
        self._set_state(busy=True, status=None)
        try:
            receipt = await lifecycle.connect(
                identity,
                approved_tools=self._state.approved_tools,
                test_receipt=self._test_receipt,
            )
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(
            busy=False,
            status=Message("mcp_status_connected", {"count": str(len(receipt.tools))}),
            status_level=STATUS_SUCCESS,
        )
        await self._refresh_rows()

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def disconnect(self) -> None:
        """显式断开（在途使用者会被管理器拒绝，不伪装成功）。"""

        if not self._guard():
            return
        lifecycle = self._lifecycle
        identity = self._require_saved_identity()
        if lifecycle is None or identity is None:
            return
        self._set_state(busy=True, status=None)
        try:
            await lifecycle.disconnect(identity)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._test_receipt = None
        self._set_state(
            busy=False,
            discovered_tools=(),
            approved_tools=(),
            status=Message("mcp_status_disconnected", {"service_id": identity.service_id}),
            status_level=STATUS_INFO,
        )
        await self._refresh_rows()

    @log_async_operation(threshold_ms=PerfThreshold.EXTERNAL_NETWORK)
    async def retry_test_residue_close(self) -> None:
        """重试关闭连接测试未确认的残留会话（收尾未知不得靠新连接覆盖）。"""

        if not self._guard():
            return
        lifecycle = self._lifecycle
        identity = self._require_saved_identity()
        if lifecycle is None or identity is None:
            return
        self._set_state(busy=True, status=None)
        try:
            closed = await lifecycle.retry_test_residue_close(identity)
        except asyncio.CancelledError:
            self._set_state(busy=False)
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._set_state(
            busy=False,
            status=Message(
                "mcp_status_residue_closed" if closed else "mcp_status_no_residue",
                {"service_id": identity.service_id, "count": str(closed)},
            ),
            status_level=STATUS_SUCCESS,
        )

    # -- 内部 ---------------------------------------------------------------

    def _guard(self) -> bool:
        """可用且非忙才执行；不可用时给出原因而不静默无反应。"""

        if self._lifecycle is None:
            self._set_state(status=Message(UNAVAILABLE_KEY), status_level=STATUS_WARNING)
            return False
        return not self._state.busy

    def _require_saved_identity(self) -> MCPServiceIdentity | None:
        """当前草稿必须对应已保存配置；否则提示先保存，不尝试连接。"""

        identity = self._draft_identity()
        if identity is None:
            self._set_state(
                status=Message("mcp_status_invalid_draft", {"field": "identity"}),
                status_level=STATUS_ERROR,
            )
            return None
        if identity.stable_key() not in self._configs:
            self._set_state(status=Message("mcp_status_need_save"), status_level=STATUS_WARNING)
            return None
        return identity

    def _draft_identity(self) -> MCPServiceIdentity | None:
        """从草稿解析服务身份；非法返回 ``None``（不构造非法对象）。"""

        service_id = self._state.draft.service_id.strip()
        try:
            version = int(self._state.draft.config_version.strip())
        except ValueError:
            return None
        if not service_id or version < 1:
            return None
        return MCPServiceIdentity(service_id=service_id, config_version=version)

    def _is_draft_saved(self) -> bool:
        identity = self._draft_identity()
        return identity is not None and identity.stable_key() in self._configs

    def _build_config(self) -> MCPServerConfig:
        """草稿 → ``MCPServerConfig``；结构不合法时抛 ``ValueError``（值为字段名）。"""

        draft = self._state.draft
        identity = self._draft_identity()
        if identity is None:
            raise ValueError("identity")
        kind = MCPTransportKind(draft.transport_kind)
        is_stdio = kind is MCPTransportKind.STDIO
        transport = MCPTransportConfig(
            kind=kind,
            command=(draft.command.strip() or None) if is_stdio else None,
            args=_split_lines(draft.args_text) if is_stdio else (),
            env=_parse_pairs(draft.env_text, "env") if is_stdio else (),
            cwd=(draft.cwd.strip() or None) if is_stdio else None,
            url=(draft.url.strip() or None) if not is_stdio else None,
            sse_compat_approved=draft.sse_compat_approved,
            headers=_parse_pairs(draft.headers_text, "headers") if not is_stdio else (),
            http_risk_approved=draft.http_risk_approved,
        )
        budget_values = {
            name: _parse_optional_positive_int(raw, f"budget.{name}")
            for name, raw in zip(MCP_BUDGET_FIELDS, draft.budget_values, strict=True)
        }
        tool_scope = _split_lines(draft.audit_tool_scope_text)
        invalidation = _split_lines(draft.audit_invalidation_text)
        fingerprint = draft.audit_implementation_fingerprint.strip()
        evidence = draft.audit_evidence_source.strip()
        reviewed_at = draft.audit_reviewed_at.strip()
        has_audit = bool(tool_scope or fingerprint or evidence or reviewed_at or invalidation)
        audit = (
            MCPReadOnlyAudit(
                service=identity,
                tool_scope=tool_scope,
                implementation_fingerprint=fingerprint or None,
                evidence_source=evidence,
                reviewed_at=reviewed_at,
                invalidation_conditions=invalidation,
            )
            if has_audit
            else None
        )
        return MCPServerConfig(
            identity=identity,
            display_name=draft.display_name.strip(),
            transport=transport,
            budget=MCPBudgetProfile(**budget_values),
            connect_deadline_s=_parse_required_int(draft.connect_deadline_s, "connect_deadline_s"),
            tool_deadline_s=_parse_required_int(draft.tool_deadline_s, "tool_deadline_s"),
            audit=audit,
        )

    def _build_rows(self, configs: tuple[MCPServerConfig, ...]) -> tuple[MCPServiceRow, ...]:
        lifecycle = self._lifecycle
        if lifecycle is None:
            return ()
        return tuple(
            MCPServiceRow(
                identity_key=config.identity.stable_key(),
                service_id=config.identity.service_id,
                config_version=config.identity.config_version,
                display_name=config.display_name,
                transport_kind=config.transport.kind.value,
                connected=lifecycle.is_connected(config.identity),
            )
            for config in configs
        )

    def _draft_from_config(self, config: MCPServerConfig) -> MCPConfigDraft:
        """已保存配置 → 表单草稿（往返一致，不丢字段）。"""

        transport = config.transport
        budget_map = {name: getattr(config.budget, name) for name in MCP_BUDGET_FIELDS}
        audit = config.audit
        return MCPConfigDraft(
            service_id=config.identity.service_id,
            config_version=str(config.identity.config_version),
            display_name=config.display_name,
            transport_kind=transport.kind.value,
            command=transport.command or "",
            args_text="\n".join(transport.args),
            env_text="\n".join(f"{key}={value}" for key, value in transport.env),
            cwd=transport.cwd or "",
            url=transport.url or "",
            headers_text="\n".join(f"{key}={value}" for key, value in transport.headers),
            sse_compat_approved=transport.sse_compat_approved,
            http_risk_approved=transport.http_risk_approved,
            connect_deadline_s=str(config.connect_deadline_s),
            tool_deadline_s=str(config.tool_deadline_s),
            budget_values=tuple(
                "" if budget_map[name] is None else str(budget_map[name]) for name in MCP_BUDGET_FIELDS
            ),
            audit_tool_scope_text="\n".join(audit.tool_scope) if audit is not None else "",
            audit_implementation_fingerprint=(audit.implementation_fingerprint or "") if audit is not None else "",
            audit_evidence_source=audit.evidence_source if audit is not None else "",
            audit_reviewed_at=audit.reviewed_at if audit is not None else "",
            audit_invalidation_text="\n".join(audit.invalidation_conditions) if audit is not None else "",
        )

    async def _refresh_rows(self) -> None:
        """只刷新列表连接状态（不重置表单与工具选择）。"""

        if self._lifecycle is None:
            return
        try:
            configs = await self._lifecycle.list_services()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(exc)
            return
        self._configs = {config.identity.stable_key(): config for config in configs}
        self._set_state(rows=self._build_rows(configs), draft_saved=self._is_draft_saved())

    def _fail(self, exc: BaseException) -> None:
        """失败统一出口：脱敏后进状态，不伪装成功（R21/R9）。"""

        sanitized = _format_error(exc)
        logger.error("[MCPServicesVM] operation failed: %s", sanitized)
        self._set_state(
            busy=False,
            status=Message("mcp_status_error", {"reason": sanitized}),
            status_level=STATUS_ERROR,
        )
