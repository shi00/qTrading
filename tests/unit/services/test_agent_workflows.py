"""一层叶 DAG 调度与原生 workflow 骨架（DD F04 / SQ-04；HANDOFF §7.2/§8.3，D-02）。

覆盖：
- 就绪判定：仅「全部前置成功（complete）」的未启动叶就绪；失败/终止/partial
  只阻相关后继、独立分支继续（不借成功兄弟补答案）；
- 暂停/等待/unknown 效果不自动结束父（不伪造终态、不启动后继）；
- ``closed_unknown`` 为不可恢复终态（F10）：结案收口不保持可恢复占位、单独汇总、
  阻其下游、迟到事实不复活；
- 汇总区分叶事实与目标完成（``leaves_all_succeeded`` 不等于目标已覆盖）；
- 事件驱动推进：B 结束即启动 D，不按固定批次 gather；
- 观察预算耗尽经持久等待接续：超预算完成的长叶仍推进后继且后继只启动一次；
- 取消与未判定异常向上传播（R2/R21：不伪造 failed）；
- 封闭 wire 与叶执行器登记的明确拒绝语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    AgentValidationError,
    ConsumptionFact,
    ConsumptionState,
    PlanNodeSpec,
    PlanSpecProposal,
    plan_spec_digest,
)
from dbos import DBOS
from services.agent_service.workflows import (
    ANSWER_MESSAGE_TOPIC,
    IDLE_WAIT_INTERVAL_SEC,
    LEAF_EXECUTE_STEP_NAME,
    ActualInputBinderRegistry,
    ActualInputBinding,
    AnswerConsumerRegistry,
    LeafDagScheduler,
    LeafExecutionContext,
    LeafIdentity,
    LeafRuntimeRegistry,
    LeafTicketGateRegistry,
    LeafTicketLease,
    NodeOutcome,
    ParentBindIdentity,
    PlanAdmission,
    PlanRunSummary,
    _leaf_step_evidence,
    _terminal_outcome,
    admit_research_plan,
    build_leaf_payload,
    build_parent_bind_request,
    build_research_payload,
    build_retry_payload,
    claim_leaf_ticket,
    compute_actual_input_manifest,
    compute_retry_scope,
    consume_answer_step,
    decode_actual_input_binding,
    decode_answer_message,
    decode_binding_identity,
    decode_leaf_payload,
    decode_node_outcome,
    decode_plan_admission,
    decode_research_payload,
    decode_research_payload_full,
    drive_leaf_dag,
    leaf_ticket_slot_id,
    leaf_workflow_id,
    observe_leaf_children,
    receive_pending_answers,
    record_plan_admission,
    release_leaf_ticket,
    validate_actual_input_binding,
    wait_next_observation_window,
)

pytestmark = pytest.mark.unit


def _node(node_id: str, depends_on: tuple[str, ...] = ()) -> PlanNodeSpec:
    return PlanNodeSpec(node_id=node_id, target=f"目标-{node_id}", role_key="researcher", depends_on=depends_on)


def _plan(*nodes: PlanNodeSpec) -> PlanSpecProposal:
    return PlanSpecProposal(nodes=nodes)


def _outcome(node_id: str, state: str = "complete", **kwargs: Any) -> NodeOutcome:
    if state == "complete" and "artifact_ref" not in kwargs:
        kwargs["artifact_ref"] = f"art-{node_id}@1"
    return NodeOutcome(node_id=node_id, state=state, **kwargs)


async def _wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """轮询等待谓词成立（事件驱动测试，避免固定 sleep 竞态）。"""

    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met within timeout")
        await asyncio.sleep(0)


class _DagHarness:
    """父协程顺序驱动的叶模拟（与生产一致：启动/观察由父顺序调用）。

    ``start`` 只登记启动并模拟叶自身并发执行（叶是独立子 workflow，不是父体
    并发操作）；``observe`` 返回已完成叶的终态事实，模拟 DBOS 观察 step。
    """

    def __init__(self) -> None:
        self.started: list[str] = []
        self.predecessors: dict[str, tuple[tuple[str, str], ...]] = {}
        self.states: dict[str, str] = {}
        self.active = 0
        self.max_concurrent = 0
        self._gates: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def gate(self, node_id: str) -> asyncio.Event:
        return self._gates.setdefault(node_id, asyncio.Event())

    async def start(self, node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        node_id = node.node_id
        self.started.append(node_id)
        self.predecessors[node_id] = predecessors
        self.active += 1
        self.max_concurrent = max(self.max_concurrent, self.active)

        async def leaf() -> None:
            try:
                await self.gate(node_id).wait()
            finally:
                self.active -= 1

        self._tasks[node_id] = asyncio.ensure_future(leaf())
        return f"wf:{node_id}"

    async def observe(self, children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        for _ in range(3):
            await asyncio.sleep(0)
        facts: dict[str, NodeOutcome] = {}
        for node_id, key in children:
            assert key == f"wf:{node_id}"
            task = self._tasks[node_id]
            if task.done():
                facts[node_id] = _outcome(node_id, self.states.get(node_id, "complete"))
        return facts


# ---------------------------------------------------------------------------
# 就绪与阻塞语义
# ---------------------------------------------------------------------------


def test_initial_ready_only_dependency_free_leaves() -> None:
    """初始就绪只有无前置叶；依赖未满足的叶不就绪。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b"), _node("c", ("a", "b"))))
    assert scheduler.ready() == ("a", "b")


def test_successor_ready_only_after_all_prerequisites_complete() -> None:
    """多前置：仅当全部前置 complete 才就绪（A 成功、B 未完成 → C 不就绪）。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b"), _node("c", ("a", "b"))))
    scheduler.start("a")
    scheduler.record(_outcome("a"))
    assert scheduler.ready() == ("b",)
    scheduler.start("b")
    scheduler.record(_outcome("b"))
    assert scheduler.ready() == ("c",)


def test_failure_blocks_only_related_successors() -> None:
    """失败只阻相关后继：独立 B 不受影响，C（依赖 A）被阻塞。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b"), _node("c", ("a",))))
    scheduler.start("a")
    scheduler.start("b")
    scheduler.record(_outcome("a", "failed", error_code="model_error"))
    assert scheduler.ready() == ()
    scheduler.record(_outcome("b"))
    summary = scheduler.summary()
    assert summary.failed == ("a",)
    assert summary.succeeded == ("b",)
    assert summary.blocked == ("c",)
    assert summary.converged is True


def test_block_propagates_transitively() -> None:
    """阻塞沿依赖传递：A 失败 → C 阻塞 → D（依赖 C）阻塞。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("c", ("a",)), _node("d", ("c",))))
    scheduler.start("a")
    scheduler.record(_outcome("a", "withdrawn"))
    summary = scheduler.summary()
    assert summary.blocked == ("c", "d")
    assert summary.converged is True


def test_partial_prerequisite_does_not_unlock_successor() -> None:
    """partial 不是「适用成功前置」：后继阻塞而不得静默启动。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b", ("a",))))
    scheduler.start("a")
    scheduler.record(_outcome("a", "partial"))
    assert scheduler.ready() == ()
    summary = scheduler.summary()
    assert summary.partial == ("a",)
    assert summary.blocked == ("b",)
    assert summary.leaves_all_succeeded is False


def test_non_terminal_outcome_keeps_pending_without_fabricating() -> None:
    """暂停/未知效果不自动结束：节点保持 pending、后继不启动、父不收敛。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b", ("a",))))
    scheduler.start("a")
    scheduler.record(_outcome("a", "paused"))
    assert scheduler.ready() == ()
    summary = scheduler.summary()
    assert summary.pending == ("a", "b")
    assert summary.converged is False


@pytest.mark.parametrize("state", ["queued", "running", "waiting_dependency", "waiting_user", "pausing", "ending"])
def test_waiting_states_are_non_terminal(state: str) -> None:
    """等待/收尾中的状态不推进后继、不算收敛。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b", ("a",))))
    scheduler.start("a")
    scheduler.record(_outcome("a", state))
    assert scheduler.converged() is False
    assert scheduler.summary().pending == ("a", "b")


