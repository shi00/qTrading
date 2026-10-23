"""S4 请求级双轨分派 helper 单测（模型统一.md §12.2/§14.2-6，方案 §1.1）。

覆盖（s4_plan.md §6.2 判定用例的分派前置条件组）：
- 调度服务未组装 → None（不伪装，走旧链，R21）；
- migration_state != "active" → None（pending 期间生产继续走原链 §12.2）；
- 场景路由 disabled / 候选为空 → None；
- 全条件满足 → 返回 ScenarioDispatch（executor + 冻结快照 + 路由元数据），
  快照仅含该场景路由、candidates 形态对齐 Executor 契约、连接全量透传；
- F01 分派授权身份：候选携带规范化外发目标（本地 / 缺失 → None）与场景用途，
  快照 scope 版本取安全状态 scope_version（独立于 security_revision）；
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
        connections=(
            {"connection_id": "conn-1", "adapter_kind": "litellm_chat", "api_base": "https://API.Example.com/v1"},
            {"connection_id": "conn-local", "adapter_kind": "gguf_chat"},
        ),
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
        security_scope_version=1,
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
    # F16：usage_policy 未配置限额（fixture usage_policy={}）→ usage_only 预算身份
    # 三 None（不伪装政策存在，R21）；owner / 派发身份不再以伪值占位（由 Executor
    # per-invocation 生成），候选不携带 reservation/owner 键
    assert all(c["budget_amount_cny"] is None for c in route["candidates"])
    assert all(c["budget_limit_cny"] is None for c in route["candidates"])
    assert all(c["budget_policy_snapshot"] is None for c in route["candidates"])
    assert all(
        "reservation_id" not in c and "owner_session_id" not in c and "owner_epoch" not in c
        for c in route["candidates"]
    )
    # F01 分派授权身份：远程候选携带规范化外发目标（host 小写 + 显式端口 + 完整 path），
    # 用途即场景 id；快照中不存在的连接诚实置 None（不臆造目标，R21）
    by_conn = {c["connection_id"]: c for c in route["candidates"]}
    assert by_conn["conn-1"]["target_fingerprint"] == "https://api.example.com:443/v1"
    assert by_conn["conn-1"]["purpose"] == _SCENARIO_ID
    assert by_conn["conn-2"]["target_fingerprint"] is None
    assert by_conn["conn-2"]["purpose"] == _SCENARIO_ID
    # 连接全量透传 + 快照归属字段
    assert snap.connections == mm.connections
    assert snap.config_revision == 7
    # F01：快照 scope 版本取安全状态 scope_version，独立于 security_revision（5）
    assert snap.security_scope_version == 1
    assert mm.security_revision == 5
    # 契约版本按 task_id 携带
    assert set(snap.business_contract_versions) >= {"topic", "sentiment"}


async def test_dispatch_local_connection_target_none(monkeypatch):
    """F01：本地 gguf 连接无外发目标，候选 target_fingerprint 诚实为 None。"""
    mm = _mm_snapshot(candidates=["conn-local"])
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    candidate = dispatch.snapshot.routes[0]["candidates"][0]
    assert candidate["target_fingerprint"] is None
    assert candidate["purpose"] == _SCENARIO_ID


async def test_dispatch_fee_candidates_with_monthly_limit(monkeypatch):
    """F16：usage_policy 携带月度限额 → 费用型候选冻结预算身份（amount 暂 "0.00"，
    F17 接入预估计价前不伪装单次成本 R21；预算门按已结算 + 在途 + 0 判定）。"""
    mm = _mm_snapshot()
    mm.usage_policy = {"monthly_cny_limit": "50.00"}
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    route = dispatch.snapshot.routes[0]
    for cand in route["candidates"]:
        assert cand["budget_amount_cny"] == "0.00"
        assert cand["budget_limit_cny"] == "50.00"
        assert cand["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
    # 父请求预算政策快照可由候选冻结值提取（executor 侧 _budget_policy_snapshot 消费）
    assert route["candidates"][0]["budget_policy_snapshot"] is not None


async def test_provider_injection_roundtrip():
    provider = lambda: {"executor": object()}  # noqa: E731
    scenario_dispatch.set_scheduling_services_provider(provider)
    assert scenario_dispatch.get_scheduling_services() is not None
    scenario_dispatch.set_scheduling_services_provider(None)
    assert scenario_dispatch.get_scheduling_services() is None
