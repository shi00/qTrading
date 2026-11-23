"""S3 ScenarioExecutor.execute_chat_turn 单测（B-02-3，HANDOFF §6.1 / DD §10.1，SQ-02）。

覆盖：
- 成功链路：真实流事件在 final 前经 on_event 可等待送达（SQ-02「实际 delta
  有界事件」）；ChatTurnOutcome 契约：transport=ok / settlement=settled /
  model_call → invocation → attempt 身份关联；adapter 收到 stream=True /
  response_format 按 output_mode 生成（native → 真实 json_schema 封装；tool /
  prompted / text → 无）；用量观察 + succeeded 终态；
- binding 契约（受理冻结模型绑定）：scenario_id / task_id 缺失 fail-closed
  （ValueError，零副作用——不建立 invocation、不派发）；
- 必需登记失败零出站（SQ-02）：begin_attempt persisted=False → invoke_chat 零
  调用 + 明确失败 + halt（第二候选零派发）；
- §6.4-5 已公开分片后失败：保 partial/unknown 及原 attempt——不重试（单次
  invoke_chat）不换候选（第二候选零调用）；未公开分片的瞬态失败按「费用未知」
  收敛（不释放预留、不自动重试、不换候选，T-04）；
- 拒绝不换候选规避（DD §10.1）：refused 回合 failed + transport=ok + 完整
  response + halt；用量已观测不丢弃（R21）；
- 背压：事件 handler 未完成前生产不推进（on_event await 语义透传，§6.4）；
- 取消传播（R2）：CancelledError 始终传播，不包装为失败；F19 cancelled 终态
  收敛；
- 非聊天适配器（FinBERT 等不接 ChatInferenceAdapter 协议）派发前淘汰（零预留
  副作用）；
- 无路由失败（F16：零尝试请求同样建立 invocation）；结算未确认投影 deferred。
"""

import asyncio
import inspect
import logging
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.agent_types import (
    DataAttemptOutcome,
    DataAttemptReceipt,
    data_consent_source_scope_digest,
)
from core.model_management_types import (
    ChatMessage,
    ChatOutputMode,
    ChatStreamEvent,
    ChatTurnRequest,
    ChatTurnResponse,
    ExecutionSnapshot,
    FunctionToolSpec,
    ImageContentPart,
    NormalizedLLMUsage,
    ModelTurnProtocolError,
    PersistenceOutcome,
    ResolvedConnection,
    TextContentPart,
)
from services.ai_service.litellm_client import LiteLLMChatAdapter, LiteLLMClient
from services.ai_service.scenario_executor import ScenarioExecutor, _AttemptLedgerState
from services.ai_service.usage_recorder import ReservationOutcome

pytestmark = pytest.mark.unit

_REQUEST_DEADLINE_BUDGET_S = 30.0


def _remote_conn(connection_id: str = "conn-remote", concurrency: int = 4) -> ResolvedConnection:
    return ResolvedConnection(
        connection_id=connection_id,
        connection_revision=1,
        adapter_kind="litellm_chat",
        provider="zhipu",
        model="glm-4-air",
        api_base=None,
        adapter_options={"temperature": 0.2},
        context_window=8192,
        concurrency_limit=concurrency,
        credential_ref="keyring:item",
        credential_version="v1",
    )


def _candidate(index: int, connection_id: str, task_id: str = "sentiment") -> dict:
    return {
        "candidate_index": index,
        "task_id": task_id,
        "connection_id": connection_id,
        "target_fingerprint": "fp-1",
        "purpose": "sentiment",
    }


_CHAT_CAPABILITY_EVIDENCE: dict[str, dict[str, Any]] = {
    # 检视 M06：chat 入口按请求必需能力核验连接证据（support + 证据版本）
    "stream": {"support": "supported", "source": "validation", "connection_revision": 1},
    "tools": {"support": "supported", "source": "validation", "connection_revision": 1},
}


def _snapshot(
    candidates: tuple[dict, ...],
    connections: tuple[ResolvedConnection, ...],
    *,
    capabilities: dict[str, dict[str, Any]] | None = None,
) -> ExecutionSnapshot:
    evidence = _CHAT_CAPABILITY_EVIDENCE if capabilities is None else capabilities
    serialized = tuple(
        {
            "connection_id": c.connection_id,
            "connection_revision": c.connection_revision,
            "adapter_kind": c.adapter_kind,
            "provider": c.provider,
            "model": c.model,
            "api_base": c.api_base,
            "adapter_options": dict(c.adapter_options),
            "context_window": c.context_window,
            "concurrency_limit": c.concurrency_limit,
            "credential_ref": c.credential_ref,
            "credential_version": c.credential_version,
            "enabled": True,
            "capabilities": evidence,
        }
        for c in connections
    )
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=5,
        routes=(
            {
                "scenario_id": "news_sentiment",
                "route_revision": 1,
                "candidates": candidates,
            },
        ),
        connections=serialized,
        business_contract_versions={"sentiment": "v1"},
        security_scope_version=1,
        created_at="2026-10-16T00:00:00Z",
    )


def _binding(**overrides: Any) -> dict[str, Any]:
    return {"scenario_id": "news_sentiment", "task_id": "sentiment", **overrides}


def _chat_request(
    model_call_id: str = "mc-1",
    output_schema: dict[str, Any] | None = None,
    *,
    output_mode: ChatOutputMode = "text",
    output_tools: tuple[FunctionToolSpec, ...] = (),
) -> ChatTurnRequest:
    return ChatTurnRequest(
        model_call_id=model_call_id,
        messages=(ChatMessage(role="user", content_parts=(TextContentPart(text="研究问题"),)),),
        instructions=("系统提示",),
        tools=(),
        output_schema=output_schema,
        frozen_params={},
        max_output_tokens=256,
        deadline=asyncio.get_running_loop().time() + _REQUEST_DEADLINE_BUDGET_S,
        output_mode=output_mode,
        output_tools=output_tools,
    )


def _usage() -> NormalizedLLMUsage:
    return NormalizedLLMUsage(
        input_total_tokens=10,
        uncached_input_tokens=10,
        cache_read_tokens=None,
        cache_write_tokens=None,
        output_tokens=5,
    )