def test_unknown_keeps_pending_while_closed_unknown_converges() -> None:
    """unknown 效果未知，不自动结束本次执行；closed_unknown 已结案才算收敛（F10）。"""
    scheduler = LeafDagScheduler(_plan(_node("a")))
    scheduler.start("a")
    scheduler.record(_outcome("a", "unknown"))
    assert scheduler.converged() is False
    assert scheduler.summary().pending == ("a",)
    scheduler_two = LeafDagScheduler(_plan(_node("b")))
    scheduler_two.start("b")
    scheduler_two.record(_outcome("b", "closed_unknown"))
    assert scheduler_two.converged() is True
    assert scheduler_two.summary().pending == ()


def test_closed_unknown_is_terminal_but_not_success_or_failure() -> None:
    """closed_unknown 收口为终态：单列汇总、不并入 failed/ended/pending（F10）。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b")))
    scheduler.start("a")
    scheduler.start("b")
    scheduler.record(_outcome("a", "closed_unknown", error_code="outcome_unknown", artifact_ref="artifact_a_1"))
    scheduler.record(_outcome("b"))
    summary = scheduler.summary()
    assert summary.closed_unknown == ("a",)
    assert summary.failed == ()
    assert summary.ended == ()
    assert summary.pending == ()
    assert summary.succeeded == ("b",)
    assert summary.leaves_all_succeeded is False
    assert summary.converged is True
    assert summary.to_wire()["closed_unknown"] == ["a"]
    # 未知关联保留：结案叶的成果引用仍随事实回报，不被丢弃或改写。
    assert summary.outcomes[0] == _outcome(
        "a", "closed_unknown", error_code="outcome_unknown", artifact_ref="artifact_a_1"
    )


def test_closed_unknown_blocks_dependents_with_unknown_reason() -> None:
    """closed_unknown 不是适用成功前置：后继阻塞并保留上游未知关联。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b", ("a",)), _node("c", ("b",))))
    scheduler.start("a")
    scheduler.record(_outcome("a", "closed_unknown"))
    assert scheduler.ready() == ()
    summary = scheduler.summary()
    assert summary.blocked == ("b", "c")
    assert summary.block_reasons[0] == ("b", "upstream_closed_unknown:a")
    assert summary.converged is True


def test_late_fact_does_not_resurrect_closed_unknown() -> None:
    """迟到事实不得复活已结案的 closed_unknown：同一事实幂等、其余冲突。"""
    scheduler = LeafDagScheduler(_plan(_node("a")))
    scheduler.start("a")
    scheduler.record(_outcome("a", "closed_unknown"))
    scheduler.record(_outcome("a", "closed_unknown"))  # 观察重投幂等
    with pytest.raises(AgentValidationError) as excinfo:
        scheduler.record(_outcome("a", "complete"))
    assert excinfo.value.code == "node_outcome_conflict"
    with pytest.raises(AgentValidationError) as excinfo_running:
        scheduler.record(_outcome("a", "running"))
    assert excinfo_running.value.code == "node_outcome_conflict"
    summary = scheduler.summary()
    assert summary.closed_unknown == ("a",)
    assert summary.succeeded == ()
    assert summary.pending == ()


def test_start_requires_ready_node() -> None:
    """未就绪节点不得启动（调度不越过依赖）。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b", ("a",))))
    with pytest.raises(AgentValidationError) as excinfo:
        scheduler.start("b")
    assert excinfo.value.code == "node_not_ready"


def test_summary_does_not_claim_goal_coverage() -> None:
    """汇总只报叶事实：全部成功也不宣称目标覆盖（不能以成功叶数量代替）。"""
    scheduler = LeafDagScheduler(_plan(_node("a"), _node("b")))
    scheduler.start("a")
    scheduler.start("b")
    scheduler.record(_outcome("a"))
    scheduler.record(_outcome("b"))
    summary = scheduler.summary()
    assert isinstance(summary, PlanRunSummary)
    assert summary.leaves_all_succeeded is True
    assert summary.converged is True
    assert not hasattr(summary, "goal_coverage")


# ---------------------------------------------------------------------------
# 事件驱动推进（不按批次 gather）
# ---------------------------------------------------------------------------


def test_drive_starts_newly_ready_leaf_immediately() -> None:
    """B 完成即启动 D（依赖 B），不等待独立的 A（A/B→C，B→D）。"""
    plan = _plan(_node("a"), _node("b"), _node("c", ("a", "b")), _node("d", ("b",)))
    harness = _DagHarness()

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe))
        await _wait_until(lambda: harness.started == ["a", "b"])
        harness.gate("b").set()
        await _wait_until(lambda: "d" in harness.started)
        # C 必须仍等待 A：不因 B 完成而提前
        assert "c" not in harness.started
        harness.gate("a").set()
        await _wait_until(lambda: "c" in harness.started)
        for node_id in ("c", "d"):
            harness.gate(node_id).set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.started == ["a", "b", "d", "c"]
    assert summary.succeeded == ("a", "b", "c", "d")
    assert summary.converged is True


def test_drive_runs_independent_leaves_concurrently() -> None:
    """独立叶并发执行（不串行排队）。"""
    plan = _plan(_node("a"), _node("b"))
    harness = _DagHarness()

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe))
        await _wait_until(lambda: len(harness.started) == 2)
        harness.gate("a").set()
        harness.gate("b").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.max_concurrent == 2
    assert summary.succeeded == ("a", "b")


def test_drive_failure_keeps_independent_branch_running() -> None:
    """A 失败不阻独立 B；C 被阻塞并以事实记录（不补答）。"""
    plan = _plan(_node("a"), _node("b"), _node("c", ("a",)))
    harness = _DagHarness()
    harness.states["a"] = "failed"

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe))
        await _wait_until(lambda: len(harness.started) == 2)
        harness.gate("a").set()
        await _wait_until(lambda: harness.active == 1)
        harness.gate("b").set()
        return await task

    summary = asyncio.run(scenario())
    assert summary.failed == ("a",)
    assert summary.succeeded == ("b",)
    assert summary.blocked == ("c",)
    assert summary.converged is True
    assert summary.leaves_all_succeeded is False


def test_drive_non_terminal_leaf_does_not_fabricate_completion() -> None:
    """叶暂停：驱动结束但不伪造成功/失败，父不收敛。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    harness = _DagHarness()
    harness.states["a"] = "waiting_user"

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe))
        await _wait_until(lambda: harness.started == ["a"])
        harness.gate("a").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.started == ["a"]
    assert summary.pending == ("a", "b")
    assert summary.converged is False
    assert summary.succeeded == ()


def test_drive_closed_unknown_closes_branch_without_starting_successor() -> None:
    """叶结案未知：驱动按结案收口、不启动后继、不伪造成功/失败（F10）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    harness = _DagHarness()
    harness.states["a"] = "closed_unknown"

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe))
        await _wait_until(lambda: harness.started == ["a"])
        harness.gate("a").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.started == ["a"]
    assert summary.closed_unknown == ("a",)
    assert summary.blocked == ("b",)
    assert summary.pending == ()
    assert summary.failed == ()
    assert summary.succeeded == ()
    assert summary.converged is True


def test_drive_propagates_cancellation() -> None:
    """取消向上传播（R2：不吞 CancelledError、不转成 failed 事实）。"""
    plan = _plan(_node("a"))

    async def cancelled(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        raise asyncio.CancelledError

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await drive_leaf_dag(plan, cancelled, observe)
        assert excinfo.type is asyncio.CancelledError

    asyncio.run(scenario())


def test_drive_propagates_unexpected_exception() -> None:
    """未判定异常向上传播（不伪造 failed：未知不等于失败）。"""
    plan = _plan(_node("a"))

    async def broken(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        raise ValueError("infra boom")

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    async def scenario() -> None:
        with pytest.raises(ValueError) as excinfo:
            await drive_leaf_dag(plan, broken, observe)
        assert str(excinfo.value) == "infra boom"

    asyncio.run(scenario())


# ---------------------------------------------------------------------------
# 封闭 wire 与执行器登记
# ---------------------------------------------------------------------------


def test_node_outcome_wire_roundtrip() -> None:
    """叶结果 wire 往返（四键封闭）。"""
    outcome = NodeOutcome(node_id="a", state="complete", error_code=None, artifact_ref="artifact_a_1")
    assert decode_node_outcome(outcome.to_wire()) == outcome


def test_node_outcome_rejects_unknown_state() -> None:
    """叶结果状态必须是 display_state 冻结值（不自造词表）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        decode_node_outcome({"node_id": "a", "state": "success", "error_code": None, "artifact_ref": None})
    assert excinfo.value.code == "invalid_display_state"


