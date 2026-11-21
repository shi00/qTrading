"""研究边界能力（``ResearchBoundary``）的生产装配点（I65）。

生产消费方：:func:`services.agent_service.leaf_agent.run_leaf_agent` 构造
pydantic-ai ``Agent`` 时把 :func:`build_leaf_boundary` 的产物装入 ``capabilities``，
在每次模型请求前按最新来源事实与上下文窗口判定（DD §10.3）。

本模块只做装配与事实读取，不授权（授权仍在派发点按最新事实核定）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from core.model_management_types import ExecutionSnapshot
from services.agent_service.capabilities import ResearchBoundary, ResearchBoundaryPolicy

__all__ = [
    "BOUNDARY_HOST_WIRED",
    "DaoResearchBoundaryPolicyProvider",
    "ResearchBoundaryRunScope",
    "build_leaf_boundary",
    "build_research_boundary",
    "resolve_leaf_context_window",
]

#: 边界能力是否已有生产宿主（叶 Agent）消费；生产宿主见模块说明。
BOUNDARY_HOST_WIRED: Final[bool] = True


@dataclass(frozen=True, slots=True)
class ResearchBoundaryRunScope:
    """一次叶运行的边界作用域（叶 Agent 装配作为 ``RunContext.deps`` 注入）。"""

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
    """叶 Agent 构造处的唯一边界装配入口。"""

    return ResearchBoundary(policy_provider=provider)


def resolve_leaf_context_window(snapshot: ExecutionSnapshot, *, scenario_id: str) -> int | None:
    """解析叶运行所用首候选连接的上下文窗口（D9）。

    路径：``routes[scenario_id].candidates[0].connection_id`` →
    ``connections[connection_id].context_window``。路由/候选/连接/窗口任一缺失或
    非法 → ``None``：由能力按「窗口未知拒绝放行」处理（R21），不猜默认窗口。
    """

    if not isinstance(scenario_id, str) or not scenario_id:
        return None
    route = next(
        (item for item in snapshot.routes if isinstance(item, Mapping) and item.get("scenario_id") == scenario_id),
        None,
    )
    if route is None:
        return None
    candidates = route.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)) or not candidates:
        return None
    first = candidates[0]
    if not isinstance(first, Mapping):
        return None
    connection_id = first.get("connection_id")
    if not isinstance(connection_id, str) or not connection_id:
        return None
    connection = next(
        (
            item
            for item in snapshot.connections
            if isinstance(item, Mapping) and item.get("connection_id") == connection_id
        ),
        None,
    )
    if connection is None:
        return None
    window = connection.get("context_window")
    if isinstance(window, bool) or not isinstance(window, int) or window < 1:
        return None
    return window


def build_leaf_boundary(
    dao: _BoundarySourceReader,
    *,
    snapshot: ExecutionSnapshot,
    scenario_id: str,
) -> ResearchBoundary:
    """叶 Agent 的生产边界：执行模式 + 空工具集 + 首候选连接窗口（D9）。"""

    provider = DaoResearchBoundaryPolicyProvider(
        dao=dao,
        mode=Mode.EXECUTE,
        allowed_tool_names=frozenset(),
        context_window_tokens=resolve_leaf_context_window(snapshot, scenario_id=scenario_id),
        estimated_input_tokens=None,
    )
    return build_research_boundary(provider)
