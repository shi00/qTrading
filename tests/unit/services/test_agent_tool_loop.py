"""Deferred 工具循环驱动（DD §10.3；§SQ-03；DD-F15）。

- 首轮 Deferred → 派发 → 原 ``tool_call_id`` 回填 → 续跑至文本；
- UNKNOWN 显式标记且不重试；拒绝记录工具名；
- 轮次上限抛 ``BUDGET_BLOCKED``；``CancelledError`` 原样传播。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic_ai import DeferredToolRequests
from pydantic_ai.messages import ToolCallPart

from core.agent_types import AgentErrorCode, AgentServiceError
from services.agent_service.mcp_tools import ToolCallOutcome, ToolCallState
from services.agent_service.tool_loop import ToolLoopPorts, run_agent_tool_loop

pytestmark = pytest.mark.unit


class _Result:
    def __init__(self, output: Any, messages: list[Any] | None = None) -> None:
        self.output = output
        self._messages = messages or []

    def all_messages(self) -> list[Any]:
        return self._messages


class _Agent:
    def __init__(self, outputs: list[_Result]) -> None:
        self.outputs = list(outputs)
        self.calls: list[dict[str, Any]] = []

    async def run(
        self,
        prompt: str | None = None,
        *,
        deps: Any = None,
        message_history: list[Any] | None = None,
        deferred_tool_results: Any = None,
    ) -> _Result:
        self.calls.append(
            {
                "prompt": prompt,
                "deps": deps,
                "message_history": message_history,
                "deferred_tool_results": deferred_tool_results,
            }
        )
        return self.outputs.pop(0)


class _Executor:
    def __init__(self, outcome: ToolCallOutcome | BaseException) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []

    async def execute(self, *, tool_name: str, arguments: dict[str, Any], tool_call_id: str) -> ToolCallOutcome:
        self.calls.append({"tool_name": tool_name, "arguments": arguments, "tool_call_id": tool_call_id})
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def _deferred(tool_call_id: str = "call-1", args: Any = None) -> DeferredToolRequests:
    return DeferredToolRequests(
        calls=[
            ToolCallPart(tool_name="search", args={"query": "x"} if args is None else args, tool_call_id=tool_call_id)
        ]
    )


class TestRunAgentToolLoop:
    async def test_defers_then_resumes_with_original_tool_call_id(self) -> None:
        agent = _Agent([_Result(_deferred()), _Result("done")])
        executor = _Executor(ToolCallOutcome(ToolCallState.COMPLETED, {"answer": 1}, "op-1"))

        result = await run_agent_tool_loop(agent, "prompt", deps="deps", ports=ToolLoopPorts(executor=executor))

        assert result.run_result.output == "done"
        assert executor.calls == [{"tool_name": "search", "arguments": {"query": "x"}, "tool_call_id": "call-1"}]
        deferred = agent.calls[1]["deferred_tool_results"]
        assert set(deferred.calls) == {"call-1"}
        assert agent.calls[1]["message_history"] == []
        assert result.unknown_operation_refs == ()
        assert result.denied_tool_names == ()

    async def test_unknown_is_reported_and_not_retried(self) -> None:
        agent = _Agent([_Result(_deferred()), _Result("done")])
        executor = _Executor(ToolCallOutcome(ToolCallState.UNKNOWN, None, "op-unknown"))

        result = await run_agent_tool_loop(agent, "prompt", deps=None, ports=ToolLoopPorts(executor=executor))

        assert len(executor.calls) == 1
        assert result.unknown_operation_refs == ("op-unknown",)

    async def test_denied_is_reported(self) -> None:
        agent = _Agent([_Result(_deferred()), _Result("done")])
        executor = _Executor(ToolCallOutcome(ToolCallState.FAILED, {"state": "denied"}, "op-1"))

        result = await run_agent_tool_loop(agent, "prompt", deps=None, ports=ToolLoopPorts(executor=executor))

        assert result.denied_tool_names == ("search",)

    async def test_round_limit_raises_budget_blocked(self) -> None:
        agent = _Agent([_Result(_deferred()), _Result(_deferred("call-2"))])
        executor = _Executor(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))

        with pytest.raises(AgentServiceError) as excinfo:
            await run_agent_tool_loop(agent, "prompt", deps=None, ports=ToolLoopPorts(executor=executor, max_rounds=1))

        assert excinfo.value.code is AgentErrorCode.BUDGET_BLOCKED

    async def test_unparsable_arguments_are_not_dispatched(self) -> None:
        agent = _Agent([_Result(_deferred(args="not-json")), _Result("done")])
        executor = _Executor(ToolCallOutcome(ToolCallState.COMPLETED, {}, "op-1"))

        result = await run_agent_tool_loop(agent, "prompt", deps=None, ports=ToolLoopPorts(executor=executor))

        assert executor.calls == []
        assert result.run_result.output == "done"

    async def test_cancellation_propagates(self) -> None:
        agent = _Agent([_Result(_deferred())])
        executor = _Executor(asyncio.CancelledError())

        with pytest.raises(asyncio.CancelledError):
            await run_agent_tool_loop(agent, "prompt", deps=None, ports=ToolLoopPorts(executor=executor))
