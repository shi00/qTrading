"""DecisionCenterViewModel — 应用级统一决定/授权界面 VM（W-07，J14/F22/F24/F31/F36）。

职责边界：

- **统一聚合**：经 facade ``read_pending_answers`` 读取跨会话待答复/待交付/待消费
  事实，任何业务页都从同一处查看，不把决定散落在各页。
- **三动作**：授权类决定提供「允许 / 拒绝 / 稍后」；澄清类决定提供文本答复 +
  「稍后」。所有写操作仍经 ``AgentService.answer`` 唯一入口，不直连 DAO。
- **授权范围与撤销**：授权项先展示载荷中的工具/服务/参数摘要与期限；用户显式
  点击「查看授权范围」时才按会话首读取 ``grant_state`` / ``grant_revision``，
  撤销经注入的 port 发精确 CAS（无 port 时如实提示未接线，不伪造撤销成功）。
- **跨页提醒**：新增待处理、事项解除阻塞、交付异常都写入只读提醒并生成播报
  公告 key；VM 不 import flet、不抢焦点、不感知 locale（公告 key 由 View 渲染，
  经 ``SemanticsService.announce_message`` 非抢焦点播报）。
- **至多一次**：提交经 ``submitting_decision_id`` 单槽守卫；答复命令键与观察层
  共用 ``ui-answer:<decision_id>``，同一决定不会产生第二个有效答复（AC28/AC35）。

R2：取消传播；R19：单测覆盖三动作/幂等/期限/撤销/提醒；R21：未知即未知。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Protocol, cast

from core.agent_types import (
    AnswerDeliveryOutcome,
    AnswerDeliveryRequest,
    AnswerReceipt,
    DecisionSummary,
    GrantRevisionRequest,
    PendingAnswerFact,
    PendingAnswerPage,
    SaveAnswerRequest,
    SessionReadQuery,
    json_doc,
)
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from ui.viewmodels.research_observer_view_model import (
    LOCAL_DESKTOP_USER_SCOPE,
    PENDING_ANSWER_READ_LIMIT,
    UNAVAILABLE_NOT_WIRED,
)

#: 授权类决定（需要专用授权界面：范围/期限/撤销）。
AUTHORIZATION_DECISION_KINDS: Final[frozenset[str]] = frozenset({"tool_permission"})
#: 可在本界面直接允许/拒绝的授权类决定（其余授权类型仍只读）。
AUTHORIZATION_ACTIONABLE_KINDS: Final[frozenset[str]] = frozenset({"tool_permission"})
#: 可在本界面直接文本答复的澄清类决定。
CLARIFICATION_DECISION_KINDS: Final[frozenset[str]] = frozenset({"user_choice"})
#: 无轮澄清答复的提示 key：答复将在下一次提问时带上（不冒充已投递）。
CLARIFICATION_NEXT_TURN_KEY: Final[str] = "agent_research_clarification_next_turn"
#: 问题正文提取候选键（其余结构化参数不展开，避免把授权参数当普通文本展示）。
_QUESTION_KEYS: Final[tuple[str, ...]] = ("question", "prompt", "text", "title", "label", "description")
#: 答复命令键前缀：与观察层保持一致，同一决定跨入口命中同一命令（AC28）。
_ANSWER_COMMAND_KEY_PREFIX: Final[str] = "ui-answer:"
#: 许可状态可变（可撤销）集合；已撤销/已失败不重复撤销。
REVOCABLE_GRANT_STATES: Final[frozenset[str]] = frozenset({"pending", "granted", "claimed"})
#: 授权范围详情按需读取的会话首读上限（单次只读）。
_DETAIL_READ_LIMIT: Final[int] = 50
#: 参数摘要展示长度（只展示摘要前缀，不回显完整参数）。
_DIGEST_PREVIEW_LENGTH: Final[int] = 12

NOTICE_STALE = "agent_decision_stale"
NOTICE_EXPIRED = "agent_decision_expired"
NOTICE_READ_ONLY_KIND = "agent_decision_read_only_kind"
NOTICE_EMPTY_ANSWER = "agent_decision_empty_answer"
NOTICE_SAVED = "agent_decision_action_saved"
NOTICE_SCOPE_FIRST = "agent_decision_scope_required"
NOTICE_REVOKE_UNAVAILABLE = "agent_decision_revoke_unavailable"
NOTICE_REVOKE_DONE = "agent_decision_revoke_done"

REMINDER_RESOLVED = "agent_decision_reminder_resolved_fmt"
REMINDER_DELIVERY = "agent_decision_reminder_delivery_fmt"
REMINDER_CONSUMPTION = "agent_decision_reminder_consumption_fmt"

ANNOUNCE_NEW = "agent_decision_announce_new_fmt"
ANNOUNCE_RESOLVED = "agent_decision_announce_resolved_fmt"

ACTION_ALLOW = "allow"
ACTION_DENY = "deny"
ACTION_ANSWER = "answer"
ACTION_REVOKE = "revoke"
ACTION_RETRY_DELIVERY = "retry_delivery"


class DecisionCenterService(Protocol):
    """本 VM 需要的 facade 子集（P01 ``AgentService`` 结构性满足；UI 不 import app）。"""

    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> PendingAnswerPage: ...

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> Any: ...

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt: ...

    async def deliver_answer(self, request: AnswerDeliveryRequest) -> AnswerDeliveryOutcome: ...


class ToolGrantRevokerPort(Protocol):
    """工具许可撤销端口（组合根注入；UI 不 import services 实现）。"""

    async def revoke_tool_grant(self, request: GrantRevisionRequest) -> Any: ...


@dataclass(frozen=True)
class DecisionItemView:
    """跨会话待处理决定的只读投影（授权范围以摘要呈现，不回显原始参数）。"""

    decision_id: str
    session_id: str
    session_title: str | None
    execution_id: str
    round_id: str | None
    decision_kind: str
    revision: int
    stage: str
    created_at: str
    expires_at: str | None
    expiry_unknown: bool
    expired: bool
    question_text: str | None
    tool_name: str | None
    service_id: str | None
    config_version: str | None
    parameter_digest: str | None
    command_record_id: str | None
    delivery_state: str | None
    consumption_state: str | None

    @property
    def is_authorization(self) -> bool:
        """是否为工具调用授权类决定。"""

        return self.decision_kind in AUTHORIZATION_DECISION_KINDS

    @property
    def authorization_actionable(self) -> bool:
        """是否允许本界面直接允许/拒绝（仅可答复阶段的工具授权）。"""

        return self.is_authorization and self.stage == "answer" and self.decision_kind in AUTHORIZATION_ACTIONABLE_KINDS

    @property
    def clarification_actionable(self) -> bool:
        """是否允许本界面文本答复（仅可答复阶段的澄清类）。"""

        return self.stage == "answer" and self.decision_kind in CLARIFICATION_DECISION_KINDS

    @property
    def retryable_delivery(self) -> bool:
        """待交付且投递未被判为 unknown（unknown 不重发、不冒充已投递）。"""

        return self.stage == "delivery" and self.delivery_state == "not_delivered"


@dataclass(frozen=True)
class AuthorizationDetail:
    """授权范围详情：许可状态/版本来自会话首读的决定行（按需加载）。"""

    decision_id: str
    loaded: bool = False
    grant_state: str | None = None
    grant_revision: int | None = None
    error_code: str | None = None

    @property
    def revocable(self) -> bool:
        """许可状态处于可撤销集合且持有许可版本。"""

        return self.grant_revision is not None and (self.grant_state or "") in REVOCABLE_GRANT_STATES


@dataclass(frozen=True)
class ReminderView:
    """跨页提醒（已解除阻塞 / 交付异常 / 待消费）；``session_id`` 供定位源会话。"""

    seq: int
    kind: str
    key: str
    params: tuple[tuple[str, str], ...]
    session_id: str
    session_title: str | None
    decision_id: str


@dataclass(frozen=True)
class AnnouncementView:
    """非抢焦点读屏公告（key + 参数；View 渲染后经平台语义服务播报）。"""

    seq: int
    key: str
    params: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class DecisionCenterState:
    """统一决定界面的不可变 state snapshot。"""

    available: bool = False
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED
    loading: bool = False
    items: tuple[DecisionItemView, ...] = ()
    truncated: bool = False
    error_code: str | None = None
    notice_key: str | None = None
    notice_params: tuple[tuple[str, str], ...] = ()
    submitting_decision_id: str | None = None
    submitting_action: str | None = None
    answer_drafts: tuple[tuple[str, str], ...] = ()
    deferred_ids: tuple[str, ...] = ()
    details: tuple[AuthorizationDetail, ...] = ()
    reminders: tuple[ReminderView, ...] = ()
    announcement: AnnouncementView | None = None
    announcement_seq: int = 0
    revoke_available: bool = False

    @property
    def pending_count(self) -> int:
        """可操作待处理数（待答复 + 待交付；待消费不计入操作数）。"""

        return sum(1 for item in self.items if item.stage in ("answer", "delivery"))

    def item_by_id(self, decision_id: str) -> DecisionItemView | None:
        """按决定身份取投影（不存在返回 None，不猜）。"""

        return next((item for item in self.items if item.decision_id == decision_id), None)

    def draft_for(self, decision_id: str) -> str:
        """读取某决定的答复草稿（收起/重渲染后仍在）。"""

        return next((value for key, value in self.answer_drafts if key == decision_id), "")

    def detail_for(self, decision_id: str) -> AuthorizationDetail | None:
        """读取已加载的授权范围详情（未加载返回 None）。"""

        return next((detail for detail in self.details if detail.decision_id == decision_id), None)

    def is_deferred(self, decision_id: str) -> bool:
        """该决定是否被用户「稍后」暂缓（仍待处理，仅降噪）。"""

        return decision_id in self.deferred_ids

    def ordered_items(self) -> tuple[DecisionItemView, ...]:
        """展示顺序：未稍后项在前、稍后项在后（不隐去任何待处理项）。"""

        return tuple(item for item in self.items if not self.is_deferred(item.decision_id)) + tuple(
            item for item in self.items if self.is_deferred(item.decision_id)
        )


def _tag_text(value: Any) -> str | None:
    """枚举/文本标签 → 文本（None 透传，未知保持原值不猜语义）。"""

    if value is None:
        return None
    return getattr(value, "value", value)


def _answer_command_key(decision_id: str) -> str:
    """答复命令键：与观察层同前缀，同一决定跨入口命中同一命令（AC28）。"""

    return f"{_ANSWER_COMMAND_KEY_PREFIX}{decision_id}"


def _parse_payload(question_payload: str | None) -> dict[str, Any] | None:
    """解析 CanonicalJson 载荷（不可解释返回 None，不猜）。"""

    if not question_payload:
        return None
    try:
        decoded = json.loads(question_payload)
    except (TypeError, ValueError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _payload_text(payload: dict[str, Any] | None, *keys: str) -> str | None:
    """按候选键取非空文本（缺省 None）。"""

    if payload is None:
        return None
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _digest_preview(digest: str | None) -> str | None:
    """参数摘要只展示前缀（不回显完整参数，边界信息够用即可）。"""

    if not digest:
        return None
    return digest if len(digest) <= _DIGEST_PREVIEW_LENGTH else f"{digest[:_DIGEST_PREVIEW_LENGTH]}…"


def _parse_moment(value: str | None) -> datetime | None:
    """解析 ISO 时刻（不可解释返回 None，不把未知当未过期）。"""

    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _item_view(fact: PendingAnswerFact, *, now: datetime) -> DecisionItemView:
    """聚合事实 → 只读投影（阶段/命令态原样保留，未知不猜）。"""

    payload = _parse_payload(fact.question_payload)
    expires_moment = _parse_moment(fact.expires_at)
    return DecisionItemView(
        decision_id=fact.decision_id,
        session_id=fact.session_id,
        session_title=fact.session_title,
        execution_id=fact.execution_id,
        round_id=fact.round_id,
        decision_kind=fact.decision_kind,
        revision=fact.revision,
        stage=fact.stage,
        created_at=fact.created_at,
        expires_at=fact.expires_at,
        expiry_unknown=fact.expires_at is not None and expires_moment is None,
        expired=expires_moment is not None and expires_moment <= now,
        question_text=_payload_text(payload, *_QUESTION_KEYS),
        tool_name=_payload_text(payload, "tool_name", "tool", "tool_ref"),
        service_id=_payload_text(payload, "service_id", "mcp_service_id", "service"),
        config_version=_payload_text(payload, "config_version", "service_config_version"),
        parameter_digest=_digest_preview(_payload_text(payload, "parameter_digest")),
        command_record_id=fact.command_record_id,
        delivery_state=_tag_text(fact.delivery_state),
        consumption_state=_tag_text(fact.consumption_state),
    )


def _decision_summary(read: Any, decision_id: str) -> DecisionSummary | None:
    """从会话首读里取指定决定行（缺失返回 None）。"""

    decisions = getattr(read, "decisions", ()) or ()
    for decision in decisions:
        if getattr(decision, "decision_id", None) == decision_id:
            return cast(DecisionSummary, decision)
    return None


@dataclass(frozen=True)
class _Diff:
    """刷新差分：新增事项、解除阻塞事项、累计提醒与公告。"""

    new_items: tuple[DecisionItemView, ...]
    resolved: tuple[str, ...]
    reminders: tuple[ReminderView, ...]
    announcement: AnnouncementView | None


class DecisionCenterViewModel(ObservableViewModelMixin[DecisionCenterState]):
    """应用级统一决定/授权界面 VM（跨页共享，不抢焦点）。"""

    def __init__(
        self,
        service: DecisionCenterService | None,
        *,
        user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
        unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
        revoker: ToolGrantRevokerPort | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._service = service
        self._user_scope = user_scope
        self._revoker = revoker
        self._now = now or (lambda: datetime.now(UTC))
        self._state = DecisionCenterState(
            available=service is not None,
            unavailable_reason_key=unavailable_reason_key,
            revoke_available=revoker is not None,
        )
        self._known_ids: set[str] | None = None
        self._known_sessions: dict[str, tuple[str, str | None]] = {}
        self._reminder_seq = 0
        self._announcement_seq = 0
        self._init_mixin_fields()

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    async def refresh(self) -> None:
        """只读刷新跨会话待处理决定（不触碰执行状态）。"""

        if self._service is None or not self._state.available or self._state.loading:
            return
        self._track_inflight_cancellable()
        self._set_state(loading=True, error_code=None)
        try:
            page, error_code = await self._read_pending()
        finally:
            if self._state.loading:
                self._set_state(loading=False)
        if page is None:
            self._set_state(error_code=error_code)
            return
        now = self._now()
        items = tuple(_item_view(fact, now=now) for fact in page.items)
        diff = self._diff(items)
        self._set_state(
            items=items,
            truncated=bool(page.truncated),
            error_code=None,
            reminders=diff.reminders,
            announcement=diff.announcement or self._state.announcement,
            announcement_seq=self._announcement_seq,
        )
        self._known_ids = {item.decision_id for item in items}
        self._known_sessions = {item.decision_id: (item.session_id, item.session_title) for item in items}

    async def _read_pending(self) -> tuple[PendingAnswerPage | None, str | None]:
        """读取跨会话待处理聚合（失败如实给出错误码，不把失败读成 0 待办）。"""

        service = self._service
        reader = getattr(service, "read_pending_answers", None) if service is not None else None
        if not callable(reader):
            return None, None
        load = cast("Callable[..., Any]", reader)
        try:
            return await load(user_scope=self._user_scope, limit=PENDING_ANSWER_READ_LIMIT), None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return None, self._error_code(e)

    def _diff(self, items: tuple[DecisionItemView, ...]) -> _Diff:
        """计算新增/解除阻塞事项并生成提醒（首次读取只建基线，不打断用户）。"""

        current_ids = {item.decision_id for item in items}
        previous_ids = self._known_ids
        new_items = (
            () if previous_ids is None else tuple(item for item in items if item.decision_id not in previous_ids)
        )
        resolved = () if previous_ids is None else tuple(sorted(previous_ids - current_ids))
        current_by_id = {item.decision_id: item for item in items}
        reminders = []
        for reminder in self._state.reminders:
            current = current_by_id.get(reminder.decision_id)
            if reminder.kind == "resolved" or current is None or current.stage == reminder.kind:
                # 已解除阻塞的事项保留最后一次交付/消费提醒作为历史证据（R21）。
                reminders.append(reminder)
        seen = {(reminder.kind, reminder.decision_id) for reminder in reminders}
        for decision_id in resolved:
            if ("resolved", decision_id) in seen:
                continue
            session_id, session_title = self._known_sessions.get(decision_id, ("", None))
            self._reminder_seq += 1
            reminders.append(
                ReminderView(
                    seq=self._reminder_seq,
                    kind="resolved",
                    key=REMINDER_RESOLVED,
                    params=(("session", session_title or ""),),
                    session_id=session_id,
                    session_title=session_title,
                    decision_id=decision_id,
                )
            )
        for item in items:
            if item.stage == "delivery" and item.delivery_state in ("not_delivered", "delivery_unknown"):
                if ("delivery", item.decision_id) in seen:
                    continue
                self._reminder_seq += 1
                reminders.append(
                    ReminderView(
                        seq=self._reminder_seq,
                        kind="delivery",
                        key=REMINDER_DELIVERY,
                        params=(
                            ("session", item.session_title or ""),
                            ("state", item.delivery_state),
                        ),
                        session_id=item.session_id,
                        session_title=item.session_title,
                        decision_id=item.decision_id,
                    )
                )
            elif item.stage == "consumption" and ("consumption", item.decision_id) not in seen:
                self._reminder_seq += 1
                reminders.append(
                    ReminderView(
                        seq=self._reminder_seq,
                        kind="consumption",
                        key=REMINDER_CONSUMPTION,
                        params=(("session", item.session_title or ""),),
                        session_id=item.session_id,
                        session_title=item.session_title,
                        decision_id=item.decision_id,
                    )
                )
        return _Diff(
            new_items=new_items,
            resolved=resolved,
            reminders=tuple(reminders),
            announcement=self._build_announcement(new_items=new_items, resolved=resolved),
        )

    def _build_announcement(
        self, *, new_items: tuple[DecisionItemView, ...], resolved: tuple[str, ...]
    ) -> AnnouncementView | None:
        """生成非抢焦点公告（新增优先；无事件返回 None）。"""

        if new_items:
            self._announcement_seq += 1
            return AnnouncementView(
                seq=self._announcement_seq,
                key=ANNOUNCE_NEW,
                params=(("count", str(len(new_items))),),
            )
        if resolved:
            self._announcement_seq += 1
            _session_id, session_title = self._known_sessions.get(resolved[0], ("", None))
            return AnnouncementView(
                seq=self._announcement_seq,
                key=ANNOUNCE_RESOLVED,
                params=(("session", session_title or ""),),
            )
        return None

    # ------------------------------------------------------------------
    # 三动作：允许 / 拒绝 / 稍后
    # ------------------------------------------------------------------
    async def allow(self, decision_id: str) -> None:
        """允许授权（approve=true）；仅可答复阶段的工具授权。"""

        await self._answer(decision_id, action=ACTION_ALLOW, payload={"approve": True})

    async def deny(self, decision_id: str) -> None:
        """拒绝授权（approve=false）；旧项零批准、不重放。"""

        await self._answer(decision_id, action=ACTION_DENY, payload={"approve": False})

    async def submit_answer(self, decision_id: str) -> None:
        """提交澄清答复（文本）；空答复不提交。"""

        text = self._state.draft_for(decision_id).strip()
        if not text:
            self._set_state(notice_key=NOTICE_EMPTY_ANSWER, notice_params=())
            return
        await self._answer(decision_id, action=ACTION_ANSWER, payload={"text": text})

    def defer(self, decision_id: str) -> None:
        """「稍后」只暂缓提醒（不写服务、不延长期限、不从待处理移除）。"""

        item = self._state.item_by_id(decision_id)
        if item is None or decision_id in self._state.deferred_ids:
            return
        self._set_state(deferred_ids=(*self._state.deferred_ids, decision_id))

    def set_answer_draft(self, decision_id: str, text: str) -> None:
        """更新草稿（纯 UI 状态；不写服务）。"""

        drafts = tuple((key, value) for key, value in self._state.answer_drafts if key != decision_id)
        self._set_state(answer_drafts=(*drafts, (decision_id, text)))

    def report_refusal(self, reason_key: str) -> None:
        """仲裁层拒绝时如实展示原因（不排队、不冒充已提交）。"""

        self._set_state(notice_key=reason_key, notice_params=())

    async def _answer(self, decision_id: str, *, action: str, payload: dict[str, Any]) -> None:
        """唯一写入口：校验归属后经 facade ``answer`` 保存并尝试交付一次。"""

        if self._service is None or not self._state.available:
            self._set_state(notice_key=self._state.unavailable_reason_key, notice_params=())
            return
        item = self._state.item_by_id(decision_id)
        if item is None or item.stage != "answer":
            self._set_state(notice_key=NOTICE_STALE, notice_params=())
            return
        actionable = (
            item.authorization_actionable if action in (ACTION_ALLOW, ACTION_DENY) else item.clarification_actionable
        )
        if not actionable:
            self._set_state(notice_key=NOTICE_READ_ONLY_KIND, notice_params=())
            return
        if item.expired:
            self._set_state(notice_key=NOTICE_EXPIRED, notice_params=())
            return
        if self._state.submitting_decision_id is not None:
            # 至多一次提交：同一时刻只允许一个在途决定（AC28/AC35）。
            return
        self._set_state(
            submitting_decision_id=decision_id,
            submitting_action=action,
            notice_key=None,
            notice_params=(),
            error_code=None,
        )
        self._mark_inflight_committing()
        try:
            receipt = await self._service.answer(
                SaveAnswerRequest(
                    user_scope=self._user_scope,
                    decision_id=decision_id,
                    client_command_id=_answer_command_key(decision_id),
                    expected_decision_revision=item.revision,
                    expected_execution_id=item.execution_id,
                    expected_round_id=item.round_id,
                    answer_payload=json_doc(payload),
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(
                submitting_decision_id=None,
                submitting_action=None,
                error_code=self._error_code(e),
            )
            return
        command_record_id = getattr(receipt, "command_record_id", None)
        if item.round_id is not None and isinstance(command_record_id, str) and command_record_id:
            # 有轮答复才走原生投递；无轮澄清答复在下一轮受理时消费。
            await self._attempt_delivery(command_record_id)
        if action == ACTION_ANSWER:
            self._set_state(
                answer_drafts=tuple((key, value) for key, value in self._state.answer_drafts if key != decision_id)
            )
        self._set_state(
            submitting_decision_id=None,
            submitting_action=None,
            notice_key=NOTICE_SAVED if item.round_id is not None else CLARIFICATION_NEXT_TURN_KEY,
            notice_params=(),
        )
        await self.refresh()

    async def _attempt_delivery(self, command_record_id: str) -> None:
        """交付一次原答复命令（失败保持待交付；unknown 不重发）。"""

        deliver = getattr(self._service, "deliver_answer", None) if self._service is not None else None
        if not callable(deliver):
            return
        deliver_once = cast("Callable[[AnswerDeliveryRequest], Any]", deliver)
        try:
            await deliver_once(AnswerDeliveryRequest(user_scope=self._user_scope, command_record_id=command_record_id))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(error_code=self._error_code(e))

    async def retry_delivery(self, decision_id: str) -> None:
        """按原答复命令重试交付（不新建答复、不换命令键）。"""

        if self._service is None or not self._state.available:
            self._set_state(notice_key=self._state.unavailable_reason_key, notice_params=())
            return
        item = self._state.item_by_id(decision_id)
        if item is None or item.stage != "delivery" or item.command_record_id is None:
            self._set_state(notice_key=NOTICE_STALE, notice_params=())
            return
        if not item.retryable_delivery:
            self._set_state(notice_key="agent_research_delivery_unknown", notice_params=())
            return
        if self._state.submitting_decision_id is not None:
            return
        self._set_state(
            submitting_decision_id=decision_id,
            submitting_action=ACTION_RETRY_DELIVERY,
            notice_key=None,
            notice_params=(),
        )
        await self._attempt_delivery(item.command_record_id)
        self._set_state(submitting_decision_id=None, submitting_action=None)
        await self.refresh()

    # ------------------------------------------------------------------
    # 授权范围 / 撤销
    # ------------------------------------------------------------------
    async def load_authorization_detail(self, decision_id: str) -> None:
        """按会话首读加载授权范围详情（许可状态/版本；只读）。"""

        item = self._state.item_by_id(decision_id)
        if self._service is None or item is None or not item.is_authorization:
            self._set_state(notice_key=NOTICE_STALE, notice_params=())
            return
        existing = self._state.detail_for(decision_id)
        if existing is not None and existing.loaded:
            return
        try:
            read = await self._service.read_session(
                SessionReadQuery(session_id=item.session_id, limit=_DETAIL_READ_LIMIT),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            detail = AuthorizationDetail(decision_id=decision_id, loaded=False, error_code=self._error_code(e))
        else:
            summary = _decision_summary(read, decision_id)
            detail = AuthorizationDetail(
                decision_id=decision_id,
                loaded=True,
                grant_state=_tag_text(getattr(summary, "grant_state", None)),
                grant_revision=getattr(summary, "grant_revision", None),
                error_code=None if summary is not None else "decision_not_in_session_read",
            )
        details = tuple(candidate for candidate in self._state.details if candidate.decision_id != decision_id)
        self._set_state(details=(*details, detail))

    async def revoke(self, decision_id: str) -> None:
        """撤销工具许可（精确 CAS；未加载详情/未接线时如实拒绝）。"""

        if self._service is None or not self._state.available:
            self._set_state(notice_key=self._state.unavailable_reason_key, notice_params=())
            return
        detail = self._state.detail_for(decision_id)
        if detail is None or not detail.loaded:
            self._set_state(notice_key=NOTICE_SCOPE_FIRST, notice_params=())
            return
        if detail.grant_revision is None:
            self._set_state(notice_key="agent_decision_grant_unknown", notice_params=())
            return
        if self._revoker is None:
            self._set_state(notice_key=NOTICE_REVOKE_UNAVAILABLE, notice_params=())
            return
        if not detail.revocable:
            self._set_state(
                notice_key="agent_decision_grant_state_fmt",
                notice_params=(("state", detail.grant_state or "unknown"),),
            )
            return
        if self._state.submitting_decision_id is not None:
            return
        self._set_state(
            submitting_decision_id=decision_id,
            submitting_action=ACTION_REVOKE,
            notice_key=None,
            notice_params=(),
            error_code=None,
        )
        self._mark_inflight_committing()
        try:
            fact = await self._revoker.revoke_tool_grant(
                GrantRevisionRequest(
                    user_scope=self._user_scope,
                    decision_id=decision_id,
                    expected_grant_revision=detail.grant_revision,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(
                submitting_decision_id=None,
                submitting_action=None,
                error_code=self._error_code(e),
            )
            return
        revoked_state = _tag_text(getattr(fact, "grant_state", None)) or "revoked"
        updated = AuthorizationDetail(
            decision_id=decision_id,
            loaded=True,
            grant_state=revoked_state,
            grant_revision=getattr(fact, "grant_revision", detail.grant_revision),
            error_code=None,
        )
        details = tuple(candidate for candidate in self._state.details if candidate.decision_id != decision_id)
        self._set_state(
            submitting_decision_id=None,
            submitting_action=None,
            details=(*details, updated),
            notice_key=NOTICE_REVOKE_DONE,
            notice_params=(),
        )
        await self.refresh()

    # ------------------------------------------------------------------
    # 提醒
    # ------------------------------------------------------------------
    def dismiss_reminder(self, seq: int) -> None:
        """忽略一条跨页提醒（只清本地提醒，不改服务事实）。"""

        reminders = tuple(reminder for reminder in self._state.reminders if reminder.seq != seq)
        if len(reminders) != len(self._state.reminders):
            self._set_state(reminders=reminders)

    @staticmethod
    def _error_code(error: Exception) -> str:
        """错误码提取（与观察层同口径，不吞异常信息）。"""

        return str(getattr(error, "code", None) or type(error).__name__)


def resolve_tool_grant_revoker(agent_wiring: Any | None) -> ToolGrantRevokerPort | None:
    """从接线结果解析许可撤销端口（未提供即为 None，不伪造撤销能力）。"""

    if agent_wiring is None:
        return None
    for attribute in ("tool_grant_revoker", "tool_grants", "service"):
        candidate = getattr(agent_wiring, attribute, None)
        if callable(getattr(candidate, "revoke_tool_grant", None)):
            return cast(ToolGrantRevokerPort, candidate)
    return None


_shared_vm: DecisionCenterViewModel | None = None


def shared_decision_center(
    service: DecisionCenterService | None,
    *,
    user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
    unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
    revoker: ToolGrantRevokerPort | None = None,
) -> DecisionCenterViewModel:
    """取本会话共享的统一决定 VM（同一 facade/撤销端口复用；变化或已销毁则重建）。"""

    global _shared_vm
    vm = _shared_vm
    if (
        vm is not None
        and not vm.disposed
        and vm._service is service
        and vm._revoker is revoker
        and vm._user_scope == user_scope
    ):
        return vm
    if vm is not None:
        vm.dispose()
    _shared_vm = DecisionCenterViewModel(
        service,
        user_scope=user_scope,
        unavailable_reason_key=unavailable_reason_key,
        revoker=revoker,
    )
    return _shared_vm


def reset_shared_decision_center() -> None:
    """销毁共享实例（测试隔离/会话重建；幂等）。"""

    global _shared_vm
    vm = _shared_vm
    _shared_vm = None
    if vm is not None:
        vm.dispose()
