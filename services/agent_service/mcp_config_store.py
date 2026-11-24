"""MCP 服务配置的 ``app_state`` 持久化适配（实现 :class:`MCPConfigStorePort`）。

- 复用既有配置域 ``app_state``（键值 UPSERT），**不新建 Agent 表、不新增迁移**；
- 本模块不 import ``data``：读写由组合根注入（``get_app_state`` / ``set_app_state`` /
  ``list_app_state`` 绑定共享引擎后的偏函数），保持 services 层可独立测试；
- 保存失败向上抛（保存必须可确认）；读取无值返回 ``None``（由管理器按
  ``CONFIG_UNAVAILABLE`` 拒绝），载荷损坏抛 ``AgentServiceError``，不伪装成「未配置」。
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from core.agent_types import AgentErrorCode, AgentServiceError, AgentValidationError, MCPServiceIdentity
from services.agent_service.mcp import (
    MCPConfigStorePort,
    MCPServerConfig,
    validate_mcp_server_config,
    validate_transport_config,
)
from services.agent_service.mcp_config_codec import decode_mcp_server_config, encode_mcp_server_config

__all__ = ["AppStateMCPConfigStore", "MCPConfigListEntry"]

_JSON_SEPARATORS = (",", ":")

#: 列表项的失效原因（稳定原因码；界面据此标注，不把不可用伪装成可用）。
LISTING_PATH_UNAVAILABLE = "path_unavailable"
LISTING_INVALID = "invalid"
LISTING_UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class MCPConfigListEntry:
    """一条已保存配置的列表项：可解码配置 + 失效原因；单条失败不牵连其它记录。

    ``identity_key`` 取自存储键（记录原始身份锚点）；``config`` 为 ``None`` 表示
    载荷连结构都无法解码（``problem=LISTING_UNREADABLE``），该记录无法在界面修复，
    但仍计入列表结果以免被静默丢弃（R21）。
    """

    identity_key: str
    config: MCPServerConfig | None = None
    problem: str | None = None


class AppStateMCPConfigStore(MCPConfigStorePort):
    """按稳定身份读写的 JSON 配置存储（键前缀 ``agent.mcp.service.``）。"""

    PREFIX = "agent.mcp.service."

    def __init__(
        self,
        *,
        read: Callable[[str], Awaitable[str | None]],
        write: Callable[[str, str], Awaitable[None]],
        scan: Callable[[str], Awaitable[Mapping[str, str]]] | None = None,
    ) -> None:
        self._read = read
        self._write = write
        self._scan = scan

    @classmethod
    def key_for(cls, identity: MCPServiceIdentity) -> str:
        return f"{cls.PREFIX}{identity.stable_key()}"

    async def save(self, config: MCPServerConfig) -> None:
        """规范 JSON 覆盖写；失败传播（不吞异常）。"""

        payload = encode_mcp_server_config(config)
        text = json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True)
        await self._write(self.key_for(config.identity), text)

    async def load(self, identity: MCPServiceIdentity) -> MCPServerConfig | None:
        """读取并解码；无值 ``None``，载荷损坏明确失败（不伪装未配置）。"""

        text = await self._read(self.key_for(identity))
        if text is None:
            return None
        try:
            raw: Any = json.loads(text)
            config = decode_mcp_server_config(raw)
        except (TypeError, ValueError) as exc:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp config payload is not decodable",
                identity.stable_key(),
            ) from exc
        if config.identity != identity:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp config identity mismatch",
                identity.stable_key(),
            )
        return config

    async def list_configs(self) -> tuple[MCPConfigListEntry, ...]:
        """逐行列出已保存配置（按显示名/身份排序）；扫描端口不可用时显式拒绝。

        单条记录损坏或程序/工作目录已不存在只影响该行：可解码的记录照常返回并用
        ``problem`` 标注原因（供界面标为路径失效并允许载入修复），连结构都无法解码
        的记录以 ``config=None`` 返回并标注 ``unreadable``，不被静默丢弃（R21）。
        列表容忍不代表使用点放行：``load`` 仍 fail-closed（解码含准入校验）。
        """

        if self._scan is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp config listing is not wired",
                self.PREFIX,
            )
        values = await self._scan(self.PREFIX)
        entries = [self._list_entry(key, text) for key, text in values.items()]
        return tuple(
            sorted(
                entries,
                key=lambda item: (item.config.display_name if item.config is not None else "", item.identity_key),
            )
        )

    def _list_entry(self, key: str, text: str) -> MCPConfigListEntry:
        """单条记录 → 列表项：结构解码失败/准入失败都只降级本行。"""

        identity_key = key[len(self.PREFIX) :] if key.startswith(self.PREFIX) else key
        try:
            config = decode_mcp_server_config(json.loads(text), validate_admission=False)
        except (TypeError, ValueError):
            return MCPConfigListEntry(identity_key=identity_key, problem=LISTING_UNREADABLE)
        try:
            validate_mcp_server_config(config)
        except AgentValidationError:
            return MCPConfigListEntry(identity_key, config, self._listing_problem(config))
        return MCPConfigListEntry(identity_key, config)

    @staticmethod
    def _listing_problem(config: MCPServerConfig) -> str:
        """准入失败的原因码：仅程序/工作目录不可用算「路径失效」，其余为校验不通过。

        两者都可载入修复，但标注必须如实：语义校验失败（如 URL 缺失、风险未确认）
        不应被渲染成「路径失效」。目标字段取自校验异常的 ``field_path`` 叶子。
        """

        try:
            validate_transport_config(config.transport)
        except AgentValidationError as exc:
            # ``field_path`` 形如 ``$.mcp.transport.command: <原因>``：取目标字段名判类。
            field = exc.field_path.partition(":")[0].rstrip().rsplit(".", 1)[-1]
            return LISTING_PATH_UNAVAILABLE if field in {"command", "cwd"} else LISTING_INVALID
        return LISTING_INVALID
