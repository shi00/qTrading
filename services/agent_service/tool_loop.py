"""Deferred 工具循环驱动（DD §10.3 / L1784；§SQ-03；DD-F15）。

合同：

- 首轮 ``agent.run(prompt)``；若输出为 :class:`DeferredToolRequests`，逐 call 交
  :class:`ToolExecutorPort`，再用 ``agent.run(message_history=..., deferred_tool_results=...)``
  续跑，**按原 tool_call_id 回填**，直到输出不再是 DeferredToolRequests；
- 派发后 ``UNKNOWN`` 显式回填 ``{"state": "unknown", "operation_id": ...}`` 且不自动
  重试（I15）；拒绝 ``denied`` 同样显式回填，叶可继续；
- 单一轮次上限（复用既有 ``max_tool_calls`` 默认 20），超限抛 ``BUDGET_BLOCKED``；
- ``asyncio.CancelledError`` 原样传播（R2）。

不在本模块：授权/claim/派发（由 :class:`ToolExecutorPort` 的生产实现负责）、
成果落库、摘要压缩。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Protocol

from pydantic_ai import Agent, AgentRunResult, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.usage import UsageLimits

from core.agent_types import AgentErrorCode, AgentServiceError
from services.agent_service.mcp_tools import ToolCallOutcome, ToolCallState

__all__ = [
    "MAX_TOOL_ROUNDS",
    "ToolExecutorPort",
    "ToolLoopPorts",
    "ToolLoopResult",
    "run_agent_tool_loop",
]

#: 工具轮次上限：与既有 ``LoopLimits.max_tool_calls`` 默认值一致，不新增产品数值。
MAX_TOOL_ROUNDS: Final[int] = 20

_LOOP_LABEL: Final[str] = "tool_loop"
_JSON_SEPARATORS: Final[tuple[str, str]] = (",", ":")


class ToolExecutorPort(Protocol):
    """一次工具调用的执行端口（生产实现负责决定/许可/派发/结果落库）。"""

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        tool_call_id: str,
    ) -> ToolCallOutcome: ...


@dataclass(frozen=True, slots=True)
class ToolLoopPorts:
    """工具循环依赖：执行端口 + 轮次上限。"""

    executor: ToolExecutorPort
    max_rounds: int = MAX_TOOL_ROUNDS


#: 逐回合模型设置提供者：每轮调用一次，返回该轮真实设置（必须含正数 ``timeout``）。
TurnModelSettings = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class ToolLoopResult:
    """循环终态：SDK 结果 + 后效未知 operation + 被拒绝工具名（均保序去重）。"""

    run_result: AgentRunResult[Any]
    unknown_operation_refs: tuple[str, ...]
    denied_tool_names: tuple[str, ...]


def _canonical_text(payload: Any) -> str:
    """规范 JSON 文本（键序稳定、无空白）；不可序列化时退回显式失败文本。"""

    try:
        return json.dumps(payload, ensure_ascii=False, separators=_JSON_SEPARATORS, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return json.dumps({"state": "failed", "error": "tool_result_not_serializable"}, ensure_ascii=False)


def _decode_arguments(part: ToolCallPart) -> dict[str, Any] | None:
    """模型参数 → dict；字符串参数按 JSON 解析，其余形状一律视为不可派发。"""

    args = part.args
    if isinstance(args, Mapping):
        return dict(args)
    if isinstance(args, str):
        try:
            decoded = json.loads(args)
        except (TypeError, ValueError):
            return None
        return dict(decoded) if isinstance(decoded, Mapping) else None
    return None


def _result_text(outcome: ToolCallOutcome) -> str:
    """把调用结果转成模型可读文本；未知/失败显式标注，不伪装成功。"""

    if outcome.state is ToolCallState.UNKNOWN:
        return _canonical_text({"state": "unknown", "operation_id": outcome.operation_id})
    if outcome.state is ToolCallState.FAILED:
        payload: dict[str, Any] = {"state": "failed", "operation_id": outcome.operation_id}
        if outcome.result:
            payload.update(outcome.result)
        return _canonical_text(payload)
    return _canonical_text(outcome.result if outcome.result is not None else {"state": "completed"})


def _denied(outcome: ToolCallOutcome) -> bool:
    return outcome.state is ToolCallState.FAILED and bool(outcome.result) and outcome.result.get("state") == "denied"


async def _execute_one(part: ToolCallPart, executor: ToolExecutorPort) -> ToolCallOutcome:
    """派发单个 deferred call；参数不可解析即确定失败（零派发）。"""

    arguments = _decode_arguments(part)
    if arguments is None:
        return ToolCallOutcome(
            ToolCallState.FAILED,
            {"state": "failed", "error": "invalid_arguments"},
            f"{part.tool_call_id}:invalid_arguments",
        )
    return await executor.execute(tool_name=part.tool_name, arguments=arguments, tool_call_id=part.tool_call_id)


async def run_agent_tool_loop(
    agent: Agent[Any, Any],
    prompt: str,
    *,
    deps: Any,
    ports: ToolLoopPorts,
    turn_model_settings: TurnModelSettings | None = None,
    event_stream_handler: Any = None,
    usage_limits: UsageLimits | None = None,
) -> ToolLoopResult:
    """首轮 + Deferred 续跑；原 ``tool_call_id`` 回填；``UNKNOWN`` 不重试。

    ``turn_model_settings`` 每轮调用一次：叶把同一冻结绝对期限换算出的剩余预算
    逐轮交出（桥层 ``timeout`` = 该轮剩余），SDK 运行期限取同一值；否则时段
    无界。``event_stream_handler`` 原样交每轮 ``run``（公开事件回调）。
    ``usage_limits`` 原样交每轮 ``run``（2.77.0 #14154：内部子运行请求计入同一
    预算，配额由装配方预留）。
    """

    def _run_kwargs() -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if event_stream_handler is not None:
            kwargs["event_stream_handler"] = event_stream_handler
        if usage_limits is not None:
            kwargs["usage_limits"] = usage_limits
        if turn_model_settings is not None:
            settings = dict(turn_model_settings())
            timeout = settings.get("timeout")
            if isinstance(timeout, bool) or not isinstance(timeout, int | float) or float(timeout) <= 0:
                raise ValueError("turn model settings must carry a positive 'timeout' (bridge deadline)")
            kwargs["model_settings"] = settings
            kwargs["timeout"] = float(timeout)
        return kwargs

    result = await agent.run(prompt, deps=deps, **_run_kwargs())
    rounds = 0
    unknown_refs: list[str] = []
    denied_names: list[str] = []
    while isinstance(result.output, DeferredToolRequests):
        rounds += 1
        if rounds > ports.max_rounds:
            raise AgentServiceError(AgentErrorCode.BUDGET_BLOCKED, "tool_round_limit", _LOOP_LABEL)
        calls: dict[str, Any] = {}
        for part in result.output.calls:
            outcome = await _execute_one(part, ports.executor)
            calls[part.tool_call_id] = _result_text(outcome)
            if outcome.state is ToolCallState.UNKNOWN and outcome.operation_id not in unknown_refs:
                unknown_refs.append(outcome.operation_id)
            if _denied(outcome) and part.tool_name not in denied_names:
                denied_names.append(part.tool_name)
        result = await agent.run(
            message_history=result.all_messages(),
            deferred_tool_results=DeferredToolResults(calls=calls),
            deps=deps,
            **_run_kwargs(),
        )
    return ToolLoopResult(
        run_result=result,
        unknown_operation_refs=tuple(unknown_refs),
        denied_tool_names=tuple(denied_names),
    )
