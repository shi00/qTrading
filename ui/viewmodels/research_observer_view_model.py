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
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from core.agent_types import (
    AnswerReceipt,
    ArtifactReadQuery,
    DraftRead,
    DraftReadQuery,
    HistoryPage,
    HistoryQuery,
    HistoryRoundSelection,
    SaveAnswerRequest,
    SessionRead,
    SessionReadQuery,
    json_doc,
)
from ui.viewmodels.observable_mixin import ObservableViewModelMixin

#: 桌面单用户域（与 app.agent_wiring 的默认 user_scope 保持一致；UI 层不 import app）。
LOCAL_DESKTOP_USER_SCOPE = "local-desktop"

#: 只有「用户选择/澄清」类决定允许经本观察层以文本答复；授权类决定必须走专用界面。
_ANSWERABLE_DECISION_KINDS = frozenset({"user_choice"})

#: 问题正文提取的候选键（其余结构化参数保持不展开，避免把授权参数当普通文本展示）。
_QUESTION_KEYS = ("question", "prompt", "text", "title", "label", "description")

UNAVAILABLE_NOT_WIRED = "agent_research_not_wired"
UNAVAILABLE_NOT_READY = "agent_research_not_ready"


class AgentResearchService(Protocol):
    """本 VM 需要的 facade 子集（P01 ``AgentService`` 结构性满足，UI 不 import app）。"""

    async def search(self, query: HistoryQuery, *, user_scope: str) -> HistoryPage: ...

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead: ...

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> Any: ...

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt: ...

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

    def draft_for(self, decision_id: str) -> str:
        """读取某决定的答复草稿（收起/重渲染后仍在）。"""

        for key, value in self.answer_drafts:
            if key == decision_id:
                return value
        return ""

    def is_collapsed(self, execution_id: str) -> bool:
        """子任务节点是否处于收起态（展开态由共享 VM 持久化，切页不丢）。"""

        return execution_id in self.collapsed_execution_ids

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
    ) -> None:
        self._service = service
        self._user_scope = user_scope
        self._session_key: int | None = id(service) if service is not None else None
        # 原始首读投影：展开/收起/选中只重算展示，不重复读取、不触碰执行。
        self._raw_executions: tuple[Any, ...] = ()
        self._raw_messages: tuple[Any, ...] = ()
        self._raw_artifacts: tuple[Any, ...] = ()
        self._state = ResearchObserverState(
            available=service is not None,
            unavailable_reason_key=unavailable_reason_key if service is None else "",
        )
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """重新读取最近研究会话与待处理决定（只读；不触碰执行状态）。"""

        if self._service is None or not self._state.available:
            return
        if self._state.loading:
            return
        self._track_inflight_cancellable()
        self._set_state(loading=True, error_key=None, last_error_code=None)
        try:
            history = await self._service.search(
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
            session_id = next((hit.session_id for hit in history.hits), None)
            if session_id is None:
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
                    last_updated_at=history.read_moment,
                )
                return
            read = await self._service.read_session(
                SessionReadQuery(session_id=session_id, limit=50),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            # R2：取消必须传播；先释放 loading，避免会话存续期间刷新永久被拦截。
            self._set_state(loading=False)
            raise
        except Exception as e:  # facade 已脱敏的类型化错误；未知错误如实报码，不伪装空结果
            # 断线不清空既有快照：保留上次已知状态，只如实报错（R21）。
            self._set_state(loading=False, last_error_code=self._error_code(e))
            return

        pending = _pending_decisions(read)
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
            pending_count=len(pending),
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

        ``execution_id`` / ``round_id`` 必须是界面呈现该决定时的归属：与当前投影
        任一不符即视为过期视图（拒绝，不按同 id 作答另一轮）；已过期决定在 UI 层
        直接拒绝。授权类决定（data_consent / tool_grant）拒绝在本层提交（需专用
        授权界面）；空草稿不提交；facade 拒绝时保留草稿并如实给出错误码。
        """

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        decision = next((item for item in self._state.decisions if item.decision_id == decision_id), None)
        if decision is None or decision.execution_id != execution_id or decision.round_id != round_id:
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
                    client_command_id=f"ui-{uuid.uuid4().hex}",
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

        delivery_state = getattr(receipt.delivery_state, "value", str(receipt.delivery_state))
        self._set_state(
            submitting=False,
            last_answer_delivery_state=delivery_state,
            answer_drafts=tuple((key, value) for key, value in self._state.answer_drafts if key != decision_id),
        )
        await self.refresh()

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
) -> ResearchObserverViewModel:
    """取本会话共享的观察层 VM（同一 facade 复用；facade 变化或已销毁则重建）。"""

    global _shared_vm
    session_key = id(service) if service is not None else None
    if _shared_vm is None or _shared_vm.disposed or _shared_vm._session_key != session_key:
        stale = _shared_vm
        _shared_vm = ResearchObserverViewModel(
            service,
            user_scope=user_scope,
            unavailable_reason_key=unavailable_reason_key,
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
