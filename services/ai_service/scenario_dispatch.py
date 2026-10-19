"""S4 请求级双轨分派 helper（模型统一.md §12.2/§14.2-6，方案 §1.1）。

职责：三入口共用的新链分派点——判定统一调度链是否可用，可用时构造冻结
ExecutionSnapshot 返回分派上下文；任一条件不满足返回 None（调用方走旧链，
不伪装成功，R21）。分派条件（全部满足 → 新链，否则旧链）：

1. 统一调度服务已组装（executor 可用）；
2. ``ModelConfigurationService.get_snapshot().migration_state == "active"``；
3. 该场景路由 ``enabled`` 且候选列表非空。

同请求单轨（§14.2-6）：分派点唯一且在入口处，一次请求只走一条链；
不同请求按分派时点状态各自选择。

R1 合规：本模块与三入口同在 services 层，不 import ``app.bootstrap``——
调度服务经 :func:`set_scheduling_services_provider` 注册点由 app 层注入
（app → services 为合法方向），bootstrap 组装完成后注册。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from core.model_management_types import ExecutionSnapshot

# S4 prompt/schema 契约版本（§13.1 business_contract_versions：task_id → 版本）。
# 三入口 prompt/schema 变更时递增；news_annotation 拆分 topic / sentiment 两任务。
_TASK_CONTRACT_VERSIONS: Final[Mapping[str, str]] = {
    "topic": "news_topic_v1",
    "sentiment": "news_sentiment_v1",
    "analysis": "stock_analysis_v1",
    "risk": "news_risk_v1",
}

# 调度服务注册点（R1：services 不 import app；bootstrap 组装后注入 provider）。
_scheduling_services_provider: Callable[[], dict[str, Any] | None] | None = None


def set_scheduling_services_provider(provider: Callable[[], dict[str, Any] | None] | None) -> None:
    """注册 / 清除调度服务 provider（app.bootstrap 组装完成后调用）。"""

    global _scheduling_services_provider
    _scheduling_services_provider = provider


def get_scheduling_services() -> dict[str, Any] | None:
    """读取当前调度服务（未注册 / 未组装返回 None，走旧链）。"""

    return _scheduling_services_provider() if _scheduling_services_provider is not None else None


def _new_config_service() -> Any:
    """ModelConfigurationService 构造点（测试 monkeypatch 锚）。"""

    from services.model_configuration_service import ModelConfigurationService

    return ModelConfigurationService()


@dataclass(frozen=True, slots=True)
class ScenarioDispatch:
    """一次新链分派上下文：executor + 冻结快照 + 场景路由元数据。"""

    executor: Any
    snapshot: ExecutionSnapshot
    route_revision: int
    total_timeout_seconds: float
    task_routes: Mapping[str, Any]


async def resolve_executor(scenario_id: str) -> ScenarioDispatch | None:
    """按三条件分派新链；任一不满足返回 None（调用方走旧链）。"""

    services = get_scheduling_services()
    executor = services.get("executor") if services else None
    if executor is None:
        return None

    mm_snapshot = await _new_config_service().get_snapshot()
    if mm_snapshot.migration_state != "active":
        return None

    route = next(
        (r for r in mm_snapshot.scenario_routes if r.get("scenario_id") == scenario_id),
        None,
    )
    if route is None or not route.get("enabled") or not route.get("candidates"):
        return None

    # scenario_routes.candidates 为有序 connection_id 列表，转 Executor 契约形态；
    # S4 无预算预留（S5 接入）：reservation_id=None + 无 budget 金额 → D5 usage_only 语义。
    candidates = tuple(
        {
            "candidate_index": index,
            "task_id": scenario_id,
            "connection_id": conn_id,
            "reservation_id": None,
            "owner_session_id": "",
            "owner_epoch": 0,
        }
        for index, conn_id in enumerate(route["candidates"])
    )

    snapshot = ExecutionSnapshot(
        snapshot_id=mm_snapshot.snapshot_id,
        run_id=None,
        config_revision=mm_snapshot.config_revision,
        routes=(
            {
                "scenario_id": scenario_id,
                "route_revision": int(route.get("route_revision", 1)),
                "candidates": candidates,
            },
        ),
        connections=mm_snapshot.connections,
        business_contract_versions=dict(_TASK_CONTRACT_VERSIONS),
        security_scope_version=mm_snapshot.security_revision,
        created_at=mm_snapshot.created_at,
    )
    return ScenarioDispatch(
        executor=executor,
        snapshot=snapshot,
        route_revision=int(route.get("route_revision", 1)),
        total_timeout_seconds=float(route.get("total_timeout_seconds", 60)),
        task_routes=route.get("task_routes") or {},
    )
