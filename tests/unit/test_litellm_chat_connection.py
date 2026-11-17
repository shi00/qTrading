"""B-02-1 单连接聊天 SDK 测试（HANDOFF §6.1/§6.3/§6.4）。

覆盖 ``LiteLLMClient.invoke_chat_connection`` 单连接聊天契约：
- 完整 messages/tools/response_format/stream 出站组装（``num_retries=0``、
  ``drop_params=False``，不做路由与业务 final 校验）；
- ChatMessage / instructions / FunctionToolSpec → OpenAI chat completions 出口
  映射（§6.3 出口规则：保序、tool-only content=None、未知/不可解析分型显式拒绝
  零出站）；
- 真流片段（§6.4）：真实 delta 逐事件经 on_event 可等待回调产出，tool 参数
  闭合且校验通过才派发 tool_call_closed；缺结束标记（断流）不得因迭代耗尽
  而成功；
- usage 经 ``normalize_litellm_usage`` 归一化，缺失保 None 不倒灌 0（R21）；
- deadline 预算、凭据租约一致性（F06）、SEC-01 零出站门、CancelledError 传播
  （R2）；
- ``ChatInferenceAdapter`` 聊天适配器协议（云实现委托单连接入口）。

非目标：ScenarioExecutor 路由/候选/结算（B-02-3）、GatewayModel SDK 映射
（B-02-4）、Recorder 结算（B-02-2）。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from core.model_management_types import (
    AssistantToolCall,
    AttemptContext,
    ChatMessage,
    ChatTurnRequest,
    ChatStreamEvent,
    ChatTurnResponse,
    FunctionToolSpec,
    ImageContentPart,
    ModelTurnProtocolError,
    NormalizedLLMUsage,
    ResolvedConnection,
    TextContentPart,
)
from services.ai_service.litellm_client import LiteLLMChatAdapter, LiteLLMClient


@pytest.fixture(autouse=True)
def _ack_egress_and_isolate_audit(tmp_path):
    """SEC-01 门控默认视为已确认（门控拒绝语义由专项用例覆盖）；审计落盘隔离到临时目录。"""

    from utils.egress_audit import EgressAudit

    EgressAudit._reset_singleton()
    EgressAudit._configure_for_tests(str(tmp_path / "egress_audit.jsonl"))
    with patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=True):
        yield


# ---------------------------------------------------------------------------
# 测试构造 helper
# ---------------------------------------------------------------------------
def _attempt_context() -> AttemptContext:
    return AttemptContext(
        owner_session_id="sess-1",
        owner_epoch=1,
        invocation_id="inv-1",
        attempt_id="att-1",
    )


def _connection(**overrides: Any) -> ResolvedConnection:
    base = {
        "connection_id": "conn-1",
        "connection_revision": 1,
        "adapter_kind": "litellm_chat",
        "provider": "openai",
        "model": "gpt-4o-mini",
        "api_base": "https://api.openai.com/v1",
        "adapter_options": {},
        "context_window": None,
        "concurrency_limit": 1,
        "credential_ref": None,
        "credential_version": None,
    }
    base.update(overrides)
    return ResolvedConnection(**base)


def _tool_spec(name: str = "get_quote", description: str = "行情查询") -> FunctionToolSpec:
    return FunctionToolSpec(
        name=name,
        description=description,
        json_schema={"type": "object", "properties": {"symbol": {"type": "string"}}, "required": ["symbol"]},
        schema_version="v1",
    )


def _messages() -> tuple[ChatMessage, ...]:
    return (
        ChatMessage(role="system", content_parts=(TextContentPart(text="你是选股助手"),)),
        ChatMessage(role="user", content_parts=(TextContentPart(text="查询贵州茅台"),)),
        ChatMessage(
            role="assistant",
            content_parts=(),
            assistant_tool_calls=(
                AssistantToolCall(call_id="call_9", name="get_quote", arguments_json='{"symbol":"600519"}'),
            ),
        ),
        ChatMessage(role="tool", content_parts=(TextContentPart(text="价格 1700"),), tool_call_id="call_9"),
    )


def _chat_request(
    messages: tuple[ChatMessage, ...] | None = None,
    *,
    tools: tuple[FunctionToolSpec, ...] = (),
    output_tools: tuple[FunctionToolSpec, ...] = (),
    instructions: tuple[str, ...] = (),
    frozen_params: dict[str, Any] | None = None,
    max_output_tokens: int | None = 123,
    deadline: float | None = None,
) -> ChatTurnRequest:
    if deadline is None:
        deadline = asyncio.get_running_loop().time() + 60.0
    return ChatTurnRequest(
        model_call_id="mc-1",
        messages=messages if messages is not None else _messages(),
        instructions=instructions,
        tools=tools,
        output_schema=None,
        frozen_params=frozen_params if frozen_params is not None else {"temperature": 0.2},
        max_output_tokens=max_output_tokens,
        deadline=deadline,
        output_tools=output_tools,
    )


def _nonstream_response(
    content: str = "结果",
    finish_reason: str = "tool_calls",
    model: str = "gpt-4o-mini-2024",
    tool_calls: list[Any] | None = None,
    refusal: str | None = None,
    usage: Any = None,
) -> Any:
    message = SimpleNamespace(content=content, refusal=refusal, tool_calls=tool_calls if tool_calls is not None else [])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model=model,
        usage=usage,
    )


def _stream_chunks(chunks: list[Any]) -> AsyncIterator[Any]:
    async def _gen() -> AsyncIterator[Any]:
        for chunk in chunks:
            yield chunk

    return _gen()


def _text_delta(text: str, finish_reason: str | None = None) -> Any:
    delta = SimpleNamespace(content=text, refusal=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=None)


def _tool_delta(index: int, call_id: str | None, name: str | None, fragment: str) -> Any:
    delta = SimpleNamespace(
        content=None,
        refusal=None,
        tool_calls=[SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=fragment))],
    )
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)


def _usage_chunk(usage: Any) -> Any:
    return SimpleNamespace(choices=[], usage=usage)


def _finish_chunk(finish_reason: str = "tool_calls") -> Any:
    delta = SimpleNamespace(content=None, refusal=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)], usage=None)


def _events_from(on_event_calls: list[ChatStreamEvent]) -> Any:
    async def _on_event(event: ChatStreamEvent) -> None:
        on_event_calls.append(event)

    return _on_event


class _TrackedStream:
    """可计数关闭的真流替身（M17）：逐 chunk 产出，``aclose`` 次数可断言。

    不用 async generator 是因为其 ``aclose`` 可能被解释器终结器再次触发，无法
    证明「恰一次显式关闭」；用显式异步迭代器把关闭责任只留在被测代码。
    """

    def __init__(self, chunks: list[Any], *, chunk_delay: float = 0.0) -> None:
        self._chunks = list(chunks)
        self._chunk_delay = chunk_delay
        self._index = 0
        self.close_calls = 0
        self.model = "gpt-4o-mini-2024"

    def __aiter__(self) -> _TrackedStream:
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        if self._chunk_delay:
            await asyncio.sleep(self._chunk_delay)
        return chunk

    async def aclose(self) -> None:
        self.close_calls += 1


# ---------------------------------------------------------------------------
# 非流式：出口映射 + 响应组装
# ---------------------------------------------------------------------------
class TestInvokeChatConnectionNonStream:
    async def test_full_mapping_and_response_assembly(self):
        """完整消息/工具/指令/参数出口映射 + ChatTurnResponse 组装（含 ensure_valid）。"""

        request = _chat_request(
            tools=(_tool_spec(),),
            output_tools=(_tool_spec("final_report", "结构最终输出"),),
            instructions=("研究守则",),
        )
        response = _nonstream_response(
            tool_calls=[
                SimpleNamespace(
                    id="call_1", function=SimpleNamespace(name="get_quote", arguments='{"symbol":"600519"}')
                )
            ],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20),
        )
        acompletion = AsyncMock(return_value=response)
        with patch("services.ai_service.acompletion", acompletion):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )

        assert acompletion.await_count == 1
        kwargs = acompletion.await_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o-mini"
        assert kwargs["num_retries"] == 0
        assert kwargs["drop_params"] is False
        assert kwargs["max_tokens"] == 123
        assert kwargs["temperature"] == 0.2
        assert isinstance(kwargs["timeout"], httpx.Timeout)
        # instructions → 前置 system 消息；ChatMessage 保序映射；tool-only content=None
        assert kwargs["messages"] == [
            {"role": "system", "content": "研究守则"},
            {"role": "system", "content": "你是选股助手"},
            {"role": "user", "content": "查询贵州茅台"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_9",
                        "type": "function",
                        "function": {"name": "get_quote", "arguments": '{"symbol":"600519"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_9", "content": "价格 1700"},
        ]
        # tools + output_tools 全部入站
        assert kwargs["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "get_quote",
                    "description": "行情查询",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "final_report",
                    "description": "结构最终输出",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"],
                    },
                },
            },
        ]
        assert result.finish_reason == "tool_calls"
        assert result.tool_calls == (
            AssistantToolCall(call_id="call_1", name="get_quote", arguments_json='{"symbol":"600519"}'),
        )
        assert result.text == "结果"
        assert result.actual_model == "gpt-4o-mini-2024"
        assert result.usage_observed is True
        assert result.usage == NormalizedLLMUsage(
            input_total_tokens=100,
            uncached_input_tokens=100,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=20,
        )
        assert result.transport_state == "ok"
        result.ensure_valid()

    async def test_user_multi_text_parts_preserve_order(self):
        """user 多 text 分型按顺序映射为 content 分段列表（§6.3 保序）。"""

        request = _chat_request(
            messages=(
                ChatMessage(
                    role="user",
                    content_parts=(TextContentPart(text="第一段"), TextContentPart(text="第二段")),
                ),
            )
        )
        acompletion = AsyncMock(return_value=_nonstream_response(finish_reason="stop"))
        with patch("services.ai_service.acompletion", acompletion):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )
        assert acompletion.await_args.kwargs["messages"] == [
            {"role": "user", "content": [{"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"}]},
        ]
        assert result.finish_reason == "final"

    async def test_response_format_passthrough(self):
        """response_format 参数透传（native JSON schema / prompted 决策在 GatewayModel）。"""

        request = _chat_request()
        acompletion = AsyncMock(return_value=_nonstream_response(finish_reason="stop"))
        with patch("services.ai_service.acompletion", acompletion):
            await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request,
                _connection(),
                request.deadline,
                _attempt_context(),
                stream=False,
                response_format={"type": "json_object"},
            )
        assert acompletion.await_args.kwargs["response_format"] == {"type": "json_object"}

    async def test_usage_missing_keeps_none(self):
        """usage 缺失 → usage_observed=False、usage=None，不倒灌 0（R21/§6.3）。"""

        request = _chat_request()
        response = _nonstream_response(finish_reason="stop", usage=None)
        with patch("services.ai_service.acompletion", AsyncMock(return_value=response)):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )
        assert result.usage_observed is False
        assert result.usage is None

    async def test_refusal_maps_to_refused_finish(self):
        """拒绝映射 finish=refused + refusal 文本（不换候选规避由 Executor 承载）。"""

        request = _chat_request()
        response = _nonstream_response(finish_reason="stop", refusal="无法协助该请求")
        with patch("services.ai_service.acompletion", AsyncMock(return_value=response)):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "无法协助该请求"
        result.ensure_valid()

    async def test_mixed_text_and_refusal_with_stop_maps_to_refused(self):
        """M05 非流对照：混文本 + refusal 文本 + finish=stop 仍是拒绝（拒绝优先于
        finish 原值），不得映射 final 记成功。"""

        request = _chat_request()
        response = _nonstream_response(content="以下是部分回答", finish_reason="stop", refusal="无法协助该请求")
        with patch("services.ai_service.acompletion", AsyncMock(return_value=response)):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "无法协助该请求"
        assert result.text == "以下是部分回答"
        result.ensure_valid()

    async def test_content_filter_without_refusal_text(self):
        """finish=content_filter 而无 refusal 文本 → refusal 取 provider 信号原文。"""

        request = _chat_request()
        response = _nonstream_response(finish_reason="content_filter", refusal=None)
        with patch("services.ai_service.acompletion", AsyncMock(return_value=response)):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=False
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "content_filter"
        result.ensure_valid()

    async def test_invalid_tool_arguments_rejected(self):
        """非流式工具参数非闭合 JSON → 协议失败，不伪装成功（§10.1）。"""

        request = _chat_request()
        response = _nonstream_response(
            finish_reason="tool_calls",
            tool_calls=[SimpleNamespace(id="call_1", function=SimpleNamespace(name="get_quote", arguments='{"sym'))],
        )
        with patch("services.ai_service.acompletion", AsyncMock(return_value=response)):
            with pytest.raises(ModelTurnProtocolError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, _connection(), request.deadline, _attempt_context(), stream=False
                )
        assert excinfo.value.code == "invalid_arguments_json"

    async def test_image_part_explicit_reject_zero_outbound(self):
        """图片分型在桥层无二进制解析（能力表未落地）→ 显式拒绝、零出站。"""

        request = _chat_request(
            messages=(
                ChatMessage(role="user", content_parts=(ImageContentPart(mime="image/png", binary_ref="asset-x"),)),
            )
        )
        acompletion = AsyncMock(return_value=_nonstream_response(finish_reason="stop"))
        with patch("services.ai_service.acompletion", acompletion):
            with pytest.raises(ModelTurnProtocolError, match="image"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, _connection(), request.deadline, _attempt_context(), stream=False
                )
        assert acompletion.await_count == 0


# ---------------------------------------------------------------------------
# 真流（§6.4）：真实 delta 逐事件、闭合校验、finish/usage
# ---------------------------------------------------------------------------
class TestInvokeChatConnectionStream:
    async def test_stream_real_deltas_event_order_and_response(self):
        """真片段按序经 on_event 产出；closed 校验通过才派发；finish 携 usage。"""

        request = _chat_request()
        chunks = [
            _text_delta("你好"),
            _text_delta("，查询中"),
            _tool_delta(0, "call_1", "get_quote", '{"sym'),
            _tool_delta(0, None, None, 'bol":"600519"}'),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, refusal=None, tool_calls=None), finish_reason="tool_calls"
                    )
                ],
                usage=None,
            ),
            _usage_chunk(SimpleNamespace(prompt_tokens=10, completion_tokens=5)),
        ]
        events: list[ChatStreamEvent] = []
        acompletion = AsyncMock(return_value=_stream_chunks(chunks))
        with patch("services.ai_service.acompletion", acompletion):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request,
                _connection(),
                request.deadline,
                _attempt_context(),
                stream=True,
                on_event=_events_from(events),
            )

        # 真流出站参数
        kwargs = acompletion.await_args.kwargs
        assert kwargs["stream"] is True
        assert kwargs["stream_options"] == {"include_usage": True}
        # 事件序列：text×2 → tool_call_delta×2 → tool_call_closed → finish
        assert [(e.sequence, e.kind) for e in events] == [
            (1, "text"),
            (2, "text"),
            (3, "tool_call_delta"),
            (4, "tool_call_delta"),
            (5, "tool_call_closed"),
            (6, "finish"),
        ]
        assert events[2].call_index == 0
        assert events[2].call_id == "call_1"
        assert events[2].name == "get_quote"
        assert events[2].arguments_fragment == '{"sym'
        assert events[3].call_id == "call_1"  # 稳定 call_id 聚合（后续分片缺 id 复用）
        assert events[3].arguments_fragment == 'bol":"600519"}'
        assert events[4].closed_arguments_json == '{"symbol":"600519"}'
        assert events[5].finish_reason == "tool_calls"
        assert events[5].usage == NormalizedLLMUsage(
            input_total_tokens=10,
            uncached_input_tokens=10,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=5,
        )
        # 响应由真实累积组装（非完整文本切块）
        assert result.text == "你好，查询中"
        assert result.tool_calls == (
            AssistantToolCall(call_id="call_1", name="get_quote", arguments_json='{"symbol":"600519"}'),
        )
        assert result.finish_reason == "tool_calls"
        assert result.usage_observed is True
        result.ensure_valid()

    async def test_stream_missing_finish_fails_loud(self):
        """断流（迭代耗尽无结束标记）不得成功——协议失败而非空结果（§6.4-4）。"""

        request = _chat_request()
        chunks = [_text_delta("半截")]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            with pytest.raises(ModelTurnProtocolError, match="finish"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_events_from(events),
                )

    async def test_stream_malformed_tool_arguments_rejected(self):
        """闭合分片拼出非法 JSON → 协议失败，closed 事件不产出（闭合前不派发）。"""

        request = _chat_request()
        chunks = [
            _tool_delta(0, "call_1", "get_quote", '{"sym'),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, refusal=None, tool_calls=None), finish_reason="tool_calls"
                    )
                ],
                usage=None,
            ),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            with pytest.raises(ModelTurnProtocolError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_events_from(events),
                )
        assert excinfo.value.code == "invalid_arguments_json"
        assert all(e.kind != "tool_call_closed" for e in events)

    async def test_stream_buffered_fragment_replayed_when_identity_known(self):
        """M04：身份未齐时的 leading 分片在 id/name 齐备后整段补发一次，
        增量拼接 == 闭合事实（早期参数不丢）。"""

        request = _chat_request()
        chunks = [
            _tool_delta(0, None, None, '{"sym'),
            _tool_delta(0, "call_1", "get_quote", 'bol":"600519"}'),
            _finish_chunk(),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        deltas = [e for e in events if e.kind == "tool_call_delta"]
        assert [(e.call_id, e.name, e.arguments_fragment) for e in deltas] == [
            ("call_1", "get_quote", '{"symbol":"600519"}')
        ]
        closed = [e for e in events if e.kind == "tool_call_closed"]
        assert len(closed) == 1
        assert closed[0].closed_arguments_json == "".join(e.arguments_fragment or "" for e in deltas)
        assert result.tool_calls == (
            AssistantToolCall(call_id="call_1", name="get_quote", arguments_json='{"symbol":"600519"}'),
        )

    async def test_stream_fragmented_tool_name_accumulates(self):
        """M04：分片名称按 SDK 语义增量拼接（同名 id 重复出现不算漂移）。"""

        request = _chat_request()
        chunks = [
            _tool_delta(0, "call_1", "get_", "{"),
            _tool_delta(0, "call_1", "quote", "}"),
            _finish_chunk(),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        deltas = [e for e in events if e.kind == "tool_call_delta"]
        assert [e.name for e in deltas] == ["get_", "get_quote"]
        assert result.tool_calls == (AssistantToolCall(call_id="call_1", name="get_quote", arguments_json="{}"),)

    async def test_stream_interleaved_tool_call_indices(self):
        """M04：交错调用按 index 各自累积，closed 按 index 序派发，互不串流。"""

        request = _chat_request()
        chunks = [
            _tool_delta(0, "c0", "alpha", '{"a": 1'),
            _tool_delta(1, "c1", "beta", '{"b": 2}'),
            _tool_delta(0, None, None, "}"),
            _finish_chunk(),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        assert [e.call_index for e in events if e.kind == "tool_call_delta"] == [0, 1, 0]
        closed = [e for e in events if e.kind == "tool_call_closed"]
        assert [(e.call_index, e.call_id, e.closed_arguments_json) for e in closed] == [
            (0, "c0", '{"a": 1}'),
            (1, "c1", '{"b": 2}'),
        ]
        assert result.tool_calls == (
            AssistantToolCall(call_id="c0", name="alpha", arguments_json='{"a": 1}'),
            AssistantToolCall(call_id="c1", name="beta", arguments_json='{"b": 2}'),
        )

    async def test_stream_late_conflicting_tool_call_id_fails_closed(self):
        """M04：同 index 晚到不同 id = 身份漂移，fail-closed（不猜、不静默改写）。"""

        request = _chat_request()
        chunks = [
            _tool_delta(0, "call_1", "get_quote", "{"),
            _tool_delta(0, "call_2", None, "}"),
            _finish_chunk(),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            with pytest.raises(ModelTurnProtocolError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_events_from(events),
                )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.stream.tool_calls[0].id"
        assert all(e.kind != "tool_call_closed" for e in events)

    async def test_stream_refusal_maps_to_refused(self):
        """流式 refusal 片段累积 + content_filter → finish refused + refusal 文本。"""

        request = _chat_request()
        refusal_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, refusal="无法协助", tool_calls=None),
                    finish_reason="content_filter",
                )
            ],
            usage=None,
        )
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks([refusal_chunk]))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "无法协助"
        assert result.text is None
        result.ensure_valid()

    async def test_stream_pure_refusal_with_stop_maps_to_refused(self):
        """M05：纯拒绝（refusal 文本）以 stop 结束 → finish refused（拒绝优先于
        finish 原值），不得映射 final 被 Executor 记 succeeded。"""

        request = _chat_request()
        refusal_chunk = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=None, refusal="无法协助", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            usage=None,
        )
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks([refusal_chunk]))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "无法协助"
        assert result.text is None
        assert events[-1].kind == "finish"
        assert events[-1].finish_reason == "refused"
        result.ensure_valid()

    async def test_stream_mixed_text_and_refusal_with_stop_maps_to_refused(self):
        """M05：混文本 + refusal 且以 stop 结束 → finish refused（与非流同序），
        已产出文本原样保留，不得记成功、不得凭「输出非法」换候选绕过拒绝。"""

        request = _chat_request()
        chunks = [
            _text_delta("以下是部分回答"),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, refusal="无法协助", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            ),
        ]
        events: list[ChatStreamEvent] = []
        with patch("services.ai_service.acompletion", AsyncMock(return_value=_stream_chunks(chunks))):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True, on_event=_events_from(events)
            )
        assert result.finish_reason == "refused"
        assert result.refusal == "无法协助"
        assert result.text == "以下是部分回答"
        assert events[-1].finish_reason == "refused"
        result.ensure_valid()

    async def test_cancelled_error_propagates_during_stream(self):
        """流中取消 → CancelledError 传播（R2），不包装不吞没；审计 cancelled。"""

        request = _chat_request()

        async def _cancelled_gen() -> AsyncIterator[Any]:
            yield _text_delta("开头")
            raise asyncio.CancelledError

        audit = AsyncMock()
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=_cancelled_gen())),
            patch("services.ai_service.litellm_client._audit_egress_outcome", audit),
        ):
            with pytest.raises(asyncio.CancelledError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, _connection(), request.deadline, _attempt_context(), stream=True
                )
        assert excinfo.type is asyncio.CancelledError
        assert audit.await_args.kwargs["status"] == "cancelled"


# ---------------------------------------------------------------------------
# M17：同一绝对 deadline 环绕读取 / 背压 / on_event / 关流
# ---------------------------------------------------------------------------
class TestInvokeChatConnectionStreamDeadline:
    async def test_continuous_small_chunks_bounded_by_same_absolute_budget(self):
        """持续小片：逐片检查同一绝对预算，超时收尾并关闭真实流（恰一次）。"""

        request = _chat_request(deadline=asyncio.get_running_loop().time() + 0.5)
        stream = _TrackedStream([_text_delta("片")] * 200, chunk_delay=0.01)
        events: list[ChatStreamEvent] = []
        audit = AsyncMock()
        with (
            patch("services.ai_service.acompletion", AsyncMock(return_value=stream)),
            patch("services.ai_service.litellm_client._audit_egress_outcome", audit),
        ):
            with pytest.raises(TimeoutError, match="usage left unknown"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_events_from(events),
                )
        assert stream.close_calls == 1
        # 费用不伪造（R21）：超时不产出 finish，也不回填任何 usage
        assert all(e.kind != "finish" for e in events)
        assert all(e.usage is None for e in events)
        assert audit.await_args.kwargs["status"] == "failed"
        assert audit.await_args.kwargs["error_category"] == "timeout"

    async def test_slow_on_event_handler_bounded_by_same_absolute_budget(self):
        """慢 on_event：处理等待同受绝对预算约束，超时收尾并关闭真实流。"""

        request = _chat_request(deadline=asyncio.get_running_loop().time() + 0.5)
        stream = _TrackedStream([_text_delta("慢")])
        events: list[ChatStreamEvent] = []

        async def _slow_handler(event: ChatStreamEvent) -> None:
            events.append(event)
            await asyncio.sleep(1.0)

        with patch("services.ai_service.acompletion", AsyncMock(return_value=stream)):
            with pytest.raises(TimeoutError, match="usage left unknown"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_slow_handler,
                )
        assert stream.close_calls == 1
        assert [e.kind for e in events] == ["text"]

    async def test_full_channel_backpressure_bounded_by_same_absolute_budget(self):
        """通道满：on_event await 永不返回也不得无限挂起，超时收尾并关流。"""

        request = _chat_request(deadline=asyncio.get_running_loop().time() + 0.5)
        stream = _TrackedStream([_text_delta("满")])
        events: list[ChatStreamEvent] = []
        never_set = asyncio.Event()

        async def _blocked_handler(event: ChatStreamEvent) -> None:
            events.append(event)
            await never_set.wait()  # 有界通道满：生产侧 put 阻塞

        with patch("services.ai_service.acompletion", AsyncMock(return_value=stream)):
            with pytest.raises(TimeoutError, match="usage left unknown"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request,
                    _connection(),
                    request.deadline,
                    _attempt_context(),
                    stream=True,
                    on_event=_blocked_handler,
                )
        assert stream.close_calls == 1
        assert [e.kind for e in events] == ["text"]

    async def test_normal_stream_closes_once_and_keeps_usage(self):
        """正常路径不改变：完整流返回成功响应，真实流仍收尾关闭恰一次。"""

        request = _chat_request()
        chunks = [
            _text_delta("好"),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None, refusal=None, tool_calls=None), finish_reason="stop"
                    )
                ],
                usage=None,
            ),
        ]
        stream = _TrackedStream(chunks)
        with patch("services.ai_service.acompletion", AsyncMock(return_value=stream)):
            result = await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(), request.deadline, _attempt_context(), stream=True
            )
        assert result.finish_reason == "final"
        assert result.text == "好"
        assert stream.close_calls == 1


# ---------------------------------------------------------------------------
# 门控与预算（SEC-01 / deadline / F06 租约）
# ---------------------------------------------------------------------------
class TestInvokeChatConnectionGates:
    async def test_sec01_unacknowledged_zero_outbound(self):
        """SEC-01 未确认 → AIPolicyNotAcknowledgedError，零出站。"""

        request = _chat_request()
        acompletion = AsyncMock(return_value=_nonstream_response())
        with (
            patch("services.ai_service.litellm_client.is_egress_acknowledged", return_value=False),
            patch("services.ai_service.acompletion", acompletion),
        ):
            from core.errors import AIPolicyNotAcknowledgedError

            with pytest.raises(AIPolicyNotAcknowledgedError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, _connection(), request.deadline, _attempt_context(), stream=False
                )
        assert excinfo.value.info.code == "ai_policy_not_acknowledged"
        assert acompletion.await_count == 0

    async def test_deadline_exhausted_before_dispatch(self):
        """dispatch 前 deadline 耗尽 → TimeoutError，零出站。"""

        loop = asyncio.get_running_loop()
        request = _chat_request(deadline=loop.time() - 1.0)
        acompletion = AsyncMock(return_value=_nonstream_response())
        with patch("services.ai_service.acompletion", acompletion):
            with pytest.raises(TimeoutError) as excinfo:
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, _connection(), request.deadline, _attempt_context(), stream=False
                )
        assert "before chat turn dispatch" in str(excinfo.value)
        assert acompletion.await_count == 0

    async def test_credential_lease_mismatch_rejects_before_resolution(self):
        """F06：版本化凭据 ref 与租约不同源 → ValueError，零出站零凭据读取。"""

        request = _chat_request()
        conn = _connection(credential_ref="keyring:model-connection-6", credential_version="7")
        resolver = MagicMock()
        acompletion = AsyncMock(return_value=_nonstream_response())
        with (
            patch("services.ai_service.litellm_client.resolve_credential", resolver),
            patch("services.ai_service.acompletion", acompletion),
        ):
            with pytest.raises(ValueError, match="lease|ref"):
                await LiteLLMClient(MagicMock()).invoke_chat_connection(
                    request, conn, request.deadline, _attempt_context(), stream=False
                )
        assert resolver.call_count == 0
        assert acompletion.await_count == 0

    async def test_non_chat_adapter_kind_rejected(self):
        """adapter_kind 非 litellm_chat → ValueError（协议只接聊天连接）。"""

        request = _chat_request()
        with pytest.raises(ValueError, match="litellm_chat"):
            await LiteLLMClient(MagicMock()).invoke_chat_connection(
                request, _connection(adapter_kind="gguf_sentiment"), request.deadline, _attempt_context(), stream=False
            )


# ---------------------------------------------------------------------------
# ChatInferenceAdapter 协议（云实现委托）
# ---------------------------------------------------------------------------
class TestChatInferenceAdapter:
    async def test_cloud_adapter_delegates_to_invoke_chat_connection(self):
        """云适配器 invoke_chat 委托单连接入口，参数原样透传，返回值直通。"""

        request = _chat_request()
        conn = _connection()
        deadline = request.deadline
        ctx = _attempt_context()
        on_event = _events_from([])
        client = LiteLLMClient(MagicMock())
        expected = ChatTurnResponse(
            text="ok",
            finish_reason="final",
            actual_model="m",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        adapter = LiteLLMChatAdapter(client)
        with patch.object(
            LiteLLMClient,
            "invoke_chat_connection",
            new=AsyncMock(return_value=expected),
        ) as delegated:
            result = await adapter.invoke_chat(request, conn, deadline, ctx, on_event=on_event)
        assert result is expected
        delegated.assert_awaited_once_with(
            request, conn, deadline, ctx, stream=True, response_format=None, on_event=on_event
        )
