"""ResearchComparisonViewModel — 多模型对比视图（F37）。

职责边界（CLAUDE.md §3.2 MVVM）：

- **只读投影**：组状态、逐路状态/缺口/费用全部来自服务层
  ``AgentService.read_comparison`` 的只读投影；未知/未计价如实为 ``None``，
  不填 0（R21）。
- **纯状态 + 命令**：VM 不 import flet、不调 ``page.update()``、不感知 locale；
  state 为 frozen dataclass，i18n 只产出 key。
- **连接目录复用**：两路可选连接来自既有模型连接目录
  （``ModelConfigurationService.get_snapshot``），不另建读取通道；不合法连接在
  选择阶段即明确拒绝（禁用 / 非云端聊天 / 无合法外发目标）。
- **共享 MCP 断线影响清单**：复用服务层 ``plan_group_stop`` 的逐路决策，只标
  真正受影响的路；已完成成果（``preserve_result``）不误标。
- **单路重试 / 停止整组**：直接转发 ``retry_comparison_lane`` /
  ``stop_comparison_group``，不重跑其他路、不伪造回执。

R2：所有含 await 的方法显式 ``except asyncio.CancelledError: raise``。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from core.agent_types import AgentValidationError, Mode, Purpose, SessionReadQuery, stable_id
from core.model_management_types import ModelManagementSnapshot, normalize_egress_target
from services.model_configuration_service import ModelConfigurationService
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

if TYPE_CHECKING:
    # N01：Agent 栈（含 pydantic_ai）仅在类型检查时导入；运行时按需局部导入，
    # 依赖缺失时主壳与对比页壳仍可导入。
    from services.agent_service.comparison import (
        ComparisonGroupView,
        ComparisonLaneSelection,
        ComparisonReadQuery,
        ComparisonSubmissionRequest,
        FrozenComparisonGroup,
    )

logger = logging.getLogger(__name__)

#: 桌面单用户域（与 app.agent_wiring 默认 user_scope 一致；UI 层不 import app）。
_DEFAULT_USER_SCOPE: Final[str] = "local-desktop"

UNAVAILABLE_NOT_WIRED: Final[str] = "agent_comparison_not_wired"
UNAVAILABLE_NOT_READY: Final[str] = "agent_comparison_not_ready"

#: 两路槽位（F37：至少两个具体连接才构成对比）。
LANE_SLOTS: Final[tuple[str, str]] = ("a", "b")

#: 并列 / 表格两种对照视图。
VIEW_SIDE_BY_SIDE: Final[str] = "side_by_side"
VIEW_TABLE: Final[str] = "table"
VIEW_MODES: Final[tuple[str, str]] = (VIEW_SIDE_BY_SIDE, VIEW_TABLE)

#: 研究模式（与既有首问模式同一域）。
COMPARISON_MODES: Final[tuple[str, ...]] = ("discuss", "plan", "execute")

#: 组停止原因（共享 MCP 断线按「共享输入停止」语义复用服务层停止计划）。
SHARED_MCP_DISCONNECT_REASON: Final[str] = "shared_mcp_disconnected"

#: 云端聊天连接 kind（本地 kinds 需设备内核验，对比入口明确拒绝）。
_ADAPTER_LITELLM: Final[str] = "litellm_chat"

_GROUP_STATE_KEYS: Final[Mapping[str, str]] = {
    "queued": "agent_comparison_group_queued",
    "running": "agent_comparison_group_running",
    "complete": "agent_comparison_group_complete",
    "partial": "agent_comparison_group_partial",
    "failed": "agent_comparison_group_failed",
    "ended": "agent_comparison_group_ended",
    "closed_unknown": "agent_comparison_group_closed_unknown",
}

_GROUP_COVERAGE_KEYS: Final[Mapping[str, str]] = {
    "full": "agent_comparison_coverage_full",
    "partial": "agent_comparison_coverage_partial",
    "none": "agent_comparison_coverage_none",
    "unknown": "agent_comparison_coverage_unknown",
}

_LANE_STATE_KEYS: Final[Mapping[str, str]] = {
    "queued": "agent_comparison_lane_state_queued",
    "running": "agent_comparison_lane_state_running",
    "waiting_dependency": "agent_comparison_lane_state_waiting_dependency",
    "waiting_user": "agent_comparison_lane_state_waiting_user",
    "pausing": "agent_comparison_lane_state_pausing",
    "paused": "agent_comparison_lane_state_paused",
    "ending": "agent_comparison_lane_state_ending",
    "unknown": "agent_comparison_lane_state_unknown",
    "ended": "agent_comparison_lane_state_ended",
    "failed": "agent_comparison_lane_state_failed",
    "complete": "agent_comparison_lane_state_complete",
    "partial": "agent_comparison_lane_state_partial",
    "planned": "agent_comparison_lane_state_planned",
    "withdrawn": "agent_comparison_lane_state_withdrawn",
    "closed_unknown": "agent_comparison_lane_state_closed_unknown",
}

_LANE_REASON_KEYS: Final[Mapping[str, str]] = {
    "not_started": "agent_comparison_reason_not_started",
    "started_no_fact": "agent_comparison_reason_started_no_fact",
    "dispatch_failed": "agent_comparison_reason_dispatch_failed",
    "accept_failed": "agent_comparison_reason_accept_failed",
    "result_unconfirmed": "agent_comparison_reason_result_unconfirmed",
    "complete": "agent_comparison_reason_complete",
    "partial_result": "agent_comparison_reason_partial_result",
    "failed_no_result": "agent_comparison_reason_failed_no_result",
    "withdrawn": "agent_comparison_reason_withdrawn",
    "preserve_result": "agent_comparison_reason_preserve_result",
}

_LANE_GAP_KEYS: Final[Mapping[str, str]] = {
    "no_terminal_fact": "agent_comparison_gap_no_terminal_fact",
    "result_unconfirmed": "agent_comparison_gap_result_unconfirmed",
    "missing_data": "agent_comparison_gap_missing_data",
    "no_result": "agent_comparison_gap_no_result",
}

_COST_STATE_KEYS: dict[str, str] = {}


def _cost_state_keys() -> Mapping[str, str]:
    """费用状态 → i18n key（N01：comparison 常量惰性导入，Agent 依赖缺失时本模块仍可导入）。"""

    if not _COST_STATE_KEYS:
        from services.agent_service.comparison import (
            COMPARISON_COST_FAILED,
            COMPARISON_COST_KNOWN,
            COMPARISON_COST_PARTIAL,
            COMPARISON_COST_RESERVED,
            COMPARISON_COST_UNKNOWN,
        )

        _COST_STATE_KEYS.update(
            {
                COMPARISON_COST_KNOWN: "agent_comparison_cost_state_known",
                COMPARISON_COST_PARTIAL: "agent_comparison_cost_state_partial",
                COMPARISON_COST_RESERVED: "agent_comparison_cost_state_reserved",
                COMPARISON_COST_UNKNOWN: "agent_comparison_cost_state_unknown",
                COMPARISON_COST_FAILED: "agent_comparison_cost_state_failed",
                "unread": "agent_comparison_cost_state_unread",
            }
        )
    return _COST_STATE_KEYS


_MCP_IMPACT_KEYS: Final[Mapping[str, str]] = {
    "withdraw": "agent_comparison_impact_withdraw",
    "stop_new_dispatch": "agent_comparison_impact_stop_new_dispatch",
    "verify_unknown": "agent_comparison_impact_verify_unknown",
    "preserve_result": "agent_comparison_impact_unaffected",
    "unknown": "agent_comparison_impact_unknown",
}


class ComparisonServicePort(Protocol):
    """对比相关服务面（``AgentService`` 子集；宿主注入，UI 不 import app）。"""

    async def submit_comparison(self, request: ComparisonSubmissionRequest) -> Any: ...

    async def read_comparison(self, query: ComparisonReadQuery) -> ComparisonGroupView: ...

    async def retry_comparison_lane(
        self,
        query: ComparisonReadQuery,
        lane_ref: str,
        *,
        expected_revision: int,
        node_id: str = ...,
        source_display_name: str = ...,
    ) -> Any: ...

    async def stop_comparison_group(self, query: ComparisonReadQuery, *, reason: str = ...) -> Any: ...

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> Any: ...


class ConnectionCatalogPort(Protocol):
    """既有模型连接目录读取面（``ModelConfigurationService`` 子集）。"""

    async def get_snapshot(self) -> ModelManagementSnapshot: ...


@dataclass(frozen=True, slots=True)
class ComparisonConnectionOption:
    """可选择的一路连接（目录只读投影 + 资格判定）。"""

    connection_id: str
    display_name: str
    model: str
    revision: int
    eligible: bool
    reject_key: str | None
    target_fingerprint: str | None


@dataclass(frozen=True, slots=True)
class ComparisonLaneRow:
    """一路只读投影（状态/缺口/费用/耗时/受影响标记）。"""

    lane_ref: str
    connection_id: str
    display_name: str
    display_state: str | None
    reliable_result: bool | None
    state_key: str
    reason: str
    reason_key: str
    gap_key: str | None
    cost_state: str
    cost_key: str
    known_cny: Decimal | None
    reserved_cny: Decimal | None
    unpriced_attempts: int | None
    active_ms: int | None
    retryable: bool
    mcp_impact: str | None
    mcp_impact_key: str | None


@dataclass(frozen=True, slots=True)
class ComparisonGroupSummary:
    """组级只读投影（计数/覆盖/组费用状态；未知不拼成完整总费用）。"""

    group_ref: str
    group_state: str
    state_key: str
    coverage_key: str
    ended: bool
    selected_count: int
    complete_count: int
    partial_count: int
    failed_count: int
    pending_count: int
    not_started_count: int
    unconfirmed_count: int
    cost_state: str
    cost_key: str
    known_cny: Decimal | None
    reserved_cny: Decimal | None
    unpriced_attempts: int


@dataclass(frozen=True, slots=True)
class ResearchComparisonState:
    """对比面板不可变 state snapshot。"""

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED
    connections: tuple[ComparisonConnectionOption, ...] = ()
    lane_a: str | None = None
    lane_b: str | None = None
    text: str = ""
    mode: str = "execute"
    view_mode: str = VIEW_SIDE_BY_SIDE
    group: ComparisonGroupSummary | None = None
    lanes: tuple[ComparisonLaneRow, ...] = ()
    session_id: str | None = None
    group_ref: str | None = None
    loading: bool = False
    submitting: bool = False
    retrying_lane: str | None = None
    stopping: bool = False
    message_key: str | None = None
    error_key: str | None = None
    error_code: str | None = None
    selection_error_key: str | None = None
    mcp_reported: bool = False


def build_comparison_connection_options(
    snapshot: ModelManagementSnapshot,
) -> tuple[ComparisonConnectionOption, ...]:
    """既有连接目录快照 → 可选路列表（不合法连接带拒绝 key，不静默隐藏）。

    对比路必须能冻结非空外发目标（``normalize_egress_target``）；本地适配器需
    设备内核验、凭据类适配器不可作对比路，一律明确拒绝而不是在提交时才发现。
    """

    options: list[ComparisonConnectionOption] = []
    for raw in snapshot.connections:
        if not isinstance(raw, Mapping):
            continue
        connection_id = raw.get("connection_id")
        if not isinstance(connection_id, str) or not connection_id:
            continue
        display_name = raw.get("name")
        name = display_name if isinstance(display_name, str) and display_name else connection_id
        model = raw.get("model")
        revision = _positive_int(raw.get("connection_revision"))
        adapter_kind = raw.get("adapter_kind")
        enabled = bool(raw.get("enabled", True))
        target = normalize_egress_target(raw.get("api_base"))
        reject_key: str | None = None
        if not enabled:
            reject_key = "agent_comparison_connection_disabled"
        elif adapter_kind != _ADAPTER_LITELLM:
            reject_key = "agent_comparison_connection_local_unsupported"
        elif target is None:
            reject_key = "agent_comparison_connection_no_target"
        elif revision is None:
            reject_key = "agent_comparison_connection_unknown"
        options.append(
            ComparisonConnectionOption(
                connection_id=connection_id,
                display_name=name,
                model=model if isinstance(model, str) else "",
                revision=revision or 0,
                eligible=reject_key is None,
                reject_key=reject_key,
                target_fingerprint=target,
            )
        )
    return tuple(options)


def _positive_int(value: object) -> int | None:
    """修订号读数：非 bool 正整数；其余返回 ``None``（未知，不填 1）。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _state_key(state: str | None) -> str:
    if state is None:
        return "agent_comparison_lane_state_unstarted"
    return _LANE_STATE_KEYS.get(state, "agent_comparison_lane_state_unknown")


