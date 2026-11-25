"""ResearchObserverViewModel — 壳层公共研究观察层与必要决定的共享 VM（检视 P02）。

设计边界（UX §3.1、P05/P10/P12，F01/F19/F31/F36）：

- **共享实例**：观察层是壳层公共区的一层，不是某个业务页的私有状态；
  同一会话内经 ``shared_research_observer`` 复用同一 VM，展开/收起不换实例、
  不丢快照（收起只是 View 的可见性变化）。
- **观察为主、控制受限**：默认只调用 facade 的只读查询与答复入口；W-01 起新增
  显式控制命令族（暂停/继续/终止/未知结案/重试/排队撤回）经同一 facade
  ``control`` / ``retry_execution`` 提交，不解锁其它写路径。
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
import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Final, Protocol, cast

from core.agent_types import (
    AnswerDeliveryOutcome,
    AnswerDeliveryRequest,
    AnswerReceipt,
    ArtifactReadQuery,
    CommandRef,
    DraftRead,
    DraftReadQuery,
    EventReadQuery,
    ExecutionControlKind,
    ExecutionControlReceipt,
    ExecutionControlRequest,
    HistoryPage,
    HistoryQuery,
    HistoryRoundSelection,
    MCPServiceIdentity,
    MessageRole,
    Mode,
    NON_RECOVERABLE_DISPLAY_STATES,
    PAUSABLE_DISPLAY_STATES,
    PendingAnswerFact,
    PendingAnswerPage,
    RESUMABLE_DISPLAY_STATES,
    RETRYABLE_EXECUTION_DISPLAY_STATES,
    RETRYABLE_LEAF_DISPLAY_STATES,
    RetryExecutionReceipt,
    RetryExecutionRequest,
    RevisionReceipt,
    SaveAnswerRequest,
    SessionAction,
    SessionChangeCommand,
    SessionRead,
    SessionReadQuery,
    WITHDRAWABLE_DISPLAY_STATES,
    json_doc,
)
from ui.viewmodels.ai_brain_settings_view_model import ROLE_PROMPT_NAME_KEYS
from ui.viewmodels.observable_mixin import ObservableViewModelMixin
from utils.config_handler import ConfigHandler
from utils.config_models import ROLE_PROMPT_DEFAULTS
from utils.thread_pool import TaskType, ThreadPoolManager

if TYPE_CHECKING:
    # N01：``services.agent_service`` 包初始化会拉入 Agent 专用依赖（DBOS / pydantic_ai）。
    # 该导入只用于类型标注；运行时仅在已接线（intake 存在）路径内惰性导入。
    from services.agent_service.main_turn import MainTurnIntakeResult, PlanReference
    from services.agent_service.model import StreamedTextFragment

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

#: 派发原因码：受理端口未接线派发器 / 派发器已接线但未给出原因码。
DISPATCH_REASON_NOT_WIRED = "not_wired"
DISPATCH_REASON_UNKNOWN = "zero_dispatch"

#: 首问可选模式（三值；直答是循环动作，不作为第四模式）。
COMPOSER_MODES: Final[tuple[str, ...]] = ("discuss", "plan", "execute")
#: 缺省模式：执行（F21 默认执行）。
DEFAULT_COMPOSER_MODE: Final[str] = "execute"
#: 精确导航目标在读取期间被更新的最大重读次数（超过则如实报码，不落过期会话）。
_REFRESH_TARGET_ATTEMPTS: Final[int] = 3

#: 会话列表：历史扫描上限（HistoryQuery.limit 硬上界 50）。
_SESSION_SCAN_LIMIT: Final[int] = 50
#: 会话列表展示上限（每个会话需一次轻量摘要读取，限制读取扇出）。
SESSION_LIST_LIMIT: Final[int] = 10

#: 会话状态文案 key（未知/已清理/已归档/正常分列；不把未知读成「正常」）。
SESSION_STATUS_UNKNOWN = "agent_research_session_status_unknown"
SESSION_STATUS_CLEANED = "agent_research_session_status_cleaned"
SESSION_STATUS_ARCHIVED = "agent_research_session_status_archived"
SESSION_STATUS_ACTIVE = "agent_research_session_status_active"

# --- W-12 研究侧模型/角色选择、费用透明与耗时分段 ---

#: 研究场景 id（与 ``resolve_agent_model_binding`` 解算的连接来源同一配置域）。
AGENT_RESEARCH_SCENARIO_ID: Final[str] = "agent_research"

#: 研究角色候选（与设置页角色 Prompt 同一来源；子角色只执行主角色分配的单任务，
#: 不能作为首问主研究角色，选择即显式拒绝）。
RESEARCH_ROLE_KEYS: Final[tuple[str, ...]] = ("main", "sub")

#: 角色是否可作为首问主研究角色（子角色仅由主角色分配执行）。
_RESEARCH_MAIN_ROLE: Final[str] = "main"

#: 费用投影状态 → i18n key（未知/未计价/预留分列，不合并成伪零）。
COST_STATE_KEYS: Final[Mapping[str, str]] = {
    "known": "agent_research_cost_state_known",
    "partial": "agent_research_cost_state_partial",
    "unpriced": "agent_research_cost_state_unpriced",
    "reserved": "agent_research_cost_state_reserved",
    "unknown": "agent_research_cost_state_unknown",
    "failed": "agent_research_cost_state_failed",
}

#: 发送前预算状态。
BUDGET_ESTIMATED = "estimated"
BUDGET_UNPRICED = "unpriced"
BUDGET_UNKNOWN = "unknown"

#: 五段耗时（排队/用户等待/实际工作/模型等待/保存）与来源字段说明 key。
#: 来源字段取自既有只读投影，不新增持久化字段、不做估算（R21）。
DURATION_SEGMENTS: Final[tuple[tuple[str, str, str], ...]] = (
    ("queue", "agent_research_duration_queue", "agent_research_duration_source_queue"),
    ("user_wait", "agent_research_duration_user_wait", "agent_research_duration_source_user_wait"),
    ("work", "agent_research_duration_work", "agent_research_duration_source_work"),
    ("model_wait", "agent_research_duration_model_wait", "agent_research_duration_source_model_wait"),
    ("save", "agent_research_duration_save", "agent_research_duration_source_save"),
)
DURATION_UNKNOWN_KEY: Final[str] = "agent_research_duration_unknown"

#: 耗时分段推导所需的执行级快照页大小：消息页按 session_seq DESC 取最近 N 条，
#: 首个用户提问超出该页时「排队」保持未知（不凭空估算）。
# NOTE(lazy): 排队段依赖首个用户消息落在快照消息页内. ceiling: 单执行消息超过 20 条且首个用户消息在页外时该段为未知. upgrade: 服务层提供执行级受理时间投影（如 execution.accepted_at）后改为直接读取该字段.
_DURATION_MESSAGE_PAGE_LIMIT: Final[int] = 20

#: 累计费用覆盖的执行数上限（每个执行需一次快照 + 一次费用投影；超出如实标注截断）。
CUMULATIVE_EXECUTION_LIMIT: Final[int] = 10

#: 首层进度文案 key（W-13）：「在做什么 / 等什么 / 已保存 / 下一步」。
#: 数据缺失一律用 ``PROGRESS_UNKNOWN_KEY`` 显式未知，不填业务上合法的默认值（R21）。
PROGRESS_UNKNOWN_KEY = "agent_research_progress_unknown"
PROGRESS_DOING_STREAMING = "agent_research_progress_doing_streaming"
PROGRESS_DOING_RUNNING = "agent_research_progress_doing_running"
PROGRESS_DOING_WAITING_USER = "agent_research_progress_doing_waiting_user"
PROGRESS_DOING_IDLE = "agent_research_progress_doing_idle"
PROGRESS_DOING_NONE = "agent_research_progress_doing_none"
PROGRESS_WAITING_USER = "agent_research_progress_waiting_user"
PROGRESS_WAITING_NONE = "agent_research_progress_waiting_none"
PROGRESS_SAVED_ARTIFACTS = "agent_research_progress_saved_artifacts"
PROGRESS_SAVED_NONE = "agent_research_progress_saved_none"
PROGRESS_NEXT_ANSWER = "agent_research_progress_next_answer"
PROGRESS_NEXT_WAIT = "agent_research_progress_next_wait"
PROGRESS_NEXT_IDLE = "agent_research_progress_next_idle"

#: 在途执行展示状态（排队/运行/依赖/人工等待/暂停中/已暂停/收尾中）。
_ACTIVE_DISPLAY_STATES: Final[frozenset[str]] = frozenset(
    {"queued", "running", "waiting_dependency", "waiting_user", "pausing", "paused", "ending"}
)

#: 返焦/重连追平的页大小与页数上限（有界读取；未追平如实报码，不宣称已核）。
_EVENT_CATCH_UP_PAGE: Final[int] = 50
_EVENT_CATCH_UP_MAX_PAGES: Final[int] = 20
CATCH_UP_INCOMPLETE = "catch_up_incomplete"


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
        role_prompt_refs: Sequence[tuple[str, str]] = (),
        skill_refs: Sequence[tuple[str, str]] = (),
        mcp_selection: tuple[MCPServiceIdentity, ...] = (),
    ) -> MainTurnIntakeResult: ...

    async def start_plan(
        self,
        reference: PlanReference,
        *,
        session_id: str,
        expected_revision: int,
    ) -> MainTurnIntakeResult: ...


class MCPScopeCatalogPort(Protocol):
    """研究侧 MCP 范围选择所需的服务目录（组合根注入；UI 不 import app）。"""

    async def list_services(self) -> tuple[Any, ...]: ...


class ModelCatalogPort(Protocol):
    """既有模型连接目录读取面（``ModelConfigurationService`` 子集，只读快照）。"""

    async def get_snapshot(self) -> Any: ...


class AgentResearchService(Protocol):
    """本 VM 需要的 facade 子集（P01 ``AgentService`` 结构性满足，UI 不 import app）。"""

    async def search(self, query: HistoryQuery, *, user_scope: str) -> HistoryPage: ...

    async def read_session(self, query: SessionReadQuery, *, user_scope: str) -> SessionRead: ...

    async def read_artifact(self, query: ArtifactReadQuery, *, user_scope: str) -> Any: ...

    async def answer(self, request: SaveAnswerRequest) -> AnswerReceipt: ...

    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> PendingAnswerPage: ...

    async def deliver_answer(self, request: AnswerDeliveryRequest) -> AnswerDeliveryOutcome: ...

    async def read_draft(self, query: DraftReadQuery, *, user_scope: str) -> DraftRead: ...

    async def control(self, request: ExecutionControlRequest) -> RevisionReceipt | ExecutionControlReceipt: ...

    async def retry_execution(self, request: RetryExecutionRequest) -> RetryExecutionReceipt: ...


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
    """子任务树的只读节点（``depth`` 由父子关系展开计算，展开态由 VM 持久化）。

    ``node_id`` / ``execution_kind`` / ``current_round_id`` / ``revision`` /
    ``dispatch_closed`` 为 W-01 控制动作所需的权威身份与 CAS 预期版；缺失时
    对应动作一律禁用并给出原因，不按猜测值提交。
    """

    execution_id: str
    parent_execution_id: str | None
    display_state: str
    goal_coverage: str
    model_attempt_count: int
    tool_call_count: int
    active_ms: int
    depth: int
    has_children: bool
    node_id: str = ""
    execution_kind: str = ""
    current_round_id: str | None = None
    revision: int = 0
    dispatch_closed: bool = False


@dataclass(frozen=True)
class ControlActionView:
    """单个可用/不可用控制动作（不可用时给出原因 key，不静默隐藏）。"""

    kind: str
    label_key: str
    enabled: bool
    disabled_reason_key: str | None = None
    requires_reason: bool = False


@dataclass(frozen=True)
class ControlResultView:
    """控制动作结果（成功/冲突/未知如实分列；未知不读成成功，R21）。"""

    execution_id: str
    kind: str
    display_state: str
    applied: bool
    replayed: bool
    reason: str | None
    error_code: str | None
    released_slot_count: int
    open_slot_count: int
    native_control_state: str


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
class SessionListItemView:
    """会话列表项只读投影（标题/时点/状态/元数据版）。

    ``title`` 为 None 表示标题读取失败或缺失（View 显示未命名，不伪造标题）；
    ``revision`` 为 None 表示该会话摘要未读到，改名/置顶/归档等 CAS 动作被拒绝
    （不以猜测的预期版覆盖）。
    """

    session_id: str
    title: str | None
    updated_at: str | None
    revision: int | None
    pinned: bool
    archived: bool
    cleaned: bool
    status_unknown: bool = False

    @property
    def status_key(self) -> str:
        """状态文案 key（未知优先，不把读取失败读成「正常」）。"""

        if self.status_unknown:
            return SESSION_STATUS_UNKNOWN
        if self.cleaned:
            return SESSION_STATUS_CLEANED
        if self.archived:
            return SESSION_STATUS_ARCHIVED
        return SESSION_STATUS_ACTIVE


@dataclass(frozen=True)
class DispatchReasonView:
    """零派发的结构化原因（稳定原因码 + 键值参数；View 按码 i18n，不拼接自由文本）。"""

    code: str
    params: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ModelConnectionOption:
    """工具栏可选的模型连接（研究场景路由候选的只读投影 + 资格判定）。

    ``position`` 为路由候选序（1 起）；``effective`` 为真正承载本次研究的连接
    （``resolve_agent_model_binding`` 按场景路由首选解算）。非首选候选仍可选定
    （仅在首选不可用时按路由回退顺序生效）；资格不合法的连接带 ``reject_key``，
    由命令显式拒绝而不是静默隐藏。
    """

    connection_id: str
    display_name: str
    model: str
    context_window: int | None
    position: int
    effective: bool
    reject_key: str | None

    @property
    def eligible(self) -> bool:
        return self.reject_key is None


@dataclass(frozen=True)
class RoleOption:
    """可选的研究角色（与设置页角色 Prompt 同一来源，版本随首问冻结）。"""

    role_key: str
    name_key: str
    version: str | None
    reject_key: str | None

    @property
    def eligible(self) -> bool:
        return self.reject_key is None


@dataclass(frozen=True)
class BudgetEstimate:
    """发送前预算提示：可估算时给金额与单位，不可估时显式未计价/未知（R21）。

    ``amount_cny`` 为 ``None`` 时不得在界面显示 0；仅 ``estimated`` 状态携带金额；
    ``basis_key`` 说明估算口径（仅正文 + 输出预留，不含系统提示/资料/工具）。
    """

    state: str
    state_key: str
    amount_cny: Decimal | None
    unit_key: str
    basis_key: str | None
    input_tokens: int | None
    output_reserve_tokens: int | None
    tokens_verified: bool


@dataclass(frozen=True)
class CostUsageView:
    """费用只读投影（本轮/累计）：已知、未计价、预留中、未知、读取失败分列。"""

    state: str
    state_key: str
    known_cny: Decimal | None
    reserved_cny: Decimal | None
    unpriced_attempts: int | None
    truncated: bool
    read_error: str | None


@dataclass(frozen=True)
class DurationSegment:
    """单段耗时：有来源事实给毫秒 + 来源字段 key，缺来源显式未知（不以 0 冒充）。"""

    segment: str
    label_key: str
    source_key: str
    value_ms: int | None
    unknown_key: str | None


@dataclass(frozen=True)
class ProgressSummaryView:
    """首层进度四问的 i18n key（数据缺失为 ``PROGRESS_UNKNOWN_KEY``，不猜）。"""

    doing_key: str = PROGRESS_UNKNOWN_KEY
    waiting_key: str = PROGRESS_UNKNOWN_KEY
    saved_key: str = PROGRESS_UNKNOWN_KEY
    next_key: str = PROGRESS_UNKNOWN_KEY


#: 未读到会话时的默认进度（四项均为显式未知）。
_UNKNOWN_PROGRESS: Final[ProgressSummaryView] = ProgressSummaryView()


@dataclass(frozen=True)
class ObservationViewState:
    """返回现场（仅展示状态）：滚动锚点与离开时选中的子执行。

    由 View 在导航前上报，VM 原样保存并在返回父级时恢复；VM 不解释锚点语义。
    """

    scroll_anchor: str | None = None
    selected_execution_id: str | None = None


class ObservationViewStateStore(Protocol):
    """观察现场存储端口（可注入；默认内存实现，进程重启即失效，不冒充持久化）。"""

    def save(self, session_id: str, execution_id: str, view: ObservationViewState) -> None: ...

    def load(self, session_id: str, execution_id: str) -> ObservationViewState | None: ...


class InMemoryObservationViewStateStore:
    """观察现场的内存实现（会话内有效；跨进程不复用，避免把过期锚点当现场）。"""

    def __init__(self) -> None:
        self._views: dict[tuple[str, str], ObservationViewState] = {}

    def save(self, session_id: str, execution_id: str, view: ObservationViewState) -> None:
        self._views[(session_id, execution_id)] = view

    def load(self, session_id: str, execution_id: str) -> ObservationViewState | None:
        return self._views.get((session_id, execution_id))


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
    #: 研究侧 MCP 范围选择（J25）：本次允许的服务子集；空选择即「本次不使用 MCP」，
    #: 不解释为全部允许（§6 默认拒绝）。目录读取失败如实报错，不展示空成功。
    mcp_scope_available: bool = False
    #: ``(identity_key, 展示名)``；保持目录返回顺序稳定。
    mcp_scope_options: tuple[tuple[str, str], ...] = ()
    mcp_scope_selected: tuple[str, ...] = ()
    mcp_scope_error_key: str | None = None
    #: 最近一次受理的派发事实：``submitted=False`` 的结构化原因码 + 参数（讨论/计划
    #: 为零派发，属预期，不当作错误；执行模式零派发如实展示原因，不伪造成已启动）。
    last_dispatch_reason: DispatchReasonView | None = None
    #: 主回合直答（讨论/执行直答）；为空表示尚未产生或未接线，不以占位文本伪装。
    last_answer_text: str = ""
    plan_steps: tuple[PlanStepView, ...] = ()
    plan_reference: tuple[str, int] | None = None
    plan_pending: bool = False
    plan_error_key: str | None = None
    #: 会话列表与元数据动作（W-14）：列表只读投影 + 动作在途/错误 + 新建/改名输入态。
    sessions: tuple[SessionListItemView, ...] = ()
    sessions_loading: bool = False
    sessions_error_code: str | None = None
    session_action_pending: bool = False
    session_action_error_key: str | None = None
    session_action_error_code: str | None = None
    new_session_title: str = ""
    rename_target_session_id: str | None = None
    rename_text: str = ""
    #: 当前会话的元数据版（会话 CAS 动作的预期版；读取失败保持 0，动作被拒绝）。
    session_revision: int = 0
    #: 增量流式（W-13）：受治理片段按序合并；``stream_text`` 只含真实模型片段，
    #: 合成/占位片段以 ``stream_synthetic_visible`` 单独标记（不与真实输出混淆）。
    stream_fragments: tuple[StreamedTextFragment, ...] = ()
    stream_text: str = ""
    stream_synthetic_visible: bool = False
    #: 返焦/重连权威核对水位（0 表示尚未核对）；持久事件为权威，内存增量不得替代。
    authoritative_seq: int = 0
    last_catch_up_error_code: str | None = None
    #: 首层进度文案（R21：数据缺失为显式未知，不填默认值）。
    progress: ProgressSummaryView = _UNKNOWN_PROGRESS
    #: 子会话两级返回（父→子→返回父）：父链 + 返回目标 + 返回后恢复的现场。
    observation_path: tuple[str, ...] = ()
    return_to_execution_id: str | None = None
    restored_scroll_anchor: str | None = None
    restored_child_selection: str | None = None

    # --- W-12 研究侧模型/角色选择、费用透明与耗时分段 ---
    model_selection_available: bool = False
    model_options: tuple[ModelConnectionOption, ...] = ()
    model_selection_error_key: str | None = None
    role_options: tuple[RoleOption, ...] = ()
    role_selection_error_key: str | None = None
    #: 当前待发送选择（未随首问发送前可改）。
    model_selection: str | None = None
    role_selection: str | None = None
    #: 已随首问冻结的选择（首问成功后记录；同会话内不再静默改写）。
    model_selection_frozen: str | None = None
    role_selection_frozen: str | None = None
    budget: BudgetEstimate | None = None
    usage_round: CostUsageView | None = None
    usage_cumulative: CostUsageView | None = None
    usage_error_key: str | None = None
    durations: tuple[DurationSegment, ...] = ()
    durations_execution_id: str | None = None

    def model_option(self, connection_id: str | None) -> ModelConnectionOption | None:
        """按连接 id 读取工具栏选项（不存在返回 None，不猜近似连接）。"""

        for option in self.model_options:
            if option.connection_id == connection_id:
                return option
        return None

    def role_option(self, role_key: str | None) -> RoleOption | None:
        """按角色 key 读取选项（不存在返回 None）。"""

        for option in self.role_options:
            if option.role_key == role_key:
                return option
        return None

    #: W-01 执行控制：在途目标（非空时该行动作禁用，防并发重复提交）。
    control_pending_execution_id: str | None = None
    #: 最近一次控制动作结果（成功/冲突/未知如实分列；切会话时清空）。
    control_result: ControlResultView | None = None
    #: 终止/未知结案的原因输入态（原因必填，未填不发命令）。
    control_reason_execution_id: str | None = None
    control_reason_kind: str | None = None
    control_reason_text: str = ""
    control_reason_error_key: str | None = None

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


def _unknown_session_item(session_id: str) -> SessionListItemView:
    """会话摘要读取失败时的列表项：身份已知、标题/时点/版未知（R21）。"""

    return SessionListItemView(
        session_id=session_id,
        title=None,
        updated_at=None,
        revision=None,
        pinned=False,
        archived=False,
        cleaned=False,
        status_unknown=True,
    )


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
                node_id=str(getattr(execution, "node_id", "") or ""),
                execution_kind=str(getattr(execution, "execution_kind", "") or ""),
                current_round_id=getattr(execution, "current_round_id", None),
                revision=int(getattr(execution, "revision", 0) or 0),
                dispatch_closed=bool(getattr(execution, "dispatch_closed", False)),
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


def _research_route(snapshot: Any) -> Mapping[str, Any] | None:
    """从连接目录快照取研究场景路由（缺失/停用/无候选返回 None，不猜默认）。"""

    for route in getattr(snapshot, "scenario_routes", ()) or ():
        if not isinstance(route, Mapping) or route.get("scenario_id") != AGENT_RESEARCH_SCENARIO_ID:
            continue
        if not route.get("enabled") or not route.get("candidates"):
            return None
        return route
    return None


def _research_connections(snapshot: Any) -> dict[str, Mapping[str, Any]]:
    """连接目录快照 → connection_id → 连接定义（非映射行跳过，不臆造字段）。"""

    connections: dict[str, Mapping[str, Any]] = {}
    for connection in getattr(snapshot, "connections", ()) or ():
        if not isinstance(connection, Mapping):
            continue
        connection_id = connection.get("connection_id")
        if isinstance(connection_id, str) and connection_id:
            connections.setdefault(connection_id, connection)
    return connections


def _positive_int(value: object) -> int | None:
    """正整数读取（布尔/非法值 → None）。"""

    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def build_model_connection_options(snapshot: Any) -> tuple[ModelConnectionOption, ...]:
    """研究场景路由候选 → 工具栏连接选项（全序保留，非法候选带拒绝 key）。

    只投影 ``resolve_agent_model_binding`` 实际会解算的同一条路径
    （``scenario_routes[agent_research].candidates``）；不在该路由内的连接不出现在
    选项里（选了也不生效，选择时显式拒绝）。
    """

    route = _research_route(snapshot)
    if route is None:
        return ()
    connections = _research_connections(snapshot)
    options: list[ModelConnectionOption] = []
    seen: set[str] = set()
    for index, raw_id in enumerate(route.get("candidates") or (), start=1):
        if not isinstance(raw_id, str) or not raw_id or raw_id in seen:
            continue
        seen.add(raw_id)
        connection = connections.get(raw_id)
        if connection is None:
            options.append(
                ModelConnectionOption(
                    connection_id=raw_id,
                    display_name=raw_id,
                    model="",
                    context_window=None,
                    position=index,
                    effective=False,
                    reject_key="agent_research_model_selection_unknown",
                )
            )
            continue
        name = connection.get("name")
        model = connection.get("model")
        enabled = bool(connection.get("enabled", True))
        options.append(
            ModelConnectionOption(
                connection_id=raw_id,
                display_name=name if isinstance(name, str) and name else raw_id,
                model=model if isinstance(model, str) else "",
                context_window=_positive_int(connection.get("context_window")),
                position=index,
                effective=not options,
                reject_key=None if enabled else "agent_research_model_selection_disabled",
            )
        )
    return tuple(options)


def _frozen_role_version(role_key: str) -> str | None:
    """角色 Prompt 版本：优先设置页已保存版本，缺省用默认版本（与叶运行同源）。"""

    defaults = ROLE_PROMPT_DEFAULTS.get(role_key)
    if defaults is None:
        return None
    default_version = defaults[1]
    saved = ConfigHandler.get_ai_role_prompt_version(role_key)
    version = saved if isinstance(saved, str) and saved.strip() else default_version
    return version if isinstance(version, str) and version.strip() else None


def build_role_options() -> tuple[RoleOption, ...]:
    """研究角色选项：主角色可选；子角色仅由主角色分配执行，选择即显式拒绝。"""

    options: list[RoleOption] = []
    for role_key in RESEARCH_ROLE_KEYS:
        version = _frozen_role_version(role_key)
        reject_key: str | None = None
        if role_key not in ROLE_PROMPT_DEFAULTS:
            reject_key = "agent_research_role_selection_unknown"
        elif version is None:
            reject_key = "agent_research_role_selection_version_missing"
        elif role_key != _RESEARCH_MAIN_ROLE:
            reject_key = "agent_research_role_selection_child_role"
        options.append(
            RoleOption(
                role_key=role_key,
                name_key=ROLE_PROMPT_NAME_KEYS.get(role_key, "agent_research_role_name_unknown"),
                version=version,
                reject_key=reject_key,
            )
        )
    return tuple(options)


def _decimal_amount(value: object) -> Decimal | None:
    """金额读取：非法/缺失 → None（未知不填 0）。"""

    if value is None or isinstance(value, bool):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() else None


def cost_state_view(
    state: str,
    *,
    known_cny: Decimal | None = None,
    reserved_cny: Decimal | None = None,
    unpriced_attempts: int | None = None,
    truncated: bool = False,
    read_error: str | None = None,
) -> CostUsageView:
    """费用状态 → 只读视图（未知/未计价不提升为已知金额）。"""

    return CostUsageView(
        state=state,
        state_key=COST_STATE_KEYS.get(state, COST_STATE_KEYS["unknown"]),
        known_cny=known_cny,
        reserved_cny=reserved_cny,
        unpriced_attempts=unpriced_attempts,
        truncated=truncated,
        read_error=read_error,
    )


def unavailable_cost_view(error_key: str | None = None) -> CostUsageView:
    """费用读取面不可用/失败（不显示 0，不冒充已结算）。"""

    return cost_state_view("failed" if error_key else "unknown", read_error=error_key)


def _parse_wire_moment(value: object) -> datetime | None:
    """wire 时间戳（``...Z``）→ 带时区时间；非字符串/非法文案 → None（未知不猜）。"""

    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _delta_ms(later: object, earlier: object) -> int | None:
    """两时间戳差（毫秒）：任一端缺失/非法或出现负值 → None（不臆造区间）。"""

    end = _parse_wire_moment(later)
    start = _parse_wire_moment(earlier)
    if end is None or start is None:
        return None
    delta_ms = int((end - start).total_seconds() * 1000)
    return delta_ms if delta_ms >= 0 else None


def _user_intake_moment(snapshot: Any) -> object:
    """本执行首个用户提问的提交时刻（消息页内最早 ``role=user`` 的 committed_at）。"""

    items = getattr(getattr(snapshot, "messages_page", None), "items", ()) or ()
    candidates: list[object] = []
    for item in items:
        if getattr(item, "role", None) != MessageRole.USER:
            continue
        committed_at = getattr(item, "committed_at", None)
        if _parse_wire_moment(committed_at) is not None:
            candidates.append(committed_at)
    if not candidates:
        return None
    return min(candidates, key=lambda value: cast(datetime, _parse_wire_moment(value)))


def _user_wait_ms(snapshot: Any) -> int | None:
    """用户等待：需要答复→答复被消费的累计时长（任一答复缺失/未消费 → 未知）。"""

    decisions = tuple(getattr(snapshot, "decisions", ()) or ())
    answered = [
        decision
        for decision in decisions
        if getattr(decision, "answer_schema", None) is not None
        or getattr(decision, "answer_consumed_at", None) is not None
    ]
    if not answered:
        return None
    total = 0
    for decision in answered:
        waited = _delta_ms(
            getattr(decision, "answer_consumed_at", None),
            getattr(decision, "created_at", None),
        )
        if waited is None:
            return None
        total += waited
    return total


def _save_ms(snapshot: Any) -> int | None:
    """保存：工作结束（execution.ended_at）→ 成果发布（published_at 最大值）。"""

    execution = getattr(snapshot, "execution", None)
    ended_at = getattr(execution, "ended_at", None)
    items = getattr(getattr(snapshot, "artifacts_page", None), "items", ()) or ()
    published: list[str] = [
        item.published_at
        for item in items
        if isinstance(getattr(item, "published_at", None), str) and getattr(item, "published_at", "")
    ]
    if not published:
        return None
    latest = max(published, key=lambda value: cast(datetime, _parse_wire_moment(value)))
    return _delta_ms(latest, ended_at)


def derive_duration_values(snapshot: Any) -> dict[str, int | None]:
    """执行快照 → 五段耗时毫秒值（无来源/不可解算的段为 None，由视图显式未知）。

    来源字段：
    - 排队：``message.committed_at``(role=user 最早) → ``execution.created_at``；
    - 用户等待：``decision.created_at`` → ``decision.answer_consumed_at``（逐条求和）；
    - 实际工作：``execution.active_ms``（既有累计活跃时长）；
    - 模型等待：无可见 attempt 时间戳投影 → 未知；
    - 保存：``execution.ended_at`` → ``artifact.published_at``（最大值）。
    """

    execution = getattr(snapshot, "execution", None)
    return {
        "queue": _delta_ms(getattr(execution, "created_at", None), _user_intake_moment(snapshot)),
        "user_wait": _user_wait_ms(snapshot),
        "work": getattr(getattr(snapshot, "usage", None), "active_ms", None),
        "model_wait": None,
        "save": _save_ms(snapshot),
    }


def build_duration_segments(
    *,
    active_ms: int | None = None,
    queue_ms: int | None = None,
    user_wait_ms: int | None = None,
    model_wait_ms: int | None = None,
    save_ms: int | None = None,
) -> tuple[DurationSegment, ...]:
    """五段耗时投影：有来源事实给毫秒，缺来源显式未知（R21，不以 0 冒充）。"""

    values: Mapping[str, int | None] = {
        "queue": queue_ms,
        "user_wait": user_wait_ms,
        "work": active_ms,
        "model_wait": model_wait_ms,
        "save": save_ms,
    }
    return tuple(
        DurationSegment(
            segment,
            label_key,
            source_key,
            values.get(segment),
            None if values.get(segment) is not None else DURATION_UNKNOWN_KEY,
        )
        for segment, label_key, source_key in DURATION_SEGMENTS
    )


def _billing_of(connection: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """连接计费子域（缺失按空映射；不臆造单价）。"""

    billing = connection.get("billing") if connection is not None else None
    return billing if isinstance(billing, Mapping) else {}


def estimate_budget(
    *,
    text: str,
    connection: Mapping[str, Any] | None,
    token_counter: Callable[[str, str | None], tuple[int, bool]],
) -> BudgetEstimate:
    """发送前预算：按连接计费快照 + 正文 token 估算（不可估即显式未计价/未知）。

    估算口径如实标注（仅正文 + 输出预留），不把「未含系统提示/资料/工具」的
    小计伪装成整次请求的确定费用；单价缺失或按用量计费时不给金额（R21）。
    """

    billing = _billing_of(connection)
    unit_key = "agent_research_budget_unit_per_request"
    if connection is None:
        return BudgetEstimate(
            state=BUDGET_UNKNOWN,
            state_key="agent_research_budget_unknown_no_model",
            amount_cny=None,
            unit_key=unit_key,
            basis_key=None,
            input_tokens=None,
            output_reserve_tokens=None,
            tokens_verified=False,
        )
    if billing.get("mode") != "estimate_cost":
        return BudgetEstimate(
            state=BUDGET_UNPRICED,
            state_key="agent_research_budget_unpriced_usage_only",
            amount_cny=None,
            unit_key=unit_key,
            basis_key=None,
            input_tokens=None,
            output_reserve_tokens=None,
            tokens_verified=False,
        )
    if billing.get("pricing_unit") != "llm_tokens":
        return BudgetEstimate(
            state=BUDGET_UNPRICED,
            state_key="agent_research_budget_unpriced_encoder",
            amount_cny=None,
            unit_key=unit_key,
            basis_key=None,
            input_tokens=None,
            output_reserve_tokens=None,
            tokens_verified=False,
        )
    input_price = _decimal_amount(billing.get("input_price_per_m"))
    output_price = _decimal_amount(billing.get("output_price_per_m"))
    if input_price is None or output_price is None:
        return BudgetEstimate(
            state=BUDGET_UNPRICED,
            state_key="agent_research_budget_unpriced_price_missing",
            amount_cny=None,
            unit_key=unit_key,
            basis_key=None,
            input_tokens=None,
            output_reserve_tokens=None,
            tokens_verified=False,
        )
    stripped = text.strip()
    if not stripped:
        return BudgetEstimate(
            state=BUDGET_UNKNOWN,
            state_key="agent_research_budget_unknown_no_text",
            amount_cny=None,
            unit_key=unit_key,
            basis_key=None,
            input_tokens=None,
            output_reserve_tokens=None,
            tokens_verified=False,
        )
    model_ref = connection.get("model") if connection is not None else None
    provider = connection.get("provider") if connection is not None else None
    tokenizer_model = (
        f"{provider}/{model_ref}"
        if isinstance(provider, str) and provider and isinstance(model_ref, str) and model_ref
        else (model_ref if isinstance(model_ref, str) and model_ref else None)
    )
    input_tokens, verified = token_counter(stripped, tokenizer_model)
    # 惰性 import：避免 UI 模块导入期拉入 AI 服务组合根重依赖。
    from services.ai_service.token_budget import OUTPUT_RESERVE_TOKENS

    total = (
        Decimal(input_tokens) / Decimal(1_000_000) * input_price
        + Decimal(OUTPUT_RESERVE_TOKENS) / Decimal(1_000_000) * output_price
    )
    return BudgetEstimate(
        state=BUDGET_ESTIMATED,
        state_key="agent_research_budget_estimated",
        amount_cny=total,
        unit_key=unit_key,
        basis_key="agent_research_budget_basis",
        input_tokens=input_tokens,
        output_reserve_tokens=OUTPUT_RESERVE_TOKENS,
        tokens_verified=verified,
    )


def _default_model_catalog() -> ModelCatalogPort | None:
    """生产缺省连接目录（惰性 import：UI 不 import app，只依赖服务层只读快照）。"""

    from services.model_configuration_service import ModelConfigurationService

    return cast(ModelCatalogPort, ModelConfigurationService())


def _default_token_counter(text: str, model: str | None) -> tuple[int, bool]:
    """生产缺省 token 计数（惰性 import：避免 UI 模块导入期拉入 AI 服务重依赖）。"""

    from services.ai_service.token_budget import count_tokens_with_basis

    return count_tokens_with_basis(text, model)


def _ancestor_path(executions: tuple[Any, ...], execution_id: str | None) -> tuple[str, ...]:
    """从根到该执行的父链（不含自身；父不在本页或环形引用即截断）。"""

    if execution_id is None:
        return ()
    parents = {execution.execution_id: execution.parent_execution_id for execution in executions}
    path: list[str] = []
    seen: set[str] = set()
    current = parents.get(execution_id)
    while isinstance(current, str) and current in parents and current not in seen:
        seen.add(current)
        path.append(current)
        current = parents[current]
    path.reverse()
    return tuple(path)


def _progress_summary(
    *,
    session_known: bool,
    executions: Sequence[Any],
    pending_count: int,
    artifact_count: int,
    streaming: bool,
) -> ProgressSummaryView:
    """首层进度四问（由已知事实推导；未读到会话时四项全为显式未知，R21）。"""

    if not session_known:
        return _UNKNOWN_PROGRESS
    active = any(getattr(item, "display_state", None) in _ACTIVE_DISPLAY_STATES for item in executions)
    if streaming:
        doing = PROGRESS_DOING_STREAMING
    elif active:
        doing = PROGRESS_DOING_RUNNING
    elif pending_count > 0:
        doing = PROGRESS_DOING_WAITING_USER
    elif executions:
        doing = PROGRESS_DOING_IDLE
    else:
        doing = PROGRESS_DOING_NONE
    waiting = PROGRESS_WAITING_USER if pending_count > 0 else PROGRESS_WAITING_NONE
    saved = PROGRESS_SAVED_ARTIFACTS if artifact_count > 0 else PROGRESS_SAVED_NONE
    if pending_count > 0:
        next_key = PROGRESS_NEXT_ANSWER
    elif streaming or active:
        next_key = PROGRESS_NEXT_WAIT
    else:
        next_key = PROGRESS_NEXT_IDLE
    return ProgressSummaryView(doing_key=doing, waiting_key=waiting, saved_key=saved, next_key=next_key)


#: 控制动作文案 key（显式命令族；不按模糊参数切换动作）。
CONTROL_LABEL_KEYS: Final[dict[str, str]] = {
    "pause": "agent_research_control_pause",
    "resume": "agent_research_control_resume",
    "terminate": "agent_research_control_terminate",
    "close_unknown": "agent_research_control_close_unknown",
    "withdraw_queued": "agent_research_control_withdraw",
    "retry": "agent_research_control_retry",
}

#: 不可用原因 key（不可用动作一律显式禁用并给原因，不静默隐藏）。
CONTROL_DISABLED_TERMINAL: Final[str] = "agent_research_control_disabled_terminal"
CONTROL_DISABLED_NOT_PAUSABLE: Final[str] = "agent_research_control_disabled_not_pausable"
CONTROL_DISABLED_NO_ROUND: Final[str] = "agent_research_control_disabled_no_round"
CONTROL_DISABLED_NOT_RESUMABLE: Final[str] = "agent_research_control_disabled_not_resumable"
CONTROL_DISABLED_DISPATCHED: Final[str] = "agent_research_control_disabled_dispatched"
CONTROL_DISABLED_RETRY_STATE: Final[str] = "agent_research_control_disabled_retry_state"
CONTROL_DISABLED_RETRY_ROOT: Final[str] = "agent_research_control_disabled_retry_root"
CONTROL_UNAVAILABLE_KEY: Final[str] = "agent_research_control_unavailable"

#: 控制命令键前缀（按执行身份 + 版 + 原因派生稳定键，双击/重传命中同一命令）。
_CONTROL_COMMAND_KEY_PREFIX = "ui-control:"


def _control_action(
    kind: str,
    *,
    enabled: bool,
    disabled_reason_key: str | None = None,
    requires_reason: bool = False,
) -> ControlActionView:
    return ControlActionView(
        kind=kind,
        label_key=CONTROL_LABEL_KEYS[kind],
        enabled=enabled,
        disabled_reason_key=None if enabled else disabled_reason_key,
        requires_reason=requires_reason,
    )


def control_actions_for(node: ExecutionNodeView, *, root: ExecutionNodeView | None) -> tuple[ControlActionView, ...]:
    """按节点/根事实给出控制动作表（纯函数；不可用给原因，不猜可提交）。

    - 暂停/继续/终止/未知结案：任一非终态节点可用；暂停要求有效轮；
    - 撤回：仅未派发排队项（无有效轮、门闩未关）可用；
    - 重试：仅非根的可重试叶（``failed`` / ``ended``）且根处于可重试状态时可用。
    """

    terminal = node.display_state in NON_RECOVERABLE_DISPLAY_STATES
    has_round = node.current_round_id is not None
    actions: list[ControlActionView] = []

    if node.display_state in PAUSABLE_DISPLAY_STATES:
        actions.append(_control_action("pause", enabled=has_round, disabled_reason_key=CONTROL_DISABLED_NO_ROUND))
    elif node.display_state in RESUMABLE_DISPLAY_STATES:
        actions.append(_control_action("pause", enabled=False, disabled_reason_key=CONTROL_DISABLED_NOT_PAUSABLE))
    else:
        actions.append(
            _control_action(
                "pause",
                enabled=False,
                disabled_reason_key=CONTROL_DISABLED_TERMINAL if terminal else CONTROL_DISABLED_NOT_PAUSABLE,
            )
        )

    actions.append(
        _control_action(
            "resume",
            enabled=node.display_state in RESUMABLE_DISPLAY_STATES,
            disabled_reason_key=CONTROL_DISABLED_TERMINAL if terminal else CONTROL_DISABLED_NOT_RESUMABLE,
        )
    )
    actions.append(
        _control_action(
            "terminate",
            enabled=not terminal,
            disabled_reason_key=CONTROL_DISABLED_TERMINAL,
            requires_reason=True,
        )
    )
    actions.append(
        _control_action(
            "close_unknown",
            enabled=not terminal,
            disabled_reason_key=CONTROL_DISABLED_TERMINAL,
            requires_reason=True,
        )
    )
    withdrawable = node.display_state in WITHDRAWABLE_DISPLAY_STATES and not has_round and not node.dispatch_closed
    actions.append(
        _control_action(
            "withdraw_queued",
            enabled=withdrawable,
            disabled_reason_key=CONTROL_DISABLED_TERMINAL if terminal else CONTROL_DISABLED_DISPATCHED,
        )
    )
    if node.parent_execution_id is not None:
        retryable_leaf = node.display_state in RETRYABLE_LEAF_DISPLAY_STATES
        root_retryable = (
            root is not None
            and root.display_state in RETRYABLE_EXECUTION_DISPLAY_STATES
            and root.current_round_id is not None
        )
        actions.append(
            _control_action(
                "retry",
                enabled=retryable_leaf and root_retryable,
                disabled_reason_key=CONTROL_DISABLED_RETRY_ROOT if retryable_leaf else CONTROL_DISABLED_RETRY_STATE,
            )
        )
    return tuple(actions)


class ResearchObserverViewModel(ObservableViewModelMixin[ResearchObserverState]):
    """壳层公共研究观察层 VM（共享实例；观察 + W-01 显式控制命令族）。"""

    def __init__(
        self,
        service: AgentResearchService | None,
        *,
        user_scope: str = LOCAL_DESKTOP_USER_SCOPE,
        unavailable_reason_key: str = UNAVAILABLE_NOT_WIRED,
        intake: MainTurnIntakePort | None = None,
        mcp_catalog: MCPScopeCatalogPort | None = None,
        model_catalog: ModelCatalogPort | None = None,
        token_counter: Callable[[str, str | None], tuple[int, bool]] | None = None,
        view_store: ObservationViewStateStore | None = None,
    ) -> None:
        self._service = service
        self._user_scope = user_scope
        self._intake = intake
        self._mcp_catalog = mcp_catalog
        #: 既有模型连接目录（缺省按 ``ModelConfigurationService`` 只读快照；UI 不 import app）。
        self._model_catalog = model_catalog if model_catalog is not None else _default_model_catalog()
        #: 正文 token 计数（可注入替身；生产按 litellm tokenizer，经 CPU 池执行）。
        self._token_counter = token_counter if token_counter is not None else _default_token_counter
        #: 连接定义快照（预算估算读计费子域；不缓存秘密，仅含展示/计价字段）。
        self._connection_rows: dict[str, Mapping[str, Any]] = {}
        #: 观察现场存储（可注入；默认内存实现，返回父级时恢复锚点/选中项）。
        self._view_store: ObservationViewStateStore = (
            view_store if view_store is not None else InMemoryObservationViewStateStore()
        )
        #: 目录身份映射（identity_key → 服务身份）；范围选择提交时据此还原强类型身份。
        self._mcp_identities: dict[str, MCPServiceIdentity] = {}
        self._session_key: int | None = id(service) if service is not None else None
        # 精确会话导航目标（建稿回执驱动）：仅在读取与目标仍一致时落 state，
        # 读取期间被更新的目标会触发重读，避免壳层退回「历史首条」。
        self._preferred_session_id: str | None = None
        # 原始首读投影：展开/收起/选中只重算展示，不重复读取、不触碰执行。
        self._raw_executions: tuple[Any, ...] = ()
        self._raw_messages: tuple[Any, ...] = ()
        self._raw_artifacts: tuple[Any, ...] = ()
        # 已读到的会话清理事实：清理后到达的正文/草稿读取结果不再落 state（T-05）。
        self._session_cleaned = False
        #: 预算估算票据：文本变化期间的旧估算结果不得覆盖新估算。
        self._budget_ticket = 0
        self._state = ResearchObserverState(
            available=service is not None,
            unavailable_reason_key=unavailable_reason_key if service is None else "",
            composer_available=service is not None and intake is not None,
            mcp_scope_available=mcp_catalog is not None,
            model_selection_available=self._model_catalog is not None,
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
                # 费用与耗时投影是只读附加读面：失败不清空会话快照，只如实标注。
                await self.refresh_usage()
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

    # ------------------------------------------------------------------
    # W-01 执行控制（显式命令族；只作用于目标执行，不连带其它执行）
    # ------------------------------------------------------------------

    def execution_node(self, execution_id: str) -> ExecutionNodeView | None:
        """按执行身份取当前投影节点（未知返回 ``None``，不按猜测提交）。"""

        for node in self._state.execution_nodes:
            if node.execution_id == execution_id:
                return node
        return None

    def control_actions(self, execution_id: str) -> tuple[ControlActionView, ...]:
        """取某执行节点的控制动作表（不可用动作给原因 key）。"""

        node = self.execution_node(execution_id)
        if node is None:
            return ()
        return control_actions_for(node, root=self._root_for(node))

    async def pause_execution(self, execution_id: str) -> None:
        """暂停目标执行（关该执行的派发门，不连带其它执行/轮次）。"""

        await self._submit_control(ExecutionControlKind.PAUSE, execution_id)

    async def resume_execution(self, execution_id: str) -> None:
        """继续暂停中的目标执行（从持久状态重开派发门）。"""

        await self._submit_control(ExecutionControlKind.RESUME, execution_id)

    async def terminate_execution(self, execution_id: str, *, reason: str) -> None:
        """终止目标执行（必填终止原因；不释放未核实名额）。"""

        await self._submit_control(ExecutionControlKind.TERMINATE, execution_id, reason=reason)

    async def close_unknown_execution(self, execution_id: str, *, reason: str) -> None:
        """未知结案（必填证据原因；落 ``closed_unknown``，不伪装完成，R21）。"""

        await self._submit_control(ExecutionControlKind.CLOSE_UNKNOWN, execution_id, reason=reason)

    async def withdraw_execution(self, execution_id: str) -> None:
        """撤回排队项（仅未派发排队项；已派发一律拒绝，不按撤回处理）。"""

        await self._submit_control(ExecutionControlKind.WITHDRAW_QUEUED, execution_id)

    def prompt_control_reason(self, execution_id: str, kind: str) -> None:
        """进入终止/未知结案的原因输入态（原因必填，UI 不预设默认原因）。"""

        if kind not in (ExecutionControlKind.TERMINATE.value, ExecutionControlKind.CLOSE_UNKNOWN.value):
            return
        self._set_state(
            control_reason_execution_id=execution_id,
            control_reason_kind=kind,
            control_reason_text="",
            control_reason_error_key=None,
        )

    def cancel_control_reason(self) -> None:
        """取消原因输入（不发命令）。"""

        self._set_state(
            control_reason_execution_id=None,
            control_reason_kind=None,
            control_reason_text="",
            control_reason_error_key=None,
        )

    def update_control_reason(self, text: str) -> None:
        """更新原因输入（仅本地展示态，不触达执行）。"""

        self._set_state(control_reason_text=text, control_reason_error_key=None)

    async def submit_control_reason(self) -> None:
        """提交带原因的终止/未知结案（空原因不发命令，如实标必填）。"""

        execution_id = self._state.control_reason_execution_id
        kind = self._state.control_reason_kind
        reason = self._state.control_reason_text.strip()
        if execution_id is None or kind is None:
            return
        if not reason:
            self._set_state(control_reason_error_key="agent_research_control_reason_required")
            return
        self._set_state(control_reason_execution_id=None, control_reason_kind=None, control_reason_text="")
        if kind == ExecutionControlKind.TERMINATE.value:
            await self.terminate_execution(execution_id, reason=reason)
        elif kind == ExecutionControlKind.CLOSE_UNKNOWN.value:
            await self.close_unknown_execution(execution_id, reason=reason)

    async def retry_execution(self, execution_id: str, target_node_id: str) -> None:
        """重试某单任务（``execution_id`` 为根执行，``target_node_id`` 为失败叶）。

        范围由服务按持久计划精确重算；UI 只提交目标身份与根执行预期版。
        """

        if self._service is None or not self._state.available:
            self._set_state(last_error_code="CONTROL_UNAVAILABLE")
            return
        node = self.execution_node(execution_id)
        target = self.execution_node(target_node_id)
        if node is None or target is None or node.current_round_id is None or not target.node_id:
            self._set_control_error(execution_id, "retry", "CONTROL_TARGET_UNAVAILABLE")
            return
        if self._state.control_pending_execution_id is not None:
            return
        session_id = self._state.session_id
        if session_id is None:
            self._set_control_error(execution_id, "retry", "CONTROL_SESSION_UNAVAILABLE")
            return
        command_id = self._control_command_id("retry", execution_id, node.revision, target.node_id)
        request = RetryExecutionRequest(
            user_scope=self._user_scope,
            session_id=session_id,
            execution_id=execution_id,
            client_command_id=command_id,
            expected_execution_revision=node.revision,
            round_id=node.current_round_id,
            targets=(target.node_id,),
        )
        self._set_state(control_pending_execution_id=execution_id, last_error_code=None)
        try:
            receipt = await self._service.retry_execution(request)
        except asyncio.CancelledError:
            self._set_state(control_pending_execution_id=None)
            raise
        except Exception as error:  # facade 已脱敏；如实报码，不伪装成功
            self._set_control_error(execution_id, "retry", self._error_code(error))
            return
        self._set_state(
            control_pending_execution_id=None,
            control_result=ControlResultView(
                execution_id=execution_id,
                kind="retry",
                display_state="planned" if receipt.applied else node.display_state,
                applied=receipt.applied,
                replayed=receipt.replayed,
                reason=receipt.dispatch_reason or receipt.reason,
                error_code=None,
                released_slot_count=0,
                open_slot_count=0,
                native_control_state=receipt.dispatch_state,
            ),
        )
        await self.refresh(self._state.session_id)

    async def _submit_control(
        self,
        kind: ExecutionControlKind,
        execution_id: str,
        *,
        reason: str | None = None,
    ) -> None:
        """提交单条控制命令：CAS 预期版取当前投影，成功/冲突/未知如实落状态。"""

        if self._service is None or not self._state.available:
            self._set_state(last_error_code="CONTROL_UNAVAILABLE")
            return
        node = self.execution_node(execution_id)
        if node is None:
            self._set_control_error(execution_id, kind.value, "CONTROL_TARGET_UNAVAILABLE")
            return
        if kind in (ExecutionControlKind.TERMINATE, ExecutionControlKind.CLOSE_UNKNOWN):
            cleaned = (reason or "").strip()
            if not cleaned:
                self._set_control_error(execution_id, kind.value, "CONTROL_REASON_REQUIRED")
                return
            reason = cleaned
        if self._state.control_pending_execution_id is not None:
            return
        session_id = self._state.session_id
        if session_id is None:
            self._set_control_error(execution_id, kind.value, "CONTROL_SESSION_UNAVAILABLE")
            return
        request = ExecutionControlRequest(
            user_scope=self._user_scope,
            session_id=session_id,
            execution_id=execution_id,
            client_command_id=self._control_command_id(kind.value, execution_id, node.revision, reason),
            expected_execution_revision=node.revision,
            kind=kind,
            round_id=node.current_round_id,
            reason=reason,
        )
        self._set_state(control_pending_execution_id=execution_id, last_error_code=None)
        try:
            receipt = await self._service.control(request)
        except asyncio.CancelledError:
            # R2：取消必须传播；先释放在途标记，避免存续期间永久禁用控制。
            self._set_state(control_pending_execution_id=None)
            raise
        except Exception as error:  # facade 已脱敏；如实报码，不伪装已生效
            self._set_control_error(execution_id, kind.value, self._error_code(error))
            return
        if not isinstance(receipt, ExecutionControlReceipt):
            # 契约收窄：非控制回执不得读成控制成功（R21）。
            self._set_control_error(execution_id, kind.value, "CONTROL_UNTYPED_RECEIPT")
            return
        self._set_state(
            control_pending_execution_id=None,
            control_result=ControlResultView(
                execution_id=execution_id,
                kind=kind.value,
                display_state=receipt.display_state,
                applied=receipt.applied,
                replayed=receipt.replayed,
                reason=receipt.reason,
                error_code=None,
                released_slot_count=len(receipt.released_slot_ids),
                open_slot_count=len(receipt.open_slot_ids),
                native_control_state=receipt.native_control_state,
            ),
        )
        await self.refresh(self._state.session_id)

    def _set_control_error(self, execution_id: str, kind: str, error_code: str) -> None:
        node = self.execution_node(execution_id)
        self._set_state(
            control_pending_execution_id=None,
            control_result=ControlResultView(
                execution_id=execution_id,
                kind=kind,
                display_state=node.display_state if node is not None else "",
                applied=False,
                replayed=False,
                reason=None,
                error_code=error_code,
                released_slot_count=0,
                open_slot_count=0,
                native_control_state="unknown",
            ),
        )

    def _root_for(self, node: ExecutionNodeView) -> ExecutionNodeView | None:
        current = node
        for _ in range(32):  # 防御异常深链；仍找不到即未知（不猜根）
            if current.parent_execution_id is None:
                return current
            parent = self.execution_node(current.parent_execution_id)
            if parent is None:
                return None
            current = parent
        return None

    @staticmethod
    def _control_command_id(kind: str, execution_id: str, revision: int, extra: str | None) -> str:
        suffix = hashlib.sha256((extra or "").encode("utf-8")).hexdigest()[:12]
        return f"{_CONTROL_COMMAND_KEY_PREFIX}{kind}:{execution_id}:{revision}:{suffix}"

    # ------------------------------------------------------------------
    # 会话列表与元数据（W-14；只读列表 + 元数据 CAS 动作，不派发研究）
    # ------------------------------------------------------------------

    async def load_sessions(self) -> None:
        """读取会话列表（标题/时间/状态；只读）。

        列表由历史命中去重得到（newest_first），逐会话以 ``limit=1`` 只读摘要取
        标题/时点/元数据版。单个会话读取失败如实标为「状态未知」并给出错误码，
        不把失败读成空列表或伪造标题（R21）。
        """

        if self._service is None or not self._state.available:
            return
        if self._state.sessions_loading:
            return
        self._track_inflight_cancellable()
        self._set_state(sessions_loading=True, sessions_error_code=None)
        try:
            page = await self._service.search(
                HistoryQuery(
                    scope="all_saved",
                    round_selection=HistoryRoundSelection(kind="all_rounds"),
                    query_text="",
                    time_basis="updated",
                    sort="newest_first",
                    limit=_SESSION_SCAN_LIMIT,
                ),
                user_scope=self._user_scope,
            )
        except asyncio.CancelledError:
            self._set_state(sessions_loading=False)
            raise
        except Exception as e:
            self._set_state(sessions_loading=False, sessions_error_code=self._error_code(e))
            return
        session_ids: list[str] = []
        for hit in page.hits:
            if hit.session_id not in session_ids:
                session_ids.append(hit.session_id)
            if len(session_ids) >= SESSION_LIST_LIMIT:
                break
        items: list[SessionListItemView] = []
        failure: str | None = None
        for session_id in session_ids:
            try:
                read = await self._service.read_session(
                    SessionReadQuery(session_id=session_id, limit=1),
                    user_scope=self._user_scope,
                )
            except asyncio.CancelledError:
                self._set_state(sessions_loading=False)
                raise
            except Exception as e:
                failure = failure or self._error_code(e)
                items.append(_unknown_session_item(session_id))
                continue
            summary = read.summary
            items.append(
                SessionListItemView(
                    session_id=session_id,
                    title=(summary.title or None),
                    updated_at=getattr(summary, "updated_at", None),
                    revision=int(getattr(summary, "revision", 0) or 0),
                    pinned=bool(getattr(summary, "pinned", False)),
                    archived=bool(getattr(summary, "archived", False)),
                    cleaned=getattr(summary, "cleaned_at", None) is not None,
                    status_unknown=False,
                )
            )
        self._set_state(sessions_loading=False, sessions=tuple(items), sessions_error_code=failure)

    def set_new_session_title(self, text: str) -> None:
        """记录「新建会话」标题输入（纯状态；空标题提交时如实拒绝）。"""

        self._set_state(new_session_title=text)

    async def create_session(self) -> None:
        """新建空会话并导航到它（标题必填；不派发研究、不写草稿）。"""

        if self._service is None or not self._state.available:
            self._set_state(
                session_action_error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED,
                session_action_error_code=None,
            )
            return
        title = self._state.new_session_title.strip()
        if not title:
            self._set_state(
                session_action_error_key="agent_research_session_title_required",
                session_action_error_code=None,
            )
            return
        session_id = f"ui-session-{uuid.uuid4().hex}"
        receipt = await self._change_session_metadata(
            session_id,
            SessionAction.CREATE,
            expected_revision=0,
            title=title,
        )
        if receipt is None:
            return
        self._set_state(new_session_title="")
        await self.open_session(session_id)
        if not any(item.session_id == session_id for item in self._state.sessions):
            # 新会话尚无消息/成果，历史搜索不会命中：按创建回执补一条（版来自回执）。
            self._set_state(
                sessions=(
                    SessionListItemView(
                        session_id=session_id,
                        title=title,
                        updated_at=None,
                        revision=int(getattr(receipt, "current_revision", 0) or 0),
                        pinned=False,
                        archived=False,
                        cleaned=False,
                    ),
                    *self._state.sessions,
                )
            )

    def begin_rename(self, session_id: str) -> None:
        """进入改名编辑（目标必须已在列表中；预填当前标题，不猜标题）。"""

        item = next((entry for entry in self._state.sessions if entry.session_id == session_id), None)
        if item is None:
            self._set_state(
                session_action_error_key="agent_research_session_unknown",
                session_action_error_code=None,
            )
            return
        self._set_state(
            rename_target_session_id=session_id,
            rename_text=item.title or "",
            session_action_error_key=None,
            session_action_error_code=None,
        )

    def set_rename_text(self, text: str) -> None:
        self._set_state(rename_text=text)

    def cancel_rename(self) -> None:
        self._set_state(rename_target_session_id=None, rename_text="")

    async def commit_rename(self) -> None:
        """提交改名（CAS 预期版取自列表摘要；版本未知则拒绝，不猜版覆盖）。"""

        target = self._state.rename_target_session_id
        item = next((entry for entry in self._state.sessions if entry.session_id == target), None)
        if target is None or item is None:
            self._set_state(
                session_action_error_key="agent_research_session_unknown",
                session_action_error_code=None,
                rename_target_session_id=None,
                rename_text="",
            )
            return
        title = self._state.rename_text.strip()
        if not title:
            self._set_state(
                session_action_error_key="agent_research_session_title_required",
                session_action_error_code=None,
            )
            return
        if item.revision is None:
            self._set_state(
                session_action_error_key="agent_research_session_revision_unknown",
                session_action_error_code=None,
            )
            return
        receipt = await self._change_session_metadata(
            target,
            SessionAction.RENAME,
            expected_revision=item.revision,
            title=title,
        )
        if receipt is not None:
            self._set_state(rename_target_session_id=None, rename_text="")

    async def pin_session(self, session_id: str) -> None:
        """置顶会话（元数据 CAS；不改变研究执行）。"""

        await self._run_session_action(session_id, SessionAction.PIN)

    async def archive_session(self, session_id: str) -> None:
        """归档会话（只改元数据，不终止执行；§9.3）。"""

        await self._run_session_action(session_id, SessionAction.ARCHIVE)

    async def _run_session_action(self, session_id: str, action: SessionAction) -> None:
        item = next((entry for entry in self._state.sessions if entry.session_id == session_id), None)
        if item is None or item.revision is None:
            self._set_state(
                session_action_error_key="agent_research_session_revision_unknown",
                session_action_error_code=None,
            )
            return
        await self._change_session_metadata(session_id, action, expected_revision=item.revision)

    async def _change_session_metadata(
        self,
        session_id: str,
        action: SessionAction,
        *,
        expected_revision: int,
        title: str | None = None,
    ) -> Any | None:
        """执行会话元数据动作（唯一写入口 ``change_session``）；失败返回 None 并报码。"""

        service = self._service
        change = getattr(service, "change_session", None) if service is not None else None
        if not callable(change):
            self._set_state(session_action_error_key=UNAVAILABLE_NOT_WIRED, session_action_error_code=None)
            return None
        if action is not SessionAction.CREATE and expected_revision < 1:
            self._set_state(
                session_action_error_key="agent_research_session_revision_unknown",
                session_action_error_code=None,
            )
            return None
        if self._state.session_action_pending:
            return None
        command_id = f"ui-session-{action.value}:{session_id}:{expected_revision}:{title or ''}"
        self._set_state(
            session_action_pending=True,
            session_action_error_key=None,
            session_action_error_code=None,
            last_error_code=None,
        )
        try:
            receipt = await cast("Callable[..., Awaitable[Any]]", change)(
                SessionChangeCommand(
                    command=CommandRef(
                        user_scope=self._user_scope,
                        source_module="ui",
                        business_request_id=command_id,
                        command_id=command_id,
                        expected_revision=expected_revision,
                    ),
                    action=action,
                    session_id=session_id,
                    expected_session_revision=expected_revision,
                    title=title,
                )
            )
        except asyncio.CancelledError:
            self._set_state(session_action_pending=False)
            raise
        except Exception as e:
            code = self._error_code(e)
            self._set_state(
                session_action_pending=False,
                session_action_error_key=None,
                session_action_error_code=code,
                last_error_code=code,
            )
            return None
        self._set_state(session_action_pending=False)
        await self.load_sessions()
        if session_id == self._state.session_id:
            await self.refresh()
        return receipt

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
        self._session_cleaned = False
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
            # 会话级 UI 状态随会话消失（首问正文属于面板输入，不在此清空）。
            session_revision=0,
            last_answer_text="",
            last_dispatch_reason=None,
            plan_steps=(),
            plan_reference=None,
            plan_error_key=None,
            rename_target_session_id=None,
            rename_text="",
            # 费用/耗时/冻结选择随会话消失（模型与角色的待发送选择保留在工具栏）。
            usage_round=None,
            usage_cumulative=None,
            usage_error_key=None,
            durations=(),
            durations_execution_id=None,
            model_selection_frozen=None,
            role_selection_frozen=None,
            # W-13：流式增量、权威水位、进度与观察现场随会话消失（不保留上一会话残影）。
            stream_fragments=(),
            stream_text="",
            stream_synthetic_visible=False,
            authoritative_seq=0,
            last_catch_up_error_code=None,
            progress=_UNKNOWN_PROGRESS,
            observation_path=(),
            return_to_execution_id=None,
            restored_scroll_anchor=None,
            restored_child_selection=None,
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
        # 清理事实优先于缓存复用（T-05）：同一会话刷新读到清理状态时，已加载的
        # 正文/草稿缓存不得继续重画（清理只把库内文本换成 tombstone，缓存里的旧
        # 内容仍是可读明文，保留即把「已清理」伪装成「仍可读」）。
        session_cleaned = read.summary.cleaned_at is not None
        self._session_cleaned = session_cleaned
        self._raw_executions = tuple(read.executions)
        self._raw_messages = tuple(read.messages.items)
        self._raw_artifacts = tuple(read.artifacts.items)
        selected = _normalized_selection(self._raw_executions, self._state.selected_execution_id)
        collapsed = _normalized_collapsed(self._raw_executions, self._state.collapsed_execution_ids)
        artifact_keys = {f"{item.artifact_id}:{item.artifact_version}" for item in self._raw_artifacts}
        cleaned_keys = {
            f"{item.artifact_id}:{item.artifact_version}" for item in self._raw_artifacts if item.cleaned_at is not None
        }
        pending_keys = tuple(
            key for key in self._state.artifact_body_pending_keys if key in artifact_keys and key not in cleaned_keys
        )
        # R2-I23：已载入草稿只在新快照仍指向同一会话同一稿版时保留；稿版已推进、
        # 切了会话或会话已清理即丢弃，避免把旧稿正文冒充当前可继续编辑的内容。
        loaded_draft = self._state.session_draft
        if session_cleaned or (
            loaded_draft is not None
            and (loaded_draft.session_id != session_id or loaded_draft.draft_revision != read.summary.draft_revision)
        ):
            loaded_draft = None
        if session_changed or session_cleaned:
            artifact_bodies = ()
        else:
            artifact_bodies = tuple(body for body in self._state.artifact_bodies if body.key not in cleaned_keys)
        # 切换会话即隔离草稿与上一会话的直答/计划：正文与计划属于具体会话，
        # 不随后续发送串到另一会话（W-14）；无会话时的首问草稿不在此清空。
        switch_changes: dict[str, Any] = (
            {
                "composer_text": "",
                "composer_error_key": None,
                "last_answer_text": "",
                "last_dispatch_reason": None,
                "plan_steps": (),
                "plan_reference": None,
                "plan_error_key": None,
                "control_result": None,
                "control_pending_execution_id": None,
                "control_reason_execution_id": None,
                "control_reason_kind": None,
                "control_reason_text": "",
                "control_reason_error_key": None,
            }
            if session_changed
            else {}
        )
        self._set_state(
            loading=False,
            session_id=session_id,
            session_title=read.summary.title,
            session_revision=int(getattr(read.summary, "revision", 0) or 0),
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
            # 换会话或读到清理状态时不复用正文缓存（同 id 跨会话/已清理也不冒充可读成果）
            artifact_bodies=artifact_bodies,
            session_draft=loaded_draft,
            draft_error_code=None,
            # 持久化首读为权威：内存增量片段被快照取代（返焦/重连不得只靠内存增量）。
            stream_fragments=(),
            stream_text="",
            stream_synthetic_visible=False,
            progress=_progress_summary(
                session_known=True,
                executions=self._raw_executions,
                pending_count=pending_count,
                artifact_count=len(self._raw_artifacts),
                streaming=False,
            ),
            **self._observation_changes(selected),
            **switch_changes,
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

    def select_execution(self, execution_id: str | None, *, scroll_anchor: str | None = None) -> None:
        """改变观察位置（整会话 / 某执行子树）；只改展示过滤，不改变研究执行。

        子会话为只读视图，返回父级即恢复到上一现场：``scroll_anchor`` 由 View 上报
        当前滚动锚点，离开节点前存入可注入的现场存储；返回该节点时恢复锚点与
        离开时选中的子执行（父→子→返回父两级返回）。
        """

        current = self._state.selected_execution_id
        selected = _normalized_selection(self._raw_executions, execution_id)
        self._save_view_state(current, scroll_anchor=scroll_anchor, leaving_to=selected)
        restored = self._load_view_state(selected)
        self._set_state(
            selected_execution_id=selected,
            restored_scroll_anchor=restored.scroll_anchor if restored is not None else None,
            restored_child_selection=restored.selected_execution_id if restored is not None else None,
            **self._observation_changes(selected),
            **self._progress_changes(),
            **self._projection_changes(
                selected=selected,
                collapsed=self._state.collapsed_execution_ids,
            ),
        )

    def _save_view_state(
        self,
        execution_id: str | None,
        *,
        scroll_anchor: str | None,
        leaving_to: str | None,
    ) -> None:
        """离开某观察节点前保存现场（会话隔离；无会话不保存，避免跨会话锚点）。"""

        session_id = self._state.session_id
        if session_id is None:
            return
        self._view_store.save(
            session_id,
            execution_id or "",
            ObservationViewState(scroll_anchor=scroll_anchor, selected_execution_id=leaving_to),
        )

    def _load_view_state(self, execution_id: str | None) -> ObservationViewState | None:
        session_id = self._state.session_id
        if session_id is None:
            return None
        return self._view_store.load(session_id, execution_id or "")

    def _observation_changes(self, selected: str | None) -> dict[str, Any]:
        """观察位置派生：父链与「返回父级」目标（无父即整会话，返回 None）。"""

        path = _ancestor_path(self._raw_executions, selected)
        return {
            "observation_path": path,
            "return_to_execution_id": path[-1] if path else None,
        }

    def _progress_changes(self, *, streaming: bool | None = None) -> dict[str, Any]:
        """按当前已知事实重算首层进度（未读到会话时显式未知，R21）。

        ``streaming`` 由调用方在改 state 前显式给出（避免读到上一帧的片段集合）。
        """

        return {
            "progress": _progress_summary(
                session_known=self._state.session_id is not None,
                executions=self._raw_executions,
                pending_count=self._state.pending_count,
                artifact_count=len(self._raw_artifacts),
                streaming=bool(self._state.stream_fragments) if streaming is None else streaming,
            )
        }

    # ------------------------------------------------------------------
    # 增量流式呈现（W-13：受治理片段；合成片段显式标记，不混入真实正文）
    # ------------------------------------------------------------------

    def apply_stream_event(self, event: object, *, synthetic: bool = False) -> None:
        """合并一个受治理流事件（``LeafStreamEvent`` / ``ChatStreamEvent`` 形态）。

        按 ``sequence`` 幂等合并：重复序号忽略、乱序按序插入（展示顺序恒为来源
        顺序）；``synthetic=True`` 标记本地合成/占位片段，不进入 ``stream_text``。
        本方法只改展示状态，不落库、不推进执行；权威补齐仍以持久事件为准。
        """

        # N01：惰性导入，避免 UI 模块导入期拉入 ``services.agent_service`` 重依赖。
        from services.agent_service.model import (
            STREAM_SOURCE_MODEL,
            STREAM_SOURCE_SYNTHETIC,
            project_stream_text_fragment,
        )

        fragment = project_stream_text_fragment(
            event,
            source=STREAM_SOURCE_SYNTHETIC if synthetic else STREAM_SOURCE_MODEL,
        )
        if fragment is None:
            return
        self._merge_stream_fragment(fragment)

    def apply_stream_text(
        self,
        text: str,
        *,
        sequence: int | None = None,
        synthetic: bool = False,
    ) -> None:
        """合并一段增量文本（无来源事件时的入口；``sequence`` 缺省按已并入序号 +1）。"""

        if not text:
            return
        # N01：惰性导入，避免 UI 模块导入期拉入 ``services.agent_service`` 重依赖。
        from services.agent_service.model import (
            STREAM_SOURCE_MODEL,
            STREAM_SOURCE_SYNTHETIC,
            StreamedTextFragment,
        )

        resolved = sequence if sequence is not None else self._next_stream_sequence()
        self._merge_stream_fragment(
            StreamedTextFragment(
                sequence=resolved,
                text=text,
                source=STREAM_SOURCE_SYNTHETIC if synthetic else STREAM_SOURCE_MODEL,
            )
        )

    def _merge_stream_fragment(self, fragment: StreamedTextFragment) -> None:
        """按序号幂等合并（重复序号覆盖为同一片段，乱序按序插入）。"""

        # N01：惰性导入，避免 UI 模块导入期拉入 ``services.agent_service`` 重依赖。
        from services.agent_service.model import STREAM_SOURCE_MODEL

        fragments = [item for item in self._state.stream_fragments if item.sequence != fragment.sequence]
        fragments.append(fragment)
        fragments.sort(key=lambda item: item.sequence)
        self._set_state(
            stream_fragments=tuple(fragments),
            stream_text="".join(item.text for item in fragments if item.source == STREAM_SOURCE_MODEL),
            stream_synthetic_visible=any(item.synthetic for item in fragments),
            **self._progress_changes(streaming=True),
        )

    def reset_stream(self) -> None:
        """清空内存增量片段（断线/切会话；不影响持久化消息与执行）。"""

        self._set_state(
            stream_fragments=(),
            stream_text="",
            stream_synthetic_visible=False,
            **self._progress_changes(streaming=False),
        )

    def _next_stream_sequence(self) -> int:
        fragments = self._state.stream_fragments
        return (max((item.sequence for item in fragments), default=0)) + 1

    # ------------------------------------------------------------------
    # 返焦/重连：以持久化事件为权威补齐（W-13）
    # ------------------------------------------------------------------

    async def catch_up(self) -> None:
        """窗口返焦或连接恢复时自动核权威：先追平持久事件，再重读会话快照。

        内存增量只用于即时呈现；本方法以 ``read_events`` 的持久水位为权威，
        并把会话首读重新投影（消息/成果/进度均来自持久化事实）。重复事件按
        ``session_seq`` 幂等：没有新事件时不改动已呈现的增量片段。
        """

        if self._service is None or not self._state.available:
            return
        session_id = self._state.session_id
        if session_id is None:
            await self.refresh()
            return
        await self._reconcile_persisted_events(session_id)
        await self.refresh()

    async def _reconcile_persisted_events(self, session_id: str) -> None:
        """按 ``authoritative_seq`` 有界追平持久事件；未追平如实报码，不宣称已核。

        可靠事件包络不含 TextDelta（临时事件无可靠 ``session_seq``，见
        ``ResearchEvent`` 契约），因此增量文本永远以 ``refresh`` 的会话快照为准；
        追平只确认「持久事实已前进」，用于判定内存增量是否已被快照取代。
        """

        service = self._service
        reader = getattr(service, "read_events", None) if service is not None else None
        if not callable(reader):
            # 未接线事件读取：仍以会话首读为权威（refresh），不报成功也不报错。
            return
        read_events = cast("Callable[[EventReadQuery], Awaitable[Any]]", reader)
        after_seq = self._state.authoritative_seq
        cursor = after_seq
        incomplete = False
        try:
            for _page_index in range(_EVENT_CATCH_UP_MAX_PAGES):
                page = await read_events(
                    EventReadQuery(
                        user_scope=self._user_scope,
                        session_id=session_id,
                        after_seq=cursor,
                        limit=_EVENT_CATCH_UP_PAGE,
                    )
                )
                for event in getattr(page, "events", ()):
                    seq = getattr(event, "session_seq", None)
                    if isinstance(seq, int) and not isinstance(seq, bool) and seq > cursor:
                        cursor = seq
                last_seq = getattr(page, "last_seq", None)
                if isinstance(last_seq, int) and not isinstance(last_seq, bool) and last_seq > cursor:
                    cursor = last_seq
                if not bool(getattr(page, "has_more", False)):
                    break
            else:
                incomplete = True  # 达到页数上限仍有更多：不宣称已核（R21）
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(last_catch_up_error_code=self._error_code(e))
            return
        changes: dict[str, Any] = {
            "last_catch_up_error_code": CATCH_UP_INCOMPLETE if incomplete else None,
            "authoritative_seq": cursor,
        }
        if cursor > after_seq:
            # 持久事件确认了更新事实：内存增量被权威快照取代（后续 refresh 投影）。
            changes.update(stream_fragments=(), stream_text="", stream_synthetic_visible=False)
        self._set_state(**changes)

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
        if self._session_cleaned or self._artifact_is_cleaned(key):
            # 清理事实先于本次读取到达：读回的是清理前内容，不得作为当前可读正文
            # 重画（T-05；并发刷新竞态）。
            self._set_state(
                artifact_body_pending_keys=tuple(
                    pending for pending in self._state.artifact_body_pending_keys if pending != key
                )
            )
            return
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
        # 并发刷新已切走会话/推进稿版/读到清理时，读回的旧稿不再属于当前观察位置。
        current = self._state
        session_mismatch = current.session_id is not None and current.session_id != read.session_id
        revision_mismatch = current.session_id is not None and current.session_draft_revision != read.draft_revision
        if session_mismatch or revision_mismatch or self._session_cleaned:
            self._set_state(draft_pending=False)
            return
        self._set_state(draft_pending=False, session_draft=_session_draft_view(read))

    def _artifact_is_cleaned(self, key: str) -> bool:
        """当前首读投影中该成果版本是否已清理（清理只换 tombstone 文本）。"""

        return any(
            f"{item.artifact_id}:{item.artifact_version}" == key and item.cleaned_at is not None
            for item in self._raw_artifacts
        )

    def set_composer_text(self, text: str) -> None:
        """记录首问正文（纯状态；收起观察层不清稿）。"""

        self._set_state(composer_text=text)

    def set_composer_mode(self, mode: str) -> None:
        """记录用户选择的模式（只接受三值；未知值不改写当前模式）。"""

        if mode in COMPOSER_MODES:
            self._set_state(composer_mode=mode)

    # -- 研究侧 MCP 范围（J25） --------------------------------------------

    async def load_mcp_scope(self) -> None:
        """读取可选的 MCP 服务目录；目录未接线保持不可用，读取失败如实报错。"""

        catalog = self._mcp_catalog
        if catalog is None:
            return
        try:
            configs = await catalog.list_services()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_state(mcp_scope_error_key="agent_research_mcp_scope_error")
            return
        self._mcp_identities = {config.identity.stable_key(): config.identity for config in configs}
        options = tuple((config.identity.stable_key(), config.display_name) for config in configs)
        selected = tuple(key for key in self._state.mcp_scope_selected if key in self._mcp_identities)
        self._set_state(
            mcp_scope_options=options,
            mcp_scope_selected=selected,
            mcp_scope_error_key=None,
        )

    def toggle_mcp_scope(self, identity_key: str) -> None:
        """勾选/取消本次允许的 MCP 服务（只接受目录内服务；空选择=不使用 MCP）。"""

        if identity_key not in self._mcp_identities and not any(
            key == identity_key for key, _label in self._state.mcp_scope_options
        ):
            return
        selected = set(self._state.mcp_scope_selected)
        if identity_key in selected:
            selected.discard(identity_key)
        else:
            selected.add(identity_key)
        self._set_state(mcp_scope_selected=tuple(sorted(selected)))

    # -- 研究侧模型/角色选择（F16/F27/J23） --------------------------------

    async def load_model_selection(self) -> None:
        """读取研究场景候选连接与角色选项（只读；目录缺失/失败如实报 key）。

        模型选项只来自 ``scenario_routes[agent_research].candidates``——与
        ``resolve_agent_model_binding`` 解算连接的同一路径；不在该路由内的连接
        不进入选项，选中即显式拒绝（不静默换成默认模型）。
        """

        role_options = build_role_options()
        current_role = self._state.role_selection
        role_selection = current_role
        if role_selection is None or self._role_option(role_options, role_selection) is None:
            role_selection = next((option.role_key for option in role_options if option.eligible), None)
        catalog = self._model_catalog
        if catalog is None:
            self._set_state(
                role_options=role_options,
                role_selection=role_selection,
                role_selection_error_key=None,
                model_selection_available=False,
            )
            return
        try:
            snapshot = await catalog.get_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._set_state(
                role_options=role_options,
                role_selection=role_selection,
                role_selection_error_key=None,
                model_selection_error_key="agent_research_model_selection_error",
            )
            return
        options = build_model_connection_options(snapshot)
        self._connection_rows = _research_connections(snapshot)
        current_model = self._state.model_selection
        model_selection = current_model if self._option(options, current_model) is not None else None
        if model_selection is None:
            model_selection = next((option.connection_id for option in options if option.eligible), None)
        self._set_state(
            model_selection_available=True,
            model_options=options,
            model_selection=model_selection,
            model_selection_error_key=None if options else "agent_research_model_selection_route_unavailable",
            role_options=role_options,
            role_selection=role_selection,
            role_selection_error_key=None,
        )

    @staticmethod
    def _option(options: Sequence[ModelConnectionOption], connection_id: str | None) -> ModelConnectionOption | None:
        for option in options:
            if option.connection_id == connection_id:
                return option
        return None

    @staticmethod
    def _role_option(options: Sequence[RoleOption], role_key: str | None) -> RoleOption | None:
        for option in options:
            if option.role_key == role_key:
                return option
        return None

    async def select_model_connection(self, connection_id: str) -> None:
        """选择模型连接：仅接受研究场景路由候选且已启用；非法选择显式拒绝。"""

        option = self._option(self._state.model_options, connection_id)
        if option is None:
            self._set_state(model_selection_error_key="agent_research_model_selection_unknown")
            return
        if option.reject_key is not None:
            self._set_state(model_selection_error_key=option.reject_key)
            return
        self._set_state(model_selection=connection_id, model_selection_error_key=None)
        await self.refresh_budget_hint()

    def select_role(self, role_key: str) -> None:
        """选择研究角色：未知/子角色/版本缺失显式拒绝，不静默替换为默认角色。"""

        option = self._role_option(self._state.role_options, role_key)
        if option is None:
            self._set_state(role_selection_error_key="agent_research_role_selection_unknown")
            return
        if option.reject_key is not None:
            self._set_state(role_selection_error_key=option.reject_key)
            return
        self._set_state(role_selection=role_key, role_selection_error_key=None)

    def _selected_connection(self) -> Mapping[str, Any] | None:
        """当前选择的连接定义（未选择/目录未载入 → None，不猜候选）。"""

        connection_id = self._state.model_selection
        if connection_id is None:
            return None
        return self._connection_rows.get(connection_id)

    async def refresh_budget_hint(self) -> None:
        """发送前预算提示：按当前选择与正文估算（token 计数经 CPU 池，R16）。

        文本/选择在计数期间变化时以最新票据为准，旧结果丢弃（不落过期估算）。
        """

        self._budget_ticket += 1
        ticket = self._budget_ticket
        connection = self._selected_connection()
        text = self._state.composer_text
        if self._model_catalog is None:
            self._set_state(budget=None)
            return
        counter = self._token_counter
        estimate = await ThreadPoolManager().run_async(
            TaskType.CPU, estimate_budget, text=text, connection=connection, token_counter=counter
        )
        if ticket != self._budget_ticket:
            return
        self._set_state(budget=estimate)

    # -- 费用与耗时分段（F27/NFR-P06） -------------------------------------

    async def refresh_usage(self) -> None:
        """读取本轮与累计费用、五段耗时（只读投影；读取面不可用如实未知）。"""

        service = self._service
        reader = getattr(service, "get_snapshot", None) if service is not None else None
        if not callable(reader):
            self._set_state(
                usage_round=None,
                usage_cumulative=None,
                durations=(),
                durations_execution_id=None,
                usage_error_key="agent_research_usage_unavailable",
            )
            return
        executions = self._state.executions
        target = self._state.selected_execution_id or (executions[0][0] if executions else None)
        if target is None:
            self._set_state(
                usage_round=None,
                usage_cumulative=None,
                durations=(),
                durations_execution_id=None,
                usage_error_key=None,
            )
            return
        get_snapshot = cast("Callable[..., Awaitable[Any]]", reader)
        try:
            # 消息页用于解算「排队」的受理时刻；页大小见 ``_DURATION_MESSAGE_PAGE_LIMIT``。
            snapshot = await get_snapshot(target, user_scope=self._user_scope, limit=_DURATION_MESSAGE_PAGE_LIMIT)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._set_state(
                usage_round=unavailable_cost_view(self._error_code(e)),
                usage_cumulative=None,
                durations=build_duration_segments(),
                durations_execution_id=target,
                usage_error_key="agent_research_usage_error",
            )
            return
        round_view = await self._project_execution_cost(snapshot)
        cumulative = await self._project_cumulative_cost(executions, prefetched={target: round_view})
        values = derive_duration_values(snapshot)
        self._set_state(
            usage_round=round_view,
            usage_cumulative=cumulative,
            usage_error_key=None,
            durations=build_duration_segments(
                queue_ms=values["queue"],
                user_wait_ms=values["user_wait"],
                active_ms=values["work"],
                model_wait_ms=values["model_wait"],
                save_ms=values["save"],
            ),
            durations_execution_id=target,
        )

    async def _project_execution_cost(self, snapshot: Any) -> CostUsageView:
        """单个执行快照 → 费用投影（未知/未计价/预留分列，不拼成伪零）。"""

        usage = getattr(snapshot, "usage", None)
        cost_state = getattr(usage, "cost_state", None)
        attempt_count = getattr(usage, "model_attempt_count", None)
        ledger_refs = tuple(
            dict.fromkeys(
                operation.invocation_id
                for operation in getattr(snapshot, "operation_summaries", ()) or ()
                if isinstance(getattr(operation, "invocation_id", None), str)
                and getattr(operation, "invocation_id", "")
            )
        )
        if cost_state == "known" and not ledger_refs:
            # 账本无引用且模型调用计数为 0：确实零费用（已知零，与未知可区分）。
            if attempt_count == 0:
                return cost_state_view("known", known_cny=Decimal("0"), reserved_cny=Decimal("0"))
            return cost_state_view("unknown")
        if cost_state in ("unpriced", "reserved", "unknown"):
            return cost_state_view(cast(str, cost_state))
        if cost_state != "known":
            return cost_state_view("unknown")
        service = self._service
        reader = getattr(service, "read_cost_projection", None) if service is not None else None
        if not callable(reader):
            return unavailable_cost_view("ledger_unavailable")
        read_projection = cast("Callable[..., Awaitable[Any]]", reader)
        try:
            projection = await read_projection(ledger_refs=ledger_refs)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            return unavailable_cost_view(self._error_code(e))
        if getattr(projection, "read_state", None) != "ok":
            return unavailable_cost_view(getattr(projection, "read_error", None) or "read_failed")
        known = _decimal_amount(getattr(projection, "known_cny", None))
        unpriced = getattr(projection, "unpriced_attempts", None)
        if known is None:
            return cost_state_view("unknown", unpriced_attempts=unpriced if isinstance(unpriced, int) else None)
        if isinstance(unpriced, int) and unpriced > 0:
            return cost_state_view(
                "partial",
                known_cny=known,
                reserved_cny=_decimal_amount(getattr(projection, "reserved_cny", None)),
                unpriced_attempts=unpriced,
            )
        return cost_state_view(
            "known",
            known_cny=known,
            reserved_cny=_decimal_amount(getattr(projection, "reserved_cny", None)),
            unpriced_attempts=unpriced if isinstance(unpriced, int) else None,
        )

    async def _project_cumulative_cost(
        self,
        executions: tuple[tuple[str, str], ...],
        *,
        prefetched: Mapping[str, CostUsageView] | None = None,
    ) -> CostUsageView:
        """会话累计费用：逐执行只读投影求和；任一执行未知即不冒充完整总额。

        ``prefetched`` 为已为本轮算过的执行视图（避免同一执行重复读取账本）。
        """

        if not executions:
            return cost_state_view("unknown")
        service = self._service
        reader = getattr(service, "get_snapshot", None) if service is not None else None
        if not callable(reader):
            return unavailable_cost_view("ledger_unavailable")
        get_snapshot = cast("Callable[..., Awaitable[Any]]", reader)
        known_views = prefetched or {}
        limit = min(len(executions), CUMULATIVE_EXECUTION_LIMIT)
        truncated = len(executions) > limit
        total: Decimal | None = None
        unpriced_total = 0
        complete = True
        for execution_id, _label in executions[:limit]:
            view = known_views.get(execution_id)
            if view is None:
                try:
                    snapshot = await get_snapshot(execution_id, user_scope=self._user_scope, limit=1)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    return unavailable_cost_view(self._error_code(e))
                view = await self._project_execution_cost(snapshot)
            if view.state in ("failed", "unknown", "unpriced", "reserved"):
                complete = False
                if view.unpriced_attempts:
                    unpriced_total += view.unpriced_attempts
                continue
            if view.known_cny is not None:
                total = view.known_cny if total is None else total + view.known_cny
            if view.state == "partial":
                complete = False
                unpriced_total += view.unpriced_attempts or 0
        if not complete:
            if total is None:
                return cost_state_view("unknown", truncated=truncated)
            return cost_state_view("partial", known_cny=total, unpriced_attempts=unpriced_total, truncated=truncated)
        return cost_state_view(
            "known",
            known_cny=total if total is not None else Decimal("0"),
            truncated=truncated,
        )

    async def send_first_question(self) -> None:
        """发送首问：经注入的受理端口创建会话/受理（讨论/计划零派发属预期）。

        未接线受理端口时如实给出不可用 key（R21），不伪造已发送；取消继续传播
        （R2）；空正文不提交。本次手选的 MCP 服务子集与角色随首问冻结进 manifest；
        角色/连接选择非法时显式拒绝发送（不静默回退默认角色或默认模型）。
        """

        intake = self._intake
        if intake is None or not self._state.composer_available:
            self._set_state(composer_error_key=self._state.unavailable_reason_key or UNAVAILABLE_NOT_WIRED)
            return
        text = self._state.composer_text.strip()
        if not text:
            self._set_state(composer_error_key="agent_research_empty_draft")
            return
        role_refs: tuple[tuple[str, str], ...] = ()
        role_key = self._state.role_selection
        if role_key is not None:
            role_option = self._role_option(self._state.role_options, role_key)
            if role_option is None or role_option.reject_key is not None or role_option.version is None:
                self._set_state(
                    composer_error_key=role_option.reject_key
                    if role_option is not None and role_option.reject_key is not None
                    else "agent_research_role_selection_unknown"
                )
                return
            role_refs = ((role_key, role_option.version),)
        if self._state.composer_pending:
            return
        self._track_inflight_cancellable()
        self._set_state(composer_pending=True, composer_error_key=None, last_dispatch_reason=None)
        selection = tuple(
            self._mcp_identities[key] for key in self._state.mcp_scope_selected if key in self._mcp_identities
        )
        try:
            result = await intake.start_question(
                text,
                mode=Mode(self._state.composer_mode),
                session_id=self._state.session_id,
                expected_revision=(self._state.session_draft_revision if self._state.session_id is not None else 0),
                role_prompt_refs=role_refs,
                mcp_selection=selection,
            )
        except asyncio.CancelledError:
            self._set_state(composer_pending=False)
            raise
        except Exception as e:
            self._set_state(composer_pending=False, composer_error_key=self._error_code(e))
            return
        main_turn = getattr(result, "main_turn", None)
        changes: dict[str, Any] = {
            "composer_pending": False,
            "composer_text": "",
            # 选择随首问冻结（同会话内展示冻结值，不被后续点选静默改写）。
            "model_selection_frozen": self._state.model_selection,
            "role_selection_frozen": self._state.role_selection,
        }
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
        await self.refresh_budget_hint()

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
            from services.agent_service.main_turn import PlanReference

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
    def _dispatch_reason(result: Any) -> DispatchReasonView | None:
        """受理结果的派发事实：已派发为 None；未接线/零派发返回结构化原因。"""

        dispatch = getattr(result, "dispatch", None)
        if dispatch is None:
            return DispatchReasonView(code=DISPATCH_REASON_NOT_WIRED)
        if getattr(dispatch, "submitted", False):
            return None
        code = getattr(dispatch, "reason_code", None)
        raw_params = getattr(dispatch, "reason_params", ())
        params: tuple[tuple[str, str], ...] = ()
        if isinstance(raw_params, tuple | list):
            params = tuple(
                (name, value) for name, value in raw_params if isinstance(name, str) and isinstance(value, str)
            )
        return DispatchReasonView(
            code=code if isinstance(code, str) and code else DISPATCH_REASON_UNKNOWN,
            params=params,
        )

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
    mcp_catalog: MCPScopeCatalogPort | None = None,
    model_catalog: ModelCatalogPort | None = None,
) -> ResearchObserverViewModel:
    """取本会话共享的观察层 VM（同一 facade/受理端口复用；变化或已销毁则重建）。"""

    global _shared_vm
    session_key = id(service) if service is not None else None
    if (
        _shared_vm is None
        or _shared_vm.disposed
        or _shared_vm._session_key != session_key
        or _shared_vm._intake is not intake
        or _shared_vm._mcp_catalog is not mcp_catalog
        # 未显式注入目录时由 VM 自建只读目录实例，重复调用不得因 None 反复重建。
        or (model_catalog is not None and _shared_vm._model_catalog is not model_catalog)
    ):
        stale = _shared_vm
        _shared_vm = ResearchObserverViewModel(
            service,
            user_scope=user_scope,
            unavailable_reason_key=unavailable_reason_key,
            intake=intake,
            mcp_catalog=mcp_catalog,
            model_catalog=model_catalog,
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
