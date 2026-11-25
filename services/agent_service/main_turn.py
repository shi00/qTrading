"""主回合生产实现（F01/F03/F16/F21；契约 §4/§4.1）。

职责边界：

- **唯一循环的生产构造点**：:class:`MainTurnRunner` 只构造既有
  :class:`~services.agent_service.loop.ExecutionLoop`，不复制第二套执行逻辑；
  模式仍取三值 ``DISCUSS`` / ``PLAN`` / ``EXECUTE``，直答是循环动作
  （``LoopAction.ANSWER``）而非第四模式。
- **讨论/计划零研究动作**：讨论只把获准资料以读工具交给循环并直接作答；计划只
  交付可读计划并等待用户「开始这份计划」。两者研究取数/检索/MCP 研究调用/叶子
  派发均为 0——委派端口在这两种模式下**不注入**，工具白名单只含调用方登记的
  读资料工具 / 计划工具（越界动作由循环在派发前拒绝）。
- **执行**：才允许一层委派；已确认计划经受理回执投递到持久研究 workflow
  （派发准入在 ``dispatch``，本模块只提供计划来源与角色能力来源）。
- **可读计划 → 可执行计划显式映射**：计划模式把模型给出的结构化步骤保存为
  既有 ``PlanProposal``（用户可读计划）；「开始这份计划」再按该计划版经
  :func:`plan_spec_from_proposal` 生成 ``PlanSpecProposal``（可执行节点表）。
  两者共用同一份步骤内容，不把可读步骤直接冒充可执行节点表。
- **角色能力来源**：只复用本次请求已冻结的 Skill/MCP 显式手选集
  （:func:`derive_frozen_selection_policy`），不新增角色能力配置表；未选择
  视为空集并 fail-closed（``None``），不得解释为「全部允许」。
- **如实拒绝**：计划来源/能力来源/受理事实任一不可得即零派发；``CancelledError``
  原样传播（R2），其余异常只记录脱敏原因，不伪造已派发（R21）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Protocol, cast

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    CanonicalJson,
    ClarificationAnswerFact,
    ClarificationFact,
    CommandReceipt,
    CommandRef,
    Mode,
    PlanItemSnapshot,
    PlanNodeSpec,
    PlanProposal,
    PlanSpecProposal,
    Purpose,
    SaveClarificationDecisionRequest,
    SessionAction,
    SessionChangeCommand,
    SourceVersionRef,
    MCPServiceIdentity,
    SubmissionReceipt,
    decode_json_doc,
    decode_plan_proposal,
    decode_source_version_ref,
    derive_round_id,
    encode_mcp_service_identity,
    is_digest,
    json_doc,
    normalize_plan_proposal,
    sha256_hex,
)
from services.agent_service.capabilities import PlanProposalStore
from services.agent_service.dispatch import (
    PLAN_UNAVAILABLE,
    DispatchOutcome,
    PlanSpecProvider,
    ResearchDispatcher,
    RoleCapabilityProvider,
    zero_dispatch,
)
from services.agent_service.loop import (
    DEFAULT_PLAN_TOOLS,
    AgentTurn,
    DelegationRunner,
    ExecutionLoop,
    LoopIdentity,
    LoopLimits,
    LoopRunOutcome,
    LoopRunState,
    ResearchResultProducer,
    ToolExecutor,
    TurnDecision,
)
from utils.sanitizers import DataSanitizer

__all__ = [
    "FrozenSelectionPolicy",
    "MainTurnIntake",
    "MainTurnIntakeResult",
    "MainTurnOutcome",
    "MainTurnRequest",
    "MainTurnRunner",
    "PlanReference",
    "build_plan_spec_provider",
    "build_role_capability_provider",
    "decode_plan_reference",
    "derive_frozen_selection_policy",
    "plan_spec_from_proposal",
    "plan_steps_from_structured_fields",
]

logger = logging.getLogger(__name__)

#: 路由级模型引用（真实模型名由执行器落账；回合身份只需稳定、可追溯的引用）。
MAIN_TURN_MODEL_REF: Final[str] = "agent_research:governed"

#: 澄清问题的可答窗口：超期即不再注入下一轮，不把过期问答冒充已澄清。
CLARIFICATION_VALIDITY: Final[timedelta] = timedelta(days=7)

#: 澄清结构化载荷键（问题文本另由 ``TurnDecision.text`` 承载）。
CLARIFICATION_OPTIONS_KEY: Final[str] = "options"
CLARIFICATION_IMPACT_KEY: Final[str] = "impact"

#: 结构化计划步骤在回合 ``structured_fields`` 中的顶层键。
PLAN_STEPS_KEY: Final[str] = "steps"
#: 计划引用在冻结 manifest 中的顶层键（「开始这份计划」新意图据此定位已审版本）。
PLAN_REFERENCE_KEY: Final[str] = "plan_ref"
#: 计划来源在冻结 manifest 中的顶层键：``auto`` 表示执行模式首问由主回合自动规划。
PLAN_SOURCE_KEY: Final[str] = "plan_source"
#: 自动规划来源取值（仅执行模式首问冻结；其余入口仍只认显式 ``plan_ref``）。
PLAN_SOURCE_AUTO: Final[str] = "auto"

_PLAN_STEP_KEYS: Final[frozenset[str]] = frozenset({"id", "content", "depends_on"})
_PLAN_REFERENCE_KEYS: Final[frozenset[str]] = frozenset({"execution_id", "plan_revision"})


def _clarification_expiry() -> str:
    """澄清问题的有界人答窗口（UTC 微秒 Z 文本，与 DAO wire 时点一致）。"""

    return (datetime.now(UTC) + CLARIFICATION_VALIDITY).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fail(code: str, path: str) -> AgentValidationError:
    return AgentValidationError(code, path)


# ---------------------------------------------------------------------------
# 冻结手选集合 → 角色能力（唯一来源）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FrozenSelectionPolicy:
    """本次请求冻结的角色与能力（来自显式手选 Skill/MCP 集合）。"""

    role_key: str
    capabilities: frozenset[str]
    source_refs: tuple[SourceVersionRef, ...] = ()


def _manifest_object(manifest_payload: str) -> dict[str, Any]:
    """规范 manifest → 对象（非规范/非对象一律拒绝，不做宽容解析）。"""

    if not isinstance(manifest_payload, str) or not manifest_payload:
        raise _fail("invalid_type", "$.manifest_payload")
    decoded = decode_json_doc(manifest_payload)
    if not isinstance(decoded, dict):
        raise _fail("invalid_type", "$.manifest_payload")
    return decoded


def _frozen_section(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    """冻结输入段：比较组 manifest 取 ``shared``，普通 manifest 取顶层。"""

    shared = manifest.get("shared")
    if isinstance(shared, Mapping):
        return shared
    return manifest


def _ref_pairs(value: object, path: str) -> tuple[tuple[str, str], ...]:
    """``[[id, version], ...]`` → 去空值对；形状非法即拒绝（不猜语义）。"""

    if value in (None, []):
        return ()
    if not isinstance(value, list):
        raise _fail("invalid_type", path)
    pairs: list[tuple[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise _fail("invalid_type", f"{path}[{index}]")
        first, second = item[0], item[1]
        if not isinstance(first, str) or not first or not isinstance(second, str) or not second:
            raise _fail("invalid_value", f"{path}[{index}]")
        pairs.append((first, second))
    return tuple(pairs)


def _source_refs(section: Mapping[str, Any]) -> tuple[SourceVersionRef, ...]:
    """冻结资料引用（可为空；非法引用即拒绝，不静默丢弃）。"""

    raw = section.get("source_refs")
    if raw in (None, []):
        return ()
    if not isinstance(raw, list):
        raise _fail("invalid_type", "$.manifest_payload.source_refs")
    return tuple(
        decode_source_version_ref(item, f"$.manifest_payload.source_refs[{index}]") for index, item in enumerate(raw)
    )


def derive_frozen_selection_policy(manifest_payload: str) -> FrozenSelectionPolicy | None:
    """冻结 manifest → 角色能力策略；不可判定/空手选集合返回 ``None``（fail-closed）。

    - 角色：``role_prompt_refs`` 必须恰好一项（读计划步骤不含角色字段，多个角色
      无法无歧义映射，按不可判定处理）；
    - 能力：``skill_refs`` 每项生成 ``skill:<id>@<version>``，``mcp_scope_digest``
      非空时生成 ``mcp_scope:<digest>``；
    - 能力集为空 = 用户未显式手选任何 Skill/MCP：返回 ``None``，**不得**把「未
      选择」解释为全部允许（契约 §6 默认拒绝）。
    """

    section = _frozen_section(_manifest_object(manifest_payload))
    roles = _ref_pairs(section.get("role_prompt_refs"), "$.manifest_payload.role_prompt_refs")
    if len(roles) != 1:
        return None
    capabilities = {
        f"skill:{skill_id}@{version}"
        for skill_id, version in _ref_pairs(section.get("skill_refs"), "$.manifest_payload.skill_refs")
    }
    digest = section.get("mcp_scope_digest")
    if isinstance(digest, str) and digest:
        capabilities.add(f"mcp_scope:{digest}")
    if not capabilities:
        return None
    return FrozenSelectionPolicy(
        role_key=roles[0][0],
        capabilities=frozenset(capabilities),
        source_refs=_source_refs(section),
    )


# ---------------------------------------------------------------------------
# 已确认计划版本 → 可执行节点表
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanReference:
    """「开始这份计划」引用的已确认计划版本（源执行 + 计划版）。"""

    execution_id: str
    plan_revision: int


def decode_plan_reference(manifest_payload: str) -> PlanReference | None:
    """冻结 manifest → 计划引用；未携带返回 ``None``，形状非法即拒绝。"""

    section = _frozen_section(_manifest_object(manifest_payload))
    raw = section.get(PLAN_REFERENCE_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise _fail("invalid_type", f"$.manifest_payload.{PLAN_REFERENCE_KEY}")
    if set(raw) != _PLAN_REFERENCE_KEYS:
        raise _fail("invalid_keys_unknown", f"$.manifest_payload.{PLAN_REFERENCE_KEY}")
    execution_id = raw["execution_id"]
    revision = raw["plan_revision"]
    if not isinstance(execution_id, str) or not execution_id:
        raise _fail("invalid_value", f"$.manifest_payload.{PLAN_REFERENCE_KEY}.execution_id")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise _fail("invalid_revision", f"$.manifest_payload.{PLAN_REFERENCE_KEY}.plan_revision")
    return PlanReference(execution_id=execution_id, plan_revision=revision)


def plan_spec_from_proposal(proposal: PlanProposal, policy: FrozenSelectionPolicy) -> PlanSpecProposal:
    """已确认计划版 → ``PlanSpecProposal``（角色/能力/来源均由冻结手选集派生）。

    节点身份取计划步骤 ``id``，目标取用户可见正文 ``content``，依赖取步骤
    ``depends_on``；能力集为本次手选全集（节点不得超出），来源引用取冻结请求的
    资料引用。图合法性（空计划/环/缺引用/角色/能力）仍由派发前
    ``admit_research_plan`` 判定：非法图零执行。
    """

    if not isinstance(proposal, PlanProposal):
        raise _fail("invalid_type", "$.plan_proposal")
    if not isinstance(policy, FrozenSelectionPolicy):
        raise _fail("invalid_type", "$.role_policy")
    capabilities = tuple(sorted(policy.capabilities))
    nodes: list[PlanNodeSpec] = []
    for index, item in enumerate(proposal.items):
        target = item.content
        if not isinstance(target, str) or not target.strip():
            # 可执行节点必须有可读目标；空步骤不伪造为非空目标（R21）。
            raise _fail("invalid_value", f"$.plan_proposal.items[{index}].content")
        nodes.append(
            PlanNodeSpec(
                node_id=item.id,
                target=target,
                role_key=policy.role_key,
                depends_on=item.depends_on,
                capabilities=capabilities,
                source_refs=policy.source_refs,
            )
        )
    return PlanSpecProposal(nodes=tuple(nodes))


def plan_steps_from_structured_fields(structured_fields: CanonicalJson | None) -> tuple[PlanItemSnapshot, ...] | None:
    """回合结构化字段 → 计划步骤快照；未给出返回 ``None``，形状非法即拒绝。

    只接受 ``{"steps": [{"id", "content", "depends_on"}]}``：其它形状不猜测、
    不丢字段，直接拒绝（拒绝即不保存计划版，启动入口因无计划引用而零派发）。
    """

    if structured_fields is None:
        return None
    decoded = decode_json_doc(structured_fields)
    if not isinstance(decoded, dict) or set(decoded) != {PLAN_STEPS_KEY}:
        raise _fail("invalid_keys_unknown", "$.structured_fields")
    raw_steps = decoded[PLAN_STEPS_KEY]
    if not isinstance(raw_steps, list):
        raise _fail("invalid_type", "$.structured_fields.steps")
    steps: list[PlanItemSnapshot] = []
    for index, raw in enumerate(raw_steps):
        path = f"$.structured_fields.steps[{index}]"
        if not isinstance(raw, dict) or set(raw) != _PLAN_STEP_KEYS:
            raise _fail("invalid_keys_unknown", path)
        step_id = raw["id"]
        content = raw["content"]
        depends_on = raw["depends_on"]
        if not isinstance(step_id, str) or not step_id:
            raise _fail("invalid_value", f"{path}.id")
        if not isinstance(content, str) or not content.strip():
            raise _fail("invalid_value", f"{path}.content")
        if not isinstance(depends_on, list) or any(not isinstance(item, str) or not item for item in depends_on):
            raise _fail("invalid_type", f"{path}.depends_on")
        steps.append(
            PlanItemSnapshot(
                id=step_id,
                content=content,
                status="pending",
                active_form=content,
                parent_id=None,
                depends_on=tuple(depends_on),
            )
        )
    return tuple(steps)


# ---------------------------------------------------------------------------
# 计划来源 / 角色能力来源（生产端口）
# ---------------------------------------------------------------------------


class _ManifestReader(Protocol):
    async def read_authoritative_manifest(self, *, user_scope: str, execution_id: str) -> str | None: ...


class _PlanProposalReader(Protocol):
    async def read_plan_proposal(
        self, *, user_scope: str, execution_id: str, plan_revision: int
    ) -> PlanProposal | None: ...


def _auto_plan_requested(manifest: Mapping[str, Any]) -> bool:
    """冻结 manifest 是否声明「执行模式首问自动规划」（只认显式值，不猜）。"""

    return _frozen_section(manifest).get(PLAN_SOURCE_KEY) == PLAN_SOURCE_AUTO


def build_plan_spec_provider(dao: _ManifestReader, proposals: _PlanProposalReader | None = None) -> PlanSpecProvider:
    """生产计划来源：显式计划引用优先，其次执行模式首问的自动规划版本。

    - 显式 ``plan_ref``（「开始这份计划」）→ 已确认计划版 → 可执行节点表；
    - ``plan_source=auto``（执行模式首问）→ 本执行当前计划版（主回合已保存且经
      ``decode_plan_proposal`` 严格校验）→ 可执行节点表；无保存版即不可得；
    - 角色/能力仍只取冻结手选集（``derive_frozen_selection_policy``），未选择即
      不可得；其余形状一律返回 ``None``（派发侧据此 ``PLAN_UNAVAILABLE`` 零派发）。
    """

    reader: _PlanProposalReader = proposals if proposals is not None else cast(_PlanProposalReader, dao)

    async def _provide(user_scope: str, execution_id: str) -> PlanSpecProposal | None:
        manifest = await dao.read_authoritative_manifest(user_scope=user_scope, execution_id=execution_id)
        if manifest is None:
            return None
        decoded = _manifest_object(manifest)
        reference = decode_plan_reference(manifest)
        if reference is not None:
            proposal = await reader.read_plan_proposal(
                user_scope=user_scope,
                execution_id=reference.execution_id,
                plan_revision=reference.plan_revision,
            )
        elif _auto_plan_requested(decoded):
            current = getattr(dao, "read_current_plan_proposal", None)
            if not callable(current):
                return None
            read_current = cast(Any, current)
            proposal = await read_current(user_scope=user_scope, execution_id=execution_id)
        else:
            return None
        if proposal is None:
            return None
        policy = derive_frozen_selection_policy(manifest)
        if policy is None:
            return None
        return plan_spec_from_proposal(proposal, policy)

    return _provide


def build_role_capability_provider(dao: _ManifestReader) -> RoleCapabilityProvider:
    """生产角色能力来源：只取本次请求冻结的 Skill/MCP 手选集合。"""

    async def _provide(user_scope: str, execution_id: str) -> Mapping[str, frozenset[str]] | None:
        manifest = await dao.read_authoritative_manifest(user_scope=user_scope, execution_id=execution_id)
        if manifest is None:
            return None
        policy = derive_frozen_selection_policy(manifest)
        if policy is None:
            return None
        return {policy.role_key: policy.capabilities}

    return _provide


# ---------------------------------------------------------------------------
# 唯一循环的生产构造
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MainTurnRequest:
    """一次主回合的冻结身份、模式与提示（模式只由用户选择，模型不得切换）。"""

    identity: LoopIdentity
    mode: Mode
    prompt_text: str
    source_refs: tuple[SourceVersionRef, ...] = ()
    read_tool_names: frozenset[str] = frozenset()
    plan_tool_names: frozenset[str] = DEFAULT_PLAN_TOOLS
    #: 本轮之前已答复且尚未消费的澄清问答（由受理入口取一次，只注入不重取）。
    clarification_answers: tuple[ClarificationAnswerFact, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LoopIdentity):
            raise ValueError("main turn identity must be a LoopIdentity")
        if not isinstance(self.mode, Mode):
            raise ValueError("main turn mode must be a Mode")
        if not isinstance(self.prompt_text, str) or not self.prompt_text.strip():
            raise ValueError("main turn prompt_text must be non-empty text")
        for name in ("read_tool_names", "plan_tool_names"):
            value = getattr(self, name)
            if not isinstance(value, frozenset) or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"main turn {name} must be a frozenset of non-empty text")
        if not isinstance(self.clarification_answers, tuple) or any(
            not isinstance(item, ClarificationAnswerFact) for item in self.clarification_answers
        ):
            raise ValueError("main turn clarification_answers must be a tuple of ClarificationAnswerFact")


@dataclass(frozen=True, slots=True)
class MainTurnOutcome:
    """主回合结果：循环终态 + 计划模式保存的计划引用（未保存为 ``None``）。

    ``clarification`` 为澄清终态携带的提案（``CLARIFIED`` 时非空且无成果）。
    """

    outcome: LoopRunOutcome
    plan_reference: PlanReference | None = None
    clarification: TurnDecision | None = None


#: 主回合回合端口工厂：按请求（含提示/模式）产出该次回合的模型调用。
TurnFactory = Callable[[MainTurnRequest], AgentTurn]


class MainTurnRunner:
    """生产主回合：构造唯一 ``ExecutionLoop``，讨论/计划零研究动作。

    ``tools`` 在讨论模式只应包含调用方登记的「已准备资料」读工具；计划模式只应
    包含计划工具。``delegate`` 只在执行模式注入——讨论/计划即使模型提案委派，也
    由循环在派发前以 ``POLICY_DENIED`` 拒绝（零叶子派发）。
    """

    def __init__(
        self,
        *,
        producer: ResearchResultProducer,
        tools: ToolExecutor | None = None,
        delegate: DelegationRunner | None = None,
        limits: LoopLimits | None = None,
        plan_store_factory: Any | None = None,
        turn_factory: TurnFactory | None = None,
    ) -> None:
        self._producer = producer
        self._tools = tools
        self._delegate = delegate
        self._limits = limits
        self._plan_store_factory = plan_store_factory
        self._turn_factory = turn_factory

    async def run(self, request: MainTurnRequest, *, turn: AgentTurn | None = None) -> MainTurnOutcome:
        """推进一次主回合；计划成功交付时保存用户可读计划版并返回其引用。"""

        mode = request.mode
        effective_turn = turn
        if effective_turn is None and self._turn_factory is not None:
            effective_turn = self._turn_factory(request)
        if effective_turn is None:
            # 未注入模型回合端口：零模型调用、零成果（不伪造回合）。
            raise AgentServiceError(
                AgentErrorCode.POLICY_DENIED,
                "main turn model port not wired",
                request.identity.execution_id,
            )
        loop = ExecutionLoop(
            identity=request.identity,
            mode=mode,
            turn=effective_turn,
            producer=self._producer,
            tools=self._tools,
            # 讨论/计划一律不注入委派端口（零叶子派发，契约 §4.1）。
            delegate=self._delegate if mode is Mode.EXECUTE else None,
            limits=self._limits,
            read_tool_names=request.read_tool_names,
            plan_tool_names=request.plan_tool_names,
            depth=0,
        )
        outcome = await loop.run()
        if outcome.state is LoopRunState.CLARIFIED:
            # 澄清终态无成果，也不存在可保存的计划版（不伪造计划/直答）。
            return MainTurnOutcome(outcome=outcome, clarification=outcome.clarification)
        if mode is not Mode.PLAN:
            return MainTurnOutcome(outcome=outcome)
        return MainTurnOutcome(outcome=outcome, plan_reference=await self._save_plan(request, outcome))

    async def _save_plan(self, request: MainTurnRequest, outcome: LoopRunOutcome) -> PlanReference | None:
        """把计划回合的结构化步骤保存为可读计划版；未给出或非法步骤即不保存。

        模型提案先按既有 ``decode_plan_proposal`` 严格校验（结构版本/键集/步骤
        身份/重复 id），校验失败不写计划版——空计划与非法计划都不得冒充「已确认
        计划」（R21）；执行模式首问因此得到 ``PLAN_UNAVAILABLE`` 而非伪派发。
        """

        factory = self._plan_store_factory
        if factory is None:
            logger.info("[MainTurn] plan store not wired; readable plan saved without executable reference")
            return None
        if outcome.result is None:
            # 澄清终态无成果：不保存计划版（也不伪造空计划）。
            return None
        steps = plan_steps_from_structured_fields(outcome.result.structured_fields)
        if not steps:
            logger.info("[MainTurn] no plan steps produced; plan version not saved")
            return None
        if not _plan_steps_validated(steps, execution_id=request.identity.execution_id):
            return None
        store: PlanProposalStore = factory(request.identity)
        await store.set_items(_harness_plan_items(steps))
        return PlanReference(execution_id=request.identity.execution_id, plan_revision=store.revision)


def _plan_failure_cause(exc: Exception) -> str:
    """把计划生成失败转成稳定原因码（供 UI i18n；不拼接自由文本）。"""

    if isinstance(exc, AgentServiceError):
        return exc.code.value
    return type(exc).__name__


def _plan_steps_validated(steps: tuple[PlanItemSnapshot, ...], *, execution_id: str) -> bool:
    """模型计划提案的严格 wire 往返校验（复用 ``decode_plan_proposal``，失败不落库）。"""

    try:
        wire = normalize_plan_proposal(PlanProposal(execution_id=execution_id, plan_revision=1, items=steps))
        decode_plan_proposal(decode_json_doc(wire), execution_id=execution_id)
    except AgentValidationError as exc:
        logger.warning("[MainTurn] model plan proposal rejected by strict decode: %s", exc.code)
        return False
    return True


def _harness_plan_items(steps: tuple[PlanItemSnapshot, ...]) -> list[Any]:
    """步骤快照 → Harness ``PlanItem``（只做形状转换，不改内容/顺序）。"""

    from pydantic_ai_harness.planning import PlanItem, TaskStatus

    return [
        PlanItem(
            id=step.id,
            content=step.content,
            status=TaskStatus(step.status),
            active_form=step.active_form,
            parent_id=step.parent_id,
            depends_on=list(step.depends_on),
        )
        for step in steps
    ]


# ---------------------------------------------------------------------------
# 首问 / 开始计划 受理入口（组合根注入 facade）
# ---------------------------------------------------------------------------


class AgentIntakeFacade(Protocol):
    """本入口需要的 facade 子集（P01 ``AgentService`` 结构性满足）。"""

    async def change_session(self, command: SessionChangeCommand) -> CommandReceipt: ...

    async def accept(self, request: AcceptRequest, *, command: CommandRef) -> SubmissionReceipt: ...

    async def submit_and_dispatch(self, request: AcceptRequest, *, command: CommandRef) -> Any: ...

    async def save_clarification_decision(self, request: SaveClarificationDecisionRequest) -> ClarificationFact: ...

    async def consume_clarification(
        self, *, user_scope: str, session_id: str
    ) -> tuple[ClarificationAnswerFact, ...]: ...

    async def restore_clarification_consumption(
        self, *, user_scope: str, session_id: str, decision_ids: tuple[str, ...]
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class MainTurnIntakeResult:
    """一次受理结果：会话 + 受理回执 + 派发事实（``None`` = 未接线派发器）。"""

    session_id: str
    receipt: SubmissionReceipt
    dispatch: DispatchOutcome | None
    plan_reference: PlanReference | None = None
    #: 讨论/计划模式的主回合终态（执行模式由持久 workflow 承接，此处为 None）。
    main_turn: MainTurnOutcome | None = None
    #: 本次主回合产出的澄清决定身份（未澄清为 ``None``）。
    clarification_decision_id: str | None = None


class MainTurnIntake:
    """首问与「开始这份计划」的受理入口（创建会话 → 受理 → 派发）。

    讨论/计划请求同样经受理落库；真实派发仍由 ``ResearchDispatcher`` 按冻结模式
    判定（讨论/计划零派发）。「开始这份计划」把源计划执行冻结的输入完整带入新
    意图，只改 ``mode=execute`` 并追加计划引用，不扩大资料/权限/截止范围。

    **执行模式首问**：受理后先由主回合产出计划（同一 ``decode_plan_proposal``
    严格校验并保存计划版），再显式派发；模型不可用/提案非法/无角色策略时返回
    ``PLAN_UNAVAILABLE`` 结构化原因，零派发且不伪造已启动（R21）。
    """

    def __init__(
        self,
        facade: AgentIntakeFacade,
        *,
        manifest_reader: _ManifestReader,
        user_scope: str,
        source_module: str = "ui",
        source_display_name: str = "Agent",
        main_turn: MainTurnRunner | None = None,
        dispatcher: ResearchDispatcher | None = None,
    ) -> None:
        self._facade = facade
        self._manifest_reader = manifest_reader
        self._user_scope = user_scope
        self._source_module = source_module
        self._source_display_name = source_display_name
        self._main_turn = main_turn
        self._dispatcher = dispatcher

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
        mcp_selection: Sequence[MCPServiceIdentity] = (),
        revise_source_message_id: str | None = None,
        revise_causal_cutoff_message_id: str | None = None,
    ) -> MainTurnIntakeResult:
        """首问：必要时新建会话，冻结本次手选集合后受理。

        讨论/计划零研究派发；执行模式受理后先产出计划再派发。``role_prompt_refs``
        / ``skill_refs`` / ``mcp_selection`` 为本次显式手选集（未选择即空集，
        能力来源据此 fail-closed；不由本入口补默认角色/能力）。``revise_*`` 为
        可选修订声明：仅编辑并重发旧问题时声明，随新意图在同一受理事务内原子
        切换因果头（不覆盖原消息历史）；缺省表示普通新问题。
        """

        question = text.strip()
        if not question:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "question text is empty", self._user_scope)
        if mode not in (Mode.DISCUSS, Mode.PLAN, Mode.EXECUTE):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "unsupported mode", self._user_scope)
        target_session = session_id
        revision = 0 if expected_revision is None else expected_revision
        if target_session is None:
            target_session = f"session-{uuid.uuid4().hex}"
            revision = 0
            await self._facade.change_session(
                SessionChangeCommand(
                    command=CommandRef(
                        user_scope=self._user_scope,
                        source_module=self._source_module,
                        business_request_id=target_session,
                        command_id=f"ui-{uuid.uuid4().hex}",
                        expected_revision=0,
                    ),
                    action=SessionAction.CREATE,
                    session_id=target_session,
                    expected_session_revision=0,
                    title=self._title(question, session_title),
                )
            )
        manifest = self._base_manifest(
            text=question,
            mode=mode,
            purpose=Purpose.CONCEPT,
            role_prompt_refs=role_prompt_refs,
            skill_refs=skill_refs,
            mcp_selection=mcp_selection,
        )
        if mode is Mode.EXECUTE:
            return await self._start_execute_question(
                target_session,
                expected_revision=revision,
                manifest=manifest,
                question=question,
                revise_source_message_id=revise_source_message_id,
                revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
            )
        result = await self._submit(
            target_session,
            expected_revision=revision,
            manifest=manifest,
            revise_source_message_id=revise_source_message_id,
            revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
        )
        if self._main_turn is None:
            return result
        # 讨论/计划：受理后由生产主回合产出直答/可读计划；零研究动作由回合端口保证。
        identity = self._main_turn_identity(target_session, result.receipt, revision=revision, manifest=manifest)
        clarification_answers = await self._consume_clarification(target_session)
        try:
            turn_outcome = await self._main_turn.run(
                MainTurnRequest(
                    identity=identity,
                    mode=mode,
                    prompt_text=question,
                    clarification_answers=clarification_answers,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # 回合失败时答复未进入任何 prompt：补偿还原，避免静默丢失（原异常照抛）。
            await self._restore_clarification(target_session, clarification_answers)
            raise
        clarification_decision_id = await self._save_clarification(
            session_id=target_session,
            identity=identity,
            outcome=turn_outcome,
        )
        return replace(
            result,
            main_turn=turn_outcome,
            plan_reference=turn_outcome.plan_reference or result.plan_reference,
            clarification_decision_id=clarification_decision_id,
        )

    async def _start_execute_question(
        self,
        session_id: str,
        *,
        expected_revision: int,
        manifest: CanonicalJson,
        question: str,
        revise_source_message_id: str | None = None,
        revise_causal_cutoff_message_id: str | None = None,
    ) -> MainTurnIntakeResult:
        """执行模式首问：受理（不自动派发）→ 主回合产出计划 → 显式派发。

        任何一步不可得都返回结构化 ``PLAN_UNAVAILABLE``（cause 参数），零派发：
        未接主回合、模型失败、无步骤/非法步骤均不投递，不把空计划冒充已启动。
        ``CancelledError`` 原样传播（R2）。
        """

        result = await self._accept(
            session_id,
            expected_revision=expected_revision,
            manifest=manifest,
            revise_source_message_id=revise_source_message_id,
            revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
        )
        runner = self._main_turn
        if runner is None:
            return replace(result, dispatch=self._plan_unavailable(result, cause="main_turn_not_wired"))
        if derive_frozen_selection_policy(manifest) is None:
            # 未手选角色/Skills/MCP：无派发准入判据，先如实零派发（不耗模型、不猜授权）。
            return replace(result, dispatch=self._plan_unavailable(result, cause="role_policy_missing"))
        identity = self._main_turn_identity(session_id, result.receipt, revision=expected_revision, manifest=manifest)
        clarification_answers: tuple[ClarificationAnswerFact, ...] = ()
        try:
            clarification_answers = await self._consume_clarification(session_id)
            turn_outcome = await runner.run(
                MainTurnRequest(
                    identity=identity,
                    mode=Mode.PLAN,
                    prompt_text=question,
                    clarification_answers=clarification_answers,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 模型/计划端口失败：如实零派发，不伪造已启动
            await self._restore_clarification(session_id, clarification_answers)
            logger.warning(
                "[MainTurn] execute first-question plan generation failed, zero dispatch (execution=%s): %s",
                result.receipt.execution_id,
                DataSanitizer.sanitize_error(exc),
            )
            return replace(result, dispatch=self._plan_unavailable(result, cause=_plan_failure_cause(exc)))
        if getattr(turn_outcome, "clarification", None) is not None:
            # 澄清：用户答复前零派发；不把「等待澄清」判成计划失败。
            decision_id = await self._save_clarification(
                session_id=session_id,
                identity=identity,
                outcome=turn_outcome,
            )
            return replace(result, main_turn=turn_outcome, clarification_decision_id=decision_id)
        reference = turn_outcome.plan_reference
        if reference is None:
            return replace(result, dispatch=self._plan_unavailable(result, cause="plan_not_produced"))
        dispatcher = self._dispatcher
        if dispatcher is None:
            return replace(result, plan_reference=reference)
        outcome = await dispatcher.dispatch(result.receipt, user_scope=self._user_scope, mode=Mode.EXECUTE)
        return replace(result, dispatch=outcome, plan_reference=reference)

    def _plan_unavailable(self, result: MainTurnIntakeResult, *, cause: str) -> DispatchOutcome:
        """执行模式首问的零派发事实（原因码 + cause 参数，供 UI 按码 i18n）。"""

        return zero_dispatch(
            result.receipt.execution_id,
            result.receipt.workflow_id,
            PLAN_UNAVAILABLE,
            params=(("cause", cause),),
        )

    async def _consume_clarification(self, session_id: str) -> tuple[ClarificationAnswerFact, ...]:
        """取走本会话未消费的澄清问答（只注入一次；读取失败不阻断新问题）。

        未接线消费端口或无已答项即空集；读取失败只记录脱敏原因并照常受理新
        问题（不伪造「已澄清」，也不因此拒绝新问题）。
        """

        consume = getattr(self._facade, "consume_clarification", None)
        if not callable(consume):
            return ()
        typed_consume = cast(Callable[..., Any], consume)
        try:
            return tuple(await typed_consume(user_scope=self._user_scope, session_id=session_id))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MainTurn] clarification answers unavailable; continuing without them (session=%s): %s",
                session_id,
                DataSanitizer.sanitize_error(exc),
            )
            return ()

    async def _restore_clarification(self, session_id: str, facts: tuple[ClarificationAnswerFact, ...]) -> None:
        """补偿还原本轮已消费的澄清答复（仅非空时；失败只记录脱敏原因）。

        还原失败不掩盖原始异常（调用方仍按原异常/零派发处理），也不伪造
        「已还原」；``CancelledError`` 依 R2 原样上抛。
        """

        if not facts:
            return
        restore = getattr(self._facade, "restore_clarification_consumption", None)
        if not callable(restore):
            logger.warning(
                "[MainTurn] clarification restore port not wired; answers stay consumed (session=%s)", session_id
            )
            return
        typed_restore = cast(Callable[..., Any], restore)
        try:
            await typed_restore(
                user_scope=self._user_scope,
                session_id=session_id,
                decision_ids=tuple(fact.decision_id for fact in facts),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[MainTurn] clarification restore failed; answers stay consumed (session=%s): %s",
                session_id,
                DataSanitizer.sanitize_error(exc),
            )

    async def _save_clarification(
        self, *, session_id: str, identity: LoopIdentity, outcome: MainTurnOutcome
    ) -> str | None:
        """把澄清提案落成待答复决定；返回决定身份（未澄清/未接线为 ``None``）。"""

        decision = getattr(outcome, "clarification", None)
        if decision is None:
            return None
        saver = getattr(self._facade, "save_clarification_decision", None)
        if not callable(saver):
            logger.warning("[MainTurn] clarification decision port not wired; decision not persisted")
            return None
        question_text = decision.text
        if not isinstance(question_text, str) or not question_text:
            return None
        payload = decode_json_doc(decision.structured_fields or "{}")
        section = payload if isinstance(payload, dict) else {}
        raw_options = section.get(CLARIFICATION_OPTIONS_KEY)
        options = (
            tuple(item for item in raw_options if isinstance(item, str) and item.strip())
            if isinstance(raw_options, list)
            else ()
        )
        raw_impact = section.get(CLARIFICATION_IMPACT_KEY)
        impact = raw_impact if isinstance(raw_impact, str) else ""
        typed_saver = cast(Callable[..., Any], saver)
        fact = await typed_saver(
            SaveClarificationDecisionRequest(
                user_scope=self._user_scope,
                session_id=session_id,
                execution_id=identity.execution_id,
                node_id=identity.node_id,
                question_text=question_text,
                options=options,
                impact=impact,
                expires_at=_clarification_expiry(),
            )
        )
        return fact.decision_id

    def _main_turn_identity(
        self,
        session_id: str,
        receipt: SubmissionReceipt,
        *,
        revision: int,
        manifest: CanonicalJson,
    ) -> LoopIdentity:
        """由受理事实派生回合身份（digest 缺失时取冻结 manifest 摘要，不造假）。"""

        digest = receipt.intent_digest
        if not is_digest(digest):
            digest = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
        return LoopIdentity(
            user_scope=self._user_scope,
            session_id=session_id,
            execution_id=receipt.execution_id,
            round_id=derive_round_id(
                user_scope=self._user_scope,
                execution_id=receipt.execution_id,
                workflow_id=receipt.workflow_id,
            ),
            node_id="root",
            input_revision=revision + 1,
            actual_input_digest=digest,
            actual_model_ref=MAIN_TURN_MODEL_REF,
        )

    async def start_plan(
        self, reference: PlanReference, *, session_id: str, expected_revision: int
    ) -> MainTurnIntakeResult:
        """开始已审计划：复制源请求冻结输入，只改执行模式并追加计划引用。"""

        if not isinstance(reference, PlanReference):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "invalid plan reference", self._user_scope)
        source_manifest = await self._manifest_reader.read_authoritative_manifest(
            user_scope=self._user_scope,
            execution_id=reference.execution_id,
        )
        if source_manifest is None:
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "confirmed plan input unavailable",
                f"{self._user_scope}/{reference.execution_id}",
            )
        manifest = self._manifest_object(source_manifest)
        text = _frozen_section(manifest).get("text")
        if not isinstance(text, str) or not text.strip():
            raise AgentServiceError(
                AgentErrorCode.OPERATION_UNKNOWN,
                "confirmed plan request text unavailable",
                f"{self._user_scope}/{reference.execution_id}",
            )
        started = dict(manifest)
        section: dict[str, Any]
        shared = started.get("shared")
        if shared is not None:
            if not isinstance(shared, dict):
                raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "frozen shared section invalid", self._user_scope)
            section = dict(shared)
            started["shared"] = section
        else:
            section = started
        section["mode"] = Mode.EXECUTE.value
        section[PLAN_REFERENCE_KEY] = {
            "execution_id": reference.execution_id,
            "plan_revision": reference.plan_revision,
        }
        payload = json_doc(started)
        return await self._submit(
            session_id,
            expected_revision=expected_revision,
            manifest=payload,
            mode=Mode.EXECUTE,
            text=text,
            purpose=self._purpose_from(manifest),
            plan_reference=reference,
        )

    async def _submit(
        self,
        session_id: str,
        *,
        expected_revision: int,
        manifest: CanonicalJson,
        mode: Mode | None = None,
        text: str | None = None,
        purpose: Purpose | None = None,
        plan_reference: PlanReference | None = None,
        revise_source_message_id: str | None = None,
        revise_causal_cutoff_message_id: str | None = None,
    ) -> MainTurnIntakeResult:
        request, command = self._build_request(
            session_id,
            expected_revision=expected_revision,
            manifest=manifest,
            mode=mode,
            text=text,
            purpose=purpose,
            revise_source_message_id=revise_source_message_id,
            revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
        )
        outcome = await self._facade.submit_and_dispatch(request, command=command)
        receipt = getattr(outcome, "receipt", None)
        if not isinstance(receipt, SubmissionReceipt):
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "submission receipt unavailable",
                self._user_scope,
            )
        dispatch = getattr(outcome, "dispatch", None)
        return MainTurnIntakeResult(
            session_id=session_id,
            receipt=receipt,
            dispatch=dispatch if isinstance(dispatch, DispatchOutcome) else None,
            plan_reference=plan_reference,
        )

    async def _accept(
        self,
        session_id: str,
        *,
        expected_revision: int,
        manifest: CanonicalJson,
        revise_source_message_id: str | None = None,
        revise_causal_cutoff_message_id: str | None = None,
    ) -> MainTurnIntakeResult:
        """受理落定但不自动派发（执行模式首问先产出计划，再由本入口显式派发）。"""

        request, command = self._build_request(
            session_id,
            expected_revision=expected_revision,
            manifest=manifest,
            revise_source_message_id=revise_source_message_id,
            revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
        )
        receipt = await self._facade.accept(request, command=command)
        if not isinstance(receipt, SubmissionReceipt):
            raise AgentServiceError(
                AgentErrorCode.PERSISTENCE_UNAVAILABLE,
                "submission receipt unavailable",
                self._user_scope,
            )
        return MainTurnIntakeResult(session_id=session_id, receipt=receipt, dispatch=None)

    def _build_request(
        self,
        session_id: str,
        *,
        expected_revision: int,
        manifest: CanonicalJson,
        mode: Mode | None = None,
        text: str | None = None,
        purpose: Purpose | None = None,
        revise_source_message_id: str | None = None,
        revise_causal_cutoff_message_id: str | None = None,
    ) -> tuple[AcceptRequest, CommandRef]:
        """由冻结 manifest 构造受理请求与命令引用（幂等键逐请求唯一）。

        ``revise_*`` 非空时以修订声明受理：因果头在同一受理事务内切到源消息
        之前的前缀，原消息不被覆盖；缺省为普通新意图。
        """

        decoded = self._manifest_object(manifest)
        frozen_mode = mode or self._mode_from(decoded)
        frozen_purpose = purpose or self._purpose_from(decoded)
        body = text if text is not None else decoded.get("text")
        if not isinstance(body, str) or not body.strip():
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "request text unavailable", self._user_scope)
        intent = uuid.uuid4().hex
        request = AcceptRequest(
            user_scope=self._user_scope,
            session_id=session_id,
            source_module=self._source_module,
            business_request_id=f"intent-{intent}",
            client_command_id=f"ui-{intent}",
            expected_revision=expected_revision,
            node_id=f"node-{intent}",
            workflow_id=f"workflow-{intent}",
            mode=frozen_mode,
            purpose=frozen_purpose,
            text=body,
            manifest_payload=manifest,
            source_display_name=self._source_display_name,
            revise_source_message_id=revise_source_message_id,
            revise_causal_cutoff_message_id=revise_causal_cutoff_message_id,
        )
        command = CommandRef(
            user_scope=self._user_scope,
            source_module=self._source_module,
            business_request_id=request.business_request_id,
            command_id=request.client_command_id,
            expected_revision=request.expected_revision,
        )
        return request, command

    def _base_manifest(
        self,
        *,
        text: str,
        mode: Mode,
        purpose: Purpose,
        role_prompt_refs: Sequence[tuple[str, str]] = (),
        skill_refs: Sequence[tuple[str, str]] = (),
        mcp_selection: Sequence[MCPServiceIdentity] = (),
    ) -> CanonicalJson:
        """首问冻结 manifest（仅写入调用方显式手选集合；能力来源据此 fail-closed）。

        首问用途固定为概念用途：研究用途/数据窗口在草稿/业务对象入口按 F20 冻结，
        本入口不替用户决定研究截止日。执行模式额外冻结 ``plan_source=auto``：首问
        计划由主回合产出并经严格校验保存，派发侧只读取该已保存计划版。
        ``mcp_selection`` 为空表示未选择（不解释为全部允许，§6 默认拒绝）；非空时
        按服务身份排序冻结并写入规范摘要 ``mcp_scope_digest``，供能力策略派生
        ``mcp_scope:<digest>``。
        """

        selected = {identity.stable_key(): identity for identity in mcp_selection}
        ordered = tuple(selected[key] for key in sorted(selected))
        digest: str | None = None
        if ordered:
            canonical = json.dumps(
                [encode_mcp_service_identity(identity) for identity in ordered],
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            digest = sha256_hex(canonical.encode("utf-8"))
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "text": text,
            "mode": mode.value,
            "purpose": purpose.value,
            "cutoff": None,
            "source_refs": [],
            "visible_history_message_ids": [],
            "visible_history_artifact_refs": [],
            "role_prompt_refs": [[role_id, revision] for role_id, revision in role_prompt_refs],
            "skill_refs": [[skill_id, revision] for skill_id, revision in skill_refs],
            "mcp_selection": [encode_mcp_service_identity(identity) for identity in ordered],
            "mcp_scope_digest": digest,
            "output_requirements": {},
        }
        if mode is Mode.EXECUTE:
            manifest[PLAN_SOURCE_KEY] = PLAN_SOURCE_AUTO
        return json_doc(manifest)

    def _manifest_object(self, payload: str) -> dict[str, Any]:
        return _manifest_object(payload)

    @staticmethod
    def _mode_from(manifest: Mapping[str, Any]) -> Mode:
        raw = _frozen_section(manifest).get("mode")
        if not isinstance(raw, str):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "frozen manifest mode unavailable", "")
        try:
            return Mode(raw)
        except ValueError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "frozen manifest mode invalid", "") from exc

    @staticmethod
    def _purpose_from(manifest: Mapping[str, Any]) -> Purpose:
        raw = _frozen_section(manifest).get("purpose")
        if not isinstance(raw, str):
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "frozen manifest purpose unavailable", "")
        try:
            return Purpose(raw)
        except ValueError as exc:
            raise AgentServiceError(AgentErrorCode.INPUT_INVALID, "frozen manifest purpose invalid", "") from exc

    @staticmethod
    def _title(text: str, title: str | None) -> str:
        """会话标题：用户给定优先；否则用脱敏后的首问摘要（不调模型、不含附件正文）。"""

        if isinstance(title, str) and title.strip():
            return title.strip()
        compact = " ".join(text.split())
        summary = DataSanitizer.sanitize_dict({"title": compact[:40]})["title"]
        return summary or "新研究"
