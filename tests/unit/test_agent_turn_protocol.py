"""M01 模型 turn 协议（设计正本 §7.1 / §12.5 M01）。

先建立行为反例：旧文本请求/响应不因扩展字段改变；tool-only 轮不触发最终业务
校验；未闭合分片不得作为可派发工具调用；refusal 不切换候选；on_delta 由同一
Executor 消费。DTO 与校验函数缺失时测试以行为失败（AttributeError / TypeError）
呈现，不制造收集错误。
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import core.model_management_types as mmt
from core.errors import AIContentRefusedError
from core.i18n import Message
from core.model_management_types import (
    AdapterResponse,
    ExecutionSnapshot,
    InferenceRequest,
    NormalizedLLMUsage,
    PersistenceOutcome,
    ResolvedConnection,
    ValidatedBusinessOutput,
)
from services.ai_service.scenario_executor import ScenarioExecutor
from services.ai_service.usage_recorder import ReservationOutcome

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 协议 DTO 构造
# ---------------------------------------------------------------------------


def _request(*, turn: Any = None, deadline: float | None = None) -> InferenceRequest:
    if deadline is None:
        try:
            deadline = asyncio.get_running_loop().time() + 30.0
        except RuntimeError:
            deadline = time.monotonic() + 30.0
    kwargs: dict[str, Any] = {"turn": turn} if turn is not None else {}
    return InferenceRequest(
        scenario_id="stock_analysis",
        task_id="task-1",
        invocation_id="inv-1",
        payload={"system_prompt": "系统", "prompt": "问题"},
        deadline=deadline,
        **kwargs,
    )


def _turn_request(messages: tuple[Any, ...], tools: tuple[Any, ...] = (), max_output_tokens: int = 64) -> Any:
    return mmt.ModelTurnRequest(
        messages=messages,
        tools=tools,
        output_contract="answer/1",
        max_output_tokens=max_output_tokens,
    )


def _tool_call(call_id: str = "c1", name: str = "lookup", arguments_json: str = '{"q":"x"}') -> Any:
    return mmt.ToolCall(call_id=call_id, name=name, arguments_json=arguments_json)


def _tool_only_result(call_id: str = "c1") -> Any:
    return mmt.ModelTurnResult(
        message=mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(call_id),)),
        finish_reason="tool_calls",
    )


def _valid_tool_messages() -> tuple[Any, ...]:
    return (
        mmt.ModelMessage(role="user", content="查一下"),
        mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(),)),
        mmt.ModelMessage(role="tool", content='{"ok":true}', tool_call_id="c1"),
    )


# ---------------------------------------------------------------------------
# 旧文本接口保持
# ---------------------------------------------------------------------------


def test_old_text_request_keeps_no_turn() -> None:
    request = _request()
    assert getattr(request, "turn", None) is None


def test_old_text_response_keeps_no_turn() -> None:
    response = AdapterResponse(content="ok", usage=None, actual_model="m")
    assert getattr(response, "turn", None) is None


def test_turn_dtos_are_frozen_slots_with_fixed_fields() -> None:
    expected = {
        "ToolCall": ("call_id", "name", "arguments_json"),
        "ToolDefinition": ("name", "description", "parameters_json", "schema_revision"),
        "ModelMessage": ("role", "content", "tool_calls", "tool_call_id", "media_refs"),
        "ModelTurnRequest": ("messages", "tools", "output_contract", "max_output_tokens"),
        "ModelTurnResult": ("message", "finish_reason"),
        "ModelStreamDelta": (
            "sequence",
            "kind",
            "call_index",
            "call_id",
            "name",
            "text",
            "arguments_fragment",
            "finish_reason",
        ),
    }
    for name, fields in expected.items():
        cls = getattr(mmt, name)
        assert [f.name for f in dataclasses.fields(cls)] == list(fields)
        assert dataclasses.is_dataclass(cls)
        assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
        assert cls.__slots__  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# 消息 / turn 校验
# ---------------------------------------------------------------------------


def test_tool_only_message_allows_none_content_but_contentless_without_calls_is_rejected() -> None:
    validate_request = mmt.validate_model_turn_request
    validate_request(_turn_request(_valid_tool_messages()))
    invalid = _turn_request(
        (
            mmt.ModelMessage(role="user", content="查一下"),
            mmt.ModelMessage(role="assistant", content=None),
        )
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_request(invalid)


def test_tool_arguments_reject_duplicate_keys_and_non_finite_numbers() -> None:
    for bad in ('{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', "[]", "not json"):
        request = _turn_request(
            (
                mmt.ModelMessage(role="user", content="查一下"),
                mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(arguments_json=bad),)),
                mmt.ModelMessage(role="tool", content="{}", tool_call_id="c1"),
            )
        )
        with pytest.raises(mmt.ModelTurnProtocolError):
            mmt.validate_model_turn_request(request)


def test_tool_message_must_pair_with_exactly_one_completed_call() -> None:
    validate_request = mmt.validate_model_turn_request
    # 指向不存在的 call
    unknown = _turn_request(
        (
            mmt.ModelMessage(role="user", content="查一下"),
            mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(),)),
            mmt.ModelMessage(role="tool", content="{}", tool_call_id="c2"),
        )
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_request(unknown)
    # 同一 call 返回两次
    duplicate_return = _turn_request(
        (
            mmt.ModelMessage(role="user", content="查一下"),
            mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(),)),
            mmt.ModelMessage(role="tool", content="{}", tool_call_id="c1"),
            mmt.ModelMessage(role="tool", content="{}", tool_call_id="c1"),
        )
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_request(duplicate_return)
    # tool 消息不得自带 tool_calls
    nested = _turn_request(
        (
            mmt.ModelMessage(role="user", content="查一下"),
            mmt.ModelMessage(role="assistant", content=None, tool_calls=(_tool_call(),)),
            mmt.ModelMessage(role="tool", content="{}", tool_call_id="c1", tool_calls=(_tool_call("c9"),)),
        )
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_request(nested)


def test_result_validation_enforces_finish_reason_shape() -> None:
    validate_result = mmt.validate_model_turn_result
    validate_result(_tool_only_result())
    validate_result(
        mmt.ModelTurnResult(message=mmt.ModelMessage(role="assistant", content="done"), finish_reason="final")
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_result(
            mmt.ModelTurnResult(message=mmt.ModelMessage(role="assistant", content=None), finish_reason="final")
        )
    with pytest.raises(mmt.ModelTurnProtocolError):
        validate_result(
            mmt.ModelTurnResult(
                message=mmt.ModelMessage(role="assistant", content="x"), finish_reason=cast(Any, "bogus")
            )
        )


def test_unclosed_or_ill_formed_stream_is_not_dispatchable() -> None:
    delta = mmt.ModelStreamDelta
    assemble = mmt.assemble_model_stream
    unclosed = (
        delta(sequence=1, kind="text", text="思考"),
        delta(sequence=2, kind="tool_call", call_index=0, call_id="c1", name="lookup", arguments_fragment='{"q":'),
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        assemble(unclosed)
    no_finish = (delta(sequence=1, kind="text", text="你好"),)
    with pytest.raises(mmt.ModelTurnProtocolError):
        assemble(no_finish)
    after_finish = (
        delta(sequence=1, kind="text", text="你好"),
        delta(sequence=2, kind="finish", finish_reason="final"),
        delta(sequence=3, kind="text", text="迟到"),
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        assemble(after_finish)
    bad_sequence = (
        delta(sequence=1, kind="text", text="a"),
        delta(sequence=3, kind="finish", finish_reason="final"),
    )
    with pytest.raises(mmt.ModelTurnProtocolError):
        assemble(bad_sequence)


def test_stream_assembly_builds_complete_tool_call() -> None:
    delta = mmt.ModelStreamDelta
    assemble = mmt.assemble_model_stream
    deltas = (
        delta(sequence=1, kind="tool_call", call_index=0, call_id="c1", name="lookup", arguments_fragment='{"q"'),
        delta(sequence=2, kind="tool_call", call_index=0, arguments_fragment=':"x"}'),
        delta(sequence=3, kind="finish", finish_reason="tool_calls"),
    )
    result = assemble(deltas)
    assert result.finish_reason == "tool_calls"
    assert result.message.tool_calls[0].arguments_json == '{"q":"x"}'
    assert result.message.tool_calls[0].call_id == "c1"


# ---------------------------------------------------------------------------
# Executor：同一候选 / 重试 / 计费权威
# ----------------------------------------------------------------------------


def _remote_conn(connection_id: str = "conn-remote") -> dict[str, Any]:
    return dataclasses.asdict(
        ResolvedConnection(
            connection_id=connection_id,
            connection_revision=1,
            adapter_kind="litellm_chat",
            provider="zhipu",
            model="glm-4-air",
            api_base=None,
            adapter_options={"temperature": 0.2},
            context_window=8192,
            concurrency_limit=4,
            credential_ref="keyring:item",
            credential_version="v1",
        )
    )


def _snapshot(count: int = 1) -> ExecutionSnapshot:
    candidates = tuple(
        {
            "candidate_index": index,
            "task_id": "task-1",
            "connection_id": "conn-remote",
            "target_fingerprint": "fp-1",
            "purpose": "stock_analysis",
        }
        for index in range(count)
    )
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=5,
        routes=(
            {
                "scenario_id": "stock_analysis",
                "route_revision": 1,
                "candidates": candidates,
            },
        ),
        connections=(_remote_conn(),),
        business_contract_versions={"task-1": "v1"},
        security_scope_version=1,
        created_at="2026-11-06T00:00:00Z",
    )


def _recorder() -> MagicMock:
    recorder = MagicMock()
    recorder.observe_usage = MagicMock()
    recorder.ensure_invocation = AsyncMock(return_value=PersistenceOutcome(persisted=True))
    recorder.try_reserve = AsyncMock(
        return_value=ReservationOutcome(persisted=True, error_code=None, reservation=MagicMock())
    )
    recorder.mark_dispatched = AsyncMock(return_value=PersistenceOutcome(persisted=True))
    recorder.begin_attempt = AsyncMock(return_value=PersistenceOutcome(persisted=True))
    recorder.submit_terminal = AsyncMock(return_value=PersistenceOutcome(persisted=True))
    return recorder


def _executor(adapter: Any, recorder: MagicMock) -> ScenarioExecutor:
    config_service = MagicMock()
    config_service.authorize_attempt = AsyncMock(return_value=MagicMock(security_revision=1))
    compatibility = MagicMock()
    compatibility.resolve_execution_params = MagicMock(
        return_value=MagicMock(compatible=True, reason=None, resolved_options={"temperature": 0.2})
    )
    return ScenarioExecutor(
        config_service=config_service,
        recorder=recorder,
        compatibility=compatibility,
        adapters={"litellm_chat": adapter},
        retry_attempts=0,
        cooldown_seconds=0.0,
        local_only_mode_reader=lambda: False,
    )


def _turn_response(finish_reason: str, call_id: str = "c1") -> AdapterResponse:
    turn = (
        _tool_only_result(call_id)
        if finish_reason == "tool_calls"
        else mmt.ModelTurnResult(
            message=mmt.ModelMessage(role="assistant", content=None),
            finish_reason=cast(Any, finish_reason),
        )
    )
    return AdapterResponse(
        content=None,
        usage=NormalizedLLMUsage(None, None, None, None, None),
        actual_model="glm-4-air",
        turn=turn,
    )


@pytest.mark.asyncio
async def test_tool_only_turn_skips_final_business_validation_but_keeps_billing() -> None:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=_turn_response("tool_calls"))
    recorder = _recorder()
    executor = _executor(adapter, recorder)
    validator = MagicMock()

    result = await executor.execute(
        _request(turn=_turn_request(_valid_tool_messages())),
        _snapshot(),
        validator,
    )

    assert result.status == "succeeded"
    assert validator.call_count == 0
    assert len(getattr(result, "turns", ())) == 1
    assert result.turns[0].finish_reason == "tool_calls"
    # 计费/尝试仍由同一 Executor 收敛
    recorder.try_reserve.assert_awaited_once()
    recorder.mark_dispatched.assert_awaited_once()
    recorder.begin_attempt.assert_awaited_once()
    recorder.submit_terminal.assert_awaited_once()
    recorder.observe_usage.assert_called_once()


@pytest.mark.asyncio
async def test_refused_turn_halts_without_switching_candidates() -> None:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=_turn_response("refused"))
    recorder = _recorder()
    executor = _executor(adapter, recorder)

    result = await executor.execute(
        _request(turn=_turn_request(_valid_tool_messages())),
        _snapshot(count=2),
        MagicMock(),
    )

    assert result.status == "failed"
    assert adapter.invoke.await_count == 1
    assert result.candidate_decisions[0].reason == "CONTENT_REFUSED"
    assert not any(decision.decision == "switched" for decision in result.candidate_decisions)


@pytest.mark.asyncio
async def test_on_delta_uses_streaming_adapter_through_same_executor() -> None:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(return_value=_turn_response("tool_calls"))
    adapter.invoke_stream = AsyncMock(return_value=_turn_response("tool_calls"))
    recorder = _recorder()
    executor = _executor(adapter, recorder)
    observed: list[Any] = []

    async def on_delta(delta: Any) -> None:
        observed.append(delta)

    result = await executor.execute(
        _request(turn=_turn_request(_valid_tool_messages())),
        _snapshot(),
        MagicMock(),
        on_delta=on_delta,
    )

    assert result.status == "succeeded"
    adapter.invoke_stream.assert_awaited_once()
    assert adapter.invoke_stream.await_args.kwargs["on_delta"] is on_delta
    assert adapter.invoke.await_count == 0
    recorder.try_reserve.assert_awaited_once()


@pytest.mark.asyncio
async def test_old_text_path_still_halts_on_content_refusal() -> None:
    adapter = MagicMock()
    adapter.invoke = AsyncMock(side_effect=AIContentRefusedError(Message("llm_err_content_policy")))
    recorder = _recorder()
    executor = _executor(adapter, recorder)

    result = await executor.execute(_request(), _snapshot(count=2), MagicMock())

    assert result.status == "failed"
    assert adapter.invoke.await_count == 1
    assert result.candidate_decisions[0].reason == "CONTENT_REFUSED"


def test_validator_contract_still_accepts_plain_response() -> None:
    """旧文本路径的 validate_output 契约不受 turn 字段影响。"""
    valid = ValidatedBusinessOutput(output={"label": "中性"}, metadata={})
    assert valid.output == {"label": "中性"}