def _chat_ok_response(text: str = "ok", usage: NormalizedLLMUsage | None = None) -> ChatTurnResponse:
    return ChatTurnResponse(
        text=text,
        finish_reason="final",
        actual_model="glm-4-air",
        usage_observed=usage is not None,
        usage=usage,
        transport_state="ok",
    )


def _text_event(sequence: int, delta: str) -> ChatStreamEvent:
    return ChatStreamEvent(sequence=sequence, kind="text", text_delta=delta)


def _recorder_mock(persisted: bool = True) -> MagicMock:
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.ensure_invocation = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.try_reserve = AsyncMock(
        return_value=ReservationOutcome(
            persisted=persisted,
            error_code=None if persisted else "DB_UNAVAILABLE",
            reservation=MagicMock() if persisted else None,
        )
    )
    recorder.mark_dispatched = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.begin_attempt = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    recorder.submit_terminal = AsyncMock(
        return_value=PersistenceOutcome(persisted=persisted, error_code=None if persisted else "DB_UNAVAILABLE")
    )
    return recorder


def _executor(adapter: Any, recorder: Any, **overrides: Any) -> ScenarioExecutor:
    config_service = MagicMock()
    config_service.authorize_attempt = AsyncMock(
        return_value=MagicMock(attempt_id="att-x", connection_id="conn-remote", security_revision=1, granted_at="t")
    )
    compatibility = MagicMock()
    compatibility.resolve_execution_params = MagicMock(
        return_value=MagicMock(compatible=True, reason=None, resolved_options={"temperature": 0.2})
    )
    kwargs = {
        "config_service": config_service,
        "recorder": recorder,
        "compatibility": compatibility,
        "adapters": {"litellm_chat": adapter},
        "retry_attempts": 1,
        "retry_max_attempts": 2,
        "cooldown_seconds": 30.0,
    }
    kwargs.update(overrides)
    return ScenarioExecutor(**kwargs)


class _ScriptedChatAdapter:
    """invoke_chat 测试替身（B-02-3）：按调用序编程 (events, error, response)，
    逐事件经 on_event 可等待送达；记录每次调用的 attempt 身份与参数。"""

    def __init__(self, script: list[tuple[tuple[ChatStreamEvent, ...], Exception | None, ChatTurnResponse | None]]):
        self.script = list(script)
        self.invoke_calls: list[dict[str, Any]] = []

    async def invoke_chat(
        self, request, connection, deadline, attempt_context, *, stream=True, response_format=None, on_event=None
    ) -> ChatTurnResponse:
        self.invoke_calls.append(
            {
                "attempt_id": attempt_context.attempt_id,
                "connection_id": connection.connection_id,
                "deadline": deadline,
                "stream": stream,
                "response_format": response_format,
                "model_call_id": request.model_call_id,
                "request": request,
            }
        )
        events, error, response = self.script.pop(0)
        for event in events:
            if on_event is not None:
                result = on_event(event)
                if inspect.isawaitable(result):
                    await result
        if error is not None:
            raise error
        assert response is not None
        return response


class _HangingChatAdapter:
    """invoke_chat 阻塞替身（取消传播测试）：外发开始后挂起直至被取消。"""

    def __init__(self) -> None:
        self.invoke_calls: list[str] = []
        self.started = asyncio.Event()

    async def invoke_chat(self, request, connection, deadline, attempt_context, **kwargs) -> ChatTurnResponse:
        self.invoke_calls.append(attempt_context.attempt_id)
        self.started.set()
        await asyncio.Event().wait()  # 永不完成——等待取消（R2）
        raise AssertionError("unreachable")


