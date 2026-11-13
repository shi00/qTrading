"""Agent 模型桥 GatewayModel 单测（B-02-4，DD §10.1 / HANDOFF §6.2-6.3）。

覆盖：
- §6.3 SDK 消息 → ChatTurnRequest 一次投影：SystemPromptPart/instructions 保序、
  user 文本（str 与 TextContent）、assistant 历史（TextPart/ToolCallPart/
  ThinkingPart 跳过）、ToolReturnPart/RetryPromptPart tool_call_id 严格配对、
  工具白名单五元组（function_tools/output_tools/output_object）；
- 未识别 part 明确拒绝（zero outbound，R21 不裁剪）：未知请求 part、未知
  响应 part、BinaryContent 用户内容、timeout 缺失 / 畸形；
- 拒绝保真（§6.3）：refused 回合 UnexpectedModelBehavior 携带 refusal 原文；
- 费用缺失保 None：usage None → SDK unmeasured 标记（账本事实由 Recorder 经
  execute_chat_turn 承载，SDK 默认 0 不倒灌账本）；
- 流路径：ChatStreamEvent → SDK 流事件穷举分发、finish/usage 确认、
  provider_response_id = model_call_id（身份双通道）、生产任务 join（§6.4-1）、
  失败回合不因迭代耗尽成功（§6.4-4）、分片后失败保 partial（§6.4-5）。
"""

