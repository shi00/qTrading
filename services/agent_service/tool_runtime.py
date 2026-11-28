"""工具 runtime 装配：冻结目录 → 白名单 → 完整 schema 校验 → 授权/派发/结果落库。

职责边界：

- **只装配，不新建第二权限库**：许可仍走既有 ``AgentDao.claim_tool_dispatch``
  （经 :class:`~services.agent_service.tool_grants.DaoToolGrantPort`）；
- **派发仍走唯一调用门** :class:`~services.agent_service.mcp_tools.MCPToolDispatcher`
  （目录/审核范围/schema 漂移、参数摘要冻结、单一 deadline、UNKNOWN 不重试）；
- **决定先落库再 claim**：``save_tool_decision`` 写 ``agent_decision`` +
  ``agent_operation``，使派发认领有可核来源；无新鲜许可即零派发；
- **逐调用人答（P1-10）**：决定只落 ``pending`` + 有界有效期，只有本次答复被
  消费且为批准才激活 ``granted``；启连审核/目录勾选不构成逐调用批准，未答/
  拒答/过期一律零派发；
- **可恢复审批等待（D7-01）**：``pending`` 后由
  :class:`ToolApprovalCoordinator` 按 ``decision_id`` 登记等待本次答复事件，
  受决定有效期与单次工具 deadline 上限约束；答复消费事务（``consume_answer``）
  完成后唤醒等待者，醒后仍以持久 ``agent_decision`` 事实重核（不复用内存
  事件作为授权证据，重启/跨任务恢复均按库内状态判定）；严禁在不可中断 step
  内死循环轮询，``CancelledError`` 原样传播（R2）；
- **恢复后全量重核与幂等防重发**：再次 claim 前由 DAO 在事务内重核有效期、
  执行轮次、参数/授权摘要、撤销状态与 ``agent_operation`` 尚未出站/未入终态
  （CAS）；已认领/已出站的重放只回明未知，不二次外发，也不把已知结果改写成
  失败（R21）；
- **精确错误语义**：超时未答 → ``approval_timeout``、用户明确拒绝 →
  ``permission_rejected``、已授后撤销 → ``permission_revoked``，不得混同为
  ``source_disabled``；
- **研究边界新鲜核定**（I43）：保存决定前读取来源事实并冻结摘要，派发前由调用门
  重新读取比对；已登记来源中存在禁用版本即零决定/零 claim/零派发；
- **结果如实落库**：``completed`` / ``failed`` / ``unknown`` 三态分别映射
  ``phase``/``effect_state``，未知不伪装成失败或成功（R21 / I15）。

生产组合根经 :func:`build_tool_runtime_factory` 接线：只认用户显式启连的单一服务，
未启连 / 无授权 / 目录漂移一律返回 ``None``（叶 Agent 零工具、零外发），
**不会隐式建连**。答复消费经组合根登记 ``AnswerConsumerRegistry``，消费成功后
唤醒同一进程内的审批等待者。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, cast

from pydantic_ai import Tool

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    GrantRevisionRequest,
    MCPServiceIdentity,
    OperationGrantScope,
    SaveToolDecisionRequest,
    SaveToolResultRequest,
    ToolGrantFact,
    ToolGrantState,
    resolve_tool_allowlist,
    sha256_hex,
    stable_id,
)
from data.persistence.daos.base_dao import EngineDisposedError
from services.agent_service.mcp import MCPToolDescriptor, SharedMCPConnectionManager
from services.agent_service.mcp_jsonschema import JsonSchemaToolValidator
from services.agent_service.mcp_lifecycle import MCPServiceLifecycle
from services.agent_service.mcp_tools import (
    MCPToolDispatcher,
    ToolBoundaryPort,
    ToolCallOutcome,
    ToolCallRequest,
    ToolCallState,
    audit_scope_digest,
    canonical_arguments_digest,
)
from services.agent_service.tool_boundary import BoundarySourceReader, DaoToolBoundaryPort
from services.agent_service.tool_grants import DaoToolGrantPort
from services.agent_service.tools import ToolSpec, build_deferred_tools, validate_tool_schema

logger = logging.getLogger(__name__)

__all__ = [
    "APPROVAL_TIMEOUT",
    "PERMISSION_REJECTED",
    "PERMISSION_REVOKED",
    "ToolApprovalCoordinator",
    "ToolApprovalPort",
    "ToolApprovalTicket",
    "ToolDecisionDao",
    "ToolRunScope",
    "ToolRuntime",
    "ToolRuntimeFactory",
    "build_tool_runtime",
    "build_tool_runtime_factory",
]

_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")
#: 单次工具许可的人答窗口（P1-10）：过期即不可回答/不可认领，必须重新发起调用。
#: 值为有界默认（不存在「无限期授权」）；调用方未给期限即拒绝（DAO 强制）。
_TOOL_DECISION_TTL: Final[timedelta] = timedelta(minutes=5)
#: 可折叠为「确定未派发」的调用门拒绝码；其余（审核/预算/引擎）继续传播。
_DENIED_CODES: Final[frozenset[AgentErrorCode]] = frozenset(
    {
        AgentErrorCode.POLICY_DENIED,
        AgentErrorCode.SOURCE_DISABLED,
        AgentErrorCode.INPUT_INVALID,
    }
)
#: 审批闭环的精确拒绝原因（D7-01；不得混同为 ``source_disabled``）。
APPROVAL_TIMEOUT: Final[str] = "approval_timeout"
PERMISSION_REJECTED: Final[str] = "permission_rejected"
PERMISSION_REVOKED: Final[str] = "permission_revoked"
#: 已入终态的 operation phase：重放不得覆盖已知结果/已知失败（R21）。
_TERMINAL_OPERATION_PHASES: Final[frozenset[str]] = frozenset({"result_saved", "failed_confirmed", "unknown"})


class ToolApprovalTicket:
    """一次审批等待的登记句柄：只负责唤醒，不承载授权事实。"""

    def __init__(self, *, decision_id: str) -> None:
        self.decision_id = decision_id
        # 在 prepare() 的运行循环内创建，实例不跨循环复用。
        self._loop = asyncio.get_running_loop()
        self._event = asyncio.Event()  # R11_ALLOWED: 运行循环内创建，实例不跨循环复用

    async def wait(self, timeout_s: float) -> bool:
        """等待答复消费唤醒；超时返回 ``False``，取消原样传播（R2）。"""

        if timeout_s <= 0:
            return False
        try:
            async with asyncio.timeout(timeout_s):
                await self._event.wait()
            return True
        except TimeoutError:
            return False

    def _notify(self) -> None:
        """跨任务/跨线程安全唤醒（DBOS 消费步骤可能不在等待者线程）。"""

        try:
            self._loop.call_soon_threadsafe(self._event.set)
        except RuntimeError:
            # 等待者循环已关闭：等待方已取消/退出，无对象可唤醒。
            return


class ToolApprovalCoordinator:
    """进程内可恢复审批等待登记（D7-01）。

    只做「唤醒」：答复消费事务提交后由组合根调用 :meth:`notify_consumed`，
    被唤醒方必须重新读取持久 ``agent_decision`` 事实，内存事件不构成授权证据。
    """

    def __init__(self) -> None:
        self._waiters: dict[str, list[ToolApprovalTicket]] = {}

    def prepare(self, *, decision_id: str) -> ToolApprovalTicket:
        ticket = ToolApprovalTicket(decision_id=decision_id)
        self._waiters.setdefault(decision_id, []).append(ticket)
        return ticket

    def release(self, ticket: ToolApprovalTicket) -> None:
        remaining = [item for item in self._waiters.get(ticket.decision_id, ()) if item is not ticket]
        if remaining:
            self._waiters[ticket.decision_id] = remaining
        else:
            self._waiters.pop(ticket.decision_id, None)

    def notify_consumed(self, decision_id: str) -> None:
        """答复消费成功后唤醒该决定的所有等待者（幂等，可重复调用）。"""

        for ticket in tuple(self._waiters.get(decision_id, ())):
            ticket._notify()


class ToolApprovalPort(Protocol):
    """审批等待端口（生产为 :class:`ToolApprovalCoordinator`）。"""

    def prepare(self, *, decision_id: str) -> ToolApprovalTicket: ...

    def release(self, ticket: ToolApprovalTicket) -> None: ...

    def notify_consumed(self, decision_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _PermissionState:
    """持久许可事实的解析结果（``denied_reason`` 为空且未出站才允许派发）。"""

    scope: OperationGrantScope | None
    denied_reason: str | None = None
    already_started: bool = False


@dataclass(frozen=True, slots=True)
class ToolRunScope:
    """一次叶运行的工具归属作用域（决定/operation/结果全部绑定这组身份）。"""

    user_scope: str
    session_id: str
    execution_id: str
    round_id: str
    node_id: str


class ToolDecisionDao(Protocol):
    """工具 runtime 所需的最小 DAO 面（生产为 ``AgentDao``）。"""

    async def save_tool_decision(self, request: SaveToolDecisionRequest) -> ToolGrantFact: ...

    async def save_tool_result(self, request: SaveToolResultRequest) -> None: ...

    async def read_operation_grant_scope(self, *, operation_id: str) -> OperationGrantScope | None: ...

    async def claim_tool_dispatch(self, request: GrantRevisionRequest) -> ToolGrantFact: ...


class ToolRuntimeDao(ToolDecisionDao, BoundarySourceReader, Protocol):
    """工具 runtime 工厂所需的完整 DAO 面：决定/结果/授权反查 + 研究边界来源事实。"""


@dataclass(frozen=True, slots=True)
class ToolRuntime:
    """已装配的工具集与执行端口（实现 ``ToolExecutorPort``）。"""

    tools: tuple[Tool[Any], ...]
    dispatcher: MCPToolDispatcher
    catalog: tuple[MCPToolDescriptor, ...]
    connection_generation: int
    identity: MCPServiceIdentity
    scope: ToolRunScope
    role_tools: tuple[str, ...]
    request_tools: tuple[str, ...]
    permission_tools: tuple[str, ...]
    service_tools: tuple[str, ...]
    audit_scope: tuple[str, ...]
    dao: ToolDecisionDao
    boundary: ToolBoundaryPort
    #: 审批等待端口；``None`` = 无可恢复等待能力（PENDING 直接按超时零派发）。
    approvals: ToolApprovalPort | None = None
    #: 单次审批等待上限（另受决定 ``expires_at`` 约束；生产取单次工具 deadline）。
    approval_timeout_s: float = _TOOL_DECISION_TTL.total_seconds()

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
    ) -> ToolCallOutcome:
        """决定 → claim → 派发 → 结果落库；任一步不可证即零派发/如实失败。"""

        operation_id = _operation_id(self.scope, tool_call_id)
        decision_id = _decision_id(self.scope, tool_call_id)
        try:
            arguments_digest = canonical_arguments_digest(arguments)
        except (TypeError, ValueError, RecursionError):
            return ToolCallOutcome(
                ToolCallState.FAILED,
                {"state": "failed", "error": "invalid_arguments"},
                operation_id,
            )
        descriptor = next((item for item in self.catalog if item.name == tool_name), None)
        if descriptor is None:
            return ToolCallOutcome(
                ToolCallState.FAILED,
                {"state": "denied", "reason": AgentErrorCode.INPUT_INVALID.value},
                operation_id,
            )
        # I43：保存决定前冻结研究边界事实。来源已禁用即零决定/零 claim/零派发；
        # 事实不可得（DAO 故障）原样传播，不以「读不到」当作「无禁用来源」。
        attestation = await self.boundary.attest()
        if attestation.blocked_reason is not None:
            return ToolCallOutcome(
                ToolCallState.FAILED,
                {"state": "denied", "reason": AgentErrorCode.SOURCE_DISABLED.value},
                operation_id,
            )
        try:
            fact = await self.dao.save_tool_decision(
                SaveToolDecisionRequest(
                    user_scope=self.scope.user_scope,
                    session_id=self.scope.session_id,
                    execution_id=self.scope.execution_id,
                    round_id=self.scope.round_id,
                    node_id=self.scope.node_id,
                    decision_id=decision_id,
                    operation_id=operation_id,
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    parameter_digest=arguments_digest,
                    schema_digest=sha256_hex(descriptor.schema_json),
                    audit_scope_digest=audit_scope_digest(self.audit_scope),
                    question_payload=_question_payload(self.identity, tool_name, arguments_digest, attestation.digest),
                    # P1-10：决定只落 pending 且有界有效期；本决定由本次人答激活，
                    # 启连审核/目录勾选不构成逐调用批准。
                    expires_at=_wire_moment(datetime.now(UTC) + _TOOL_DECISION_TTL),
                )
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentServiceError as exc:
            if exc.code in _DENIED_CODES:
                return ToolCallOutcome(
                    ToolCallState.FAILED,
                    {"state": "denied", "reason": exc.code.value},
                    operation_id,
                )
            raise

        # D7-01：pending 时等待本次 decision_id 的答复事件；醒后（或无可恢复等待
        # 能力时）一律以持久许可事实重核，精确区分超时/拒绝/撤销/已出站重放。
        permission = await self._resolve_permission(decision_id=decision_id, operation_id=operation_id, fact=fact)
        if permission.already_started:
            return await self._already_started(operation_id, permission.scope)
        if permission.denied_reason is not None:
            return await self._deny(operation_id, permission.denied_reason, scope=permission.scope)

        request = ToolCallRequest(
            identity=self.identity,
            tool_name=tool_name,
            arguments=arguments,
            arguments_digest=arguments_digest,
            catalog=self.catalog,
            role_tools=self.role_tools,
            request_tools=self.request_tools,
            permission_tools=self.permission_tools,
            operation_id=operation_id,
            connection_generation=self.connection_generation,
            boundary_digest=attestation.digest,
        )
        try:
            outcome = await self.dispatcher.call(request)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentServiceError as exc:
            if exc.code in _DENIED_CODES:
                # claim 竞争/撤销/过期等由调用门折叠为拒绝码；按持久事实还原
                # 精确原因，不把许可问题混同为 source_disabled（R21）。
                refusal = await self._classify_dispatch_refusal(operation_id)
                if refusal is not None:
                    if refusal.already_started:
                        return await self._already_started(operation_id, refusal.scope)
                    if refusal.denied_reason is not None:
                        return await self._deny(operation_id, refusal.denied_reason, scope=refusal.scope)
                kicker = {"state": "denied", "reason": exc.code.value}
                await self._record(operation_id, ToolCallState.FAILED, kicker)
                return ToolCallOutcome(ToolCallState.FAILED, kicker, operation_id)
            if exc.code is AgentErrorCode.BUDGET_BLOCKED:
                # 结果族超限时调用已发生：结果不可用按 unknown 记录，不伪装确定失败。
                await self._record(operation_id, ToolCallState.UNKNOWN, {"state": "unknown"})
            raise
        await self._record(operation_id, outcome.state, outcome.result)
        return outcome

    async def _resolve_permission(
        self, *, decision_id: str, operation_id: str, fact: ToolGrantFact
    ) -> _PermissionState:
        """读取持久许可事实；pending 先等待答复，醒后仍以库内状态为准。"""

        scope = await self.dao.read_operation_grant_scope(operation_id=operation_id)
        state = scope.grant_state if scope is not None else fact.grant_state
        if state is ToolGrantState.PENDING:
            await self._await_answer(decision_id=decision_id, operation_id=operation_id, scope=scope)
            scope = await self.dao.read_operation_grant_scope(operation_id=operation_id)
            state = scope.grant_state if scope is not None else ToolGrantState.PENDING
        if state is ToolGrantState.GRANTED:
            if _decision_expired(scope):
                return _PermissionState(scope=scope, denied_reason=APPROVAL_TIMEOUT)
            if scope is not None and _operation_started(scope):
                return _PermissionState(scope=scope, already_started=True)
            return _PermissionState(scope=scope)
        if state is ToolGrantState.REVOKED:
            return _PermissionState(scope=scope, denied_reason=_revocation_reason(scope))
        if state is ToolGrantState.CLAIMED:
            return _PermissionState(scope=scope, already_started=True)
        # 等待结束仍 pending：本次未答/不可解释，按超时零派发。
        return _PermissionState(scope=scope, denied_reason=APPROVAL_TIMEOUT)

    async def _await_answer(self, *, decision_id: str, operation_id: str, scope: OperationGrantScope | None) -> None:
        """登记并等待本次答复事件（单次有界等待，不做不可中断轮询）。"""

        if self.approvals is None:
            return
        timeout_s = self._approval_remaining(scope)
        if timeout_s <= 0:
            return
        ticket = self.approvals.prepare(decision_id=decision_id)
        try:
            # 可恢复：注册后立即复核持久事实，防「答复在注册前已消费」的丢唤醒。
            latest = await self.dao.read_operation_grant_scope(operation_id=operation_id)
            if latest is not None and latest.grant_state is not ToolGrantState.PENDING:
                return
            timeout_s = self._approval_remaining(latest if latest is not None else scope, fallback=timeout_s)
            if timeout_s <= 0:
                return
            await ticket.wait(timeout_s)
        finally:
            self.approvals.release(ticket)

    def _approval_remaining(self, scope: OperationGrantScope | None, *, fallback: float | None = None) -> float:
        limit = self.approval_timeout_s if fallback is None else min(self.approval_timeout_s, fallback)
        if scope is not None:
            expires_at = _parse_wire_moment(scope.expires_at)
            if expires_at is not None:
                limit = min(limit, (expires_at - datetime.now(UTC)).total_seconds())
        return max(0.0, limit)

    async def _classify_dispatch_refusal(self, operation_id: str) -> _PermissionState | None:
        """claim 被拒后按持久事实还原原因；``None`` = 与许可状态无关的拒绝。"""

        scope = await self.dao.read_operation_grant_scope(operation_id=operation_id)
        if scope is None:
            return None
        if scope.grant_state is ToolGrantState.REVOKED:
            return _PermissionState(scope=scope, denied_reason=_revocation_reason(scope))
        if scope.grant_state is ToolGrantState.CLAIMED:
            return _PermissionState(scope=scope, already_started=True)
        if scope.grant_state is ToolGrantState.PENDING:
            return _PermissionState(scope=scope, denied_reason=APPROVAL_TIMEOUT)
        if scope.grant_state is ToolGrantState.GRANTED:
            if _decision_expired(scope):
                return _PermissionState(scope=scope, denied_reason=APPROVAL_TIMEOUT)
            if _operation_started(scope):
                return _PermissionState(scope=scope, already_started=True)
        return None

    async def _deny(self, operation_id: str, reason: str, *, scope: OperationGrantScope | None) -> ToolCallOutcome:
        payload = {"state": "denied", "reason": reason}
        if not _operation_terminal(scope):
            await self._record(operation_id, ToolCallState.FAILED, payload)
        return ToolCallOutcome(ToolCallState.FAILED, payload, operation_id)

    async def _already_started(self, operation_id: str, scope: OperationGrantScope | None) -> ToolCallOutcome:
        """已认领/已出站的重放：不二次外发，也不写/覆盖 operation 结果（R21）。

        认领可能属于仍在途的原执行者；此处回明未知，由原执行者落终态，避免并发
        重放把对方的 ``prepared``/``result_saved`` 改写成 ``unknown``。
        """

        return ToolCallOutcome(ToolCallState.UNKNOWN, None, operation_id)

    async def _record(self, operation_id: str, state: ToolCallState, payload: Any) -> None:
        """落库结果；保存未确认即明确失败，不发布成果。"""

        try:
            await self.dao.save_tool_result(
                SaveToolResultRequest(
                    user_scope=self.scope.user_scope,
                    operation_id=operation_id,
                    outcome_state=state.value,
                    result_payload=_canonical_text(payload) if payload is not None else None,
                    error_code=None if state is ToolCallState.COMPLETED else "tool_call_" + state.value,
                )
            )
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except Exception as exc:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "tool_result_not_saved",
                f"{self.scope.user_scope}/{operation_id}",
            ) from exc


#: 叶运行期构造工具 runtime 的工厂；``None`` = 本轮零工具（无 MCP 管理器）。
ToolRuntimeFactory = Callable[[ToolRunScope], ToolRuntime | None]


def _wire_moment(moment: datetime) -> str:
    """UTC 微秒 Z 时刻文本（与 DAO wire 格式一致）。"""

    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_wire_moment(value: str | None) -> datetime | None:
    """解析 DAO wire 时刻；不可解析返回 ``None``（不得当作无限期，R21）。"""

    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _operation_started(scope: OperationGrantScope) -> bool:
    """operation 是否可能已出站（phase/effect/io 任一离开初始态）。"""

    if scope.operation_phase is not None and scope.operation_phase != "prepared":
        return True
    if scope.effect_state is not None and scope.effect_state != "not_dispatched":
        return True
    return scope.io_state is not None and scope.io_state != "not_started"


def _operation_terminal(scope: OperationGrantScope | None) -> bool:
    """operation 是否已入终态（终态不得被重放覆盖，R21）。"""

    return scope is not None and scope.operation_phase in _TERMINAL_OPERATION_PHASES


def _decision_expired(scope: OperationGrantScope | None) -> bool:
    """决定有效期是否已过；不可解析按未过期处理（由 DAO 事务内再次拒绝，R21）。"""

    if scope is None:
        return False
    expires_at = _parse_wire_moment(scope.expires_at)
    return expires_at is not None and expires_at <= datetime.now(UTC)


def _revocation_reason(scope: OperationGrantScope | None) -> str:
    """撤销原因：仅 ``approval is False`` 才是用户明确拒绝，其余按已授后撤销。"""

    if scope is not None and scope.approval is False:
        return PERMISSION_REJECTED
    return PERMISSION_REVOKED


def _canonical_text(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID, "tool result is not canonical json", "tool_runtime"
        ) from exc


def _question_payload(identity: MCPServiceIdentity, tool_name: str, arguments_digest: str, boundary_digest: str) -> str:
    """决定问题载荷（人可读 + 摘要）；不含参数原文，避免把调用细节写入问题文本。

    ``service_id`` / ``config_version`` 是持久化的「研究引用了该 MCP 配置版」证据：
    断开/更新共享配置前由 DAO 据此核对仍未终结的使用者（I59）。``boundary_digest``
    是授权时冻结的研究边界事实摘要（I43），派发前由调用门新鲜重核比对。
    """

    return _canonical_text(
        {
            "question": f"是否允许本次调用工具 {tool_name}？",
            "tool_name": tool_name,
            "parameter_digest": arguments_digest,
            "boundary_digest": boundary_digest,
            "service_id": identity.service_id,
            "config_version": identity.config_version,
        }
    )


def _operation_id(scope: ToolRunScope, tool_call_id: str) -> str:
    return stable_id("tool_operation", [scope.user_scope, scope.execution_id, scope.round_id, tool_call_id])


def _decision_id(scope: ToolRunScope, tool_call_id: str) -> str:
    return stable_id("tool_decision", [scope.user_scope, scope.execution_id, scope.round_id, tool_call_id])


def _descriptor_schema(descriptor: MCPToolDescriptor) -> Mapping[str, Any] | None:
    try:
        schema = json.loads(descriptor.schema_json)
    except (TypeError, ValueError):
        logger.warning("[ToolRuntime] tool schema is not json; tool not installed")
        return None
    if not isinstance(schema, Mapping):
        logger.warning("[ToolRuntime] tool schema is not an object; tool not installed")
        return None
    return schema


def _specs_from_catalog(catalog: tuple[MCPToolDescriptor, ...], *, allowed: frozenset[str]) -> tuple[ToolSpec, ...]:
    """逐条校验；非法 schema 的工具不装（不降低校验、不补默认字段）。"""

    specs: list[ToolSpec] = []
    seen: set[str] = set()
    for descriptor in catalog:
        if descriptor.name not in allowed:
            continue
        if descriptor.name in seen:
            raise AgentValidationError("duplicate_tool_name", "$.mcp.catalog")
        seen.add(descriptor.name)
        schema = _descriptor_schema(descriptor)
        if schema is None:
            continue
        description = schema.get("description")
        spec = ToolSpec(
            name=descriptor.name,
            description=description
            if isinstance(description, str) and description.strip()
            else f"MCP tool {descriptor.name}",
            json_schema=schema,
        )
        try:
            validate_tool_schema(spec)
        except AgentValidationError:
            logger.warning("[ToolRuntime] tool schema is incomplete; tool not installed: %s", descriptor.name)
            continue
        specs.append(spec)
    return tuple(specs)


def build_tool_runtime_factory(
    *, dao: ToolRuntimeDao, lifecycle: MCPServiceLifecycle, approvals: ToolApprovalPort | None = None
) -> ToolRuntimeFactory:
    """构造逐叶工具工厂：只认显式启连的单一服务，否则零工具（**绝不自动启连**）。

    - 无已连接服务 / 多个已连接（无法确定归属）/ 无启连授权 / 目录摘要漂移
      → 返回 ``None``（叶运行零工具）；
    - 其余走 :func:`build_tool_runtime`：冻结目录 ∩ 授权集合 → 完整 schema 校验。
    - ``approvals`` 为组合根共享的审批等待登记（答复消费侧须调用同一实例的
      :meth:`ToolApprovalCoordinator.notify_consumed`）。
    """

    def _factory(scope: ToolRunScope) -> ToolRuntime | None:
        identity = lifecycle.active_identity()
        if identity is None or not lifecycle.is_connected(identity):
            return None
        sets = lifecycle.tool_sets(identity)
        if sets is None:
            return None
        return build_tool_runtime(
            dao=dao,
            manager=lifecycle.manager,
            identity=identity,
            scope=scope,
            role_tools=sets.role_tools,
            request_tools=sets.request_tools,
            permission_tools=sets.permission_tools,
            boundary=DaoToolBoundaryPort(dao=dao, user_scope=scope.user_scope, execution_id=scope.execution_id),
            approvals=approvals,
        )

    return _factory


def _approval_timeout_ceiling(manager: SharedMCPConnectionManager, identity: MCPServiceIdentity) -> float:
    """审批等待上限：不超出单次工具 deadline，也不超出决定 TTL。"""

    ceiling = _TOOL_DECISION_TTL.total_seconds()
    deadline_reader = getattr(manager, "tool_deadline_s", None)
    if callable(deadline_reader):
        ceiling = min(ceiling, float(cast(float, deadline_reader(identity))))
    return max(0.0, ceiling)


def build_tool_runtime(
    *,
    dao: ToolDecisionDao,
    manager: SharedMCPConnectionManager,
    identity: MCPServiceIdentity,
    scope: ToolRunScope,
    role_tools: Iterable[str],
    request_tools: Iterable[str],
    permission_tools: Iterable[str],
    boundary: ToolBoundaryPort,
    approvals: ToolApprovalPort | None = None,
) -> ToolRuntime:
    """冻结目录 + 允许集 → 校验/装工具；空允许集返回零工具（合法形态）。"""

    catalog = manager.frozen_catalog(identity)
    service_tools = tuple(sorted(descriptor.name for descriptor in catalog))
    allowed = frozenset(
        resolve_tool_allowlist(
            service_tools=service_tools,
            role_tools=role_tools,
            request_tools=request_tools,
            permission_tools=permission_tools,
        )
    )
    specs = _specs_from_catalog(catalog, allowed=allowed)
    dispatcher = MCPToolDispatcher(
        manager=manager,
        validator=JsonSchemaToolValidator(),
        grants=DaoToolGrantPort(dao),
        boundary=boundary,
    )
    runtime = ToolRuntime(
        tools=build_deferred_tools(specs),
        dispatcher=dispatcher,
        catalog=catalog,
        connection_generation=manager.connection_generation(identity),
        identity=identity,
        scope=scope,
        role_tools=tuple(role_tools),
        request_tools=tuple(request_tools),
        permission_tools=tuple(permission_tools),
        service_tools=service_tools,
        audit_scope=manager.audit_tool_scope(identity),
        dao=dao,
        boundary=boundary,
        approvals=approvals,
        approval_timeout_s=_approval_timeout_ceiling(manager, identity),
    )
    return runtime
