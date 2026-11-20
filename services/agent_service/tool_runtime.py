"""工具 runtime 装配：冻结目录 → 白名单 → 完整 schema 校验 → 授权/派发/结果落库。

职责边界：

- **只装配，不新建第二权限库**：许可仍走既有 ``AgentDao.claim_tool_dispatch``
  （经 :class:`~services.agent_service.tool_grants.DaoToolGrantPort`）；
- **派发仍走唯一调用门** :class:`~services.agent_service.mcp_tools.MCPToolDispatcher`
  （目录/审核范围/schema 漂移、参数摘要冻结、单一 deadline、UNKNOWN 不重试）；
- **决定先落库再 claim**：``save_tool_decision`` 写 ``agent_decision`` +
  ``agent_operation``，使派发认领有可核来源；无新鲜许可即零派发；
- **结果如实落库**：``completed`` / ``failed`` / ``unknown`` 三态分别映射
  ``phase``/``effect_state``，未知不伪装成失败或成功（R21 / I15）。

生产组合根经 :func:`build_tool_runtime_factory` 接线：只认用户显式启连的单一服务，
未启连 / 无授权 / 目录漂移一律返回 ``None``（叶 Agent 零工具、零外发），
**不会隐式建连**。人工答复 UI 闭环同样不在本轮。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

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
    ToolCallOutcome,
    ToolCallRequest,
    ToolCallState,
    audit_scope_digest,
    canonical_arguments_digest,
)
from services.agent_service.tool_grants import DaoToolGrantPort
from services.agent_service.tools import ToolSpec, build_deferred_tools, validate_tool_schema

logger = logging.getLogger(__name__)

__all__ = [
    "ToolDecisionDao",
    "ToolRunScope",
    "ToolRuntime",
    "ToolRuntimeFactory",
    "build_tool_runtime",
    "build_tool_runtime_factory",
]

_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")
#: 可折叠为「确定未派发」的调用门拒绝码；其余（审核/预算/引擎）继续传播。
_DENIED_CODES: Final[frozenset[AgentErrorCode]] = frozenset(
    {
        AgentErrorCode.POLICY_DENIED,
        AgentErrorCode.SOURCE_DISABLED,
        AgentErrorCode.INPUT_INVALID,
    }
)


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
        try:
            await self.dao.save_tool_decision(
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
                    question_payload=_question_payload(self.identity, tool_name, arguments_digest),
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
        )
        try:
            outcome = await self.dispatcher.call(request)
        except asyncio.CancelledError:
            raise
        except EngineDisposedError:
            raise
        except AgentServiceError as exc:
            if exc.code in _DENIED_CODES:
                await self._record(operation_id, ToolCallState.FAILED, {"state": "denied", "reason": exc.code.value})
                return ToolCallOutcome(
                    ToolCallState.FAILED,
                    {"state": "denied", "reason": exc.code.value},
                    operation_id,
                )
            if exc.code is AgentErrorCode.BUDGET_BLOCKED:
                # 结果族超限时调用已发生：结果不可用按 unknown 记录，不伪装确定失败。
                await self._record(operation_id, ToolCallState.UNKNOWN, {"state": "unknown"})
            raise
        await self._record(operation_id, outcome.state, outcome.result)
        return outcome

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


def _canonical_text(payload: Any) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise AgentServiceError(
            AgentErrorCode.INPUT_INVALID, "tool result is not canonical json", "tool_runtime"
        ) from exc


def _question_payload(identity: MCPServiceIdentity, tool_name: str, arguments_digest: str) -> str:
    """决定问题载荷（人可读 + 摘要）；不含参数原文，避免把调用细节写入问题文本。

    ``service_id`` / ``config_version`` 是持久化的「研究引用了该 MCP 配置版」证据：
    断开/更新共享配置前由 DAO 据此核对仍未终结的使用者（I59）。
    """

    return _canonical_text(
        {
            "question": f"是否允许本次调用工具 {tool_name}？",
            "tool_name": tool_name,
            "parameter_digest": arguments_digest,
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


def build_tool_runtime_factory(*, dao: ToolDecisionDao, lifecycle: MCPServiceLifecycle) -> ToolRuntimeFactory:
    """构造逐叶工具工厂：只认显式启连的单一服务，否则零工具（**绝不自动启连**）。

    - 无已连接服务 / 多个已连接（无法确定归属）/ 无启连授权 / 目录摘要漂移
      → 返回 ``None``（叶运行零工具）；
    - 其余走 :func:`build_tool_runtime`：冻结目录 ∩ 授权集合 → 完整 schema 校验。
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
        )

    return _factory


def build_tool_runtime(
    *,
    dao: ToolDecisionDao,
    manager: SharedMCPConnectionManager,
    identity: MCPServiceIdentity,
    scope: ToolRunScope,
    role_tools: Iterable[str],
    request_tools: Iterable[str],
    permission_tools: Iterable[str],
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
    )
    return runtime
