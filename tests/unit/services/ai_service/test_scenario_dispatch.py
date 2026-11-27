"""S4 请求级双轨分派 helper 单测（模型统一.md §12.2/§14.2-6，方案 §1.1）。

覆盖（s4_plan.md §6.2 判定用例的分派前置条件组）：
- 调度服务未组装 → None（不伪装，走旧链，R21）；
- migration_state != "active" → None（pending 期间生产继续走原链 §12.2）；
- 场景路由 disabled / 候选为空 → None；
- 全条件满足 → 返回 ScenarioDispatch（executor + 冻结快照 + 路由元数据），
  快照仅含该场景路由、candidates 形态对齐 Executor 契约、连接全量透传；
- F01 分派授权身份：候选携带规范化外发目标（本地 / 缺失 → None）与场景用途，
  快照 scope 版本取安全状态 scope_version（独立于 security_revision）；
- D13-001 路由策略完整性：主路由经共享构造冻结 transient_retries /
  fallback_enabled / total_timeout_seconds；0 重试 / 禁用 fallback 原值保留；
  缺字段 / 非法值 / 旧版三键路由 → RoutePolicyIncompleteError 显式失败；
- M16 深冻结：路由条目 / 候选 / 预算政策快照嵌套写一律 TypeError，拒绝后旧受理
  候选与预算不变；读取投影、dict() / deepcopy / json 序列化保持兼容；
- 首选连接收窄：None 行为不变；命中候选且连接启用 → 候选收窄为该连接；不在候选 /
  连接缺失 / 未启用 / 路由层不可用（路由禁用 / 候选为空）→
  RoutePolicyIncompleteError("preferred_candidate_unavailable")；系统级不可用
  （executor 缺失 / 迁移未激活）→ None（首选不生效）；
- R1：services 层不 import app，调度服务经注册点注入（bootstrap app→services）。
"""

import copy
import json
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import ExecutionSnapshot, LegacyDispatchBlockedError
from services.ai_service import scenario_dispatch

pytestmark = pytest.mark.unit

_SCENARIO_ID = "news_annotation"

# D13-001：路由冻结快照的完整可执行字段集合（白名单契约）
_EXPECTED_ROUTE_FIELDS = {
    "scenario_id",
    "route_revision",
    "candidates",
    "transient_retries",
    "fallback_enabled",
    "total_timeout_seconds",
}


def _mm_snapshot(
    migration_state: str = "active",
    enabled: bool = True,
    candidates: list[str] | None = None,
    *,
    transient_retries: int = 1,
    fallback_enabled: bool = True,
    total_timeout_seconds: int = 45,
):
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
                "total_timeout_seconds": total_timeout_seconds,
                "transient_retries": transient_retries,
                "fallback_enabled": fallback_enabled,
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
    # D13-001：路由条目一次携带完整可执行策略字段（白名单），默认策略原值保留
    assert set(route) == _EXPECTED_ROUTE_FIELDS
    assert route["transient_retries"] == 1
    assert route["fallback_enabled"] is True
    assert route["total_timeout_seconds"] == 45.0
    assert [c["connection_id"] for c in route["candidates"]] == ["conn-1", "conn-2"]
    assert [c["candidate_index"] for c in route["candidates"]] == [0, 1]
    assert all(c["task_id"] == _SCENARIO_ID for c in route["candidates"])
    # F16：usage_policy 未配置限额（fixture usage_policy={}）→ usage_only 预算身份
    # 两 None（不伪装政策存在，R21）；owner / 派发身份不再以伪值占位（由 Executor
    # per-invocation 生成），候选不携带 reservation/owner 键
    # 单次预留金额由 Executor 按计价快照 + payload 估算（F19），dispatch 不伪造
    assert all("budget_amount_cny" not in c for c in route["candidates"])
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


