"""显式 MCP 工具调用门（E-02 / 详细设计 F15、HANDOFF §9）。

顺序：连接代次/冻结目录/schema/只读审核范围核验 → 参数闭合 → 完整 schema 校验（端口）→
服务/角色/请求/新鲜许可交集 → 授权 claim（撤销先胜）→ 登记在途 → 单一 deadline 调用 →
结果并入预算 → 配对返回。

目录、审核范围与 schema 以连接 owner 启连时冻结的发现结果为准，请求自报仅用于比对：
换目录、审核外工具、schema 漂移、旧代次一律在 claim 前拒绝（零预留/零执行/零外发）。

派发后超时/断线无确证记 ``UNKNOWN`` 且不自动重试；取消继续传播并释放归属。
真实 jsonschema 适配器与每 send 目的地门仍 needs-spike，不在本轮宣称已验证。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPServiceIdentity,
    resolve_tool_allowlist,
)
from services.agent_service.mcp import (
    MCPClientSessionPort,
    MCPToolDescriptor,
    SharedMCPConnectionManager,
)
from services.agent_service.mcp_budget import MCPBudgetExceeded, MCPBudgetGuard

__all__ = [
    "MCPBudgetExceeded",
    "MCPToolDispatcher",
    "ToolArgumentValidatorPort",
    "ToolCallOutcome",
    "ToolCallRequest",
    "ToolCallState",
    "ToolDispatchVerdict",
    "ToolGrantPort",
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


class ToolArgumentValidatorPort(Protocol):
    """完整 jsonschema 校验端口；参数不闭合必须抛 ``AgentValidationError``。"""

    def validate(self, schema_json: str, arguments: dict[str, Any]) -> None: ...


class ToolGrantPort(Protocol):
    """新鲜许可 claim 端口（生产接 C-04 单胜者仲裁，不建第二权限库）。"""

    async def claim(
        self, *, identity: MCPServiceIdentity, tool_name: str, operation_id: str
    ) -> ToolDispatchVerdict: ...


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """一次显式工具调用请求：``catalog``/``connection_generation`` 必须能对上连接冻结态。"""

    identity: MCPServiceIdentity
    tool_name: str
    arguments: dict[str, Any]
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
        if request.tool_name not in self._manager.audit_tool_scope(request.identity):
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool is outside the reviewed read-only scope", label)
        allowed = resolve_tool_allowlist(
            service_tools=frozen_names,
            role_tools=request.role_tools,
            request_tools=request.request_tools,
            permission_tools=request.permission_tools,
        )
        if request.tool_name not in allowed:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool is not allowed for this request", label)
        try:
            self._validator.validate(descriptor.schema_json, request.arguments)
        except AgentValidationError as exc:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID, f"tool arguments are not closed: {exc.code}", label
            ) from exc

        verdict = await self._grants.claim(
            identity=request.identity,
            tool_name=request.tool_name,
            operation_id=request.operation_id,
        )
        if verdict is ToolDispatchVerdict.REVOKED:
            raise AgentServiceError(AgentErrorCode.SOURCE_DISABLED, "tool permission was revoked", label)
        if verdict is ToolDispatchVerdict.UNKNOWN:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "tool permission closure is unknown", label)

        session = self._manager.session(request.identity)
        deadline_s = self._manager.tool_deadline_s(request.identity)
        guard = self._manager.budget_guard(request.identity)
        self._manager.register_in_flight(request.identity, request.operation_id)
        try:
            return await self._invoke(session, request, guard, deadline_s)
        finally:
            self._manager.release_in_flight(request.identity, request.operation_id)

    async def _invoke(
        self,
        session: MCPClientSessionPort,
        request: ToolCallRequest,
        guard: MCPBudgetGuard,
        deadline_s: int,
    ) -> ToolCallOutcome:
        try:
            # 单一总 deadline 由本层强制：端口不守时限时在 await 点切断（同步阻塞仍须端口自律）。
            async with asyncio.timeout(deadline_s):
                result = await session.call_tool(
                    request.tool_name,
                    dict(request.arguments),
                    deadline_s=deadline_s,
                )
        except asyncio.CancelledError:
            raise
        except (TimeoutError, ConnectionError):
            # 已派发但无确证：unknown，原 operation 不自动重发。
            return ToolCallOutcome(ToolCallState.UNKNOWN, None, request.operation_id)
        except Exception:
            return ToolCallOutcome(ToolCallState.FAILED, None, request.operation_id)
        try:
            encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            size = len(encoded.encode("utf-8"))
        except (TypeError, ValueError, RecursionError):
            # 效果已发生但结果无法进入类型化契约（二进制/自定义对象/孤立代理项/超深结构）：无确证，不重试。
            return ToolCallOutcome(ToolCallState.UNKNOWN, None, request.operation_id)
        guard.single_result_bytes(size)
        guard.retention_bytes(size)
        return ToolCallOutcome(ToolCallState.COMPLETED, result, request.operation_id)
