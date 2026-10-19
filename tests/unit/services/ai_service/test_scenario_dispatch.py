"""S4 请求级双轨分派 helper 单测（模型统一.md §12.2/§14.2-6，方案 §1.1）。

覆盖（s4_plan.md §6.2 判定用例的分派前置条件组）：
- 调度服务未组装 → None（不伪装，走旧链，R21）；
- migration_state != "active" → None（pending 期间生产继续走原链 §12.2）；
- 场景路由 disabled / 候选为空 → None；
- 全条件满足 → 返回 ScenarioDispatch（executor + 冻结快照 + 路由元数据），
  快照仅含该场景路由、candidates 形态对齐 Executor 契约、连接全量透传；
- R1：services 层不 import app，调度服务经注册点注入（bootstrap app→services）。
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import ExecutionSnapshot
from services.ai_service import scenario_dispatch

pytestmark = pytest.mark.unit

_SCENARIO_ID = "news_annotation"


def _mm_snapshot(migration_state: str = "active", enabled: bool = True, candidates: list[str] | None = None):
    """构造 ModelManagementSnapshot 形态的 dict 快照（对齐 get_snapshot 产出）。"""
    return MagicMock(
        snapshot_id=str(uuid.uuid4()),
        config_revision=7,
        migration_state=migration_state,
        connections=({"connection_id": "conn-1", "adapter_kind": "litellm_chat"},),
        scenario_routes=(
            {
                "scenario_id": _SCENARIO_ID,
                "enabled": enabled,
                "candidates": candidates if candidates is not None else ["conn-1", "conn-2"],
                "route_revision": 3,
                "total_timeout_seconds": 45,
                "task_routes": {"sentiment": {"mode": "bundled", "candidates": [], "reuse_bundled_sentiment": True}},
            },
        ),
        usage_policy={},
        security_revision=5,
        created_at="2026-10-16T00:00:00Z",
    )


def _patch_env(monkeypatch, mm_snapshot, services: dict | None) -> None:
    svc = services if services is not None else {"executor": MagicMock(), "recorder": MagicMock()}
    scenario_dispatch.set_scheduling_services_provider(lambda: svc)
    config_service = MagicMock()
    config_service.get_snapshot = AsyncMock(return_value=mm_snapshot)
    monkeypatch.setattr(scenario_dispatch, "_new_config_service", lambda: config_service)


@pytest.fixture(autouse=True)
def _reset_provider():
    yield
    scenario_dispatch.set_scheduling_services_provider(None)


async def test_returns_none_when_scheduling_services_absent(monkeypatch):
    scenario_dispatch.set_scheduling_services_provider(None)
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID) is None


async def test_returns_none_when_no_executor_in_services(monkeypatch):
    scenario_dispatch.set_scheduling_services_provider(lambda: {"recorder": MagicMock()})
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID) is None


async def test_returns_none_when_migration_pending(monkeypatch):
    _patch_env(monkeypatch, _mm_snapshot(migration_state="pending"), None)
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID) is None


async def test_returns_none_when_route_disabled(monkeypatch):
    _patch_env(monkeypatch, _mm_snapshot(enabled=False), None)
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID) is None


async def test_returns_none_when_candidates_empty(monkeypatch):
    _patch_env(monkeypatch, _mm_snapshot(candidates=[]), None)
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID) is None


async def test_returns_none_when_scenario_missing(monkeypatch):
    _patch_env(monkeypatch, _mm_snapshot(), None)
    assert await scenario_dispatch.resolve_executor("stock_analysis") is None


async def test_dispatch_built_when_all_conditions_met(monkeypatch):
    mm = _mm_snapshot()
    executor = MagicMock()
    _patch_env(monkeypatch, mm, {"executor": executor})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    assert dispatch.executor is executor
    assert isinstance(dispatch.snapshot, ExecutionSnapshot)
    # 路由元数据：route_revision / total_timeout_seconds 供入口构造 deadline
    assert dispatch.route_revision == 3
    assert dispatch.total_timeout_seconds == 45.0
    assert dispatch.task_routes["sentiment"]["mode"] == "bundled"

    snap = dispatch.snapshot
    # 快照仅含该场景路由，candidates 形态对齐 Executor 消费契约
    assert len(snap.routes) == 1
    route = snap.routes[0]
    assert route["scenario_id"] == _SCENARIO_ID
    assert route["route_revision"] == 3
    assert [c["connection_id"] for c in route["candidates"]] == ["conn-1", "conn-2"]
    assert [c["candidate_index"] for c in route["candidates"]] == [0, 1]
    assert all(c["task_id"] == _SCENARIO_ID for c in route["candidates"])
    assert all(c["reservation_id"] is None for c in route["candidates"])
    # 连接全量透传 + 快照归属字段
    assert snap.connections == mm.connections
    assert snap.config_revision == 7
    assert snap.security_scope_version == 5
    # 契约版本按 task_id 携带
    assert set(snap.business_contract_versions) >= {"topic", "sentiment"}


async def test_provider_injection_roundtrip():
    provider = lambda: {"executor": object()}  # noqa: E731
    scenario_dispatch.set_scheduling_services_provider(provider)
    assert scenario_dispatch.get_scheduling_services() is not None
    scenario_dispatch.set_scheduling_services_provider(None)
    assert scenario_dispatch.get_scheduling_services() is None
