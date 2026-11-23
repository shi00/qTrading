"""叶运行链装配（R2-I01）：叶 runner 安装 + GatewayModel 工厂注入。

本模块只做**组装期接线**：

- 叶 runner 登记到 :class:`~services.agent_service.workflows.LeafRuntimeRegistry`
  （未安装时 ``require`` 明确拒绝，不以占位执行器冒充真实叶执行）；
- 模型桥经 :func:`build_gateway_model` 构造：``call_identity`` 固定取叶 workflow
  稳定身份（:func:`~services.agent_service.workflows.leaf_workflow_id`），回合调用号
  再由 :func:`~services.agent_service.model.derive_model_call_id` 确定性派生
  （R2-I41：恢复重放复得同一 ``model_call_id``，不新造随机身份）；
- 模型绑定解析 / 叶 Agent 执行以可注入端口表达：生产默认解析 ``agent_research``
  场景路由（未配置路由即显式失败、不静默换模型），叶 Agent 主体由
  :mod:`services.agent_service.leaf_agent` 提供（真实 pydantic-ai ``Agent`` 单次
  运行 + 成果可靠保存 + 边界能力消费）；
  ``unavailable_leaf_agent`` 仅作显式「未接线、零外发」替身保留。

不在本模块：DAG 准入校验（派发前 ``validate_plan_spec_proposal``）、叶成果落库、
主会话/UI 研究界面。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Final, Protocol, cast

from core.model_management_types import ExecutionSnapshot
from pydantic_ai.profiles import ModelProfile

from services.agent_service.comparison import (
    ComparisonLaneParameters,
    FrozenComparisonGroup,
    decode_comparison_manifest,
    require_lane_parameters,
)
from services.agent_service.model import ChatTurnExecutor, GatewayModel, derive_model_call_id
from services.agent_service.tool_runtime import ToolRuntimeFactory
from services.agent_service.workflows import (
    ActualInputBinder,
    ActualInputBinderRegistry,
    ActualInputBinding,
    LeafExecutionContext,
    LeafIdentity,
    LeafRunner,
    LeafRuntimeRegistry,
    LeafTicketGate,
    LeafTicketGateRegistry,
    LeafTicketLease,
    NodeOutcome,
    leaf_ticket_slot_id,
    leaf_workflow_id,
)

logger = logging.getLogger(__name__)

#: 研究叶所属模型场景（模型配置路由键；未启用/无候选即 fail-closed 零模型外发）。
AGENT_RESEARCH_SCENARIO_ID: Final[str] = "agent_research"

#: 叶执行错误码：装配默认叶 Agent 尚未接线（不伪造成功/空成果）。
LEAF_AGENT_UNAVAILABLE: Final[str] = "leaf_agent_unavailable"

# NOTE(lazy): 工具 schema 版本以装配常量承载. ceiling: 当前工具 schema 只有 v1，无独立版本来源. upgrade: 工具 schema 版本化落地（MCP/Skill schema 自带版本）时改从来源读取.
AGENT_TOOL_SCHEMA_VERSION: Final[str] = "v1"


@dataclass(frozen=True, slots=True)
class LeafModelBinding:
    """一次叶执行的冻结模型绑定（执行器 + 派发时点快照 + 受理绑定 + 绝对期限）。

    ``deadline`` 为按单调时钟记的**绝对期限**（秒），取自派发侧冻结路由的
    ``total_timeout_seconds``（``now + total``）：同一次叶执行的每个模型回合都
    由它派生剩余预算，贯穿适配器期限、消费背压与收尾，不另造默认值（R21）。
    """

    executor: ChatTurnExecutor
    snapshot: ExecutionSnapshot
    binding: Mapping[str, Any]
    deadline: float
    tool_schema_version: str = AGENT_TOOL_SCHEMA_VERSION
    profile: ModelProfile | None = None


class LeafDeadlineUnavailableError(RuntimeError):
    """绑定未携带可核验的绝对期限（缺失 / 非有限数 / 非正）——叶零外发（R21）。"""


class LeafDeadlineExhaustedError(RuntimeError):
    """冻结绝对期限已耗尽——叶不再发起任何模型外发。"""


def require_leaf_turn_timeout(binding: LeafModelBinding) -> float:
    """本回合可用剩余秒数（同一冻结绝对期限派生，不做默认回退）。

    桥层要求 ``ModelSettings['timeout']`` 为正数（缺失即协议失败），因此叶必须
    交出真实剩余预算；绑定期限不可核验与已耗尽分别以稳定异常表达，调用方据此
    如实失败（R21：不以臆造默认预算继续外发）。

    Raises:
        LeafDeadlineUnavailableError: 期限缺失 / 非有限数 / 非正（装配事实不成立）。
        LeafDeadlineExhaustedError: 期限已耗尽。
    """

    deadline = binding.deadline
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, int | float)
        or not math.isfinite(float(deadline))
        or float(deadline) <= 0
    ):
        raise LeafDeadlineUnavailableError("leaf model binding has no verified absolute deadline")
    remaining = float(deadline) - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise LeafDeadlineExhaustedError("leaf absolute deadline exhausted (no further outbound)")
    return remaining


#: 叶身份 → 冻结模型绑定（生产实现按场景路由解析；不可用即抛错，零模型外发）。
ModelBindingResolver = Callable[[LeafExecutionContext], Awaitable[LeafModelBinding]]

#: 叶 Agent 主体：给定模型桥、叶上下文与冻结绑定产出叶终态事实（成果落库在其中）。
LeafAgentRunner = Callable[[GatewayModel, LeafExecutionContext, LeafModelBinding], Awaitable[NodeOutcome]]

#: 叶资料许可提供者（I43）：返回该叶的 15 键冻结资料上下文；``None`` = 无决定。
DataConsentProvider = Callable[[LeafExecutionContext], Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class FrozenLaneBinding:
    """叶所属比较路的冻结绑定（运行期只许用该连接，禁按当前设置重解析）。"""

    group_ref: str
    lane_ref: str
    parameters: ComparisonLaneParameters


#: 比较路冻结参数提供者：非比较执行返回 ``None``；读不到即抛错（零外发）。
LaneFreezeProvider = Callable[[LeafExecutionContext], Awaitable[FrozenLaneBinding | None]]


@dataclass(frozen=True, slots=True)
class LeafRuntimeWiring:
    """叶运行链装配参数（组合根可注入替身；缺省走生产默认）。"""

    model_binding: ModelBindingResolver
    agent_runner: LeafAgentRunner
    #: I43：资料许可逐 attempt 核权的决策来源（缺省不接线 = 无许可即拒绝）。
    data_consent_provider: DataConsentProvider | None = None
    #: 工具 runtime 工厂（缺省 ``None`` = 零工具；MCP 管理器未装配前不伪造已接线）。
    tool_runtime_factory: ToolRuntimeFactory | None = None
    #: 父实际输入绑定器（缺省不接线 = 父零叶派发；建立真实应用轮次）。
    actual_input_binder: ActualInputBinder | None = None
    #: 叶资源票据门（缺省不接线 = 父零叶派发；树级额度闭环）。
    leaf_ticket_gate: LeafTicketGate | None = None
    #: 共享 DAO（逐回合模型/工具额度计数用；缺省不计数——生产必须提供）。
    dao: Any | None = None


#: 逐回合出口额度计数的 DAO 最小面（生产为 ``AgentDao``）。
class _UsageDao(Protocol):
    async def admit_usage(self, request: Any) -> Any: ...

    async def read_leaf_dispatch_facts(self, execution_id: str, round_id: str) -> Any: ...


class AdmissionCountingExecutor:
    """模型回合执行器包装：每次真实调用前先经 ``admit_usage`` 计数（R2-I33）。

    记账失败（``BUDGET_BLOCKED`` 等）原样传播，被拒回合不发起模型外发；用户域
    在首次调用时从权威叶派发事实读取并缓存（不信任载荷自报）。
    """

    def __init__(self, executor: ChatTurnExecutor, *, dao: _UsageDao, execution_id: str, round_id: str) -> None:
        self._executor = executor
        self._dao = dao
        self._execution_id = execution_id
        self._round_id = round_id
        self._user_scope: str | None = None

    async def _require_user_scope(self) -> str:
        if self._user_scope is None:
            facts = await self._dao.read_leaf_dispatch_facts(self._execution_id, self._round_id)
            self._user_scope = str(facts.user_scope)
        return self._user_scope

    async def execute_chat_turn(
        self, request: Any, snapshot: Any, *, binding: Mapping[str, Any], on_event: Any = None
    ) -> Any:
        from core.agent_types import UsageAdmissionRequest

        user_scope = await self._require_user_scope()
        await self._dao.admit_usage(
            UsageAdmissionRequest(user_scope=user_scope, execution_id=self._execution_id, model_attempts=1)
        )
        return await self._executor.execute_chat_turn(request, snapshot, binding=binding, on_event=on_event)


class AdmissionCountingToolExecutor:
    """工具执行包装：每次真实调用前先经 ``admit_usage`` 计数（R2-I33）。

    计数发生在决定/派发之前；被拒调用不产生任何工具外发。
    """

    def __init__(self, executor: Any, *, dao: _UsageDao, user_scope: str, execution_id: str) -> None:
        self._executor = executor
        self._dao = dao
        self._user_scope = user_scope
        self._execution_id = execution_id

    async def execute(self, *, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        from core.agent_types import UsageAdmissionRequest

        await self._dao.admit_usage(
            UsageAdmissionRequest(user_scope=self._user_scope, execution_id=self._execution_id, tool_calls=1)
        )
        return await self._executor.execute(tool_name=tool_name, arguments=arguments, tool_call_id=tool_call_id)


def _require_bind_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"actual input binding request field '{name}' must be non-empty text")
    return value


def build_actual_input_binder(dao: Any) -> ActualInputBinder:
    """DAO 适配的父实际输入绑定器：建立应用轮次并冻结每叶身份与输入版。

    ``round_id`` 必须等于按 ``user_scope+execution+workflow`` 派生的轮次身份；
    回执输入版必须等于派发侧冻结的期望版（CAS 由 DAO 行锁保证）。任一不符即
    抛错 → 父零叶派发（不伪造轮次）。
    """

    async def _binder(request: dict[str, Any]) -> dict[str, Any]:
        from core.agent_types import BindActualInputRequest, derive_round_id

        user_scope = _require_bind_text(request.get("user_scope"), "user_scope")
        session_id = _require_bind_text(request.get("session_id"), "session_id")
        execution_id = _require_bind_text(request.get("execution_id"), "execution_id")
        round_id = _require_bind_text(request.get("round_id"), "round_id")
        plan_digest = _require_bind_text(request.get("plan_digest"), "plan_digest")
        workflow_id = _require_bind_text(request.get("workflow_id"), "workflow_id")
        workflow_code_version = _require_bind_text(request.get("workflow_code_version"), "workflow_code_version")
        run_epoch = request.get("run_epoch")
        expected_input_revision = request.get("expected_input_revision")
        if not isinstance(run_epoch, int) or isinstance(run_epoch, bool) or run_epoch < 1:
            raise ValueError("actual input binding request 'run_epoch' must be a positive int")
        if (
            not isinstance(expected_input_revision, int)
            or isinstance(expected_input_revision, bool)
            or expected_input_revision < 1
        ):
            raise ValueError("actual input binding request 'expected_input_revision' must be a positive int")
        derived = derive_round_id(user_scope=user_scope, execution_id=execution_id, workflow_id=workflow_id)
        if derived != round_id:
            raise ValueError("actual input binding round identity does not match workflow identity")
        raw_digests = request.get("input_digests")
        if not isinstance(raw_digests, Mapping) or not raw_digests:
            raise ValueError("actual input binding request 'input_digests' must be a non-empty map")
        input_digests = {str(node_id): str(digest) for node_id, digest in raw_digests.items()}
        receipt = await dao.bind_actual_input(
            BindActualInputRequest(
                user_scope=user_scope,
                session_id=session_id,
                execution_id=execution_id,
                node_id=min(input_digests),
                workflow_id=workflow_id,
                workflow_code_version=workflow_code_version,
                run_epoch=run_epoch,
                expected_input_revision=expected_input_revision,
                frozen_manifest=_require_bind_text(request.get("frozen_manifest"), "frozen_manifest"),
                actual_input=_require_bind_text(request.get("actual_input"), "actual_input"),
            )
        )
        if receipt.round_id != round_id:
            raise ValueError("bound round id does not match dispatched round")
        if receipt.input_revision != expected_input_revision:
            raise ValueError("bound input revision does not match dispatched revision")
        facts = await dao.read_leaf_dispatch_facts(execution_id, round_id)
        if int(facts.input_revision) != int(receipt.input_revision):
            raise ValueError("leaf dispatch facts input revision does not match binding")
        return ActualInputBinding(
            execution_id=execution_id,
            round_id=round_id,
            plan_digest=plan_digest,
            input_revision=int(receipt.input_revision),
            owner_epoch=int(facts.owner_epoch),
            leaves=tuple(
                LeafIdentity(
                    node_id=node_id,
                    leaf_id=leaf_workflow_id(execution_id=execution_id, round_id=round_id, node_id=node_id),
                    input_digest=digest,
                )
                for node_id, digest in sorted(input_digests.items())
            ),
        ).to_wire()

    return _binder


class DaoLeafTicketGate:
    """``AgentDao.claim_resource``/``release_resource`` 的票据门适配。"""

    def __init__(self, dao: Any) -> None:
        self._dao = dao

    async def claim(self, *, execution_id: str, round_id: str, node_id: str) -> LeafTicketLease | None:
        from core.agent_types import ResourceClaimRequest, ResourceSlotKind

        facts = await self._dao.read_leaf_dispatch_facts(execution_id, round_id)
        ticket = await self._dao.claim_resource(
            ResourceClaimRequest(
                user_scope=facts.user_scope,
                slot_id=leaf_ticket_slot_id(execution_id, round_id, node_id),
                slot_kind=ResourceSlotKind.LEAF,
                execution_id=execution_id,
                node_id=node_id,
                round_id=round_id,
                owner_epoch=int(facts.owner_epoch),
                operation_refs=None,
            )
        )
        return LeafTicketLease(
            slot_id=ticket.slot_id,
            user_scope=facts.user_scope,
            execution_id=execution_id,
            node_id=node_id,
            owner_epoch=int(ticket.owner_epoch),
        )

    async def release(self, lease: LeafTicketLease, *, exit_verified: bool) -> None:
        from core.agent_types import ResourceReleaseRequest

        await self._dao.release_resource(
            ResourceReleaseRequest(
                user_scope=lease.user_scope,
                slot_id=lease.slot_id,
                execution_id=lease.execution_id,
                node_id=lease.node_id,
                owner_epoch=lease.owner_epoch,
                exit_verified=exit_verified,
            )
        )


def build_leaf_ticket_gate(dao: Any) -> LeafTicketGate:
    """DAO 适配的叶票据门（树级额度闭环）。"""

    return DaoLeafTicketGate(dao)


async def resolve_leaf_data_consent(
    context: LeafExecutionContext, *, dao: Any | None = None
) -> Mapping[str, Any] | None:
    """生产默认资料许可提供者（I43）：按 execution/round/node 读权威决定。

    无决定行 → ``None``（装配侧写入 ``require_data_consent``，执行器零外发拒绝）；
    决定行不可读 / 作用域不一致 / 摘要不自洽 → 抛错（装配期 fail-closed，零外发）。
    返回值恰为执行器 ``data_consent`` 契约的 15 键（不含 ``attempt_id``）；
    ``operation_id`` 为叶首回合派生身份占位——真实 operation 身份在执行器逐次
    取 ``model_call_id`` 覆盖（一模型调用一 operation）。
    """

    agent_dao = dao
    if agent_dao is None:
        # lazy-import：CacheManager 依赖全量 DAO 注册链，仅资料许可路径需要。
        from data.cache.cache_manager import CacheManager

        agent_dao = getattr(CacheManager(), "agent_dao", None)
    if agent_dao is None:
        raise RuntimeError("agent dao unavailable (zero model dispatch)")

    from core.agent_types import data_consent_source_scope_digest
    from services.ai_service.scenario_executor import DATA_CONSENT_BINDING_KEYS

    facts = await agent_dao.read_leaf_dispatch_facts(context.execution_id, context.round_id)
    authority = await agent_dao.read_authoritative_data_consent(
        execution_id=context.execution_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )
    if authority is None:
        return None
    binding = authority.binding
    if (
        binding.user_scope != facts.user_scope
        or binding.execution_id != context.execution_id
        or binding.round_id != context.round_id
    ):
        raise ValueError("data consent binding scope does not match leaf execution")
    if data_consent_source_scope_digest(binding.source_selections) != binding.source_scope_digest:
        raise ValueError("data consent source scope digest does not match source selections")
    payload: dict[str, Any] = {
        "user_scope": binding.user_scope,
        "operation_id": derive_model_call_id(leaf_call_identity(context), 0),
        "consent_id": authority.consent_id,
        "expected_consent_revision": binding.consent_revision,
        "connection_id": binding.connection_authorization_ref.connection_id,
        "connection_revision": binding.connection_revision,
        "security_revision": binding.security_revision,
        "credential_version": binding.credential_version,
        "purpose": binding.purpose,
        "target_fingerprint": binding.target_fingerprint,
        "scope_version": binding.scope_version,
        "source_selections": binding.source_selections,
        "source_scope_digest": binding.source_scope_digest,
        "owner_epoch": facts.owner_epoch,
        "run_epoch": facts.run_epoch,
    }
    missing = [key for key in DATA_CONSENT_BINDING_KEYS if key not in payload]
    if missing:
        raise ValueError(f"data consent context missing keys: {', '.join(missing)}")
    return payload


def _validated_data_consent_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    """校验装配侧资料上下文（I43）：15 键闭合 + 源项类型 + 摘要新鲜重算。"""

    from core.agent_types import DataConsentSourceItem, data_consent_source_scope_digest
    from services.ai_service.scenario_executor import DATA_CONSENT_BINDING_KEYS

    expected = set(DATA_CONSENT_BINDING_KEYS)
    keys = set(raw)
    if keys != expected:
        missing = sorted(expected - keys)
        extra = sorted(keys - expected)
        raise ValueError(f"data consent keys mismatch (missing={missing}, extra={extra})")
    selections = raw["source_selections"]
    if not isinstance(selections, tuple) or any(not isinstance(item, DataConsentSourceItem) for item in selections):
        raise ValueError("data consent 'source_selections' must be a tuple of DataConsentSourceItem")
    if data_consent_source_scope_digest(selections) != raw["source_scope_digest"]:
        raise ValueError("data consent 'source_scope_digest' does not match source selections")
    return dict(raw)


async def resolve_comparison_lane_freeze(
    context: LeafExecutionContext, *, dao: Any | None = None
) -> FrozenLaneBinding | None:
    """读叶所属比较路的冻结参数（非比较执行返回 ``None``；读不到即抛错）。

    只用持久事实：执行摘要给出 ``group_ref``/``lane_ref``，组共同 manifest 给出
    该路冻结连接（ID/版本/目标指纹/用途/范围版本）。组/路引用存在但 manifest
    不可读、不含该路或参数不全 → 抛错（调用方零模型外发，不回退当前
    ``agent_research`` 场景路由另选连接）。
    """

    from core.agent_types import decode_json_doc

    agent_dao = dao
    if agent_dao is None:
        # lazy-import：CacheManager 依赖全量 DAO 注册链，仅比较路绑定需要。
        from data.cache.cache_manager import CacheManager

        agent_dao = getattr(CacheManager(), "agent_dao", None)
    if agent_dao is None:
        raise RuntimeError("agent dao unavailable (zero model dispatch)")
    facts = await agent_dao.read_leaf_dispatch_facts(context.execution_id, context.round_id)
    snapshot = await agent_dao.get_execution_snapshot(context.execution_id, user_scope=facts.user_scope, limit=1)
    summary = snapshot.execution
    group_ref = getattr(summary, "comparison_group_ref", None)
    if group_ref is None:
        return None
    lane_ref = getattr(summary, "comparison_lane_ref", None)
    if not isinstance(lane_ref, str) or lane_ref == "":
        raise ValueError("comparison group execution is missing its lane ref (zero model dispatch)")
    payload = await agent_dao.read_comparison_manifest(user_scope=facts.user_scope, group_ref=group_ref)
    if payload is None:
        raise ValueError("comparison group manifest unavailable (zero model dispatch)")
    group: FrozenComparisonGroup = decode_comparison_manifest(decode_json_doc(payload))
    parameters = require_lane_parameters(group, lane_ref)
    return FrozenLaneBinding(group_ref=group.group_ref, lane_ref=parameters.lane_ref, parameters=parameters)


def _pinned_snapshot(snapshot: ExecutionSnapshot, parameters: ComparisonLaneParameters) -> ExecutionSnapshot:
    """把执行快照收窄到本路冻结连接（只留该候选；缺失/目标变化即 fail-closed）。

    不按当前场景路由顺序另选连接：路线里没有该连接、或该连接的目标指纹已与受理
    时冻结值不同（用户改过连接）→ 抛错，调用方零模型外发。
    """

    from services.ai_service.scenario_dispatch import build_route_snapshot

    pinned: list[Mapping[str, Any]] = []
    for route in snapshot.routes:
        matches = [
            candidate
            for candidate in route.get("candidates", ())
            if isinstance(candidate, Mapping) and str(candidate.get("connection_id")) == parameters.connection_id
        ]
        if not matches:
            continue
        candidate = matches[0]
        fingerprint = candidate.get("target_fingerprint")
        if fingerprint is not None and str(fingerprint) != parameters.target_fingerprint:
            raise RuntimeError("comparison lane connection target changed since acceptance (zero model dispatch)")
        pinned.append(
            build_route_snapshot(
                route,
                scenario_id=str(route.get("scenario_id") or AGENT_RESEARCH_SCENARIO_ID),
                candidates=({**dict(candidate), "candidate_index": 0},),
            )
        )
    if not pinned:
        raise RuntimeError("comparison lane frozen connection is unavailable (zero model dispatch)")
    return replace(snapshot, routes=tuple(pinned))


def _require_consent_connection(payload: Mapping[str, Any], parameters: ComparisonLaneParameters) -> None:
    """资料许可的实际连接必须等于本路冻结连接（M15：运行期只核不换）。"""

    connection_id = payload.get("connection_id")
    connection_revision = payload.get("connection_revision")
    if connection_id != parameters.connection_id:
        raise ValueError("comparison lane data consent connection does not match frozen lane (zero model dispatch)")
    if (
        isinstance(connection_revision, bool)
        or not isinstance(connection_revision, int)
        or connection_revision != parameters.connection_revision
    ):
        raise ValueError(
            "comparison lane data consent connection revision does not match frozen lane (zero model dispatch)"
        )


async def resolve_agent_model_binding(
    context: LeafExecutionContext,
    *,
    data_consent_provider: DataConsentProvider | None = None,
    lane_freeze_provider: LaneFreezeProvider | None = None,
) -> LeafModelBinding:
    """生产默认模型绑定：按 ``agent_research`` 场景路由取执行器与冻结快照。

    路由未启用/无候选/调度服务未组装 → 明确失败（不静默换模型、零外发）。
    I43：资料许可决定随 binding 冻结——有决定则写 ``data_consent``（15 键）与
    operation 归属作用域；无决定（含未接线 provider）写 ``require_data_consent``
    且**不写** ``data_consent``（执行器零外发拒绝，不放行「无资料发送」）。

    F37 比较路（``lane_freeze_provider`` 命中）：快照收窄到该路冻结连接，且资料
    许可的连接 ID/版本必须与冻结值逐项相等；任一缺失/不同 → 抛错（零外发），
    不以当前场景路由替换本路所选模型。

    期限：``deadline = now + dispatch.total_timeout_seconds``（派发侧冻结路由的总
    时限）；缺失 / 非法即抛错（零外发），不以臆造默认预算放行（R21）。
    """

    from services.ai_service.scenario_dispatch import resolve_executor

    dispatch = await resolve_executor(AGENT_RESEARCH_SCENARIO_ID)
    if dispatch is None:
        raise RuntimeError(f"{AGENT_RESEARCH_SCENARIO_ID} route unavailable (zero model dispatch)")
    total_timeout = dispatch.total_timeout_seconds
    if (
        isinstance(total_timeout, bool)
        or not isinstance(total_timeout, int | float)
        or not math.isfinite(float(total_timeout))
        or float(total_timeout) <= 0
    ):
        raise ValueError(
            f"{AGENT_RESEARCH_SCENARIO_ID} route total_timeout_seconds missing or invalid (zero model dispatch)"
        )
    deadline = asyncio.get_running_loop().time() + float(total_timeout)
    lane_freeze = await lane_freeze_provider(context) if lane_freeze_provider is not None else None
    snapshot = dispatch.snapshot
    raw_consent = await data_consent_provider(context) if data_consent_provider is not None else None
    if lane_freeze is not None:
        if raw_consent is None:
            raise RuntimeError("comparison lane requires a frozen data consent (zero model dispatch)")
        _require_consent_connection(raw_consent, lane_freeze.parameters)
        snapshot = _pinned_snapshot(snapshot, lane_freeze.parameters)
    binding: dict[str, Any] = {
        "scenario_id": AGENT_RESEARCH_SCENARIO_ID,
        "task_id": context.node.node_id,
    }
    if raw_consent is None:
        binding["require_data_consent"] = True
    else:
        binding["data_consent"] = _validated_data_consent_payload(raw_consent)
        binding["data_operation_scope"] = {
            "execution_id": context.execution_id,
            "round_id": context.round_id,
            "node_id": context.node.node_id,
        }
    return LeafModelBinding(
        executor=dispatch.executor,
        snapshot=snapshot,
        binding=binding,
        deadline=deadline,
    )


async def unavailable_leaf_agent(
    model: GatewayModel,
    context: LeafExecutionContext,
    binding: LeafModelBinding,
) -> NodeOutcome:
    """显式「未接线、零外发」叶主体替身：如实报失败，不伪造成果（R21）。"""

    logger.warning(
        "[LeafRuntime] leaf agent runner explicitly unavailable, leaf fails without outbound "
        "(execution=%s round=%s node=%s)",
        context.execution_id,
        context.round_id,
        context.node.node_id,
    )
    return NodeOutcome(node_id=context.node.node_id, state="failed", error_code=LEAF_AGENT_UNAVAILABLE)


def default_leaf_runtime(
    dao: Any | None = None,
    *,
    tool_runtime_factory: ToolRuntimeFactory | None = None,
) -> LeafRuntimeWiring:
    """生产默认装配：真实场景路由解析 + 资料许可提供者 + 真实叶 Agent 主体。

    ``dao`` 缺省时提供者与叶主体在运行时惰性取 ``CacheManager().agent_dao``；无法
    取得时掷错/如实失败（叶失败、零外发），不降级为「无许可约束」。
    """

    provider: DataConsentProvider = (
        resolve_leaf_data_consent
        if dao is None
        else cast(DataConsentProvider, functools.partial(resolve_leaf_data_consent, dao=dao))
    )
    # F37：比较路按持久 manifest 冻结连接绑定（非比较执行返回 None，不改语义）。
    lane_provider: LaneFreezeProvider = (
        resolve_comparison_lane_freeze
        if dao is None
        else cast(LaneFreezeProvider, functools.partial(resolve_comparison_lane_freeze, dao=dao))
    )

    async def _model_binding(context: LeafExecutionContext) -> LeafModelBinding:
        return await resolve_agent_model_binding(
            context, data_consent_provider=provider, lane_freeze_provider=lane_provider
        )

    # lazy-import：leaf_agent 依赖本模块的类型与常量，模块级互引会成环。
    from services.agent_service.leaf_agent import build_leaf_agent_runner

    return LeafRuntimeWiring(
        model_binding=_model_binding,
        agent_runner=build_leaf_agent_runner(dao, tool_runtime_factory=tool_runtime_factory),
        data_consent_provider=provider,
        tool_runtime_factory=tool_runtime_factory,
        actual_input_binder=None if dao is None else build_actual_input_binder(dao),
        leaf_ticket_gate=None if dao is None else build_leaf_ticket_gate(dao),
        dao=dao,
    )


def leaf_call_identity(context: LeafExecutionContext) -> str:
    """叶执行的持久执行身份（R2-I41：同一步骤重放复得同一调用号）。"""

    return leaf_workflow_id(
        execution_id=context.execution_id,
        round_id=context.round_id,
        node_id=context.node.node_id,
    )


def build_gateway_model(
    context: LeafExecutionContext, binding: LeafModelBinding, *, dao: Any | None = None
) -> GatewayModel:
    """GatewayModel 工厂：模型桥身份只由叶持久执行身份派生（禁随机身份）。

    提供 ``dao`` 时把执行器包上逐回合额度准入（真实模型调用前先计数）；未提供
    则不计数（仅供纯装配/单测构造，生产组合根始终提供 DAO）。
    """

    executor: ChatTurnExecutor = (
        binding.executor
        if dao is None
        else AdmissionCountingExecutor(
            binding.executor, dao=dao, execution_id=context.execution_id, round_id=context.round_id
        )
    )
    return GatewayModel(
        executor,
        binding.binding,
        binding.snapshot,
        tool_schema_version=binding.tool_schema_version,
        call_identity=leaf_call_identity(context),
        profile=binding.profile,
    )


def build_leaf_runner(
    *, model_binding: ModelBindingResolver, agent_runner: LeafAgentRunner, dao: Any | None = None
) -> LeafRunner:
    """组合叶 runner：解析冻结绑定 → 构造模型桥 → 交叶 Agent 主体。"""

    async def _run(context: LeafExecutionContext) -> NodeOutcome:
        resolved = await model_binding(context)
        model = build_gateway_model(context, resolved, dao=dao)
        return await agent_runner(model, context, resolved)

    return _run


def install_leaf_runtime(wiring: LeafRuntimeWiring | None = None) -> None:
    """在生产组合根安装叶 runner 与父绑定/票据门（重复安装以最后一次为准）。

    未提供绑定器/票据门则不登记对应单例：父 workflow 在派发前 ``require``
    失败并零叶派发（fail-closed，不以缺轮次/缺额度推进）。
    """

    runtime = wiring or default_leaf_runtime()
    LeafRuntimeRegistry.install(
        build_leaf_runner(
            model_binding=runtime.model_binding,
            agent_runner=runtime.agent_runner,
            dao=runtime.dao,
        )
    )
    if runtime.actual_input_binder is not None:
        ActualInputBinderRegistry.install(runtime.actual_input_binder)
    if runtime.leaf_ticket_gate is not None:
        LeafTicketGateRegistry.install(runtime.leaf_ticket_gate)
    logger.info("[LeafRuntime] leaf runner installed (call identity derived from leaf workflow id)")