def test_node_outcome_rejects_extra_key() -> None:
    """叶结果额外键拒绝。"""
    with pytest.raises(AgentValidationError) as excinfo:
        decode_node_outcome({"node_id": "a", "state": "complete", "error_code": None, "artifact_ref": None, "x": 1})
    assert excinfo.value.code == "unknown_field"


def test_leaf_payload_roundtrip_and_strict_keys() -> None:
    """叶载荷往返（含轮次身份）；额外键拒绝（封闭 wire）。"""
    payload = build_leaf_payload(
        execution_id="exec-1", round_id="round-1", plan_digest="b" * 64, node=_node("a", ("b",))
    )
    context = decode_leaf_payload(payload)
    assert (context.execution_id, context.round_id, context.plan_digest) == ("exec-1", "round-1", "b" * 64)
    assert context.node.node_id == "a"
    assert context.node.depends_on == ("b",)
    with pytest.raises(AgentValidationError) as excinfo:
        decode_leaf_payload({**payload, "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_leaf_workflow_id_is_identity_based_not_order_based() -> None:
    """B1 回归：叶 workflow 身份按 execution/round/node 固定，与启动顺序无关。"""
    first = leaf_workflow_id(execution_id="exec-1", round_id="round-1", node_id="c")
    same = leaf_workflow_id(execution_id="exec-1", round_id="round-1", node_id="c")
    other_node = leaf_workflow_id(execution_id="exec-1", round_id="round-1", node_id="d")
    other_round = leaf_workflow_id(execution_id="exec-1", round_id="round-2", node_id="c")
    assert first == same
    assert first != other_node
    assert first != other_round
    assert first.startswith("agent_leaf:exec-1:round-1:")


def test_leaf_workflow_id_segments_are_injective() -> None:
    """B1/N1 回归：分隔符出现在身份段内时不产生碰撞（转义后单射）。"""
    left = leaf_workflow_id(execution_id="a:b", round_id="c", node_id="d")
    right = leaf_workflow_id(execution_id="a", round_id="b:c", node_id="d")
    percent = leaf_workflow_id(execution_id="a%3Ab", round_id="c", node_id="d")
    assert left != right
    assert left != percent


def test_drive_issues_start_and_observe_in_parent_order() -> None:
    """B1 重放面回归：启动按计划顺序、观察按运行集合，均由父协程顺序发出。"""
    plan = _plan(_node("a"), _node("b"))
    harness = _DagHarness()
    calls: list[tuple[str, tuple[str, ...]]] = []

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        calls.append(("start", (node.node_id,)))
        return await harness.start(node, predecessors)

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        calls.append(("observe", tuple(node_id for node_id, _ in children)))
        return await harness.observe(children)

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, start, observe))
        await _wait_until(lambda: harness.started == ["a", "b"])
        harness.gate("a").set()
        harness.gate("b").set()
        return await task

    summary = asyncio.run(scenario())
    assert calls[0] == ("start", ("a",))
    assert calls[1] == ("start", ("b",))
    assert ("observe", ("a", "b")) in calls
    # 观察之后不得再出现「父协程并发启动」：所有启动都发生在观察之前或事实之后
    assert summary.succeeded == ("a", "b")


def test_drive_observation_budget_only_stops_observation() -> None:
    """未提供持久等待接续者时，达预算只停观察：converged=False、不伪造叶终态（§8.3/R21）。"""
    plan = _plan(_node("a"))

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        return "wf:a"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    summary = asyncio.run(drive_leaf_dag(plan, start, observe, idle_observation_limit=3))
    assert summary.converged is False
    assert summary.pending == ("a",)
    assert summary.succeeded == ()


def test_drive_budget_exhaustion_resumes_with_persistent_wait() -> None:
    """预算耗尽不结束父：持久等待后续观察，超预算完成的长叶仍推进后继且后继只启动一次（R02）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    started: list[str] = []
    observation_rounds = 0
    waits: list[int] = []

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        started.append(node.node_id)
        return f"wf:{node.node_id}"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        nonlocal observation_rounds
        observation_rounds += 1
        node_ids = {node_id for node_id, _ in children}
        if observation_rounds == 7 and "a" in node_ids:
            return {"a": _outcome("a")}
        if observation_rounds == 8 and "b" in node_ids:
            return {"b": _outcome("b")}
        return {}

    async def wait_checkpoint() -> bool:
        waits.append(observation_rounds)
        return True

    summary = asyncio.run(
        drive_leaf_dag(
            plan,
            start,
            observe,
            idle_observation_limit=3,
            wait_checkpoint=wait_checkpoint,
        )
    )
    assert waits == [3, 6]  # 两个预算窗口耗尽各持久等待一次
    assert started == ["a", "b"]  # B 只在 A 完成后启动一次，不依赖新消息或重提请求
    assert summary.succeeded == ("a", "b")
    assert summary.converged is True


def test_drive_wait_checkpoint_can_end_batch_without_fake_terminal() -> None:
    """负例：接续者拒绝继续时结束批次，保持 converged=False、不伪造终态（R21）。"""
    plan = _plan(_node("a"))

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        return "wf:a"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    async def wait_checkpoint() -> bool:
        return False

    summary = asyncio.run(
        drive_leaf_dag(plan, start, observe, idle_observation_limit=2, wait_checkpoint=wait_checkpoint)
    )
    assert summary.converged is False
    assert summary.pending == ("a",)
    assert summary.succeeded == ()


def test_drive_wait_checkpoint_cancellation_propagates() -> None:
    """取消在持久等待点继续传播（R2：不吞 CancelledError、不转成终态事实）。"""
    plan = _plan(_node("a"))

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        return "wf:a"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    calls: list[str] = []

    async def wait_checkpoint() -> bool:
        calls.append("wait")
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError) as excinfo:
        asyncio.run(drive_leaf_dag(plan, start, observe, idle_observation_limit=1, wait_checkpoint=wait_checkpoint))
    assert calls == ["wait"]  # 取消在持久等待点传出，未被吞并成终态事实
    assert isinstance(excinfo.value, asyncio.CancelledError)


def test_wait_next_observation_window_uses_durable_sleep_interval() -> None:
    """持久等待检查点按固定间隔 sleep 一次并返回继续（DBOS 原生可恢复等待的间隔面）。"""
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    assert asyncio.run(wait_next_observation_window(sleep=fake_sleep)) is True
    assert slept == [IDLE_WAIT_INTERVAL_SEC]


def test_drive_rejects_outcome_for_unstarted_node() -> None:
    """未启动节点的完成事实不得计入本轮成果（身份核验）。"""
    plan = _plan(_node("a"))

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        return "wf:a"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {"ghost": _outcome("ghost")}

    with pytest.raises(AgentValidationError) as excinfo:
        asyncio.run(drive_leaf_dag(plan, start, observe))
    assert excinfo.value.code == "outcome_unknown_node"


def test_terminal_outcome_projects_success_wire() -> None:
    """子 workflow 成功：返回值投影为叶终态事实（身份核验通过）。"""
    outcome = _terminal_outcome("a", state="SUCCESS", output=_outcome("a", artifact_ref="art-1").to_wire())
    assert outcome == _outcome("a", artifact_ref="art-1")


def test_terminal_outcome_rejects_foreign_node() -> None:
    """返回事实的节点身份与观察目标不一致即拒绝（不错记成果）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        _terminal_outcome("a", state="SUCCESS", output=_outcome("b").to_wire())
    assert excinfo.value.code == "outcome_identity_mismatch"


@pytest.mark.parametrize(
    ("state", "error_code"),
    [
        ("ERROR", "leaf_workflow_error"),
        ("MAX_RECOVERY_ATTEMPTS_EXCEEDED", "leaf_workflow_recovery_exhausted"),
    ],
)
def test_terminal_outcome_maps_failure_statuses(state: str, error_code: str) -> None:
    """未判定异常/恢复耗尽 → failed 通用错误码；原生状态分别标注（明细留 DBOS，R9）。"""
    outcome = _terminal_outcome("a", state=state, output=None)
    assert outcome == NodeOutcome(node_id="a", state="failed", error_code=error_code)


