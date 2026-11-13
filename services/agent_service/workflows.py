"""原生一层叶 DAG workflow 与就绪叶调度（DD §8 F04/SQ-04；HANDOFF §7.2/§8.3，D-02）。

职责边界：

- **本模块只做编排**：父研究 workflow 与一层叶 workflow 的稳定命名、封闭载荷、
  就绪叶事件驱动推进与叶事实汇总；实际取数/模型/工具执行由组装期登记的叶执行器
  （:class:`LeafRuntimeRegistry`）承担（D-03/E 系列接线）。
- **DBOS 是唯一持久执行推进器**（INV-02）：应用表只保存业务事实与可核对投影，
  不以应用镜像状态驱动第二调度。
- **一层由结构保证**：计划为扁平节点表（:class:`PlanSpecProposal`），叶不再委派、
  不暴露间接 Agent 工具（工具白名单在 E 系列装配处约束）。

调度语义（DD F04/F05、SQ-04）：

- 父 workflow 只启动就绪叶：其全部前置为 ``complete``（"适用成功前置"）；
- 消费完成事实后**立即**启动新就绪叶（A/B→C、B→D 时 B 结束即启 D，不按固定
  批次 gather）；
- 失败/终止/``partial`` 只阻相关后继（不借成功兄弟补答案），独立分支继续；
- ``paused``/``waiting_*``/``unknown``/``closed_unknown`` 等不自动结束父：不伪造
  终态、不启动后继、父不收敛（未知不等于失败或成功）；
- 汇总只报叶事实（``leaves_all_succeeded``），**不以成功叶数量代替目标完整完成**；
  目标覆盖与收敛结论由服务层按 F04 保存。

恢复/重放：``application_version`` 必须为真实工作流代码版本（HANDOFF §8.3），
workflow/step 名称一经发布即稳定标识，重命名等同新版本；载荷带 ``payload_version``
封闭校验，跨版本恢复前须核对兼容矩阵（§8.4）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, NoReturn

from dbos import DBOS, WorkflowSerializationFormat

from core.agent_types import (
    DISPLAY_STATES,
    AgentValidationError,
    PlanNodeSpec,
    PlanSpecProposal,
    decode_plan_node_spec,
    decode_plan_spec_proposal,
    is_digest,
    require_identity,
)
from utils.singleton_registry import register_singleton

# ---------------------------------------------------------------------------
# 冻结常量：workflow/step 名称与载荷版本（发布后不得改写，重命名等同新版本）
# ---------------------------------------------------------------------------

#: 父研究 workflow 稳定名（DBOS 重放身份，HANDOFF §8.3）。
RESEARCH_WORKFLOW_NAME: Final[str] = "agent_research"
#: 一层叶 workflow 稳定名。
LEAF_WORKFLOW_NAME: Final[str] = "agent_leaf"
#: 叶执行 step 稳定名（唯一实际 IO 段，DBOS 按 step 持久化）。
LEAF_EXECUTE_STEP_NAME: Final[str] = "agent_leaf_execute"

#: 载荷结构版本（进入封闭校验；跨版本恢复前须核对兼容矩阵 §8.4）。
PAYLOAD_VERSION: Final[int] = 1

#: 子 workflow 结果轮询间隔（仅影响观察节拍，不影响推进正确性）。
CHILD_RESULT_POLL_INTERVAL_SEC: Final[float] = 0.1

_PAYLOAD_VERSION_KEY: Final = "payload_version"
_RESEARCH_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "plan_digest", "nodes"})
#: 重试轮载荷在版本 1 上的兼容扩展键（缺省视为空：首轮无保留前置）。
_RESEARCH_PAYLOAD_OPTIONAL_KEYS: Final = frozenset({"preserved_outcomes"})
_LEAF_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "plan_digest", "node"})
_NODE_OUTCOME_KEYS: Final = frozenset({"node_id", "state", "error_code", "artifact_ref"})

#: 叶状态语义分类（值域 = 冻结的 display_state 15 值，不自造词表）。
_SUCCESS_STATES: Final[frozenset[str]] = frozenset({"complete"})
_BLOCKING_STATES: Final[frozenset[str]] = frozenset({"failed", "withdrawn", "ended", "partial"})
_NON_TERMINAL_STATES: Final[frozenset[str]] = DISPLAY_STATES - _SUCCESS_STATES - _BLOCKING_STATES


# ---------------------------------------------------------------------------
# 封闭载荷与叶结果 wire
# ---------------------------------------------------------------------------


def _fail(code: str, field_path: str) -> NoReturn:
    raise AgentValidationError(code, field_path)


def _require_digest(value: object, path: str) -> str:
    if not isinstance(value, str) or not is_digest(value):
        _fail("invalid_digest", path)
    return value


def _require_str(value: object, path: str) -> str:
    return require_identity(value, path)


def _node_to_wire(node: PlanNodeSpec) -> dict[str, Any]:
    """节点 wire（与 core 规范化形态一致；PORTABLE JSON 可序列化）。"""

    return {
        "node_id": node.node_id,
        "target": node.target,
        "role_key": node.role_key,
        "depends_on": list(node.depends_on),
        "capabilities": list(node.capabilities),
        "source_refs": [
            {
                "source_kind": ref.source_kind.value,
                "source_id": ref.source_id,
                "source_version": ref.source_version,
                "source_digest": ref.source_digest,
            }
            for ref in node.source_refs
        ],
    }


def _require_exact_keys(value: Mapping[str, object], required: frozenset[str], path: str) -> None:
    """全键必填、禁额外键（封闭 wire；缺失/额外分别给出稳定错误码）。"""

    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")


def _require_keys_with_optional(
    value: Mapping[str, object], required: frozenset[str], optional: frozenset[str], path: str
) -> None:
    """必填键全在、额外键仅限已声明可选键（兼容扩展不放开任意键）。"""

    missing = sorted(required - value.keys())
    if missing:
        _fail("missing_field", f"{path}.{missing[0]}")
    extra = sorted(value.keys() - required - optional)
    if extra:
        _fail("unknown_field", f"{path}.{extra[0]}")


def _require_payload_version(value: Mapping[str, object], path: str) -> int:
    version = value[_PAYLOAD_VERSION_KEY]
    if isinstance(version, bool) or not isinstance(version, int) or version != PAYLOAD_VERSION:
        _fail("unsupported_version", f"{path}.{_PAYLOAD_VERSION_KEY}")
    return version


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    """叶终态事实（一条叶执行的可核对结果；状态取冻结 display_state 值）。

    ``error_code``/``artifact_ref`` 缺失以 ``None`` 表示（不填充业务合法值，R21）。
    """

    node_id: str
    state: str
    error_code: str | None = None
    artifact_ref: str | None = None

    def to_wire(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "state": self.state,
            "error_code": self.error_code,
            "artifact_ref": self.artifact_ref,
        }


def _opt_str_or_none(value: object, path: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, path)


def decode_node_outcome(value: object, path: str = "$.node_outcome") -> NodeOutcome:
    """严格解码叶结果（四键封闭；状态必须为冻结 display_state）。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _NODE_OUTCOME_KEYS, path)
    state = value["state"]
    if not isinstance(state, str) or state not in DISPLAY_STATES:
        _fail("invalid_display_state", f"{path}.state")
    return NodeOutcome(
        node_id=_require_str(value["node_id"], f"{path}.node_id"),
        state=state,
        error_code=_opt_str_or_none(value["error_code"], f"{path}.error_code"),
        artifact_ref=_opt_str_or_none(value["artifact_ref"], f"{path}.artifact_ref"),
    )


