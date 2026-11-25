"""R2-I01 组合根装配 / 派发接线 / 准入门单测（无外部网络，全部端口注入替身）。

覆盖：

- launch 门闩由显式配置派生（默认关闭，取值不识别仍关闭）；
- 叶 runner 安装后模型桥 ``call_identity`` 只由叶持久执行身份派生
  （``derive_model_call_id(leaf_workflow_id, 0)``，非随机身份）；
- 受理 → 派发 → 读回：仅已提交受理、门闩打开、计划可得时投递，其余零派发并
  如实给原因；``CancelledError`` 原样传播；
- 服务层：注入派发器时 ``submit_and_dispatch`` 返回受理 + 派发双事实，未注入时
  零派发（``dispatch=None``），结果读回未接线时显式拒绝。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from app.agent_wiring import AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, agent_runtime_launch_allowed
from core.agent_types import (
    CommandRef,
    CommitState,
    Mode,
    PlanNodeSpec,
    PlanSpecProposal,
    SubmissionReceipt,
    derive_round_id,
    json_doc,
    plan_spec_digest,
)
from core.model_management_types import ChatTurnOutcome, ChatTurnResponse, ExecutionSnapshot
from services.agent_service.dispatch import (
    BINDING_IDENTITY_MISMATCH,
    BINDING_IDENTITY_UNAVAILABLE,
    DISPATCH_CLOSED,
    MODE_RESEARCH_DISABLED,
    NOT_COMMITTED,
    PLAN_UNAVAILABLE,
    ResearchDispatcher,
    ResearchWorkflowResult,
    build_binding_identity_provider,
)
from services.agent_service.leaf_runtime import (
    LEAF_AGENT_UNAVAILABLE,
    LeafModelBinding,
    LeafRuntimeWiring,
    build_gateway_model,
    install_leaf_runtime,
    leaf_call_identity,
)
from services.agent_service.model import BOUND_MODEL_ALIAS, derive_model_call_id
from services.agent_service.service import AgentService
from services.agent_service.workflows import (
    LeafExecutionContext,
    LeafRuntimeRegistry,
    NodeOutcome,
    ParentBindIdentity,
    decode_research_payload,
    decode_research_payload_full,
    leaf_workflow_id,
)

_USER_SCOPE = "u1"
_EXECUTION_ID = "execution-1"
_WORKFLOW_ID = "wf-1"

_REF = CommandRef(
    user_scope=_USER_SCOPE,
    source_module="ui",
    business_request_id="req-1",
    command_id="client-1",
    expected_revision=0,
)

_PLAN = PlanSpecProposal(
    nodes=(
        PlanNodeSpec(node_id="n1", target="梳理公司业务", role_key="analyst"),
        PlanNodeSpec(node_id="n2", target="核对财务", role_key="analyst", depends_on=("n1",)),
    )
)

#: 派发前准入的角色能力策略（plan 节点未声明能力 → analyst 允许集为空）。
_ROLE_CAPABILITIES: dict[str, frozenset[str]] = {"analyst": frozenset()}

_SNAPSHOT = ExecutionSnapshot(
    snapshot_id="snap-1",
    run_id=None,
    config_revision=1,
    routes=(),
    connections=(),
    business_contract_versions={},
    security_scope_version=1,
    created_at="2026-11-18T00:00:00.000000Z",
)


def _receipt(*, commit_state: CommitState = CommitState.COMMITTED, mode: Mode | None = None) -> SubmissionReceipt:
    return SubmissionReceipt(
        command=_REF,
        command_record_id="cmd-1",
        session_id="s1",
        execution_id=_EXECUTION_ID,
        workflow_id=_WORKFLOW_ID,
        intent_digest="a" * 64,
        commit_state=commit_state,
        verified_at="2026-11-18T00:00:00.000000Z",
        next_action=None,
        mode=mode,
    )


def _leaf_context(node_id: str = "n1") -> LeafExecutionContext:
    return LeafExecutionContext(
        execution_id=_EXECUTION_ID,
        round_id="round-1",
        plan_digest="b" * 64,
        node=PlanNodeSpec(node_id=node_id, target="梳理公司业务", role_key="analyst"),
    )


class _FakeExecutor:
    """只记录请求、回固定成功回合的模型执行器（零外部网络）。"""

    def __init__(self) -> None:
        self.requests: list[Any] = []

    async def execute_chat_turn(
        self, request: Any, snapshot: Any, *, binding: Any, on_event: Any = None
    ) -> ChatTurnOutcome:
        self.requests.append(request)
        return ChatTurnOutcome(
            model_call_id=request.model_call_id,
            invocation_id="inv-1",
            status="succeeded",
            transport_state="ok",
            settlement_state=None,
            attempt_id="attempt-1",
            response=ChatTurnResponse(
                text="ok",
                finish_reason="final",
                actual_model="fake-model",
                usage_observed=False,
                usage=None,
                transport_state="ok",
            ),
            partial_text=None,
            error_code=None,
        )


class _FakeGate:
    def __init__(self, *, closed: bool) -> None:
        self._closed = closed

    def dispatch_closed(self) -> bool:
        return self._closed


class _ToggleGate:
    """可在派发过程中被合上的门闩（模拟关闭链与在途派发竞态）。"""

    def __init__(self) -> None:
        self.closed = False

    def close_dispatch(self) -> bool:
        self.closed = True
        return True

    def dispatch_closed(self) -> bool:
        return self.closed


class _RecordingSubmitter:
    def __init__(self, *, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._error = error

    async def __call__(self, workflow_id: str, payload: dict[str, Any]) -> None:
        self.calls.append((workflow_id, payload))
        if self._error is not None:
            raise self._error


def _dispatcher(
    *,
    gate: Any,
    submit: Any,
    plan_provider: Any = None,
    read_result: Any = None,
    binding_identity_provider: Any = None,
) -> ResearchDispatcher:
    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        assert (user_scope, execution_id) == (_USER_SCOPE, _EXECUTION_ID)
        return _PLAN

    async def _read(workflow_id: str) -> ResearchWorkflowResult:
        return ResearchWorkflowResult(workflow_id=workflow_id, state="SUCCESS", output={"summary": "ok"})

    async def _binding(user_scope: str, execution_id: str) -> ParentBindIdentity:
        assert (user_scope, execution_id) == (_USER_SCOPE, _EXECUTION_ID)
        return ParentBindIdentity(
            user_scope=user_scope,
            session_id="s1",
            workflow_code_version="1.0.0",
            run_epoch=1,
            expected_input_revision=1,
        )

    return ResearchDispatcher(
        gate=gate,
        submit=submit,
        plan_provider=_plan if plan_provider is None else plan_provider,
        read_result=_read if read_result is None else read_result,
        role_capabilities=_ROLE_CAPABILITIES,
        binding_identity_provider=_binding if binding_identity_provider is None else binding_identity_provider,
    )


class _FakeDao:
    def __init__(self, receipt: SubmissionReceipt) -> None:
        self._receipt = receipt
        self.accept_calls = 0

    async def accept_request(self, request: Any) -> SubmissionReceipt:
        self.accept_calls += 1
        return self._receipt


def _accept_request(*, mode: Any = None) -> Any:
    from core.agent_types import AcceptRequest, Mode, Purpose

    return AcceptRequest(
        user_scope=_USER_SCOPE,
        session_id="s1",
        source_module="ui",
        business_request_id="req-1",
        client_command_id="client-1",
        expected_revision=0,
        node_id="root",
        workflow_id=_WORKFLOW_ID,
        mode=Mode.EXECUTE if mode is None else mode,
        purpose=Purpose.CONCEPT,
        text="研究三家公司",
        manifest_payload=json_doc({"schema_version": 1, "text": "研究三家公司"}),
        source_display_name="用户消息",
    )


# --- launch 门闩 -------------------------------------------------------------


def test_launch_gate_defaults_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, raising=False)
    assert agent_runtime_launch_allowed() is False
    monkeypatch.setenv(AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, "")
    assert agent_runtime_launch_allowed() is False
    monkeypatch.setenv(AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, "unexpected")
    assert agent_runtime_launch_allowed() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_launch_gate_requires_explicit_config(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(AGENT_RUNTIME_LAUNCH_ALLOWED_ENV, value)
    assert agent_runtime_launch_allowed() is True


# --- 叶 runner 安装 + 模型桥身份 ---------------------------------------------


def test_installed_leaf_runner_uses_persistent_identity_for_model_calls() -> None:
    context = _leaf_context()
    executor = _FakeExecutor()
    seen: list[Any] = []

    async def _resolve(ctx: LeafExecutionContext) -> LeafModelBinding:
        assert ctx is context
        return LeafModelBinding(
            executor=cast(Any, executor),
            snapshot=_SNAPSHOT,
            binding={"scenario_id": "agent_research"},
            deadline=time.monotonic() + 60.0,
        )

    async def _run(model: Any, ctx: LeafExecutionContext, binding: LeafModelBinding) -> NodeOutcome:
        seen.append(model)
        await model.request(
            [ModelRequest(parts=[UserPromptPart(content="梳理公司业务")])],
            {"timeout": 30.0},
            ModelRequestParameters(),
        )
        return NodeOutcome(node_id=ctx.node.node_id, state="complete", artifact_ref="artifact-1")

    install_leaf_runtime(LeafRuntimeWiring(model_binding=_resolve, agent_runner=_run))

    runner = LeafRuntimeRegistry.require()
    outcome = asyncio.run(cast(Any, runner(context)))

    assert outcome == NodeOutcome(node_id="n1", state="complete", artifact_ref="artifact-1")
    expected_call_id = derive_model_call_id(
        leaf_workflow_id(execution_id=_EXECUTION_ID, round_id="round-1", node_id="n1"),
        0,
    )
    assert [request.model_call_id for request in executor.requests] == [expected_call_id]
    assert leaf_call_identity(context) == leaf_workflow_id(execution_id=_EXECUTION_ID, round_id="round-1", node_id="n1")


def test_gateway_model_uses_bound_alias() -> None:
    context = _leaf_context()
    binding = LeafModelBinding(
        executor=cast(Any, _FakeExecutor()), snapshot=_SNAPSHOT, binding={}, deadline=time.monotonic() + 60.0
    )
    model = build_gateway_model(context, binding)
    assert model.model_name == BOUND_MODEL_ALIAS


# --- 受理 → 派发 → 读回 ------------------------------------------------------


def test_derive_round_id_matches_dispatch_formula() -> None:
    from core.agent_types import stable_id

    assert derive_round_id(user_scope=_USER_SCOPE, execution_id=_EXECUTION_ID, workflow_id=_WORKFLOW_ID) == stable_id(
        "round", [_USER_SCOPE, _EXECUTION_ID, _WORKFLOW_ID]
    )


def test_dispatch_submits_and_reads_back_result() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is True
    assert outcome.workflow_id == _WORKFLOW_ID
    assert outcome.round_id == derive_round_id(
        user_scope=_USER_SCOPE, execution_id=_EXECUTION_ID, workflow_id=_WORKFLOW_ID
    )
    assert [workflow_id for workflow_id, _ in submit.calls] == [_WORKFLOW_ID]
    workflow_id, payload = submit.calls[0]
    assert workflow_id == _WORKFLOW_ID
    decoded_execution_id, decoded_round_id, decoded_digest, decoded_plan, preserved = decode_research_payload(payload)
    assert decoded_execution_id == _EXECUTION_ID
    assert [node.node_id for node in decoded_plan.nodes] == ["n1", "n2"]
    assert decoded_digest == plan_spec_digest(_PLAN)
    assert decoded_round_id == outcome.round_id
    assert preserved == ()
    # 父载荷必须携带与 agent_actual_input_bind 一致的冻结身份（不再零派发）。
    assert decode_research_payload_full(payload).binding_identity == ParentBindIdentity(
        user_scope=_USER_SCOPE,
        session_id="s1",
        workflow_code_version="1.0.0",
        run_epoch=1,
        expected_input_revision=1,
    )

    result = asyncio.run(dispatcher.read_result(_WORKFLOW_ID))
    assert result.state == "SUCCESS"
    assert result.output == {"summary": "ok"}


def test_dispatch_without_binding_identity_provider_is_zero_dispatch() -> None:
    """未接线绑定来源 → 零派发（不放宽成裸派发）。"""
    submit = _RecordingSubmitter()
    dispatcher = ResearchDispatcher(
        gate=_FakeGate(closed=False),
        submit=submit,
        plan_provider=_default_plan,
        read_result=_default_read,
        role_capabilities=_ROLE_CAPABILITIES,
    )

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == BINDING_IDENTITY_UNAVAILABLE
    assert submit.calls == []


def test_dispatch_binding_identity_mismatch_is_zero_dispatch() -> None:
    """绑定身份与受理会话不一致 → 零派发（不写入错身份）。"""

    async def _wrong_session(user_scope: str, execution_id: str) -> ParentBindIdentity:
        return ParentBindIdentity(
            user_scope=user_scope,
            session_id="other-session",
            workflow_code_version="1.0.0",
            run_epoch=1,
            expected_input_revision=1,
        )

    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, binding_identity_provider=_wrong_session)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == BINDING_IDENTITY_MISMATCH
    assert submit.calls == []


def test_binding_provider_reuses_round_binding_and_initialises_first_dispatch() -> None:
    """已有轮次沿用原绑定；尚无轮次按受理契约给输入版/代次 1。"""

    class _SnapshotDao:
        def __init__(self, identity: Any) -> None:
            self._identity = identity

        async def get_execution_snapshot(self, execution_id: str, *, user_scope: str, limit: int) -> Any:
            return type("_Snap", (), {"identity": self._identity})()

    round_identity = type(
        "_Identity",
        (),
        {
            "user_scope": _USER_SCOPE,
            "session_id": "s1",
            "execution_id": _EXECUTION_ID,
            "round_id": "round-1",
            "input_revision": 3,
            "run_epoch": 5,
        },
    )()
    provider = build_binding_identity_provider(_SnapshotDao(round_identity), application_version="0.11.7")

    async def _resolve() -> ParentBindIdentity | None:
        return await provider(_USER_SCOPE, _EXECUTION_ID)

    reused = asyncio.run(_resolve())
    assert reused == ParentBindIdentity(
        user_scope=_USER_SCOPE,
        session_id="s1",
        workflow_code_version="0.11.7",
        run_epoch=5,
        expected_input_revision=3,
    )

    queued_identity = type(
        "_Identity",
        (),
        {
            "user_scope": _USER_SCOPE,
            "session_id": "s1",
            "execution_id": _EXECUTION_ID,
            "round_id": None,
            "input_revision": None,
            "run_epoch": None,
        },
    )()
    provider = build_binding_identity_provider(_SnapshotDao(queued_identity), application_version="0.11.7")
    initial = asyncio.run(_resolve())
    assert initial == ParentBindIdentity(
        user_scope=_USER_SCOPE,
        session_id="s1",
        workflow_code_version="0.11.7",
        run_epoch=1,
        expected_input_revision=1,
    )


async def _default_plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
    return _PLAN


async def _default_read(workflow_id: str) -> ResearchWorkflowResult:
    return ResearchWorkflowResult(workflow_id=workflow_id, state="SUCCESS", output={})


def test_dispatch_closed_gate_is_zero_dispatch() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=True), submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == DISPATCH_CLOSED
    assert outcome.reason_code == DISPATCH_CLOSED
    assert outcome.reason_params == ()
    assert submit.calls == []


def test_dispatch_plan_unavailable_reason_is_structured() -> None:
    """无计划不投递，且原因是稳定原因码 + 参数（UI 按码 i18n，不拼自由文本）。"""

    async def _no_plan(user_scope: str, execution_id: str) -> None:
        return None

    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, plan_provider=_no_plan)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE, mode=Mode.EXECUTE))

    assert outcome.submitted is False
    assert outcome.reason_code == PLAN_UNAVAILABLE
    assert outcome.reason_params == (("cause", "plan_not_saved"),)
    assert submit.calls == []


def test_dispatch_closed_gate_during_plan_fetch_is_zero_dispatch() -> None:
    """N31：异步取计划期间关闭门合上，返回后必须复验，零额外提交。"""

    submit = _RecordingSubmitter()
    gate = _ToggleGate()

    async def _plan_closing(user_scope: str, execution_id: str) -> PlanSpecProposal:
        gate.close_dispatch()
        return _PLAN

    dispatcher = _dispatcher(gate=gate, submit=submit, plan_provider=_plan_closing)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == DISPATCH_CLOSED
    assert submit.calls == []


def test_dispatch_closed_gate_during_binding_resolution_is_zero_dispatch() -> None:
    """N31：其它准入异步阶段（绑定身份）合门同样必须零提交。"""

    submit = _RecordingSubmitter()
    gate = _ToggleGate()

    async def _binding_closing(user_scope: str, execution_id: str) -> ParentBindIdentity:
        gate.close_dispatch()
        return ParentBindIdentity(
            user_scope=user_scope,
            session_id="s1",
            workflow_code_version="1.0.0",
            run_epoch=1,
            expected_input_revision=1,
        )

    dispatcher = _dispatcher(gate=gate, submit=submit, binding_identity_provider=_binding_closing)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == DISPATCH_CLOSED
    assert submit.calls == []


def test_dispatch_open_gate_after_plan_fetch_still_submits() -> None:
    """N31 对照：门未合上时照常提交（复验不误伤正常派发）。"""

    submit = _RecordingSubmitter()
    gate = _ToggleGate()
    dispatcher = _dispatcher(gate=gate, submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is True
    assert [workflow_id for workflow_id, _ in submit.calls] == [_WORKFLOW_ID]


def test_dispatch_uncommitted_receipt_is_zero_dispatch() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(commit_state=CommitState.NOT_COMMITTED), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == f"{NOT_COMMITTED}:{CommitState.NOT_COMMITTED.value}"
    assert submit.calls == []


def test_dispatch_non_execute_mode_is_zero_dispatch() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE, mode=Mode.DISCUSS))

    assert outcome.submitted is False
    assert outcome.reason == f"{MODE_RESEARCH_DISABLED}:{Mode.DISCUSS.value}"
    assert submit.calls == []


def test_submit_in_discuss_mode_does_not_start_research() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)
    dao = _FakeDao(_receipt())
    service = AgentService(cast(Any, dao), dispatcher=dispatcher)

    outcome = asyncio.run(
        service.submit_and_dispatch(_accept_request(mode=Mode.DISCUSS), command=cast(Any, _receipt().command))
    )

    assert outcome.dispatch is not None
    assert outcome.dispatch.submitted is False
    assert outcome.dispatch.reason == f"{MODE_RESEARCH_DISABLED}:{Mode.DISCUSS.value}"
    assert submit.calls == []


def test_submit_dispatch_uses_accepted_receipt_mode_not_request_mode() -> None:
    """P1-03：重放返回原回执，派发必须用回执里的受理权威 mode。"""

    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)
    dao = _FakeDao(_receipt(mode=Mode.DISCUSS))
    service = AgentService(cast(Any, dao), dispatcher=dispatcher)

    outcome = asyncio.run(
        service.submit_and_dispatch(_accept_request(mode=Mode.EXECUTE), command=cast(Any, _receipt().command))
    )

    assert outcome.dispatch is not None
    assert outcome.dispatch.submitted is False
    assert outcome.dispatch.reason == f"{MODE_RESEARCH_DISABLED}:{Mode.DISCUSS.value}"
    assert submit.calls == []


def test_dispatch_missing_plan_is_zero_dispatch() -> None:
    submit = _RecordingSubmitter()

    async def _no_plan(user_scope: str, execution_id: str) -> None:
        return None

    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, plan_provider=_no_plan)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == PLAN_UNAVAILABLE
    assert submit.calls == []


def test_dispatch_plan_provider_failure_is_zero_dispatch() -> None:
    submit = _RecordingSubmitter()

    async def _boom(user_scope: str, execution_id: str) -> PlanSpecProposal:
        raise RuntimeError("plan store down")

    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, plan_provider=_boom)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == "plan_unavailable:RuntimeError"
    assert submit.calls == []


def test_dispatch_submit_failure_reports_zero_dispatch() -> None:
    submit = _RecordingSubmitter(error=RuntimeError("dbos down"))
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == "submit_failed:RuntimeError"


def test_dispatch_propagates_cancelled_error(monkeypatch: pytest.MonkeyPatch) -> None:
    submit = _RecordingSubmitter(error=asyncio.CancelledError())
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)

    with pytest.raises(asyncio.CancelledError) as excinfo:
        asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert isinstance(excinfo.value, asyncio.CancelledError)
    assert [workflow_id for workflow_id, _ in submit.calls] == [_WORKFLOW_ID]


def test_dispatch_empty_plan_is_rejected_before_dispatch() -> None:
    submit = _RecordingSubmitter()

    async def _empty_plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return PlanSpecProposal(nodes=())

    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, plan_provider=_empty_plan)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason is not None and outcome.reason.startswith("plan_invalid:")
    assert submit.calls == []


def test_dispatch_illegal_plan_graph_is_rejected_before_dispatch() -> None:
    """非法依赖图（环）派发前拒绝：零投递（R2-I09）。"""
    submit = _RecordingSubmitter()

    async def _cyclic_plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return PlanSpecProposal(
            nodes=(
                PlanNodeSpec(node_id="n1", target="梳理公司业务", role_key="analyst", depends_on=("n2",)),
                PlanNodeSpec(node_id="n2", target="核对财务", role_key="analyst", depends_on=("n1",)),
            )
        )

    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit, plan_provider=_cyclic_plan)

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason is not None and outcome.reason.startswith("plan_invalid:")
    assert submit.calls == []


def test_dispatch_without_admission_policy_is_zero_dispatch() -> None:
    """无角色能力策略即无准入判据：零派发（不自授权，R2-I09）。"""
    submit = _RecordingSubmitter()

    async def _plan(user_scope: str, execution_id: str) -> PlanSpecProposal:
        return _PLAN

    dispatcher = ResearchDispatcher(
        gate=_FakeGate(closed=False),
        submit=submit,
        plan_provider=_plan,
    )

    outcome = asyncio.run(dispatcher.dispatch(_receipt(), user_scope=_USER_SCOPE))

    assert outcome.submitted is False
    assert outcome.reason == "plan_admission_unconfigured"
    assert submit.calls == []


# --- 服务层入口 --------------------------------------------------------------


def test_submit_dispatches_when_dispatcher_wired() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)
    dao = _FakeDao(_receipt())
    service = AgentService(cast(Any, dao), dispatcher=dispatcher)

    receipt = asyncio.run(service.submit(_accept_request(), command=cast(Any, _receipt().command)))

    assert receipt.execution_id == _EXECUTION_ID
    assert [workflow_id for workflow_id, _ in submit.calls] == [_WORKFLOW_ID]


def test_submit_and_dispatch_returns_receipt_and_dispatch_facts() -> None:
    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)
    dao = _FakeDao(_receipt())
    service = AgentService(cast(Any, dao), dispatcher=dispatcher)

    outcome = asyncio.run(service.submit_and_dispatch(_accept_request(), command=cast(Any, _receipt().command)))

    assert dao.accept_calls == 1
    assert outcome.receipt.execution_id == _EXECUTION_ID
    assert outcome.dispatch is not None
    assert outcome.dispatch.submitted is True
    assert outcome.dispatch.round_id == derive_round_id(
        user_scope=_USER_SCOPE, execution_id=_EXECUTION_ID, workflow_id=_WORKFLOW_ID
    )
    assert [workflow_id for workflow_id, _ in submit.calls] == [_WORKFLOW_ID]
    result = asyncio.run(service.read_research_result(_WORKFLOW_ID))
    assert result.output == {"summary": "ok"}


def test_accept_commits_without_dispatching() -> None:
    """仅受理不派发：执行模式首问先产出计划，再由入口显式派发（不自动启动）。"""

    submit = _RecordingSubmitter()
    dispatcher = _dispatcher(gate=_FakeGate(closed=False), submit=submit)
    dao = _FakeDao(_receipt())
    service = AgentService(cast(Any, dao), dispatcher=dispatcher)

    receipt = asyncio.run(service.accept(_accept_request(), command=cast(Any, _receipt().command)))

    assert dao.accept_calls == 1
    assert receipt.execution_id == _EXECUTION_ID
    assert submit.calls == []


def test_submit_and_dispatch_without_dispatcher_is_zero_dispatch() -> None:
    dao = _FakeDao(_receipt())
    service = AgentService(cast(Any, dao))

    outcome = asyncio.run(service.submit_and_dispatch(_accept_request(), command=cast(Any, _receipt().command)))

    assert outcome.receipt.commit_state is CommitState.COMMITTED
    assert outcome.dispatch is None


def test_read_research_result_without_dispatcher_is_explicit_failure() -> None:
    from core.agent_types import AgentErrorCode, AgentServiceError

    service = AgentService(cast(Any, _FakeDao(_receipt())))

    with pytest.raises(AgentServiceError) as excinfo:
        asyncio.run(service.read_research_result(_WORKFLOW_ID))

    assert excinfo.value.code is AgentErrorCode.PERSISTENCE_UNAVAILABLE


def test_unavailable_leaf_agent_reports_failed_without_faking_success() -> None:
    from services.agent_service.leaf_runtime import unavailable_leaf_agent

    context = _leaf_context()
    binding = LeafModelBinding(
        executor=cast(Any, _FakeExecutor()), snapshot=_SNAPSHOT, binding={}, deadline=time.monotonic() + 60.0
    )
    model = build_gateway_model(context, binding)

    outcome = asyncio.run(unavailable_leaf_agent(model, context, binding))

    assert outcome.state == "failed"
    assert outcome.error_code == LEAF_AGENT_UNAVAILABLE
    assert outcome.artifact_ref is None