def test_terminal_outcome_maps_cancelled_without_execution_to_unknown() -> None:
    """无任何执行记录的取消（如启动隔离取消）→ unknown（未核退出），不伪装成 ended。"""
    outcome = _terminal_outcome("a", state="CANCELLED", output=None)
    assert outcome == NodeOutcome(
        node_id="a",
        state="unknown",
        error_code="leaf_workflow_exit_unverified",
        exit_verified=False,
    )


def test_terminal_outcome_projects_outcome_recorded_after_cancel() -> None:
    """取消时 IO 在途、随后落盘的真实成果优先投影，不被 ended 覆盖。"""
    outcome = _terminal_outcome(
        "a",
        state="CANCELLED",
        output=None,
        executed=True,
        step_output=_outcome("a", artifact_ref="art-1").to_wire(),
    )
    assert outcome == _outcome("a", artifact_ref="art-1")


def test_terminal_outcome_projects_partial_outcome_on_error() -> None:
    """工作流级错误但叶执行已产出部分成果 → 投影 partial（不伪装成 failed）。"""
    outcome = _terminal_outcome(
        "a",
        state="ERROR",
        output=None,
        executed=True,
        step_output=_outcome("a", state="partial", artifact_ref="art-2").to_wire(),
    )
    assert outcome == _outcome("a", state="partial", artifact_ref="art-2")


def test_terminal_outcome_rejects_foreign_step_outcome() -> None:
    """落盘成果的节点身份与观察目标不一致即拒绝（不错记成果）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        _terminal_outcome(
            "a",
            state="CANCELLED",
            output=None,
            executed=True,
            step_output=_outcome("b").to_wire(),
        )
    assert excinfo.value.code == "outcome_identity_mismatch"


def test_terminal_outcome_cancelled_after_failed_step_is_failed() -> None:
    """已有执行记录但无成果（step 报错）→ failed，不把实际失败报成 ended。"""
    outcome = _terminal_outcome("a", state="CANCELLED", output=None, executed=True)
    assert outcome == NodeOutcome(node_id="a", state="failed", error_code="leaf_workflow_error")


def test_leaf_step_evidence_reads_execute_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """只认叶执行 step 的落盘记录；其它 step 不当作成果。"""

    async def fake_steps(workflow_id: str) -> list[dict[str, object]]:
        assert workflow_id == "wf:a"
        return [
            {"function_name": "other", "output": {"ignored": True}},
            {"function_name": LEAF_EXECUTE_STEP_NAME, "output": _outcome("a", state="partial").to_wire()},
        ]

    monkeypatch.setattr(DBOS, "list_workflow_steps_async", fake_steps)
    assert asyncio.run(_leaf_step_evidence("wf:a")) == (True, _outcome("a", state="partial").to_wire())


def test_leaf_step_evidence_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """无任何 step 落盘（未执行/在途）→ 实际退出与成果覆盖均未知。"""

    async def fake_steps(workflow_id: str) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(DBOS, "list_workflow_steps_async", fake_steps)
    assert asyncio.run(_leaf_step_evidence("wf:a")) == (False, None)


def test_observe_projects_recorded_outcome_after_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    """观察链路：取消时 IO 在途但执行记录已落盘，投影真实成果而非合成 ended。"""

    class _Status:
        status = "CANCELLED"
        output = None

    async def fake_status(workflow_id: str) -> _Status:
        return _Status()

    async def fake_steps(workflow_id: str) -> list[dict[str, object]]:
        return [{"function_name": LEAF_EXECUTE_STEP_NAME, "output": _outcome("a", artifact_ref="art-9").to_wire()}]

    monkeypatch.setattr(DBOS, "get_workflow_status_async", fake_status)
    monkeypatch.setattr(DBOS, "list_workflow_steps_async", fake_steps)
    wires = asyncio.run(observe_leaf_children([{"node_id": "a", "workflow_id": "wf:a"}], 0.0))
    assert wires == [_outcome("a", artifact_ref="art-9").to_wire()]


def test_observe_reports_cancelled_without_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """观察链路：无执行记录的取消 → unknown（未核退出），不宣称成果或已结束。"""

    class _Status:
        status = "CANCELLED"
        output = None

    async def fake_status(workflow_id: str) -> _Status:
        return _Status()

    async def fake_steps(workflow_id: str) -> list[dict[str, object]]:
        return []

    monkeypatch.setattr(DBOS, "get_workflow_status_async", fake_status)
    monkeypatch.setattr(DBOS, "list_workflow_steps_async", fake_steps)
    wires = asyncio.run(
        observe_leaf_children(
            [
                {"node_id": "a", "workflow_id": "wf:x"},
                {"node_id": "b", "workflow_id": "wf:x"},
            ],
            0.0,
        )
    )
    assert wires == [
        NodeOutcome(
            node_id="a", state="unknown", error_code="leaf_workflow_exit_unverified", exit_verified=False
        ).to_wire(),
        NodeOutcome(
            node_id="b", state="unknown", error_code="leaf_workflow_exit_unverified", exit_verified=False
        ).to_wire(),
    ]


@pytest.mark.parametrize("state", ["PENDING", "ENQUEUED", "DELAYED"])
def test_terminal_outcome_keeps_non_terminal_pending(state: str) -> None:
    """非终态不产生事实（不伪造结论，父不因未知收敛）。"""
    assert _terminal_outcome("a", state=state, output=None) is None


def test_research_payload_decode_nodes() -> None:
    """父载荷解码为已校验前保存的计划（nodes 严格解码；preserved 缺省为空）。"""
    payload = {
        "payload_version": 1,
        "execution_id": "exec-1",
        "round_id": "round-1",
        "plan_digest": "c" * 64,
        "nodes": [],
    }
    # 空 nodes 结构合法（是否为空白由派发前 validate_plan_spec_proposal/载荷构造拒绝）
    execution_id, round_id, plan_digest, plan, preserved = decode_research_payload(payload)
    assert (execution_id, round_id, plan_digest) == ("exec-1", "round-1", "c" * 64)
    assert plan.nodes == ()
    assert preserved == ()


def test_leaf_registry_requires_install() -> None:
    """未登记叶执行器明确拒绝（不默认放行）。"""
    LeafRuntimeRegistry._reset_singleton()
    with pytest.raises(RuntimeError) as excinfo:
        LeafRuntimeRegistry.require()
    assert "leaf runner not installed" in str(excinfo.value)


def test_leaf_registry_install_and_reset() -> None:
    """登记后返回同一执行器；复位清空（测试隔离）。"""

    async def runner(context: LeafExecutionContext) -> NodeOutcome:
        return _outcome(context.node.node_id)

    LeafRuntimeRegistry.install(runner)
    assert LeafRuntimeRegistry.require() is runner
    LeafRuntimeRegistry._reset_singleton()
    assert LeafRuntimeRegistry._instance is None


# ---------------------------------------------------------------------------
# 失败重试新轮与下游重做范围（F04/F05）
# ---------------------------------------------------------------------------


def test_retry_scope_includes_target_and_downstream() -> None:
    """A→B→C，重试 B：范围为 (B, C)，A 保留（不重做前置）。"""
    plan = _plan(_node("a"), _node("b", ("a",)), _node("c", ("b",)))
    scope = compute_retry_scope(plan, targets=("b",))
    assert scope.redo == ("b", "c")
    assert scope.preserved == ("a",)


def test_retry_scope_excludes_independent_branch() -> None:
    """独立 B 不在范围（A 失败只连带 C）；B 保留旧成果与费用。"""
    plan = _plan(_node("a"), _node("b"), _node("c", ("a", "b")))
    scope = compute_retry_scope(plan, targets=("a",))
    assert scope.redo == ("a", "c")
    assert scope.preserved == ("b",)


def test_retry_scope_unions_multiple_targets() -> None:
    """多个目标取并集（含各自传递下游）。"""
    plan = _plan(_node("a"), _node("b"), _node("c", ("a", "b")), _node("d", ("c",)))
    scope = compute_retry_scope(plan, targets=("a", "b"))
    assert scope.redo == ("a", "b", "c", "d")
    assert scope.preserved == ()


def test_retry_scope_rejects_unknown_or_empty_targets() -> None:
    """未知目标与空目标明确拒绝（不静默空重做）。"""
    plan = _plan(_node("a"))
    with pytest.raises(AgentValidationError) as unknown_exc:
        compute_retry_scope(plan, targets=("ghost",))
    assert unknown_exc.value.code == "unknown_node"
    with pytest.raises(AgentValidationError) as empty_exc:
        compute_retry_scope(plan, targets=())
    assert empty_exc.value.code == "invalid_targets"


def test_preserved_outcome_must_be_complete() -> None:
    """保留前置必须是成功事实（失败/未知不得作为「适用成功前置」）。"""
    scheduler = LeafDagScheduler(_plan(_node("b", ("a",))))
    with pytest.raises(AgentValidationError) as excinfo:
        scheduler.seed_preserved((_outcome("a", "failed"),))
    assert excinfo.value.code == "invalid_preserved_outcome"


def test_preserved_outcome_must_be_outside_scope() -> None:
    """本轮范围内节点不得同时作为保留前置（身份冲突显式拒绝）。"""
    scheduler = LeafDagScheduler(_plan(_node("b", ("a",))))
    with pytest.raises(AgentValidationError) as excinfo:
        scheduler.seed_preserved((_outcome("b"),))
    assert excinfo.value.code == "preserved_node_in_retry_scope"


def test_retry_drive_consumes_preserved_prerequisite() -> None:
    """重试轮：保留前置 A 已成功 → B 立即就绪，C 随后；不重做 A。"""
    plan = _plan(_node("b", ("a",)), _node("c", ("b",)))
    harness = _DagHarness()

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, harness.observe, preserved=(_outcome("a"),)))
        await _wait_until(lambda: harness.started == ["b"])
        harness.gate("b").set()
        await _wait_until(lambda: harness.started == ["b", "c"])
        harness.gate("c").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.started == ["b", "c"]
    assert summary.succeeded == ("b", "c")
    assert tuple(outcome.node_id for outcome in summary.outcomes) == ("b", "c")
    assert summary.converged is True


def test_retry_payload_carries_scope_and_preserved() -> None:
    """重试载荷：nodes 为范围内节点，preserved_outcomes 携带旧轮成功前置。"""
    plan = _plan(_node("a"), _node("b", ("a",)), _node("c", ("b",)))
    payload, scope = build_retry_payload(
        plan,
        execution_id="exec-2",
        round_id="round-2",
        plan_digest="d" * 64,
        targets=("b",),
        preserved_outcomes=(_outcome("a", artifact_ref="artifact_a_1"),),
    )
    assert scope.redo == ("b", "c")
    _execution_id, round_id, _digest, retry_plan, preserved = decode_research_payload(payload)
    assert round_id == "round-2"
    assert tuple(node.node_id for node in retry_plan.nodes) == ("b", "c")
    assert preserved == (_outcome("a", artifact_ref="artifact_a_1"),)


def test_retry_payload_reuses_original_binding_identity() -> None:
    """重试轮沿用原绑定：重试载荷与首轮携带同一冻结身份（不新造轮次身份）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    identity = _binding_identity()
    payload, _scope = build_retry_payload(
        plan,
        execution_id="exec-2",
        round_id="round-2",
        plan_digest="d" * 64,
        targets=("b",),
        preserved_outcomes=(_outcome("a", artifact_ref="artifact_a_1"),),
        binding_identity=identity,
    )
    assert decode_research_payload_full(payload).binding_identity == identity


