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
- ``paused``/``waiting_*``/``unknown`` 等不自动结束父：不伪造终态、不启动后继、
  父不收敛（未知不等于失败或成功）；``closed_unknown`` 是**不可恢复终态**（F10）：
  执行已结束但结果未知，按结案收口（全部叶结案后可收敛、迟到事实不复活），
  单独列入汇总不并入失败，并阻其下游（未知结果不能作为适用成功前置）；
- 汇总只报叶事实（``leaves_all_succeeded``），**不以成功叶数量代替目标完整完成**；
  目标覆盖与收敛结论由服务层按 F04 保存。

恢复/重放：``application_version`` 必须为真实工作流代码版本（HANDOFF §8.3），
workflow/step 名称一经发布即稳定标识，重命名等同新版本；载荷带 ``payload_version``
封闭校验，跨版本恢复前须核对兼容矩阵（§8.4）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, NoReturn

from dbos import DBOS, SetWorkflowID, WorkflowSerializationFormat, WorkflowStatus, WorkflowStatusString

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
#: 叶观察 step 稳定名（父 workflow 的完成集合判定检查点，B1 重放面）。
LEAF_OBSERVE_STEP_NAME: Final[str] = "agent_leaf_observe"

#: 载荷结构版本（进入封闭校验；跨版本恢复前须核对兼容矩阵 §8.4）。
PAYLOAD_VERSION: Final[int] = 1

#: 观察 step 内部轮询间隔（仅影响单步耗时，不影响推进正确性）。
OBSERVE_POLL_INTERVAL_SEC: Final[float] = 0.2

#: 单次观察 step 的内部轮询上限（毫秒级检查点密度；重放按记录还原，不重跑）。
OBSERVE_SLICE_SEC: Final[float] = 2.0

#: 连续无进展观察预算（HANDOFF §8.3：观察超时只停观察、不取消执行）。
#: 取值覆盖单叶长任务（多轮模型调用 + 工具）并留余量；预算耗尽**不结束父**：
#: 生产父 workflow 经 :func:`wait_next_observation_window` 转入 DBOS 持久等待后
#: 开启新观察窗口（见 :func:`drive_leaf_dag`），超预算完成的长叶仍推进其后继，
#: 不伪造叶终态（§8.2 未知不等于失败）。以「连续无进展观察次数」计量：该计数
#: 只来自被记录的 step 结果，重放可确定复现，不引入墙钟依赖（不把超时升级为
#: workflow ERROR，避免恢复在同点反复失败）。
CHILD_OBSERVE_TIMEOUT_SEC: Final[float] = 900.0

#: 观察预算耗尽后的持久等待间隔（DBOS 原生可恢复等待）：父 workflow 以
#: ``DBOS.sleep_async`` 让出长叶期间的推进（恢复时已完成的 sleep 按记录跳过），
#: 等待结束后重新观察，不把「叶子仍在运行」误记为父批次结束。
IDLE_WAIT_INTERVAL_SEC: Final[float] = 60.0


_PAYLOAD_VERSION_KEY: Final = "payload_version"
_RESEARCH_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "round_id", "plan_digest", "nodes"})
#: 重试轮载荷在版本 1 上的兼容扩展键（缺省视为空：首轮无保留前置）。
_RESEARCH_PAYLOAD_OPTIONAL_KEYS: Final = frozenset({"preserved_outcomes"})
_LEAF_PAYLOAD_KEYS: Final = frozenset({"payload_version", "execution_id", "round_id", "plan_digest", "node"})
_NODE_OUTCOME_KEYS: Final = frozenset({"node_id", "state", "error_code", "artifact_ref"})

#: 叶状态语义分类（值域 = 冻结的 display_state 15 值，不自造词表）。
_SUCCESS_STATES: Final[frozenset[str]] = frozenset({"complete"})
_BLOCKING_STATES: Final[frozenset[str]] = frozenset({"failed", "withdrawn", "ended", "partial"})
#: 已结案但结果未知（不可恢复终态，F10 / ``NON_RECOVERABLE_DISPLAY_STATES``）。
#: 执行已结束、成果不可知：必须收口为终态（不保持可恢复占位、迟到事实不复活），
#: 但不得当作适用成功前置，故与阻塞态一样阻其下游；汇总单独列出，不与失败混同。
_CLOSED_UNKNOWN_STATES: Final[frozenset[str]] = frozenset({"closed_unknown"})
_TERMINAL_STATES: Final[frozenset[str]] = _SUCCESS_STATES | _BLOCKING_STATES | _CLOSED_UNKNOWN_STATES
_NON_TERMINAL_STATES: Final[frozenset[str]] = DISPLAY_STATES - _TERMINAL_STATES


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


