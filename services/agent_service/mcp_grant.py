"""启连授权记录（用户显式核准的工具集）与 ``app_state`` 持久化适配。

语义：

- 授权绑定「服务身份 + 配置版本」（``MCPServiceIdentity``）与启连时冻结的
  **发现目录摘要**：schema/目录变化后旧勾选不得沿用（摘要不一致即要求重新核准）；
- 空工具集合法（= 启连但零工具）；
- 本模块不 import ``data``：读写由组合根注入（同 :mod:`mcp_config_store`）。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    MCPServiceIdentity,
    decode_mcp_service_identity,
    encode_mcp_service_identity,
    is_digest,
    require_identity,
    require_utc_timestamp,
)

__all__ = ["AppStateMCPGrantStore", "MCPConnectGrant", "MCPToolSets"]

_JSON_SEPARATORS = (",", ":")
_GRANT_KEYS = frozenset({"identity", "tools", "catalog_digest", "approved_at"})


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], path: str) -> None:
    if set(value) != expected:
        raise AgentValidationError("invalid_mcp_grant", f"{path}: keys mismatch")


@dataclass(frozen=True, slots=True)
class MCPConnectGrant:
    """一次显式启连的用户核准：启用工具子集 + 冻结目录摘要 + 核准时间。"""

    identity: MCPServiceIdentity
    tools: tuple[str, ...]
    catalog_digest: str
    approved_at: str

    def __post_init__(self) -> None:
        self.identity.stable_key()
        for name in self.tools:
            require_identity(name, "$.mcp.grant.tools")
        if not is_digest(self.catalog_digest):
            raise AgentValidationError("invalid_mcp_grant", "$.mcp.grant.catalog_digest: must be a sha256 digest")
        require_utc_timestamp(self.approved_at, "$.mcp.grant.approved_at")

    def encode(self) -> dict[str, Any]:
        return {
            "identity": encode_mcp_service_identity(self.identity),
            "tools": list(self.tools),
            "catalog_digest": self.catalog_digest,
            "approved_at": self.approved_at,
        }

    @classmethod
    def decode(cls, value: object, path: str = "$.mcp.grant") -> MCPConnectGrant:
        if not isinstance(value, dict):
            raise AgentValidationError("invalid_mcp_grant", f"{path}: must be an object")
        _require_exact_keys(value, _GRANT_KEYS, path)
        raw_tools = value["tools"]
        if not isinstance(raw_tools, list) or any(not isinstance(item, str) for item in raw_tools):
            raise AgentValidationError("invalid_mcp_grant", f"{path}.tools: must be a list of strings")
        digest = value["catalog_digest"]
        approved_at = value["approved_at"]
        if not isinstance(digest, str) or not isinstance(approved_at, str):
            raise AgentValidationError("invalid_mcp_grant", f"{path}: digest/timestamp must be strings")
        return cls(
            identity=decode_mcp_service_identity(value["identity"], f"{path}.identity"),
            tools=tuple(raw_tools),
            catalog_digest=digest,
            approved_at=approved_at,
        )


@dataclass(frozen=True, slots=True)
class MCPToolSets:
    """一次叶运行的工具三集合。

    # NOTE(lazy): 单用户桌面版把「用户启连核准的工具集」同时作为角色/请求/许可集合.
    # ceiling: 无法表达角色级差异、请求级临时授权与逐次许可收紧（三者当前恒等）。
    # upgrade: 角色定义（E-03）或请求级工具授权落地时，改为三个独立来源并分别持久化.
    """

    role_tools: tuple[str, ...]
    request_tools: tuple[str, ...]
    permission_tools: tuple[str, ...]

    @classmethod
    def from_grant(cls, grant: MCPConnectGrant) -> MCPToolSets:
        return cls(
            role_tools=grant.tools,
            request_tools=grant.tools,
            permission_tools=grant.tools,
        )


class AppStateMCPGrantStore:
    """按稳定身份读写的授权存储（键前缀 ``agent.mcp.grant.``）。"""

    PREFIX = "agent.mcp.grant."

    def __init__(
        self,
        *,
        read: Callable[[str], Awaitable[str | None]],
        write: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._read = read
        self._write = write

    @classmethod
    def key_for(cls, identity: MCPServiceIdentity) -> str:
        return f"{cls.PREFIX}{identity.stable_key()}"

    async def save(self, grant: MCPConnectGrant) -> None:
        text = json.dumps(grant.encode(), ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True)
        await self._write(self.key_for(grant.identity), text)

    async def load(self, identity: MCPServiceIdentity) -> MCPConnectGrant | None:
        text = await self._read(self.key_for(identity))
        if text is None:
            return None
        try:
            grant = MCPConnectGrant.decode(json.loads(text))
        except (TypeError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp connect grant is not decodable",
                identity.stable_key(),
            ) from exc
        if grant.identity != identity:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp connect grant identity mismatch",
                identity.stable_key(),
            )
        return grant
