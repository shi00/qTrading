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
    LeafRuntimeRegistry,
    NodeOutcome,
    PlanRunSummary,
    build_leaf_payload,
    decode_leaf_payload,
    decode_node_outcome,
    decode_research_payload,
    drive_leaf_dag,
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


class _ControlledRunner:
    """受控叶执行器：每个节点等待自己的 gate 后返回预设状态。"""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.gates: dict[str, asyncio.Event] = {}
        self.states: dict[str, str] = {}
        self.active = 0
        self.max_concurrent = 0

    def gate(self, node_id: str) -> asyncio.Event:
        return self.gates.setdefault(node_id, asyncio.Event())

    async def __call__(self, node: PlanNodeSpec) -> NodeOutcome:
        self.started.append(node.node_id)
        self.active += 1
        self.max_concurrent = max(self.max_concurrent, self.active)
        try:
            await self.gate(node.node_id).wait()
            return _outcome(node.node_id, self.states.get(node.node_id, "complete"))
        finally:
            self.active -= 1


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
    runner = _ControlledRunner()

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, runner))
        await _wait_until(lambda: runner.started == ["a", "b"])
        runner.gate("b").set()
        await _wait_until(lambda: "d" in runner.started)
        # C 必须仍等待 A：不因 B 完成而提前
        assert "c" not in runner.started
        runner.gate("a").set()
        await _wait_until(lambda: "c" in runner.started)
        for node_id in ("c", "d"):
            runner.gate(node_id).set()
        return await task

    summary = asyncio.run(scenario())
    assert runner.started == ["a", "b", "d", "c"]
    assert summary.succeeded == ("a", "b", "c", "d")
    assert summary.converged is True


def test_drive_runs_independent_leaves_concurrently() -> None:
    """独立叶并发执行（不串行排队）。"""
    plan = _plan(_node("a"), _node("b"))
    runner = _ControlledRunner()

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, runner))
        await _wait_until(lambda: len(runner.started) == 2)
        runner.gate("a").set()
        runner.gate("b").set()
        return await task

    summary = asyncio.run(scenario())
    assert runner.max_concurrent == 2
    assert summary.succeeded == ("a", "b")


def test_drive_failure_keeps_independent_branch_running() -> None:
    """A 失败不阻独立 B；C 被阻塞并以事实记录（不补答）。"""
    plan = _plan(_node("a"), _node("b"), _node("c", ("a",)))
    runner = _ControlledRunner()
    runner.states["a"] = "failed"

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, runner))
        await _wait_until(lambda: len(runner.started) == 2)
        runner.gate("a").set()
        await _wait_until(lambda: runner.active == 1)
        runner.gate("b").set()
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
    runner = _ControlledRunner()
    runner.states["a"] = "waiting_user"

    async def scenario() -> PlanRunSummary:
        task = asyncio.ensure_future(drive_leaf_dag(plan, runner))
        await _wait_until(lambda: runner.started == ["a"])
        runner.gate("a").set()
        return await task

    summary = asyncio.run(scenario())
    assert runner.started == ["a"]
    assert summary.pending == ("a", "b")
    assert summary.converged is False
    assert summary.succeeded == ()


def test_drive_propagates_cancellation() -> None:
    """取消向上传播（R2：不吞 CancelledError、不转成 failed 事实）。"""
    plan = _plan(_node("a"))

    async def cancelled(node: PlanNodeSpec) -> NodeOutcome:
        raise asyncio.CancelledError

    async def scenario() -> None:
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await drive_leaf_dag(plan, cancelled)
        assert excinfo.type is asyncio.CancelledError

    asyncio.run(scenario())


def test_drive_propagates_unexpected_exception() -> None:
    """未判定异常向上传播（不伪造 failed：未知不等于失败）。"""
    plan = _plan(_node("a"))

    async def broken(node: PlanNodeSpec) -> NodeOutcome:
        raise ValueError("infra boom")

    async def scenario() -> None:
        with pytest.raises(ValueError) as excinfo:
            await drive_leaf_dag(plan, broken)
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
    """叶载荷往返；额外键拒绝（封闭 wire）。"""
    payload = build_leaf_payload(execution_id="exec-1", plan_digest="b" * 64, node=_node("a", ("b",)))
    execution_id, plan_digest, node = decode_leaf_payload(payload)
    assert (execution_id, plan_digest) == ("exec-1", "b" * 64)
    assert node.node_id == "a"
    assert node.depends_on == ("b",)
    with pytest.raises(AgentValidationError) as excinfo:
        decode_leaf_payload({**payload, "extra": 1})
    assert excinfo.value.code == "unknown_field"


def test_research_payload_decode_nodes() -> None:
    """父载荷解码为已校验前保存的计划（nodes 严格解码）。"""
    payload = {"payload_version": 1, "execution_id": "exec-1", "plan_digest": "c" * 64, "nodes": []}
    # 空 nodes 结构合法（是否为空白由派发前 validate_plan_spec_proposal 拒绝）
    execution_id, plan_digest, plan = decode_research_payload(payload)
    assert (execution_id, plan_digest) == ("exec-1", "c" * 64)
    assert plan.nodes == ()


def test_leaf_registry_requires_install() -> None:
    """未登记叶执行器明确拒绝（不默认放行）。"""
    LeafRuntimeRegistry._reset_singleton()
    with pytest.raises(RuntimeError) as excinfo:
        LeafRuntimeRegistry.require()
    assert "leaf runner not installed" in str(excinfo.value)


def test_leaf_registry_install_and_reset() -> None:
    """登记后返回同一执行器；复位清空（测试隔离）。"""

    async def runner(node: PlanNodeSpec) -> NodeOutcome:
        return _outcome(node.node_id)

    LeafRuntimeRegistry.install(runner)
    assert LeafRuntimeRegistry.require() is runner
    LeafRuntimeRegistry._reset_singleton()
    assert LeafRuntimeRegistry._runner is None