class TestChatSuccessPath:
    @pytest.mark.asyncio
    async def test_success_streams_events_and_outcome_contract(self):
        usage = _usage()
        events = (
            _text_event(1, "ab"),
            ChatStreamEvent(sequence=2, kind="finish", finish_reason="final", usage=usage),
        )
        adapter = _ScriptedChatAdapter([(events, None, _chat_ok_response(usage=usage))])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        seen: list[ChatStreamEvent] = []

        async def on_event(event: ChatStreamEvent) -> None:
            seen.append(event)

        outcome = await executor.execute_chat_turn(
            _chat_request(output_schema={"type": "object"}, output_mode="native"),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
            on_event=on_event,
        )

        assert outcome.status == "succeeded"
        assert outcome.transport_state == "ok"
        assert outcome.settlement_state == "settled"
        assert outcome.error_code is None
        assert outcome.partial_text is None
        assert outcome.response is not None and outcome.response.finish_reason == "final"
        # 身份关联：model_call → invocation（一回合一 invocation）→ 实际 attempt
        assert outcome.model_call_id == "mc-1"
        assert outcome.invocation_id == "mc-1"
        assert outcome.attempt_id == adapter.invoke_calls[0]["attempt_id"]
        # SQ-02：真实 delta 在 final 前经 on_event 送达
        assert [e.text_delta for e in seen if e.kind == "text"] == ["ab"]
        assert seen[-1].kind == "finish"
        # 出口参数透传：stream=True / native 模式的真实 json_schema 封装（M18：
        # 不把裸 schema dict 透传给 LiteLLM）
        call = adapter.invoke_calls[0]
        assert call["stream"] is True
        assert call["response_format"] == {
            "type": "json_schema",
            "json_schema": {"name": "final_result", "schema": {"type": "object"}},
        }
        # F16 父请求：invocation 取 model_call_id
        recorder.ensure_invocation.assert_awaited_once()
        assert recorder.ensure_invocation.await_args.kwargs["invocation_id"] == "mc-1"
        assert recorder.ensure_invocation.await_args.kwargs["scenario_id"] == "news_sentiment"
        # 用量观察 + succeeded 终态（usage 同源）
        assert recorder.observe_usage.call_count == 1
        observation = recorder.observe_usage.call_args.args[0]
        assert observation.usage is usage
        assert observation.attempt_id == outcome.attempt_id
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "succeeded"
        assert terminal.usage is usage
        assert terminal.attempt_id == outcome.attempt_id

    @pytest.mark.asyncio
    async def test_on_event_backpressure_blocks_production(self):
        """§6.4：on_event 可等待——handler 未完成前生产不推进（背压实限制）。"""
        handler_started = asyncio.Event()
        release = asyncio.Event()

        async def slow_handler(event: ChatStreamEvent) -> None:
            handler_started.set()
            await release.wait()

        adapter = _ScriptedChatAdapter([((_text_event(1, "a"), _text_event(2, "b")), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        task = asyncio.create_task(
            executor.execute_chat_turn(
                _chat_request(),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding=_binding(),
                on_event=slow_handler,
            )
        )
        await asyncio.wait_for(handler_started.wait(), timeout=2.0)
        assert not task.done()  # 背压：首个事件 handler 阻塞，生产不推进
        release.set()
        outcome = await asyncio.wait_for(task, timeout=5.0)
        assert outcome.status == "succeeded"

    @pytest.mark.asyncio
    async def test_settlement_deferred_when_terminal_persistence_unconfirmed(self):
        """结算事实投影：终态提交未确认持久化（F20 重放承接）→ deferred。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        recorder.submit_terminal = AsyncMock(
            return_value=PersistenceOutcome(persisted=False, error_code="DB_SETTLE_TIMEOUT")
        )
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        assert outcome.transport_state == "ok"
        assert outcome.settlement_state == "deferred"


class TestChatOutputModeWiring:
    """检视 M18：``output_mode`` 随请求保留，并按固定 SDK 语义决定出站形态。

    - native → OpenAI ``json_schema`` 原生封装（LiteLLM 对 dict 原样透传，不补
      封装，裸 schema 直传无效）；
    - tool / prompted / text → 无 ``response_format``（tool 经 output tool，prompted
      经 instructions 承载 schema）；
    - 模式与 schema 不自洽 → 入口 fail-closed（零出站，R21 不伪装）。
    """

    @pytest.mark.asyncio
    async def test_native_mode_builds_provider_json_schema_wrapper(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        outcome = await executor.execute_chat_turn(
            _chat_request(
                output_schema={"type": "object", "properties": {"x": {"type": "string"}}}, output_mode="native"
            ),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        call = adapter.invoke_calls[0]
        assert call["request"].output_mode == "native"
        assert call["response_format"] == {
            "type": "json_schema",
            "json_schema": {
                "name": "final_result",
                "schema": {"type": "object", "properties": {"x": {"type": "string"}}},
            },
        }

    @pytest.mark.asyncio
    async def test_tool_mode_sends_output_tool_without_response_format(self):
        output_tool = FunctionToolSpec(
            name="final_result",
            description="结构 final",
            json_schema={"type": "object"},
            schema_version="v1",
            output_tool=True,
        )
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        outcome = await executor.execute_chat_turn(
            _chat_request(output_mode="tool", output_tools=(output_tool,)),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        call = adapter.invoke_calls[0]
        assert call["request"].output_mode == "tool"
        assert [tool.name for tool in call["request"].output_tools] == ["final_result"]
        assert call["response_format"] is None

    @pytest.mark.asyncio
    async def test_prompted_mode_sends_schema_in_instructions_not_response_format(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        outcome = await executor.execute_chat_turn(
            _chat_request(output_schema={"type": "object"}, output_mode="prompted"),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        call = adapter.invoke_calls[0]
        assert call["request"].output_mode == "prompted"
        assert call["response_format"] is None

    @pytest.mark.asyncio
    async def test_text_mode_sends_no_response_format(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        assert adapter.invoke_calls[0]["response_format"] is None

    @pytest.mark.asyncio
    async def test_native_without_schema_fails_closed_before_dispatch(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            await executor.execute_chat_turn(
                _chat_request(output_mode="native"),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding=_binding(),
            )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.output_schema"
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tool_mode_without_output_tools_fails_closed(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            await executor.execute_chat_turn(
                _chat_request(output_mode="tool"),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding=_binding(),
            )
        assert excinfo.value.field_path == "$.output_tools"
        assert adapter.invoke_calls == []


class TestChatBindingContract:
    @pytest.mark.asyncio
    async def test_binding_missing_scenario_id_fails_closed(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        with pytest.raises(ValueError) as excinfo:
            await executor.execute_chat_turn(
                _chat_request(),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding={"task_id": "sentiment"},
            )
        assert "scenario_id" in str(excinfo.value)
        # 零副作用：不建立 invocation、不派发（fail-closed 于入口）
        recorder.ensure_invocation.assert_not_awaited()
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_binding_missing_task_id_fails_closed(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        with pytest.raises(ValueError) as excinfo:
            await executor.execute_chat_turn(
                _chat_request(),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding={"scenario_id": "news_sentiment"},
            )
        assert "task_id" in str(excinfo.value)
        recorder.ensure_invocation.assert_not_awaited()
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_no_route_records_invocation_and_fails(self):
        """无路由：明确失败 + F16 零尝试请求同样建立 invocation（请求页可查），
        settlement 无账目事实 → None。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(scenario_id="agent_research"),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "failed"
        assert "no route" in (outcome.error_code or "")
        assert outcome.attempt_id is None
        assert outcome.settlement_state is None
        recorder.ensure_invocation.assert_awaited_once()
        assert adapter.invoke_calls == []


class TestChatRequiredRegistration:
    @pytest.mark.asyncio
    async def test_begin_attempt_failure_zero_outbound_and_halt(self):
        """SQ-02：必需登记失败 / unknown → 明确失败零出站 + halt（第二候选零派发）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response()), ((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        recorder.begin_attempt = AsyncMock(
            return_value=PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")
        )
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "failed"
        assert "attempt registration unavailable" in (outcome.error_code or "")
        assert outcome.attempt_id is not None  # 尝试身份已建立（可关联查账）
        # 零出站 + halt：invoke_chat 零调用，第二候选零登记
        assert adapter.invoke_calls == []
        assert recorder.begin_attempt.await_count == 1

    @pytest.mark.asyncio
    async def test_reserve_unknown_zero_outbound_and_halt(self):
        """M08：usage_only（无金额上限）预留未确认 → 零出站 + halt。

        begin_attempt 健康（persisted=True）也不得补放行此前未确认的必需登记
        （前置分支已收敛，begin / 外发均不进入）；原因可观测（R21）。
        """
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response()), ((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        recorder.try_reserve = AsyncMock(
            return_value=ReservationOutcome(persisted=False, error_code="DB_UNAVAILABLE", reservation=None)
        )
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "failed"
        assert "required budget reservation unavailable: DB_UNAVAILABLE" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        # halt：第二候选零预留；此前登记失败后 begin 不再进入（不补放行）
        assert recorder.try_reserve.await_count == 1
        recorder.mark_dispatched.assert_not_awaited()
        recorder.begin_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_unknown_zero_outbound_and_halt(self):
        """M08：usage_only 派发绑定未确认 → 零出站 + halt；begin 成功不补放行。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response()), ((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        recorder.mark_dispatched = AsyncMock(
            return_value=PersistenceOutcome(persisted=False, error_code="DB_UNAVAILABLE")
        )
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "failed"
        assert "required dispatch binding unavailable: DB_UNAVAILABLE" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        assert recorder.try_reserve.await_count == 1
        recorder.begin_attempt.assert_not_awaited()


class TestChatStreamFailureExposure:
    @pytest.mark.asyncio
    async def test_failure_after_public_fragments_keeps_partial_and_original_attempt(self):
        """§6.4-5：已公开分片后失败——保 partial/unknown 及原 attempt，不重试
        不换候选（不换模型拼接）；失败事实即时收敛（R21）。"""
        events = (_text_event(1, "a"), _text_event(2, "b"))
        adapter = _ScriptedChatAdapter([(events, TimeoutError("stream broken"), None)])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "failed"
        assert outcome.partial_text == "ab"
        assert outcome.response is None
        assert "public fragments" in (outcome.error_code or "")
        # 保原 attempt：单次 invoke_chat（无重试），第二候选零调用
        assert len(adapter.invoke_calls) == 1
        assert outcome.attempt_id == adapter.invoke_calls[0]["attempt_id"]
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "failed"

    @pytest.mark.asyncio
    async def test_failure_before_any_event_records_unknown_without_retry(self):
        """T-04：无公开分片的瞬态失败不证明提供方未处理——按费用未知记账且不
        自动重试（不得按「失败零费用」释放后重试）。"""
        adapter = _ScriptedChatAdapter(
            [
                ((), TimeoutError("upstream timed out"), None),
                ((_text_event(1, "ok"),), None, _chat_ok_response()),
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.error_code == "OUTBOUND_UNCONFIRMED"
        assert outcome.settlement_state == "unknown"  # 费用无法证明，不伪装 settled
        assert len(adapter.invoke_calls) == 1  # 不自动重试
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "cancelled"  # 未确认，不伪装失败零费用
        assert terminal.reservation_id is None  # 预留不按失败释放（留待租约/人工处置）

    @pytest.mark.asyncio
    async def test_failure_before_any_event_does_not_switch_candidates(self):
        """T-04：未确认外发中断遍历——备用候选不得顶替重发。"""
        adapter = _ScriptedChatAdapter(
            [
                ((), TimeoutError("upstream timed out"), None),
                ((), None, _chat_ok_response()),
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.error_code == "OUTBOUND_UNCONFIRMED"
        assert len(adapter.invoke_calls) == 1


class TestChatStopTraversalCooldown:
    @pytest.mark.asyncio
    async def test_stop_traversal_enters_connection_cooldown(self):
        """§6.4-5 / R-B02-2：stop_traversal 属连接侧事实——中断遍历且沿常规失败
        路径进冷却（文档契约 L1752-1753）；后续请求同连接候选 skipped 零派发。"""
        events = (_text_event(1, "a"), _text_event(2, "b"))
        adapter = _ScriptedChatAdapter([(events, TimeoutError("stream broken"), None)])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        snapshot = _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),))

        first = await executor.execute_chat_turn(_chat_request(), snapshot, binding=_binding())
        assert first.status == "failed"
        assert "public fragments" in (first.error_code or "")

        # 冷却已登记：第二次请求同连接候选被 skipped（零派发），失败原因透传
        second = await executor.execute_chat_turn(_chat_request(), snapshot, binding=_binding())
        assert second.status == "failed"
        assert len(adapter.invoke_calls) == 1  # 第二次零派发
        assert "cooldown until" in (second.error_code or "")


class TestChatRefusal:
    @pytest.mark.asyncio
    async def test_refused_turn_halts_without_candidate_switch(self):
        """DD §10.1：拒绝不换候选规避——完整 refused 回合 failed 终态 + 完整
        response 承载 + halt；用量已观测不丢弃（R21）。"""
        usage = _usage()
        refused = ChatTurnResponse(
            text=None,
            finish_reason="refused",
            actual_model="glm-4-air",
            usage_observed=True,
            usage=usage,
            transport_state="ok",
            refusal="敏感内容",
        )
        adapter = _ScriptedChatAdapter(
            [((), None, refused), ((), None, _chat_ok_response())]  # 第二候选不得派发
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "ok"  # 拒绝是完成的模型回合（传输事实成立）
        assert outcome.response is refused
        assert outcome.error_code == "CONTENT_REFUSED"
        assert len(adapter.invoke_calls) == 1
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "failed"
        assert terminal.usage is usage

    @pytest.mark.asyncio
    async def test_mixed_text_refusal_halts_without_candidate_switch(self):
        """M05：混文本 + 拒绝回合（流式解释产出 finish=refused）仍 failed + halt——
        不因已有文本被当作成功，也不得换候选绕过拒绝。"""

        usage = _usage()
        refused = ChatTurnResponse(
            text="以下是部分回答",
            finish_reason="refused",
            actual_model="glm-4-air",
            usage_observed=True,
            usage=usage,
            transport_state="ok",
            refusal="无法协助",
        )
        adapter = _ScriptedChatAdapter(
            [
                (
                    (
                        _text_event(1, "以下是部分回答"),
                        ChatStreamEvent(sequence=2, kind="finish", finish_reason="refused", usage=usage),
                    ),
                    None,
                    refused,
                ),
                ((), None, _chat_ok_response()),  # 第二候选不得派发
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert outcome.transport_state == "ok"
        assert outcome.response is refused
        assert outcome.error_code == "CONTENT_REFUSED"
        assert len(adapter.invoke_calls) == 1
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "failed"
        assert terminal.usage is usage


class TestChatCancelPropagation:
    @pytest.mark.asyncio
    async def test_cancel_propagates_and_converges_cancelled_terminal(self):
        """R2：取消始终传播，不包装为失败；F19 受限清理提交 cancelled 终态。"""
        adapter = _HangingChatAdapter()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        task = asyncio.create_task(
            executor.execute_chat_turn(
                _chat_request(),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding=_binding(),
            )
        )
        await asyncio.wait_for(adapter.started.wait(), timeout=2.0)  # 外发已开始
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as excinfo:
            await task
        assert type(excinfo.value) is asyncio.CancelledError  # R2：不包装不吞没
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "cancelled"


class TestChatAdapterProtocolGate:
    @pytest.mark.asyncio
    async def test_non_chat_adapter_rejected_before_reserve(self):
        """FinBERT 等非聊天适配器（不接 ChatInferenceAdapter 协议）派发前淘汰，
        零预留副作用（F16 预留纪律）。"""

        class _LegacyOnlyAdapter:
            def __init__(self) -> None:
                self.invoked = False

            async def invoke(self, *args: Any, **kwargs: Any) -> Any:
                self.invoked = True
                raise AssertionError("legacy invoke must not be reached from chat entry")

        adapter = _LegacyOnlyAdapter()
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "failed"
        assert "no chat adapter" in (outcome.error_code or "")
        assert adapter.invoked is False
        recorder.try_reserve.assert_not_awaited()
        recorder.begin_attempt.assert_not_awaited()


class TestRealLiteLLMChatAdapterWiring:
    """检视 M01：真实 ``LiteLLMChatAdapter`` 可直接注册并服务聊天入口，零旧 invoke。"""

    @pytest.mark.asyncio
    async def test_real_adapter_registers_and_serves_chat_without_legacy_invoke(self):
        usage = _usage()

        class _FakeLiteLLMClient:
            def __init__(self) -> None:
                self.chat_calls = 0
                self.legacy_calls = 0

            async def invoke_chat_connection(
                self,
                request,
                connection,
                deadline,
                attempt_context,
                *,
                stream=True,
                response_format=None,
                on_event=None,
            ) -> ChatTurnResponse:
                self.chat_calls += 1
                return _chat_ok_response(usage=usage)

            async def invoke_single_connection(self, *args: Any, **kwargs: Any) -> Any:
                self.legacy_calls += 1
                raise AssertionError("legacy invoke must not be reached from chat entry")

        client = _FakeLiteLLMClient()
        adapter = LiteLLMChatAdapter(cast(LiteLLMClient, client))
        recorder = _recorder_mock()
        # 真实适配器（新协议 invoke_chat）注册不再被旧协议强制 invoke 误杀
        executor = _executor(adapter, recorder)

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )

        assert outcome.status == "succeeded"
        assert outcome.transport_state == "ok"
        assert client.chat_calls == 1
        assert client.legacy_calls == 0  # 聊天入口零旧 invoke 触达


class TestChatCapabilityAdmission:
    """检视 M06：chat 准入按请求必需能力（stream / tools）与证据版本核验真实
    Profile——unknown / unsupported / 旧证据一律派发前淘汰（零预留副作用），
    不按模型故障重试；旧直推入口政策不变。"""

    @staticmethod
    def _local_conn() -> ResolvedConnection:
        """设备内本地聊天连接（gguf_chat，免 grant）。"""
        return ResolvedConnection(
            connection_id="conn-local",
            connection_revision=1,
            adapter_kind="gguf_chat",
            provider="local",
            model="C:/models/chat.gguf",
            api_base=None,
            adapter_options={"n_ctx": 4096},
            context_window=None,
            concurrency_limit=1,
            credential_ref=None,
            credential_version=None,
        )

    async def _request_with_capabilities(
        self, capabilities: dict[str, dict[str, Any]], request: ChatTurnRequest | None = None
    ):
        usage = _usage()
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response(usage=usage))])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            request or _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),), capabilities=capabilities),
            binding=_binding(),
        )
        return outcome, adapter, recorder, executor

    @pytest.mark.asyncio
    async def test_missing_stream_evidence_zero_outbound(self):
        outcome, adapter, recorder, executor = await self._request_with_capabilities({})
        assert outcome.status == "failed"
        assert "requires confirmed stream capability" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        recorder.begin_attempt.assert_not_awaited()
        executor._config_service.authorize_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_stream_evidence_zero_outbound(self):
        outcome, adapter, recorder, _ = await self._request_with_capabilities(
            {"stream": {"support": "unknown", "source": "user_declaration", "connection_revision": 1}}
        )
        assert outcome.status == "failed"
        assert "requires supported stream capability (evidence: unknown)" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unsupported_stream_evidence_zero_outbound(self):
        outcome, adapter, _, _ = await self._request_with_capabilities(
            {"stream": {"support": "unsupported", "source": "validation", "connection_revision": 1}}
        )
        assert outcome.status == "failed"
        assert "requires supported stream capability (evidence: unsupported)" in (outcome.error_code or "")
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_stale_evidence_revision_zero_outbound(self):
        """证据记录版本与派发连接版本不一致（旧证据）→ 零出站。"""
        outcome, adapter, recorder, _ = await self._request_with_capabilities(
            {"stream": {"support": "supported", "source": "validation", "connection_revision": 0}}
        )
        assert outcome.status == "failed"
        assert "stale evidence: 0" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_revision_treated_as_stale(self):
        """证据未记录连接版本 → 不可证明同版，按旧证据淘汰。"""
        outcome, adapter, _, _ = await self._request_with_capabilities({"stream": {"support": "supported"}})
        assert outcome.status == "failed"
        assert "stale evidence: unknown revision" in (outcome.error_code or "")
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_tools_require_tools_evidence(self):
        """请求携带工具时必需工具能力证据，缺失即零出站。"""
        request = ChatTurnRequest(
            model_call_id="mc-tools",
            messages=(ChatMessage(role="user", content_parts=(TextContentPart(text="研究问题"),)),),
            instructions=("系统提示",),
            tools=(
                FunctionToolSpec(name="search", description="d", json_schema={"type": "object"}, schema_version="v1"),
            ),
            output_schema=None,
            frozen_params={},
            max_output_tokens=256,
            deadline=asyncio.get_running_loop().time() + _REQUEST_DEADLINE_BUDGET_S,
        )
        outcome, adapter, recorder, _ = await self._request_with_capabilities(
            {"stream": {"support": "supported", "source": "validation", "connection_revision": 1}},
            request=request,
        )
        assert outcome.status == "failed"
        assert "requires confirmed tools capability" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fresh_supported_evidence_dispatches_once(self):
        """证据同版且 supported → 正常派发（无重试、单次 invoke_chat）。"""
        usage = _usage()
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response(usage=usage))])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )
        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 1

    @pytest.mark.asyncio
    async def test_local_gguf_supported_claim_still_rejected(self):
        """I50：本地 gguf_chat 即使声明 stream/tools=supported（且注册了具备
        invoke_chat 的适配器）也拒绝准入——本地 worker 无绑定 request_id/运行代次
        的 chunk/tool/final IPC，声明支持属虚假准入。零预留、零授权、零调用。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, adapters={"gguf_chat": adapter})

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-local"),),
                (self._local_conn(),),
                capabilities={
                    "stream": {"support": "supported", "source": "user_declaration", "connection_revision": 1},
                    "tools": {"support": "supported", "source": "user_declaration", "connection_revision": 1},
                },
            ),
            binding=_binding(),
        )

        assert outcome.status == "failed"
        assert "not admitted" in (outcome.error_code or "")
        assert "no chunk/tool/final frames" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        recorder.begin_attempt.assert_not_awaited()
        executor._config_service.authorize_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_local_gguf_rejected_by_kind_before_evidence(self):
        """I50：本地 kind 的拒绝先于能力证据核验（无证据也给出明确「未准入」
        原因，不落回泛化「no evidence」）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock(), adapters={"gguf_chat": adapter})

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-local"),), (self._local_conn(),), capabilities={}),
            binding=_binding(),
        )

        assert outcome.status == "failed"
        assert "not admitted" in (outcome.error_code or "")
        assert adapter.invoke_calls == []


