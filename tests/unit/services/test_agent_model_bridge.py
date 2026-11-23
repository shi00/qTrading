"""Agent 模型桥 GatewayModel 单测（B-02-4，DD §10.1 / HANDOFF §6.2-6.3）。

覆盖：
- §6.3 SDK 消息 → ChatTurnRequest 一次投影：SystemPromptPart/instructions 保序、
  user 文本（str 与 TextContent）、assistant 历史（TextPart/ToolCallPart/
  ThinkingPart 跳过）、ToolReturnPart/RetryPromptPart tool_call_id 严格配对、
  工具白名单五元组（function_tools/output_tools/output_object）、
  ``output_mode`` 原样保留且不支持模式明确拒绝（M18）；
- 未识别 part 明确拒绝（zero outbound，R21 不裁剪）：未知请求 part、未知
  响应 part、BinaryContent 用户内容、timeout 缺失 / 畸形；
- 拒绝保真（§6.3）：refused 回合 UnexpectedModelBehavior 携带 refusal 原文；
- 费用缺失保 None：usage None → SDK unmeasured 标记（账本事实由 Recorder 经
  execute_chat_turn 承载，SDK 默认 0 不倒灌账本）；
- 流路径：ChatStreamEvent → SDK 流事件穷举分发、finish/usage 确认、
  provider_response_id = model_call_id（身份双通道）、生产任务 join（§6.4-1）、
  失败回合不因迭代耗尽成功（§6.4-4）、分片后失败保 partial（§6.4-5）；
- T-03：截断流（无 finish 确认）与收尾未核（期限已过仍无法 join）按协议失败
  收敛；流式 ``ModelResponse.model_name`` 沿真实模型身份（非绑定别名）。
"""

