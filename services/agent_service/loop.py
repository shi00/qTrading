"""唯一 Agent 执行循环（R2-I08：模式、工具/委派限制与成果保存）。

职责边界：

- **唯一循环**：讨论、计划、执行、直答四条路径共用 :class:`ExecutionLoop.run`
  这一个循环；模式只改变可见动作与工具白名单，不复制第二套执行逻辑。
- **模式绑定**（F21 / DD-F03）：

  - ``ANSWER``（直答）：零工具、零委派、零研究操作，直接回答；
  - ``DISCUSS``（讨论）：只允许调用调用方登记的获准读资料工具，零研究检索/
    MCP 研究调用、零叶子派发，结论为直答；
  - ``PLAN``（仅计划）：只允许计划工具，交付计划即结束，**不自动转执行**；
  - ``EXECUTE``（执行）：才允许研究工具与一层委派，最终成果可靠保存。

- **一层委派**：主循环委派时固定传递 ``depth=1`` / ``allow_delegation=False``；
  子循环若再次提案委派会在派发前被拒（不递归、不借 MCP 起下级 Agent）。
- **成果保存**：终态必须由 :class:`ResearchResultProducer` 写入
  ``ResearchResult``；保存失败或未确认保存（``final_output_saved=False``）
  一律不返回成功，不伪造 ``complete``（R21）。
- **取消/失败**：``asyncio.CancelledError`` 原样传播（R2）；模型/工具/委派
  异常不被吞没，失败时不保存成果、不返回成功终态。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from core.agent_types import (
    ARTIFACT_KINDS,
    DISPLAY_STATES,
    GOAL_COVERAGES,
    AgentErrorCode,
    AgentServiceError,
    CanonicalJson,
    Mode,
    ResearchResult,
    ResearchResultDraft,
    SourceVersionRef,
    is_digest,
)

__all__ = [
    "AgentTurn",
    "DelegationRequest",
    "DelegationResult",
    "DelegationRunner",
    "DelegationScope",
    "ExecutionLoop",
    "LoopAction",
    "LoopIdentity",
    "LoopLimits",
    "LoopObservation",
    "LoopPolicy",
    "LoopRunOutcome",
    "LoopRunState",
    "ModePolicy",
    "ResearchResultProducer",
    "ResearchResultStore",
    "ToolCall",
    "ToolExecutor",
    "TurnContext",
    "TurnDecision",
    "mode_policy",
]

#: 直答/讨论结论的成果类别（不作为研究结果冒充股票 final）。
ANSWER_RESULT_KIND: Final[str] = "explanation"
#: 仅计划交付的成果类别。
PLAN_RESULT_KIND: Final[str] = "plan"


class LoopAction(Enum):
    """模型回合可提案的动作（未知动作由 :class:`TurnDecision` 拒绝）。"""

    ANSWER = "answer"
    PLAN = "plan"
    RESULT = "result"
    TOOL_CALL = "tool_call"
    DELEGATE = "delegate"


class LoopRunState(Enum):
    """循环成功终态（失败/取消/限额不返回成功状态）。"""

    ANSWERED = "answered"
    PLANNED = "planned"
    RESEARCHED = "researched"


@dataclass(frozen=True, slots=True)
class LoopIdentity:
    """一次循环的冻结身份与溯源引用（成果归属，调用方提供）。"""

    user_scope: str
    session_id: str
    execution_id: str
    round_id: str
    node_id: str
    input_revision: int
    actual_input_digest: str
    actual_model_ref: str
    plan_version: int | None = None
    parent_artifact_refs: tuple[str, ...] = ()
    source_refs: tuple[SourceVersionRef, ...] = ()
    ledger_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("user_scope", "session_id", "execution_id", "round_id", "node_id", "actual_model_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"identity {name} must be non-empty text")
        if isinstance(self.input_revision, bool) or not isinstance(self.input_revision, int) or self.input_revision < 1:
            raise ValueError("identity input_revision must be a positive int")
        if not is_digest(self.actual_input_digest):
            raise ValueError("identity actual_input_digest must be a lowercase sha256 digest")
        if self.plan_version is not None and (
            isinstance(self.plan_version, bool) or not isinstance(self.plan_version, int) or self.plan_version < 1
        ):
            raise ValueError("identity plan_version must be a positive int or None")
        for name in ("parent_artifact_refs", "ledger_refs"):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"identity {name} must be a tuple of non-empty text")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, SourceVersionRef) for ref in self.source_refs
        ):
            raise ValueError("identity source_refs must be a tuple of SourceVersionRef")


@dataclass(frozen=True, slots=True)
class LoopLimits:
    """循环预算：超限显式 ``BUDGET_BLOCKED``，不静默截断或伪造完成。"""

    max_turns: int = 24
    max_tool_calls: int = 20
    max_delegations: int = 20

    def __post_init__(self) -> None:
        for name in ("max_turns", "max_tool_calls", "max_delegations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int")


@dataclass(frozen=True, slots=True)
class ModePolicy:
    """模式可见动作与工具白名单（``tool_names=None`` 表示不额外收窄）。"""

    mode: Mode
    actions: frozenset[LoopAction]
    tool_names: frozenset[str] | None
    allow_delegation: bool

    def allow(self, action: LoopAction) -> bool:
        return action in self.actions


#: 计划工具默认白名单：计划模式只交付/修订方法，不执行研究。
DEFAULT_PLAN_TOOLS: Final[frozenset[str]] = frozenset({"write_plan", "read_plan"})


def mode_policy(
    mode: Mode,
    *,
    read_tool_names: frozenset[str] = frozenset(),
    plan_tool_names: frozenset[str] = DEFAULT_PLAN_TOOLS,
) -> ModePolicy:
    """模式 → 动作/工具/委派策略（唯一映射，循环据此在派发前拒绝越界动作）。

    - ``ANSWER``：仅 ``ANSWER``；零工具、零委派；
    - ``DISCUSS``：``ANSWER`` + 获准读资料工具；零研究工具、零委派；
    - ``PLAN``：``PLAN`` + 计划工具；零研究工具、零委派，计划后不自动执行；
    - ``EXECUTE``：``ANSWER/RESULT/TOOL_CALL/DELEGATE``；工具按调用方登记的
      全量白名单，允许一层委派。
    """

    if mode is Mode.ANSWER:
        return ModePolicy(
            mode=mode, actions=frozenset({LoopAction.ANSWER}), tool_names=frozenset(), allow_delegation=False
        )
    if mode is Mode.DISCUSS:
        return ModePolicy(
            mode=mode,
            actions=frozenset({LoopAction.ANSWER, LoopAction.TOOL_CALL}),
            tool_names=read_tool_names,
            allow_delegation=False,
        )
    if mode is Mode.PLAN:
        return ModePolicy(
            mode=mode,
            actions=frozenset({LoopAction.PLAN, LoopAction.TOOL_CALL}),
            tool_names=plan_tool_names,
            allow_delegation=False,
        )
    return ModePolicy(
        mode=mode,
        actions=frozenset({LoopAction.ANSWER, LoopAction.RESULT, LoopAction.TOOL_CALL, LoopAction.DELEGATE}),
        tool_names=None,
        allow_delegation=True,
    )


@dataclass(frozen=True, slots=True)
class ToolCall:
    """一次工具调用提案：参数必须是闭合 JSON 对象（工具执行前由注册表再核）。"""

    tool_name: str
    arguments: CanonicalJson

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name:
            raise ValueError("tool name must be non-empty text")
        if not isinstance(self.arguments, str) or not self.arguments:
            raise ValueError("tool arguments must be canonical JSON text")


@dataclass(frozen=True, slots=True)
class DelegationRequest:
    """一层委派提案（主 Agent → 子 Agent；子不得再委派）。"""

    node_id: str
    target: str
    role_key: str
    source_refs: tuple[SourceVersionRef, ...] = ()
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("node_id", "target", "role_key"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be non-empty text")
        for name in ("capabilities",):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(item, str) or not item for item in values):
                raise ValueError(f"{name} must be a tuple of non-empty text")
        if not isinstance(self.source_refs, tuple) or any(
            not isinstance(ref, SourceVersionRef) for ref in self.source_refs
        ):
            raise ValueError("source_refs must be a tuple of SourceVersionRef")


@dataclass(frozen=True, slots=True)
class DelegationScope:
    """委派深度与递归授权（子 Agent 固定 depth=1、allow_delegation=False）。"""

    depth: int
    allow_delegation: bool


@dataclass(frozen=True, slots=True)
class DelegationResult:
    """子 Agent 的可核对结果（失败/未知如实保留，不借成功兄弟补答）。"""

    node_id: str
    state: str
    summary: str | None = None
    artifact_ref: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str) or not self.node_id:
            raise ValueError("delegation result requires node_id")
        if self.state not in DISPLAY_STATES:
            raise ValueError("delegation result state must be a frozen display state")
        for name in ("summary", "artifact_ref", "error_code"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError(f"{name} must be non-empty text or None")


@dataclass(frozen=True, slots=True)
class LoopObservation:
    """回合观察：工具返回或子 Agent 结果（下一回合可见）。"""

    kind: str
    ref: str
    text: str | None
    state: str | None = None


@dataclass(frozen=True, slots=True)
class TurnDecision:
    """模型回合提案（每个动作只允许其对应载荷，越界在构造时拒绝）。"""

    action: LoopAction
    text: str | None = None
    summary: str | None = None
    key_points: tuple[str, ...] = ()
    structured_fields: CanonicalJson | None = None
    object_rows: tuple[CanonicalJson, ...] = ()
    evidence_links: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    data_warnings: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    result_kind: str | None = None
    goal_coverage: str = "unknown"
    convergence_confirmed: bool = False
    tool_call: ToolCall | None = None
    delegation: DelegationRequest | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, LoopAction):
            raise ValueError("turn action must be a LoopAction")
        if self.tool_call is not None and self.action is not LoopAction.TOOL_CALL:
            raise ValueError("tool_call is only valid for TOOL_CALL")
        if self.delegation is not None and self.action is not LoopAction.DELEGATE:
            raise ValueError("delegation is only valid for DELEGATE")
        if self.action is LoopAction.TOOL_CALL and self.tool_call is None:
            raise ValueError("TOOL_CALL requires a tool_call")
        if self.action is LoopAction.DELEGATE and self.delegation is None:
            raise ValueError("DELEGATE requires a delegation")
        if self.action in (LoopAction.ANSWER, LoopAction.PLAN):
            if not isinstance(self.text, str) or not self.text:
                raise ValueError(f"{self.action.value} requires non-empty text")
        if self.action is LoopAction.PLAN and self.result_kind not in (None, PLAN_RESULT_KIND):
            raise ValueError("PLAN result_kind must be 'plan'")
        if self.action is LoopAction.RESULT:
            if self.result_kind is None or self.result_kind not in ARTIFACT_KINDS:
                raise ValueError("RESULT requires a known result_kind")
            if self.result_kind == PLAN_RESULT_KIND:
                raise ValueError("PLAN must use the PLAN action")
        if self.goal_coverage not in GOAL_COVERAGES:
            raise ValueError("goal_coverage must be a frozen coverage value")
        if not isinstance(self.convergence_confirmed, bool):
            raise ValueError("convergence_confirmed must be bool")


@dataclass(frozen=True, slots=True)
class TurnContext:
    """回合上下文：模式、回合号与累计观察（观察按发生顺序，不复制隐藏推理）。"""

    mode: Mode
    turn: int
    observations: tuple[LoopObservation, ...]


AgentTurn = Callable[[TurnContext], Awaitable[TurnDecision]]
"""模型回合端口：返回动作提案；异常/取消原样传播。"""

ToolExecutor = Callable[[ToolCall], Awaitable[str | None]]
"""工具执行端口：返回可公开文本（``None`` 表示无返回，与空文本可区分）。"""

DelegationRunner = Callable[[DelegationRequest, DelegationScope], Awaitable[DelegationResult]]
"""子 Agent 执行端口：必须按 ``DelegationScope`` 禁止递归委派。"""


class ResearchResultStore(Protocol):
    """成果可靠保存端口（生产为 :class:`~data.persistence.daos.agent_dao.AgentDao`）。"""

    async def save_research_result(self, draft: ResearchResultDraft) -> ResearchResult: ...


@dataclass(frozen=True, slots=True)
class LoopRunOutcome:
    """循环成功终态（保存失败会抛错，不构造本对象）。"""

    state: LoopRunState
    result: ResearchResult
    turns: int
    tool_calls: int
    delegations: int
    observations: tuple[LoopObservation, ...]


LoopPolicy = ModePolicy


class ResearchResultProducer:
    """把循环终态产出为已可靠保存的 :class:`ResearchResult`。

    保存失败或保存端口未确认（``final_output_saved=False``）一律抛
    ``PERSISTENCE_UNAVAILABLE``：执行结束与成果保存必须同时成立，不以内存
    文本冒充已保存成果（R21）。
    """

    def __init__(self, store: ResearchResultStore) -> None:
        self._store = store

    async def produce(
        self,
        *,
        identity: LoopIdentity,
        decision: TurnDecision,
        result_kind: str,
        execution_state: str,
    ) -> ResearchResult:
        draft = ResearchResultDraft(
            user_scope=identity.user_scope,
            session_id=identity.session_id,
            execution_id=identity.execution_id,
            round_id=identity.round_id,
            node_id=identity.node_id,
            input_revision=identity.input_revision,
            actual_input_digest=identity.actual_input_digest,
            actual_model_ref=identity.actual_model_ref,
            result_kind=result_kind,
            summary=decision.summary,
            key_points=decision.key_points,
            body_text=decision.text,
            structured_fields=decision.structured_fields,
            object_rows=decision.object_rows,
            evidence_links=decision.evidence_links,
            # 来源是否仍可得不由循环猜测：未核定写 unknown，读取时按原事实呈现。
            source_available_state="unknown",
            coverage=None,
            missing_fields=decision.missing_fields,
            failed_signal_dates=(),
            data_warnings=decision.data_warnings,
            limitations=decision.limitations,
            verification_state="unverified",
            execution_state=execution_state,
            goal_coverage=decision.goal_coverage,
            convergence_confirmed=decision.convergence_confirmed,
            unknown_operation_refs=(),
            ledger_refs=identity.ledger_refs,
            plan_version=identity.plan_version,
            parent_artifact_refs=identity.parent_artifact_refs,
            source_refs=identity.source_refs,
        )
        saved = await self._store.save_research_result(draft)
        if not isinstance(saved, ResearchResult) or saved.final_output_saved is not True:
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "research result save was not confirmed",
                f"{identity.user_scope}/{identity.execution_id}",
            )
        return saved


class ExecutionLoop:
    """唯一 Agent 执行循环（模式裁剪 → 工具/一层委派 → 成果保存）。

    单实例单次运行：并发调用 ``run`` 显式拒绝，不共享可变回合状态；取消与
    模型/工具/委派异常原样传播（R2），失败时不保存成果。
    """

    def __init__(
        self,
        *,
        identity: LoopIdentity,
        mode: Mode,
        turn: AgentTurn,
        producer: ResearchResultProducer,
        tools: ToolExecutor | None = None,
        delegate: DelegationRunner | None = None,
        limits: LoopLimits | None = None,
        policy: ModePolicy | None = None,
        read_tool_names: frozenset[str] = frozenset(),
        plan_tool_names: frozenset[str] = DEFAULT_PLAN_TOOLS,
        depth: int = 0,
    ) -> None:
        if isinstance(depth, bool) or not isinstance(depth, int) or depth not in (0, 1):
            raise ValueError("loop depth must be 0 (main) or 1 (subagent)")
        self._identity = identity
        self._mode = mode
        self._turn = turn
        self._producer = producer
        self._tools = tools
        self._delegate = delegate
        self._limits = limits or LoopLimits()
        self._depth = depth
        self._policy = policy or mode_policy(
            mode,
            read_tool_names=read_tool_names,
            plan_tool_names=plan_tool_names,
        )
        self._running = False

    @property
    def policy(self) -> ModePolicy:
        return self._policy

    async def run(self) -> LoopRunOutcome:
        """推进唯一回合循环直到成功终态；越权动作在派发前拒绝。"""

        if self._running:
            raise AgentServiceError(
                AgentErrorCode.INPUT_INVALID,
                "agent loop is already running",
                f"{self._identity.user_scope}/{self._identity.execution_id}",
            )
        self._running = True
        try:
            observations: list[LoopObservation] = []
            tool_calls = 0
            delegations = 0
            for turn_number in range(1, self._limits.max_turns + 1):
                decision = await self._turn(
                    TurnContext(mode=self._mode, turn=turn_number, observations=tuple(observations))
                )
                if decision.action in (LoopAction.ANSWER, LoopAction.PLAN, LoopAction.RESULT):
                    return await self._finish(
                        decision,
                        turns=turn_number,
                        tool_calls=tool_calls,
                        delegations=delegations,
                        observations=observations,
                    )
                if decision.action is LoopAction.TOOL_CALL:
                    self._require_action(decision.action)
                    call = decision.tool_call
                    assert call is not None  # TurnDecision 已校验
                    self._require_tool(call.tool_name)
                    if self._tools is None:
                        raise self._denied(f"no tool executor registered for {call.tool_name}")
                    if tool_calls >= self._limits.max_tool_calls:
                        raise AgentServiceError(
                            AgentErrorCode.BUDGET_BLOCKED,
                            "tool call budget exhausted",
                            self._identity.execution_id,
                        )
                    text = await self._tools(call)
                    tool_calls += 1
                    observations.append(LoopObservation(kind="tool", ref=call.tool_name, text=text))
                    continue
                # DELEGATE
                self._require_action(LoopAction.DELEGATE)
                request = decision.delegation
                assert request is not None  # TurnDecision 已校验
                if self._depth > 0:
                    raise self._denied("subagents cannot delegate (one layer only)")
                if not self._policy.allow_delegation or self._delegate is None:
                    raise self._denied("delegation is not allowed in this mode")
                if delegations >= self._limits.max_delegations:
                    raise AgentServiceError(
                        AgentErrorCode.BUDGET_BLOCKED,
                        "delegation budget exhausted",
                        self._identity.execution_id,
                    )
                result = await self._delegate(
                    request,
                    DelegationScope(depth=1, allow_delegation=False),
                )
                if result.node_id != request.node_id:
                    raise AgentServiceError(
                        AgentErrorCode.INPUT_INVALID,
                        "delegation result identity mismatch",
                        self._identity.execution_id,
                    )
                delegations += 1
                observations.append(
                    LoopObservation(
                        kind="delegation",
                        ref=result.node_id,
                        text=result.summary,
                        state=result.state,
                    )
                )
            raise AgentServiceError(
                AgentErrorCode.BUDGET_BLOCKED,
                "turn budget exhausted without a final result",
                self._identity.execution_id,
            )
        finally:
            self._running = False

    async def _finish(
        self,
        decision: TurnDecision,
        *,
        turns: int,
        tool_calls: int,
        delegations: int,
        observations: list[LoopObservation],
    ) -> LoopRunOutcome:
        self._require_action(decision.action)
        if decision.action is LoopAction.PLAN:
            result_kind = PLAN_RESULT_KIND
            state = LoopRunState.PLANNED
            execution_state = "planned"
        elif decision.action is LoopAction.RESULT:
            assert decision.result_kind is not None  # TurnDecision 已校验
            result_kind = decision.result_kind
            state = LoopRunState.RESEARCHED
            execution_state = "complete" if decision.convergence_confirmed else "running"
        else:
            result_kind = ANSWER_RESULT_KIND
            state = LoopRunState.ANSWERED
            execution_state = "complete" if decision.convergence_confirmed else "running"
        saved = await self._producer.produce(
            identity=self._identity,
            decision=decision,
            result_kind=result_kind,
            execution_state=execution_state,
        )
        return LoopRunOutcome(
            state=state,
            result=saved,
            turns=turns,
            tool_calls=tool_calls,
            delegations=delegations,
            observations=tuple(observations),
        )

    def _require_action(self, action: LoopAction) -> None:
        if not self._policy.allow(action):
            raise self._denied(f"action {action.value} is not allowed in {self._mode.value} mode")

    def _require_tool(self, tool_name: str) -> None:
        allowed = self._policy.tool_names
        if allowed is not None and tool_name not in allowed:
            raise self._denied(f"tool {tool_name} is not allowed in {self._mode.value} mode")

    def _denied(self, reason: str) -> AgentServiceError:
        return AgentServiceError(
            AgentErrorCode.POLICY_DENIED,
            reason,
            f"{self._identity.user_scope}/{self._identity.execution_id}/{self._identity.node_id}",
        )