def build_leaf_payload(*, execution_id: str, round_id: str, plan_digest: str, node: PlanNodeSpec) -> dict[str, Any]:
    """构造叶 workflow 载荷（PORTABLE JSON 可序列化；封闭键）。"""

    return {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "round_id": _require_str(round_id, "$.round_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "node": _node_to_wire(node),
    }


@dataclass(frozen=True, slots=True)
class LeafExecutionContext:
    """叶执行上下文（身份三元组 + 节点；执行器据此保存叶事实，不靠隐式全局）。

    轮次进入上下文是重试轮隔离的前提：新轮新身份，不复用旧轮成果。
    """

    execution_id: str
    round_id: str
    plan_digest: str
    node: PlanNodeSpec


def decode_leaf_payload(value: object, path: str = "$.leaf_payload") -> LeafExecutionContext:
    """严格解码叶载荷为 :class:`LeafExecutionContext`。"""

    if not isinstance(value, dict):
        _fail("invalid_type", path)
    _require_exact_keys(value, _LEAF_PAYLOAD_KEYS, path)
    _require_payload_version(value, path)
    return LeafExecutionContext(
        execution_id=_require_str(value["execution_id"], f"{path}.execution_id"),
        round_id=_require_str(value["round_id"], f"{path}.round_id"),
        plan_digest=_require_digest(value["plan_digest"], f"{path}.plan_digest"),
        node=decode_plan_node_spec(value["node"], f"{path}.node"),
    )


def leaf_workflow_id(*, execution_id: str, round_id: str, node_id: str) -> str:
    """叶 workflow 稳定身份（按计划身份而非调用顺序；B1 修复之身份面）。

    重放顺序稳定性由父 workflow 的确定性操作序列保证（启动/观察均由父协程按
    计划顺序发出、完成集合经 ``agent_leaf_observe`` 记录还原，见
    :func:`drive_leaf_dag`）；本函数提供与语义身份绑定的显式 ID：恢复/观察按
    ID 复接同一子执行，重试轮因 ``round_id`` 不同不复用旧成果。各段按
    ``%``／``:`` 转义后再拼接，保证拼接映射单射（不同三元组不产生同一 ID）。
    """

    def _escape(part: object) -> str:
        text = _require_str(part, "$.leaf_workflow_id")
        return text.replace("%", "%25").replace(":", "%3A")

    return "agent_leaf:" + ":".join((_escape(execution_id), _escape(round_id), _escape(node_id)))


def decode_research_payload(
    value: object, path: str = "$.research_payload"
) -> tuple[str, str, str, PlanSpecProposal, tuple[NodeOutcome, ...]]:
    """严格解码父载荷：``(execution_id, round_id, plan_digest, plan, preserved)``。

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
        _require_str(value["round_id"], f"{path}.round_id"),
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
    ``blocked`` 为上游失败/终止/partial/``closed_unknown`` 导致未启动的节点，
    ``block_reasons`` 给出 ``(node_id, 原因)`` 供保存受阻原因。``pending`` 为非
    终态节点（暂停/等待/``unknown`` 等），非空中则 ``converged`` 为 False。
    ``closed_unknown`` 为已结束但结果未知的结案叶（不可恢复终态）：单独列出，
    不并入 ``failed``/``ended``，也不进入 ``pending``（F10）。
    """

    succeeded: tuple[str, ...]
    failed: tuple[str, ...]
    withdrawn: tuple[str, ...]
    ended: tuple[str, ...]
    partial: tuple[str, ...]
    closed_unknown: tuple[str, ...]
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
            "closed_unknown": list(self.closed_unknown),
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

    只依据叶终态事实推进：前置全部 ``complete`` 才就绪；失败/终止/partial/
    ``closed_unknown`` 使下游阻塞并逐层传播（未知结果不能作为成功前置）；
    非终态（暂停/等待/``unknown``）保持 pending，不伪造结论。
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
        不能作为「适用成功前置」，冲突必须显式拒绝而非静默降级。判据与派发前
        :func:`_validate_preserved` 同组（此处为纵深防御）。
        """

        _validate_preserved(outcomes, scope_node_ids=set(self._nodes))
        for outcome in outcomes:
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
        """登记叶终态事实；非成功终态递归阻塞其下游。

        终态单调（同类 F07「迟到 running 不覆终态」）：已登记成功/阻塞/结案未知
        终态后再记任何事实都属冲突（迟到/重复事实不得把收敛翻回未收敛，也不得
        复活已结案的 ``closed_unknown``，F10）。
        """

        self.node(outcome.node_id)
        if outcome.state not in _TERMINAL_STATES | _NON_TERMINAL_STATES:
            _fail("invalid_display_state", f"$.outcomes[{outcome.node_id}].state")
        existing = self._outcomes.get(outcome.node_id)
        if existing is not None and existing.state in _TERMINAL_STATES:
            if existing == outcome:
                return  # 幂等：同一终态事实重复登记（观察重投）不视为冲突
            _fail("node_outcome_conflict", f"$.outcomes[{outcome.node_id}]")
        self._outcomes[outcome.node_id] = outcome
        if outcome.state in _BLOCKING_STATES | _CLOSED_UNKNOWN_STATES:
            self._block_dependents(outcome.node_id, f"upstream_{outcome.state}:{outcome.node_id}")

    def _block_dependents(self, node_id: str, reason: str) -> None:
        for dependent in self._dependents[node_id]:
            if dependent in self._blocked:
                continue
            self._blocked[dependent] = reason
            self._block_dependents(dependent, f"blocked_by:{dependent}")

    def converged(self) -> bool:
        """全部节点均已有终态或阻塞（非终态既不算收敛也不算终态）。"""

        return all(
            node_id in self._blocked
            or (node_id in self._outcomes and self._outcomes[node_id].state in _TERMINAL_STATES)
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
        # 非终态事实（暂停/等待/unknown）仍属 pending：不参与收敛、不启动后继；
        # closed_unknown 为不可恢复终态，单列于 closed_unknown、不保持可恢复占位。
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
            closed_unknown=_state_ids("closed_unknown"),
            blocked=blocked,
            pending=pending,
            block_reasons=tuple((node_id, self._blocked[node_id]) for node_id in blocked),
            outcomes=tuple(self._outcomes[node_id] for node_id in self._order if node_id in self._outcomes),
            leaves_all_succeeded=bool(succeeded) and len(succeeded) == len(self._order),
            converged=self.converged(),
        )


LeafRunner = Callable[[LeafExecutionContext], Awaitable[NodeOutcome]]
"""叶执行器（装配期登记；接收身份上下文与节点，据此保存叶事实）。"""

LeafStart = Callable[[PlanNodeSpec], Awaitable[str]]
"""启动就绪叶，返回观察键（DBOS 内为子 workflow ID）。

**必须由父 workflow 协程按计划顺序调用**（不得包进并发任务）：启动记录按
父体内操作序号（``parent_fid``）匹配重放；并发启动会让序号随真实时序漂移，
重放时复接到错误的子执行记录（B1）。
"""

LeafObserve = Callable[[tuple[tuple[str, str], ...]], Awaitable[dict[str, NodeOutcome]]]
"""观察已启动叶：输入 ``(node_id, 观察键)`` 序列，返回本轮新到达的终态事实。

DBOS 内由单个观察 step 承载（其结果被记录），重放时按记录还原完成集合与
迭代次数，父体操作序号因此确定（B1）；无新事实时返回空字典。
"""

LeafWaitCheckpoint = Callable[[], Awaitable[bool]]
"""观察预算耗尽时的持久等待检查点（返回 ``True`` 继续新观察窗口）。

生产实现为 :func:`wait_next_observation_window`（DBOS 持久 sleep，崩溃恢复时
跳过已完成睡眠）；纯逻辑调用可不提供，此时预算耗尽即结束本批次。
"""


async def wait_next_observation_window(sleep: Callable[[float], Awaitable[None]] = DBOS.sleep_async) -> bool:
    """观察预算耗尽后执行一次持久等待，返回 ``True`` 表示继续观察。

    默认使用 ``DBOS.sleep_async``：它是 DBOS 原生可恢复等待（已完成睡眠在恢复
    时被跳过），故父 workflow 崩溃重启后从记录点继续观察，而不是当作正常结束。
    ``sleep`` 可注入以便单测核验等待间隔而不启动 DBOS 运行时。
    """

    await sleep(IDLE_WAIT_INTERVAL_SEC)
    return True


async def drive_leaf_dag(
    plan: PlanSpecProposal,
    start_leaf: LeafStart,
    observe_leaves: LeafObserve,
    *,
    preserved: tuple[NodeOutcome, ...] = (),
    idle_observation_limit: int | None = None,
    wait_checkpoint: LeafWaitCheckpoint | None = None,
) -> PlanRunSummary:
    """事件驱动推进一层叶 DAG，返回叶事实汇总。

    每个完成事实到达后立即启动新就绪叶（不按固定批次 gather）。``preserved``
    为重试轮冻结的旧轮成功前置（计划外节点，不重做、不重计费用）。``start_leaf``
    须把「已判定的业务失败」作为 ``NodeOutcome(state="failed")`` 返回；未判定
    异常与 ``CancelledError`` 向上传播（不伪造 failed、不吞取消，R2/R21）。
    非终态结果（暂停/等待/``unknown``）使本次推进在没有可运行叶时结束且不收敛；
    ``closed_unknown`` 为不可恢复终态，按结案收口并阻其下游（F10）。

    观察预算（``idle_observation_limit`` 个连续无进展切片）耗尽**不是父终态**：
    提供 ``wait_checkpoint``（生产为 DBOS 持久等待）时，本函数持久等待后开启新
    观察窗口继续推进——超预算完成的长叶仍只启动一次后继（R02）；未提供时（纯
    逻辑调用）按本批次无可恢复接续者结束并保持 ``converged=False``，不伪造终态。

    确定性（B1 重放面）：本循环只由**父协程顺序**发出启动/观察操作——不创建
    并发任务，不以真实时序决定操作顺序；完成集合由 ``observe_leaves`` 的
    检查点结果决定。故重放时父体操作序号逐项对齐，子 workflow 记录不会错配。
    """

    scheduler = LeafDagScheduler(plan)
    scheduler.seed_preserved(preserved)
    running: dict[str, str] = {}
    idle_observations = 0
    while True:
        for node_id in scheduler.ready():
            scheduler.start(node_id)
            running[node_id] = await start_leaf(scheduler.node(node_id))
        if scheduler.converged():
            break
        if not running:
            # 无可运行叶且未收敛：剩余节点为非终态事实或不可达（不伪造结论）。
            break
        facts = await observe_leaves(tuple(running.items()))
        if not facts:
            idle_observations += 1
            if idle_observation_limit is not None and idle_observations >= idle_observation_limit:
                if wait_checkpoint is None:
                    # §8.3：无持久等待接续者时只停观察，不取消子执行、不伪造终态。
                    break
                # 预算耗尽：持久等待检查点后重开观察窗口（叶子仍在运行，父不结束）。
                idle_observations = 0
                if not await wait_checkpoint():
                    break
                continue
            continue
        idle_observations = 0
        for node_id, outcome in facts.items():
            if running.pop(node_id, None) is None:
                # 未启动节点的完成事实：不得计入本轮成果（身份核验）
                _fail("outcome_unknown_node", f"$.outcomes[{node_id}]")
            if outcome.node_id != node_id:
                # 错归属结果不得记到本节点（身份核验，DD §4.1）
                _fail("outcome_identity_mismatch", f"$.outcomes[{node_id}].node_id")
            scheduler.record(outcome)
    return scheduler.summary()


# ---------------------------------------------------------------------------
# 叶执行器登记（组装期注入；未登记明确拒绝）
# ---------------------------------------------------------------------------


@register_singleton
class LeafRuntimeRegistry:
    """叶执行器登记表（D-03/E 系列组装期安装；进程内单例，测试自动复位）。

    未登记时 ``require`` 明确拒绝——不默认放行、不以占位执行器冒充真实叶执行。
    """

    _instance: LeafRunner | None = None

    @classmethod
    def install(cls, runner: LeafRunner) -> None:
        if not callable(runner):
            raise TypeError("leaf runner must be callable")
        cls._instance = runner

    @classmethod
    def require(cls) -> LeafRunner:
        runner = cls._instance
        if runner is None:
            raise RuntimeError("leaf runner not installed (assemble agent runtime first)")
        return runner

    @classmethod
    def _reset_singleton(cls) -> None:
        cls._instance = None


# ---------------------------------------------------------------------------
# 原生 workflow（唯一持久推进面；名称发布后稳定）
# ---------------------------------------------------------------------------


@DBOS.step(name=LEAF_EXECUTE_STEP_NAME)
async def execute_leaf_step(payload: dict[str, Any]) -> dict[str, Any]:
    """叶执行 step：解析封闭载荷 → 已登记执行器 → 终态事实 wire。"""

    context = decode_leaf_payload(payload)
    outcome = await LeafRuntimeRegistry.require()(context)
    if outcome.node_id != context.node.node_id:
        _fail("outcome_identity_mismatch", "$.node_outcome.node_id")
    return outcome.to_wire()


@DBOS.workflow(name=LEAF_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_leaf_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """一层叶 workflow（独立上下文；不暴露继续委派）。"""

    decode_leaf_payload(payload)
    return await execute_leaf_step(payload)


#: 需要读取叶执行 step 落盘记录的原生状态（原生状态本身不足以判定实际退出与成果）。
_LEAF_EVIDENCE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        WorkflowStatusString.ERROR.value,
        WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value,
        WorkflowStatusString.CANCELLED.value,
    }
)


async def _leaf_step_evidence(workflow_id: str) -> tuple[bool, object]:
    """读取叶执行 step 的落盘记录：``(已有执行记录, 已落盘成果)``。

    DBOS 默认不抢占在途 step（``preemptible=False``）：取消可能先落到工作流状态，
    而在途 IO 结束后仍会把 ``agent_leaf_execute`` 的成果写入 step 记录。故只能在
    step 落盘后断言「实际退出」，不得据原生状态冒充（R04）。
    """

    for step in await DBOS.list_workflow_steps_async(workflow_id):
        if step["function_name"] == LEAF_EXECUTE_STEP_NAME:
            return True, step["output"]
    return False, None


def _terminal_outcome(
    node_id: str,
    *,
    state: str,
    output: object,
    executed: bool = False,
    step_output: object = None,
) -> NodeOutcome | None:
    """把子 workflow 状态投影为叶终态事实；非终态返回 ``None``（不伪造结论）。

    原生状态、实际退出与成果覆盖分开核对，互不冒充（R04）：

    - ``state`` 为 DBOS 原生状态，本身不等于业务结论；
    - ``executed`` 表示叶执行 step 已有落盘记录（实际 IO 已结束并有可核对结果）；
    - ``step_output`` 为已落盘成果——取消/工作流级错误后到达的成果仍按真实成果
      投影（含 ``partial``），不被 ``ended``/``failed`` 覆盖。

    - ``SUCCESS``：返回值必须是本节点的封闭终态 wire（身份核验）；
    - 有落盘成果：直接投影该成果；
    - ``ERROR`` / 恢复耗尽：``failed``（通用错误码，明细留在 DBOS 状态，R9）；
    - ``CANCELLED``：已有执行记录但无成果（step 报错）→ ``failed``；无任何执行
      记录（如启动隔离取消）→ ``ended``，成果覆盖未知，不用空成果冒充；
    - 其余（PENDING/ENQUEUED/DELAYED）：非终态，不参与收敛。
    """

    if state == WorkflowStatusString.SUCCESS.value:
        outcome = decode_node_outcome(output, f"$.children[{node_id}].output")
        if outcome.node_id != node_id:
            _fail("outcome_identity_mismatch", f"$.children[{node_id}].output.node_id")
        return outcome
    if state in _LEAF_EVIDENCE_STATUSES and step_output is not None:
        outcome = decode_node_outcome(step_output, f"$.children[{node_id}].step_output")
        if outcome.node_id != node_id:
            _fail("outcome_identity_mismatch", f"$.children[{node_id}].step_output.node_id")
        return outcome
    if state == WorkflowStatusString.ERROR.value:
        return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_error")
    if state == WorkflowStatusString.MAX_RECOVERY_ATTEMPTS_EXCEEDED.value:
        return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_recovery_exhausted")
    if state == WorkflowStatusString.CANCELLED.value:
        if executed:
            return NodeOutcome(node_id=node_id, state="failed", error_code="leaf_workflow_error")
        return NodeOutcome(node_id=node_id, state="ended", error_code="leaf_workflow_cancelled")
    return None


@DBOS.step(name=LEAF_OBSERVE_STEP_NAME)
async def observe_leaf_children(children: list[dict[str, str]], slice_seconds: float) -> list[dict[str, Any]]:
    """观察已启动叶：返回本轮新到达的终态事实 wire（无终态则空列表）。

    作为**单个 step** 记录输出：父 workflow 重放时按记录还原完成集合与迭代
    次数，父体操作序号逐项对齐（B1 重放面）。内部按 ``slice_seconds`` 轮询，
    只影响检查点密度与观察延迟，不参与任何业务判定。
    """

    deadline = time.monotonic() + max(slice_seconds, 0.0)
    # 每个工作流在本 slice 内只读一次执行记录（实际退出/成果覆盖证据）。
    evidence: dict[str, tuple[bool, object]] = {}
    while True:
        facts: list[dict[str, Any]] = []
        for child in children:
            node_id = _require_str(child.get("node_id"), "$.children[].node_id")
            workflow_id = _require_str(child.get("workflow_id"), "$.children[].workflow_id")
            status: WorkflowStatus | None = await DBOS.get_workflow_status_async(workflow_id)
            if status is None:
                _fail("unknown_leaf_execution", f"$.children[{node_id}]")
            executed, step_output = False, None
            if status.status in _LEAF_EVIDENCE_STATUSES:
                cached = evidence.get(workflow_id)
                if cached is None:
                    cached = await _leaf_step_evidence(workflow_id)
                    evidence[workflow_id] = cached
                executed, step_output = cached
            outcome = _terminal_outcome(
                node_id,
                state=status.status,
                output=status.output,
                executed=executed,
                step_output=step_output,
            )
            if outcome is not None:
                facts.append(outcome.to_wire())
        if facts or time.monotonic() >= deadline:
            return facts
        await asyncio.sleep(OBSERVE_POLL_INTERVAL_SEC)


@DBOS.workflow(name=RESEARCH_WORKFLOW_NAME, serialization_type=WorkflowSerializationFormat.PORTABLE)
async def run_research_workflow(payload: dict[str, Any]) -> dict[str, Any]:
    """父研究 workflow：启动就绪叶、消费完成事实、汇总叶事实。

    计划须在派发前已由服务层校验并保存（计划版 + 原生引用），本 workflow 只做
    严格解码与编排，不重复准入判定。

    语义边界：
    - 返回 ``converged=False`` 的汇总（暂停/等待/决定/未知）**不等于研究结束**，
      父 workflow 结束只表示本推进批次结束；终态事实由服务层投影，不由本层升级。
    - 叶事实在叶执行内部保存（应用侧），父观察被取消/失败不丢失已完成叶成果。
    - 观察预算耗尽转入 DBOS 持久等待（:func:`wait_next_observation_window`）后
      继续观察；父不因超预算正常结束，超预算完成的长叶仍推进其后继（R02）。
      观察异常上抛由恢复机制重接；不伪造叶终态。
    - 重放顺序（B1）：启动/观察操作全部由父协程按计划顺序发出，完成集合由
      ``agent_leaf_observe`` step 的记录还原；子 workflow ID 按身份固定
      （``leaf_workflow_id``）作为去重、观察复接与重试轮隔离。
    """

    execution_id, round_id, plan_digest, plan, preserved = decode_research_payload(payload)
    idle_limit = max(1, round(CHILD_OBSERVE_TIMEOUT_SEC / OBSERVE_SLICE_SEC))

    async def start_leaf(node: PlanNodeSpec) -> str:
        leaf_payload = build_leaf_payload(
            execution_id=execution_id,
            round_id=round_id,
            plan_digest=plan_digest,
            node=node,
        )
        with SetWorkflowID(
            leaf_workflow_id(execution_id=execution_id, round_id=round_id, node_id=node.node_id),
            workflow_id_reuse_policy="return-existing",
        ):
            handle = await DBOS.start_workflow_async(run_leaf_workflow, leaf_payload)
        return handle.get_workflow_id()

    async def observe_leaves(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        wires = await observe_leaf_children(
            [{"node_id": node_id, "workflow_id": workflow_id} for node_id, workflow_id in children],
            OBSERVE_SLICE_SEC,
        )
        outcomes: dict[str, NodeOutcome] = {}
        for wire in wires:
            outcome = decode_node_outcome(wire)
            outcomes[outcome.node_id] = outcome
        return outcomes

    summary = await drive_leaf_dag(
        plan,
        start_leaf,
        observe_leaves,
        preserved=preserved,
        idle_observation_limit=idle_limit,
        wait_checkpoint=wait_next_observation_window,
    )
    return summary.to_wire()


def build_research_payload(
    plan: PlanSpecProposal,
    *,
    execution_id: str,
    round_id: str,
    plan_digest: str,
    preserved: tuple[NodeOutcome, ...] = (),
) -> dict[str, Any]:
    """构造父 workflow 载荷（与保存的计划版同源内容；PORTABLE JSON 可序列化）。

    ``preserved`` 为重试轮冻结的旧轮成功前置事实（首轮为空）。空计划在派发前
    拒绝（F03：不制造空任务树；DBOS 侧无法给出可回执的准入拒绝）。
    """

    if not plan.nodes:
        _fail("empty_plan", "$.nodes")
    _validate_preserved(preserved, scope_node_ids={node.node_id for node in plan.nodes})
    return {
        _PAYLOAD_VERSION_KEY: PAYLOAD_VERSION,
        "execution_id": _require_str(execution_id, "$.execution_id"),
        "round_id": _require_str(round_id, "$.round_id"),
        "plan_digest": _require_digest(plan_digest, "$.plan_digest"),
        "nodes": [_node_to_wire(node) for node in plan.nodes],
        "preserved_outcomes": [outcome.to_wire() for outcome in preserved],
    }


def _validate_preserved(outcomes: tuple[NodeOutcome, ...], *, scope_node_ids: set[str]) -> None:
    """派发前校验保留前置：必须是成功事实且不在本轮范围内。

    与 :meth:`LeafDagScheduler.seed_preserved` 同组判据，但**前移到派发前**
    （HANDOFF §7.2：保存后才派发；非法载荷不得以 workflow ERROR 形式暴露）。
    """

    for outcome in outcomes:
        if outcome.state != "complete":
            _fail("invalid_preserved_outcome", f"$.preserved_outcomes[{outcome.node_id}]")
        if outcome.node_id in scope_node_ids:
            _fail("preserved_node_in_retry_scope", f"$.preserved_outcomes[{outcome.node_id}]")
    seen: set[str] = set()
    for outcome in outcomes:
        if outcome.node_id in seen:
            _fail("duplicate_preserved_outcome", f"$.preserved_outcomes[{outcome.node_id}]")
        seen.add(outcome.node_id)


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
    round_id: str,
    plan_digest: str,
    targets: tuple[str, ...],
    preserved_outcomes: tuple[NodeOutcome, ...],
) -> tuple[dict[str, Any], RetryScope]:
    """构造重试轮父载荷：``nodes`` 为范围内节点，``preserved_outcomes`` 为旧轮
    成功前置。

    派发前校验（HANDOFF §7.2：保存后才派发）：保留前置须为成功事实且不得落在
    本轮范围内；范围外前置必须由 ``preserved_outcomes`` 全部覆盖。均显式拒绝
    （不以 workflow ERROR 暴露非法载荷、不静默不满足依赖）。
    """

    scope = compute_retry_scope(plan, targets=targets)
    redo_ids = set(scope.redo)
    _validate_preserved(preserved_outcomes, scope_node_ids=redo_ids)
    redo_nodes = tuple(node for node in plan.nodes if node.node_id in redo_ids)
    preserved_ids = {outcome.node_id for outcome in preserved_outcomes}
    for node in redo_nodes:
        for dependency in node.depends_on:
            if dependency not in redo_ids and dependency not in preserved_ids:
                _fail("missing_preserved_predecessor", f"$.nodes[{node.node_id}].depends_on")
    payload = build_research_payload(
        PlanSpecProposal(nodes=redo_nodes),
        execution_id=execution_id,
        round_id=round_id,
        plan_digest=plan_digest,
        preserved=preserved_outcomes,
    )
    return payload, scope


__all__ = [
    "IDLE_WAIT_INTERVAL_SEC",
    "LEAF_EXECUTE_STEP_NAME",
    "LEAF_OBSERVE_STEP_NAME",
    "LEAF_WORKFLOW_NAME",
    "PAYLOAD_VERSION",
    "RESEARCH_WORKFLOW_NAME",
    "LeafDagScheduler",
    "LeafExecutionContext",
    "LeafObserve",
    "LeafRunner",
    "LeafRuntimeRegistry",
    "LeafStart",
    "LeafWaitCheckpoint",
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
    "leaf_workflow_id",
    "observe_leaf_children",
    "run_leaf_workflow",
    "run_research_workflow",
    "wait_next_observation_window",
]
