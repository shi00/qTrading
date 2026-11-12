"""B-01-3 聊天 turn/stream 公共协议单元测试（设计正本 §10.1，Agent 模型桥）。

覆盖：ChatMessage/ContentPart/FunctionToolSpec/ChatTurnRequest/ChatTurnResponse/
ChatStreamEvent 的冻结协议与不变量——SDK parts 往返语义、tool-only content=None
合法、未知 part 明确拒绝、工具参数闭合校验、usage 缺失保 None。
"""

import dataclasses
import json
from decimal import Decimal

import pytest

from core.model_management_types import (
    AssistantToolCall,
    ChatMessage,
    ChatStreamEvent,
    ChatTurnRequest,
    ChatTurnResponse,
    FunctionToolSpec,
    ImageContentPart,
    ModelTurnProtocolError,
    NormalizedLLMUsage,
    TextContentPart,
    validate_chat_message,
    validate_closed_tool_arguments,
)

_ALL_CODES = {
    "invalid_pairing",
    "invalid_part",
    "invalid_arguments_json",
    "duplicate_key",
    "non_finite_number",
    "invalid_field",
    "invalid_finish",
}


def code_of(excinfo: pytest.ExceptionInfo[ModelTurnProtocolError]) -> str:
    assert excinfo.value.code in _ALL_CODES
    return excinfo.value.code


def text_part(text: str) -> TextContentPart:
    return TextContentPart(text=text)


def tool_call(call_id: str = "call-1", name: str = "read_source", arguments: str = '{"path":"x"}') -> AssistantToolCall:
    return AssistantToolCall(call_id=call_id, name=name, arguments_json=arguments)


# ---------------------------------------------------------------------------
# validate_closed_tool_arguments（§10.1：工具参数闭合前不派发）
# ---------------------------------------------------------------------------