import asyncio
import dataclasses
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.model_management_types import (
    ChatStreamEvent,
    ChatTurnOutcome,
    ChatTurnRequest,
    ChatTurnResponse,
    ExecutionSnapshot,
    ModelTurnProtocolError,
    NormalizedLLMUsage,
    TextContentPart,
)
from pydantic_ai import UnexpectedModelBehavior
from pydantic_ai.messages import (
    BinaryContent,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from services.agent_service.model import (
    BOUND_MODEL_ALIAS,
    GatewayModel,
    build_chat_turn_request,
    map_finish_reason,
    map_usage,
)

pytestmark = pytest.mark.unit

_USAGE = NormalizedLLMUsage(
    input_total_tokens=100,
    uncached_input_tokens=80,
    cache_read_tokens=20,
    cache_write_tokens=None,
    output_tokens=30,
)


def _binding(**overrides: Any) -> dict[str, Any]:
    return {"scenario_id": "news_sentiment", "task_id": "sentiment", **overrides}


def _settings(**overrides: Any) -> ModelSettings:
    return ModelSettings(timeout=30.0, temperature=0.2, max_tokens=256, **overrides)


def _tool(name: str, description: str = "d") -> ToolDefinition:
    return ToolDefinition(name=name, description=description, parameters_json_schema={"type": "object"})


def _output_object() -> Any:
    from pydantic_ai.output import OutputObjectDefinition

    return OutputObjectDefinition(json_schema={"type": "object"}, name="final_result", description=None, strict=None)


def _ok_response(**overrides: Any) -> ChatTurnResponse:
    kwargs: dict[str, Any] = {
        "text": "hello",
        "finish_reason": "final",
        "actual_model": "glm-4-air",
        "usage_observed": True,
        "usage": _USAGE,
        "transport_state": "ok",
    }
    kwargs.update(overrides)
    return ChatTurnResponse(**kwargs)


def _succeeded_outcome(model_call_id: str = "mc-1", response: ChatTurnResponse | None = None) -> ChatTurnOutcome:
    return ChatTurnOutcome(
        model_call_id=model_call_id,
        invocation_id=model_call_id,
        status="succeeded",
        transport_state="ok",
        settlement_state="settled",
        attempt_id="att-1",
        response=response or _ok_response(),
        partial_text=None,
        error_code=None,
    )


def _failed_outcome(model_call_id: str = "mc-1", **overrides: Any) -> ChatTurnOutcome:
    kwargs: dict[str, Any] = {
        "model_call_id": model_call_id,
        "invocation_id": model_call_id,
        "status": "failed",
        "transport_state": "failed",
        "settlement_state": None,
        "attempt_id": None,
        "response": None,
        "partial_text": None,
        "error_code": "attempt registration unavailable: DB_UNAVAILABLE",
    }
    kwargs.update(overrides)
    return ChatTurnOutcome(**kwargs)


class _FakeExecutor:
    """execute_chat_turn 结构替身：可编程事件流 / 异常 / 终态，记录调用事实。"""

    def __init__(
        self,
        outcome: ChatTurnOutcome | None = None,
        events: tuple[ChatStreamEvent, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self.outcome = outcome
        self.events = events
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.last_outcome: ChatTurnOutcome | None = None

    async def execute_chat_turn(
        self,
        request: ChatTurnRequest,
        snapshot: ExecutionSnapshot,
        *,
        binding: Any,
        on_event: Any = None,
    ) -> ChatTurnOutcome:
        self.calls.append({"request": request, "binding": dict(binding), "snapshot": snapshot})
        for event in self.events:
            if on_event is not None:
                await on_event(event)
        if self.error is not None:
            raise self.error
        assert self.outcome is not None
        # 真实 execute_chat_turn 契约：outcome.model_call_id == request.model_call_id
        # （invocation=model_call_id）；替身对齐该契约，不保留 fixture 自设值
        self.last_outcome = dataclasses.replace(
            self.outcome, model_call_id=request.model_call_id, invocation_id=request.model_call_id
        )
        return self.last_outcome


def _gateway(executor: Any, tool_schema_version: str = "v1") -> GatewayModel:
    snapshot = MagicMock(spec=ExecutionSnapshot)
    return GatewayModel(executor, _binding(), snapshot, tool_schema_version=tool_schema_version)


def _params(**overrides: Any) -> ModelRequestParameters:
    return ModelRequestParameters(**overrides)


# ---------------------------------------------------------------------------
# §6.3 投影：build_chat_turn_request
# ---------------------------------------------------------------------------


class TestBuildChatTurnRequest:
    @pytest.mark.asyncio
    async def test_projection_preserves_order_and_skips_thinking(self):
        history = [
            ModelRequest(
                parts=[
                    SystemPromptPart(content="sys"),
                    UserPromptPart(content="hi"),
                ]
            ),
            ModelResponse(
                parts=[
                    TextPart(content="hello"),
                    ToolCallPart(tool_name="search", args={"q": 1}, tool_call_id="c1"),
                    ThinkingPart(content="hmm"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name="search", content={"a": 1, "b": "中"}, tool_call_id="c1"),
                    RetryPromptPart(content="retry feedback", tool_call_id="c1", tool_name="search"),
                ]
            ),
        ]
        request = build_chat_turn_request(
            history, _settings(), _params(), tool_schema_version="v1", model_call_id="mc-x"
        )
        assert request.model_call_id == "mc-x"
        assert request.instructions == ("sys",)
        assert [m.role for m in request.messages] == ["user", "assistant", "tool", "tool"]
        part0 = request.messages[0].content_parts[0]
        assert isinstance(part0, TextContentPart)
        assert part0.text == "hi"
        # assistant：text + tool call；ThinkingPart 跳过（设计明示不进入公开桥）
        part1 = request.messages[1].content_parts[0]
        assert isinstance(part1, TextContentPart)
        assert part1.text == "hello"
        assert len(request.messages[1].assistant_tool_calls) == 1
        assert request.messages[1].assistant_tool_calls[0].arguments_json == json.dumps({"q": 1}, sort_keys=True)
        assert request.messages[1].assistant_tool_calls[0].call_id == "c1"
        # tool_call_id 严格配对
        assert request.messages[2].tool_call_id == "c1"
        part2 = request.messages[2].content_parts[0]
        assert isinstance(part2, TextContentPart)
        assert json.loads(part2.text) == {"a": 1, "b": "中"}
        assert request.messages[3].tool_call_id == "c1"

    @pytest.mark.asyncio
    async def test_tool_mapping_and_output_schema(self):
        params = _params(
            function_tools=[_tool("search")],
            output_tools=[_tool("final_result")],
            output_object=_output_object(),
        )
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])], _settings(), params, tool_schema_version="v2"
        )
        by_output = [t for t in request.tools if t.output_tool]
        by_function = [t for t in request.tools if not t.output_tool]
        assert [t.name for t in by_function] == ["search"]
        assert [t.name for t in by_output] == ["final_result"]
        assert all(t.schema_version == "v2" for t in request.tools)
        assert request.output_schema == {"type": "object"}

    @pytest.mark.asyncio
    async def test_frozen_params_and_deadline(self):
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            _params(),
            tool_schema_version="v1",
        )
        # timeout 消费为 deadline，其余 frozen params 全量透传
        assert request.frozen_params == {"temperature": 0.2, "max_tokens": 256}
        assert request.max_output_tokens == 256
        assert request.deadline > asyncio.get_running_loop().time() + 29.0

    @pytest.mark.asyncio
    async def test_unknown_request_part_rejected(self):
        class _WeirdPart:
            part_kind = "compaction"

        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[_WeirdPart()])],  # type: ignore[arg-type]  # 验证未知 part 拒绝路径
                _settings(),
                _params(),
                tool_schema_version="v1",
            )
        assert excinfo.value.code == "unknown_part"
        assert "compaction" in excinfo.value.field_path

    @pytest.mark.asyncio
    async def test_unknown_response_part_rejected(self):
        class _WeirdResponsePart:
            part_kind = "native_call"

        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelResponse(parts=[_WeirdResponsePart()])],  # type: ignore[arg-type]  # 验证未知 part 拒绝路径
                _settings(),
                _params(),
                tool_schema_version="v1",
            )
        assert excinfo.value.code == "unknown_part"

    @pytest.mark.asyncio
    async def test_binary_content_rejected_zero_outbound(self):
        # PNG/JPEG binary 引用随 E-02 附件装配接入（NOTE(lazy)），当前明确拒绝
        image = BinaryContent(data=b"png", media_type="image/png")
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content=[image])])],
                _settings(),
                _params(),
                tool_schema_version="v1",
            )
        assert excinfo.value.code == "unknown_part"

    @pytest.mark.asyncio
    async def test_missing_timeout_rejected(self):
        settings: ModelSettings = ModelSettings(temperature=0.2)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])], settings, _params(), tool_schema_version="v1"
            )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.settings.timeout"

    @pytest.mark.asyncio
    async def test_missing_schema_version_rejected(self):
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])], _settings(), _params(), tool_schema_version=""
            )
        assert excinfo.value.field_path == "$.tool_schema_version"