async def test_preferred_connection_none_keeps_candidates(monkeypatch):
    """preferred_connection_id=None：行为不变，候选全量透传。"""
    _patch_env(monkeypatch, _mm_snapshot(candidates=["conn-1", "conn-local"]), None)

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id=None)

    assert isinstance(dispatch, scenario_dispatch.ScenarioDispatch)
    route = dispatch.snapshot.routes[0]
    assert [c["connection_id"] for c in route["candidates"]] == ["conn-1", "conn-local"]
    assert [c["candidate_index"] for c in route["candidates"]] == [0, 1]
    assert route["candidates"][0]["target_fingerprint"] == "https://api.example.com:443/v1"


async def test_preferred_connection_narrows_candidates(monkeypatch):
    """命中候选且连接启用 → 候选收窄为该连接，其余路由字段 / 执行器 / 子路由不变。"""
    executor = MagicMock()
    _patch_env(monkeypatch, _mm_snapshot(candidates=["conn-1", "conn-local"]), {"executor": executor})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local")

    assert isinstance(dispatch, scenario_dispatch.ScenarioDispatch)
    assert dispatch.executor is executor
    assert dispatch.task_routes == {"sentiment": {"mode": "bundled", "candidates": [], "reuse_bundled_sentiment": True}}
    route = dispatch.snapshot.routes[0]
    assert set(route) == _EXPECTED_ROUTE_FIELDS
    assert [c["connection_id"] for c in route["candidates"]] == ["conn-local"]
    assert [c["candidate_index"] for c in route["candidates"]] == [0]
    assert route["candidates"][0]["purpose"] == _SCENARIO_ID
    assert route["candidates"][0]["target_fingerprint"] is None
    assert route["route_revision"] == 3
    assert route["transient_retries"] == 1
    assert route["fallback_enabled"] is True
    assert route["total_timeout_seconds"] == 45.0
    assert dispatch.route_revision == 3
    assert dispatch.total_timeout_seconds == 45.0