class TestClosedToolArguments:
    def test_valid_object_returns_exact_decimal(self) -> None:
        parsed = validate_closed_tool_arguments('{"path":"a.csv","weight":0.1}')
        assert parsed == {"path": "a.csv", "weight": Decimal("0.1")}
        assert isinstance(parsed["weight"], Decimal)

    def test_nested_object_and_array(self) -> None:
        parsed = validate_closed_tool_arguments('{"opt":{"deep":[1,2]},"flag":false}')
        assert parsed["opt"] == {"deep": [1, 2]}
        assert parsed["flag"] is False

    def test_duplicate_key_rejected(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_closed_tool_arguments('{"a":1,"a":2}')
        assert code_of(exc) == "duplicate_key"

    def test_non_finite_rejected(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_closed_tool_arguments('{"v":NaN}')
        assert code_of(exc) == "non_finite_number"

    def test_non_object_rejected(self) -> None:
        for raw in ("[1,2]", '"x"', "42", "null"):
            with pytest.raises(ModelTurnProtocolError) as exc:
                validate_closed_tool_arguments(raw)
            assert code_of(exc) == "invalid_arguments_json"

    def test_malformed_json_rejected(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_closed_tool_arguments('{"a":')
        assert code_of(exc) == "invalid_arguments_json"

    def test_non_str_input_rejected(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_closed_tool_arguments({"a": 1})  # type: ignore[arg-type]  # 验证拒绝路径
        assert code_of(exc) == "invalid_arguments_json"

    def test_fragment_not_closed_rejected(self) -> None:
        # 分片未闭合（§7.1/§10.1）：流式累积期间不得派发
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_closed_tool_arguments('{"path":')
        assert code_of(exc) == "invalid_arguments_json"


# ---------------------------------------------------------------------------
# ChatMessage / ContentPart（§10.1）
# ---------------------------------------------------------------------------


class TestChatMessage:
    def test_user_text_message(self) -> None:
        msg = ChatMessage(role="user", content_parts=(text_part("分析该股票"),))
        validate_chat_message(msg)
        assert to_parts(msg) == (text_part("分析该股票"),)

    def test_tool_only_assistant_message_legal(self) -> None:
        # tool-only content=None 合法：content_parts 空且仅 tool_calls
        msg = ChatMessage(role="assistant", content_parts=(), assistant_tool_calls=(tool_call(),))
        validate_chat_message(msg)

    def test_assistant_text_and_tool_calls(self) -> None:
        msg = ChatMessage(
            role="assistant",
            content_parts=(text_part("我需要读取来源"),),
            assistant_tool_calls=(tool_call(),),
        )
        validate_chat_message(msg)

    def test_tool_role_requires_tool_call_id(self) -> None:
        msg = ChatMessage(role="tool", content_parts=(text_part("结果"),))
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(msg)
        assert code_of(exc) == "invalid_pairing"

    def test_tool_role_with_tool_call_id_valid(self) -> None:
        msg = ChatMessage(role="tool", content_parts=(text_part("结果"),), tool_call_id="call-1")
        validate_chat_message(msg)

    def test_system_rejects_tool_calls(self) -> None:
        msg = ChatMessage(
            role="system",
            content_parts=(text_part("你是研究员"),),
            assistant_tool_calls=(tool_call(),),
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(msg)
        assert code_of(exc) == "invalid_pairing"

    def test_user_rejects_tool_call_id(self) -> None:
        msg = ChatMessage(role="user", content_parts=(text_part("q"),), tool_call_id="call-1")
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(msg)
        assert code_of(exc) == "invalid_pairing"

    def test_image_part_png_jpeg_only(self) -> None:
        png = ImageContentPart(mime="image/png", binary_ref="bin-1")
        validate_chat_message(ChatMessage(role="user", content_parts=(png,)))
        jpeg = ImageContentPart(mime="image/jpeg", binary_ref="bin-2")
        validate_chat_message(ChatMessage(role="user", content_parts=(jpeg,)))
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(
                ChatMessage(role="user", content_parts=(ImageContentPart(mime="image/webp", binary_ref="b"),))
            )
        assert code_of(exc) == "invalid_part"

    def test_empty_text_part_rejected(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(ChatMessage(role="user", content_parts=(TextContentPart(text=""),)))
        assert code_of(exc) == "invalid_part"

    def test_metadata_keys_non_empty(self) -> None:
        msg = ChatMessage(role="user", content_parts=(text_part("q"),), metadata={"source_ref": "src-1@1"})
        validate_chat_message(msg)
        bad = ChatMessage(role="user", content_parts=(text_part("q"),), metadata={"": "v"})
        with pytest.raises(ModelTurnProtocolError) as exc:
            validate_chat_message(bad)
        assert code_of(exc) == "invalid_field"


def to_parts(msg: ChatMessage) -> tuple[object, ...]:
    return msg.content_parts


# ---------------------------------------------------------------------------
# FunctionToolSpec / ChatTurnRequest（§10.1）
# ---------------------------------------------------------------------------


class TestChatTurnRequest:
    def spec(self, *, output_tool: bool = False) -> FunctionToolSpec:
        return FunctionToolSpec(
            name="read_source",
            description="读取已选来源",
            json_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            schema_version="schema-v1",
            output_tool=output_tool,
        )

    def test_request_fields_frozen_and_ordered(self) -> None:
        request = ChatTurnRequest(
            model_call_id="mc-1",
            messages=(
                ChatMessage(
                    role="user",
                    content_parts=(text_part("q")),
                ),
            ),
            instructions=("指令一", "指令二"),
            tools=(self.spec(),),
            output_schema=None,
            frozen_params={"temperature": "0.2"},
            max_output_tokens=2048,
            deadline=123.5,
        )
        assert request.instructions == ("指令一", "指令二")  # SystemPromptPart/instructions 保序
        assert request.deadline == 123.5
        assert request.output_schema is None
        # 精确异常类型即强断言：frozen dataclass 赋值必须抛 FrozenInstanceError
        with pytest.raises(dataclasses.FrozenInstanceError):  # noqa: weak-assertion  # 异常类型精确且赋值后无可验证状态变化
            request.model_call_id = "x"  # type: ignore[misc]  # frozen dataclass 赋值拒绝
        assert request.model_call_id == "mc-1"  # 赋值未生效（不可变保持）

    def test_output_tools_marked_as_structural_final(self) -> None:
        final_tool = self.spec(output_tool=True)
        assert final_tool.output_tool is True
        request = ChatTurnRequest(
            model_call_id="mc-1",
            messages=(
                ChatMessage(
                    role="user",
                    content_parts=(text_part("q")),
                ),
            ),
            instructions=(),
            tools=(),
            output_tools=(final_tool,),
            output_schema={"type": "object"},
            frozen_params={},
            max_output_tokens=None,
            deadline=1.0,
        )
        assert request.output_tools[0].output_tool is True

    def test_empty_messages_rejected(self) -> None:
        request = ChatTurnRequest(
            model_call_id="mc-1",
            messages=(),
            instructions=(),
            tools=(),
            output_schema=None,
            frozen_params={},
            max_output_tokens=None,
            deadline=1.0,
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            request.ensure_valid()
        assert code_of(exc) == "invalid_field"

    def test_spec_requires_identifiers(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            FunctionToolSpec(
                name="", description="d", json_schema={}, schema_version="v1", output_tool=False
            ).ensure_valid()
        assert code_of(exc) == "invalid_field"
        with pytest.raises(ModelTurnProtocolError) as exc:
            FunctionToolSpec(
                name="t", description="d", json_schema={}, schema_version="", output_tool=False
            ).ensure_valid()
        assert code_of(exc) == "invalid_field"


# ---------------------------------------------------------------------------
# ChatTurnResponse（§10.1：usage 缺失保 None；finish/refusal/transport 分态）
# ---------------------------------------------------------------------------


class TestChatTurnResponse:
    def test_tool_only_response_legal(self) -> None:
        resp = ChatTurnResponse(
            text=None,
            tool_calls=(tool_call(),),
            finish_reason="tool_calls",
            actual_model="gpt-x",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        resp.ensure_valid()
        assert resp.text is None
        assert resp.usage is None  # 不能把 SDK 默认 usage=0 倒灌账本

    def test_usage_observed_without_usage_rejected(self) -> None:
        resp = ChatTurnResponse(
            text="ok",
            finish_reason="final",
            actual_model="m",
            usage_observed=True,
            usage=None,
            transport_state="ok",
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            resp.ensure_valid()
        assert code_of(exc) == "invalid_field"

    def test_refusal_finish_requires_refusal_text(self) -> None:
        resp = ChatTurnResponse(
            text=None,
            finish_reason="refused",
            refusal="政策原因",
            actual_model="m",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        resp.ensure_valid()
        bad = ChatTurnResponse(
            text=None,
            finish_reason="refused",
            actual_model="m",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            bad.ensure_valid()
        assert code_of(exc) == "invalid_field"

    def test_tool_calls_finish_requires_calls(self) -> None:
        bad = ChatTurnResponse(
            text=None,
            finish_reason="tool_calls",
            actual_model="m",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            bad.ensure_valid()
        assert code_of(exc) == "invalid_field"

    def test_transport_states_closed_set(self) -> None:
        for state in ("ok", "failed", "unknown"):
            resp = ChatTurnResponse(
                text="x",
                finish_reason="final",
                actual_model="m",
                usage_observed=False,
                usage=None,
                transport_state=state,  # type: ignore[arg-type]  # 逐值构造合法态
            )
            resp.ensure_valid()

    def test_tool_call_arguments_must_be_closed(self) -> None:
        resp = ChatTurnResponse(
            text=None,
            tool_calls=(tool_call(arguments='{"a":'),),
            finish_reason="tool_calls",
            actual_model="m",
            usage_observed=False,
            usage=None,
            transport_state="ok",
        )
        with pytest.raises(ModelTurnProtocolError) as exc:
            resp.ensure_valid()
        assert code_of(exc) == "invalid_arguments_json"


# ---------------------------------------------------------------------------
# ChatStreamEvent（§10.1：choice/tool index、稳定 call_id、闭合后才可派发）
# ---------------------------------------------------------------------------


class TestChatStreamEvent:
    def test_text_delta_event(self) -> None:
        event = ChatStreamEvent(sequence=1, kind="text", text_delta="你好")
        event.ensure_valid()
        assert event.call_id is None

    def test_tool_fragment_accumulates_before_closure(self) -> None:
        event = ChatStreamEvent(
            sequence=2,
            kind="tool_call_delta",
            call_index=0,
            call_id="call-1",
            name="read_source",
            arguments_fragment='{"pa',
        )
        event.ensure_valid()
        # 分片事件不携带闭合参数（闭合前不派发）
        assert event.closed_arguments_json is None

    def test_closed_event_requires_valid_arguments(self) -> None:
        event = ChatStreamEvent(
            sequence=3,
            kind="tool_call_closed",
            call_index=0,
            call_id="call-1",
            name="read_source",
            closed_arguments_json='{"path":"x"}',
        )
        event.ensure_valid()
        with pytest.raises(ModelTurnProtocolError) as exc:
            ChatStreamEvent(
                sequence=3,
                kind="tool_call_closed",
                call_index=0,
                call_id="call-1",
                name="read_source",
                closed_arguments_json='{"path":',
            ).ensure_valid()
        assert code_of(exc) == "invalid_arguments_json"

    def test_finish_event_carries_optional_usage(self) -> None:
        usage = NormalizedLLMUsage(
            input_total_tokens=10,
            uncached_input_tokens=10,
            cache_read_tokens=None,
            cache_write_tokens=None,
            output_tokens=5,
        )
        event = ChatStreamEvent(sequence=4, kind="finish", finish_reason="final", usage=usage)
        event.ensure_valid()
        assert event.usage == usage
        empty = ChatStreamEvent(sequence=4, kind="finish", finish_reason="final", usage=None)
        empty.ensure_valid()  # usage 缺失保 None

    def test_kind_field_requirements(self) -> None:
        with pytest.raises(ModelTurnProtocolError) as exc:
            ChatStreamEvent(sequence=1, kind="text", text_delta=None).ensure_valid()
        assert code_of(exc) == "invalid_field"
        with pytest.raises(ModelTurnProtocolError) as exc:
            ChatStreamEvent(sequence=1, kind="finish", finish_reason=None).ensure_valid()
        assert code_of(exc) == "invalid_field"
        with pytest.raises(ModelTurnProtocolError) as exc:
            ChatStreamEvent(sequence=0, kind="text", text_delta="x").ensure_valid()
        assert code_of(exc) == "invalid_field"

    def test_roundtrip_preserves_order_and_content(self) -> None:
        events = [
            ChatStreamEvent(sequence=1, kind="text", text_delta="a"),
            ChatStreamEvent(
                sequence=2, kind="tool_call_delta", call_index=0, call_id="c1", name="t", arguments_fragment='{"'
            ),
            ChatStreamEvent(
                sequence=3,
                kind="tool_call_closed",
                call_index=0,
                call_id="c1",
                name="t",
                closed_arguments_json='{"a":1}',
            ),
            ChatStreamEvent(sequence=4, kind="finish", finish_reason="tool_calls"),
        ]
        for event in events:
            event.ensure_valid()
        assert [event.sequence for event in events] == [1, 2, 3, 4]
        payload = json.dumps(
            [[event.kind, event.text_delta, event.arguments_fragment, event.closed_arguments_json] for event in events],
            ensure_ascii=False,
        )
        assert json.loads(payload)[2][3] == '{"a":1}'
