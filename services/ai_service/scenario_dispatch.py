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

D13-001 路由策略完整性：冻结快照的 Executor 消费字段（``transient_retries`` /
``fallback_enabled`` / ``total_timeout_seconds``）由 :func:`build_route_snapshot`
**唯一构造**，主路由与 news_annotation 情绪子路由共用；缺失 / 非法时显式
raise :class:`RoutePolicyIncompleteError`，不得由消费侧静默回退构造默认
（否则用户保存的 0 重试 / 禁用备用策略丢失而调用仍报成功，R21）。
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

# 重试预算域（与 utils.config_models.ScenarioRouteConfig 的 ge=0 / le=2 一致）
_ROUTE_RETRIES_MIN: Final[int] = 0
_ROUTE_RETRIES_MAX: Final[int] = 2

# 调度服务注册点（R1：services 不 import app；bootstrap 组装后注入 provider）。
_scheduling_services_provider: Callable[[], dict[str, Any] | None] | None = None


def set_scheduling_services_provider(provider: Callable[[], dict[str, Any] | None] | None) -> None:
    """注册 / 清除调度服务 provider（app.bootstrap 组装完成后调用）。"""

    global _scheduling_services_provider
    _scheduling_services_provider = provider


def get_scheduling_services() -> dict[str, Any] | None:
    """读取当前调度服务（未注册 / 未组装返回 None，走旧链）。"""

    return _scheduling_services_provider() if _scheduling_services_provider is not None else None


class RoutePolicyIncompleteError(ValueError):
    """路由快照缺少 / 携非法可执行策略字段（D13-001）。

    显式失败而非由 Executor 静默回退构造默认：用户保存的 0 重试 / 禁用备用
    策略在分派侧丢失后，消费侧按默认继续执行会把「策略未生效」伪装成成功
    （R21）。调用方（三入口 helper）按分派异常阻断旧链并传出，不降级放行。
    """


def build_route_snapshot(
    route: Mapping[str, Any],
    *,
    scenario_id: str,
    candidates: tuple[Mapping[str, Any], ...],
) -> dict[str, Any]:
    """构造冻结路由条目：一次携带 Executor 消费的全部可执行策略字段（D13-001）。

    两条分派路径（:func:`resolve_executor` 主路由 / ``news_classifier`` 情绪
    子路由）共用本函数，任何路径不得自行拼装路由 dict——否则丢键后 Executor
    回退构造默认，0 重试 / 禁用 fallback 不生效而日志仍报成功。

    ``transient_retries``（int 0..2）/ ``fallback_enabled``（bool）/
    ``total_timeout_seconds``（> 0）缺失或非法 → raise
    :class:`RoutePolicyIncompleteError`（显式失败，不静默回退默认）。
    bool 是 int 子类，重试预算显式排除，避免 True 被当作 1 重试。
    白名单外的配置键（草稿态 / 展示字段 / 未知新键）不进入执行快照。
    """

    retries = route.get("transient_retries")
    if (
        isinstance(retries, bool)
        or not isinstance(retries, int)
        or not (_ROUTE_RETRIES_MIN <= retries <= _ROUTE_RETRIES_MAX)
    ):
        raise RoutePolicyIncompleteError(
            f"route {scenario_id!r}: transient_retries missing or invalid ({retries!r}); "
            f"expected int in [{_ROUTE_RETRIES_MIN}, {_ROUTE_RETRIES_MAX}]"
        )
    fallback_enabled = route.get("fallback_enabled")
    if not isinstance(fallback_enabled, bool):
        raise RoutePolicyIncompleteError(
            f"route {scenario_id!r}: fallback_enabled missing or invalid ({fallback_enabled!r}); expected bool"
        )
    total_timeout_seconds = route.get("total_timeout_seconds")
    if (
        isinstance(total_timeout_seconds, bool)
        or not isinstance(total_timeout_seconds, int | float)
        or float(total_timeout_seconds) <= 0
    ):
        raise RoutePolicyIncompleteError(
            f"route {scenario_id!r}: total_timeout_seconds missing or invalid "
            f"({total_timeout_seconds!r}); expected positive number"
        )
    return {
        "scenario_id": scenario_id,
        "route_revision": int(route.get("route_revision", 1)),
        "candidates": candidates,
        "transient_retries": retries,
        "fallback_enabled": fallback_enabled,
        "total_timeout_seconds": float(total_timeout_seconds),
    }


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
    """按三条件分派新链；任一不满足返回 None（调用方走旧链）。

    命中时经 :func:`build_route_snapshot` 冻结完整路由策略；路由缺少 / 携非法
    可执行字段 → raise :class:`RoutePolicyIncompleteError`（D13-001 显式失败，
    不返回 None 降级旧链、不静默按默认策略继续）。
    """

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

    # scenario_routes.candidates 为有序 connection_id 列表，转 Executor 契约形态。
    # F16 预算身份在此冻结（快照语义：普通保存只影响后续请求）：usage_policy
    # 携带月度限额 → 候选携带限额与政策快照；单次预留金额由 Executor 按连接计价
    # 快照 + 请求 payload 估算（F19：dispatch 不持有 payload，不在本层伪造金额；
    # R21：未知成本不以 0 占位）。未配置限额 → usage_only（两 None，不伪装政策
    # 存在）。owner / 派发身份由 Executor per-invocation 生成（F23 fencing），
    # 不再以伪值占位。
    monthly_limit = (
        mm_snapshot.usage_policy.get("monthly_cny_limit") if isinstance(mm_snapshot.usage_policy, Mapping) else None
    )
    if monthly_limit is not None:
        budget_limit_cny: str | None = str(monthly_limit)
        budget_policy_snapshot: dict[str, Any] | None = {"monthly_cny_limit": str(monthly_limit)}
    else:
        budget_limit_cny = None
        budget_policy_snapshot = None
    candidates = tuple(
        {
            "candidate_index": index,
            "task_id": scenario_id,
            "connection_id": conn_id,
            "budget_limit_cny": budget_limit_cny,
            "budget_policy_snapshot": budget_policy_snapshot,
            "target_fingerprint": connection_targets.get(str(conn_id)),
            "purpose": scenario_id,
        }
        for index, conn_id in enumerate(route["candidates"])
    )

    # D13-001：路由条目经共享构造一次携带全部可执行策略字段（0 重试 / 禁用
    # fallback / 总时限），缺失或非法显式 RoutePolicyIncompleteError——
    # 不得省略后让 Executor 静默回退构造默认（策略丢失仍报成功，R21）。
    route_snapshot = build_route_snapshot(route, scenario_id=scenario_id, candidates=candidates)
    snapshot = ExecutionSnapshot(
        snapshot_id=mm_snapshot.snapshot_id,
        run_id=None,
        config_revision=mm_snapshot.config_revision,
        routes=(route_snapshot,),
        connections=mm_snapshot.connections,
        business_contract_versions=dict(_TASK_CONTRACT_VERSIONS),
        security_scope_version=mm_snapshot.security_scope_version,
        created_at=mm_snapshot.created_at,
    )
    return ScenarioDispatch(
        executor=executor,
        snapshot=snapshot,
        route_revision=int(route_snapshot["route_revision"]),
        total_timeout_seconds=float(route_snapshot["total_timeout_seconds"]),
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
