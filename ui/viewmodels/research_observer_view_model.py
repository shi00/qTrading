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
from typing import Any, Protocol
from core.agent_types import (
    AnswerReceipt,
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

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt: ...


@dataclass(frozen=True)
class PendingDecision:
    """必要决定的只读投影（展示用；不回显完整授权参数）。"""

    decision_id: str
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

    def draft_for(self, decision_id: str) -> str:
        """读取某决定的答复草稿（收起/重渲染后仍在）。"""

        for key, value in self.answer_drafts:
            if key == decision_id:
                return value
        return ""


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
                decision_kind=decision.decision_kind,
                revision=decision.revision,
                created_at=decision.created_at,
                expires_at=decision.expires_at,
                question_text=_extract_question_text(decision.question_payload),
            )
        )
    return tuple(pending)


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
                self._set_state(
                    loading=False,
                    session_id=None,
                    session_title=None,
                    executions=(),
                    decisions=(),
                    pending_count=0,
                    last_updated_at=history.read_moment,
                )
                return
            read = await self._service.read_session(
                SessionReadQuery(session_id=session_id, limit=20),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:  # facade 已脱敏的类型化错误；未知错误如实报码，不伪装空结果
            self._set_state(loading=False, last_error_code=self._error_code(e))
            return

        pending = _pending_decisions(read)
        self._set_state(
            loading=False,
            session_id=session_id,
            session_title=read.summary.title,
            executions=tuple((execution.execution_id, execution.display_state) for execution in read.executions),
            decisions=pending,
            pending_count=len(pending),
            last_updated_at=read.summary.updated_at,
        )

    def set_answer_draft(self, decision_id: str, text: str) -> None:
        """记录答复草稿（收起观察层不清稿）。"""

        drafts = tuple((key, value) for key, value in self._state.answer_drafts if key != decision_id)
        self._set_state(answer_drafts=(*drafts, (decision_id, text)))

    async def submit_answer(self, decision_id: str) -> None:
        """提交答复：唯一写入口经 facade ``answer``（统一决定，不直连 DAO）。

        授权类决定（data_consent / tool_grant）拒绝在本层提交（需专用授权界面）；
        空草稿不提交；facade 拒绝时保留草稿并如实给出错误码。
        """

        if self._service is None or not self._state.available:
            self._set_state(error_key=self._state.unavailable_reason_key)
            return
        decision = next((item for item in self._state.decisions if item.decision_id == decision_id), None)
        if decision is None:
            self._set_state(error_key="agent_research_decision_stale")
            return
        if not decision.answerable:
            self._set_state(error_key="agent_research_decision_dedicated")
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