def _cost_key(cost_state: str) -> str:
    return _cost_state_keys().get(cost_state, "agent_comparison_cost_state_unknown")


def _lane_row(lane: Any, *, active_ms: int | None, impact: str | None) -> ComparisonLaneRow:
    """``ComparisonLaneView`` → 展示行（费用未知保持 ``None``，R21）。"""

    from services.agent_service.comparison import COMPARISON_COST_UNKNOWN

    cost = lane.cost
    if cost is None:
        cost_state = "unread"
        known = None
        reserved = None
        unpriced = None
    else:
        cost_state = getattr(cost, "read_state", COMPARISON_COST_UNKNOWN)
        readable = cost_state == "ok"
        known = cost.known_cny if readable else None
        reserved = cost.reserved_cny if readable else None
        unpriced = cost.unpriced_attempts if readable else None
    return ComparisonLaneRow(
        lane_ref=lane.lane_ref,
        connection_id=lane.connection_id,
        display_name=lane.display_name,
        display_state=lane.display_state,
        reliable_result=lane.reliable_result,
        state_key=_state_key(lane.display_state),
        reason=lane.reason,
        reason_key=_LANE_REASON_KEYS.get(lane.reason, "agent_comparison_reason_result_unconfirmed"),
        gap_key=_LANE_GAP_KEYS.get(lane.gap) if lane.gap is not None else None,
        cost_state=cost_state,
        cost_key=_cost_key(cost_state),
        known_cny=known,
        reserved_cny=reserved,
        unpriced_attempts=unpriced,
        active_ms=active_ms,
        retryable=lane.reliable_result is False,
        mcp_impact=impact,
        mcp_impact_key=_MCP_IMPACT_KEYS.get(impact) if impact is not None else None,
    )


