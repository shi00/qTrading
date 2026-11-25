"""比较路明确拒绝原因的 ``app_state`` 持久化适配（N29）。

- 复用既有配置域 ``app_state``（键值 UPSERT），**不新建 Agent 表、不新增迁移**；
- 本模块不 import ``data``：读写/扫描由组合根按共享引擎注入，保持 services 层可
  独立测试；
- 每条原因一个键（``agent.comparison.lane_note.<group_ref>.<lane_ref>``），同组多路
  各写各的键，无需读-改-写，避免并发覆盖；
- 扫描失败向上抛（读取失败不得伪装成「没有拒绝原因」，R21）；载荷不可解码同样
  明确失败，不把损坏值当成正常原因。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from core.agent_types import AgentErrorCode, AgentServiceError
from services.agent_service.comparison import ACCEPT_FAILED_PREFIX, DISPATCH_FAILED_PREFIX

__all__ = ["AppStateComparisonLaneNotes", "ComparisonLaneNotesPort"]

#: 可持久化的原因前缀：只有明确拒绝（受理失败/零派发）才落库，排队与未知不落。
_REFUSAL_PREFIXES = (DISPATCH_FAILED_PREFIX, ACCEPT_FAILED_PREFIX)


class ComparisonLaneNotesPort(Protocol):
    """比较路明确拒绝原因的持久化端口（读写均零派发、零模型调用）。"""

    async def load(self, group_ref: str) -> Mapping[str, str]:
        """读本组全部已持久化原因：``lane_ref -> note``；无记录返回空映射。"""
        ...

    async def save(self, group_ref: str, lane_ref: str, note: str) -> None:
        """覆盖写本路原因；写失败由调用方决定降级或传播。"""
        ...


class AppStateComparisonLaneNotes:
    """按组/路稳定身份读写的 ``app_state`` 原因记录。"""

    PREFIX = "agent.comparison.lane_note."

    def __init__(
        self,
        *,
        write: Callable[[str, str], Awaitable[None]],
        scan: Callable[[str], Awaitable[Mapping[str, str]]],
    ) -> None:
        self._write = write
        self._scan = scan

    @classmethod
    def key_for(cls, group_ref: str, lane_ref: str) -> str:
        return f"{cls.PREFIX}{group_ref}.{lane_ref}"

    async def load(self, group_ref: str) -> Mapping[str, str]:
        prefix = f"{self.PREFIX}{group_ref}."
        values = await self._scan(prefix)
        notes: dict[str, str] = {}
        for key, value in values.items():
            lane_ref = key[len(prefix) :]
            if not lane_ref or not value.startswith(_REFUSAL_PREFIXES):
                raise AgentServiceError(
                    AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                    "comparison lane note is not decodable",
                    key,
                )
            notes[lane_ref] = value
        return notes

    async def save(self, group_ref: str, lane_ref: str, note: str) -> None:
        if not note.startswith(_REFUSAL_PREFIXES):
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "comparison lane note is not a refusal reason",
                self.key_for(group_ref, lane_ref),
            )
        await self._write(self.key_for(group_ref, lane_ref), note)