def test_retry_payload_rejects_invalid_preserved_before_dispatch() -> None:
    """B2 回归：非法保留事实（非成功/落在范围内）在派发前拒绝，不留给 workflow ERROR。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    with pytest.raises(AgentValidationError) as failed_exc:
        build_retry_payload(
            plan,
            execution_id="exec-2",
            round_id="round-2",
            plan_digest="d" * 64,
            targets=("a",),
            preserved_outcomes=(_outcome("a", "failed"),),
        )
    assert failed_exc.value.code == "invalid_preserved_outcome"
    with pytest.raises(AgentValidationError) as scope_exc:
        build_retry_payload(
            plan,
            execution_id="exec-2",
            round_id="round-2",
            plan_digest="d" * 64,
            targets=("b",),
            preserved_outcomes=(_outcome("b"),),
        )
    assert scope_exc.value.code == "preserved_node_in_retry_scope"


def test_build_research_payload_rejects_empty_plan() -> None:
    """空计划派发前拒绝（F03 不制造空任务树）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        build_research_payload(
            PlanSpecProposal(nodes=()),
            execution_id="exec-1",
            round_id="round-1",
            plan_digest="e" * 64,
        )
    assert excinfo.value.code == "empty_plan"


def test_record_rejects_conflicting_terminal_outcome() -> None:
    """终态单调：已终态后再记不同事实被拒；同一事实重复登记幂等。"""
    scheduler = LeafDagScheduler(_plan(_node("a")))
    scheduler.start("a")
    scheduler.record(_outcome("a"))
    scheduler.record(_outcome("a"))  # 幂等：同终态事实重复投递
    with pytest.raises(AgentValidationError) as excinfo:
        scheduler.record(_outcome("a", "running"))
    assert excinfo.value.code == "node_outcome_conflict"


def test_validate_preserved_rejects_duplicates() -> None:
    """重复保留事实（同 node_id 两条）派发前拒绝（不留给 workflow 冲突）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    with pytest.raises(AgentValidationError) as excinfo:
        build_retry_payload(
            plan,
            execution_id="exec-2",
            round_id="round-2",
            plan_digest="d" * 64,
            targets=("b",),
            preserved_outcomes=(_outcome("a"), _outcome("a")),
        )
    assert excinfo.value.code == "duplicate_preserved_outcome"


def test_retry_payload_requires_preserved_predecessors() -> None:
    """范围外前置未被保留事实覆盖时明确拒绝（不静默不满足依赖）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    with pytest.raises(AgentValidationError) as excinfo:
        build_retry_payload(
            plan,
            execution_id="exec-2",
            round_id="round-2",
            plan_digest="d" * 64,
            targets=("b",),
            preserved_outcomes=(),
        )
    assert excinfo.value.code == "missing_preserved_predecessor"


# ---------------------------------------------------------------------------
# 计划准入与实际输入清单（R2-I09）
# ---------------------------------------------------------------------------

_ROLES: dict[str, frozenset[str]] = {"researcher": frozenset(), "analyst": frozenset()}


def _admit(plan: PlanSpecProposal, *, input_version_digest: str | None = None) -> PlanAdmission:
    return admit_research_plan(
        plan,
        plan_digest=plan_spec_digest(plan),
        role_capabilities=_ROLES,
        input_version_digest=input_version_digest,
    )


def test_admission_rejects_digest_detached_from_plan() -> None:
    """计划版摘要必须与内容一致（不得以旧摘要冒充新计划）。"""
    with pytest.raises(AgentValidationError) as excinfo:
        admit_research_plan(_plan(_node("a")), plan_digest="f" * 64, role_capabilities=_ROLES)
    assert excinfo.value.code == "plan_digest_mismatch"


def test_admission_requires_role_policy() -> None:
    """无角色能力策略即 fail-closed（不以提案自述能力自授权）。"""
    plan = _plan(_node("a"))
    with pytest.raises(AgentValidationError) as excinfo:
        admit_research_plan(plan, plan_digest=plan_spec_digest(plan), role_capabilities=None)
    assert excinfo.value.code == "plan_admission_policy_missing"


def test_admission_rejects_cyclic_graph() -> None:
    """环图零派发（非法结构不得进入叶编排）。"""
    plan = _plan(_node("a", ("b",)), _node("b", ("a",)))
    with pytest.raises(AgentValidationError) as excinfo:
        _admit(plan)
    assert excinfo.value.code == "plan_cycle"


def test_admission_rejects_unknown_role() -> None:
    """未知角色零派发（角色能力交集核验前移）。"""
    plan = _plan(PlanNodeSpec(node_id="a", target="目标-a", role_key="ghost"))
    with pytest.raises(AgentValidationError) as excinfo:
        _admit(plan)
    assert excinfo.value.code == "unknown_role"


