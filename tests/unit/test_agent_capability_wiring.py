"""I65：ResearchBoundary 生产装配函数与 DAO 策略提供器。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent_types import AgentErrorCode, AgentServiceError, Mode
from services.agent_service.capabilities import ResearchBoundary, evaluate_research_boundary
from services.agent_service.capability_wiring import (
    BOUNDARY_HOST_WIRED,
    DaoResearchBoundaryPolicyProvider,
    ResearchBoundaryRunScope,
    build_research_boundary,
)


class _Dao:
    def __init__(self, active: frozenset[tuple[str, str]], disabled: frozenset[tuple[str, str]] = frozenset()) -> None:
        self.active = active
        self.disabled = disabled

    async def read_boundary_source_keys(self, *, user_scope: str, execution_id: str):
        return self.active, self.disabled


def _provider(dao: _Dao, *, window: int | None = 8000) -> DaoResearchBoundaryPolicyProvider:
    return DaoResearchBoundaryPolicyProvider(
        dao=dao,
        mode=Mode.EXECUTE,
        allowed_tool_names=frozenset({"search"}),
        context_window_tokens=window,
        estimated_input_tokens=100,
    )


def _ctx(user_scope: str = "u1", execution_id: str = "ex-1") -> SimpleNamespace:
    return SimpleNamespace(deps=ResearchBoundaryRunScope(user_scope=user_scope, execution_id=execution_id))


class TestDaoResearchBoundaryPolicyProvider:
    async def test_reads_active_and_disabled_keys_from_dao(self) -> None:
        dao = _Dao(frozenset({("backtest:run-1", "1")}), frozenset({("backtest:run-1", "1")}))
        policy = await _provider(dao)(_ctx())
        assert policy.active_source_keys == frozenset({("backtest:run-1", "1")})
        assert policy.disabled_source_keys == frozenset({("backtest:run-1", "1")})
        assert evaluate_research_boundary(policy, measured_input_tokens=10) == "source_disabled"

    async def test_unknown_window_stays_unknown_and_blocks(self) -> None:
        dao = _Dao(frozenset())
        policy = await _provider(dao, window=None)(_ctx())
        assert policy.context_window_tokens is None
        assert evaluate_research_boundary(policy, measured_input_tokens=10) == "window_unknown"

    async def test_missing_scope_is_rejected(self) -> None:
        dao = _Dao(frozenset())
        with pytest.raises(AgentServiceError) as exc:
            await _provider(dao)(_ctx(user_scope=""))
        assert exc.value.code is AgentErrorCode.POLICY_DENIED

    def test_boundary_assembly_is_hosted_by_leaf_agent(self) -> None:
        boundary = build_research_boundary(_provider(_Dao(frozenset())))
        assert isinstance(boundary, ResearchBoundary)
        assert boundary.id == "research_boundary"
        # 叶 Agent 已消费边界（feat 83d2f3a50）；常量必须与真实装配一致，不得回退为未接线。
        assert BOUNDARY_HOST_WIRED is True