def build_leaf_payload(*, execution_id: str, plan_digest: str, node: PlanNodeSpec) -> dict[str, Any]:
    """构造叶 workflow 载荷（PORTABLE JSON 可序列化；封闭键）。"""

    return {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "node": _node_to_wire(node),
    }


def decode_leaf_payload(value: object, path: str = "$.leaf_payload") -> tuple[str, str, PlanNodeSpec]:
    """严格解码叶载荷：``(execution_id, plan_digest, node)``。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _LEAF_PAYLOAD_KEYS, path)
    _require_payload_version(value, path)
    return (
        _require_str(value["execution_id"], f"{path}.execution_id"),
        _require_digest(value["plan_digest"], f"{path}.plan_digest"),
        decode_plan_node_spec(value["node"], f"{path}.node"),
    )


def decode_research_payload(
    value: object, path: str = "$.research_payload"
) -> tuple[str, str, PlanSpecProposal, tuple[NodeOutcome, ...]]:
    """严格解码父载荷：``(execution_id, plan_digest, plan, preserved_outcomes)``。

    ``preserved_outcomes`` 为重试轮冻结的「仍适用成功前置」旧轮事实；首轮缺省
    为空（版本 1 内的兼容扩展键，不放开任意键）。
    """

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_keys_with_optional(value, _RESEARCH_PAYLOAD_KEYS, _RESEARCH_PAYLOAD_OPTIONAL_KEYS, path)
    _require_payload_version(value, path)
    raw_nodes = value["nodes"]
    if not isinstance(raw_nodes, list):
        _fail("invalid_type", f"{path}.nodes")
    raw_preserved = value.get("preserved_outcomes", [])
    if not isinstance(raw_preserved, list):
        _fail("invalid_type", f"{path}.preserved_outcomes")
    preserved = tuple(
        decode_node_outcome(item, f"{path}.preserved_outcomes[{index}]") for index, item in enumerate(raw_preserved)
    )
    return (
        _require_str(value["execution_id"], f"{path}.execution_id"),
        _require_digest(value["plan_digest"], f"{path}.plan_digest"),
        decode_plan_spec_proposal({"nodes": raw_nodes}, f"{path}.plan_spec"),
        preserved,
    )


# ---------------------------------------------------------------------------
# 汇总（只报叶事实，不宣称目标覆盖）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanRunSummary:
    """一次父执行的叶事实汇总。

    ``leaves_all_succeeded`` 只说明全部叶``complete``，**不代表目标已覆盖**；
    目标覆盖/收敛结论由服务层按 F04 保存（不以成功叶数量代替）。
    ``blocked`` 为上游失败/终止/partial 导致未启动的节点，``block_reasons``
    给出 ``(node_id, 原因)`` 供保存受阻原因。``pending`` 为非终态节点
    （暂停/等待/未知等），非空中则 ``converged`` 为 False。
    """

    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    withdrawn: tuple[str, ...]
    ended: tuple[str, ...]
    partial: tuple[str, ...]
    blocked: tuple[str, ...]
    pending: tuple[str, ...]
    block_reasons: tuple[tuple[str, str], ...]
    outcomes: tuple[NodeOutcome, ...]
    leaves_all_succeeded: bool
    converged: bool

    def to_wire(self) -> dict[str, Any]:
        return {
            _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
            "succeeded": list(self.succeeded),
            "failed": list(self.failed),
            "withdrawn": list(self.withdrawn),
            "ended": list(self.ended),
            "partial": list(self.partial),
            "blocked": list(self.blocked),
            "pending": list(self.pending),
            "block_reasons": [[node_id, reason] for node_id, reason in self.block_reasons],
            "outcomes": [outcome.to_wire() for outcome in self.outcomes],
            "leaves_all_succeeded": self.leaves_all_succeeded,
            "converged": self.converged,
        }


# ---------------------------------------------------------------------------
# 就绪叶调度（纯逻辑，不依赖 DBOS）
# ---------------------------------------------------------------------------


class LeafDagScheduler:
    """一层叶 DAG 调度状态机（依赖已通过派发前校验，见 ``validate_plan_spec_proposal``）。

    只依据叶终态事实推进：前置全部 ``complete`` 才就绪；失败/终止/partial 使
    下游阻塞并逐层传播；非终态（暂停/等待/未知）保持 pending，不伪造结论。
    """

    def __init__(self, plan: PlanSpecProposal) -> None:
        self._order: tuple[str, ...] = tuple(node.node_id for node in plan.nodes)
        self._nodes: dict[str, PlanNodeSpec] = {node.node_id: node for node in plan.nodes}
        self._dependents: dict[str, tuple[str, ...]] = {
            node_id: tuple(other.node_id for other in plan.nodes if node_id in other.depends_on)
            for node_id in self._order
        }
        self._started: set[str] = set()
        self._outcomes: dict[str, NodeOutcome] = {}
        self._blocked: dict[str, str] = {}
        #: 重试轮冻结的「仍适用成功前置」旧轮事实（计划外节点；不属本轮成果）。
        self._preserved: dict[str, NodeOutcome] = {}

    def node(self, node_id: str) -> PlanNodeSpec:
        """按身份取节点（未知身份明确拒绝）。"""

        node = self._nodes.get(node_id)
        if node is None:
            _fail("unknown_node", f"$.nodes[{node_id}]")
        return node

    def seed_preserved(self, outcomes: tuple[NodeOutcome, ...]) -> None:
        """登记重试轮保留的成功前置（只接受 ``complete`` 且不得在本轮范围内）。

        保留事实来自旧轮已保存成果（不重做、不复制许可与费用）；非成功前置
        不能作为「适用成功前置」，冲突必须显式拒绝而非静默降级。
        """

        for outcome in outcomes:
            if outcome.state != "complete":
                _fail("invalid_preserved_outcome", f"$.preserved_outcomes[{outcome.node_id}]")
            if outcome.node_id in self._nodes:
                _fail("preserved_node_in_retry_scope", f"$.preserved_outcomes[{outcome.node_id}]")
            self._preserved[outcome.node_id] = outcome

    def _dependency_satisfied(self, dependency: str) -> bool:
        """前置满足：本轮已 ``complete``，或重试轮保留的 ``complete`` 旧事实。"""

        outcome = self._outcomes.get(dependency) or self._preserved.get(dependency)
        return outcome is not None and outcome.state in _SUCCESS_STATES

    def ready(self) -> tuple[str, ...]:
        """当前就绪叶：未启动、未阻塞且全部前置 ``complete``（按计划顺序）。"""

        ready: list[str] = []
        for node_id in self._order:
            if node_id in self._started or node_id in self._blocked:
                continue
            node = self._nodes[node_id]
            if all(self._dependency_satisfied(dependency) for dependency in node.depends_on):
                ready.append(node_id)
        return tuple(ready)

    def start(self, node_id: str) -> None:
        """登记叶启动（仅允许就绪叶；重复启动/越过依赖明确拒绝）。"""

        self.node(node_id)
        if node_id in self._started:
            _fail("node_already_started", f"$.nodes[{node_id}]")
        if node_id not in self.ready():
            _fail("node_not_ready", f"$.nodes[{node_id}]")
        self._started.add(node_id)

    def record(self, outcome: NodeOutcome) -> None:
        """登记叶终态事实；非成功终态递归阻塞其下游。"""

        self.node(outcome.node_id)
        if outcome.state not in _SUCCESS_STATES | _BLOCKING_STATES | _NON_TERMINAL_STATES:
            _fail("invalid_display_state", f"$.outcomes[{outcome.node_id}].state")
        self._outcomes[outcome.node_id] = outcome
        if outcome.state in _BLOCKING_STATES:
            self._block_dependents(outcome.node_id, f"upstream_{outcome.state}:{outcome.node_id}")

    def _block_dependents(self, node_id: str, reason: str) -> None:
        for dependent in self._dependents[node_id]:
            if dependent in self._blocked:
                continue
            self._blocked[dependent] = reason
            self._block_dependents(dependent, f"blocked_by:{dependent}")

    def converged(self) -> bool:
        """全部节点均已有成功/阻塞终态（非终态既不算收敛也不算终态）。"""

        return all(
            node_id in self._blocked
            or (node_id in self._outcomes and self._outcomes[node_id].state in _SUCCESS_STATES | _BLOCKING_STATES)
            for node_id in self._order
        )

    def summary(self) -> PlanRunSummary:
        """叶事实汇总（不做目标覆盖结论）。"""

        def _state_ids(state: str) -> tuple[str, ...]:
            return tuple(
                node_id
                for node_id in self._order
                if node_id in self._outcomes and self._outcomes[node_id].state == state
            )

        succeeded = _state_ids("complete")
        blocked = tuple(node_id for node_id in self._order if node_id in self._blocked)
        # 非终态事实（暂停/等待/未知）仍属 pending：不参与收敛、不启动后继。
        pending = tuple(
            node_id
            for node_id in self._order
            if node_id not in self._blocked
            and (node_id not in self._outcomes or self._outcomes[node_id].state in _NON_TERMINAL_STATES)
        )
        return PlanRunSummary(
            succeeded=succeeded,
            failed=_state_ids("failed"),
            withdrawn=_state_ids("withdrawn"),
            ended=_state_ids("ended"),
            partial=_state_ids("partial"),
            blocked=blocked,
            pending=pending,
            block_reasons=tuple((node_id, self._blocked[node_id]) for node_id in blocked),
            outcomes=tuple(self._outcomes[node_id] for node_id in self._order if node_id in self._outcomes),
            leaves_all_succeeded=bool(succeeded) and len(succeeded) == len(self._order),
            converged=self.converged(),
        )


LeafRunner = Callable[[PlanNodeSpec], Awaitable[NodeOutcome]]


async def drive_leaf_dag(
    plan: PlanSpecProposal,
    start_leaf: LeafRunner,
    *,
    preserved: tuple[NodeOutcome, ...] = (),
) -> PlanRunSummary:
    """事件驱动推进一层叶 DAG，返回叶事实汇总。

    每个完成事实到达后立即启动新就绪叶（不按固定批次 gather）。``preserved``
    为重试轮冻结的旧轮成功前置（计划外节点，不重做、不重计费用）。``start_leaf``
    须把「已判定的业务失败」作为 ``NodeOutcome(state="failed")`` 返回；未判定
    异常与 ``CancelledError`` 向上传播（不伪造 failed、不吞取消，R2/R21）。
    非终态结果（暂停/等待/未知）使本次推进在没有可运行叶时结束且不收敛。
    """

    scheduler = LeafDagScheduler(plan)
    scheduler.seed_preserved(preserved)
    running: dict[str, asyncio.Task[NodeOutcome]] = {}
    try:
        while True:
            for node_id in scheduler.ready():
                scheduler.start(node_id)
                running[node_id] = asyncio.ensure_future(start_leaf(scheduler.node(node_id)))
            if not running:
                break
            done, _ = await asyncio.wait(running.values(), return_when=asyncio.FIRST_COMPLETED)
            for node_id in [nid for nid, task in running.items() if task in done]:
                task = running.pop(node_id)
                outcome = task.result()
                if outcome.node_id != node_id:
                    # 错归属结果不得记到本节点（身份核验，DD §4.1）
                    _fail("outcome_identity_mismatch", f"$.outcomes[{node_id}].node_id")
                scheduler.record(outcome)
    finally:
        for task in running.values():
            if not task.done():
                task.cancel()
    return scheduler.summary()


# ---------------------------------------------------------------------------
# 叶执行器登记（组装期注入；未登记明确拒绝）
# ---------------------------------------------------------------------------


@register_singleton
class LeafRuntimeRegistry:
    """叶执行器登记表（D-03/E 系列组装期安装；进程内单例，测试自动复位）。

    未登记时 ``require`` 明确拒绝——不默认放行、不以占位执行器冒充真实叶执行。
    """

    _runner: LeafRunner | None = None

    @classmethod
    def install(cls, runner: LeafRunner) -> None:
        if not callable(runner):
            raise TypeError("leaf runner must be callable")
        cls._runner = runner

    @classmethod
    def require(cls) -> LeafRunner:
        runner = cls._runner
        if runner is None:
            raise RuntimeError("leaf runner not installed (assemble agent runtime first)")
        return runner

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._runner = None


# ---------------------------------------------------------------------------
# 原生 workflow（唯一持久推进面；名称发布后稳定）
# ---------------------------------------------------------------------------


@DBOS.step(name=LEAF_EXECUTE_STEP_NAME)
async def execute_leaf_step(payload: dict[str, Any]) -> dict[str, Any]:
    """叶执行 step：解析封闭载荷 → 已登记执行器 → 终态事实 wire。"""

    _execution_id, _plan_digest, node = decode_leaf_payload(payload)
    outcome = await LeafRuntimeRegistry.require()(node)
    if outcome.node_id != node.node_id:
        _fail("outcome_identity_mismatch", "$.node_outcome.node_id")
    return outcome.to_wire()


@DBOS.workflow(name=LEAF_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_leaf_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """一层叶 workflow（独立上下文；不暴露继续委派）。"""

    decode_leaf_payload(payload)
    return await execute_leaf_step(payload)


@DBOS.workflow(name=RESEARCH_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_research_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """父研究 workflow：启动就绪叶、消费完成事实、汇总叶事实。

    计划须在派发前已由服务层校验并保存（计划版 + 原生引用），本 workflow 只做
    严格解码与编排，不重复准入判定。
    """

    execution_id, plan_digest, plan, preserved = decode_research_payload(payload)

    async def start_leaf(node: PlanNodeSpec) -> NodeOutcome:
        handle = await DBOS.start_workflow_async(
            run_leaf_workflow,
            build_leaf_payload(execution_id=execution_id, plan_digest=plan_digest, node=node),
        )
        return decode_node_outcome(await handle.get_result(polling_interval_sec=CHILD_RESULT_POLL_INTERVAL_SEC))

    summary = await drive_leaf_dag(plan, start_leaf, preserved=preserved)
    return summary.to_wire()


def build_research_payload(
    plan: PlanSpecProposal,
    *,
    execution_id: str,
    plan_digest: str,
    preserved: tuple[NodeOutcome, ...] = (),
) -> dict[str, Any]:
    """构造父 workflow 载荷（与保存的计划版同源内容；PORTABLE JSON 可序列化）。

    ``preserved`` 为重试轮冻结的旧轮成功前置事实（首轮为空）。
    """

    return {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "nodes": [_node_to_wire(node) for node in plan.nodes],
        "preserved_outcomes": [outcome.to_wire() for outcome in preserved],
    }


# ---------------------------------------------------------------------------
# 失败重试范围（F04/F05：开新轮只重做目标及受影响下游，保旧成果与费用）
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RetryScope:
    """重试新轮范围：``redo`` 按计划顺序含目标及传递下游；``preserved`` 为不在
    范围内、仍可复用的旧轮成功前置（或被保留的独立分支）。

    独立兄弟不在 ``redo`` 内（失败仅阻相关后继）；范围外旧成果与费用保持原样。
    """

    redo: tuple[str, ...]
    preserved: tuple[str, ...]


def compute_retry_scope(plan: PlanSpecProposal, *, targets: tuple[str, ...]) -> RetryScope:
    """计算重试范围：目标 → 传递下游（前置变更同理使下游失效）。

    Args:
        plan: 已校验的一层叶计划（原计划版，含全部节点）。
        targets: 需重做的节点身份（失败叶或前置输入变更的叶）。

    Raises:
        AgentValidationError: 目标为空、未知目标或重复目标（``unknown_node`` /
            ``invalid_targets``）。
    """

    if not targets:
        _fail("invalid_targets", "$.targets")
    if len(set(targets)) != len(targets):
        _fail("invalid_targets", "$.targets")
    order = tuple(node.node_id for node in plan.nodes)
    nodes = {node.node_id: node for node in plan.nodes}
    dependents: dict[str, list[str]] = {node_id: [] for node_id in order}
    for node in plan.nodes:
        for dependency in node.depends_on:
            dependents[dependency].append(node.node_id)

    affected: set[str] = set()
    stack = list(targets)
    while stack:
        node_id = stack.pop()
        if node_id not in nodes:
            _fail("unknown_node", f"$.targets[{node_id}]")
        if node_id in affected:
            continue
        affected.add(node_id)
        stack.extend(dependents[node_id])

    redo = tuple(node_id for node_id in order if node_id in affected)
    preserved = tuple(node_id for node_id in order if node_id not in affected)
    return RetryScope(redo=redo, preserved=preserved)


def build_retry_payload(
    plan: PlanSpecProposal,
    *,
    execution_id: str,
    plan_digest: str,
    targets: tuple[str, ...],
    preserved_outcomes: tuple[NodeOutcome, ...],
) -> tuple[dict[str, Any], RetryScope]:
    """构造重试轮父载荷：``nodes`` 为范围内节点，``preserved_outcomes`` 为旧轮
    成功前置。

    范围外前置必须由 ``preserved_outcomes`` 全部覆盖（仍适用成功结果）；缺失
    显式拒绝（不静默不满足依赖）。
    """

    scope = compute_retry_scope(plan, targets=targets)
    redo_nodes = tuple(node for node in plan.nodes if node.node_id in set(scope.redo))
    preserved_ids = {outcome.node_id for outcome in preserved_outcomes}
    for node in redo_nodes:
        for dependency in node.depends_on:
            if dependency not in set(scope.redo) and dependency not in preserved_ids:
                _fail("missing_preserved_predecessor", f"$.nodes[{node.node_id}].depends_on")
    payload = build_research_payload(
        PlanSpecProposal(nodes=redo_nodes),
        execution_id=execution_id,
        plan_digest=plan_digest,
        preserved=preserved_outcomes,
    )
    return payload, scope


__all__ = [
    "CHILD_RESULT_POLL_INTERVAL_SEC",
    "LEAF_EXECUTE_STEP_NAME",
    "LEAF_WORKFLOW_NAME",
    "PAYLOAD_VERSION",
    "RESEARCH_WORKFLOW_NAME",
    "LeafDagScheduler",
    "LeafRunner",
    "LeafRuntimeRegistry",
    "NodeOutcome",
    "PlanRunSummary",
    "RetryScope",
    "build_leaf_payload",
    "build_research_payload",
    "build_retry_payload",
    "compute_retry_scope",
    "decode_leaf_payload",
    "decode_node_outcome",
    "decode_research_payload",
    "drive_leaf_dag",
    "execute_leaf_step",
    "run_leaf_workflow",
    "run_research_workflow",
]