def test_actual_input_manifest_is_deterministic_and_input_version_bound() -> None:
    """清单按拓扑序计算且绑定输入版本：版本变化即指纹变化（R2-I09）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    first = _admit(plan, input_version_digest="1" * 64)
    same = _admit(plan, input_version_digest="1" * 64)
    changed = _admit(plan, input_version_digest="2" * 64)
    assert first.manifest == same.manifest
    assert [record.node_id for record in first.manifest.nodes] == ["a", "b"]
    assert first.manifest.digest_for("b") != changed.manifest.digest_for("b")
    assert first.manifest.to_wire() == decode_plan_admission(first.to_wire()).manifest.to_wire()


def test_admission_step_roundtrip_and_strict_keys() -> None:
    """准入检查点回写规范化记录；坏记录拒绝（不伪装已准入）。"""
    admission = _admit(_plan(_node("a")), input_version_digest="1" * 64)
    assert asyncio.run(record_plan_admission(admission.to_wire())) == admission.to_wire()
    with pytest.raises(AgentValidationError) as excinfo:
        decode_plan_admission({**admission.to_wire(), "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_scheduler_adopts_preserved_only_when_input_version_matches() -> None:
    """保留前置仅在其输入指纹与当前清单一致时被采纳（R2-I09）。"""
    full = _plan(_node("a"), _node("b", ("a",)))
    expected = compute_actual_input_manifest(full, input_version_digest="1" * 64).digest_for("a")
    assert expected is not None
    redo = _plan(_node("b", ("a",)))

    admitted = admit_research_plan(
        redo,
        plan_digest="7" * 64,
        role_capabilities=_ROLES,
        preserved=(_outcome("a", input_digest=expected),),
        preserved_input_manifest={"a": expected},
        input_version_digest="1" * 64,
    )
    assert admitted.preserved_expected == {"a": expected}
    adopted = LeafDagScheduler(redo, manifest=admitted.manifest, preserved_expected=admitted.preserved_expected)
    adopted.seed_preserved((_outcome("a", input_digest=expected),))
    assert adopted.ready() == ("b",)
    assert ("a", expected) in adopted.summary().adopted_inputs

    recorded_stale = admit_research_plan(
        redo,
        plan_digest="7" * 64,
        role_capabilities=_ROLES,
        preserved=(_outcome("a", input_digest=expected),),
        preserved_input_manifest={"a": "9" * 64},
        input_version_digest="1" * 64,
    )
    stale = LeafDagScheduler(
        redo, manifest=recorded_stale.manifest, preserved_expected=recorded_stale.preserved_expected
    )
    stale.seed_preserved((_outcome("a", input_digest=expected),))
    summary = stale.summary()
    assert summary.stale_preserved == ("a",)
    assert summary.blocked == ("b",)
    assert summary.block_reasons[0] == ("b", "preserved_input_stale:a")
    assert summary.converged is True


def test_scheduler_does_not_reuse_preserved_without_input_evidence() -> None:
    """旧轮事实未回报输入指纹：不得当作「仍适用」复用，阻其下游。"""
    redo = _plan(_node("b", ("a",)))
    admission = admit_research_plan(
        redo,
        plan_digest="7" * 64,
        role_capabilities=_ROLES,
        preserved=(_outcome("a"),),
        input_version_digest="1" * 64,
    )
    assert admission.preserved_expected == {}
    scheduler = LeafDagScheduler(redo, manifest=admission.manifest, preserved_expected=admission.preserved_expected)
    scheduler.seed_preserved((_outcome("a"),))
    assert scheduler.ready() == ()
    assert scheduler.summary().stale_preserved == ("a",)


def test_scheduler_blocks_dependents_when_leaf_input_changed() -> None:
    """本轮叶回报的输入指纹与清单不符：其成果不得被下游采用，阻断下游。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    admission = _admit(plan, input_version_digest="1" * 64)
    scheduler = LeafDagScheduler(plan, manifest=admission.manifest)
    scheduler.start("a")
    scheduler.record(_outcome("a", input_digest="9" * 64))
    summary = scheduler.summary()
    assert summary.succeeded == ("a",)
    assert summary.blocked == ("b",)
    assert summary.block_reasons[0] == ("b", "upstream_input_changed:a")


def test_summary_records_adopted_input_versions() -> None:
    """汇总记录本次实际采用的输入版本（已启动叶 + 已采纳保留前置）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    admission = _admit(plan, input_version_digest="1" * 64)
    scheduler = LeafDagScheduler(plan, manifest=admission.manifest)
    scheduler.start("a")
    scheduler.record(_outcome("a", input_digest=admission.manifest.digest_for("a")))
    scheduler.start("b")
    adopted = dict(scheduler.summary().adopted_inputs)
    assert set(adopted) == {"a", "b"}
    assert adopted["b"] == admission.manifest.digest_for("b")


def test_research_payload_carries_admission_inputs_and_rejects_unknown_keys() -> None:
    """父载荷带角色策略与输入版本指纹（可选键）；未知键仍拒绝。"""
    plan = _plan(_node("a"))
    payload = build_research_payload(
        plan,
        execution_id="exec-1",
        round_id="round-1",
        plan_digest=plan_spec_digest(plan),
        role_capabilities=_ROLES,
        input_version_digest="1" * 64,
    )
    decoded = decode_research_payload_full(payload)
    assert decoded.role_capabilities == _ROLES
    assert decoded.input_version_digest == "1" * 64
    with pytest.raises(AgentValidationError) as excinfo:
        decode_research_payload_full({**payload, "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_retry_payload_rejects_stale_preserved_when_input_version_known() -> None:
    """输入版本已核定时，陈旧保留前置在派发前拒绝（不以旧成果构造新轮）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    expected = compute_actual_input_manifest(plan, input_version_digest="1" * 64).digest_for("a")
    assert expected is not None
    with pytest.raises(AgentValidationError) as excinfo:
        build_retry_payload(
            plan,
            execution_id="exec-2",
            round_id="round-2",
            plan_digest=plan_spec_digest(plan),
            targets=("b",),
            preserved_outcomes=(_outcome("a", input_digest="9" * 64),),
            input_version_digest="1" * 64,
        )
    assert excinfo.value.code == "preserved_input_stale"
    payload, _scope = build_retry_payload(
        plan,
        execution_id="exec-2",
        round_id="round-2",
        plan_digest=plan_spec_digest(plan),
        targets=("b",),
        preserved_outcomes=(_outcome("a", input_digest=expected),),
        input_version_digest="1" * 64,
    )
    assert decode_research_payload(payload)[4][0].input_digest == expected


def test_node_outcome_accepts_legacy_four_key_wire() -> None:
    """旧四键叶结果仍可解码（input_digest 缺省为 None，不伪造）。"""
    legacy = {"node_id": "a", "state": "complete", "error_code": None, "artifact_ref": None}
    assert decode_node_outcome(legacy).input_digest is None


def test_leaf_payload_carries_input_digest() -> None:
    """叶载荷携带本次采用的输入指纹（可选键）。"""
    payload = build_leaf_payload(
        execution_id="exec-1", round_id="round-1", plan_digest="b" * 64, node=_node("a"), input_digest="1" * 64
    )
    assert decode_leaf_payload(payload).input_digest == "1" * 64
    without = build_leaf_payload(execution_id="exec-1", round_id="round-1", plan_digest="b" * 64, node=_node("a"))
    assert decode_leaf_payload(without).input_digest is None


# ---------------------------------------------------------------------------
# 实际输入绑定（应用轮次 / 准确前置成果 / 树级额度）
# ---------------------------------------------------------------------------


def _binding_identity() -> ParentBindIdentity:
    return ParentBindIdentity(
        user_scope="user-1",
        session_id="session-1",
        workflow_code_version="1.0.0",
        run_epoch=1,
        expected_input_revision=1,
    )


def _binding(plan: PlanSpecProposal, *, execution_id: str = "exec-1", round_id: str = "round-1") -> ActualInputBinding:
    digest = "1" * 64
    return ActualInputBinding(
        execution_id=execution_id,
        round_id=round_id,
        plan_digest=plan_spec_digest(plan),
        input_revision=1,
        owner_epoch=2,
        leaves=tuple(
            LeafIdentity(
                node_id=node.node_id,
                leaf_id=leaf_workflow_id(execution_id=execution_id, round_id=round_id, node_id=node.node_id),
                input_digest=digest,
            )
            for node in plan.nodes
        ),
    )