def _data_consent(**overrides: Any) -> dict[str, Any]:
    """binding ``data_consent``（M07：受理冻结 operation/资料上下文）。"""
    values: dict[str, Any] = {
        "user_scope": "user-1",
        "operation_id": "op-1",
        "consent_id": "dec-consent-1",
        "expected_consent_revision": 2,
        "connection_id": "conn-remote",
        "connection_revision": 1,
        "security_revision": 1,
        "credential_version": "v1",
        "purpose": "sentiment",
        "target_fingerprint": "fp-1",
        "scope_version": 1,
        "source_selections": (),
        "source_scope_digest": data_consent_source_scope_digest(()),
        "owner_epoch": 1,
        "run_epoch": 1,
    }
    values.update(overrides)
    return values


def _data_receipt(outcome: DataAttemptOutcome, reason: str | None = None) -> DataAttemptReceipt:
    return DataAttemptReceipt(
        outcome=outcome,
        operation_id="op-1",
        attempt_id="att-gate",
        consent_id="dec-consent-1",
        consent_revision=2,
        binding=None,
        reason=reason,
        authorized_at="2026-10-16T00:00:00.000000Z",
    )


class TestChatDataConsentGate:
    """检视 M07：每个实际 attempt（含同候选重试与后续候选）在预留/派发/外发前
    以该 attempt 的准确 operation/资料上下文新鲜核权；许可不通过即零出站。"""

    @pytest.mark.asyncio
    async def test_first_attempt_allowed_and_dispatched(self):
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.ALLOW))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 1
        assert authorize.await_args is not None
        request = authorize.await_args.args[0]
        # 该 attempt 的准确上下文：操作身份随受理冻结，attempt 身份为本次真实尝试
        assert request.operation_id == "op-1"
        assert request.consent_id == "dec-consent-1"
        assert request.attempt_id == adapter.invoke_calls[0]["attempt_id"]
        assert request.attempt_id != "mc-1"

    @pytest.mark.asyncio
    async def test_authorize_attempt_uses_frozen_connection_revision(self):
        """M15：Agent 严格准入按实际派发连接的冻结版本核权（不放宽为未知）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        executor = _executor(adapter, _recorder_mock())
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )
        assert outcome.status == "succeeded"
        kwargs = executor._config_service.authorize_attempt.await_args.kwargs
        assert kwargs["connection_revision"] == 1

    @pytest.mark.asyncio
    async def test_consent_target_fingerprint_mismatch_zero_outbound(self):
        """T-04：许可目的地指纹必须等于本次派发候选——不等即零出站。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((dict(_candidate(0, "conn-remote"), target_fingerprint="fp-2"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert outcome.error_code == "consent_target_mismatch"
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        executor._config_service.authorize_attempt.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_consent_purpose_mismatch_zero_outbound(self):
        """T-04：许可用途必须等于本次派发用途——不等即零出站。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((dict(_candidate(0, "conn-remote"), purpose="news"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert outcome.error_code == "consent_target_mismatch"
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_pre_begin_failure_does_not_insert_attempt_row(self):
        """T-04：严格路径未 begin 的失败只转换预留（settled），不得由结算补写
        attempt 行（``insert_if_missing=False``）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )
        executor._config_service.authorize_attempt = AsyncMock(side_effect=RuntimeError("boom"))

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert adapter.invoke_calls == []
        recorder.begin_attempt.assert_not_awaited()
        terminal = recorder.submit_terminal.await_args.args[0]
        assert terminal.status == "failed"
        assert terminal.insert_if_missing is False  # 不靠预留背书补写

    @pytest.mark.asyncio
    async def test_same_id_revision_standin_zero_outbound(self):
        """M15：快照同 ID 替身版本 ≠ 操作冻结版本 → 瞬态拒绝，零出站不回退。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.ALLOW))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"),),
                (replace(_remote_conn(), connection_revision=2),),
                capabilities={
                    "stream": {"support": "supported", "source": "validation", "connection_revision": 2},
                    "tools": {"support": "supported", "source": "validation", "connection_revision": 2},
                },
            ),
            binding=_binding(data_consent=_data_consent()),  # 冻结 connection_revision=1
        )

        assert outcome.status == "failed"
        assert "connection revision changed (transient)" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        authorize.assert_not_awaited()
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unconfirmed_outbound_halts_before_second_authorization(self):
        """T-04：无公开分片的瞬态失败按未确认收敛——不再以「失败」释放后进入
        第二次核权/派发（费用未知不得伪装为零消费失败）。"""
        adapter = _ScriptedChatAdapter(
            [
                ((), TimeoutError("upstream timed out"), None),
                ((), None, _chat_ok_response()),
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(
            side_effect=[
                _data_receipt(DataAttemptOutcome.ALLOW),
                _data_receipt(DataAttemptOutcome.DENY, "state_not_approved"),
            ]
        )
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert outcome.error_code == "OUTBOUND_UNCONFIRMED"
        assert len(adapter.invoke_calls) == 1  # 不重试
        assert authorize.await_count == 1  # 未进入第二次尝试核权
        assert recorder.try_reserve.await_count == 1

    @pytest.mark.asyncio
    async def test_fallback_candidate_blocked_by_consent_target_binding(self):
        """T-04：许可绑定真实目标——备用候选（另一连接）不得复用本候选许可。"""
        adapter = _ScriptedChatAdapter(
            [
                ((), RuntimeError("upstream broken"), None),
                ((), None, _chat_ok_response()),
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.ALLOW))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert outcome.error_code == "consent_target_mismatch"
        assert len(adapter.invoke_calls) == 1  # 备用候选零出站
        assert authorize.await_count == 1  # 第二候选在核权前即被目标绑定拒绝
        assert recorder.try_reserve.await_count == 1

    @pytest.mark.asyncio
    async def test_attempt_identity_matches_dispatched_attempt(self):
        """实际尝试以本次 attempt 身份核权（不复用外层一次，T-04 起无公开分片的
        瞬态失败不再自动重试，故单次尝试）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.ALLOW))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 1
        request = authorize.await_args.args[0]
        assert request.attempt_id == adapter.invoke_calls[0]["attempt_id"]
        # operation / 许可上下文随受理冻结（同一 operation）
        assert request.operation_id == "op-1"
        assert request.consent_id == "dec-consent-1"

    @pytest.mark.asyncio
    async def test_unknown_outcome_fails_closed(self):
        """owner 回执 unknown（不可靠重读）不当 allow → 零出站。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.UNKNOWN, "consent_unreadable"))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert "data authorization unknown: consent_unreadable" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_owner_capability_fails_closed(self):
        """资料 owner 能力缺失 → 门不可评估即 fail-closed（零出站）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service = SimpleNamespace(authorize_attempt=AsyncMock())

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert "data authorization unavailable" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_incomplete_context_fails_closed_at_entry(self):
        """资料上下文缺键：入口 fail-closed（ValueError），零副作用。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        consent = _data_consent()
        consent.pop("consent_id")

        with pytest.raises(ValueError) as excinfo:
            await executor.execute_chat_turn(
                _chat_request(),
                _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
                binding=_binding(data_consent=consent),
            )
        assert "consent_id" in str(excinfo.value)
        recorder.ensure_invocation.assert_not_awaited()
        assert adapter.invoke_calls == []


def _operation_scope(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"execution_id": "exec-1", "round_id": "round-1", "node_id": "n1"}
    values.update(overrides)
    return values


def _chat_request_with(messages: tuple[ChatMessage, ...], model_call_id: str = "mc-1") -> ChatTurnRequest:
    return ChatTurnRequest(
        model_call_id=model_call_id,
        messages=messages,
        instructions=("系统提示",),
        tools=(),
        output_schema=None,
        frozen_params={},
        max_output_tokens=256,
        deadline=asyncio.get_running_loop().time() + _REQUEST_DEADLINE_BUDGET_S,
    )


class TestAgentConsentRequiredAndClosure:
    """I43：无许可即拒绝 + 逐 attempt 结论 + payload 资料闭包校验。"""

    @pytest.mark.asyncio
    async def test_require_without_consent_denies_zero_outbound_and_halts(self, caplog: pytest.LogCaptureFixture):
        adapter = _ScriptedChatAdapter([])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)

        with caplog.at_level(logging.WARNING):
            outcome = await executor.execute_chat_turn(
                _chat_request(),
                _snapshot(
                    (_candidate(0, "conn-remote"), _candidate(1, "conn-remote")),
                    (_remote_conn(),),
                ),
                binding=_binding(require_data_consent=True),
            )

        assert outcome.status == "failed"
        assert "data_consent_required" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()
        recorder.mark_dispatched.assert_not_awaited()
        # halt：两个候选只评估一次（换候选无济于 operation 级许可缺失）
        assert sum("data consent required but absent" in record.message for record in caplog.records) == 1

    @pytest.mark.asyncio
    async def test_attempt_authorizes_with_model_call_operation_id(self):
        """operation 身份取本次 ``model_call_id``；许可核权用同一 operation 身份。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        ensure = AsyncMock()
        executor._config_service.ensure_model_operation = ensure
        authorize = AsyncMock(return_value=_data_receipt(DataAttemptOutcome.ALLOW))
        executor._config_service.authorize_data_attempt = authorize

        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent(), data_operation_scope=_operation_scope()),
        )

        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 1
        assert ensure.await_count == 1  # 每次模型调用先备 operation
        assert ensure.await_args.kwargs["operation_id"] == "mc-1"
        assert authorize.await_args.args[0].operation_id == "mc-1"

    @pytest.mark.asyncio
    async def test_revoked_consent_blocks_outbound_on_next_turn(self):
        """许可撤用后下一次模型回合零出站（逐 attempt 新鲜核权，不复用先前许可）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.ensure_model_operation = AsyncMock()
        authorize = AsyncMock(
            side_effect=[
                _data_receipt(DataAttemptOutcome.ALLOW),
                _data_receipt(DataAttemptOutcome.DENY, "state_not_approved"),
            ]
        )
        executor._config_service.authorize_data_attempt = authorize

        snapshot = _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),))
        binding = _binding(data_consent=_data_consent(), data_operation_scope=_operation_scope())
        first = await executor.execute_chat_turn(_chat_request(), snapshot, binding=binding)
        second = await executor.execute_chat_turn(_chat_request(model_call_id="mc-2"), snapshot, binding=binding)

        assert first.status == "succeeded"
        assert second.status == "failed"
        assert "data authorization deny: state_not_approved" in (second.error_code or "")
        assert len(adapter.invoke_calls) == 1  # 第二次零出站
        assert authorize.await_count == 2

    @pytest.mark.asyncio
    async def test_payload_reference_outside_consent_scope_denied(self):
        adapter = _ScriptedChatAdapter([])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )
        request = _chat_request_with(
            (
                ChatMessage(
                    role="user",
                    content_parts=(TextContentPart(text="研究问题"),),
                    metadata={"source_kind": "user_message", "source_id": "资料-A", "source_version": "v1"},
                ),
            )
        )

        outcome = await executor.execute_chat_turn(
            request,
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),  # 许可集合为空
        )

        assert outcome.status == "failed"
        assert "source_closure_mismatch" in (outcome.error_code or "")
        assert adapter.invoke_calls == []
        recorder.try_reserve.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unverifiable_payload_closure_denied(self):
        adapter = _ScriptedChatAdapter([])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        executor._config_service.authorize_data_attempt = AsyncMock(
            return_value=_data_receipt(DataAttemptOutcome.ALLOW)
        )
        request = _chat_request_with(
            (
                ChatMessage(
                    role="user",
                    content_parts=(ImageContentPart(mime="image/png", binary_ref="bin-1"),),
                ),
            )
        )

        outcome = await executor.execute_chat_turn(
            request,
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(data_consent=_data_consent()),
        )

        assert outcome.status == "failed"
        assert "source_closure_unverifiable" in (outcome.error_code or "")
        assert adapter.invoke_calls == []


class TestSourceAttemptWriteBack:
    """检视 M08：响应 / 终态只写来源 attempt 的预装配事实，跨候选不顶替。"""

    @pytest.mark.asyncio
    async def test_cross_candidate_success_binds_source_attempt_facts(self):
        """候选 1 尝试 A 失败、候选 2 尝试 B 成功：终态与出口都按来源 attempt
        写回——A 的失败事实留在 A，B 的成功事实挂在 B，互不覆盖。

        T-04 起无公开分片的瞬态失败不再重试/换候选；本用例以非瞬态可切换失败
        （``RuntimeError``）验证跨候选写回仍按来源 attempt 归属。
        """
        adapter = _ScriptedChatAdapter(
            [
                ((), RuntimeError("upstream broken"), None),
                ((), None, _chat_ok_response(usage=_usage())),
            ]
        )
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder, retry_attempts=0)  # 失败即换候选
        outcome = await executor.execute_chat_turn(
            _chat_request(),
            _snapshot(
                (_candidate(0, "conn-remote"), _candidate(1, "conn-remote-2")),
                (_remote_conn(), _remote_conn("conn-remote-2")),
            ),
            binding=_binding(),
        )

        assert outcome.status == "succeeded"
        assert outcome.settlement_state == "settled"
        attempt_a = adapter.invoke_calls[0]["attempt_id"]
        attempt_b = adapter.invoke_calls[1]["attempt_id"]
        assert attempt_a != attempt_b
        # 出口身份 = 响应来源 attempt（成功尝试），不取遍历末态顶替
        assert outcome.attempt_id == attempt_b
        records = [call.args[0] for call in recorder.submit_terminal.await_args_list]
        assert [(r.attempt_id, r.status) for r in records] == [(attempt_a, "failed"), (attempt_b, "succeeded")]
        # 用量观察同样归属来源 attempt
        observed = [call.args[0].attempt_id for call in recorder.observe_usage.call_args_list]
        assert observed == [attempt_b]

    @pytest.mark.asyncio
    async def test_deadline_exhausted_before_attempt_keeps_attempt_identity_none(self):
        """deadline 耗尽未产生 attempt：attempt_id=None 且无结算投影（零出站
        失败不伪装为有 attempt）。"""
        adapter = _ScriptedChatAdapter([((), None, _chat_ok_response())])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        outcome = await executor.execute_chat_turn(
            replace(_chat_request(), deadline=0.0),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
        )

        assert outcome.status == "failed"
        assert outcome.error_code == "deadline exhausted"
        assert adapter.invoke_calls == []
        assert outcome.attempt_id is None
        assert outcome.settlement_state is None
        recorder.submit_terminal.assert_not_awaited()


class TestTerminalWriteBackMonotonic:
    """检视 M08：重复 / 乱序完成的终态写回幂等，过期结果零覆盖（不覆盖较新事实）。"""

    @pytest.mark.asyncio
    async def test_stale_failed_write_does_not_overwrite_committed_success(self):
        recorder = _recorder_mock()
        executor = _executor(_ScriptedChatAdapter([]), recorder)
        ledger = _AttemptLedgerState()
        success = SimpleNamespace(attempt_id="att-1", status="succeeded")
        stale_failure = SimpleNamespace(attempt_id="att-1", status="failed")

        await executor._submit_terminal_logged(success, ledger)
        await executor._submit_terminal_logged(stale_failure, ledger)

        assert recorder.submit_terminal.await_count == 1  # 过期失败零覆盖
        assert ledger.committed_rank == 2
        assert ledger.concluded is True

    @pytest.mark.asyncio
    async def test_duplicate_success_write_is_idempotent(self):
        recorder = _recorder_mock()
        executor = _executor(_ScriptedChatAdapter([]), recorder)
        ledger = _AttemptLedgerState()
        record = SimpleNamespace(attempt_id="att-1", status="succeeded")

        await executor._submit_terminal_logged(record, ledger)
        await executor._submit_terminal_logged(record, ledger)

        assert ledger.committed_rank == 2
        assert ledger.concluded is True
        assert ledger.pending is None  # 重复写不残留待收敛记录
