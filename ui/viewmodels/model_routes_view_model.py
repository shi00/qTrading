"""ModelRoutesViewModel — 场景配置路由草稿的 ViewModel（S5 §11.4）。

职责（reviews/模型统一.md §11.2 / §11.4）：
- 每场景独立草稿（``ScenarioRouteDraft``），切场景保留各自草稿并显示未保存数；
- 候选引用（有序）、添加 / 移除 / 上移 / 下移、撤销最后一次移动或移除；
- 只提交当前场景（不把其它未保存场景一起提交），经 ``ModelConfigurationService.save_draft``
  的 CAS 三态（committed / not_committed / commit_unknown）与 revision 冲突处置。

设计约束（CLAUDE.md §3.2 MVVM / docs/patterns/mvvm.md）：
- 不 import flet、不感知 locale；state 为 frozen dataclass + tuple 容器；
- 消息为 ``Message``（i18n key + 参数），View 渲染时按 locale 解析；
- 非单例 DI：``__init__`` 注入 ``ModelConfigurationService``（其内部对同步 IO 已经
  ``ThreadPoolManager`` offload，本 VM 不再重复包线程池）；
- R2：含 ``await`` 的方法 ``except asyncio.CancelledError: raise``，不被 e.g. Exception 吞没。
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from core.i18n import Message
from core.model_management_types import (
    ConfigurationSaveOutcome,
    ModelConfigurationConflictError,
    ModelManagementSnapshot,
)
from services.model_configuration_service import ModelConfigurationService
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_models import SCENARIO_IDS

#: 场景默认总时限（产品默认，与 utils.config_models._default_scenario_routes 一致）。
_DEFAULT_TIMEOUTS: dict[str, int] = {"news_annotation": 45, "stock_analysis": 180, "news_risk": 90}

#: save_state 取值。
_SAVE_IDLE = "idle"
_SAVE_SAVING = "saving"
_SAVE_COMMITTED = "committed"
_SAVE_CONFLICT = "conflict"
_SAVE_ERROR = "error"
_SAVE_UNKNOWN = "commit_unknown"


@dataclass(frozen=True, slots=True)
class ScenarioRouteDraft:
    """单个场景的路由草稿（可编辑副本，与已提交快照分离）。

    候选为连接 id 的有序元组；空候选合法（空列表合法保存，§3.2）。
    F14：独立情绪候选与执行参数入模（覆盖全部可编辑任务字段，
    草稿↔payload 往返不再有损；committed 场景不经有损草稿往返重建）。
    """

    scenario_id: str
    enabled: bool = False
    candidates: tuple[str, ...] = ()
    total_timeout_seconds: int = 60
    transient_retries: int = 1
    fallback_enabled: bool = True
    route_revision: int = 1
    sentiment_mode: str = "bundled"
    reuse_bundled_sentiment: bool = True
    sentiment_candidates: tuple[str, ...] = ()
    execution_options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectionOption:
    """候选选择器可选连接（含计量与能力，供 §11.4 选择器展示）。

    ``capabilities`` 为 (task_id, support_state) 元组，不兼容项据此标记而不隐藏。
    """

    connection_id: str
    name: str = ""
    adapter_kind: str = ""
    provider: str = ""
    model: str = ""
    enabled: bool = True
    billing_mode: str = ""
    billing_unit: str = ""
    connection_revision: int = 1
    capabilities: tuple[tuple[str, str], ...] = ()

    def capability_for(self, task_id: str) -> str:
        """返回该任务的能力状态（supported / unsupported / unknown），未知即 unknown。"""
        for tid, state in self.capabilities:
            if tid == task_id:
                return state
        return "unknown"


@dataclass(frozen=True)
class ModelRoutesState:
    """ModelRoutesViewModel 的不可变 state snapshot。"""

    routes: tuple[ScenarioRouteDraft, ...] = ()
    committed_routes: tuple[ScenarioRouteDraft, ...] = ()
    selected_scenario: str = SCENARIO_IDS[0]
    candidate_picker_open: bool = False
    available_connections: tuple[ConnectionOption, ...] = ()
    dirty_counts: tuple[tuple[str, int], ...] = ()
    undo_available: bool = False
    advanced_expanded: bool = False
    save_state: str = _SAVE_IDLE
    message: Message | None = None
    #: 本 VM 提交完成序号（F30）：仅 save committed 时递增，供宿主 effect 检测
    #: 「场景域已提交、连接 VM 需重基」（不用 config_revision——其在 not_committed /
    #: commit_unknown 回执下也会经 _absorb_revision 更新，会虚假触发重基）。
    commit_seq: int = 0


@dataclass(frozen=True, slots=True)
class _PendingUndo:
    """单级撤销记录：记录最近一次移动 / 移除以便逆操作回退（只改草稿）。"""

    scenario_id: str
    action: str  # "move_up" | "move_down" | "remove"
    connection_id: str
    index: int


def _draft_from_mapping(mapping: Mapping[str, Any]) -> ScenarioRouteDraft:
    """已提交场景路由 Mapping → 草稿（F14：全字段捕获，往返无损）。"""
    task_routes = mapping.get("task_routes") or {}
    sentiment = task_routes.get("sentiment") or {}
    return ScenarioRouteDraft(
        scenario_id=str(mapping["scenario_id"]),
        enabled=bool(mapping.get("enabled", False)),
        candidates=tuple(str(c) for c in (mapping.get("candidates") or ())),
        total_timeout_seconds=int(mapping.get("total_timeout_seconds", 60)),
        transient_retries=int(mapping.get("transient_retries", 1)),
        fallback_enabled=bool(mapping.get("fallback_enabled", True)),
        route_revision=int(mapping.get("route_revision", 1)),
        sentiment_mode=str(sentiment.get("mode", "bundled")),
        reuse_bundled_sentiment=bool(sentiment.get("reuse_bundled_sentiment", True)),
        sentiment_candidates=tuple(str(c) for c in (sentiment.get("candidates") or ())),
        execution_options=dict(mapping.get("execution_options") or {}),
    )


def _default_draft(scenario_id: str) -> ScenarioRouteDraft:
    return ScenarioRouteDraft(
        scenario_id=scenario_id,
        total_timeout_seconds=_DEFAULT_TIMEOUTS.get(scenario_id, 60),
    )


def _build_drafts(scenario_routes: tuple[Mapping[str, Any], ...]) -> tuple[ScenarioRouteDraft, ...]:
    """按固定场景顺序构建草稿（缺失场景补默认行，保证切场景恒有草稿）。"""
    by_id = {str(m["scenario_id"]): _draft_from_mapping(m) for m in scenario_routes}
    return tuple(by_id.get(sid) or _default_draft(sid) for sid in SCENARIO_IDS)


def _option_from_mapping(mapping: Mapping[str, Any]) -> ConnectionOption:
    billing = mapping.get("billing") or {}
    capabilities = mapping.get("capabilities") or {}
    return ConnectionOption(
        connection_id=str(mapping["connection_id"]),
        name=str(mapping.get("name", "")),
        adapter_kind=str(mapping.get("adapter_kind", "")),
        provider=str(mapping.get("provider", "")),
        model=str(mapping.get("model", "")),
        enabled=bool(mapping.get("enabled", True)),
        billing_mode=str(billing.get("mode", "")),
        billing_unit=str(billing.get("pricing_unit", "")),
        connection_revision=int(mapping.get("connection_revision", 1)),
        capabilities=tuple((str(k), str(v.get("support", "unknown"))) for k, v in capabilities.items()),
    )


def _pending_change_count(draft: tuple[str, ...], committed: tuple[str, ...]) -> int:
    """草稿相对已提交的未保存变更数。

    新增 / 移除按存在于单侧的候选项计数；仅顺序不同（集合一致）计 1。
    """
    if draft == committed:
        return 0
    added = [c for c in draft if c not in committed]
    removed = [c for c in committed if c not in draft]
    if not added and not removed:
        return 1
    return len(added) + len(removed)


def _compute_dirty_counts(
    routes: tuple[ScenarioRouteDraft, ...],
    committed_routes: tuple[ScenarioRouteDraft, ...],
) -> tuple[tuple[str, int], ...]:
    committed_by_id = {r.scenario_id: r.candidates for r in committed_routes}
    return tuple(
        (r.scenario_id, _pending_change_count(r.candidates, committed_by_id.get(r.scenario_id, ()))) for r in routes
    )


def _route_by_id(routes: tuple[ScenarioRouteDraft, ...], scenario_id: str) -> ScenarioRouteDraft | None:
    return next((r for r in routes if r.scenario_id == scenario_id), None)


def _replace_route(
    routes: tuple[ScenarioRouteDraft, ...],
    scenario_id: str,
    new_route: ScenarioRouteDraft,
) -> tuple[ScenarioRouteDraft, ...]:
    return tuple(new_route if r.scenario_id == scenario_id else r for r in routes)


def _route_to_payload(route: ScenarioRouteDraft) -> dict[str, Any]:
    """草稿 → 持久 schema 形态的 route dict（``ScenarioRouteConfig`` 字段）。

    F14：独立情绪候选与执行参数按草稿实值发出（不再强制情绪用主候选 / options={}）。
    """
    task_routes: dict[str, Any] = {}
    if route.scenario_id == "news_annotation":
        task_routes["sentiment"] = {
            "mode": route.sentiment_mode,
            "candidates": list(route.sentiment_candidates),
            "reuse_bundled_sentiment": route.reuse_bundled_sentiment,
        }
    return {
        "scenario_id": route.scenario_id,
        "enabled": route.enabled,
        "candidates": list(route.candidates),
        "execution_options": dict(route.execution_options),
        "total_timeout_seconds": route.total_timeout_seconds,
        "transient_retries": route.transient_retries,
        "fallback_enabled": route.fallback_enabled,
        "route_revision": route.route_revision,
        "task_routes": task_routes,
    }


def _semantic_equal(a: ScenarioRouteDraft, b: ScenarioRouteDraft) -> bool:
    """除 route_revision 外的全部可编辑任务字段逐项比较（F14 revision 语义判据）。"""
    return (
        a.enabled == b.enabled
        and a.candidates == b.candidates
        and a.execution_options == b.execution_options
        and a.total_timeout_seconds == b.total_timeout_seconds
        and a.transient_retries == b.transient_retries
        and a.fallback_enabled == b.fallback_enabled
        and a.sentiment_mode == b.sentiment_mode
        and a.sentiment_candidates == b.sentiment_candidates
        and a.reuse_bundled_sentiment == b.reuse_bundled_sentiment
    )


def _semantic_route_for_save(route: ScenarioRouteDraft, committed: ScenarioRouteDraft | None) -> ScenarioRouteDraft:
    """语义相对已提交基线变化才递增 route_revision（F14）；无变化保持原值不篡改。"""
    if committed is None or _semantic_equal(route, committed):
        return route
    return replace(route, route_revision=committed.route_revision + 1)


def _rebase_drafts(
    old_drafts: tuple[ScenarioRouteDraft, ...],
    old_committed: tuple[ScenarioRouteDraft, ...],
    new_committed: tuple[ScenarioRouteDraft, ...],
) -> tuple[ScenarioRouteDraft, ...]:
    """重基草稿（F30）：只前移基线，不覆盖用户正在编辑的分叉草稿。

    - 已分叉草稿（语义字段相对旧基线不等）逐字段保留，仅保持场景 id 对齐；
    - 未分叉草稿采用新基线行（含新 route_revision），避免基线前移后出现
      「内容等于新基线却仍标脏」的幻影 dirty；
    - 新基线缺失对应场景时（``new_committed`` 由 ``_build_drafts`` 生成、恒按
      SCENARIO_IDS 补默认行，此为防御性兜底）同样采用新基线行。
    """
    old_committed_by_id = {r.scenario_id: r for r in old_committed}
    new_committed_by_id = {r.scenario_id: r for r in new_committed}
    rebased: list[ScenarioRouteDraft] = []
    for draft in old_drafts:
        new_base = new_committed_by_id.get(draft.scenario_id)
        if new_base is None:
            rebased.append(draft)
            continue
        old_base = old_committed_by_id.get(draft.scenario_id)
        if old_base is not None and not _semantic_equal(draft, old_base):
            rebased.append(draft)  # 已分叉：保留用户草稿
        else:
            rebased.append(new_base)  # 未分叉：随新基线（消幻影 dirty）
    return tuple(rebased)


class ModelRoutesViewModel(ObservableViewModelMixin[ModelRoutesState]):
    """场景配置页签的 ViewModel（每场景独立草稿、候选引用、排序与撤销）。"""

    def __init__(self, service: ModelConfigurationService) -> None:
        self._service = service
        self._config_revision = 1
        self._undo: _PendingUndo | None = None
        # 原始快照元数据（用于重建 CAS payload，不进 state）
        self._raw_connections: tuple[Mapping[str, Any], ...] = ()
        self._usage_policy: Mapping[str, Any] = {}
        self._state = ModelRoutesState()
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def current_draft(self) -> ScenarioRouteDraft | None:
        """当前选中场景的草稿。"""
        return _route_by_id(self._state.routes, self._state.selected_scenario)

    def can_move_up(self, connection_id: str) -> bool:
        """候选是否可上移（首行对应按钮禁用）。"""
        route = self.current_draft()
        if route is None or connection_id not in route.candidates:
            return False
        return route.candidates.index(connection_id) > 0

    def can_move_down(self, connection_id: str) -> bool:
        """候选是否可下移（末行对应按钮禁用）。"""
        route = self.current_draft()
        if route is None or connection_id not in route.candidates:
            return False
        return route.candidates.index(connection_id) < len(route.candidates) - 1

    def risk_confirmation_binding(
        self, connection_id: str, task_id: str, risk_code: str
    ) -> tuple[int, str, str] | None:
        """风险确认绑定键：连接当前 revision + task_id + risk_code（证据变化才需重新确认）。

        连接不存在返回 None（不臆造 revision）。
        """
        option = next((o for o in self._state.available_connections if o.connection_id == connection_id), None)
        if option is None:
            return None
        return (option.connection_revision, task_id, risk_code)

    # ------------------------------------------------------------------
    # 加载
    # ------------------------------------------------------------------

    async def load(self) -> None:
        """读取已提交快照，构建每场景草稿与候选选择器选项。"""
        try:
            snapshot: ModelManagementSnapshot = await self._service.get_snapshot()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            self._set_state(save_state=_SAVE_ERROR, message=Message("model_routes_load_failed"))
            return

        drafts = _build_drafts(snapshot.scenario_routes)
        options = tuple(_option_from_mapping(c) for c in snapshot.connections)
        self._config_revision = snapshot.config_revision
        self._raw_connections = snapshot.connections
        self._usage_policy = snapshot.usage_policy
        self._undo = None
        self._set_state(
            routes=drafts,
            committed_routes=drafts,
            available_connections=options,
            dirty_counts=_compute_dirty_counts(drafts, drafts),
            undo_available=False,
            save_state=_SAVE_IDLE,
            message=None,
        )

    async def rebase_snapshot(self) -> bool:
        """重基到最新已提交快照（F30：就地重基，保留未提交草稿）。

        触发方：宿主协调 effect（连接域提交后）与面板手动刷新入口。语义：
        - 只刷新 CAS 基线（``_config_revision`` / raw 元数据 / committed_routes）与
          候选选择器选项，不覆盖用户正在编辑的分叉草稿（``_rebase_drafts``）；
        - 冲突 / 失败态就地恢复（conflict / error → idle），commit_unknown 与 saving
          不动（F31 回执语义与在途提交不受重基干扰）；
        - 撤销记录清除（重基后撤销语义失效，非丢草稿）。

        Returns:
            ``True`` 表示重基成功；``False`` 表示读快照失败（不伪装成功，R21）。
        """
        self._track_inflight_cancellable()  # F40：只读刷新，页面卸载可安全取消
        try:
            snapshot: ModelManagementSnapshot = await self._service.get_snapshot()
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception:
            return False

        new_committed = _build_drafts(snapshot.scenario_routes)
        rebased = _rebase_drafts(self._state.routes, self._state.committed_routes, new_committed)
        options = tuple(_option_from_mapping(c) for c in snapshot.connections)
        self._config_revision = snapshot.config_revision
        self._raw_connections = snapshot.connections
        self._usage_policy = snapshot.usage_policy
        self._undo = None
        changes: dict[str, Any] = {
            "routes": rebased,
            "committed_routes": new_committed,
            "available_connections": options,
            "dirty_counts": _compute_dirty_counts(rebased, new_committed),
            "undo_available": False,
        }
        if self._state.save_state in (_SAVE_CONFLICT, _SAVE_ERROR):
            # 冲突 / 失败态已由新基线解决（重试即以新 revision 提交）；saving /
            # commit_unknown / committed 保留原状态机
            changes["save_state"] = _SAVE_IDLE
            changes["message"] = None
        self._set_state(**changes)
        return True

    # ------------------------------------------------------------------
    # 场景切换 / 选择器
    # ------------------------------------------------------------------

    def select_scenario(self, scenario_id: str) -> None:
        """切换场景：保留各自草稿与未保存计数，仅改变当前选中。"""
        if scenario_id not in SCENARIO_IDS or scenario_id == self._state.selected_scenario:
            return
        self._set_state(selected_scenario=scenario_id)

    def open_candidate_picker(self) -> None:
        self._set_state(candidate_picker_open=True)

    def close_candidate_picker(self) -> None:
        self._set_state(candidate_picker_open=False)

    # ------------------------------------------------------------------
    # 候选编辑
    # ------------------------------------------------------------------

    def add_candidate(self, connection_id: str) -> None:
        """确认选择追加末尾；已选连接禁用重复添加（不复制连接）。"""
        route = self.current_draft()
        if route is None or connection_id in route.candidates:
            return
        self._apply_current(route, (*route.candidates, connection_id))

    def remove_candidate(self, connection_id: str) -> None:
        """移除引用（不删除连接定义）；可撤销。"""
        route = self.current_draft()
        if route is None or connection_id not in route.candidates:
            return
        index = route.candidates.index(connection_id)
        self._undo = _PendingUndo(route.scenario_id, "remove", connection_id, index)
        new_candidates = route.candidates[:index] + route.candidates[index + 1 :]
        self._apply_current(route, new_candidates, undo_available=True)

    def move_up(self, connection_id: str) -> None:
        """上移候选；首行时对应按钮禁用（no-op）。"""
        route = self.current_draft()
        if route is None or connection_id not in route.candidates:
            return
        index = route.candidates.index(connection_id)
        if index == 0:
            return
        candidates = list(route.candidates)
        candidates[index - 1], candidates[index] = candidates[index], candidates[index - 1]
        self._undo = _PendingUndo(route.scenario_id, "move_up", connection_id, index)
        self._apply_current(route, tuple(candidates), undo_available=True)

    def move_down(self, connection_id: str) -> None:
        """下移候选；末行时对应按钮禁用（no-op）。"""
        route = self.current_draft()
        if route is None or connection_id not in route.candidates:
            return
        index = route.candidates.index(connection_id)
        if index >= len(route.candidates) - 1:
            return
        candidates = list(route.candidates)
        candidates[index + 1], candidates[index] = candidates[index], candidates[index + 1]
        self._undo = _PendingUndo(route.scenario_id, "move_down", connection_id, index)
        self._apply_current(route, tuple(candidates), undo_available=True)

    def undo_last_move(self) -> None:
        """撤销最后一次移动 / 移除（单级，只改草稿）；无记录即 no-op。"""
        if self._undo is None:
            return
        undo = self._undo
        self._undo = None
        route = _route_by_id(self._state.routes, undo.scenario_id)
        if route is None:
            self._set_state(undo_available=False)
            return
        candidates = list(route.candidates)
        if undo.action == "remove":
            if undo.connection_id not in candidates:
                index = min(undo.index, len(candidates))
                candidates.insert(index, undo.connection_id)
        elif undo.action == "move_up":
            # 逆操作：符号回头部方向
            if undo.connection_id in candidates:
                index = candidates.index(undo.connection_id)
                if index < len(candidates) - 1:
                    candidates[index], candidates[index + 1] = candidates[index + 1], candidates[index]
        elif undo.action == "move_down":
            if undo.connection_id in candidates:
                index = candidates.index(undo.connection_id)
                if index > 0:
                    candidates[index - 1], candidates[index] = candidates[index], candidates[index - 1]
        new_route = replace(route, candidates=tuple(candidates))
        new_routes = _replace_route(self._state.routes, undo.scenario_id, new_route)
        self._set_state(
            routes=new_routes,
            dirty_counts=_compute_dirty_counts(new_routes, self._state.committed_routes),
            undo_available=False,
        )

    def toggle_advanced(self) -> None:
        self._set_state(advanced_expanded=not self._state.advanced_expanded)

    # ------------------------------------------------------------------
    # 保存（只提交当前场景）
    # ------------------------------------------------------------------

    async def save_current_scenario(self) -> None:
        """CAS 提交当前场景草稿；其它场景以已提交值入 payload，不提交其未保存草稿。"""
        if self._state.save_state == _SAVE_SAVING:
            return
        scenario_id = self._state.selected_scenario
        route = _route_by_id(self._state.routes, scenario_id)
        if route is None:
            return

        self._set_state(save_state=_SAVE_SAVING, message=Message("model_routes_saving"))
        operation_id = str(uuid.uuid4())
        try:
            outcome = await self._service.save_draft(
                self.build_payload(scenario_id), self._config_revision, operation_id
            )
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except ModelConfigurationConflictError:
            self._set_state(save_state=_SAVE_CONFLICT, message=Message("model_routes_save_conflict"))
            return
        except Exception:
            self._set_state(save_state=_SAVE_ERROR, message=Message("model_routes_save_failed"))
            return

        self._absorb_revision(outcome)
        if outcome.status == "committed":
            # F14：回写含语义递增后的 route_revision，保证后续保存从新基线单调递增
            committed = _replace_route(self._state.committed_routes, scenario_id, self._save_route_for(scenario_id))
            self._undo = None
            self._set_state(
                committed_routes=committed,
                dirty_counts=_compute_dirty_counts(self._state.routes, committed),
                undo_available=False,
                save_state=_SAVE_COMMITTED,
                message=Message("model_routes_save_committed"),
                # F30：提交完成序号递增，宿主 effect 检测后重基连接 VM（刷新其 _raw_routes，
                # 防止下次连接保存以陈旧路由回退本次修订）
                commit_seq=self._state.commit_seq + 1,
            )
        elif outcome.status == "commit_unknown":
            self._set_state(save_state=_SAVE_UNKNOWN, message=Message("model_routes_save_unknown"))
        else:  # not_committed：旧配置仍有效，保留草稿
            self._set_state(save_state=_SAVE_ERROR, message=Message("model_routes_save_not_committed"))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _apply_current(
        self,
        route: ScenarioRouteDraft,
        candidates: tuple[str, ...],
        *,
        undo_available: bool | None = None,
    ) -> None:
        new_route = replace(route, candidates=candidates)
        new_routes = _replace_route(self._state.routes, route.scenario_id, new_route)
        changes: dict[str, Any] = {
            "routes": new_routes,
            "dirty_counts": _compute_dirty_counts(new_routes, self._state.committed_routes),
        }
        if undo_available is not None:
            changes["undo_available"] = undo_available
        self._set_state(**changes)

    def _absorb_revision(self, outcome: ConfigurationSaveOutcome) -> None:
        new_revision = (
            outcome.committed_revision if outcome.committed_revision is not None else outcome.current_revision
        )
        if new_revision is not None:
            self._config_revision = new_revision

    def _save_route_for(self, scenario_id: str) -> ScenarioRouteDraft:
        """当前场景草稿的「保存形态」：语义相对已提交基线变化时递增 route_revision。

        草稿不存在时回退已提交行（调用方保证场景存在，防御性兜底）。
        """
        draft = _route_by_id(self._state.routes, scenario_id)
        if draft is None:
            draft = _route_by_id(self._state.committed_routes, scenario_id)
        committed = _route_by_id(self._state.committed_routes, scenario_id)
        return _semantic_route_for_save(draft, committed) if draft is not None else _default_draft(scenario_id)

    def all_routes_payload(self) -> list[dict[str, Any]]:
        """全部场景草稿序列化（§11.6 向导原子提交本阶段全部差异）。

        F14：每行按语义变化递增 route_revision，未编辑场景保持原 revision。
        """
        committed = {r.scenario_id: r for r in self._state.committed_routes}
        return [
            _route_to_payload(_semantic_route_for_save(route, committed.get(route.scenario_id)))
            for route in self._state.routes
        ]

    def build_payload(self, scenario_id: str) -> dict[str, Any]:
        """重建 ``model_management`` payload：当前场景用草稿，其余用已提交值。

        迁移子域（migration_state / migration_transition）不入草稿 payload，
        由迁移专用命令独占、CAS 以磁盘现值回填（F07）。
        F14：其余场景经无损草稿模型序列化（独立情绪候选 / 执行参数保留），
        当前场景语义变化递增 route_revision。
        """
        committed = {r.scenario_id: r for r in self._state.committed_routes}
        routes: list[dict[str, Any]] = []
        for sid in SCENARIO_IDS:
            route = committed.get(sid)
            if route is None:
                continue
            routes.append(_route_to_payload(route))
        # 用当前场景草稿覆盖对应行（只提交当前场景；F14 语义递增 revision）
        save_route = self._save_route_for(scenario_id)
        routes = [  # 保持 SCENARIO_IDS 稳定顺序
            _route_to_payload(save_route) if r["scenario_id"] == scenario_id else r for r in routes
        ]
        return {
            "schema_version": 1,
            "config_revision": self._config_revision,
            "connections": [dict(c) for c in self._raw_connections],
            "scenario_routes": routes,
            "usage_policy": dict(self._usage_policy),
        }