def _group_summary(view: ComparisonGroupView) -> ComparisonGroupSummary:
    """``ComparisonGroupView`` → 组摘要（未知费用保持 ``None``）。"""

    return ComparisonGroupSummary(
        group_ref=view.group_ref,
        group_state=view.group_state,
        state_key=_GROUP_STATE_KEYS.get(view.group_state, "agent_comparison_group_queued"),
        coverage_key=_GROUP_COVERAGE_KEYS.get(view.goal_coverage, "agent_comparison_coverage_unknown"),
        ended=view.ended,
        selected_count=view.selected_count,
        complete_count=view.complete_count,
        partial_count=view.partial_count,
        failed_count=view.failed_count,
        pending_count=view.pending_count,
        not_started_count=view.not_started_count,
        unconfirmed_count=view.unconfirmed_count,
        cost_state=view.cost_state,
        cost_key=_cost_key(view.cost_state),
        known_cny=view.known_cny,
        reserved_cny=view.reserved_cny,
        unpriced_attempts=view.unpriced_attempts,
    )


class ResearchComparisonViewModel(ObservableViewModelMixin[ResearchComparisonState]):
    """多模型对比视图 VM（选择两路 → 提交 → 只读投影 → 单路重试 / 停止整组）。"""

    def __init__(
        self,
        service: ComparisonServicePort | None,
        *,
        catalog: ConnectionCatalogPort | None = None,
        user_scope: str = _DEFAULT_USER_SCOPE,
        session_id: str | None = None,
        unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
    ) -> None:
        self._service = service
        self._catalog: ConnectionCatalogPort = (
            catalog if catalog is not None else cast(ConnectionCatalogPort, ModelConfigurationService())
        )
        self._user_scope = user_scope
        self._group: FrozenComparisonGroup | None = None
        self._scope_version = 0
        self._state = ResearchComparisonState(
            available=service is not None,
            unavailable_reason_key=unavailable_reason_key if service is None else "",
            session_id=session_id,
        )
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 选择 / 输入命令（纯状态）
    # ------------------------------------------------------------------

    def set_session(self, session_id: str | None) -> None:
        """切换目标会话；会话变化即丢弃前一组的投影（不跨会话混显）。"""

        target = session_id if session_id else None
        if target == self._state.session_id:
            return
        self._group = None
        self._set_state(
            session_id=target,
            group=None,
            lanes=(),
            group_ref=None,
            message_key=None,
            error_key=None,
            error_code=None,
            mcp_reported=False,
        )

    def set_lane(self, slot: str, connection_id: str | None) -> None:
        """选择/清空一路连接；不合法或与另一路重复时明确拒绝（不改写当前选择）。"""

        if slot not in LANE_SLOTS:
            return
        field = "lane_a" if slot == "a" else "lane_b"
        if not connection_id:
            self._set_state(**{field: None, "selection_error_key": None})
            return
        option = next((item for item in self._state.connections if item.connection_id == connection_id), None)
        if option is None:
            self._set_state(selection_error_key="agent_comparison_connection_unknown")
            return
        if not option.eligible:
            self._set_state(selection_error_key=option.reject_key or "agent_comparison_connection_unknown")
            return
        other = self._state.lane_b if slot == "a" else self._state.lane_a
        if other == connection_id:
            self._set_state(selection_error_key="agent_comparison_lane_duplicate")
            return
        self._set_state(**{field: connection_id, "selection_error_key": None})

    def set_text(self, text: str) -> None:
        self._set_state(text=text)

    def set_mode(self, mode: str) -> None:
        if mode in COMPARISON_MODES:
            self._set_state(mode=mode)

    def set_view_mode(self, view_mode: str) -> None:
        if view_mode in VIEW_MODES:
            self._set_state(view_mode=view_mode)

    # ------------------------------------------------------------------
    # 读取命令
    # ------------------------------------------------------------------

    async def load_connections(self) -> None:
        """读取既有连接目录（只读；读取失败如实报错，不展示空目录）。"""

        self._track_inflight_cancellable()
        self._set_state(error_key=None, error_code=None)
        try:
            snapshot = await self._catalog.get_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("[ComparisonVM] load connections failed: %s", type(exc).__name__)
            self._set_state(error_key="agent_comparison_connections_load_failed")
            return
        scope_version = getattr(snapshot, "security_scope_version", 0)
        self._scope_version = scope_version if isinstance(scope_version, int) and scope_version >= 0 else 0
        self._set_state(connections=build_comparison_connection_options(snapshot))

    async def open_group(self, session_id: str, group_ref: str) -> None:
        """按既有组身份打开只读投影（未持有完整冻结组时，受影响范围保持未知）。"""

        if not session_id or not group_ref:
            return
        self._group = None
        self._set_state(
            session_id=session_id,
            group_ref=group_ref,
            group=None,
            lanes=(),
            mcp_reported=False,
            message_key=None,
            error_key=None,
            error_code=None,
        )
        await self.refresh()

    async def refresh(self) -> None:
        """只读刷新当前组投影（逐路费用 + 耗时）；未知如实保留。"""

        if not self._ready_to_read() or self._state.loading:
            return
        self._track_inflight_cancellable()
        self._set_state(loading=True, error_key=None)
        try:
            await self._read_group_view()
        except asyncio.CancelledError:
            self._set_state(loading=False)
            raise
        except Exception as exc:
            self._set_state(loading=False, error_key="agent_comparison_error_generic", error_code=self._error_code(exc))
        else:
            self._set_state(loading=False)

    # ------------------------------------------------------------------
    # 写命令（受理 / 单路重试 / 停止整组）
    # ------------------------------------------------------------------

    async def submit(self) -> None:
        """提交两路对比（共同输入一次冻结）；受理失败逐路如实报码，不伪造已受理。"""

        service = self._service
        if service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        session_id = self._state.session_id
        if not session_id:
            self._set_state(selection_error_key="agent_comparison_no_session")
            return
        text = self._state.text.strip()
        if not text:
            self._set_state(selection_error_key="agent_comparison_text_required")
            return
        selections = self._selections()
        if selections is None or self._state.submitting:
            return
        self._track_inflight_cancellable()
        self._set_state(submitting=True, selection_error_key=None, message_key=None, error_key=None, error_code=None)
        from services.agent_service.comparison import (
            ComparisonSharedInput,
            ComparisonSubmissionRequest,
            freeze_comparison_group,
        )

        group: FrozenComparisonGroup | None = None
        try:
            expected_revision = await self._session_revision(session_id)
            command_id = stable_id(
                "comparison_group_command",
                [self._user_scope, session_id, text, *(lane.connection_id for lane in selections)],
            )
            shared = ComparisonSharedInput(text=text, mode=Mode(self._state.mode), purpose=Purpose.END_OF_DAY_RESEARCH)
            try:
                group = freeze_comparison_group(
                    user_scope=self._user_scope,
                    session_id=session_id,
                    source_module="ui",
                    group_command_id=command_id,
                    shared=shared,
                    lanes=selections,
                )
            except AgentValidationError as exc:
                self._set_state(
                    submitting=False,
                    selection_error_key="agent_comparison_invalid_selection",
                    error_code=exc.code,
                )
                return
            receipt = await service.submit_comparison(
                ComparisonSubmissionRequest(
                    user_scope=self._user_scope,
                    session_id=session_id,
                    source_module="ui",
                    group_command_id=command_id,
                    shared=shared,
                    lanes=selections,
                    expected_revision=expected_revision,
                )
            )
        except asyncio.CancelledError:
            self._set_state(submitting=False)
            raise
        except Exception as exc:
            self._set_state(
                submitting=False,
                error_key="agent_comparison_error_generic",
                error_code=self._error_code(exc),
            )
            return
        accepted = bool(getattr(receipt, "all_accepted", False))
        self._group = group
        self._set_state(
            submitting=False,
            group_ref=group.group_ref,
            mcp_reported=False,
            message_key="agent_comparison_submitted" if accepted else "agent_comparison_submit_partial",
        )
        await self.refresh()

    async def retry_lane(self, lane_ref: str) -> None:
        """只重试某一路（服务层只放行可靠无成果的路；其余拒绝并如实报码）。"""

        service = self._service
        if service is None or not self._ready_to_read():
            self._set_state(error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        if self._state.retrying_lane is not None:
            return
        session_id = cast(str, self._state.session_id)
        self._track_inflight_cancellable()
        self._set_state(retrying_lane=lane_ref, message_key=None, error_key=None, error_code=None)
        try:
            expected_revision = await self._session_revision(session_id)
            outcome = await service.retry_comparison_lane(
                self._query(),
                lane_ref,
                expected_revision=expected_revision,
            )
        except asyncio.CancelledError:
            self._set_state(retrying_lane=None)
            raise
        except Exception as exc:
            self._set_state(
                retrying_lane=None,
                error_key="agent_comparison_error_generic",
                error_code=self._error_code(exc),
            )
            return
        error_code = getattr(outcome, "error_code", None)
        self._set_state(
            retrying_lane=None,
            message_key="agent_comparison_retry_rejected" if error_code else "agent_comparison_retry_submitted",
            error_code=error_code if isinstance(error_code, str) else None,
        )
        await self.refresh()

    async def stop_group(self) -> None:
        """停止整组（服务层先关派发门再逐路撤单；未核实路如实标未确认）。"""

        service = self._service
        if service is None or not self._ready_to_read():
            self._set_state(error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        if self._state.stopping:
            return
        self._track_inflight_cancellable()
        self._set_state(stopping=True, message_key=None, error_key=None, error_code=None)
        from services.agent_service.comparison import STOP_REASON_USER_ENDED_GROUP

        try:
            outcome = await service.stop_comparison_group(
                self._query(),
                reason=STOP_REASON_USER_ENDED_GROUP,
            )
        except asyncio.CancelledError:
            self._set_state(stopping=False)
            raise
        except Exception as exc:
            self._set_state(
                stopping=False,
                error_key="agent_comparison_error_generic",
                error_code=self._error_code(exc),
            )
            return
        verified = bool(getattr(outcome, "all_verified", False))
        self._set_state(
            stopping=False,
            message_key="agent_comparison_stopped" if verified else "agent_comparison_stopped_unverified",
        )
        await self.refresh()

    def report_shared_mcp_disconnect(self) -> None:
        """记录共享 MCP 断线并列出受影响 lane（复用服务层停止计划，不误标已完成路）。"""

        if not self._ready_to_read():
            self._set_state(message_key="agent_comparison_mcp_unavailable")
            return
        impact = self._mcp_impact()
        if impact is None:
            self._set_state(mcp_reported=True, message_key="agent_comparison_mcp_unavailable")
            return
        lanes = tuple(
            replace(
                row,
                mcp_impact=impact.get(row.lane_ref, "unknown"),
                mcp_impact_key=_MCP_IMPACT_KEYS.get(impact.get(row.lane_ref, "unknown")),
            )
            for row in self._state.lanes
        )
        self._set_state(mcp_reported=True, lanes=lanes, message_key="agent_comparison_mcp_reported")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _ready_to_read(self) -> bool:
        return bool(
            self._service is not None and self._state.available and self._state.session_id and self._state.group_ref
        )

    def _query(self) -> ComparisonReadQuery:
        from services.agent_service.comparison import ComparisonReadQuery

        return ComparisonReadQuery(
            user_scope=self._user_scope,
            session_id=cast(str, self._state.session_id),
            group_ref=cast(str, self._state.group_ref),
            include_cost=True,
        )

    def _selections(self) -> tuple[ComparisonLaneSelection, ...] | None:
        state = self._state
        if not state.lane_a or not state.lane_b or state.lane_a == state.lane_b:
            self._set_state(selection_error_key="agent_comparison_need_two_lanes")
            return None
        from services.agent_service.comparison import ComparisonLaneSelection

        by_id = {option.connection_id: option for option in state.connections}
        selections: list[ComparisonLaneSelection] = []
        for connection_id in (state.lane_a, state.lane_b):
            option = by_id.get(connection_id)
            if option is None or not option.eligible or option.target_fingerprint is None:
                self._set_state(selection_error_key="agent_comparison_connection_unknown")
                return None
            selections.append(
                ComparisonLaneSelection(
                    connection_id=option.connection_id,
                    connection_revision=option.revision,
                    target_fingerprint=option.target_fingerprint,
                    connection_purpose=Purpose.END_OF_DAY_RESEARCH.value,
                    scope_version=self._scope_version,
                    display_name=option.display_name,
                )
            )
        return tuple(selections)

    async def _read_group_view(self) -> None:
        service = cast(ComparisonServicePort, self._service)
        session_id = cast(str, self._state.session_id)
        group_ref = cast(str, self._state.group_ref)
        durations = await self._lane_durations(session_id, group_ref)
        view = await service.read_comparison(self._query())
        impact = self._mcp_impact() if self._state.mcp_reported else None
        lanes = tuple(
            _lane_row(
                lane,
                active_ms=durations.get(lane.lane_ref),
                impact=(impact.get(lane.lane_ref, "unknown") if impact is not None else None),
            )
            for lane in view.lanes
        )
        self._set_state(group=_group_summary(view), lanes=lanes)

    async def _lane_durations(self, session_id: str, group_ref: str) -> dict[str, int]:
        """逐路耗时（``active_ms``）只读投影；读取失败返回空表（未知，不填 0）。"""

        service = cast(ComparisonServicePort, self._service)
        try:
            read = await service.read_session(
                SessionReadQuery(session_id=session_id, limit=50), user_scope=self._user_scope
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[ComparisonVM] lane duration read failed: %s", type(exc).__name__)
            return {}
        durations: dict[str, int] = {}
        for row in getattr(read, "executions", ()) or ():
            if getattr(row, "comparison_group_ref", None) != group_ref:
                continue
            lane_ref = getattr(row, "comparison_lane_ref", None)
            active_ms = getattr(row, "active_ms", None)
            if isinstance(lane_ref, str) and isinstance(active_ms, int) and not isinstance(active_ms, bool):
                durations[lane_ref] = active_ms
        return durations

    async def _session_revision(self, session_id: str) -> int:
        """会话稿版（受理 CAS 基线）；读取失败返回 0（服务层按冲突如实拒绝）。"""

        service = cast(ComparisonServicePort, self._service)
        try:
            read = await service.read_session(
                SessionReadQuery(session_id=session_id, limit=1), user_scope=self._user_scope
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[ComparisonVM] session revision read failed: %s", type(exc).__name__)
            return 0
        revision = getattr(getattr(read, "summary", None), "draft_revision", 0)
        return revision if isinstance(revision, int) and not isinstance(revision, bool) and revision >= 0 else 0

    def _mcp_impact(self) -> dict[str, str] | None:
        """共享 MCP 断线的逐路决策（服务层 ``plan_group_stop``）；未知组返回 ``None``。"""

        if self._group is None:
            return None
        from services.agent_service.comparison import ComparisonLaneObservation, plan_group_stop

        observations = tuple(
            ComparisonLaneObservation(
                lane_ref=row.lane_ref,
                display_state=row.display_state,
                reliable_result=row.reliable_result,
            )
            for row in self._state.lanes
        )
        try:
            plan = plan_group_stop(self._group, observations, reason=SHARED_MCP_DISCONNECT_REASON)
        except AgentValidationError:
            return None
        return {decision.lane_ref: decision.action for decision in plan.decisions}

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        value = getattr(code, "value", code)
        return value if isinstance(value, str) and value else type(exc).__name__


__all__ = [
    "COMPARISON_MODES",
    "LANE_SLOTS",
    "SHARED_MCP_DISCONNECT_REASON",
    "UNAVAILABLE_NOT_READY",
    "UNAVAILABLE_NOT_WIRED",
    "VIEW_MODES",
    "VIEW_SIDE_BY_SIDE",
    "VIEW_TABLE",
    "ComparisonConnectionOption",
    "ComparisonGroupSummary",
    "ComparisonLaneRow",
    "ComparisonServicePort",
    "ConnectionCatalogPort",
    "ResearchComparisonState",
    "ResearchComparisonViewModel",
    "build_comparison_connection_options",
]
