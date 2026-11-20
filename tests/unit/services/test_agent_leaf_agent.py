"""叶 Agent 主体契约（DD §3.5 / §10.1 / §10.3）。

覆盖：

- 成功：单次 ``Agent.run`` → 实际模型名 → ``ResearchResultDraft`` 归属完整 → 保存确认
  才返回 ``complete``，``artifact_ref`` 为 ``id@version``、``input_digest`` 原样回报；
- 前置失败：输入指纹缺失、归属事实不可读、空文本、模型名不可得、保存未确认；
- 边界拒绝与模型失败映射为稳定 ``leaf_*`` 错误码；
- ``CancelledError`` 原样传播（R2）；失败一律零保存 / 不伪造 ``complete``（R21）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.messages import ModelResponse

from core.agent_types import AgentErrorCode, AgentServiceError, PlanNodeSpec, ResearchResult
from core.model_management_types import ExecutionSnapshot
from services.agent_service import leaf_agent
from services.agent_service.leaf_agent import (
    LEAF_ATTRIBUTION_UNAVAILABLE,
    LEAF_EMPTY_RESULT,
    LEAF_INPUT_DIGEST_MISSING,
    LEAF_MODEL_FAILED,
    LEAF_MODEL_REF_UNAVAILABLE,
    LEAF_POLICY_DENIED,
    LEAF_RESULT_SAVE_FAILED,
    build_leaf_agent_runner,
    run_leaf_agent,
)
from services.agent_service.leaf_runtime import LeafModelBinding
from services.agent_service.model import GatewayModel
from services.agent_service.workflows import LeafExecutionContext

pytestmark = pytest.mark.unit

_DIGEST = "a" * 64
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


def _context(*, input_digest: str | None = _DIGEST) -> LeafExecutionContext:
    return LeafExecutionContext(
        execution_id="execution-1",
        round_id="round-1",
        plan_digest="b" * 64,
        node=PlanNodeSpec(node_id="n1", target="梳理公司业务", role_key="analyst"),
        input_digest=input_digest,
    )


def _binding() -> LeafModelBinding:
    return LeafModelBinding(executor=cast(Any, object()), snapshot=_SNAPSHOT, binding={})


class _Facts:
    def __init__(self, *, session_id: str = "session-1", input_revision: int = 2) -> None:
        self.user_scope = "user-1"
        self.owner_epoch = 3
        self.run_epoch = 2
        self.session_id = session_id
        self.input_revision = input_revision


class _Dao:
    def __init__(
        self,
        *,
        facts: _Facts | None = None,
        facts_error: Exception | None = None,
        save_result: Any = None,
        save_error: Exception | None = None,
    ) -> None:
        self.facts = facts or _Facts()
        self.facts_error = facts_error
        self.save_result = save_result
        self.save_error = save_error
        self.fact_calls = 0
        self.drafts: list[Any] = []

    async def read_leaf_dispatch_facts(self, execution_id: str, round_id: str) -> Any:
        self.fact_calls += 1
        if self.facts_error is not None:
            raise self.facts_error
        return self.facts

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


class _FakeAgent:
    instances: list[_FakeAgent] = []
    output: str = "研究结论"
    messages: list[Any] = [ModelResponse([], model_name="provider-model-1")]
    error: Exception | None = None

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.run_calls = 0
        type(self).instances.append(self)

    async def run(self, prompt: str, *, deps: Any) -> _RunResult:
        self.run_calls += 1
        self.prompt = prompt
        self.deps = deps
        if type(self).error is not None:
            raise type(self).error
        return _RunResult(output=type(self).output, messages=list(type(self).messages))


@pytest.fixture(autouse=True)
def _fake_agent(monkeypatch: pytest.MonkeyPatch) -> type[_FakeAgent]:
    _FakeAgent.instances = []
    _FakeAgent.output = "研究结论"
    _FakeAgent.messages = [ModelResponse([], model_name="provider-model-1")]
    _FakeAgent.error = None
    monkeypatch.setattr(leaf_agent, "Agent", _FakeAgent)
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
        assert agent.deps.session_id == "session-1"


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

        with pytest.raises(asyncio.CancelledError):
            await run_leaf_agent(_model(), _context(), _binding(), dao=dao)


class TestBuildLeafAgentRunner:
    async def test_runner_without_dao_fails_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(leaf_agent, "_resolve_agent_dao", lambda _dao: None)

        outcome = await build_leaf_agent_runner()(_model(), _context(), _binding())

        assert outcome.state == "failed"
        assert outcome.error_code == LEAF_ATTRIBUTION_UNAVAILABLE
