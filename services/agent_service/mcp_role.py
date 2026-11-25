"""角色版本的 MCP 服务分配（可用集合 / 默认服务 / 工具白名单）与 ``app_state`` 持久化。

- 复用既有配置域 ``app_state``（键前缀 ``agent.mcp.role.``），**不新建 Agent 表**；
- 本模块不 import ``data``：读写由组合根注入（同 :mod:`mcp_config_store`）；
- 保存前的一致性校验由生命周期门面与服务配置联合执行：默认值必须属于可用集合，
  白名单工具必须存在于该服务**声明**的只读工具范围内（``MCPReadOnlyAudit.tool_scope``）；
- 读取无值返回 ``None``，载荷损坏显式失败（不伪装「未配置」，R21）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    RoleMCPAssignment,
    decode_role_mcp_assignment,
)

__all__ = ["AppStateMCPRoleAssignmentStore", "validate_assignment_tools_against_configs"]

_JSON_SEPARATORS = (",", ":")


def validate_assignment_tools_against_configs(
    assignment: RoleMCPAssignment,
    declared_tools: Mapping[str, tuple[str, ...]],
) -> None:
    """校验白名单工具落在各服务**声明**的工具范围内。

    ``declared_tools`` 以 ``identity.stable_key()`` 为键、值为该服务声明的只读工具
    范围；服务未配置（键缺失）即拒绝（不把「查不到」当作「无限制」，R21）。
    """

    for binding in assignment.available:
        key = binding.identity.stable_key()
        declared = declared_tools.get(key)
        if declared is None:
            raise AgentValidationError("mcp_service_not_configured", f"$.role.mcp.service[{key}]")
        outside = tuple(sorted(name for name in binding.tools if name not in declared))
        if outside:
            raise AgentValidationError(
                "mcp_tool_outside_declared_scope",
                f"$.role.mcp.service[{key}].tools: {','.join(outside)}",
            )


class AppStateMCPRoleAssignmentStore:
    """按角色身份读写的分配存储（键前缀 ``agent.mcp.role.``）。"""

    PREFIX = "agent.mcp.role."

    def __init__(
        self,
        *,
        read: Callable[[str], Awaitable[str | None]],
        write: Callable[[str, str], Awaitable[None]],
        scan: Callable[[str], Awaitable[Mapping[str, str]]],
        delete: Callable[[str], Awaitable[bool]] | None = None,
    ) -> None:
        self._read = read
        self._write = write
        self._scan = scan
        self._delete = delete

    @classmethod
    def key_for(cls, role_id: str, role_version: str) -> str:
        return f"{cls.PREFIX}{role_id}@{role_version}"

    async def save(self, assignment: RoleMCPAssignment) -> None:
        """规范 JSON 覆盖写（同一角色版本仅一条；内容变化即覆盖）。"""

        text = json.dumps(assignment.wire(), ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True)
        await self._write(self.key_for(assignment.role_id, assignment.role_version), text)

    async def load(self, role_id: str, role_version: str) -> RoleMCPAssignment | None:
        """读取并解码；无值 ``None``，载荷损坏明确失败（不伪装未配置）。"""

        text = await self._read(self.key_for(role_id, role_version))
        if text is None:
            return None
        return self._decode(text, f"{role_id}@{role_version}")

    async def list_assignments(self) -> tuple[RoleMCPAssignment, ...]:
        """列出全部角色分配（按角色身份排序）；扫描读失败向上抛出。"""

        values = await self._scan(self.PREFIX)
        assignments = [self._decode(text, key) for key, text in values.items()]
        return tuple(sorted(assignments, key=lambda item: (item.role_id, item.role_version)))

    async def delete(self, role_id: str, role_version: str) -> bool:
        """删除角色分配；未接线删除端口即显式拒绝。"""

        if self._delete is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp role assignment deletion is not wired",
                f"{role_id}@{role_version}",
            )
        return await self._delete(self.key_for(role_id, role_version))

    @staticmethod
    def _decode(text: str, label: str) -> RoleMCPAssignment:
        try:
            return decode_role_mcp_assignment(json.loads(text))
        except (TypeError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp role assignment is not decodable",
                label,
            ) from exc