def test_actual_input_binding_roundtrip_and_coverage() -> None:
    """绑定 wire 往返；覆盖计划/清单且叶身份派生一致才算有效。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    binding = _binding(plan)
    decoded = decode_actual_input_binding(binding.to_wire())
    assert decoded == binding
    manifest = compute_actual_input_manifest(plan, input_version_digest="2" * 64)
    # 清单指纹与构造绑定不同 → 覆盖校验必须拒绝（不把错输入当成已绑定）。
    with pytest.raises(AgentValidationError) as excinfo:
        validate_actual_input_binding(
            decoded,
            execution_id="exec-1",
            round_id="round-1",
            plan_digest=plan_spec_digest(plan),
            plan=plan,
            manifest=manifest,
        )
    assert excinfo.value.code == "binding_input_mismatch"


def test_actual_input_binding_rejects_mismatched_leaf_identity() -> None:
    """叶身份与三元组派生不一致即拒绝（不冒认其他叶/轮次）。"""
    plan = _plan(_node("a"))
    binding = _binding(plan)
    manifest = compute_actual_input_manifest(plan, input_version_digest=None)
    wrong = ActualInputBinding(
        execution_id=binding.execution_id,
        round_id=binding.round_id,
        plan_digest=binding.plan_digest,
        input_revision=binding.input_revision,
        owner_epoch=binding.owner_epoch,
        leaves=(LeafIdentity(node_id="a", leaf_id="agent_leaf:other", input_digest=manifest.nodes[0].input_digest),),
    )
    with pytest.raises(AgentValidationError) as excinfo:
        validate_actual_input_binding(
            wrong,
            execution_id="exec-1",
            round_id="round-1",
            plan_digest=plan_spec_digest(plan),
            plan=plan,
            manifest=manifest,
        )
    assert excinfo.value.code == "binding_leaf_identity_mismatch"


def test_actual_input_binder_registry_requires_install() -> None:
    """未登记绑定器明确拒绝（父零叶派发，fail-closed）。"""
    ActualInputBinderRegistry._reset_singleton()
    with pytest.raises(RuntimeError) as excinfo:
        ActualInputBinderRegistry.require()
    assert "actual_input_binding_unavailable" in str(excinfo.value)


def test_binding_identity_is_written_and_decoded_strictly() -> None:
    """派发侧身份写入父载荷并可严格解码；额外键拒绝。"""
    plan = _plan(_node("a"))
    payload = build_research_payload(
        plan,
        execution_id="exec-1",
        round_id="round-1",
        plan_digest=plan_spec_digest(plan),
        binding_identity=_binding_identity(),
    )
    decoded = decode_research_payload_full(payload)
    assert decoded.binding_identity == _binding_identity()
    with pytest.raises(AgentValidationError) as excinfo:
        decode_binding_identity({**_binding_identity().to_wire(), "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_parent_bind_request_shape() -> None:
    """父绑定请求携带派发身份与准入清单指纹（PORTABLE 可序列化）。"""
    request = build_parent_bind_request(
        _binding_identity(),
        execution_id="exec-1",
        round_id="round-1",
        plan_digest="b" * 64,
        workflow_id="wf-1",
        frozen_manifest="{}",
        actual_input="{}",
        input_digests={"a": "1" * 64},
    )
    assert request["user_scope"] == "user-1"
    assert request["workflow_id"] == "wf-1"
    assert request["input_digests"] == {"a": "1" * 64}


def test_leaf_payload_carries_binding_and_predecessor_artifacts() -> None:
    """叶载荷携带绑定身份、输入版与前置成果引用；叶身份不符即拒绝。"""
    leaf_id = leaf_workflow_id(execution_id="exec-1", round_id="round-1", node_id="a")
    payload = build_leaf_payload(
        execution_id="exec-1",
        round_id="round-1",
        plan_digest="b" * 64,
        node=_node("a"),
        input_digest="1" * 64,
        leaf_id=leaf_id,
        input_revision=2,
        predecessor_artifacts=(("p", "art-1@1"),),
    )
    context = decode_leaf_payload(payload)
    assert context.leaf_id == leaf_id
    assert context.input_revision == 2
    assert context.predecessor_artifacts == (("p", "art-1@1"),)
    with pytest.raises(AgentValidationError) as excinfo:
        decode_leaf_payload({**payload, "leaf_id": "agent_leaf:other"})
    assert excinfo.value.code == "leaf_id_mismatch"


def test_drive_blocks_leaf_when_ticket_claim_denied() -> None:
    """票据受阻即阻断节点（含下游），不启动叶、不伪造推进。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    harness = _DagHarness()

    async def claim(node: PlanNodeSpec) -> str | None:
        return "leaf_ticket_blocked:budget_blocked"

    summary = asyncio.run(drive_leaf_dag(plan, harness.start, harness.observe, claim_leaf=claim))
    assert harness.started == []
    assert summary.blocked == ("a", "b")
    assert summary.block_reasons[0][1] == "leaf_ticket_blocked:budget_blocked"
    assert summary.converged is True


