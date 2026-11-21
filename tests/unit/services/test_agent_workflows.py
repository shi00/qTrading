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

import pytest

from core.agent_types import AgentValidationError, PlanNodeSpec, PlanSpecProposal, plan_spec_digest
from dbos import DBOS
from services.agent_service.workflows import (
    IDLE_WAIT_INTERVAL_SEC,
    LEAF_EXECUTE_STEP_NAME,
    LeafDagScheduler,
    LeafExecutionContext,
    LeafRuntimeRegistry,
    NodeOutcome,
    PlanAdmission,
    PlanRunSummary,
    _leaf_step_evidence,
    _terminal_outcome,
    admit_research_plan,
    build_leaf_payload,
    build_research_payload,
    build_retry_payload,
    compute_actual_input_manifest,
    compute_retry_scope,
    decode_leaf_payload,
    decode_node_outcome,
    decode_plan_admission,
    decode_research_payload,
    decode_research_payload_full,
    drive_leaf_dag,
    leaf_workflow_id,
    observe_leaf_children,
    record_plan_admission,
    wait_next_observation_window,
)

pytestmark = pytest.mark.unit


def _node(node_id: str, depends_on: tuple[str, ...] = ()) -> PlanNodeSpec:
    return PlanNodeSpec(node_id=node_id, target=f"目标-{node_id}", role_key="researcher", depends_on=depends_on)


def _plan(*nodes: PlanNodeSpec) -> PlanSpecProposal:
    return PlanSpecProposal(nodes=nodes)


def _outcome(node_id: str, state: str = "complete", **kwargs: str | None) -> NodeOutcome:
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
        self.states: dict[str, str] = {}
        self.active = 0
        self.max_concurrent = 0
        self._gates: dict[str, asyncio.Event] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def gate(self, node_id: str) -> asyncio.Event:
        return self._gates.setdefault(node_id, asyncio.Event())

    async def start(self, node: PlanNodeSpec) -> str:
        node_id = node.node_id
        self.started.append(node_id)
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

    async def cancelled(node: PlanNodeSpec) -> str:
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

    async def broken(node: PlanNodeSpec) -> str:
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

    async def start(node: PlanNodeSpec) -> str:
        calls.append(("start", (node.node_id,)))
        return await harness.start(node)

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

    async def start(node: PlanNodeSpec) -> str:
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

    async def start(node: PlanNodeSpec) -> str:
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

    async def start(node: PlanNodeSpec) -> str:
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

    async def start(node: PlanNodeSpec) -> str:
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

    async def start(node: PlanNodeSpec) -> str:
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


def test_terminal_outcome_maps_cancelled_without_execution_to_ended() -> None:
    """无任何执行记录的取消（如启动隔离取消）→ ended；不伪装业务失败或空成果。"""
    outcome = _terminal_outcome("a", state="CANCELLED", output=None)
    assert outcome == NodeOutcome(node_id="a", state="ended", error_code="leaf_workflow_cancelled")


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
    """观察链路：无执行记录的取消（启动隔离取消）→ ended，不宣称成果。"""

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
        NodeOutcome(node_id="a", state="ended", error_code="leaf_workflow_cancelled").to_wire(),
        NodeOutcome(node_id="b", state="ended", error_code="leaf_workflow_cancelled").to_wire(),
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
