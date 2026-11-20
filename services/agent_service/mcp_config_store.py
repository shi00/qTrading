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
from typing import Any

from core.agent_types import AgentErrorCode, AgentServiceError, MCPServiceIdentity
from services.agent_service.mcp import MCPConfigStorePort, MCPServerConfig
from services.agent_service.mcp_config_codec import decode_mcp_server_config, encode_mcp_server_config

__all__ = ["AppStateMCPConfigStore"]

_JSON_SEPARATORS = (",", ":")


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

    async def list_configs(self) -> tuple[MCPServerConfig, ...]:
        """列出已保存配置（按显示名/身份排序）；扫描端口不可用时显式拒绝。"""

        if self._scan is None:
            raise AgentServiceError(
                AgentErrorCode.CONFIG_UNAVAILABLE,
                "mcp config listing is not wired",
                self.PREFIX,
            )
        values = await self._scan(self.PREFIX)
        configs = [decode_mcp_server_config(json.loads(text)) for text in values.values()]
        return tuple(sorted(configs, key=lambda item: (item.display_name, item.identity.stable_key())))