def test_drive_passes_predecessor_artifacts_and_releases_on_terminal() -> None:
    """前置成果引用传给后继启动；终态后按已核退出释放票据（每叶一次）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    harness = _DagHarness()
    releases: list[tuple[str, bool]] = []

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        return await harness.start(node, predecessors)

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        outcomes = await harness.observe(children)
        if "a" in outcomes:
            outcomes["a"] = _outcome("a", artifact_ref="art-a@1")
        if "b" in outcomes:
            outcomes["b"] = _outcome("b", artifact_ref="art-b@1")
        return outcomes

    async def release(node: PlanNodeSpec, exit_verified: bool) -> None:
        releases.append((node.node_id, exit_verified))

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, start, observe, release_leaf=release))
        await _wait_until(lambda: harness.started == ["a"])
        harness.gate("a").set()
        await _wait_until(lambda: "b" in harness.started)
        harness.gate("b").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.predecessors["a"] == ()
    assert harness.predecessors["b"] == (("a", "art-a@1"),)
    assert releases == [("a", True), ("b", True)]
    assert summary.succeeded == ("a", "b")


def test_drive_blocks_successor_when_predecessor_artifact_missing() -> None:
    """前置成功但无成果引用：后继被阻断（不以无成果前置启动）。"""
    plan = _plan(_node("a"), _node("b", ("a",)))
    harness = _DagHarness()

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        facts = await harness.observe(children)
        return {node_id: _outcome(node_id, artifact_ref=None) for node_id in facts}

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, harness.start, observe))
        await _wait_until(lambda: harness.started == ["a"])
        harness.gate("a").set()
        return await task

    summary = asyncio.run(scenario())
    assert harness.started == ["a"]
    assert summary.blocked == ("b",)
    assert summary.block_reasons == (("b", "predecessor_artifact_missing:b"),)


def test_ticket_gate_registry_requires_install() -> None:
    """未登记票据门即拒绝（树级额度是安全不变量，fail-closed）。"""
    LeafTicketGateRegistry._reset_singleton()
    with pytest.raises(RuntimeError) as excinfo:
        LeafTicketGateRegistry.require()
    assert "leaf_ticket_gate_unavailable" in str(excinfo.value)


def test_claim_step_maps_budget_denial_to_reason() -> None:
    """认领 BUDGET_BLOCKED 映射为受阻原因（不启动叶）。"""

    class _Gate:
        async def claim(self, *, execution_id: str, round_id: str, node_id: str) -> LeafTicketLease | None:
            from core.agent_types import AgentErrorCode, AgentServiceError

            raise AgentServiceError(AgentErrorCode.BUDGET_BLOCKED, "leaf quota", "user-1/slot")

        async def release(self, lease: LeafTicketLease, *, exit_verified: bool) -> None: ...

    LeafTicketGateRegistry.install(_Gate())
    try:
        result = asyncio.run(
            claim_leaf_ticket({"execution_id": "exec-1", "round_id": "round-1", "node_id": "a", "owner_epoch": 2})
        )
    finally:
        LeafTicketGateRegistry._reset_singleton()
    assert result == {"lease": None, "reason": "budget_blocked"}


def test_release_step_keeps_capacity_when_exit_unverified() -> None:
    """未核退出释放保持占用（返回 False，不放空树级额度）。"""
    released: list[bool] = []

    class _Gate:
        async def claim(self, *, execution_id: str, round_id: str, node_id: str) -> LeafTicketLease | None:
            return None

        async def release(self, lease: LeafTicketLease, *, exit_verified: bool) -> None:
            released.append(exit_verified)

    LeafTicketGateRegistry.install(_Gate())
    try:
        slot_id = leaf_ticket_slot_id("exec-1", "round-1", "a")
        result = asyncio.run(
            release_leaf_ticket(
                {
                    "slot_id": slot_id,
                    "user_scope": "user-1",
                    "execution_id": "exec-1",
                    "node_id": "a",
                    "owner_epoch": 2,
                    "exit_verified": False,
                }
            )
        )
    finally:
        LeafTicketGateRegistry._reset_singleton()
    assert result is False
    assert released == [False]


# ---------------------------------------------------------------------------
# 答复消费（P1-05）：原生 recv → 身份核验 → DAO 幂等 CAS
# ---------------------------------------------------------------------------


def _answer_message(**overrides: Any) -> dict[str, Any]:
    message: dict[str, Any] = {
        "schema_version": 1,
        "decision_id": "d-1",
        "execution_id": "exec-1",
        "round_id": "round-1",
        "client_command_id": "ui-answer:d-1",
        "answer": {"option": "a"},
    }
    message.update(overrides)
    return message


def _consumption(*, consumed_at: str = "2026-11-23T00:00:00.000000Z") -> ConsumptionFact:
    return ConsumptionFact(
        command_record_id="command-1",
        decision_id="d-1",
        consumption_state=ConsumptionState.CONSUMED,
        consumed_at=consumed_at,
        answer_consumed_at=consumed_at,
    )


def test_decode_answer_message_roundtrip_and_closed_keys() -> None:
    """答复消息封闭解码：身份四键可读；额外键拒绝（不放开任意 wire）。"""
    message = decode_answer_message(_answer_message())
    assert (message.decision_id, message.execution_id, message.round_id) == ("d-1", "exec-1", "round-1")
    assert message.client_command_id == "ui-answer:d-1"
    with pytest.raises(AgentValidationError) as excinfo:
        decode_answer_message({**_answer_message(), "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_decode_answer_message_rejects_unsupported_version() -> None:
    with pytest.raises(AgentValidationError) as excinfo:
        decode_answer_message(_answer_message(schema_version=2))
    assert excinfo.value.code == "unsupported_version"


def test_receive_answers_consumes_matching_message() -> None:
    """匹配本执行/本轮的答复经受治理步骤消费（只按原命令身份）。"""
    calls: list[tuple[str, str]] = []

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        assert topic == ANSWER_MESSAGE_TOPIC
        assert timeout_seconds == 0.0
        return _answer_message()

    async def consume(decision_id: str, client_command_id: str) -> dict[str, Any]:
        calls.append((decision_id, client_command_id))
        return {
            "status": "consumed",
            "decision_id": decision_id,
            "command_record_id": "command-1",
            "consumption_state": ConsumptionState.CONSUMED.value,
            "reason": None,
        }

    outcome = asyncio.run(
        receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv, consume=consume)
    )
    assert outcome is not None
    assert outcome.status == "consumed"
    assert outcome.command_record_id == "command-1"
    assert outcome.consumption_state == ConsumptionState.CONSUMED.value
    assert calls == [("d-1", "ui-answer:d-1")]


def test_receive_answers_rejects_identity_mismatch_without_consuming() -> None:
    """执行/轮次不符：拒绝且不调用消费入口（应用侧保留 not_consumed）。"""
    calls: list[tuple[str, str]] = []

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        return _answer_message(round_id="round-other")

    async def consume(decision_id: str, client_command_id: str) -> dict[str, Any]:  # pragma: no cover
        calls.append((decision_id, client_command_id))
        raise AssertionError("mismatched answer must not be consumed")

    outcome = asyncio.run(
        receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv, consume=consume)
    )
    assert outcome is not None
    assert outcome.status == "rejected"
    assert outcome.reason == "answer_identity_mismatch"
    assert outcome.consumption_state is None
    assert calls == []


def test_receive_answers_returns_none_without_message() -> None:
    """无答复：recv 超时返回 None，本入口返回 None（不阻塞叶推进）。"""

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        return None

    async def consume(decision_id: str, client_command_id: str) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("no message must not be consumed")

    assert (
        asyncio.run(receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv, consume=consume))
        is None
    )


def test_receive_answers_rejects_malformed_message() -> None:
    """坏消息如实拒绝（不解析为任意身份，不冒充已消费）。"""

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        return {"schema_version": 1}

    outcome = asyncio.run(receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv))
    assert outcome is not None
    assert outcome.status == "rejected"
    assert outcome.reason == "invalid_answer_message:missing_field"


def test_consume_answer_step_is_idempotent_on_duplicate_delivery() -> None:
    """重复投递：DAO 已消费重放返回原事实，步骤两次读到同一消费事实。"""
    seen: list[tuple[str, str]] = []

    async def consumer(decision_id: str, client_command_id: str) -> ConsumptionFact:
        seen.append((decision_id, client_command_id))
        return _consumption()  # 重放返回同一原事实（consumed_at 不变）

    AnswerConsumerRegistry.install(consumer)
    try:
        first = asyncio.run(consume_answer_step("d-1", "ui-answer:d-1"))
        second = asyncio.run(consume_answer_step("d-1", "ui-answer:d-1"))
    finally:
        AnswerConsumerRegistry._reset_singleton()

    assert first == second
    assert first["status"] == "consumed"
    assert first["consumption_state"] == ConsumptionState.CONSUMED.value
    assert seen == [("d-1", "ui-answer:d-1"), ("d-1", "ui-answer:d-1")]


def test_consume_answer_step_rejects_denied_answer_without_consuming() -> None:
    """DAO 拒绝（过期/终止/旧轮）：步骤返回拒绝并保留 not_consumed，不伪造已消费。"""

    async def consumer(decision_id: str, client_command_id: str) -> ConsumptionFact:
        raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "question expired", "u1/d-1")

    AnswerConsumerRegistry.install(consumer)
    try:
        wire = asyncio.run(consume_answer_step("d-1", "ui-answer:d-1"))
    finally:
        AnswerConsumerRegistry._reset_singleton()

    assert wire["status"] == "rejected"
    assert wire["reason"] == AgentErrorCode.POLICY_DENIED.value
    assert wire["consumption_state"] == ConsumptionState.NOT_CONSUMED.value
    assert wire["command_record_id"] is None


def test_consume_answer_step_requires_installed_consumer() -> None:
    """未安装消费者：明确拒绝（不默认放行、不伪造已消费）。"""
    AnswerConsumerRegistry._reset_singleton()
    with pytest.raises(RuntimeError) as excinfo:
        asyncio.run(consume_answer_step("d-1", "ui-answer:d-1"))
    assert "answer consumer not installed" in str(excinfo.value)


def test_receive_answers_propagates_recv_cancellation() -> None:
    """接收阶段取消原样上抛（不把取消读成「无消息」，R2）。"""

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError) as excinfo:
        asyncio.run(receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv))
    assert excinfo.type is asyncio.CancelledError


def test_receive_answers_propagates_consume_cancellation() -> None:
    """消费阶段取消原样上抛（R2）。"""

    async def recv(topic: str, *, timeout_seconds: float) -> object:
        return _answer_message()

    async def consume(decision_id: str, client_command_id: str) -> dict[str, Any]:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError) as excinfo:
        asyncio.run(receive_pending_answers(execution_id="exec-1", round_id="round-1", recv=recv, consume=consume))
    assert excinfo.type is asyncio.CancelledError


def test_drive_invokes_answer_drain_before_progress() -> None:
    """每轮推进前先消费答复（drain 先于叶启动；无消息不阻塞）。"""
    plan = _plan(_node("a"))
    harness = _DagHarness()
    hits: list[str] = []

    async def drain() -> None:
        hits.append("drain")

    async def start(node: PlanNodeSpec, predecessors: tuple[tuple[str, str], ...]) -> str:
        hits.append(f"start:{node.node_id}")
        return await harness.start(node, predecessors)

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, start, harness.observe, drain_answers=drain))
        await _wait_until(lambda: "start:a" in hits)
        harness.gate("a").set()
        return await task

    summary = asyncio.run(scenario())
    assert hits[0] == "drain"
    assert hits[1] == "start:a"
    assert summary.succeeded == ("a",)
