"""一层叶 DAG 调度与原生 workflow 骨架（DD F04 / SQ-04；HANDOFF §7.2/§8.3，D-02）。

覆盖：
- 就绪判定：仅「全部前置成功（complete）」的未启动叶就绪；失败/终止/partial
  只阻相关后继、独立分支继续（不借成功兄弟补答案）；
- 暂停/等待/未知效果不自动结束父（不伪造终态、不启动后继）；
- 汇总区分叶事实与目标完成（``leaves_all_succeeded`` 不等于目标已覆盖）；
- 事件驱动推进：B 结束即启动 D，不按固定批次 gather；
- 取消与未判定异常向上传播（R2/R21：不伪造 failed）；
- 封闭 wire 与叶执行器登记的明确拒绝语义。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from core.agent_types import AgentValidationError, PlanNodeSpec, PlanSpecProposal
from services.agent_service.workflows import (
    LeafDagScheduler,
    LeafExecutionContext,
    LeafRuntimeRegistry,
    NodeOutcome,
    PlanRunSummary,
    _terminal_outcome,
    build_leaf_payload,
    build_research_payload,
    build_retry_payload,
    compute_retry_scope,
    decode_leaf_payload,
    decode_node_outcome,
    decode_research_payload,
    drive_leaf_dag,
    leaf_workflow_id,
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


def test_unknown_and_closed_unknown_do_not_converge() -> None:
    """unknown / closed_unknown 效果未知，不自动结束本次执行。"""
    scheduler = LeafDagScheduler(_plan(_node("a")))
    scheduler.start("a")
    scheduler.record(_outcome("a", "unknown"))
    assert scheduler.converged() is False
    scheduler_two = LeafDagScheduler(_plan(_node("b")))
    scheduler_two.start("b")
    scheduler_two.record(_outcome("b", "closed_unknown"))
    assert scheduler_two.converged() is False


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
    """连续无进展观察达预算即停观察：父以 converged=False 结束、不伪造叶终态（§8.3/R21）。"""
    plan = _plan(_node("a"))

    async def start(node: PlanNodeSpec) -> str:
        return "wf:a"

    async def observe(children: tuple[tuple[str, str], ...]) -> dict[str, NodeOutcome]:
        return {}

    summary = asyncio.run(drive_leaf_dag(plan, start, observe, idle_observation_limit=3))
    assert summary.converged is False
    assert summary.pending == ("a",)
    assert summary.succeeded == ()


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


@pytest.mark.parametrize("state", ["ERROR", "MAX_RECOVERY_ATTEMPTS_EXCEEDED"])
def test_terminal_outcome_maps_failure_statuses(state: str) -> None:
    """未判定异常/恢复耗尽 → failed 通用错误码（明细留在 DBOS 状态，R9）。"""
    outcome = _terminal_outcome("a", state=state, output=None)
    assert outcome == NodeOutcome(node_id="a", state="failed", error_code="leaf_workflow_error")


def test_terminal_outcome_maps_cancelled_to_ended() -> None:
    """外部取消 → ended（不当作成功，也不伪装业务失败）。"""
    outcome = _terminal_outcome("a", state="CANCELLED", output=None)
    assert outcome == NodeOutcome(node_id="a", state="ended", error_code="leaf_workflow_cancelled")


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
