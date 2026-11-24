"""叶边界生产装配（I65）：窗口解析 + 执行模式空工具集 + 宿主已接线事实。

- 窗口缺省取 ``agent_research`` 首候选连接的 ``context_window``；传入真实授权连接
  时取该连接，并把其余候选窗口一并交给边界取最严者；路由/候选/连接/窗口任一缺失或
  非法 → ``None``（由能力拒绝放行，不猜默认窗口，R21）；
- 窗口未知时 ``evaluate_research_boundary`` 明确返回 ``window_unknown``；
- 叶边界为模型输出预留 ``OUTPUT_RESERVE_TOKENS``（输入+预留与窗口比较）；
- ``BOUNDARY_HOST_WIRED`` 如实为 ``True``（生产消费方 = ``leaf_agent``）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

from core.agent_types import Mode
from core.model_management_types import ExecutionSnapshot
from services.agent_service.capabilities import ResearchBoundary, evaluate_research_boundary
from services.agent_service.capability_wiring import (
    BOUNDARY_HOST_WIRED,
    build_leaf_boundary,
    resolve_leaf_context_window,
    resolve_leaf_context_windows,
    resolve_leaf_tokenizer_model,
)
from services.agent_service.leaf_agent import LeafAgentDeps
from services.ai_service.token_budget import OUTPUT_RESERVE_TOKENS

pytestmark = pytest.mark.unit


def _snapshot(
    *,
    routes: tuple[Any, ...] | None = None,
    connections: tuple[Any, ...] | None = None,
) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=1,
        routes=routes
        if routes is not None
        else ({"scenario_id": "agent_research", "candidates": ({"connection_id": "conn-1"},)},),
        connections=connections if connections is not None else ({"connection_id": "conn-1", "context_window": 8192},),
        business_contract_versions={},
        security_scope_version=1,
        created_at="2026-11-20T00:00:00.000000Z",
    )


class _Dao:
    def __init__(self) -> None:
        self.calls = 0

    async def read_boundary_source_keys(self, *, user_scope: str, execution_id: str) -> Any:
        self.calls += 1
        return frozenset(), frozenset()


def _deps() -> LeafAgentDeps:
    return LeafAgentDeps(
        user_scope="user-1",
        execution_id="execution-1",
        session_id="session-1",
        round_id="round-1",
        node_id="n1",
    )


class TestResolveLeafContextWindow:
    def test_first_candidate_connection_window(self) -> None:
        assert resolve_leaf_context_window(_snapshot(), scenario_id="agent_research") == 8192

    def test_route_missing_returns_none(self) -> None:
        assert resolve_leaf_context_window(_snapshot(routes=()), scenario_id="agent_research") is None

    def test_connection_missing_returns_none(self) -> None:
        assert resolve_leaf_context_window(_snapshot(connections=()), scenario_id="agent_research") is None

    def test_window_unknown_returns_none(self) -> None:
        snapshot = _snapshot(connections=({"connection_id": "conn-1", "context_window": None},))
        assert resolve_leaf_context_window(snapshot, scenario_id="agent_research") is None

    def test_blank_scenario_id_returns_none(self) -> None:
        assert resolve_leaf_context_window(_snapshot(), scenario_id="") is None


class TestResolveLeafContextWindows:
    def test_authorized_connection_window_with_other_candidates(self) -> None:
        snapshot = _snapshot(
            routes=(
                {
                    "scenario_id": "agent_research",
                    "candidates": ({"connection_id": "conn-1"}, {"connection_id": "conn-2"}),
                },
            ),
            connections=(
                {"connection_id": "conn-1", "context_window": 65_536},
                {"connection_id": "conn-2", "context_window": 8192},
            ),
        )

        window, others = resolve_leaf_context_windows(snapshot, scenario_id="agent_research", connection_id="conn-2")

        assert window == 8192
        assert others == (65_536,)

    def test_authorized_connection_outside_route_is_unknown(self) -> None:
        window, others = resolve_leaf_context_windows(
            _snapshot(), scenario_id="agent_research", connection_id="conn-other"
        )

        assert window is None
        assert others == ()

    async def test_boundary_reserves_output_tokens(self) -> None:
        dao = _Dao()
        boundary = build_leaf_boundary(dao, snapshot=_snapshot(), scenario_id="agent_research")

        policy = await boundary.policy_provider(cast(Any, SimpleNamespace(deps=_deps())))
        assert policy.output_reserve_tokens == OUTPUT_RESERVE_TOKENS
        allowed = 8192 - OUTPUT_RESERVE_TOKENS
        assert evaluate_research_boundary(policy, measured_input_tokens=allowed) is None
        assert evaluate_research_boundary(policy, measured_input_tokens=allowed + 1) == "context_window_exceeded"


class TestResolveLeafTokenizerModel:
    def test_first_candidate_connection_model(self) -> None:
        snapshot = _snapshot(
            connections=({"connection_id": "conn-1", "context_window": 8192, "provider": "openai", "model": "gpt-4o"},)
        )

        assert resolve_leaf_tokenizer_model(snapshot, scenario_id="agent_research") == "openai/gpt-4o"

    def test_authorized_connection_model_selected(self) -> None:
        snapshot = _snapshot(
            routes=(
                {
                    "scenario_id": "agent_research",
                    "candidates": ({"connection_id": "conn-1"}, {"connection_id": "conn-2"}),
                },
            ),
            connections=(
                {"connection_id": "conn-1", "context_window": 65_536, "provider": "openai", "model": "gpt-4o"},
                {"connection_id": "conn-2", "context_window": 8192, "provider": "deepseek", "model": "deepseek-chat"},
            ),
        )

        assert (
            resolve_leaf_tokenizer_model(snapshot, scenario_id="agent_research", connection_id="conn-2")
            == "deepseek/deepseek-chat"
        )

    def test_model_without_provider_used_as_is(self) -> None:
        snapshot = _snapshot(connections=({"connection_id": "conn-1", "context_window": 8192, "model": "gpt-4o"},))

        assert resolve_leaf_tokenizer_model(snapshot, scenario_id="agent_research") == "gpt-4o"

    def test_missing_model_is_unknown(self) -> None:
        assert resolve_leaf_tokenizer_model(_snapshot(), scenario_id="agent_research") is None
        blank = _snapshot(connections=({"connection_id": "conn-1", "context_window": 8192, "model": ""},))
        assert resolve_leaf_tokenizer_model(blank, scenario_id="agent_research") is None

    def test_connection_outside_route_is_unknown(self) -> None:
        assert (
            resolve_leaf_tokenizer_model(
                _snapshot(connections=({"connection_id": "conn-1", "context_window": 8192, "model": "gpt-4o"},)),
                scenario_id="agent_research",
                connection_id="conn-other",
            )
            is None
        )

    def test_blank_scenario_is_unknown(self) -> None:
        assert resolve_leaf_tokenizer_model(_snapshot(), scenario_id="") is None


class TestBuildLeafBoundary:
    async def test_unknown_window_policy_is_denied(self) -> None:
        dao = _Dao()
        boundary = build_leaf_boundary(
            dao,
            snapshot=_snapshot(connections=()),
            scenario_id="agent_research",
        )

        assert isinstance(boundary, ResearchBoundary)
        policy = await boundary.policy_provider(cast(Any, SimpleNamespace(deps=_deps())))
        assert policy.mode is Mode.EXECUTE
        assert policy.allowed_tool_names == frozenset()
        assert policy.context_window_tokens is None
        assert evaluate_research_boundary(policy, measured_input_tokens=10) == "window_unknown"

    async def test_known_window_policy_allows_under_limit(self) -> None:
        dao = _Dao()
        boundary = build_leaf_boundary(dao, snapshot=_snapshot(), scenario_id="agent_research")

        policy = await boundary.policy_provider(cast(Any, SimpleNamespace(deps=_deps())))
        assert policy.context_window_tokens == 8192
        assert evaluate_research_boundary(policy, measured_input_tokens=10) is None
        assert dao.calls == 1

    async def test_policy_carries_frozen_connection_model(self) -> None:
        dao = _Dao()
        snapshot = _snapshot(
            connections=(
                {"connection_id": "conn-1", "context_window": 8192, "provider": "deepseek", "model": "deepseek-chat"},
            )
        )
        boundary = build_leaf_boundary(dao, snapshot=snapshot, scenario_id="agent_research")

        policy = await boundary.policy_provider(cast(Any, SimpleNamespace(deps=_deps())))
        # 计数按冻结连接实际模型，而非叶模型桥的固定别名。
        assert policy.tokenizer_model == "deepseek/deepseek-chat"

    def test_boundary_host_is_wired(self) -> None:
        assert BOUNDARY_HOST_WIRED is True
