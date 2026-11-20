"""研究边界能力（``ResearchBoundary``）的生产装配点（I65）。

现状（必须如实保留）：

- 本模块提供**装配函数**与从 DAO 读取来源键的生产策略提供器；
- 但生产叶主体仍是 ``leaf_runtime.unavailable_leaf_agent``（恒 ``failed``），
  没有 pydantic-ai ``Agent`` 构造点，故 ``ResearchBoundary`` 暂无生产**消费方**；
- ``BOUNDARY_HOST_WIRED`` 固定为 ``False``，任何「边界已生效」的结论不得据此宣称。

本模块只做装配与事实读取，不授权（授权仍在派发点按最新事实核定）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Protocol

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from services.agent_service.capabilities import ResearchBoundary, ResearchBoundaryPolicy

__all__ = [
    "BOUNDARY_HOST_WIRED",
    "DaoResearchBoundaryPolicyProvider",
    "ResearchBoundaryRunScope",
    "build_research_boundary",
]

#: 边界能力是否已有生产宿主（叶 Agent）消费；当前为 ``False``（见模块说明）。
BOUNDARY_HOST_WIRED: Final[bool] = False


@dataclass(frozen=True, slots=True)
class ResearchBoundaryRunScope:
    """一次叶运行的边界作用域（由未来叶 Agent 装配作为 ``RunContext.deps`` 注入）。"""

    user_scope: str
    execution_id: str


class _BoundarySourceReader(Protocol):
    async def read_boundary_source_keys(
        self, *, user_scope: str, execution_id: str
    ) -> tuple[frozenset[tuple[str, str]], frozenset[tuple[str, str]]]: ...


@dataclass(frozen=True, slots=True)
class DaoResearchBoundaryPolicyProvider:
    """按运行作用域从 DAO 读取活跃/禁用来源键，产出边界策略。

    ``mode`` / 工具名 / 窗口由装配侧冻结注入；来源键每次请求新鲜读取（不禁用
    缓存假象）。窗口未知（``None``）原样保留，由能力拒绝放行（R21）。
    """

    dao: _BoundarySourceReader
    mode: Mode
    allowed_tool_names: frozenset[str]
    context_window_tokens: int | None
    estimated_input_tokens: int | None = None
    candidate_context_windows: tuple[int, ...] = ()

    async def __call__(self, ctx: Any) -> ResearchBoundaryPolicy:
        deps = getattr(ctx, "deps", None)
        user_scope = getattr(deps, "user_scope", None)
        execution_id = getattr(deps, "execution_id", None)
        if not isinstance(user_scope, str) or not user_scope or not isinstance(execution_id, str) or not execution_id:
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "research boundary scope unavailable; refusing to guess active sources",
                "research_boundary",
            )
        active, disabled = await self.dao.read_boundary_source_keys(user_scope=user_scope, execution_id=execution_id)
        return ResearchBoundaryPolicy(
            mode=self.mode,
            allowed_tool_names=self.allowed_tool_names,
            active_source_keys=active,
            disabled_source_keys=disabled,
            context_window_tokens=self.context_window_tokens,
            estimated_input_tokens=self.estimated_input_tokens,
            candidate_context_windows=self.candidate_context_windows,
        )


def build_research_boundary(provider: DaoResearchBoundaryPolicyProvider) -> ResearchBoundary:
    """未来叶 Agent 构造处的唯一边界装配入口（当前无生产调用方）。"""

    return ResearchBoundary(policy_provider=provider)
