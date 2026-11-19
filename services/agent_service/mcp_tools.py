"""显式 MCP 工具调用门（E-02 / 详细设计 F15、HANDOFF §9）。

顺序：连接代次/冻结目录/schema/只读审核范围核验 → 参数规范快照与摘要绑定 →
完整 schema 校验（端口）→ 服务/角色/请求/新鲜许可交集 → 授权 claim（撤销先胜，
携带 schema/参数/审核范围摘要）→ 登记在途 → 单一 deadline 调用 → 结果并入预算 →
配对返回。

参数在**校验前**即被冻结为规范 JSON 快照（键序无关、经序列化深拷贝、与调用方
对象彻底脱离），校验与发送只使用该快照：``ToolCallRequest.arguments_digest`` 记录
授权时的参数摘要，门内按实际参数重算比对，不符即零 claim/零执行。claim 之后的
``await``（claim/预算/发送门）期间调用方改动原可变对象不影响到出站参数——只发送
已被校验且被 claim 核准的原快照值。选择「仅送原值」而非「拒绝」：await 后重新
读取调用方持有的可变对象本身是 TOCTOU（读到与发送之间仍可再改，且无法判定改动
发生在校验前后），快照是唯一可确定的「已核参数 == 出站参数」证据。

目录、审核范围与 schema 以连接 owner 启连时冻结的发现结果为准，请求自报仅用于比对：
换目录、审核外工具、schema 漂移、旧代次、参数摘要不符一律在 claim 前拒绝
（零预留/零执行/零外发）。审核证据同样不是一次性结论：每次调用前重核（重新加载
配置 + 重新观测实现指纹），证据失效即零 claim/零外发；remote 传输的远端状态不可
观测，结果如实标注信任限制（``ToolCallOutcome.audit_trust_limited``），不宣称已验证（I06）。

派发后超时/断线/协议解码失败/服务端错误响应均无确证，记 ``UNKNOWN`` 且不自动重试；
只有端口在出站前显式拒绝（``MCPCallNotDispatched``）才判 ``FAILED``（效果确定未发生）。
取消继续传播并释放归属；``UNKNOWN`` 的在途归属则保留到所属会话真实退出被确认后才
释放（不立即释放占用、不放开重连重发，I53）。
真实 jsonschema 适配器与每 send 目的地门仍 needs-spike，不在本轮宣称已验证。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPServiceIdentity,
    resolve_tool_allowlist,
    sha256_hex,
)
from services.agent_service.mcp import (
    MCPCallNotDispatched,
    MCPClientSessionPort,
    MCPToolDescriptor,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_budget import MCPBudgetExceeded, MCPBudgetGuard, measure_json_bytes

__all__ = [
    "MCPBudgetExceeded",
    "MCPToolDispatcher",
    "ToolArgumentValidatorPort",
    "ToolCallOutcome",
    "ToolCallRequest",
    "ToolCallState",
    "ToolDispatchVerdict",
    "ToolGrantPort",
    "canonical_arguments_digest",
]


class ToolDispatchVerdict(Enum):
    """派发权仲裁（与 C-04 单胜者约定）：撤销先胜 / 已认领 / 闭包未知。"""

    CLAIMED = "claimed"
    REVOKED = "revoked"
    UNKNOWN = "unknown"


class ToolCallState(Enum):
    """调用结果状态：明确成功 / 明确失败 / 无确证未知（不重试）。"""

    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_JSON_SEPARATORS = (",", ":")


def _canonical_json(value: Any) -> str:
    """规范 JSON 文本：键序排序、无空白、拒绝 NaN/Infinity 与不可 JSON 化对象。"""

    return json.dumps(value, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True, allow_nan=False)


def canonical_arguments_digest(arguments: Mapping[str, Any]) -> str:
    """参数规范摘要（键序无关）：与调用门冻结快照使用同一算法。

    请求构建侧用它派生 ``ToolCallRequest.arguments_digest``（授权时记录的参数摘要）。
    非 JSON 原生类型、非有限浮点、超深结构抛 ``TypeError``/``ValueError``/``RecursionError``
    ——调用门同样拒绝这类参数，故不必为其生成摘要。
    """

    return sha256_hex(_canonical_json(dict(arguments)))


def _scope_digest(scope: tuple[str, ...]) -> str:
    """审核工具范围摘要：排序后的规范 JSON（与目录摘要同源思路，名称集合语义）。"""

    return sha256_hex(_canonical_json(sorted(scope)))


class ToolArgumentValidatorPort(Protocol):
    """完整 jsonschema 校验端口；参数不闭合必须抛 ``AgentValidationError``。"""

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None: ...


class ToolGrantPort(Protocol):
    """新鲜许可 claim 端口（生产接 C-04 单胜者仲裁，不建第二权限库）。

    实现必须把 ``schema_digest`` / ``arguments_digest`` / ``audit_scope_digest``
    与已授权决定记录比对：任一不符即返回 ``REVOKED``，不得放行。
    """

    async def claim(
        self,
        *,
        identity: MCPServiceIdentity,
        tool_name: str,
        operation_id: str,
        schema_digest: str,
        arguments_digest: str,
        audit_scope_digest: str,
    ) -> ToolDispatchVerdict: ...


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """一次显式工具调用请求：``catalog``/``connection_generation`` 必须能对上连接冻结态。

    ``arguments`` 可以是调用方持有的可变对象：调用门在校验前自行冻结规范快照，
    此后不再读取该对象，校验与发送只用快照。``arguments_digest`` 为授权时记录的
    参数规范摘要（:func:`canonical_arguments_digest` 派生），仅用于与门内重算值比对。
    """

    identity: MCPServiceIdentity
    tool_name: str
    arguments: dict[str, Any]
    arguments_digest: str
    catalog: tuple[MCPToolDescriptor, ...]
    role_tools: tuple[str, ...]
    request_tools: tuple[str, ...]
    permission_tools: tuple[str, ...]
    operation_id: str
    #: 请求构建时的连接代次；与当前连接冻结代次不符即拒绝（旧目录不可复用）。
    connection_generation: int


@dataclass(frozen=True, slots=True)
class ToolCallOutcome:
    """调用结果：UNKNOWN 时保留原 operation 身份，禁止自动重发。"""

    state: ToolCallState
    result: dict[str, Any] | None
    operation_id: str
    #: 授权依据仅覆盖本机可观测项（remote 传输的远端目标状态未被观测）：
    #: 为真时不得把本次调用呈现为「审核证据已验证」（I06）。
    audit_trust_limited: bool = False


class MCPToolDispatcher:
    """共享连接上的显式工具调用门。"""

    def __init__(
        self,
        *,
        manager: SharedMCPConnectionManager,
        validator: ToolArgumentValidatorPort,
        grants: ToolGrantPort,
    ) -> None:
        self._manager = manager
        self._validator = validator
        self._grants = grants

    async def call(self, request: ToolCallRequest) -> ToolCallOutcome:
        label = request.identity.stable_key()
        # I06：审核证据不是一次性结论。每次调用前重核（配置重新准入 + 重新观测实现指纹），
        # 未准入即在 claim/登记在途/发送之前拒绝，确保证据失效后零新调用。
        evidence = await self._manager.recheck_audit_evidence(request.identity)
        if not evidence.admitted:
            raise AgentServiceError(
                AgentErrorCode.CAPABILITY_UNVERIFIED,
                f"read-only audit refused: {evidence.reason}",
                label,
            )
        frozen = self._manager.frozen_catalog(request.identity)
        if request.connection_generation != self._manager.connection_generation(request.identity):
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool catalog generation is stale", label)
        frozen_names = tuple(sorted(item.name for item in frozen))
        claimed_names = tuple(sorted(item.name for item in request.catalog))
        if claimed_names != frozen_names:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED, "tool catalog differs from the frozen connection catalog", label
            )
        frozen_schema = {item.name: item.schema_json for item in frozen}
        if any(item.schema_json != frozen_schema[item.name] for item in request.catalog):
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED, "tool schema drifted from the frozen discovery", label
            )
        descriptor = next((item for item in frozen if item.name == request.tool_name), None)
        if descriptor is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "tool is not in the frozen connection catalog", label)
        scope = self._manager.audit_tool_scope(request.identity)
        if request.tool_name not in scope:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool is outside the reviewed read-only scope", label)
        allowed = resolve_tool_allowlist(
            service_tools=frozen_names,
            role_tools=request.role_tools,
            request_tools=request.request_tools,
            permission_tools=request.permission_tools,
        )
        if request.tool_name not in allowed:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool is not allowed for this request", label)
        # 校验前冻结：之后不再读取 request.arguments（调用方持有的可变对象）。
        arguments_digest, arguments = self._freeze_arguments(request.arguments, label)
        if request.arguments_digest != arguments_digest:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "tool arguments digest does not match the authorized operation",
                label,
            )
        try:
            self._validator.validate(descriptor.schema_json, arguments)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"tool arguments are not closed: {exc.code}", label
            ) from exc

        verdict = await self._grants.claim(
            identity=request.identity,
            tool_name=request.tool_name,
            operation_id=request.operation_id,
            schema_digest=sha256_hex(descriptor.schema_json),
            arguments_digest=arguments_digest,
            audit_scope_digest=_scope_digest(scope),
        )
        if verdict is ToolDispatchVerdict.REVOKED:
            raise AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "tool permission was revoked", label)
        if verdict is ToolDispatchVerdict.UNKNOWN:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool permission closure is unknown", label)
        if not self._still_frozen(arguments, arguments_digest):
            # 校验端口不应改动快照；一旦改动即「校验 A、发送 B」，零外发。
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool arguments changed after validation", label)

        session = self._manager.session(request.identity)
        deadline_s = self._manager.tool_deadline_s(request.identity)
        guard = self._manager.budget_guard(request.identity)
        self._manager.register_in_flight(request.identity, request.operation_id)
        try:
            outcome = await self._invoke(session, request, arguments, guard, deadline_s)
        except BaseException:
            # 取消/异常继续传播（R2）：调用方已放弃本次等待，归属随取消一起释放。
            self._manager.release_in_flight(request.identity, request.operation_id)
            raise
        if outcome.state is ToolCallState.UNKNOWN:
            # 后效未知：不得立即释放归属，也不得让断开/重连把原 operation 视为已结束。
            self._manager.mark_in_flight_unknown(request.identity, request.operation_id)
        else:
            self._manager.release_in_flight(request.identity, request.operation_id)
        return ToolCallOutcome(
            state=outcome.state,
            result=outcome.result,
            operation_id=outcome.operation_id,
            audit_trust_limited=evidence.trust_limited,
        )

    @staticmethod
    def _freeze_arguments(arguments: Any, label: str) -> tuple[str, dict[str, Any]]:
        """冻结参数：返回（规范摘要, 经序列化深拷贝且与调用方对象脱离的快照）。"""

        if not isinstance(arguments, dict):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "tool arguments must be a json object", label)
        try:
            canonical = _canonical_json(arguments)
            snapshot = json.loads(canonical)
        except (TypeError, ValueError, RecursionError) as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, "tool arguments are not canonical json", label
            ) from exc
        return sha256_hex(canonical), snapshot

    @staticmethod
    def _still_frozen(snapshot: dict[str, Any], arguments_digest: str) -> bool:
        """发送前复核快照未被校验端口改动；不可规范化的快照同样视为已改动。"""

        try:
            return sha256_hex(_canonical_json(snapshot)) == arguments_digest
        except (TypeError, ValueError, RecursionError):
            return False

    async def _invoke(
        self,
        session: MCPClientSessionPort,
        request: ToolCallRequest,
        arguments: dict[str, Any],
        guard: MCPBudgetGuard,
        deadline_s: int,
    ) -> ToolCallOutcome:
        try:
            # 单一总 deadline 由本层强制：端口不守时限时在 await 点切断（同步阻塞仍须端口自律）。
            async with asyncio.timeout(deadline_s):
                result = await session.call_tool(
                    request.tool_name,
                    arguments,
                    deadline_s=deadline_s,
                )
        except asyncio.CancelledError:
            raise
        except MCPCallNotDispatched:
            # 出站前即被拒绝（会话已失效 / 发送前 deadline 已过）：效果确定未发生，判 FAILED（可安全重试）。
            return ToolCallOutcome(ToolCallState.FAILED, None, request.operation_id)
        except (TimeoutError, ConnectionError):
            # 已派发但无确证：unknown，原 operation 不自动重发。
            return ToolCallOutcome(ToolCallState.UNKNOWN, None, request.operation_id)
        except Exception:
            # 已派发后的协议/解码失败、服务端错误响应等：均不构成「确定未执行」的证据，
            # 一律保持 unknown 并保留原 operation 身份，避免调用方把「后效未知」当作「确定失败」重发（I15）。
            return ToolCallOutcome(ToolCallState.UNKNOWN, None, request.operation_id)
        try:
            # 单条结果族按单条比较：以本族上限为增量计数阈值，超限即中止，
            # 不为比较而物化完整结果文本（I56）。
            size = measure_json_bytes(result, limit=guard.limit("single_result_bytes"))
        except (TypeError, ValueError, RecursionError):
            # 效果已发生但结果无法进入类型化契约（二进制/自定义对象/孤立代理项/超深结构）：无确证，不重试。
            return ToolCallOutcome(ToolCallState.UNKNOWN, None, request.operation_id)
        if size is None:
            guard.exceed("single_result_bytes")
        guard.single_result_bytes(size)
        if size > guard.remaining("retention_bytes"):
            guard.exceed("retention_bytes")
        guard.retention_bytes(size)
        return ToolCallOutcome(ToolCallState.COMPLETED, result, request.operation_id)
