"""长上下文压缩：预算 / 配对卸载 / 摘要谱系 / 失败不静默 / 纠正保留。

验收映射：F25、AC15、J07、QAC03、NFR-P05。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from core.agent_types import is_digest
from core.model_management_types import ExecutionSnapshot
from services.agent_service.context_compaction import (
    COMPACTION_BOUNDARY_UNCUTTABLE,
    COMPACTION_BUDGET_INSUFFICIENT,
    COMPACTION_BUDGET_UNKNOWN,
    COMPACTION_DISABLED,
    COMPACTION_NOTHING_TO_SUMMARIZE,
    COMPACTION_SUMMARY_EMPTY,
    COMPACTION_SUMMARY_NO_MODEL,
    COMPACTION_SUMMARY_TIMEOUT,
    COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE,
    COMPACTION_SUMMARY_TRUNCATED,
    COMPACTION_WITHIN_BUDGET,
    CompactionBudget,
    CompactionConfig,
    CompactionOutcome,
    ContextCompaction,
    SummaryLineage,
    build_leaf_compaction,
    compact_messages,
    has_user_correction,
    inherit_summary_lineage,
    is_tool_pair_safe_cut,
    mark_user_correction,
    plan_compaction,
    resolve_compaction_budget,
)
from services.ai_service.token_budget import OUTPUT_RESERVE_TOKENS

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _snapshot(
    *,
    routes: tuple[Any, ...] | None = None,
    connections: tuple[Any, ...] | None = None,
) -> ExecutionSnapshot:
    return ExecutionSnapshot(
        snapshot_id="snap-1",
        run_id=None,
        config_revision=1,
        routes=routes
        if routes is not None
        else ({"scenario_id": "agent_research", "candidates": ({"connection_id": "conn-1"},)},),
        connections=connections if connections is not None else ({"connection_id": "conn-1", "context_window": 8192},),
        business_contract_versions={},
        security_scope_version=1,
        created_at="2026-11-20T00:00:00.000000Z",
    )


def _user(text: str) -> ModelRequest:
    return ModelRequest(parts=[UserPromptPart(content=text)])


def _assistant(text: str) -> ModelResponse:
    return ModelResponse(parts=[TextPart(content=text)])


def _call(call_id: str) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(tool_name="query_kline", args={"code": "000001"}, tool_call_id=call_id)])


def _result(call_id: str, content: str = "结果") -> ModelRequest:
    return ModelRequest(parts=[ToolReturnPart(tool_name="query_kline", content=content, tool_call_id=call_id)])


def _long_history(*, padding: int = 20_000) -> list[ModelMessage]:
    """超过 8192-4000 预算的历史（尾部 6 条原样保留，其余可摘要）。"""

    return [
        ModelRequest(parts=[SystemPromptPart(content="你是研究助手，保持引用可核。")]),
        _user("初始目标：给出有依据的结论"),
        _assistant("明白"),
        _user("x" * padding),
        _assistant("已读取长资料"),
        _user("继续推进"),
        _assistant("正在整理"),
        _user("补充约束：只看主板"),
        _assistant("收到"),
        _user("请给结论"),
    ]


def _render_text(messages: list[ModelMessage] | tuple[ModelMessage, ...]) -> str:
    chunks: list[str] = []
    for message in messages:
        for part in message.parts:
            if isinstance(part, SystemPromptPart | TextPart | TextContent):
                chunks.append(part.content)
            elif isinstance(part, UserPromptPart):
                if isinstance(part.content, str):
                    chunks.append(part.content)
                else:
                    chunks.extend(item.content for item in part.content if isinstance(item, TextContent))
    return "\n".join(chunks)


def _tool_pairs_intact(messages: list[ModelMessage]) -> bool:
    """每条工具调用与结果都在同一份历史里（配对未拆散、无孤立残片）。"""

    calls = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    }
    returns = {
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    }
    return bool(calls) and calls == returns


def _budget(window: int | None = 8192, *, others: tuple[int, ...] = ()) -> CompactionBudget:
    return CompactionBudget(
        context_window_tokens=window,
        candidate_context_windows=others,
        output_reserve_tokens=OUTPUT_RESERVE_TOKENS,
    )


# ---------------------------------------------------------------------------
# A. 窗口预算
# ---------------------------------------------------------------------------


class TestResolveCompactionBudget:
    def test_known_window_reserves_output_tokens(self) -> None:
        budget = resolve_compaction_budget(_snapshot(), scenario_id="agent_research")

        assert budget.context_window_tokens == 8192
        assert budget.available_input_tokens() == 8192 - OUTPUT_RESERVE_TOKENS

    def test_strictest_candidate_window_wins(self) -> None:
        snapshot = _snapshot(
            routes=(
                {
                    "scenario_id": "agent_research",
                    "candidates": ({"connection_id": "conn-1"}, {"connection_id": "conn-2"}),
                },
            ),
            connections=(
                {"connection_id": "conn-1", "context_window": 65_536},
                {"connection_id": "conn-2", "context_window": 8192},
            ),
        )

        budget = resolve_compaction_budget(snapshot, scenario_id="agent_research")

        assert budget.strictest_context_window_tokens() == 8192
        assert budget.available_input_tokens() == 8192 - OUTPUT_RESERVE_TOKENS

    def test_unknown_window_stays_unknown(self) -> None:
        budget = resolve_compaction_budget(_snapshot(connections=()), scenario_id="agent_research")

        assert budget.context_window_tokens is None
        assert budget.available_input_tokens() is None

    def test_window_below_output_reserve_is_insufficient(self) -> None:
        budget = _budget(1000)

        assert budget.available_input_tokens() == 0


# ---------------------------------------------------------------------------
# B. 工具配对边界
# ---------------------------------------------------------------------------


class TestToolPairingBoundary:
    def test_split_pair_boundary_is_unsafe(self) -> None:
        messages: list[ModelMessage] = [_user("开始"), _call("c1"), _result("c1"), _assistant("ok")]

        assert is_tool_pair_safe_cut(messages, 2) is False
        assert is_tool_pair_safe_cut(messages, 3) is True

    def test_plan_walks_back_to_a_safe_boundary(self) -> None:
        messages: list[ModelMessage] = [_user("a"), _user("b"), _call("c1"), _result("c1"), _user("c")]

        plan = plan_compaction(messages, keep_messages=2)

        assert plan.cutoff == 2
        assert is_tool_pair_safe_cut(messages, 3) is False

    def test_no_safe_boundary_is_uncuttable(self) -> None:
        messages: list[ModelMessage] = [_call("c1"), _result("c1")]

        plan = plan_compaction(messages, keep_messages=1)

        assert plan.cutoff is None
        assert plan.reason == COMPACTION_BOUNDARY_UNCUTTABLE

    def test_history_shorter_than_tail_has_nothing_to_summarize(self) -> None:
        plan = plan_compaction([_user("a"), _user("b")], keep_messages=2)

        assert plan.cutoff is None
        assert plan.reason == COMPACTION_NOTHING_TO_SUMMARIZE


# ---------------------------------------------------------------------------
# C. 摘要生成 / 谱系 / 失败不静默
# ---------------------------------------------------------------------------


class TestCompactMessages:
    async def test_disabled_config_keeps_history(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            config=CompactionConfig(enabled=False),
            timeout_seconds=5.0,
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_DISABLED
        assert outcome.messages == tuple(messages)

    async def test_unknown_budget_is_explicit(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(None),
            timeout_seconds=5.0,
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_BUDGET_UNKNOWN
        assert outcome.messages == tuple(messages)

    async def test_insufficient_budget_is_explicit(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(1000),
            timeout_seconds=5.0,
        )

        assert outcome.reason == COMPACTION_BUDGET_INSUFFICIENT
        assert outcome.messages == tuple(messages)

    async def test_within_budget_skips_summary(self) -> None:
        messages = _long_history(padding=10)
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_WITHIN_BUDGET

    async def test_missing_model_keeps_history(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(None, messages, budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_NO_MODEL
        assert outcome.messages == tuple(messages)
        assert outcome.lineage is None

    async def test_unknown_summary_deadline_is_not_guessed(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"), messages, budget=_budget(), timeout_seconds=None
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE
        assert outcome.messages == tuple(messages)

    async def test_empty_summary_is_not_accepted(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="   "), messages, budget=_budget(), timeout_seconds=5.0
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_EMPTY
        assert outcome.messages == tuple(messages)

    async def test_timeout_summary_keeps_history(self) -> None:
        async def _boom(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise TimeoutError

        messages = _long_history()
        outcome = await compact_messages(FunctionModel(_boom), messages, budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_TIMEOUT
        assert outcome.messages == tuple(messages)

    async def test_truncated_summary_is_not_accepted(self) -> None:
        def _clipped(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            return ModelResponse(parts=[TextPart(content="半截摘要")], finish_reason="length")

        messages = _long_history()
        outcome = await compact_messages(FunctionModel(_clipped), messages, budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_TRUNCATED
        assert outcome.messages == tuple(messages)

    async def test_success_records_complete_lineage(self) -> None:
        messages = _long_history()
        outcome = await compact_messages(
            TestModel(custom_output_text="关键决定已保留"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            now=lambda: datetime.fromisoformat("2026-11-24T08:00:00+00:00"),
        )

        assert outcome.compressed is True
        lineage = outcome.lineage
        assert lineage is not None
        assert lineage.source_start_index == 0
        assert lineage.source_end_index == plan_compaction(messages, keep_messages=6).cutoff
        assert len(lineage.source_message_ids) == lineage.source_end_index
        assert lineage.created_at == "2026-11-24T08:00:00+00:00"
        assert lineage.model_binding_ref == "test"
        assert is_digest(lineage.summary_digest)
        # 尾部原样保留，摘要替代前缀
        assert outcome.messages[-6:] == tuple(messages[-6:])
        assert "关键决定已保留" in _render_text(outcome.messages)
        assert len(outcome.messages) < len(messages)

    async def test_compacted_history_keeps_tool_pairs_intact(self) -> None:
        messages: list[ModelMessage] = [
            _user("x" * 20_000),
            _assistant("已读长资料"),
            _call("c1"),
            _result("c1"),
            _user("结论"),
        ]

        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=10_000,
            config=CompactionConfig(keep_messages=2),
        )

        assert outcome.compressed is True
        # 切点会拆散 c1（调用在前、结果在后）→ 回退到配对完整处，尾部保留整对
        assert plan_compaction(messages, keep_messages=2).cutoff == 2
        assert _tool_pairs_intact(list(outcome.messages)) is True
        assert any(
            isinstance(part, ToolReturnPart) and part.tool_call_id == "c1"
            for message in outcome.messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )


# ---------------------------------------------------------------------------
# D. 纠正保留与继承策略
# ---------------------------------------------------------------------------


class TestCorrectionAndInheritance:
    def test_marked_correction_is_pinned(self) -> None:
        part = mark_user_correction("纠正：不要用 ST 股")

        assert has_user_correction(ModelRequest(parts=[part])) is True
        assert has_user_correction(_user("普通消息")) is False

    def test_blank_correction_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            mark_user_correction("   ")

    async def test_long_session_correction_survives_compaction(self) -> None:
        correction = mark_user_correction("纠正：不要用 ST 股，也不要用北交所")
        messages: list[ModelMessage] = [
            ModelRequest(parts=[correction]),
            _assistant("记住了"),
            _user("x" * 20_000),
            _assistant("已读"),
            _user("继续"),
            _assistant("好"),
            _user("结论"),
        ]

        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=10_000,
            config=CompactionConfig(keep_messages=2),
        )

        assert outcome.compressed is True
        assert any(has_user_correction(message) for message in outcome.messages)
        assert "纠正：不要用 ST 股，也不要用北交所" in _render_text(outcome.messages)

    def test_child_inherits_lineage_refs_only(self) -> None:
        lineage = _lineage()

        inherited = inherit_summary_lineage([lineage])

        assert inherited == (lineage,)
        assert not hasattr(lineage, "summary")

    def test_child_body_inheritance_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="never inherit"):
            inherit_summary_lineage([_lineage()], inherit_body=True)


def _lineage() -> SummaryLineage:
    return SummaryLineage(
        source_start_index=0,
        source_end_index=2,
        source_message_ids=("request#0", "response#1"),
        created_at="2026-11-24T08:00:00+00:00",
        model_binding_ref="test",
        summary_digest="a" * 64,
    )


# ---------------------------------------------------------------------------
# E. 能力装配与请求前决策
# ---------------------------------------------------------------------------


class TestContextCompactionCapability:
    def test_disabled_config_does_not_install_capability(self) -> None:
        binding = _leaf_binding()

        assert (
            build_leaf_compaction(
                binding,
                scenario_id="agent_research",
                timeout_seconds=lambda: 5.0,
                config=CompactionConfig(enabled=False),
            )
            is None
        )

    def test_enabled_config_builds_capability_with_authorized_connection(self) -> None:
        binding = _leaf_binding()

        capability = build_leaf_compaction(binding, scenario_id="agent_research", timeout_seconds=lambda: 5.0)

        assert isinstance(capability, ContextCompaction)
        assert capability.budget.context_window_tokens == 8192

    async def test_request_compacts_history_before_model_call(self) -> None:
        seen: list[list[ModelMessage]] = []

        def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            seen.append(list(messages))
            if "<messages>" in _render_text(messages):
                return ModelResponse(parts=[TextPart(content="压缩摘要内容")])
            return ModelResponse(parts=[TextPart(content="最终答复")])

        agent: Agent[None, str] = Agent(
            FunctionModel(_fn),
            output_type=str,
            capabilities=(ContextCompaction(budget=_budget(), timeout_seconds=lambda: 5.0),),
        )

        result = await agent.run("继续", message_history=_long_history(), model_settings={"timeout": 5.0})

        assert result.output == "最终答复"
        assert len(seen) == 2  # 摘要回合 + 主回合
        assert "压缩摘要内容" in _render_text(seen[1])
        assert "x" * 100 not in _render_text(seen[1])  # 被摘要的长原文不再整段外发
        assert "请给结论" in _render_text(seen[1])  # 尾部消息原样保留

    async def test_failed_summary_is_reported_and_history_kept(self) -> None:
        outcomes: list[CompactionOutcome] = []

        async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            if "<messages>" in _render_text(messages):
                raise TimeoutError
            return ModelResponse(parts=[TextPart(content="最终答复")])

        agent: Agent[None, str] = Agent(
            FunctionModel(_fn),
            output_type=str,
            capabilities=(
                ContextCompaction(budget=_budget(), timeout_seconds=lambda: 5.0, on_outcome=outcomes.append),
            ),
        )

        result = await agent.run("继续", message_history=_long_history(), model_settings={"timeout": 5.0})

        assert result.output == "最终答复"
        assert outcomes[-1].compressed is False
        assert outcomes[-1].reason == COMPACTION_SUMMARY_TIMEOUT


class _LeafBinding:
    def __init__(self, snapshot: ExecutionSnapshot, binding: dict[str, Any]) -> None:
        self.snapshot = snapshot
        self.binding = binding


def _leaf_binding() -> _LeafBinding:
    return _LeafBinding(
        _snapshot(
            routes=(
                {
                    "scenario_id": "agent_research",
                    "candidates": ({"connection_id": "conn-1"}, {"connection_id": "conn-2"}),
                },
            ),
            connections=(
                {"connection_id": "conn-1", "context_window": 65_536},
                {"connection_id": "conn-2", "context_window": 8192},
            ),
        ),
        {"data_consent": {"connection_id": "conn-2"}},
    )