import asyncio
import dataclasses
import json
from typing import Any, cast
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
from pydantic_ai._parts_manager import ModelResponsePartsManager
from pydantic_ai.messages import (
    BinaryContent,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import ModelProfile
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition

from services.agent_service.model import (
    BOUND_MODEL_ALIAS,
    GatewayModel,
    _TurnChannel,
    build_chat_turn_request,
    derive_model_call_id,
    map_finish_reason,
    map_usage,
)

pytestmark = pytest.mark.unit

#: 投影测试固定调用号（桥层不生成随机身份：``model_call_id`` 必填）
_MC: str = "mc-test"
#: 装配侧持久执行身份（测试替身；生产由叶 workflow 稳定身份派生）
_CALL_IDENTITY: str = "leaf:exec-1:round-1:node-a"


class TestTurnChannelCloseSemantics:
    """R-B02-1 回归：close 同步非阻塞 + 关闭唤醒读者（§8.2 终止唤醒契约）。"""

    @staticmethod
    def _text_event(sequence: int) -> ChatStreamEvent:
        return ChatStreamEvent(sequence=sequence, kind="text", text_delta="x")

    @pytest.mark.asyncio
    async def test_close_with_full_queue_does_not_hang(self):
        """队列满时 close 不阻塞（旧实现在消费方停读时 await put(None) 永久挂死）。"""
        channel = _TurnChannel()
        for sequence in range(1, 257):
            await channel.push(self._text_event(sequence))
        channel.close()  # 同步非阻塞：旧实现此处 await put(None) 永久挂死
        drained = 0
        while (item := await asyncio.wait_for(channel.get(), timeout=1)) is not None:
            assert item.kind == "text"
            drained += 1
        assert drained == 256

    @pytest.mark.asyncio
    async def test_close_wakes_blocked_reader_with_error_once(self):
        """阻塞读者被 close 唤醒：一次性投递错误，随后干净结束。"""
        channel = _TurnChannel()
        error = RuntimeError("turn failed")
        reader = asyncio.create_task(channel.get())
        await asyncio.sleep(0.05)  # 读者已阻塞在空队列
        channel.close(error=error)
        with pytest.raises(RuntimeError) as excinfo:
            await asyncio.wait_for(reader, timeout=1)
        assert excinfo.value is error
        # 错误一次性：后续 get 干净返回 None
        assert await asyncio.wait_for(channel.get(), timeout=1) is None

    @pytest.mark.asyncio
    async def test_drains_events_before_error(self):
        """先取尽已投递事件，再投递错误（终态事实保真，§6.4-4）。"""
        channel = _TurnChannel()
        event = self._text_event(1)
        await channel.push(event)
        error = RuntimeError("failed after fragments")
        channel.close(error=error)
        first = await asyncio.wait_for(channel.get(), timeout=1)
        assert first is event
        with pytest.raises(RuntimeError) as excinfo:
            await asyncio.wait_for(channel.get(), timeout=1)
        assert excinfo.value is error
        assert await asyncio.wait_for(channel.get(), timeout=1) is None


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


def _gateway(executor: Any, tool_schema_version: str = "v1", call_identity: str = _CALL_IDENTITY) -> GatewayModel:
    snapshot = MagicMock(spec=ExecutionSnapshot)
    return GatewayModel(
        executor, _binding(), snapshot, tool_schema_version=tool_schema_version, call_identity=call_identity
    )


def _params(**overrides: Any) -> ModelRequestParameters:
    return ModelRequestParameters(**overrides)


# ---------------------------------------------------------------------------
# R2-I41：模型调用身份确定性（持久执行身份派生，恢复复用原 operation）
# ---------------------------------------------------------------------------


class TestModelCallIdentity:
    """调用号由 (持久执行身份, 回合序号) 派生：无随机源、重放可复现。"""

    def test_derived_id_deterministic_and_scoped(self):
        first = derive_model_call_id(_CALL_IDENTITY, 0)
        assert first == derive_model_call_id(_CALL_IDENTITY, 0)  # 重复派生同一身份
        assert first != derive_model_call_id(_CALL_IDENTITY, 1)  # 回合序号参与身份
        assert first != derive_model_call_id("leaf:other", 0)  # 执行身份参与身份
        assert first.startswith("chat_turn_")

    @pytest.mark.parametrize(
        ("identity", "turn_index", "expected_message"),
        [
            ("", 0, "call_identity must be non-empty text"),
            ("leaf:a", -1, "turn_index must be a non-negative int"),
            ("leaf:a", True, "turn_index must be a non-negative int"),
        ],
        ids=["empty-identity", "negative-turn", "bool-turn"],
    )
    def test_derivation_rejects_invalid_inputs(self, identity: str, turn_index: int, expected_message: str):
        with pytest.raises(ValueError) as excinfo:
            derive_model_call_id(identity, turn_index)
        assert str(excinfo.value) == expected_message

    def test_gateway_requires_execution_identity(self):
        with pytest.raises(ValueError) as excinfo:
            GatewayModel(
                _FakeExecutor(),
                _binding(),
                MagicMock(spec=ExecutionSnapshot),
                tool_schema_version="v1",
                call_identity="",
            )
        assert "call_identity" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_replayed_step_reproduces_same_call_ids(self):
        """步骤重放（新实例从回合 0 重新推进）逐回合复得同一调用号。"""
        history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="hi")])]

        async def _turn_ids() -> list[str]:
            executor = _FakeExecutor(_succeeded_outcome())
            model = _gateway(executor)
            await model.request(history, _settings(), _params())
            await model.request(history, _settings(), _params())
            return [call["request"].model_call_id for call in executor.calls]

        first = await _turn_ids()
        second = await _turn_ids()
        assert first == second
        assert first == [derive_model_call_id(_CALL_IDENTITY, 0), derive_model_call_id(_CALL_IDENTITY, 1)]
        assert len(set(first)) == 2  # 同步骤内不同回合不复用同一调用号

    @pytest.mark.asyncio
    async def test_stream_and_request_share_sequential_turn_ids(self):
        """流式与非流式共用同一回合序号序列（重放时顺序确定）。"""
        events = (ChatStreamEvent(sequence=1, kind="finish", finish_reason="final", usage=_USAGE),)
        executor = _FakeExecutor(_succeeded_outcome(), events=events)
        model = _gateway(executor)
        history: list[ModelMessage] = [ModelRequest(parts=[UserPromptPart(content="hi")])]
        async with model.request_stream(history, _settings(), _params()) as streamed:
            async for _event in streamed:
                pass
        await model.request(history, _settings(), _params())
        ids = [call["request"].model_call_id for call in executor.calls]
        assert ids == [derive_model_call_id(_CALL_IDENTITY, 0), derive_model_call_id(_CALL_IDENTITY, 1)]

    @pytest.mark.asyncio
    async def test_empty_model_call_id_rejected(self):
        """桥层不再生成随机身份：空调用号明确拒绝（zero outbound）。"""
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])],
                _settings(),
                _params(),
                tool_schema_version="v1",
                model_call_id="",
            )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.model_call_id"


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
                    ToolCallPart(tool_name="search", args={"q": 2}, tool_call_id="c2"),
                    ThinkingPart(content="hmm"),
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(tool_name="search", content={"a": 1, "b": "中"}, tool_call_id="c1"),
                    RetryPromptPart(content="retry feedback", tool_call_id="c2", tool_name="search"),
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
        assert len(request.messages[1].assistant_tool_calls) == 2
        assert request.messages[1].assistant_tool_calls[0].arguments_json == json.dumps({"q": 1}, sort_keys=True)
        assert request.messages[1].assistant_tool_calls[0].call_id == "c1"
        # tool_call_id 严格配对
        assert request.messages[2].tool_call_id == "c1"
        part2 = request.messages[2].content_parts[0]
        assert isinstance(part2, TextContentPart)
        assert json.loads(part2.text) == {"a": 1, "b": "中"}
        assert request.messages[3].tool_call_id == "c2"
        # T-04：metadata 只承载资料谱系，工具协议字段（tool_name）不得写入——
        # 否则执行器闭包解析把它判为不合法来源并阻断正常工具后续轮
        assert request.messages[2].metadata == {}
        assert request.messages[3].metadata == {}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("content", "expected_parts"),
        [
            (None, ()),
            ("", (TextContentPart(text=""),)),
            (False, (TextContentPart(text="false"),)),
            (0, (TextContentPart(text="0"),)),
        ],
        ids=["none", "empty-str", "false", "zero"],
    )
    async def test_tool_return_empty_values_roundtrip_keep_call_id(
        self, content: Any, expected_parts: tuple[TextContentPart, ...]
    ) -> None:
        """M03：无返回（None → 空 parts）与空串/False/0 分别往返且保 tool_call_id。

        ``build_chat_turn_request`` 末尾 ``ensure_valid()`` 即核心校验通过证明。
        """
        request = build_chat_turn_request(
            [
                ModelResponse(parts=[ToolCallPart(tool_name="t", args={}, tool_call_id="c1")]),
                ModelRequest(parts=[ToolReturnPart(tool_name="t", content=content, tool_call_id="c1")]),
            ],
            _settings(),
            _params(),
            tool_schema_version="v1",
            model_call_id=_MC,
        )
        tool_message = request.messages[1]
        assert tool_message.role == "tool"
        assert tool_message.tool_call_id == "c1"
        assert tool_message.content_parts == expected_parts

    @pytest.mark.asyncio
    async def test_tool_mapping_and_output_mode(self):
        params = _params(
            output_mode="tool",
            function_tools=[_tool("search")],
            output_tools=[_tool("final_result")],
        )
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            params,
            tool_schema_version="v2",
            model_call_id=_MC,
        )
        by_output = [t for t in request.tools if t.output_tool]
        by_function = [t for t in request.tools if not t.output_tool]
        assert [t.name for t in by_function] == ["search"]
        assert [t.name for t in by_output] == ["final_result"]
        assert all(t.schema_version == "v2" for t in request.tools)
        # M18：工具模式随请求保留，结构 final 经 output tool 承载（无 output_schema）
        assert request.output_mode == "tool"
        assert request.output_schema is None

    @pytest.mark.asyncio
    async def test_native_output_mode_preserved_with_schema(self):
        """M18：native 模式与 output_object schema 一并落 ChatTurnRequest。"""
        params = _params(output_mode="native", output_object=_output_object())
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            params,
            tool_schema_version="v1",
            model_call_id=_MC,
        )
        assert request.output_mode == "native"
        assert request.output_schema == {"type": "object"}
        assert request.output_tools == ()

    @pytest.mark.asyncio
    async def test_prompted_output_mode_preserved_with_schema(self):
        """M18：prompted 模式随请求保留——schema 经 SDK 注入 instructions 承载。"""
        params = _params(output_mode="prompted", output_object=_output_object())
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            params,
            tool_schema_version="v1",
            model_call_id=_MC,
        )
        assert request.output_mode == "prompted"
        assert request.output_schema == {"type": "object"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("output_mode", ["auto", "image", "tool_or_text"])
    async def test_unsupported_output_mode_rejected(self, output_mode: str):
        """M18：桥层不支持的 SDK 模式明确拒绝（不静默降级为 text/tool）。"""
        params = _params(output_mode=cast(Any, output_mode), output_tools=[_tool("final_result")])
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])],
                _settings(),
                params,
                tool_schema_version="v1",
                model_call_id=_MC,
            )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.output_mode"

    @pytest.mark.asyncio
    async def test_frozen_params_and_deadline(self):
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            _params(),
            tool_schema_version="v1",
            model_call_id=_MC,
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
                model_call_id=_MC,
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
                model_call_id=_MC,
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
                model_call_id=_MC,
            )
        assert excinfo.value.code == "unknown_part"

    @pytest.mark.asyncio
    async def test_mixed_str_and_text_content_preserved_in_order(self):
        # §6.3：UserContent 允许裸 str 与 TextContent 混合；中文 / CRLF / 空段 "" 原样保序
        request = build_chat_turn_request(
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=[
                                "第一段\r\n含中文",
                                TextContent("第二段\r\n"),
                                "",
                                TextContent("末段"),
                            ]
                        )
                    ]
                )
            ],
            _settings(),
            _params(),
            tool_schema_version="v1",
            model_call_id=_MC,
        )
        assert [m.role for m in request.messages] == ["user"]
        part = request.messages[0].content_parts[0]
        assert isinstance(part, TextContentPart)
        # 空段保留位置（不跳过/不合并），CRLF 与中文不做归一
        assert part.text == "第一段\r\n含中文\n第二段\r\n\n\n末段"

    @pytest.mark.asyncio
    async def test_mixed_sequence_with_binary_rejected_zero_outbound(self):
        # 未知二进制混在文本序列中仍整体拒绝（fail-closed，不降级为文本/图片）
        image = BinaryContent(data=b"png", media_type="image/png")
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content=["说明", image])])],
                _settings(),
                _params(),
                tool_schema_version="v1",
                model_call_id=_MC,
            )
        assert excinfo.value.code == "unknown_part"
        assert excinfo.value.field_path.endswith("user_prompt.content[1].BinaryContent")

    @pytest.mark.asyncio
    async def test_missing_timeout_rejected(self):
        settings: ModelSettings = ModelSettings(temperature=0.2)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])],
                settings,
                _params(),
                tool_schema_version="v1",
                model_call_id=_MC,
            )
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.settings.timeout"

    @pytest.mark.asyncio
    async def test_missing_schema_version_rejected(self):
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            build_chat_turn_request(
                [ModelRequest(parts=[UserPromptPart(content="go")])],
                _settings(),
                _params(),
                tool_schema_version="",
                model_call_id=_MC,
            )
        assert excinfo.value.field_path == "$.tool_schema_version"