# ---------------------------------------------------------------------------
# §6.3 转出：finish / usage / 拒绝保真
# ---------------------------------------------------------------------------


class TestOutcomeMapping:
    @pytest.mark.asyncio
    async def test_finish_reason_map(self):
        # core 词汇表（§10.1：final/tool_calls/refused/length/error）→ SDK
        assert map_finish_reason("final") == "stop"
        assert map_finish_reason("length") == "length"
        assert map_finish_reason("tool_calls") == "tool_call"
        assert map_finish_reason("refused") == "content_filter"
        assert map_finish_reason("error") == "error"
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            map_finish_reason("mystery")  # type: ignore[arg-type]  # 验证未知值拒绝路径
        assert excinfo.value.code == "invalid_field"

    @pytest.mark.asyncio
    async def test_usage_map_missing_keeps_unmeasured(self):
        # 费用缺失保 None：SDK 层 unmeasured 标记（账本事实由 Recorder 经
        # execute_chat_turn 承载，SDK 默认 0 不倒灌账本）
        usage = map_usage(None)
        assert usage.input_tokens == 0
        assert usage.unmeasured_requests == 1
        usage = map_usage(_USAGE)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 30
        assert usage.details["cache_read_tokens"] == 20

    @pytest.mark.asyncio
    async def test_request_success_response_shape(self):
        executor = _FakeExecutor(_succeeded_outcome())
        model = _gateway(executor)
        response = await model.request([ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params())
        assert isinstance(response, ModelResponse)
        assert executor.last_outcome is not None
        # model_call_id 双通道：provider_response_id == 请求身份 == outcome 身份
        assert response.provider_response_id == executor.calls[0]["request"].model_call_id
        assert response.provider_response_id == executor.last_outcome.model_call_id
        assert response.model_name == "glm-4-air"  # 实际模型承载（不伪装别名）
        assert response.model_id == BOUND_MODEL_ALIAS
        assert [p.content for p in response.parts if isinstance(p, TextPart)] == ["hello"]
        assert response.usage.input_tokens == 100
        # Executor 收到的绑定 / 快照与模型持有的同一冻结对象
        assert executor.calls[0]["binding"]["scenario_id"] == "news_sentiment"

    @pytest.mark.asyncio
    async def test_request_refusal_raises_with_body(self):
        refused = _ok_response(text=None, finish_reason="refused", usage=_USAGE, refusal="敏感内容")
        executor = _FakeExecutor(_succeeded_outcome(response=refused))
        model = _gateway(executor)
        with pytest.raises(UnexpectedModelBehavior) as excinfo:
            await model.request([ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params())
        assert "refused" in excinfo.value.message
        assert excinfo.value.body == "敏感内容"  # 拒绝保真：原文一次转出

    @pytest.mark.asyncio
    async def test_request_failed_raises_with_stable_code(self):
        executor = _FakeExecutor(_failed_outcome())
        model = _gateway(executor)
        with pytest.raises(UnexpectedModelBehavior) as excinfo:
            await model.request([ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params())
        assert "attempt registration unavailable" in excinfo.value.message


# ---------------------------------------------------------------------------
# 流路径：GatewayStreamedResponse
# ---------------------------------------------------------------------------


class TestStreamPath:
    @pytest.mark.asyncio
    async def test_stream_text_and_finish(self):
        events = (
            ChatStreamEvent(sequence=1, kind="text", text_delta="ab"),
            ChatStreamEvent(sequence=2, kind="text", text_delta="cd"),
            ChatStreamEvent(sequence=3, kind="finish", finish_reason="final", usage=_USAGE),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-s"), events=events)
        model = _gateway(executor)
        collected: list[str] = []
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
        ) as streamed:
            async for event in streamed:
                # 首个文本分片经 PartStartEvent（TextPart）承载，后续为
                # PartDeltaEvent（TextPartDelta）——SDK 流事件契约
                if isinstance(event, PartStartEvent):
                    assert isinstance(event.part, TextPart)
                    collected.append(event.part.content)
                elif isinstance(event, PartDeltaEvent) and isinstance(event.delta, TextPartDelta):
                    collected.append(event.delta.content_delta)
            assert "".join(collected) == "abcd"
            assert executor.last_outcome is not None
            # model_call_id 双通道：provider_response_id == 请求身份 == outcome 身份
            assert streamed.provider_response_id == executor.calls[0]["request"].model_call_id
            assert streamed.provider_response_id == executor.last_outcome.model_call_id
            assert streamed.finish_reason == "stop"
            assert streamed.usage.input_tokens == 100
            response = streamed.get()
        assert response.usage.input_tokens == 100

    @pytest.mark.asyncio
    async def test_stream_failure_does_not_end_cleanly(self):
        """§6.4-4：失败回合不因迭代耗尽成功——分片后失败就地抛出。"""
        events = (ChatStreamEvent(sequence=1, kind="text", text_delta="part"),)
        executor = _FakeExecutor(_failed_outcome(partial_text="part", attempt_id="att-9"), events=events)
        model = _gateway(executor)
        with pytest.raises(UnexpectedModelBehavior) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert "attempt registration unavailable" in excinfo.value.message

    @pytest.mark.asyncio
    async def test_stream_refusal_raises_after_public_fragments(self):
        refused = _ok_response(text=None, finish_reason="refused", refusal="敏感内容")
        events = (ChatStreamEvent(sequence=1, kind="text", text_delta="start"),)
        executor = _FakeExecutor(_failed_outcome(response=refused, transport_state="ok"), events=events)
        model = _gateway(executor)
        with pytest.raises(UnexpectedModelBehavior) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert excinfo.value.body == "敏感内容"

    @pytest.mark.asyncio
    async def test_stream_producer_error_propagates(self):
        """§6.4-1：生产任务失败经通道投递消费侧；不因迭代耗尽成功。"""
        executor = _FakeExecutor(None, error=TimeoutError("upstream timed out"))
        model = _gateway(executor)
        with pytest.raises(TimeoutError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        # 生产任务原始错误经通道投递消费侧（不转译、不吞没）
        assert "upstream timed out" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_stream_tool_call_delta_and_closed(self):
        events = (
            ChatStreamEvent(
                sequence=1, kind="tool_call_delta", call_index=0, call_id="c1", name="search", arguments_fragment='{"q'
            ),
            ChatStreamEvent(
                sequence=2,
                kind="tool_call_delta",
                call_index=0,
                call_id="c1",
                name="search",
                arguments_fragment='": 1}',
            ),
            ChatStreamEvent(
                sequence=3,
                kind="tool_call_closed",
                call_index=0,
                call_id="c1",
                name="search",
                closed_arguments_json='{"q": 1}',
            ),
            ChatStreamEvent(sequence=4, kind="finish", finish_reason="tool_calls", usage=None),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-t"), events=events)
        model = _gateway(executor)
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
        ) as streamed:
            async for _event in streamed:
                pass
            assert streamed.finish_reason == "tool_call"
            # usage 缺失保 None → unmeasured 标记
            assert streamed.usage.unmeasured_requests == 1
            response = streamed.get()
        tool_calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "search"
        assert isinstance(tool_calls[0].args, str)
        assert json.loads(tool_calls[0].args) == {"q": 1}
        assert tool_calls[0].tool_call_id == "c1"

    @pytest.mark.asyncio
    async def test_stream_cancel_joins_producer(self):
        """§6.4-1/R2：消费取消 join 生产任务并继续传播 CancelledError。"""
        started = asyncio.Event()

        class _HangingExecutor:
            async def execute_chat_turn(self, request, snapshot, *, binding, on_event=None) -> ChatTurnOutcome:
                started.set()
                await asyncio.Event().wait()  # 永不完成——等待取消
                raise AssertionError("unreachable")

        model = _gateway(_HangingExecutor())
        with pytest.raises(asyncio.CancelledError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                await asyncio.wait_for(started.wait(), timeout=2.0)  # 外发已开始
                # 消费取消路径：close_stream 中止生产任务并 join（R2 传播）
                await streamed.close_stream()
        assert excinfo.type is asyncio.CancelledError  # 取消不转译、继续传播（R2）
