"""S4 请求级双轨分派 helper（模型统一.md §12.2/§14.2-6，方案 §1.1）。

职责：三入口共用的新链分派点——判定统一调度链是否可用，可用时构造冻结
ExecutionSnapshot 返回分派上下文；任一条件不满足返回 None（调用方走旧链，
不伪装成功，R21）。分派条件（全部满足 → 新链，否则旧链）：

1. 统一调度服务已组装（executor 可用）；
2. ``ModelConfigurationService.get_snapshot().migration_state == "active"``；
3. 该场景路由 ``enabled`` 且候选列表非空。

同请求单轨（§14.2-6）：分派点唯一且在入口处，一次请求只走一条链；
不同请求按分派时点状态各自选择。

F01 分派授权身份：候选携带规范化外发目标与场景用途，快照授权范围版本取
security_state.scope_version（独立于 security_revision）；Executor 逐次
authorize_attempt 时按连接 adapter_kind 声明 requires_egress_grant。

S6 D6 旧链准入（读入口）：``resolve_executor`` 未命中（走旧链前）由三入口
helper 调 :func:`evaluate_legacy_admission` 新鲜读迁移期状态判定旧链放行——
``active`` → ``refused``、无 transition → ``allowed``、``draining`` →
``draining_stop``、``sealed`` → ``refused``；非 ``allowed`` 时三入口 helper
raise ``LegacyDispatchBlockedError``（不落入「分派异常降级旧链」分支伪装成功，
R21），旧链记账写入由 ``AIUsageTracker`` 写入口封口检查二次拦截。

R1 合规：本模块与三入口同在 services 层，不 import ``app.bootstrap``——
调度服务经 :func:`set_scheduling_services_provider` 注册点由 app 层注入
（app → services 为合法方向），bootstrap 组装完成后注册。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

from core.model_management_types import (
    ExecutionSnapshot,
    LegacyDispatchBlockedError,
    normalize_egress_target,
)
from utils.error_classifier import log_classified

logger = logging.getLogger(__name__)

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

    # F01 分派授权身份：候选携带规范化外发目标（本地连接 / 非法 api_base → None，
    # 诚实表示「无外发目标」，R21）与场景用途（purpose 域即 scenario_id，§4.3
    # 「新闻专用情绪使用 news_annotation」）；Executor 按连接 adapter_kind 决定
    # 是否要求 egress grant。授权范围版本取安全状态自身 scope_version（独立于
    # security_revision，F01）。
    connection_targets = {
        str(c["connection_id"]): normalize_egress_target(c.get("api_base"))
        for c in mm_snapshot.connections
        if isinstance(c, Mapping) and c.get("connection_id") is not None
    }

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
            "target_fingerprint": connection_targets.get(str(conn_id)),
            "purpose": scenario_id,
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
        security_scope_version=mm_snapshot.security_scope_version,
        created_at=mm_snapshot.created_at,
    )
    return ScenarioDispatch(
        executor=executor,
        snapshot=snapshot,
        route_revision=int(route.get("route_revision", 1)),
        total_timeout_seconds=float(route.get("total_timeout_seconds", 60)),
        task_routes=route.get("task_routes") or {},
    )


async def resolve_analysis_track() -> ScenarioDispatch | None:
    """stock_analysis 场景执行轨道判定（F04）：分派命中返回新轨上下文，否则旧轨放行或阻断。

    - ``resolve_executor("stock_analysis")`` 命中（统一调度链 active 且场景路由启用）
      → 返回 :class:`ScenarioDispatch`（调用方应冻结该快照贯穿整次运行，同请求单轨
      §14.2-6，批次中途配置变化不混版）；
    - 未命中且 ``evaluate_legacy_admission() == "allowed"`` → 返回 ``None``（旧轨，
      调用方应用旧云端 / 旧链准入 guard）；
    - 未命中且准入为 ``draining_stop`` / ``refused`` → raise :class:`LegacyDispatchBlockedError`
      （AI 功能明确不可用必须传出，不得降级伪装成功，R21）；
    - 分派 / 准入读取过程异常（F05 fail-closed）→ raise ``LegacyDispatchBlockedError("read_failed")``
      ——读取失败等于授权状态未知，降级旧链即把「未知」伪装成「允许」（R21）。

    供单股分派 helper（``stock_analysis._resolve_stock_analysis_dispatch``）与策略
    批量入口（``strategies.ai_mixin``，strategies→services 为合法方向 R1）共用，
    语义单源。
    """

    try:
        dispatch = await resolve_executor("stock_analysis")
        if dispatch is not None:
            return dispatch
        admission = await evaluate_legacy_admission()
    except asyncio.CancelledError:
        raise
    except Exception as e:
        # F05 fail-closed：读取失败不得降级旧链授权语义（原 fail-open 行为已移除）。
        log_classified(
            logger,
            e,
            "llm",
            "[ScenarioDispatch] analysis track read failed, blocking legacy (%s): %s",
            exc_info=True,
        )
        raise LegacyDispatchBlockedError("read_failed") from e
    # S6 D6：准入停接在 try 外 raise——「AI 功能明确不可用」必须传出，不得降级旧链。
    if admission != "allowed":
        raise LegacyDispatchBlockedError(admission)
    return None


# S6 D6 旧链准入判定值域：allowed=旧链照常 / draining_stop=排水期停接 / refused=拒绝。
LegacyAdmission = Literal["allowed", "draining_stop", "refused"]


async def evaluate_legacy_admission() -> LegacyAdmission:
    """旧链准入判定（S6 D6 读入口）：新鲜读配置快照，三入口走旧链前调用。

    状态表（§12.2）：``migration_state == "active"`` → ``"refused"``（新链已接管）；
    ``migration_transition is None`` → ``"allowed"``（pending 未开启迁移）；
    ``phase == "draining"`` → ``"draining_stop"``（排水期停止新接旧链请求）；
    ``phase == "sealed"`` → ``"refused"``（已封存）；其他未知 phase 值防御性
    ``"allowed"``（不因未知值误停接，写入侧封口检查仍兜底）。

    每次调用均经 ``get_snapshot()`` 新鲜读（正本要求，不做进程内缓存）；
    配置读取异常由调用方（三入口 helper / 批量循环）按各自既有语义处置。
    """
    mm_snapshot = await _new_config_service().get_snapshot()
    if mm_snapshot.migration_state == "active":
        return "refused"
    transition = mm_snapshot.migration_transition
    if transition is None:
        return "allowed"
    phase = transition.get("phase")
    if phase == "draining":
        return "draining_stop"
    if phase == "sealed":
        return "refused"
    return "allowed"
