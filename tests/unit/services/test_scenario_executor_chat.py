"""S3 ScenarioExecutor.execute_chat_turn 单测（B-02-3，HANDOFF §6.1 / DD §10.1，SQ-02）。

覆盖：
- 成功链路：真实流事件在 final 前经 on_event 可等待送达（SQ-02「实际 delta
  有界事件」）；ChatTurnOutcome 契约：transport=ok / settlement=settled /
  model_call → invocation → attempt 身份关联；adapter 收到 stream=True /
  response_format=output_schema；用量观察 + succeeded 终态；
- binding 契约（受理冻结模型绑定）：scenario_id / task_id 缺失 fail-closed
  （ValueError，零副作用——不建立 invocation、不派发）；
- 必需登记失败零出站（SQ-02）：begin_attempt persisted=False → invoke_chat 零
  调用 + 明确失败 + halt（第二候选零派发）；
- §6.4-5 已公开分片后失败：保 partial/unknown 及原 attempt——不重试（单次
  invoke_chat）不换候选（第二候选零调用）；未公开分片前失败沿冻结候选策略
  （同候选重试 / 切换候选）；
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
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.model_management_types import (
    ChatMessage,
    ChatStreamEvent,
    ChatTurnRequest,
    ChatTurnResponse,
    ExecutionSnapshot,
    NormalizedLLMUsage,
    PersistenceOutcome,
    ResolvedConnection,
    TextContentPart,
)
from services.ai_service.litellm_client import LiteLLMChatAdapter, LiteLLMClient
from services.ai_service.scenario_executor import ScenarioExecutor
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


def _snapshot(candidates: tuple[dict, ...], connections: tuple[ResolvedConnection, ...]) -> ExecutionSnapshot:
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


def _chat_request(model_call_id: str = "mc-1", output_schema: dict[str, Any] | None = None) -> ChatTurnRequest:
    return ChatTurnRequest(
        model_call_id=model_call_id,
        messages=(ChatMessage(role="user", content_parts=(TextContentPart(text="研究问题"),)),),
        instructions=("系统提示",),
        tools=(),
        output_schema=output_schema,
        frozen_params={},
        max_output_tokens=256,
        deadline=asyncio.get_running_loop().time() + _REQUEST_DEADLINE_BUDGET_S,
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
        finish_reason="stop",
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
            ChatStreamEvent(sequence=2, kind="finish", finish_reason="stop", usage=usage),
        )
        adapter = _ScriptedChatAdapter([(events, None, _chat_ok_response(usage=usage))])
        recorder = _recorder_mock()
        executor = _executor(adapter, recorder)
        seen: list[ChatStreamEvent] = []

        async def on_event(event: ChatStreamEvent) -> None:
            seen.append(event)

        outcome = await executor.execute_chat_turn(
            _chat_request(output_schema={"type": "object"}),
            _snapshot((_candidate(0, "conn-remote"),), (_remote_conn(),)),
            binding=_binding(),
            on_event=on_event,
        )

        assert outcome.status == "succeeded"
        assert outcome.transport_state == "ok"
        assert outcome.settlement_state == "settled"
        assert outcome.error_code is None
        assert outcome.partial_text is None
        assert outcome.response is not None and outcome.response.finish_reason == "stop"
        # 身份关联：model_call → invocation（一回合一 invocation）→ 实际 attempt
        assert outcome.model_call_id == "mc-1"
        assert outcome.invocation_id == "mc-1"
        assert outcome.attempt_id == adapter.invoke_calls[0]["attempt_id"]
        # SQ-02：真实 delta 在 final 前经 on_event 送达
        assert [e.text_delta for e in seen if e.kind == "text"] == ["ab"]
        assert seen[-1].kind == "finish"
        # 出口参数透传：stream=True / response_format=output_schema
        call = adapter.invoke_calls[0]
        assert call["stream"] is True
        assert call["response_format"] == {"type": "object"}
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
    async def test_failure_before_any_event_retries_same_candidate(self):
        """未公开分片前失败：沿冻结候选策略——瞬态失败退避后同候选重试。"""
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
        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 2  # 首次失败 + 同候选重试

    @pytest.mark.asyncio
    async def test_failure_before_any_event_switches_candidates(self):
        """未公开分片前失败：沿冻结候选策略——切换备用候选。"""
        adapter = _ScriptedChatAdapter(
            [
                ((), TimeoutError("upstream timed out"), None),
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
        assert outcome.status == "succeeded"
        assert len(adapter.invoke_calls) == 3  # 候选0×2（默认1重试）+ 候选1×1


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
