"""显式 MCP 工具调用门（E-02 / 详细设计 F15、HANDOFF §9）。

顺序：参数闭合 → 完整 schema 校验（端口）→ 服务/角色/请求/新鲜许可交集 →
授权 claim（撤销先胜）→ 登记在途 → 单一 deadline 调用 → 结果并入预算 → 配对返回。

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
    """一次显式工具调用请求（工具来自本次连接已发现的目录）。"""

    identity: MCPServiceIdentity
    tool_name: str
    arguments: dict[str, Any]
    catalog: tuple[MCPToolDescriptor, ...]
    role_tools: tuple[str, ...]
    request_tools: tuple[str, ...]
    permission_tools: tuple[str, ...]
    operation_id: str


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
        descriptor = next((item for item in request.catalog if item.name == request.tool_name), None)
        if descriptor is None:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "tool is not in the discovered catalog", label)
        allowed = resolve_tool_allowlist(
            service_tools=(item.name for item in request.catalog),
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
        encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        size = len(encoded.encode("utf-8"))
        guard.single_result_bytes(size)
        guard.retention_bytes(size)
        return ToolCallOutcome(ToolCallState.COMPLETED, result, request.operation_id)
