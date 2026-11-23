"""叶 Agent 主体契约（DD §3.5 / §10.1 / §10.3）。

覆盖：

- 成功：单次 ``Agent.run`` → 实际模型名 → ``ResearchResultDraft`` 归属完整 → 保存确认
  才返回 ``complete``，``artifact_ref`` 为 ``id@version``、``input_digest`` 原样回报；
- 前置失败：输入指纹缺失、归属事实不可读、空文本、模型名不可得、保存未确认；
- 边界拒绝与模型失败映射为稳定 ``leaf_*`` 错误码；
- T-03：绑定冻结绝对期限逐回合派生真实 ``timeout``（缺失/耗尽零外发失败）、受治理
  公开事件回调（异常隔离 / 取消传播）、SDK 超时与截断协议失败如实失败且零保存；
- ``CancelledError`` 原样传播（R2）；失败一律零保存 / 不伪造 ``complete``（R21）。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.exceptions import RunTimedOut
from pydantic_ai.messages import (
    FinalResultEvent,
    FunctionToolCallEvent,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
)

from core.agent_types import (
    AgentErrorCode,
    AgentServiceError,
    PlanNodeSpec,
    ResearchResult,
    SourceKind,
    SourceVersionRef,
)
from core.model_management_types import ExecutionSnapshot, ModelTurnProtocolError
from services.agent_service import leaf_agent
from services.agent_service.leaf_agent import (
    LEAF_ATTRIBUTION_UNAVAILABLE,
    LEAF_BINDING_REVISION_STALE,
    LEAF_BINDING_REVISION_UNAVAILABLE,
    LEAF_DEADLINE_EXHAUSTED,
    LEAF_EMPTY_RESULT,
    LEAF_INPUT_DIGEST_MISSING,
    LEAF_MODEL_FAILED,
    LEAF_MODEL_PROTOCOL_FAILED,
    LEAF_MODEL_REF_UNAVAILABLE,
    LEAF_POLICY_DENIED,
    LEAF_PREDECESSOR_ARTIFACT_UNAVAILABLE,
    LEAF_RESULT_SAVE_FAILED,
    LEAF_ROLE_PROMPT_UNAVAILABLE,
    LEAF_TIMEOUT_UNAVAILABLE,
    LEAF_TOOL_SETUP_FAILED,
    build_leaf_agent_runner,
    run_leaf_agent,
)
from services.agent_service.leaf_runtime import LeafModelBinding
from services.agent_service.mcp_tools import ToolCallOutcome as _ToolCallOutcome
from services.agent_service.mcp_tools import ToolCallState as _ToolCallState
from services.agent_service.model import GatewayModel
from services.agent_service.workflows import LeafExecutionContext

pytestmark = pytest.mark.unit

_DIGEST = "a" * 64
#: autouse fixture 会替换模块函数；此处保留原始实现供「读设置页」用例直接验证。
_ORIGINAL_READ_SAVED_ROLE_PROMPT = leaf_agent._read_saved_role_prompt
_SNAPSHOT = ExecutionSnapshot(
    snapshot_id="snap-1",
    run_id=None,
    config_revision=1,
    routes=(
        {
            "scenario_id": "agent_research",
            "candidates": ({"connection_id": "conn-1"},),
        },
    ),
    connections=({"connection_id": "conn-1", "context_window": 8192},),
    business_contract_versions={},
    security_scope_version=1,
    created_at="2026-11-20T00:00:00.000000Z",
)


def _context(
    *,
    input_digest: str | None = _DIGEST,
    source_refs: tuple[Any, ...] = (),
    input_revision: int | None = 2,
    predecessor_artifacts: tuple[tuple[str, str], ...] = (),
) -> LeafExecutionContext:
    return LeafExecutionContext(
        execution_id="execution-1",
        round_id="round-1",
        plan_digest="b" * 64,
        node=PlanNodeSpec(node_id="n1", target="梳理公司业务", role_key="analyst", source_refs=source_refs),
        input_digest=input_digest,
        input_revision=input_revision,
        predecessor_artifacts=predecessor_artifacts,
    )


def _binding(*, deadline: float | None = None) -> LeafModelBinding:
    return LeafModelBinding(
        executor=cast(Any, object()),
        snapshot=_SNAPSHOT,
        binding={},
        deadline=time.monotonic() + 60.0 if deadline is None else deadline,
    )


class _Facts:
    def __init__(self, *, session_id: str = "session-1", input_revision: int = 2) -> None:
        self.user_scope = "user-1"
        self.owner_epoch = 3
        self.run_epoch = 2
        self.session_id = session_id
        self.input_revision = input_revision


class _Artifact:
    def __init__(self, **overrides: Any) -> None:
        self.artifact_id = "art-pre"
        self.artifact_version = 1
        self.session_id = "session-1"
        self.execution_id = "execution-1"
        self.round_id = "round-1"
        self.input_revision = 2
        self.body_available = True
        self.body_text = "前置成果正文"
        self.summary = "前置摘要"
        for key, value in overrides.items():
            setattr(self, key, value)


class _Dao:
    def __init__(
        self,
        *,
        facts: _Facts | None = None,
        facts_error: Exception | None = None,
        save_result: Any = None,
        save_error: Exception | None = None,
        artifacts: dict[str, Any] | None = None,
    ) -> None:
        self.facts = facts or _Facts()
        self.facts_error = facts_error
        self.save_result = save_result
        self.save_error = save_error
        self.artifacts = artifacts or {}
        self.fact_calls = 0
        self.artifact_queries: list[Any] = []
        self.drafts: list[Any] = []

    async def read_leaf_dispatch_facts(self, execution_id: str, round_id: str) -> Any:
        self.fact_calls += 1
        if self.facts_error is not None:
            raise self.facts_error
        return self.facts

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        self.artifact_queries.append(query)
        artifact = self.artifacts.get(query.artifact_id)
        if artifact is None:
            raise RuntimeError("artifact missing")
        return artifact

    async def admit_usage(self, request: Any) -> Any:
        return SimpleNamespace(execution_id=request.execution_id)

    async def save_research_result(self, draft: Any) -> Any:
        self.drafts.append(draft)
        if self.save_error is not None:
            raise self.save_error
        if self.save_result is None:
            return ResearchResult.from_draft(draft, artifact_id="art-1", artifact_version=2)
        return self.save_result

    async def read_boundary_source_keys(self, *, user_scope: str, execution_id: str) -> Any:
        return frozenset(), frozenset()


class _RunResult:
    def __init__(self, *, output: str, messages: list[Any]) -> None:
        self.output = output
        self._messages = messages

    def all_messages(self) -> list[Any]:
        return self._messages


class _FakeStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = events

    def __aiter__(self) -> Any:
        async def _iterate() -> Any:
            for event in self._events:
                yield event

        return _iterate()


class _FakeAgent:
    instances: list[_FakeAgent] = []
    output: str = "研究结论"
    outputs: list[Any] | None = None
    messages: list[Any] = [ModelResponse([], model_name="provider-model-1")]
    error: Exception | None = None
    handler_events: list[Any] | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.run_calls = 0
        self.run_kwargs: list[dict[str, Any]] = []
        type(self).instances.append(self)

    async def run(
        self,
        prompt: str | None = None,
        *,
        deps: Any,
        message_history: list[Any] | None = None,
        deferred_tool_results: Any = None,
        model_settings: Any = None,
        timeout: float | None = None,
        event_stream_handler: Any = None,
    ) -> _RunResult:
        self.run_calls += 1
        self.prompt = prompt
        self.deps = deps
        self.message_history = message_history
        self.deferred_tool_results = deferred_tool_results
        self.run_kwargs.append(
            {"model_settings": model_settings, "timeout": timeout, "event_stream_handler": event_stream_handler}
        )
        handler_events = type(self).handler_events
        if event_stream_handler is not None and handler_events is not None:
            await event_stream_handler(None, _FakeStream(list(handler_events)))
        error = type(self).error
        if error is not None:
            raise error
        output = type(self).outputs.pop(0) if type(self).outputs else type(self).output
        return _RunResult(output=output, messages=list(type(self).messages))


@pytest.fixture(autouse=True)
def _fake_agent(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAgent]:
    _FakeAgent.instances = []
    _FakeAgent.output = "研究结论"
    _FakeAgent.outputs = None
    _FakeAgent.messages = [ModelResponse([], model_name="provider-model-1")]
    _FakeAgent.error = None
    _FakeAgent.handler_events = None
    monkeypatch.setattr(leaf_agent, "Agent", _FakeAgent)
    # 角色 Prompt 读取默认走设置页（同步配置 IO）：单元测试以替身冻结，避免读真实配置。
    monkeypatch.setattr(
        leaf_agent,
        "_read_saved_role_prompt",
        lambda role_key: leaf_agent.RolePromptIdentity(text=f"saved-{role_key}-prompt", version="v-default"),
    )
    return _FakeAgent


def _model() -> GatewayModel:
    return cast(GatewayModel, object())


class TestRunLeafAgentSuccess:
    async def test_saves_result_with_authoritative_attribution(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "complete"
        assert outcome.error_code is None
        assert outcome.artifact_ref == "art-1@2"
        assert outcome.input_digest == _DIGEST
        assert len(dao.drafts) == 1
        draft = dao.drafts[0]
        assert draft.user_scope == "user-1"
        assert draft.session_id == "session-1"
        assert draft.input_revision == 2
        assert draft.execution_id == "execution-1"
        assert draft.round_id == "round-1"
        assert draft.node_id == "n1"
        assert draft.actual_input_digest == _DIGEST
        assert draft.actual_model_ref == "provider-model-1"
        assert draft.result_kind == "explanation"
        assert draft.body_text == "研究结论"
        assert draft.execution_state == "complete"
        assert draft.goal_coverage == "unknown"
        assert draft.convergence_confirmed is False
        assert draft.verification_state == "unverified"
        assert draft.source_available_state == "unknown"
        agent = _fake_agent.instances[0]
        assert agent.kwargs["defer_model_check"] is True
        assert agent.kwargs["retries"] == 0
        assert agent.kwargs["tools"] == ()
        assert agent.kwargs["name"] == "qtrading_research"
        assert "saved-sub-prompt" in agent.kwargs["instructions"]
        assert "analyst" in agent.kwargs["instructions"]
        assert agent.deps.session_id == "session-1"

    async def test_instructions_use_saved_role_prompt_default_plus_append(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=dao,
            role_prompt_reader=lambda role_key: leaf_agent.RolePromptIdentity(
                text="默认子角色正文\n\n优先解释行业景气度", version="v-saved"
            ),
        )

        instructions = _fake_agent.instances[0].kwargs["instructions"]
        assert "默认子角色正文" in instructions
        assert "优先解释行业景气度" in instructions

    async def test_role_prompt_unavailable_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        def _boom(role_key: str) -> leaf_agent.RolePromptIdentity:
            raise RuntimeError("config unreadable")

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao, role_prompt_reader=_boom)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_ROLE_PROMPT_UNAVAILABLE
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_role_prompt_cancelled_propagates(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        def _cancel(role_key: str) -> leaf_agent.RolePromptIdentity:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as exc:
            await run_leaf_agent(_model(), _context(), _binding(), dao=dao, role_prompt_reader=_cancel)

        assert exc.value.args == ()
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_blank_role_prompt_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()
        outcome = await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=dao,
            role_prompt_reader=lambda role_key: leaf_agent.RolePromptIdentity(text="  \n", version="v-saved"),
        )

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_ROLE_PROMPT_UNAVAILABLE
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_node_source_refs_are_saved_with_result(self, _fake_agent: type[_FakeAgent]) -> None:
        ref = SourceVersionRef(
            source_kind=SourceKind.USER_MESSAGE, source_id="src-1", source_version="1", source_digest="c" * 64
        )
        dao = _Dao()

        await run_leaf_agent(_model(), _context(source_refs=(ref,)), _binding(), dao=dao)

        assert dao.drafts[0].source_refs == (ref,)

    async def test_predecessor_artifacts_are_read_and_saved_as_parent_refs(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao(artifacts={"art-pre": _Artifact()})

        outcome = await run_leaf_agent(
            _model(),
            _context(predecessor_artifacts=(("a", "art-pre@1"),)),
            _binding(),
            dao=dao,
        )

        assert outcome.state == "complete"
        assert len(dao.artifact_queries) == 1
        assert dao.artifact_queries[0].artifact_id == "art-pre"
        assert dao.artifact_queries[0].artifact_version == 1
        assert dao.drafts[0].parent_artifact_refs == ("art-pre@1",)
        prompt = _fake_agent.instances[0].prompt
        assert prompt is not None
        assert "前置摘要" in prompt


class TestRunLeafAgentFailures:
    async def test_missing_input_digest_fails_before_facts_and_save(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(input_digest=None), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_INPUT_DIGEST_MISSING
        assert dao.fact_calls == 0
        assert dao.drafts == []
        assert _fake_agent.instances == []

    async def test_attribution_unreadable_fails_without_model_call(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao(facts_error=RuntimeError("db down"))

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_ATTRIBUTION_UNAVAILABLE
        assert dao.drafts == []
        assert _fake_agent.instances == []

    async def test_empty_output_fails_without_save(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.output = "   "
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_EMPTY_RESULT
        assert dao.drafts == []

    async def test_missing_actual_model_name_fails_without_save(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.messages = [ModelResponse([], model_name=None)]
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_MODEL_REF_UNAVAILABLE
        assert dao.drafts == []

    async def test_save_not_confirmed_never_reports_complete(self) -> None:
        dao = _Dao(save_result=SimpleNamespace(final_output_saved=False))

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_RESULT_SAVE_FAILED
        assert outcome.artifact_ref is None

    async def test_save_error_maps_to_save_failed(self) -> None:
        dao = _Dao(save_error=RuntimeError("write failed"))

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_RESULT_SAVE_FAILED

    async def test_policy_denied_maps_to_leaf_policy_denied(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.error = AgentServiceError(
            AgentErrorCode.POLICY_DENIED, "context window unknown", "research_boundary"
        )
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_POLICY_DENIED
        assert dao.drafts == []

    async def test_model_failure_maps_to_model_failed(self, _fake_agent: type[_FakeAgent]) -> None:
        from pydantic_ai import UnexpectedModelBehavior

        _fake_agent.error = UnexpectedModelBehavior("chat turn failed", "partial")
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_MODEL_FAILED
        assert dao.drafts == []

    async def test_cancelled_error_propagates(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.error = asyncio.CancelledError()
        dao = _Dao()

        with pytest.raises(asyncio.CancelledError) as exc:
            await run_leaf_agent(_model(), _context(), _binding(), dao=dao)
        assert type(exc.value) is asyncio.CancelledError

    async def test_missing_binding_revision_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(input_revision=None), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_BINDING_REVISION_UNAVAILABLE
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_stale_binding_revision_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao(facts=_Facts(input_revision=3))

        outcome = await run_leaf_agent(_model(), _context(input_revision=2), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_BINDING_REVISION_STALE
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_predecessor_artifact_unavailable_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao(artifacts={"art-pre": _Artifact(body_available=False)})

        outcome = await run_leaf_agent(
            _model(), _context(predecessor_artifacts=(("a", "art-pre@1"),)), _binding(), dao=dao
        )

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_PREDECESSOR_ARTIFACT_UNAVAILABLE
        assert _fake_agent.instances == []
        assert dao.drafts == []


class TestLeafDeadlineAndPublicEvents:
    """T-03：真实期限 / 公开事件回调 / 截断与收尾未核如实失败（R21）。"""

    async def test_missing_verified_deadline_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(deadline=0.0), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_TIMEOUT_UNAVAILABLE
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_expired_deadline_fails_without_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(deadline=time.monotonic() - 1.0), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_DEADLINE_EXHAUSTED
        assert _fake_agent.instances == []
        assert dao.drafts == []

    async def test_run_gets_frozen_remaining_timeout(self, _fake_agent: type[_FakeAgent]) -> None:
        dao = _Dao()

        await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        run_kwargs = _fake_agent.instances[0].run_kwargs[0]
        timeout = cast(float, run_kwargs["timeout"])
        assert 0 < timeout <= 60.0
        assert run_kwargs["model_settings"] == {"timeout": timeout}
        assert run_kwargs["event_stream_handler"] is None

    async def test_run_timed_out_maps_to_deadline_exhausted(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.error = RunTimedOut("timed out")
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_DEADLINE_EXHAUSTED
        assert dao.drafts == []

    async def test_protocol_failure_maps_to_model_protocol_failed(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.error = ModelTurnProtocolError("invalid_field", "$.finish_reason")
        dao = _Dao()

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao)

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_MODEL_PROTOCOL_FAILED
        assert dao.drafts == []

    async def test_public_events_are_governed_and_isolated(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.handler_events = [
            PartStartEvent(index=0, part=TextPart(content="首片")),
            PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="续写")),
            FunctionToolCallEvent(
                part=ToolCallPart(tool_name="web_search", args={"query": "secret"}, tool_call_id="call-1")
            ),
            FinalResultEvent(tool_name=None, tool_call_id=None),
        ]
        dao = _Dao()
        seen: list[Any] = []

        def _sink(event: Any) -> None:
            seen.append(event)
            if event.kind == "tool_call":
                raise RuntimeError("observer failed")

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao, on_event=_sink)

        assert outcome.state == "complete"
        assert dao.drafts[0].body_text == "研究结论"
        assert [event.kind for event in seen] == ["text_delta", "text_delta", "tool_call", "finish"]
        assert [event.sequence for event in seen] == [1, 2, 3, 4]
        assert seen[0].text_delta == "首片"
        assert seen[1].text_delta == "续写"
        assert seen[2].tool_name == "web_search"
        assert seen[2].text_delta is None
        assert _fake_agent.instances[0].run_kwargs[0]["event_stream_handler"] is not None

    async def test_waitable_event_sink_is_awaited(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.handler_events = [
            PartStartEvent(index=0, part=TextPart(content="a")),
            FinalResultEvent(tool_name=None, tool_call_id=None),
        ]
        dao = _Dao()
        awaited: list[str] = []

        async def _sink(event: Any) -> None:
            await asyncio.sleep(0)
            awaited.append(event.kind)

        outcome = await run_leaf_agent(_model(), _context(), _binding(), dao=dao, on_event=_sink)

        assert outcome.state == "complete"
        assert awaited == ["text_delta", "finish"]

    async def test_event_sink_cancellation_propagates(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.handler_events = [FinalResultEvent(tool_name=None, tool_call_id=None)]
        dao = _Dao()

        def _cancel(_event: Any) -> None:
            raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError) as excinfo:
            await run_leaf_agent(_model(), _context(), _binding(), dao=dao, on_event=_cancel)
        assert type(excinfo.value) is asyncio.CancelledError


class TestBuildLeafAgentRunner:
    async def test_runner_without_dao_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(leaf_agent, "_resolve_agent_dao", lambda _dao: None)

        outcome = await build_leaf_agent_runner()(_model(), _context(), _binding())

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_ATTRIBUTION_UNAVAILABLE


class _ToolRuntimeStub:
    def __init__(self, outcome: Any) -> None:
        self.tools: tuple[Any, ...] = ()
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def execute(self, *, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> Any:
        self.calls.append({"tool_name": tool_name, "arguments": arguments, "tool_call_id": tool_call_id})
        return self.outcome


class TestLeafToolLoop:
    async def test_runtime_injection_runs_tool_loop_and_records_unknown(self, _fake_agent: type[_FakeAgent]) -> None:
        runtime = _ToolRuntimeStub(_ToolCallOutcome(_ToolCallState.UNKNOWN, None, "op-1"))
        _fake_agent.outputs = [
            DeferredToolRequests(calls=[ToolCallPart(tool_name="search", args={"query": "x"}, tool_call_id="call-1")]),
            "研究结论",
        ]
        dao = _Dao()

        outcome = await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=dao,
            tool_runtime_factory=lambda scope: runtime,  # type: ignore[arg-type,return-value]
        )

        assert outcome.state == "complete"
        assert runtime.calls == [{"tool_name": "search", "arguments": {"query": "x"}, "tool_call_id": "call-1"}]
        assert dao.drafts[0].unknown_operation_refs == ("op-1",)
        assert _fake_agent.instances[0].kwargs["tools"] == ()

    async def test_each_tool_round_uses_frozen_remaining_deadline(self, _fake_agent: type[_FakeAgent]) -> None:
        runtime = _ToolRuntimeStub(_ToolCallOutcome(_ToolCallState.COMPLETED, "ok", "op-1"))
        _fake_agent.outputs = [
            DeferredToolRequests(calls=[ToolCallPart(tool_name="search", args={"query": "x"}, tool_call_id="call-1")]),
            "研究结论",
        ]
        dao = _Dao()

        outcome = await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=dao,
            tool_runtime_factory=lambda scope: runtime,  # type: ignore[arg-type,return-value]
        )

        assert outcome.state == "complete"
        per_turn = _fake_agent.instances[0].run_kwargs
        assert len(per_turn) == 2
        for kwargs in per_turn:
            timeout = cast(float, kwargs["timeout"])
            assert 0 < timeout <= 60.0
            assert kwargs["model_settings"] == {"timeout": timeout}

    async def test_runtime_setup_failure_is_zero_outbound(self, _fake_agent: type[_FakeAgent]) -> None:
        def _boom(scope: Any) -> Any:
            raise RuntimeError("mcp manager unavailable")

        outcome = await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=_Dao(),
            tool_runtime_factory=_boom,
        )

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_TOOL_SETUP_FAILED
        assert _fake_agent.instances == []

    async def test_tool_budget_failure_maps_to_stable_code(self, _fake_agent: type[_FakeAgent]) -> None:
        _fake_agent.error = AgentServiceError(AgentErrorCode.BUDGET_BLOCKED, "tool_round_limit", "tool_loop")
        dao = _Dao()

        outcome = await run_leaf_agent(
            _model(),
            _context(),
            _binding(),
            dao=dao,
            tool_runtime_factory=lambda scope: _ToolRuntimeStub(_ToolCallOutcome(_ToolCallState.COMPLETED, {}, "op-1")),
        )

        assert outcome.state == "failed"
        assert outcome.error_code == "tool_budget_exceeded"
        assert dao.drafts == []


class TestSavedRolePromptSource:
    def test_reads_default_text_plus_saved_append_and_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from utils.config_handler import ConfigHandler
        from utils.config_models import ROLE_PROMPT_DEFAULTS

        default_text, default_version = ROLE_PROMPT_DEFAULTS[leaf_agent.LEAF_ROLE_PROMPT_KEY]
        monkeypatch.setattr(ConfigHandler, "get_ai_role_prompt_append", staticmethod(lambda role_key: "优先解释景气度"))
        monkeypatch.setattr(ConfigHandler, "get_ai_role_prompt_version", staticmethod(lambda role_key: "v-saved"))

        monkeypatch.setattr(leaf_agent, "_read_saved_role_prompt", _ORIGINAL_READ_SAVED_ROLE_PROMPT)
        identity = leaf_agent._read_saved_role_prompt(leaf_agent.LEAF_ROLE_PROMPT_KEY)

        assert identity.text == f"{default_text}\n\n优先解释景气度"
        assert identity.version == "v-saved"

    def test_blank_append_falls_back_to_default_text_and_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from utils.config_handler import ConfigHandler
        from utils.config_models import ROLE_PROMPT_DEFAULTS

        default_text, default_version = ROLE_PROMPT_DEFAULTS[leaf_agent.LEAF_ROLE_PROMPT_KEY]
        monkeypatch.setattr(ConfigHandler, "get_ai_role_prompt_append", staticmethod(lambda role_key: "  \n"))
        monkeypatch.setattr(ConfigHandler, "get_ai_role_prompt_version", staticmethod(lambda role_key: ""))

        monkeypatch.setattr(leaf_agent, "_read_saved_role_prompt", _ORIGINAL_READ_SAVED_ROLE_PROMPT)
        identity = leaf_agent._read_saved_role_prompt(leaf_agent.LEAF_ROLE_PROMPT_KEY)

        assert identity.text == default_text
        assert identity.version == default_version

    def test_authorized_connection_id_reads_consent_binding(self) -> None:
        binding = LeafModelBinding(
            executor=cast(Any, object()),
            snapshot=_SNAPSHOT,
            binding={"data_consent": {"connection_id": "conn-1"}},
            deadline=time.monotonic() + 60.0,
        )

        assert leaf_agent._authorized_connection_id(binding) == "conn-1"

    @pytest.mark.parametrize("binding", [{}, {"data_consent": {}}, {"data_consent": {"connection_id": ""}}])
    def test_authorized_connection_id_absent_or_blank_is_none(self, binding: dict[str, Any]) -> None:
        model_binding = LeafModelBinding(
            executor=cast(Any, object()),
            snapshot=_SNAPSHOT,
            binding=binding,
            deadline=time.monotonic() + 60.0,
        )

        assert leaf_agent._authorized_connection_id(model_binding) is None
