"""研究边界在 MCP 工具派发点的生产适配（I43）。

能力类 :class:`~services.agent_service.capabilities.ResearchBoundary` 只在**模型请求前**
判定模式/来源撤用/窗口；工具派发发生在其后，来源可能在两段之间被撤用。本模块把同一份
来源事实（``AgentDao.read_boundary_source_keys``）接入调用门：

- 授权时（保存工具决定前）冻结来源事实摘要；
- 派发前由调用门新鲜重核，摘要不符即拒绝旧许可，来源撤用即零派发。

只读：不推断未登记来源，不修改任何状态。事实不可得时异常原样传播（fail-closed），
不把「读不到」当作「无禁用来源」。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from core.agent_types import sha256_hex
from services.agent_service.mcp_tools import ToolBoundaryAttestation

__all__ = [
    "BLOCKED_REASON_SOURCE_DISABLED",
    "BoundarySourceReader",
    "DaoToolBoundaryPort",
    "boundary_sources_digest",
]

#: 边界阻断原因：已登记来源中存在当前已禁用的版本（与能力层同名，便于按 stable 文本核对）。
BLOCKED_REASON_SOURCE_DISABLED = "source_disabled"

_JSON_SEPARATORS = (",", ":")

SourceKey = tuple[str, str]


class BoundarySourceReader(Protocol):
    """研究边界所需的来源事实读取面（生产为 ``AgentDao``）。"""

    async def read_boundary_source_keys(
        self, *, user_scope: str, execution_id: str
    ) -> tuple[frozenset[SourceKey], frozenset[SourceKey]]: ...


def boundary_sources_digest(active: frozenset[SourceKey], disabled: frozenset[SourceKey]) -> str:
    """来源事实摘要：活跃/禁用键集合（排序后）的规范 JSON 摘要（键序无关）。"""

    return sha256_hex(
        json.dumps(
            {"active": sorted(active), "disabled": sorted(disabled)},
            ensure_ascii=False,
            separators=_JSON_SEPARATORS,
            sort_keys=True,
        )
    )


@dataclass(frozen=True, slots=True)
class DaoToolBoundaryPort:
    """按运行作用域从 DAO 新鲜读取来源状态的研究边界端口（生产实现）。"""

    dao: BoundarySourceReader
    user_scope: str
    execution_id: str

    async def attest(self) -> ToolBoundaryAttestation:
        active, disabled = await self.dao.read_boundary_source_keys(
            user_scope=self.user_scope,
            execution_id=self.execution_id,
        )
        return ToolBoundaryAttestation(
            digest=boundary_sources_digest(active, disabled),
            blocked_reason=BLOCKED_REASON_SOURCE_DISABLED if active & disabled else None,
        )