async def test_preferred_connection_outside_candidates_fails(monkeypatch):
    """首选连接不在本场景候选 → 显式失败，不静默按原候选执行（R21）。"""
    _patch_env(monkeypatch, _mm_snapshot(candidates=["conn-1"]), None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_disabled_fails(monkeypatch):
    """首选连接在候选中但已禁用 → 显式失败。"""
    mm = _mm_snapshot(candidates=["conn-1"])
    mm.connections = ({"connection_id": "conn-1", "adapter_kind": "litellm_chat", "enabled": False},)
    _patch_env(monkeypatch, mm, None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-1")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_absent_from_snapshot_fails(monkeypatch):
    """首选连接在候选中但快照无该连接定义 → 显式失败。"""
    mm = _mm_snapshot(candidates=["conn-local"])
    mm.connections = ({"connection_id": "conn-1", "adapter_kind": "litellm_chat"},)
    _patch_env(monkeypatch, mm, None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_route_disabled_fails(monkeypatch):
    """路由层不可用（场景路由禁用）+ 显式首选 → 显式失败，不静默丢弃选择（R21）。"""
    _patch_env(monkeypatch, _mm_snapshot(enabled=False), None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_empty_candidates_fails(monkeypatch):
    """路由层不可用（候选为空）+ 显式首选 → 显式失败，不静默丢弃选择（R21）。"""
    _patch_env(monkeypatch, _mm_snapshot(candidates=[]), None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_missing_scenario_fails(monkeypatch):
    """场景路由缺失（``route is None``）+ 显式首选 → 显式失败，不静默走旧链（R21）。"""
    _patch_env(monkeypatch, _mm_snapshot(), None)

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError) as exc:
        await scenario_dispatch.resolve_executor("stock_analysis", preferred_connection_id="conn-local")

    assert str(exc.value) == "preferred_candidate_unavailable"


async def test_preferred_connection_not_applied_when_system_unavailable(monkeypatch):
    """系统级不可用（executor 缺失 / 迁移未激活）→ 返回 None，首选连接不生效。"""
    scenario_dispatch.set_scheduling_services_provider(lambda: {"recorder": MagicMock()})
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local") is None

    _patch_env(monkeypatch, _mm_snapshot(migration_state="pending"), None)
    assert await scenario_dispatch.resolve_executor(_SCENARIO_ID, preferred_connection_id="conn-local") is None


async def test_dispatch_fee_candidates_with_monthly_limit(monkeypatch):
    """F16：usage_policy 携带月度限额 → 候选冻结限额与政策快照；单次金额不在此
    冻结（F19：dispatch 无 payload，金额由 Executor 按计价快照估算，R21 不以 0 占位）。"""
    mm = _mm_snapshot()
    mm.usage_policy = {"monthly_cny_limit": "50.00"}
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    route = dispatch.snapshot.routes[0]
    for cand in route["candidates"]:
        assert "budget_amount_cny" not in cand
        assert cand["budget_limit_cny"] == "50.00"
        assert cand["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
    # 父请求预算政策快照可由候选冻结值提取（executor 侧 _budget_policy_snapshot 消费）
    assert route["candidates"][0]["budget_policy_snapshot"] is not None


async def test_dispatch_route_entry_rejects_nested_writes(monkeypatch):
    """M16：已受理快照的嵌套写被拒绝，旧受理候选 / 预算保持不变。

    外层 ``ExecutionSnapshot`` 仅为 frozen dataclass（只封属性重绑定）；若路由
    条目仍是普通 dict，消费方可改写同一次请求的候选预算。此用例锚定冻结契约。
    """
    mm = _mm_snapshot()
    mm.usage_policy = {"monthly_cny_limit": "50.00"}
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    route: Any = dispatch.snapshot.routes[0]
    cand = route["candidates"][0]
    with pytest.raises(TypeError, match="read-only"):
        route["transient_retries"] = 2
    with pytest.raises(TypeError, match="read-only"):
        cand["budget_limit_cny"] = "0.00"
    with pytest.raises(TypeError, match="read-only"):
        cand["budget_policy_snapshot"]["monthly_cny_limit"] = "0.00"
    # 拒绝后旧受理候选 / 预算原值不变（无部分修改）
    assert route["candidates"][0]["budget_limit_cny"] == "50.00"
    assert dict(route["candidates"][0]["budget_policy_snapshot"]) == {"monthly_cny_limit": "50.00"}


async def test_dispatch_freezes_zero_retries_and_fallback_disabled(monkeypatch):
    """D13-001：用户保存的 0 重试 / 禁用备用策略必须进入冻结快照（不得丢键）。"""
    mm = _mm_snapshot(transient_retries=0, fallback_enabled=False)
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    dispatch = await scenario_dispatch.resolve_executor(_SCENARIO_ID)

    assert dispatch is not None
    route = dispatch.snapshot.routes[0]
    assert set(route) == _EXPECTED_ROUTE_FIELDS
    # 零重试原值保留（不被默认值 1 覆盖），禁用备用为 False（不被默认值 True 覆盖）
    assert route["transient_retries"] == 0
    assert route["fallback_enabled"] is False
    assert route["total_timeout_seconds"] == 45.0
    assert dispatch.total_timeout_seconds == 45.0


@pytest.mark.parametrize(
    "field",
    ["transient_retries", "fallback_enabled", "total_timeout_seconds"],
)
async def test_route_policy_missing_field_fails_explicitly(monkeypatch, field):
    """D13-001：策略字段缺失 → 显式失败，不返回 None 降级旧链、不静默用默认。"""
    mm = _mm_snapshot()
    mm.scenario_routes[0].pop(field)
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError, match=field):
        await scenario_dispatch.resolve_executor(_SCENARIO_ID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("transient_retries", 3),
        ("transient_retries", True),
        ("transient_retries", "1"),
        ("fallback_enabled", "no"),
        ("fallback_enabled", 0),
        ("total_timeout_seconds", 0),
        ("total_timeout_seconds", None),
    ],
)
async def test_route_policy_invalid_value_fails_explicitly(monkeypatch, field, value):
    """D13-001：策略字段非法（越界 / bool 冒充 int / 类型错误）→ 显式失败。"""
    mm = _mm_snapshot()
    mm.scenario_routes[0][field] = value
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError, match=field):
        await scenario_dispatch.resolve_executor(_SCENARIO_ID)


async def test_legacy_three_key_route_fails_explicitly(monkeypatch):
    """D13-001：旧版路由形态（仅 scenario_id/route_revision/candidates）→ 显式失败。

    D13-001 前的分派构造即此三键形态；策略键丢失必须暴露，不得按默认重试 /
    允许 fallback 继续执行并把「策略未生效」伪装成成功（R21）。
    """
    mm = _mm_snapshot()
    mm.scenario_routes = (
        {
            "scenario_id": _SCENARIO_ID,
            "enabled": True,
            "candidates": ["conn-1"],
            "route_revision": 3,
            "task_routes": {},
        },
    )
    _patch_env(monkeypatch, mm, {"executor": MagicMock()})

    with pytest.raises(scenario_dispatch.RoutePolicyIncompleteError, match="transient_retries"):
        await scenario_dispatch.resolve_executor(_SCENARIO_ID)


def test_build_route_snapshot_whitelists_executable_fields():
    """D13-001：共享构造只输出白名单字段，未知 / 展示用配置键不进入执行快照。"""
    built = scenario_dispatch.build_route_snapshot(
        {
            "scenario_id": _SCENARIO_ID,
            "route_revision": 4,
            "candidates": ["stale-candidate-id"],
            "transient_retries": 0,
            "fallback_enabled": False,
            "total_timeout_seconds": 30,
            "execution_options": {"future_key": True},
            "task_routes": {"sentiment": {"mode": "bundled"}},
        },
        scenario_id=_SCENARIO_ID,
        candidates=(),
    )

    assert set(built) == _EXPECTED_ROUTE_FIELDS
    assert built["candidates"] == ()
    assert built["route_revision"] == 4
    assert built["transient_retries"] == 0
    assert built["fallback_enabled"] is False
    assert built["total_timeout_seconds"] == 30.0


def _budget_candidates() -> tuple[dict, ...]:
    """单候选 + 嵌套预算政策快照（M16：嵌套写用例的最小形态）。"""
    return (
        {
            "candidate_index": 0,
            "task_id": _SCENARIO_ID,
            "connection_id": "conn-1",
            "budget_limit_cny": "50.00",
            "budget_policy_snapshot": {"monthly_cny_limit": "50.00"},
            "target_fingerprint": "https://api.example.com:443/v1",
            "purpose": _SCENARIO_ID,
            "nested_list": ["x", {"y": 1}],
        },
    )


def test_route_entry_rejects_nested_writes_and_keeps_inputs():
    """M16：转换产物与输入同等深冻结——嵌套 dict / list 写一律拒绝，输入不被改动。"""
    candidates = _budget_candidates()
    built = scenario_dispatch.build_route_snapshot(
        {
            "scenario_id": _SCENARIO_ID,
            "route_revision": 3,
            "transient_retries": 1,
            "fallback_enabled": True,
            "total_timeout_seconds": 30,
        },
        scenario_id=_SCENARIO_ID,
        candidates=candidates,
    )

    cand = built["candidates"][0]
    with pytest.raises(TypeError, match="read-only"):
        built["scenario_id"] = "other"
    with pytest.raises(TypeError, match="read-only"):
        built["transient_retries"] = 2
    with pytest.raises(TypeError, match="read-only"):
        cand["budget_limit_cny"] = "0.00"
    with pytest.raises(TypeError, match="read-only"):
        cand["budget_policy_snapshot"]["monthly_cny_limit"] = "0.00"
    with pytest.raises(TypeError, match="read-only"):
        cand["nested_list"][1]["y"] = 2

    # 拒绝后旧受理候选 / 预算不变（冻结为深拷贝：快照与输入脱钩，无部分修改）
    assert candidates[0]["budget_limit_cny"] == "50.00"
    assert candidates[0]["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
    assert candidates[0]["nested_list"] == ["x", {"y": 1}]
    assert built["candidates"][0]["budget_limit_cny"] == "50.00"
    assert built["candidates"][0]["budget_policy_snapshot"]["monthly_cny_limit"] == "50.00"


def test_route_entry_reads_and_serialization_compatible():
    """正常读取投影可用：get / 索引 / 切片 / dict() / deepcopy / json 序列化。"""
    built = scenario_dispatch.build_route_snapshot(
        {
            "scenario_id": _SCENARIO_ID,
            "route_revision": 3,
            "transient_retries": 0,
            "fallback_enabled": False,
            "total_timeout_seconds": 45,
        },
        scenario_id=_SCENARIO_ID,
        candidates=_budget_candidates(),
    )

    assert built.get("scenario_id") == _SCENARIO_ID
    assert built["candidates"][0]["purpose"] == _SCENARIO_ID
    assert len(built["candidates"][:1]) == 1
    # Executor 消费形态：dict() 投影（_budget_policy_snapshot）/ deepcopy / 落账 json
    assert dict(built["candidates"][0]["budget_policy_snapshot"]) == {"monthly_cny_limit": "50.00"}
    assert copy.deepcopy(built)["candidates"][0]["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
    assert json.loads(json.dumps(dict(built)))["candidates"][0]["budget_limit_cny"] == "50.00"
    assert built["candidates"][0]["nested_list"] == ("x", {"y": 1})


def test_build_route_snapshot_accepts_frozen_route_for_subroute():
    """旧链兼容：从已冻结主路由条目重建子路由（news_classifier dedicated 路径）。"""
    base = scenario_dispatch.build_route_snapshot(
        {
            "scenario_id": _SCENARIO_ID,
            "route_revision": 3,
            "transient_retries": 1,
            "fallback_enabled": True,
            "total_timeout_seconds": 30,
        },
        scenario_id=_SCENARIO_ID,
        candidates=_budget_candidates(),
    )
    base_budget = base["candidates"][0]

    sub = scenario_dispatch.build_route_snapshot(
        base,
        scenario_id=_SCENARIO_ID,
        candidates=(
            {
                "candidate_index": 0,
                "task_id": "sentiment",
                "connection_id": "conn-1",
                "budget_limit_cny": base_budget["budget_limit_cny"],
                "budget_policy_snapshot": base_budget["budget_policy_snapshot"],
                "purpose": _SCENARIO_ID,
            },
        ),
    )

    assert sub["route_revision"] == base["route_revision"]
    assert sub["transient_retries"] == 1
    assert sub["candidates"][0]["budget_policy_snapshot"] == {"monthly_cny_limit": "50.00"}
    with pytest.raises(TypeError, match="read-only"):
        sub["candidates"][0]["budget_policy_snapshot"]["monthly_cny_limit"] = "0.00"
    assert base["candidates"][0]["budget_policy_snapshot"]["monthly_cny_limit"] == "50.00"


async def test_resolve_analysis_track_preserves_route_incomplete_reason(monkeypatch):
    """路由策略不完整 → route_incomplete 阻断并保留原始原因（不吞成 read_failed）。"""

    async def _raise_route_incomplete(_scenario_id: str) -> None:
        raise scenario_dispatch.RoutePolicyIncompleteError("preferred_candidate_unavailable")

    monkeypatch.setattr(scenario_dispatch, "resolve_executor", _raise_route_incomplete)

    with pytest.raises(LegacyDispatchBlockedError) as excinfo:
        await scenario_dispatch.resolve_analysis_track()

    assert excinfo.value.admission == "route_incomplete"
    assert "preferred_candidate_unavailable" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, scenario_dispatch.RoutePolicyIncompleteError)


async def test_provider_injection_roundtrip():
    provider = lambda: {"executor": object()}  # noqa: E731
    scenario_dispatch.set_scheduling_services_provider(provider)
    assert scenario_dispatch.get_scheduling_services() is not None
    scenario_dispatch.set_scheduling_services_provider(None)
    assert scenario_dispatch.get_scheduling_services() is None
