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

import logging
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from core.agent_types import (
    AcceptRequest,
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    CanonicalJson,
    CommandReceipt,
    CommandRef,
    Mode,
    PlanItemSnapshot,
    PlanNodeSpec,
    PlanProposal,
    PlanSpecProposal,
    Purpose,
    SessionAction,
    SessionChangeCommand,
    SourceVersionRef,
    SubmissionReceipt,
    decode_json_doc,
    decode_source_version_ref,
    json_doc,
)
from services.agent_service.capabilities import PlanProposalStore
from services.agent_service.dispatch import DispatchOutcome, PlanSpecProvider, RoleCapabilityProvider
from services.agent_service.loop import (
    DEFAULT_PLAN_TOOLS,
    AgentTurn,
    DelegationRunner,
    ExecutionLoop,
    LoopIdentity,
    LoopLimits,
    LoopRunOutcome,
    ResearchResultProducer,
    ToolExecutor,
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

#: 结构化计划步骤在回合 ``structured_fields`` 中的顶层键。
PLAN_STEPS_KEY: Final[str] = "steps"
#: 计划引用在冻结 manifest 中的顶层键（「开始这份计划」新意图据此定位已审版本）。
PLAN_REFERENCE_KEY: Final[str] = "plan_ref"

_PLAN_STEP_KEYS: Final[frozenset[str]] = frozenset({"id", "content", "depends_on"})
_PLAN_REFERENCE_KEYS: Final[frozenset[str]] = frozenset({"execution_id", "plan_revision"})


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


def build_plan_spec_provider(dao: _ManifestReader, proposals: _PlanProposalReader | None = None) -> PlanSpecProvider:
    """生产计划来源：冻结 manifest 的计划引用 → 已确认计划版 → 可执行节点表。

    不可得一律返回 ``None``（派发侧据此 ``PLAN_UNAVAILABLE`` 零派发）。
    """

    reader: _PlanProposalReader = proposals if proposals is not None else cast(_PlanProposalReader, dao)

    async def _provide(user_scope: str, execution_id: str) -> PlanSpecProposal | None:
        manifest = await dao.read_authoritative_manifest(user_scope=user_scope, execution_id=execution_id)
        if manifest is None:
            return None
        reference = decode_plan_reference(manifest)
        if reference is None:
            return None
        proposal = await reader.read_plan_proposal(
            user_scope=user_scope,
            execution_id=reference.execution_id,
            plan_revision=reference.plan_revision,
        )
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
    """一次主回合的冻结身份与模式（模式只由用户选择，模型不得切换）。"""

    identity: LoopIdentity
    mode: Mode
    read_tool_names: frozenset[str] = frozenset()
    plan_tool_names: frozenset[str] = DEFAULT_PLAN_TOOLS

    def __post_init__(self) -> None:
        if not isinstance(self.identity, LoopIdentity):
            raise ValueError("main turn identity must be a LoopIdentity")
        if not isinstance(self.mode, Mode):
            raise ValueError("main turn mode must be a Mode")
        for name in ("read_tool_names", "plan_tool_names"):
            value = getattr(self, name)
            if not isinstance(value, frozenset) or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"main turn {name} must be a frozenset of non-empty text")


@dataclass(frozen=True, slots=True)
class MainTurnOutcome:
    """主回合结果：循环终态 + 计划模式保存的计划引用（未保存为 ``None``）。"""

    outcome: LoopRunOutcome
    plan_reference: PlanReference | None = None


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
    ) -> None:
        self._producer = producer
        self._tools = tools
        self._delegate = delegate
        self._limits = limits
        self._plan_store_factory = plan_store_factory

    async def run(self, request: MainTurnRequest, *, turn: AgentTurn) -> MainTurnOutcome:
        """推进一次主回合；计划成功交付时保存用户可读计划版并返回其引用。"""

        mode = request.mode
        loop = ExecutionLoop(
            identity=request.identity,
            mode=mode,
            turn=turn,
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
        if mode is not Mode.PLAN:
            return MainTurnOutcome(outcome=outcome)
        return MainTurnOutcome(outcome=outcome, plan_reference=await self._save_plan(request, outcome))

    async def _save_plan(self, request: MainTurnRequest, outcome: LoopRunOutcome) -> PlanReference | None:
        """把计划回合的结构化步骤保存为可读计划版；未给出步骤即不保存。"""

        factory = self._plan_store_factory
        if factory is None:
            logger.info("[MainTurn] plan store not wired; readable plan saved without executable reference")
            return None
        steps = plan_steps_from_structured_fields(outcome.result.structured_fields)
        if steps is None:
            return None
        store: PlanProposalStore = factory(request.identity)
        await store.set_items(_harness_plan_items(steps))
        return PlanReference(execution_id=request.identity.execution_id, plan_revision=store.revision)


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

    async def submit_and_dispatch(self, request: AcceptRequest, *, command: CommandRef) -> Any: ...


@dataclass(frozen=True, slots=True)
class MainTurnIntakeResult:
    """一次受理结果：会话 + 受理回执 + 派发事实（``None`` = 未接线派发器）。"""

    session_id: str
    receipt: SubmissionReceipt
    dispatch: DispatchOutcome | None
    plan_reference: PlanReference | None = None


class MainTurnIntake:
    """首问与「开始这份计划」的受理入口（创建会话 → 受理 → 派发）。

    讨论/计划请求同样经受理落库；真实派发仍由 ``ResearchDispatcher`` 按冻结模式
    判定（讨论/计划零派发）。「开始这份计划」把源计划执行冻结的输入完整带入新
    意图，只改 ``mode=execute`` 并追加计划引用，不扩大资料/权限/截止范围。
    """

    def __init__(
        self,
        facade: AgentIntakeFacade,
        *,
        manifest_reader: _ManifestReader,
        user_scope: str,
        source_module: str = "ui",
        source_display_name: str = "Agent",
    ) -> None:
        self._facade = facade
        self._manifest_reader = manifest_reader
        self._user_scope = user_scope
        self._source_module = source_module
        self._source_display_name = source_display_name

    async def start_question(
        self,
        text: str,
        *,
        mode: Mode,
        session_id: str | None = None,
        session_title: str | None = None,
        expected_revision: int | None = None,
    ) -> MainTurnIntakeResult:
        """首问：必要时新建会话，冻结本次空手选集合后受理（讨论/计划零派发）。"""

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
        manifest = self._base_manifest(text=question, mode=mode, purpose=Purpose.CONCEPT)
        return await self._submit(target_session, expected_revision=revision, manifest=manifest)

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
    ) -> MainTurnIntakeResult:
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
        )
        command = CommandRef(
            user_scope=self._user_scope,
            source_module=self._source_module,
            business_request_id=request.business_request_id,
            command_id=request.client_command_id,
            expected_revision=request.expected_revision,
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

    def _base_manifest(self, *, text: str, mode: Mode, purpose: Purpose) -> CanonicalJson:
        """首问冻结 manifest（空手选集合；能力来源据此 fail-closed）。

        首问用途固定为概念用途：研究用途/数据窗口在草稿/业务对象入口按 F20 冻结，
        本入口不替用户决定研究截止日。执行据此零派发（无手选/无计划引用）。
        """

        return json_doc(
            {
                "schema_version": 1,
                "text": text,
                "mode": mode.value,
                "purpose": purpose.value,
                "cutoff": None,
                "source_refs": [],
                "visible_history_message_ids": [],
                "visible_history_artifact_refs": [],
                "role_prompt_refs": [],
                "skill_refs": [],
                "mcp_scope_digest": None,
                "output_requirements": {},
            }
        )

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
