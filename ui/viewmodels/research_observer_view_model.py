"""ResearchObserverViewModel — 壳层公共研究观察层与必要决定的共享 VM（检视 P02）。

设计边界（UX §3.1、P05/P10/P12，F01/F19/F31/F36）：

- **共享实例**：观察层是壳层公共区的一层，不是某个业务页的私有状态；
  同一会话内经 ``shared_research_observer`` 复用同一 VM，展开/收起不换实例、
  不丢快照（收起只是 View 的可见性变化）。
- **观察不控制**：只调用 facade 的只读查询入口（``search`` / ``read_session``）
  与答复入口 ``answer``；不停止、不取消、不推进任何执行。
- **统一决定**：所有答复提交经 ``AgentService.answer``（P01 组合根 facade），
  不在 UI 直连 DAO，也不分散调用各合同方法。
- **如实不可用**：未接线 / 运行时就绪门闩未通过时 ``available=False`` 并给出
  原因 key（R21），不伪造空研究、不填 0 待处理。
- **批准限制**：data_consent / tool_grant 等授权类决定在本观察层只展示、
  不可提交（需专用授权界面）；仅 user_choice 类支持文本答复。

VM 禁止 import flet / 持有控件 / 调 ``page.update()`` / 感知 locale（CLAUDE.md §3.2）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast
from core.agent_types import (
    AnswerDeliveryOutcome,
    AnswerDeliveryRequest,
    AnswerReceipt,
    ArtifactReadQuery,
    DraftRead,
    DraftReadQuery,
    HistoryPage,
    HistoryQuery,
    HistoryRoundSelection,
    Mode,
    PendingAnswerFact,
    PendingAnswerPage,
    SaveAnswerRequest,
    SessionRead,
    SessionReadQuery,
    json_doc,
)
from services.agent_service.main_turn import MainTurnIntakeResult, PlanReference
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

#: 桌面单用户域（与 app.agent_wiring 的默认 user_scope 保持一致；UI 层不 import app）。
LOCAL_DESKTOP_USER_SCOPE = "local-desktop"

#: 只有「用户选择/澄清」类决定允许经本观察层以文本答复；授权类决定必须走专用界面。
_ANSWERABLE_DECISION_KINDS = frozenset({"user_choice"})

#: 问题正文提取的候选键（其余结构化参数保持不展开，避免把授权参数当普通文本展示）。
_QUESTION_KEYS = ("question", "prompt", "text", "title", "label", "description")

#: 答复命令键前缀（P1-05）：按决定身份派生稳定键，重传/重启后仍命中同一命令。
_ANSWER_COMMAND_KEY_PREFIX = "ui-answer:"

#: 跨会话待处理阶段（与 core.agent_types.PENDING_ANSWER_STAGES 一致）。
PENDING_STAGES: Final[tuple[str, ...]] = ("answer", "delivery", "consumption")

#: 待处理聚合读取上限（单类）：达到上限时如实标注截断（R21）。
PENDING_ANSWER_READ_LIMIT: Final[int] = 200

#: 待处理徽标范围：全部会话（聚合成功）/ 仅本会话（聚合未接线或读取失败）。
PENDING_SCOPE_ALL = "all_sessions"
PENDING_SCOPE_SESSION = "session_only"

UNAVAILABLE_NOT_WIRED = "agent_research_not_wired"
UNAVAILABLE_NOT_READY = "agent_research_not_ready"

#: 首问可选模式（三值；直答是循环动作，不作为第四模式）。
COMPOSER_MODES: Final[tuple[str, ...]] = ("discuss", "plan", "execute")
#: 缺省模式：执行（F21 默认执行）。
DEFAULT_COMPOSER_MODE: Final[str] = "execute"
#: 精确导航目标在读取期间被更新的最大重读次数（超过则如实报码，不落过期会话）。
_REFRESH_TARGET_ATTEMPTS: Final[int] = 3


class MainTurnIntakePort(Protocol):
    """首问/开始计划受理端口（组合根注入；UI 不 import app）。"""

    async def start_question(
        self,
        text: str,
        *,
        mode: Mode,
        session_id: str | None = None,
        session_title: str | None = None,
        expected_revision: int | None = None,
    ) -> MainTurnIntakeResult: ...

    async def start_plan(
        self,
        reference: PlanReference,
        *,
        session_id: str,
        expected_revision: int,
    ) -> MainTurnIntakeResult: ...


class AgentResearchService(Protocol):
    """本 VM 需要的 facade 子集（P01 ``AgentService`` 结构性满足，UI 不 import app）。"""

    async def search(self, query: HistoryQuery, *, user_scope: str) -> HistoryPage: ...

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead: ...

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> Any: ...

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt: ...

    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> PendingAnswerPage: ...

    async def deliver_answer(self, request: AnswerDeliveryRequest) -> AnswerDeliveryOutcome: ...

    async def read_draft(self, query: DraftReadQuery, *, user_scope: str) -> DraftRead: ...


@dataclass(frozen=True)
class SessionMessageView:
    """主会话消息的只读投影（``body_ref`` 未载入正文时如实标记不可读）。"""

    message_id: str
    role: str | None
    body_text: str | None
    body_available: bool
    execution_id: str | None
    occurred_at: str


@dataclass(frozen=True)
class ExecutionNodeView:
    """子任务树的只读节点（``depth`` 由父子关系展开计算，展开态由 VM 持久化）。"""

    execution_id: str
    parent_execution_id: str | None
    display_state: str
    goal_coverage: str
    model_attempt_count: int
    tool_call_count: int
    active_ms: int
    depth: int
    has_children: bool


@dataclass(frozen=True)
class SessionArtifactView:
    """首屏成果投影（正文未随首屏返回时 ``body_available=False``，不伪造正文）。"""

    artifact_id: str
    artifact_version: int
    artifact_kind: str
    execution_id: str
    round_id: str
    summary: str | None
    body_text: str | None
    body_available: bool
    published_at: str | None


@dataclass(frozen=True)
class LoadedArtifactBody:
    """按需读取的成果正文（``body_available=False`` 时保留服务端不可用原因）。"""

    key: str
    body_text: str | None
    body_available: bool
    unavailable_reason: str | None
    truncated: bool


@dataclass(frozen=True)
class SessionDraftView:
    """按会话读回的草稿只读投影（R2-I23；读取不发送、不创建研究）。

    ``text``/选择为 None 表示该会话没有可读载荷；``cleaned`` / ``transferred``
    分别表示正文已被清理删除、已移出到其他会话，与「从未保存」不同（R21）。
    """

    session_id: str
    draft_revision: int
    text: str | None
    mode: str | None
    purpose: str | None
    research_date: str | None
    cutoff: str | None
    sources: tuple[str, ...]
    skills: tuple[str, ...]
    mcp_services: tuple[str, ...]
    customized_fields: tuple[str, ...]
    model_selection: str | None
    send_target: str | None
    object_context: str | None
    role_prompt_selection: str | None
    is_empty: bool
    cleaned: bool
    archived: bool
    transferred: bool
    read_at: str


@dataclass(frozen=True)
class PlanStepView:
    """计划预览步骤（只读投影；``depends_on`` 原样保留，不补默认依赖）。"""

    step_id: str
    content: str
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class PendingDecision:
    """必要决定的只读投影（展示用；不回显完整授权参数）。

    ``execution_id`` / ``round_id`` 为决定的权威归属（R2-I04）：提交时随
    ``decision_id`` 一起回传，证明作答的是用户看到的这一轮、这一决定。
    """

    decision_id: str
    execution_id: str
    round_id: str
    decision_kind: str
    revision: int
    created_at: str
    expires_at: str | None
    question_text: str | None

    @property
    def answerable(self) -> bool:
        """是否允许本观察层提交（仅 user_choice 类文本答复）。"""

        return self.decision_kind in _ANSWERABLE_DECISION_KINDS


@dataclass(frozen=True)
class PendingAnswerView:
    """跨会话待处理项的只读投影（P1-05）：记录/交付/消费分列。

    ``stage`` 取 :data:`PENDING_STAGES`；``command_record_id`` 仅交付/消费阶段存在，
    重试投递按它定位原命令（不新建答复、不换命令键）。
    """

    session_id: str
    session_title: str | None
    decision_id: str
    execution_id: str
    round_id: str
    decision_kind: str
    revision: int
    stage: str
    created_at: str
    expires_at: str | None
    question_text: str | None
    command_record_id: str | None
    delivery_state: str | None
    consumption_state: str | None

    @property
    def answerable(self) -> bool:
        """阶段为待答复且属于本观察层可提交的决定类型。"""

        return self.stage == "answer" and self.decision_kind in _ANSWERABLE_DECISION_KINDS

    @property
    def retryable_delivery(self) -> bool:
        """待交付且投递未被判为 unknown（unknown 不重发、不冒充已投递）。"""

        return self.stage == "delivery" and self.delivery_state == "not_delivered"

    def belongs_to(self, session_id: str | None) -> bool:
        return session_id is not None and self.session_id == session_id


@dataclass(frozen=True)
class ResearchObserverState:
    """观察层不可变快照。

    ``unavailable_reason_key`` / ``error_key`` 均为 i18n key（VM 不感知 locale）；
    ``last_error_code`` 为 facade 的类型化错误码（未知即未知，不用 0/空串冒充）。
    """

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED
    session_id: str | None = None
    session_title: str | None = None
    executions: tuple[tuple[str, str], ...] = ()
    decisions: tuple[PendingDecision, ...] = ()
    pending_count: int = 0
    loading: bool = False
    submitting: bool = False
    error_key: str | None = None
    last_error_code: str | None = None
    answer_drafts: tuple[tuple[str, str], ...] = ()
    last_updated_at: str | None = None
    last_answer_delivery_state: str | None = None
    #: 最近一次交付失败原因码（未交付才能重试；None 表示无失败或已交付）。
    last_answer_delivery_reason: str | None = None
    #: 交付在途的原答复命令（同命令不并发重发；None 表示无在途交付）。
    answer_delivery_pending_command: str | None = None
    #: 跨会话待处理（P1-05）：三态分列 + 截断事实 + 徽标范围（R21）。
    pending_items: tuple[PendingAnswerView, ...] = ()
    pending_items_truncated: bool = False
    pending_scope: str = PENDING_SCOPE_SESSION
    execution_nodes: tuple[ExecutionNodeView, ...] = ()
    messages: tuple[SessionMessageView, ...] = ()
    artifacts: tuple[SessionArtifactView, ...] = ()
    messages_has_more: bool = False
    artifacts_has_more: bool = False
    session_draft_revision: int = 0
    selected_execution_id: str | None = None
    collapsed_execution_ids: tuple[str, ...] = ()
    artifact_bodies: tuple[LoadedArtifactBody, ...] = ()
    artifact_body_pending_keys: tuple[str, ...] = ()
    session_draft: SessionDraftView | None = None
    draft_pending: bool = False
    draft_error_code: str | None = None
    #: 首问/计划（F01/F21）：输入正文、模式、提交中与错误 key 均为 UI 纯状态。
    composer_available: bool = False
    composer_text: str = ""
    composer_mode: str = DEFAULT_COMPOSER_MODE
    composer_pending: bool = False
    composer_error_key: str | None = None
    #: 最近一次受理的派发事实：``submitted=False`` 的 ``reason``（讨论/计划为零派发，
    #: 属预期，不当作错误；执行模式零派发如实展示原因，不伪造成已启动）。
    last_dispatch_reason: str | None = None
    #: 主回合直答（讨论/执行直答）；为空表示尚未产生或未接线，不以占位文本伪装。
    last_answer_text: str = ""
    plan_steps: tuple[PlanStepView, ...] = ()
    plan_reference: tuple[str, int] | None = None
    plan_pending: bool = False
    plan_error_key: str | None = None

    def draft_for(self, decision_id: str) -> str:
        """读取某决定的答复草稿（收起/重渲染后仍在）。"""

        for key, value in self.answer_drafts:
            if key == decision_id:
                return value
        return ""

    def is_collapsed(self, execution_id: str) -> bool:
        """子任务节点是否处于收起态（展开态由共享 VM 持久化，切页不丢）。"""

        return execution_id in self.collapsed_execution_ids

    def pending_for(self, stage: str) -> tuple[PendingAnswerView, ...]:
        """读取某阶段的跨会话待处理项（未知阶段返回空，不猜归属）。"""

        return tuple(item for item in self.pending_items if item.stage == stage)

    def pending_for_other_sessions(self) -> tuple[PendingAnswerView, ...]:
        """其他会话的待处理项（当前会话已有完整决定卡，不在此重复列出）。"""

        return tuple(item for item in self.pending_items if not item.belongs_to(self.session_id))

    def artifact_body_for(self, artifact_id: str, artifact_version: int) -> LoadedArtifactBody | None:
        """读取已载入的成果正文；未载入返回 ``None``（未知即未知，不造空正文）。"""

        key = f"{artifact_id}:{artifact_version}"
        for body in self.artifact_bodies:
            if body.key == key:
                return body
        return None


def _extract_question_text(question_payload: str | None) -> str | None:
    """从 CanonicalJson 载荷提取可读问题文本（不可解释则 None，不猜）。"""

    if not question_payload:
        return None
    try:
        decoded = json.loads(question_payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    for key in _QUESTION_KEYS:
        value = decoded.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _pending_decisions(read: SessionRead) -> tuple[PendingDecision, ...]:
    """从会话首读中挑出未答复决定（已答复/已消费不在待处理列表）。"""

    pending: list[PendingDecision] = []
    for decision in read.decisions:
        if decision.answer_command_id is not None:
            continue
        pending.append(
            PendingDecision(
                decision_id=decision.decision_id,
                execution_id=decision.execution_id,
                round_id=decision.round_id,
                decision_kind=decision.decision_kind,
                revision=decision.revision,
                created_at=decision.created_at,
                expires_at=decision.expires_at,
                question_text=_extract_question_text(decision.question_payload),
            )
        )
    return tuple(pending)


def _pending_answer_view(fact: PendingAnswerFact) -> PendingAnswerView:
    """聚合事实 → 只读投影（阶段与命令态原样保留，未知不猜）。"""

    return PendingAnswerView(
        session_id=fact.session_id,
        session_title=fact.session_title,
        decision_id=fact.decision_id,
        execution_id=fact.execution_id,
        round_id=fact.round_id,
        decision_kind=fact.decision_kind,
        revision=fact.revision,
        stage=fact.stage,
        created_at=fact.created_at,
        expires_at=fact.expires_at,
        question_text=_extract_question_text(fact.question_payload),
        command_record_id=fact.command_record_id,
        delivery_state=_tag_text(fact.delivery_state),
        consumption_state=_tag_text(fact.consumption_state),
    )


def _answer_command_key(decision_id: str) -> str:
    """答复命令键：按决定身份派生（同一决定的每次提交命中同一命令）。"""

    return f"{_ANSWER_COMMAND_KEY_PREFIX}{decision_id}"


def _is_expired(expires_at: str | None) -> bool:
    """决定是否已过期（UTC 微秒 ISO；不可解析时不臆断，交给 DAO 新鲜核）。

    过期决定在 UI 层即拒绝，避免发出注定被 ``POLICY_DENIED`` 的写调用。
    """

    if expires_at is None:
        return False
    try:
        deadline = datetime.strptime(expires_at, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return deadline <= datetime.now(UTC)


def _session_draft_view(read: DraftRead) -> SessionDraftView:
    """DAO 读回 → 只读投影（无载荷时字段为 None，不伪造空正文/默认选择）。"""

    payload = read.payload
    return SessionDraftView(
        session_id=read.session_id,
        draft_revision=read.draft_revision,
        text=payload.text if payload is not None else None,
        mode=_tag_text(payload.mode) if payload is not None else None,
        purpose=_tag_text(payload.purpose) if payload is not None else None,
        research_date=payload.research_date if payload is not None else None,
        cutoff=payload.cutoff if payload is not None else None,
        sources=(
            tuple(f"{_tag_text(ref.source_kind)}:{ref.source_id}@{ref.source_version}" for ref in payload.source_refs)
            if payload is not None
            else ()
        ),
        skills=(
            tuple(f"{ref.skill_id}@{ref.version}" for ref in payload.skill_versions) if payload is not None else ()
        ),
        mcp_services=(
            tuple(f"{ref.service_id}@{ref.config_version}" for ref in payload.mcp_selection)
            if payload is not None
            else ()
        ),
        customized_fields=payload.customized_fields if payload is not None else (),
        model_selection=payload.model_selection if payload is not None else None,
        send_target=payload.send_target if payload is not None else None,
        object_context=payload.object_context if payload is not None else None,
        role_prompt_selection=payload.role_prompt_selection if payload is not None else None,
        is_empty=read.is_empty,
        cleaned=read.cleaned,
        archived=read.archived,
        transferred=read.transferred,
        read_at=read.read_at,
    )


def _tag_text(value: Any) -> str | None:
    """枚举/文本标签 → 文本（None 透传，未知保持原值不猜语义）。"""

    if value is None:
        return None
    return getattr(value, "value", value)


def _execution_ids(executions: tuple[Any, ...]) -> frozenset[str]:
    return frozenset(execution.execution_id for execution in executions)


def _children_map(executions: tuple[Any, ...]) -> dict[str | None, list[Any]]:
    """按父子关系分组；父节点不在本页时归一为根（不丢节点、不猜归属）。"""

    ids = _execution_ids(executions)
    children: dict[str | None, list[Any]] = {}
    for execution in executions:
        parent = execution.parent_execution_id if execution.parent_execution_id in ids else None
        children.setdefault(parent, []).append(execution)
    return children


def _subtree_ids(executions: tuple[Any, ...], root_id: str) -> frozenset[str]:
    """计算某执行及其全部后代的 id 集（环形父子引用以已访问集合截断）。"""

    children = _children_map(executions)
    if root_id not in _execution_ids(executions):
        return frozenset()
    seen: set[str] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(child.execution_id for child in children.get(current, ()))
    return frozenset(seen)


def _flatten_executions(
    executions: tuple[Any, ...],
    *,
    collapsed: frozenset[str],
    only_ids: frozenset[str] | None,
) -> tuple[ExecutionNodeView, ...]:
    """把执行父子关系展开为只读树（收起节点隐藏后代；仅展示 ``only_ids`` 子树）。"""

    children = _children_map(executions)
    ids = _execution_ids(executions)
    roots = [
        execution
        for execution in executions
        if execution.parent_execution_id not in ids
        or (only_ids is not None and execution.parent_execution_id not in only_ids)
    ]
    if only_ids is not None:
        roots = [execution for execution in roots if execution.execution_id in only_ids]
    nodes: list[ExecutionNodeView] = []

    def _walk(execution: Any, depth: int, path: frozenset[str]) -> None:
        child_nodes = children.get(execution.execution_id, ())
        visible_children = [child for child in child_nodes if child.execution_id not in path]
        nodes.append(
            ExecutionNodeView(
                execution_id=execution.execution_id,
                parent_execution_id=execution.parent_execution_id,
                display_state=execution.display_state,
                goal_coverage=execution.goal_coverage,
                model_attempt_count=execution.model_attempt_count,
                tool_call_count=execution.tool_call_count,
                active_ms=execution.active_ms,
                depth=depth,
                has_children=bool(visible_children),
            )
        )
        if execution.execution_id in collapsed:
            return
        for child in visible_children:
            _walk(child, depth + 1, path | {execution.execution_id})

    for root in roots:
        _walk(root, 0, frozenset())
    return tuple(nodes)


def _project_messages(
    messages: tuple[Any, ...],
    *,
    only_ids: frozenset[str] | None,
) -> tuple[SessionMessageView, ...]:
    """消息只读投影：选中子任务时只保留该子树消息，会话级消息（无 execution）始终保留。"""

    projected: list[SessionMessageView] = []
    for item in messages:
        if only_ids is not None and item.execution_id is not None and item.execution_id not in only_ids:
            continue
        role = getattr(item.role, "value", item.role)
        projected.append(
            SessionMessageView(
                message_id=item.message_id,
                role=role,
                body_text=item.body_text,
                body_available=item.body_text is not None,
                execution_id=item.execution_id,
                occurred_at=item.occurred_at,
            )
        )
    return tuple(projected)


def _project_artifacts(
    artifacts: tuple[Any, ...],
    *,
    only_ids: frozenset[str] | None,
) -> tuple[SessionArtifactView, ...]:
    """成果只读投影（成果必属某执行；选中子任务时只保留该子树成果）。"""

    projected: list[SessionArtifactView] = []
    for item in artifacts:
        if only_ids is not None and item.execution_id not in only_ids:
            continue
        projected.append(
            SessionArtifactView(
                artifact_id=item.artifact_id,
                artifact_version=item.artifact_version,
                artifact_kind=item.artifact_kind,
                execution_id=item.execution_id,
                round_id=item.round_id,
                summary=item.summary,
                body_text=item.body_text,
                body_available=item.body_text is not None,
                published_at=item.published_at,
            )
        )
    return tuple(projected)


def _normalized_selection(executions: tuple[Any, ...], selected: str | None) -> str | None:
    """选中项在新快照中不存在时回落为整会话视图（不展示不可解释的空子树）。"""

    if selected is None:
        return None
    return selected if selected in _execution_ids(executions) else None


def _normalized_collapsed(executions: tuple[Any, ...], collapsed: tuple[str, ...]) -> tuple[str, ...]:
    """收起集合按当前快照裁剪（已消失的执行不长期占用状态）。"""

    ids = _execution_ids(executions)
    return tuple(sorted(execution_id for execution_id in collapsed if execution_id in ids))


class ResearchObserverViewModel(ObservableViewModelMixin[ResearchObserverState]):
    """壳层公共研究观察层 VM（共享实例；观察不控制执行）。"""

    def __init__(
        self,
        service: AgentResearchService | None,
        *,
        user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
        unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
        intake: MainTurnIntakePort | None = None,
    ) -> None:
        self._service = service
        self._user_scope = user_scope
        self._intake = intake
        self._session_key: int | None = id(service) if service is not None else None
        # 精确会话导航目标（建稿回执驱动）：仅在读取与目标仍一致时落 state，
        # 读取期间被更新的目标会触发重读，避免壳层退回「历史首条」。
        self._preferred_session_id: str | None = None
        # 原始首读投影：展开/收起/选中只重算展示，不重复读取、不触碰执行。
        self._raw_executions: tuple[Any, ...] = ()
        self._raw_messages: tuple[Any, ...] = ()
        self._raw_artifacts: tuple[Any, ...] = ()
        self._state = ResearchObserverState(
            available=service is not None,
            unavailable_reason_key=unavailable_reason_key if service is None else "",
            composer_available=service is not None and intake is not None,
        )
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    async def refresh(self, session_id: str | None = None) -> None:
        """重新读取当前（或指定）研究会话与待处理决定（只读；不触碰执行状态）。

        ``session_id`` 为精确导航目标（建稿回执驱动）：传入时读取该会话，不以
        「历史首条」替代；不传时沿用当前已选会话，仅在首次进入（无会话）时取
        历史最新一条。读取期间目标被更新会重读，保证落 state 的会话与目标一致。
        """

        if self._service is None or not self._state.available:
            return
        if session_id is not None:
            self._preferred_session_id = session_id
        if self._state.loading:
            return
        self._track_inflight_cancellable()
        self._set_state(loading=True, error_key=None, last_error_code=None)
        try:
            for _attempt in range(_REFRESH_TARGET_ATTEMPTS):
                chosen = await self._resolve_session(self._preferred_session_id)
                if chosen is None:
                    return
                # 跨会话待处理聚合（P1-05）：徽标不由首条会话单独决定；读取失败
                # 退回本会话并如实标注范围（R21），不把失败读成 0 待办。
                overview, overview_error = await self._read_pending_overview()
                read = await self._service.read_session(
                    SessionReadQuery(session_id=chosen, limit=50),
                    user_scope=self._user_scope,
                )
                if self._preferred_session_id is not None and self._preferred_session_id != chosen:
                    continue  # 读取期间目标已更新：以最新目标重读，不落过期会话
                self._apply_session_read(chosen, read, overview=overview, overview_error=overview_error)
                if self._preferred_session_id == chosen:
                    self._preferred_session_id = None
                return
            # 目标连续变化超过上限：保留既有快照，如实报码（不伪装成已导航）
            self._set_state(loading=False, last_error_code="REFRESH_TARGET_CHANGED")
        except asyncio.CancelledError:
            # R2：取消必须传播；先释放 loading，避免会话存续期间刷新永久被拦截。
            self._set_state(loading=False)
            raise
        except Exception as e:  # facade 已脱敏的类型化错误；未知错误如实报码，不伪装空结果
            # 断线不清空既有快照：保留上次已知状态，只如实报错（R21）。
            self._set_state(loading=False, last_error_code=self._error_code(e))

    async def open_session(self, session_id: str) -> None:
        """导航到精确会话（建稿回执驱动；不退回历史首条、不派发研究）。"""

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        target = (session_id or "").strip()
        if not target:
            return
        await self.refresh(target)

    async def _resolve_session(self, target: str | None) -> str | None:
        """解析要读取的会话：显式目标优先，其次当前会话，最后历史最新一条。

        历史最新一条仅用于首次进入（无任何目标/当前会话），不得覆盖精确导航。
        """

        service = self._service
        if service is None:
            return None
        chosen = target or self._state.session_id
        if chosen is not None:
            return chosen
        history = await service.search(
            HistoryQuery(
                scope="all_saved",
                round_selection=HistoryRoundSelection(kind="all_rounds"),
                query_text="",
                time_basis="updated",
                sort="newest_first",
                limit=10,
            ),
            user_scope=self._user_scope,
        )
        chosen = next((hit.session_id for hit in history.hits), None)
        if chosen is None:
            self._clear_session_state(history.read_moment)
        return chosen

    def _clear_session_state(self, read_moment: str | None) -> None:
        """无任何会话时的空状态（如实无研究，不保留上一会话残影）。"""

        self._raw_executions = ()
        self._raw_messages = ()
        self._raw_artifacts = ()
        self._set_state(
            loading=False,
            session_id=None,
            session_title=None,
            executions=(),
            execution_nodes=(),
            messages=(),
            artifacts=(),
            messages_has_more=False,
            artifacts_has_more=False,
            session_draft_revision=0,
            artifact_body_pending_keys=(),
            session_draft=None,
            draft_error_code=None,
            decisions=(),
            pending_count=0,
            pending_items=(),
            pending_items_truncated=False,
            pending_scope=PENDING_SCOPE_SESSION,
            last_updated_at=read_moment,
        )

    async def _read_pending_overview(self) -> tuple[PendingAnswerPage | None, str | None]:
        """跨会话待处理聚合读取（P1-05）：未接线返回 None，失败返回原因码。"""

        service = self._service
        reader = getattr(service, "read_pending_answers", None) if service is not None else None
        if not callable(reader):
            return None, None
        load_page = cast("Callable[..., Awaitable[PendingAnswerPage]]", reader)
        try:
            page = await load_page(user_scope=self._user_scope, limit=PENDING_ANSWER_READ_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return None, self._error_code(e)
        return page, None

    def _apply_session_read(
        self,
        session_id: str,
        read: SessionRead,
        *,
        overview: PendingAnswerPage | None = None,
        overview_error: str | None = None,
    ) -> None:
        """把会话首读投影到 state（纯展示，不触碰执行）。

        待处理徽标优先取跨会话聚合；聚合未接线/失败时退回本会话集合，并把
        ``pending_scope`` 如实标为 ``session_only``（不冒充全局完整）。
        """

        pending = _pending_decisions(read)
        if overview is not None:
            pending_items = tuple(_pending_answer_view(item) for item in overview.items)
            pending_count = int(overview.answer_count) + int(overview.delivery_count)
            pending_scope = PENDING_SCOPE_ALL
            pending_truncated = bool(overview.truncated)
        else:
            pending_items = tuple(
                PendingAnswerView(
                    session_id=session_id,
                    session_title=read.summary.title,
                    decision_id=decision.decision_id,
                    execution_id=decision.execution_id,
                    round_id=decision.round_id,
                    decision_kind=decision.decision_kind,
                    revision=decision.revision,
                    stage="answer",
                    created_at=decision.created_at,
                    expires_at=decision.expires_at,
                    question_text=decision.question_text,
                    command_record_id=None,
                    delivery_state=None,
                    consumption_state=None,
                )
                for decision in pending
            )
            pending_count = len(pending)
            pending_scope = PENDING_SCOPE_SESSION
            pending_truncated = False
        previous_session_id = self._state.session_id
        session_changed = previous_session_id is not None and previous_session_id != session_id
        self._raw_executions = tuple(read.executions)
        self._raw_messages = tuple(read.messages.items)
        self._raw_artifacts = tuple(read.artifacts.items)
        selected = _normalized_selection(self._raw_executions, self._state.selected_execution_id)
        collapsed = _normalized_collapsed(self._raw_executions, self._state.collapsed_execution_ids)
        artifact_keys = {f"{item.artifact_id}:{item.artifact_version}" for item in self._raw_artifacts}
        pending_keys = tuple(key for key in self._state.artifact_body_pending_keys if key in artifact_keys)
        # R2-I23：已载入草稿只在新快照仍指向同一会话同一稿版时保留；稿版已推进或
        # 切了会话即丢弃，避免把旧稿正文冒充当前可继续编辑的内容。
        loaded_draft = self._state.session_draft
        if loaded_draft is not None and (
            loaded_draft.session_id != session_id or loaded_draft.draft_revision != read.summary.draft_revision
        ):
            loaded_draft = None
        self._set_state(
            loading=False,
            session_id=session_id,
            session_title=read.summary.title,
            executions=tuple((execution.execution_id, execution.display_state) for execution in read.executions),
            decisions=pending,
            pending_count=pending_count,
            pending_items=pending_items,
            pending_items_truncated=pending_truncated,
            pending_scope=pending_scope,
            last_error_code=overview_error,
            last_updated_at=read.summary.updated_at,
            messages_has_more=bool(read.messages.has_more),
            artifacts_has_more=bool(read.artifacts.has_more),
            session_draft_revision=read.summary.draft_revision,
            selected_execution_id=selected,
            collapsed_execution_ids=collapsed,
            artifact_body_pending_keys=pending_keys,
            # 换会话时不复用上一会话的正文缓存（同 id 跨会话也不冒充当前会话成果）
            artifact_bodies=() if session_changed else self._state.artifact_bodies,
            session_draft=loaded_draft,
            draft_error_code=None,
            **self._projection_changes(selected=selected, collapsed=collapsed),
        )

    # ------------------------------------------------------------------
    # 只读观察位置命令（不派发、不控制执行）
    # ------------------------------------------------------------------

    def toggle_execution(self, execution_id: str) -> None:
        """展开/收起子任务节点（纯展示状态；共享 VM 持有，切页不丢）。"""

        if execution_id not in _execution_ids(self._raw_executions):
            return
        collapsed = set(self._state.collapsed_execution_ids)
        if execution_id in collapsed:
            collapsed.discard(execution_id)
        else:
            collapsed.add(execution_id)
        normalized = _normalized_collapsed(self._raw_executions, tuple(collapsed))
        self._set_state(
            collapsed_execution_ids=normalized,
            **self._projection_changes(
                selected=self._state.selected_execution_id,
                collapsed=normalized,
            ),
        )

    def select_execution(self, execution_id: str | None) -> None:
        """改变观察位置（整会话 / 某执行子树）；只改展示过滤，不改变研究执行。"""

        selected = _normalized_selection(self._raw_executions, execution_id)
        self._set_state(
            selected_execution_id=selected,
            **self._projection_changes(
                selected=selected,
                collapsed=self._state.collapsed_execution_ids,
            ),
        )

    async def load_artifact(
        self,
        artifact_id: str,
        artifact_version: int,
        execution_id: str,
        round_id: str,
    ) -> None:
        """按需读取成果正文（只读 ``read_artifact``；不重派发、不推进任何执行）。"""

        if self._service is None or not self._state.available:
            return
        key = f"{artifact_id}:{artifact_version}"
        if self._state.artifact_body_for(artifact_id, artifact_version) is not None:
            return
        if key in self._state.artifact_body_pending_keys:
            return
        self._track_inflight_cancellable()
        self._set_state(
            artifact_body_pending_keys=(*self._state.artifact_body_pending_keys, key),
            error_key=None,
            last_error_code=None,
        )
        try:
            read = await self._service.read_artifact(
                ArtifactReadQuery(
                    artifact_id=artifact_id,
                    artifact_version=artifact_version,
                    execution_id=execution_id,
                    round_id=round_id,
                ),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            # R2：取消必须传播；先移除在途标记，避免该成果永久停在「读取中」。
            self._set_state(
                artifact_body_pending_keys=tuple(
                    pending for pending in self._state.artifact_body_pending_keys if pending != key
                )
            )
            raise
        except Exception as e:
            self._set_state(
                artifact_body_pending_keys=tuple(
                    pending for pending in self._state.artifact_body_pending_keys if pending != key
                ),
                last_error_code=self._error_code(e),
            )
            return
        loaded = LoadedArtifactBody(
            key=key,
            body_text=read.body_text,
            body_available=bool(read.body_available),
            unavailable_reason=getattr(read, "unavailable_reason", None),
            truncated=getattr(read, "next_cursor", None) is not None,
        )
        bodies = tuple(body for body in self._state.artifact_bodies if body.key != key)
        self._set_state(
            artifact_bodies=(*bodies, loaded),
            artifact_body_pending_keys=tuple(
                pending for pending in self._state.artifact_body_pending_keys if pending != key
            ),
        )

    async def load_draft(self, session_id: str, draft_revision: int | None) -> None:
        """按会话/稿版读回草稿正文与选择（只读；不发送、不创建研究）。

        稿版不符时 facade 抛 ``REVISION_CONFLICT``，本层只如实给出错误码，并先
        清空已载入草稿——旧稿正文不得继续显示为「当前可恢复内容」（R21）。
        """

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        if self._state.draft_pending:
            return
        self._track_inflight_cancellable()
        self._set_state(
            draft_pending=True,
            session_draft=None,
            draft_error_code=None,
            error_key=None,
            last_error_code=None,
        )
        try:
            read = await self._service.read_draft(
                DraftReadQuery(session_id=session_id, draft_revision=draft_revision),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            # R2：取消必须传播；先释放在途标记，避免下次读取被永久拦截。
            self._set_state(draft_pending=False)
            raise
        except Exception as e:
            self._set_state(draft_pending=False, draft_error_code=self._error_code(e))
            return
        # 并发刷新已切走会话/推进稿版时，读回的旧稿不再属于当前观察位置。
        current = self._state
        session_mismatch = current.session_id is not None and current.session_id != read.session_id
        revision_mismatch = current.session_id is not None and current.session_draft_revision != read.draft_revision
        if session_mismatch or revision_mismatch:
            self._set_state(draft_pending=False)
            return
        self._set_state(draft_pending=False, session_draft=_session_draft_view(read))

    def set_composer_text(self, text: str) -> None:
        """记录首问正文（纯状态；收起观察层不清稿）。"""

        self._set_state(composer_text=text)

    def set_composer_mode(self, mode: str) -> None:
        """记录用户选择的模式（只接受三值；未知值不改写当前模式）。"""

        if mode in COMPOSER_MODES:
            self._set_state(composer_mode=mode)

    async def send_first_question(self) -> None:
        """发送首问：经注入的受理端口创建会话/受理（讨论/计划零派发属预期）。

        未接线受理端口时如实给出不可用 key（R21），不伪造已发送；取消继续传播
        （R2）；空正文不提交。
        """

        intake = self._intake
        if intake is None or not self._state.composer_available:
            self._set_state(composer_error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        text = self._state.composer_text.strip()
        if not text:
            self._set_state(composer_error_key="agent_research_empty_draft")
            return
        if self._state.composer_pending:
            return
        self._track_inflight_cancellable()
        self._set_state(composer_pending=True, composer_error_key=None, last_dispatch_reason=None)
        try:
            result = await intake.start_question(
                text,
                mode=Mode(self._state.composer_mode),
                session_id=self._state.session_id,
                expected_revision=(self._state.session_draft_revision if self._state.session_id is not None else 0),
            )
        except asyncio.CancelledError:
            self._set_state(composer_pending=False)
            raise
        except Exception as e:
            self._set_state(composer_pending=False, composer_error_key=self._error_code(e))
            return
        main_turn = getattr(result, "main_turn", None)
        changes: dict[str, Any] = {"composer_pending": False, "composer_text": ""}
        if main_turn is None:
            changes["last_dispatch_reason"] = self._dispatch_reason(result)
        else:
            loop_outcome = getattr(main_turn, "outcome", None)
            answer = getattr(getattr(loop_outcome, "result", None), "body_text", "")
            changes["last_answer_text"] = answer if isinstance(answer, str) else ""
            changes["last_dispatch_reason"] = None
            reference = getattr(main_turn, "plan_reference", None)
            if reference is not None:
                changes["plan_reference"] = (reference.execution_id, reference.plan_revision)
        self._set_state(**changes)
        reference = changes.get("plan_reference")
        if isinstance(reference, tuple) and len(reference) == 2:
            # 计划已保存：按界面呈现的版本读回步骤，避免与可执行计划错版。
            await self.load_plan_preview(reference[0], reference[1])
        if self._service is not None and self._state.available:
            await self.refresh()

    async def start_confirmed_plan(self) -> None:
        """开始已审计划：按界面呈现的计划版发起新执行意图（新意图排队）。

        只使用当前呈现的计划引用与稿版：计划引用缺失即拒绝（不猜「最新计划」）；
        稿版推进导致 CAS 冲突时原样给出错误码。
        """

        intake = self._intake
        if intake is None or not self._state.composer_available:
            self._set_state(composer_error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        reference = self._state.plan_reference
        session_id = self._state.session_id
        if reference is None:
            self._set_state(composer_error_key="agent_research_plan_missing")
            return
        if session_id is None:
            self._set_state(composer_error_key="agent_research_no_session")
            return
        if self._state.composer_pending:
            return
        self._track_inflight_cancellable()
        self._set_state(composer_pending=True, composer_error_key=None, last_dispatch_reason=None)
        try:
            result = await intake.start_plan(
                PlanReference(execution_id=reference[0], plan_revision=reference[1]),
                session_id=session_id,
                expected_revision=self._state.session_draft_revision,
            )
        except asyncio.CancelledError:
            self._set_state(composer_pending=False)
            raise
        except Exception as e:
            self._set_state(composer_pending=False, composer_error_key=self._error_code(e))
            return
        self._set_state(composer_pending=False, last_dispatch_reason=self._dispatch_reason(result))
        # 已成功投递即消费本次计划引用，避免重复点击排队同一计划版（失败保留可重试）。
        dispatch = getattr(result, "dispatch", None)
        if dispatch is not None and getattr(dispatch, "submitted", False):
            self._set_state(plan_reference=None)

    async def load_plan_preview(self, execution_id: str, plan_revision: int) -> None:
        """读取已保存计划版并投影为预览步骤（只读；读取失败不展示空计划）。"""

        service = self._service
        reader = getattr(service, "read_plan", None) if service is not None else None
        if not callable(reader):
            self._set_state(plan_error_key=UNAVAILABLE_NOT_WIRED, plan_steps=())
            return
        if self._state.plan_pending:
            return
        read_plan = cast(Callable[..., Awaitable[Any]], reader)
        self._track_inflight_cancellable()
        self._set_state(plan_pending=True, plan_error_key=None)
        try:
            proposal = await read_plan(
                user_scope=self._user_scope,
                execution_id=execution_id,
                plan_revision=plan_revision,
            )
        except asyncio.CancelledError:
            self._set_state(plan_pending=False)
            raise
        except Exception as e:
            self._set_state(plan_pending=False, plan_error_key=self._error_code(e))
            return
        steps = tuple(
            PlanStepView(step_id=item.id, content=item.content, depends_on=tuple(item.depends_on))
            for item in proposal.items
        )
        self._set_state(
            plan_pending=False,
            plan_steps=steps,
            plan_reference=(execution_id, plan_revision),
        )

    @staticmethod
    def _dispatch_reason(result: Any) -> str | None:
        """受理结果的派发事实：已派发为 None；未接线/零派发如实返回原因。"""

        dispatch = getattr(result, "dispatch", None)
        if dispatch is None:
            return UNAVAILABLE_NOT_WIRED
        if getattr(dispatch, "submitted", False):
            return None
        reason = getattr(dispatch, "reason", None)
        return reason if isinstance(reason, str) and reason else "agent_research_dispatch_zero"

    def _projection_changes(self, *, selected: str | None, collapsed: tuple[str, ...]) -> dict[str, Any]:
        """按观察位置重算树/消息/成果（纯函数投影，不发任何请求）。"""

        only_ids = _subtree_ids(self._raw_executions, selected) if selected is not None else None
        return {
            "execution_nodes": _flatten_executions(
                self._raw_executions,
                collapsed=frozenset(collapsed),
                only_ids=only_ids,
            ),
            "messages": _project_messages(self._raw_messages, only_ids=only_ids),
            "artifacts": _project_artifacts(self._raw_artifacts, only_ids=only_ids),
        }

    def set_answer_draft(self, decision_id: str, text: str) -> None:
        """记录答复草稿（收起观察层不清稿）。"""

        drafts = tuple((key, value) for key, value in self._state.answer_drafts if key != decision_id)
        self._set_state(answer_drafts=(*drafts, (decision_id, text)))

    async def submit_answer(self, decision_id: str, *, execution_id: str, round_id: str) -> None:
        """提交答复：唯一写入口经 facade ``answer``（统一决定，不直连 DAO）。

        命令键按决定身份派生（P1-05）：重传/重启后命中同一答复命令，不为同一
        决定生成第二个有效答复；保存成功后立即尝试交付一次，交付成功才清草稿，
        失败保留草稿与待交付项（可重试）。``execution_id`` / ``round_id`` 必须是
        界面呈现该决定时的归属：与当前投影任一不符即视为过期视图（拒绝，不按同
        id 作答另一轮）；已保存但未交付的决定再次提交按原命令重试交付。
        授权类决定拒绝在本层提交（需专用授权界面）；空草稿不提交；facade 拒绝
        时保留草稿并如实给出错误码。
        """

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        decision = next((item for item in self._state.decisions if item.decision_id == decision_id), None)
        if decision is None:
            delivery_item = next(
                (
                    item
                    for item in self._state.pending_items
                    if item.decision_id == decision_id and item.stage == "delivery"
                ),
                None,
            )
            if (
                delivery_item is not None
                and delivery_item.execution_id == execution_id
                and delivery_item.round_id == round_id
            ):
                await self.retry_delivery(decision_id)
                return
            self._set_state(error_key="agent_research_decision_stale")
            return
        if decision.execution_id != execution_id or decision.round_id != round_id:
            self._set_state(error_key="agent_research_decision_stale")
            return
        if not decision.answerable:
            self._set_state(error_key="agent_research_decision_dedicated")
            return
        if _is_expired(decision.expires_at):
            self._set_state(error_key="agent_research_decision_expired")
            return
        text = self._state.draft_for(decision_id).strip()
        if not text:
            self._set_state(error_key="agent_research_empty_draft")
            return
        if self._state.submitting:
            return

        self._set_state(submitting=True, error_key=None, last_error_code=None)
        # 写调用一旦发出，dispose 应放行自然完成（F40：磁盘为 SSOT，取消不宣称未提交）。
        self._mark_inflight_committing()
        try:
            receipt = await self._service.answer(
                SaveAnswerRequest(
                    user_scope=self._user_scope,
                    decision_id=decision_id,
                    client_command_id=_answer_command_key(decision_id),
                    expected_decision_revision=decision.revision,
                    expected_execution_id=execution_id,
                    expected_round_id=round_id,
                    answer_payload=json_doc({"text": text}),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(submitting=False, last_error_code=self._error_code(e))
            return

        self._set_state(submitting=False, last_answer_delivery_state=_tag_text(receipt.delivery_state))
        command_record_id = getattr(receipt, "command_record_id", None)
        if not (isinstance(command_record_id, str) and command_record_id):
            # 保存回执未带原命令身份：不能假装已交付，保留草稿等重试入口（R21）。
            self._set_state(
                last_answer_delivery_state="not_delivered", last_answer_delivery_reason="delivery_command_missing"
            )
            await self.refresh()
            return
        # 保存成功 ≠ 已投递（INV-06）：立即尝试交付一次；失败保留草稿可重试。
        await self._attempt_delivery(command_record_id, decision_id)
        await self.refresh()

    async def retry_delivery(self, decision_id: str) -> None:
        """按原答复命令重试交付（不新建答复、不换命令键；unknown 不重发）。"""

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        item = next(
            (
                candidate
                for candidate in self._state.pending_items
                if candidate.decision_id == decision_id and candidate.stage == "delivery"
            ),
            None,
        )
        if item is None or item.command_record_id is None:
            self._set_state(error_key="agent_research_decision_stale")
            return
        if not item.retryable_delivery:
            self._set_state(error_key="agent_research_delivery_unknown")
            return
        if self._state.submitting:
            return
        await self._attempt_delivery(item.command_record_id, decision_id)
        await self.refresh()

    async def _attempt_delivery(self, command_record_id: str, decision_id: str) -> bool:
        """交付一次原答复命令：返回是否已确认投递（失败保持可重试，R21）。"""

        if self._state.submitting:
            return False
        deliver = getattr(self._service, "deliver_answer", None)
        if not callable(deliver):
            # 未接线即 fail-closed：保存仍成功，但不得宣称已投递（R21）。
            self._set_state(
                submitting=False,
                answer_delivery_pending_command=None,
                last_answer_delivery_state="not_delivered",
                last_answer_delivery_reason="delivery_not_wired",
            )
            return False
        deliver_once = cast("Callable[[AnswerDeliveryRequest], Awaitable[AnswerDeliveryOutcome]]", deliver)
        self._set_state(
            submitting=True,
            error_key=None,
            last_error_code=None,
            answer_delivery_pending_command=command_record_id,
        )
        self._mark_inflight_committing()
        try:
            outcome = await deliver_once(
                AnswerDeliveryRequest(user_scope=self._user_scope, command_record_id=command_record_id)
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            code = self._error_code(e)
            self._set_state(
                submitting=False,
                answer_delivery_pending_command=None,
                last_answer_delivery_state="not_delivered",
                last_answer_delivery_reason=code,
                last_error_code=code,
            )
            return False
        delivery_state = _tag_text(getattr(outcome, "delivery_state", None)) or "not_delivered"
        changes: dict[str, Any] = {
            "submitting": False,
            "answer_delivery_pending_command": None,
            "last_answer_delivery_state": delivery_state,
            "last_answer_delivery_reason": getattr(outcome, "reason", None),
        }
        if delivery_state == "delivered":
            changes["answer_drafts"] = tuple(
                (key, value) for key, value in self._state.answer_drafts if key != decision_id
            )
        self._set_state(**changes)
        return delivery_state == "delivered"

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _error_code(error: Exception) -> str:
        """错误码文本：类型化 facade 错误取 ``code.value``，其余如实给出类名。"""

        code = getattr(error, "code", None)
        value = getattr(code, "value", None)
        if isinstance(value, str) and value:
            return value
        return type(error).__name__


_shared_vm: ResearchObserverViewModel | None = None


def shared_research_observer(
    service: AgentResearchService | None,
    *,
    user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
    intake: MainTurnIntakePort | None = None,
) -> ResearchObserverViewModel:
    """取本会话共享的观察层 VM（同一 facade/受理端口复用；变化或已销毁则重建）。"""

    global _shared_vm
    session_key = id(service) if service is not None else None
    if (
        _shared_vm is None
        or _shared_vm.disposed
        or _shared_vm._session_key != session_key
        or _shared_vm._intake is not intake
    ):
        stale = _shared_vm
        _shared_vm = ResearchObserverViewModel(
            service,
            user_scope=user_scope,
            unavailable_reason_key=unavailable_reason_key,
            intake=intake,
        )
        if stale is not None and not stale.disposed:
            stale.dispose()
    return _shared_vm


def reset_shared_research_observer() -> None:
    """销毁共享实例（测试隔离/会话重建；幂等）。"""

    global _shared_vm
    vm = _shared_vm
    _shared_vm = None
    if vm is not None:
        vm.dispose()


def resolve_agent_research_service(agent_wiring: Any | None) -> tuple[AgentResearchService | None, str]:
    """从 P01 接线结果解析 facade 与不可用原因 key（未接线/未就绪如实返回）。

    ``agent_wiring.ready`` 是宿主组装 + 启动隔离就绪的事实；未就绪时零派发，
    观察层不得伪装可用（R21）。
    """

    if agent_wiring is None:
        return None, UNAVAILABLE_NOT_WIRED
    service = getattr(agent_wiring, "service", None)
    if service is None:
        return None, UNAVAILABLE_NOT_WIRED
    if not bool(getattr(agent_wiring, "ready", False)):
        return None, UNAVAILABLE_NOT_READY
    return service, ""


def resolve_main_turn_intake(agent_wiring: Any | None) -> MainTurnIntakePort | None:
    """从组合根解析首问/开始计划受理端口；未就绪/未接线返回 ``None``（不伪造）。"""

    if agent_wiring is None or not bool(getattr(agent_wiring, "ready", False)):
        return None
    intake = getattr(agent_wiring, "intake", None)
    if intake is None:
        return None
    start_question = getattr(intake, "start_question", None)
    start_plan = getattr(intake, "start_plan", None)
    if not callable(start_question) or not callable(start_plan):
        return None
    return cast(MainTurnIntakePort, intake)