# ---------------------------------------------------------------------------
# 固定 SDK 输出模式语义（M18：防版本漂移，锁步 pydantic-ai 2.66）
# ---------------------------------------------------------------------------


class TestSdkOutputModeSemantics:
    """M18：锁定版 SDK 的三种结构化输出模式真实封装语义。

    源码依据（``pydantic_ai.models.Model.prepare_request``）：mode != tool 清空
    output_tools、mode ∉ {native, prompted} 清空 output_object、prompted/native
    追加 prompted 输出指令到 instruction_parts、native 需 profile 声明
    ``supports_json_schema_output``。桥层只搬运事实，不重造这些语义。
    """

    def _model(self, **kwargs: Any) -> GatewayModel:
        return GatewayModel(
            _FakeExecutor(),
            _binding(),
            MagicMock(spec=ExecutionSnapshot),
            tool_schema_version="v1",
            call_identity=_CALL_IDENTITY,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_prompted_prepare_request_injects_schema_into_instructions(self):
        params = _params(output_mode="prompted", output_object=_output_object())
        _settings_out, prepared = self._model().prepare_request(_settings(), params)
        assert prepared.output_mode == "prompted"
        assert prepared.output_tools == []  # SDK：tool 之外的模式清空 output_tools
        assert prepared.prompted_output_instructions  # schema 指令来源
        request = build_chat_turn_request(
            [ModelRequest(parts=[UserPromptPart(content="go")])],
            _settings(),
            prepared,
            tool_schema_version="v1",
            model_call_id=_MC,
        )
        assert request.output_mode == "prompted"
        assert request.output_schema == {"type": "object"}
        # schema 经 instructions 承载（不是 response_format）
        assert any("final_result" in text for text in request.instructions)

    @pytest.mark.asyncio
    async def test_native_prepare_request_requires_profile_capability(self):
        """native 模式在未声明 ``supports_json_schema_output`` 的连接上被 SDK 拒绝。"""
        from pydantic_ai import UserError

        params = _params(output_mode="native", output_object=_output_object())
        with pytest.raises(UserError) as excinfo:
            self._model().prepare_request(_settings(), params)
        assert "Native structured output is not supported" in str(excinfo.value)
        # 连接证据声明支持后正常放行，且 output_object 保留
        _settings_out, prepared = self._model(profile=ModelProfile(supports_json_schema_output=True)).prepare_request(
            _settings(), params
        )
        assert prepared.output_mode == "native"
        assert prepared.output_object is not None
        assert prepared.output_tools == []

    @pytest.mark.asyncio
    async def test_tool_prepare_request_keeps_output_tools_only(self):
        params = _params(output_mode="tool", output_tools=[_tool("final_result")], output_object=_output_object())
        _settings_out, prepared = self._model().prepare_request(_settings(), params)
        assert prepared.output_mode == "tool"
        assert [t.name for t in prepared.output_tools] == ["final_result"]
        assert prepared.output_object is None  # SDK：tool 模式清空 output_object


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

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("overrides", "expected_code", "expected_path"),
        [
            # provider 词汇（stop）越出核心 finish_reason 词表
            ({"finish_reason": "stop"}, "invalid_finish", "$.finish_reason"),
            # finish=tool_calls 但无工具调用（缺必需事实）
            ({"finish_reason": "tool_calls"}, "invalid_field", "$.tool_calls"),
            # usage_observed=True 但 usage 缺失（缺必需字段）
            ({"usage": None}, "invalid_field", "$.usage"),
        ],
        ids=["provider-vocab", "tool-calls-missing", "usage-missing"],
    )
    async def test_malformed_response_fails_closed(self, overrides, expected_code, expected_path):
        """M18/R21：畸形响应（缺必需字段 / 错误封装词汇）明确拒绝，不拼装成功。"""
        bad = _ok_response(**overrides)
        executor = _FakeExecutor(_succeeded_outcome(response=bad))
        model = _gateway(executor)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            await model.request([ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params())
        assert excinfo.value.code == expected_code
        assert excinfo.value.field_path == expected_path


# ---------------------------------------------------------------------------
# Profile：请求绑定的真实 Profile（检视 M06）
# ---------------------------------------------------------------------------


class TestBoundProfile:
    def test_profile_returns_bound_profile(self):
        """注入的请求绑定 Profile 原样返回，不被新的默认值替换。"""
        profile: ModelProfile = {"supports_tools": True, "supports_json_schema_output": True}
        model = GatewayModel(
            _FakeExecutor(),
            _binding(),
            MagicMock(spec=ExecutionSnapshot),
            tool_schema_version="v1",
            call_identity=_CALL_IDENTITY,
            profile=profile,
        )
        assert model.profile == profile

    def test_profile_unknown_when_not_bound(self):
        """未绑定 Profile → 空 Profile（无能力声明），不以乐观默认冒充已核验事实。"""
        model = _gateway(_FakeExecutor())
        assert model.profile == {}
        assert "supports_tools" not in model.profile
        assert "supports_json_schema_output" not in model.profile


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
        # 流式路径同样沿真实模型身份回报（非绑定别名，R21）
        assert response.model_name == "glm-4-air"

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
    async def test_stream_tool_call_fragmented_name_appends_suffix(self):
        """M04：分片名称按 SDK 增量语义续拼——已建 part 后的累积名取其前缀差。"""

        events = (
            ChatStreamEvent(
                sequence=1,
                kind="tool_call_delta",
                call_index=0,
                call_id="c1",
                name="get_",
                arguments_fragment='{"a',
            ),
            ChatStreamEvent(
                sequence=2,
                kind="tool_call_delta",
                call_index=0,
                call_id="c1",
                name="get_quote",
                arguments_fragment='": 1}',
            ),
            ChatStreamEvent(
                sequence=3,
                kind="tool_call_closed",
                call_index=0,
                call_id="c1",
                name="get_quote",
                closed_arguments_json='{"a": 1}',
            ),
            ChatStreamEvent(sequence=4, kind="finish", finish_reason="tool_calls"),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-f"), events=events)
        model = _gateway(executor)
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
        ) as streamed:
            async for _event in streamed:
                pass
            response = streamed.get()
        tool_calls = [p for p in response.parts if isinstance(p, ToolCallPart)]
        assert len(tool_calls) == 1
        assert tool_calls[0].tool_name == "get_quote"
        assert tool_calls[0].args == '{"a": 1}'
        assert tool_calls[0].tool_call_id == "c1"

    @pytest.mark.asyncio
    async def test_stream_tool_call_late_conflicting_id_rejected(self):
        """M04：已建 part 后晚到不同 id = 身份漂移，fail-closed（不静默忽略）。"""

        events = (
            ChatStreamEvent(
                sequence=1, kind="tool_call_delta", call_index=0, call_id="c1", name="search", arguments_fragment="{"
            ),
            ChatStreamEvent(
                sequence=2, kind="tool_call_delta", call_index=0, call_id="c2", name="search", arguments_fragment="}"
            ),
            ChatStreamEvent(sequence=3, kind="finish", finish_reason="tool_calls"),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-d"), events=events)
        model = _gateway(executor)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.tool_call_id"
        assert len(executor.calls) == 1  # noqa: weak-assertion 派发次数即本用例事实：无重发路径

    @pytest.mark.asyncio
    async def test_stream_tool_call_closed_mismatch_rejected(self):
        """M04：闭合完整参数与增量和不等时不静默采用任一方——协议失败。"""

        events = (
            ChatStreamEvent(
                sequence=1,
                kind="tool_call_delta",
                call_index=0,
                call_id="c1",
                name="search",
                arguments_fragment='{"q": 1}',
            ),
            ChatStreamEvent(
                sequence=2,
                kind="tool_call_closed",
                call_index=0,
                call_id="c1",
                name="search",
                closed_arguments_json='{"q": 2}',
            ),
            ChatStreamEvent(sequence=3, kind="finish", finish_reason="tool_calls"),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-c"), events=events)
        model = _gateway(executor)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert excinfo.value.field_path == "$.closed_arguments_json"

    @pytest.mark.asyncio
    async def test_stream_tool_call_incremental_equals_sdk_aggregation(self):
        """M04：交错 + 分片名 + 晚 id 的桥层最终 part 与锁定版 SDK 逐项聚合一致。"""

        events = (
            ChatStreamEvent(
                sequence=1,
                kind="tool_call_delta",
                call_index=0,
                call_id="c0",
                name="get_",
                arguments_fragment='{"a',
            ),
            ChatStreamEvent(
                sequence=2,
                kind="tool_call_delta",
                call_index=1,
                call_id="c1",
                name="lookup",
                arguments_fragment='{"b": 2}',
            ),
            ChatStreamEvent(
                sequence=3,
                kind="tool_call_delta",
                call_index=0,
                call_id="c0",
                name="get_quote",
                arguments_fragment='": 1}',
            ),
            ChatStreamEvent(
                sequence=4,
                kind="tool_call_closed",
                call_index=0,
                call_id="c0",
                name="get_quote",
                closed_arguments_json='{"a": 1}',
            ),
            ChatStreamEvent(
                sequence=5,
                kind="tool_call_closed",
                call_index=1,
                call_id="c1",
                name="lookup",
                closed_arguments_json='{"b": 2}',
            ),
            ChatStreamEvent(sequence=6, kind="finish", finish_reason="tool_calls"),
        )
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-r"), events=events)
        model = _gateway(executor)
        async with model.request_stream(
            [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
        ) as streamed:
            async for _event in streamed:
                pass
            response = streamed.get()

        reference = ModelResponsePartsManager(model_request_parameters=_params())
        reference.handle_tool_call_delta(vendor_part_id=("ref", 0), tool_name="get_", args='{"a', tool_call_id="c0")
        reference.handle_tool_call_delta(
            vendor_part_id=("ref", 1), tool_name="lookup", args='{"b": 2}', tool_call_id="c1"
        )
        reference.handle_tool_call_delta(vendor_part_id=("ref", 0), tool_name="quote", args='": 1}', tool_call_id=None)
        bridge_parts = [p for p in response.parts if isinstance(p, ToolCallPart)]
        reference_parts = [p for p in reference.get_parts() if isinstance(p, ToolCallPart)]
        assert [(p.tool_name, p.args, p.tool_call_id) for p in bridge_parts] == [
            (p.tool_name, p.args, p.tool_call_id) for p in reference_parts
        ]
        assert [p.tool_name for p in bridge_parts] == ["get_quote", "lookup"]

    @pytest.mark.asyncio
    async def test_stream_unknown_event_kind_rejected(self):
        """M04：未知流事件形态显式拒绝（不产出 part、不重发请求）。"""

        events = (ChatStreamEvent(sequence=1, kind=cast(Any, "bogus"), text_delta="x"),)
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-u"), events=events)
        model = _gateway(executor)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert excinfo.value.code == "unknown_part"
        assert len(executor.calls) == 1  # noqa: weak-assertion 派发次数即本用例事实：无重发路径

    @pytest.mark.asyncio
    async def test_stream_truncated_without_finish_fails_closed(self):
        """T-03/R21：截断流（干净 EOF 但无 finish 确认）不得当作成功回合。"""

        events = (ChatStreamEvent(sequence=1, kind="text", text_delta="hel"),)
        executor = _FakeExecutor(_succeeded_outcome(model_call_id="mc-tr"), events=events)
        model = _gateway(executor)
        with pytest.raises(ModelTurnProtocolError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], _settings(), _params()
            ) as streamed:
                async for _event in streamed:
                    pass
        assert excinfo.value.code == "invalid_field"
        assert excinfo.value.field_path == "$.finish_reason"

    @pytest.mark.asyncio
    async def test_close_stream_unconfirmed_after_deadline(self):
        """T-03/R21：期限已过仍无法确认生产收敛 → 未核收尾（不退化为干净关闭）。"""

        started = asyncio.Event()

        class _HangingExecutor:
            async def execute_chat_turn(self, request, snapshot, *, binding, on_event=None) -> ChatTurnOutcome:
                started.set()
                await asyncio.Event().wait()  # 永不完成——只能等待取消
                raise AssertionError("unreachable")

        model = _gateway(_HangingExecutor())
        with pytest.raises(asyncio.CancelledError) as excinfo:
            async with model.request_stream(
                [ModelRequest(parts=[UserPromptPart(content="hi")])], ModelSettings(timeout=0.01), _params()
            ) as streamed:
                await asyncio.wait_for(started.wait(), timeout=2.0)
                await asyncio.sleep(0.05)  # 越过冻结期限
                with pytest.raises(ModelTurnProtocolError) as protocol_exc:
                    await streamed.close_stream()
                assert protocol_exc.value.code == "unconfirmed_close"
                assert protocol_exc.value.field_path == "$.stream.close"
        assert type(excinfo.value) is asyncio.CancelledError

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
