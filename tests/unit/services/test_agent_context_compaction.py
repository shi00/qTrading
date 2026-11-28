"""长上下文压缩：预算 / 配对卸载 / 摘要谱系 / 失败不静默 / 纠正保留。

验收映射：F25、AC15、J07、QAC03、NFR-P05。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai import Agent
from pydantic_ai.messages import (
    BinaryContent,
    ImageUrl,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextContent,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

import services.agent_service.context_compaction as compaction
from core.agent_types import AgentErrorCode, AgentServiceError, Mode, is_digest
from core.model_management_types import ExecutionSnapshot, ModelTurnProtocolError
from services.agent_service.capabilities import ResearchBoundary, ResearchBoundaryPolicy
from services.agent_service.context_compaction import (
    COMPACTION_BOUNDARY_UNCUTTABLE,
    COMPACTION_BUDGET_INSUFFICIENT,
    COMPACTION_BUDGET_UNKNOWN,
    COMPACTION_DISABLED,
    COMPACTION_NOTHING_TO_SUMMARIZE,
    COMPACTION_SUMMARY_EMPTY,
    COMPACTION_SUMMARY_FAILED,
    COMPACTION_SUMMARY_MODEL_UNBOUND,
    COMPACTION_SUMMARY_NO_MODEL,
    COMPACTION_SUMMARY_SEGMENT_OVER_WINDOW,
    COMPACTION_SUMMARY_TIMEOUT,
    COMPACTION_SUMMARY_TIMEOUT_UNAVAILABLE,
    COMPACTION_SUMMARY_TRUNCATED,
    COMPACTION_WITHIN_BUDGET,
    CompactionBudget,
    CompactionConfig,
    CompactionOutcome,
    CompactionPlan,
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
    summarize_segment_within_window,
)
from services.agent_service.leaf_agent import LeafRequestGuard
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


def _varied_history(chars: int = 24_000) -> list[ModelMessage]:
    """含真实可分词长文本的历史（重复单字符会被 tokenizer 高度压缩，不能用于超窗断言）。"""

    unit = "个股研究记录：营收同比、毛利率、经营现金流与行业景气度变化。"
    text = (unit * (chars // len(unit) + 1))[:chars]
    return [
        ModelRequest(parts=[SystemPromptPart(content="你是研究助手，保持引用可核。")]),
        _user("初始目标：给出有依据的结论"),
        _assistant("明白"),
        _user(text),
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


def _two_candidate_snapshot() -> ExecutionSnapshot:
    return _snapshot(
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


def _leaf_binding() -> _LeafBinding:
    return _LeafBinding(_two_candidate_snapshot(), {"data_consent": {"connection_id": "conn-2"}})


# ---------------------------------------------------------------------------
# F. 构造期不变量（配置 / 预算 / 计划 / 谱系 / 结果）
# ---------------------------------------------------------------------------


class TestConfigurationInvariants:
    def test_config_rejects_non_bool_enabled(self) -> None:
        with pytest.raises(ValueError, match="enabled must be bool"):
            CompactionConfig(enabled=cast(Any, 1))

    def test_config_rejects_non_positive_keep_messages(self) -> None:
        with pytest.raises(ValueError, match="keep_messages must be an int >= 1"):
            CompactionConfig(keep_messages=0)

    def test_budget_rejects_non_positive_window(self) -> None:
        with pytest.raises(ValueError, match="context_window_tokens must be a positive int or None"):
            CompactionBudget(context_window_tokens=0)

    def test_budget_rejects_non_int_reserve(self) -> None:
        with pytest.raises(ValueError, match="output_reserve_tokens must be an int"):
            CompactionBudget(context_window_tokens=8192, output_reserve_tokens=cast(Any, "1024"))

    def test_budget_rejects_negative_reserve(self) -> None:
        with pytest.raises(ValueError, match="output_reserve_tokens must be non-negative"):
            CompactionBudget(context_window_tokens=8192, output_reserve_tokens=-1)

    def test_budget_rejects_non_positive_candidate_window(self) -> None:
        with pytest.raises(ValueError, match="candidate_context_windows entries must be positive ints"):
            CompactionBudget(context_window_tokens=8192, candidate_context_windows=(0,))

    def test_plan_requires_reason_when_uncuttable(self) -> None:
        with pytest.raises(ValueError, match="requires a reason"):
            CompactionPlan(cutoff=None)

    def test_plan_rejects_non_positive_cutoff(self) -> None:
        with pytest.raises(ValueError, match="cutoff must be a positive int or None"):
            CompactionPlan(cutoff=0)

    def test_plan_rejects_invalid_keep_messages(self) -> None:
        with pytest.raises(ValueError, match="keep_messages must be an int >= 1"):
            plan_compaction([_user("a")], keep_messages=0)


class TestLineageInvariants:
    @staticmethod
    def _kwargs(**overrides: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "source_start_index": 0,
            "source_end_index": 2,
            "source_message_ids": ("request#0", "response#1"),
            "created_at": "2026-11-24T08:00:00+00:00",
            "model_binding_ref": "test",
            "summary_digest": "a" * 64,
        }
        base.update(overrides)
        return base

    def test_start_index_must_be_int(self) -> None:
        with pytest.raises(ValueError, match="source_start_index must be an int"):
            SummaryLineage(**self._kwargs(source_start_index=cast(Any, "0")))

    def test_start_index_must_be_non_negative(self) -> None:
        with pytest.raises(ValueError, match="source_start_index must be non-negative"):
            SummaryLineage(**self._kwargs(source_start_index=-1))

    def test_end_index_must_be_int(self) -> None:
        with pytest.raises(ValueError, match="source_end_index must be an int"):
            SummaryLineage(**self._kwargs(source_end_index=cast(Any, "2")))

    def test_end_index_must_exceed_start(self) -> None:
        with pytest.raises(ValueError, match="source_end_index must be greater than source_start_index"):
            SummaryLineage(**self._kwargs(source_end_index=0))

    def test_message_ids_must_cover_span(self) -> None:
        with pytest.raises(ValueError, match="source_message_ids must cover the declared span exactly"):
            SummaryLineage(**self._kwargs(source_message_ids=("request#0",)))

    def test_message_ids_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="source_message_ids entries must be non-empty text"):
            SummaryLineage(**self._kwargs(source_message_ids=("", "response#1")))

    def test_created_at_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="created_at must be non-empty ISO 8601 text"):
            SummaryLineage(**self._kwargs(created_at=""))

    def test_created_at_must_parse_as_iso(self) -> None:
        with pytest.raises(ValueError, match="created_at must be ISO 8601 text"):
            SummaryLineage(**self._kwargs(created_at="yesterday"))

    def test_model_binding_ref_must_be_non_empty(self) -> None:
        with pytest.raises(ValueError, match="model_binding_ref must be non-empty text"):
            SummaryLineage(**self._kwargs(model_binding_ref=""))

    def test_summary_digest_must_be_sha256(self) -> None:
        with pytest.raises(ValueError, match="summary_digest must be a lowercase sha256 digest"):
            SummaryLineage(**self._kwargs(summary_digest="A" * 64))


class TestOutcomeInvariants:
    def test_compressed_flag_must_be_bool(self) -> None:
        with pytest.raises(ValueError, match="compressed must be bool"):
            CompactionOutcome(messages=(), compressed=cast(Any, 1))

    def test_compressed_requires_lineage(self) -> None:
        with pytest.raises(ValueError, match="a compressed outcome requires lineage"):
            CompactionOutcome(messages=(), compressed=True)

    def test_compressed_rejects_reason(self) -> None:
        with pytest.raises(ValueError, match="a compressed outcome must not carry a failure reason"):
            CompactionOutcome(messages=(), compressed=True, lineage=_lineage(), reason="x")

    def test_uncompressed_requires_reason(self) -> None:
        with pytest.raises(ValueError, match="an uncompressed outcome requires a reason code"):
            CompactionOutcome(messages=(), compressed=False)

    def test_uncompressed_rejects_lineage(self) -> None:
        with pytest.raises(ValueError, match="an uncompressed outcome must not carry lineage"):
            CompactionOutcome(messages=(), compressed=False, reason="x", lineage=_lineage())


# ---------------------------------------------------------------------------
# G. 配对守卫与摘要回合失败分支
# ---------------------------------------------------------------------------


class TestToolPairingGuards:
    def test_edge_cutoffs_are_safe(self) -> None:
        messages: list[ModelMessage] = [_user("a"), _call("c1"), _result("c1")]

        assert is_tool_pair_safe_cut(messages, 0) is True
        assert is_tool_pair_safe_cut(messages, len(messages)) is True

    def test_unknown_message_kind_is_ignored(self) -> None:
        messages = cast(list[ModelMessage], [SimpleNamespace(), _call("c1"), _result("c1")])

        assert is_tool_pair_safe_cut(messages, 1) is True

    def test_tool_return_without_call_id_is_ignored(self) -> None:
        messages: list[ModelMessage] = [
            _user("a"),
            ModelRequest(parts=[ToolReturnPart(tool_name="query_kline", content="r", tool_call_id="")]),
            _user("b"),
        ]

        assert is_tool_pair_safe_cut(messages, 2) is True

    def test_orphan_tool_return_is_ignored(self) -> None:
        messages: list[ModelMessage] = [_user("a"), _result("missing"), _user("b")]

        assert is_tool_pair_safe_cut(messages, 2) is True

    def test_orphan_tool_return_in_kept_tail_is_unsafe(self) -> None:
        """保留区间内的孤立 ToolReturnPart 外发时会被供应商拒绝（D7-02）。"""

        messages: list[ModelMessage] = [_user("a"), _user("b"), _result("missing")]

        assert is_tool_pair_safe_cut(messages, 2) is False

    def test_pending_tool_call_in_kept_tail_is_unsafe(self) -> None:
        """保留区间内未闭合的 ToolCallPart 永远等不到结果（D7-02）。"""

        messages: list[ModelMessage] = [_user("a"), _user("b"), _call("c1")]

        assert is_tool_pair_safe_cut(messages, 2) is False

    def test_retry_prompt_split_from_its_call_is_unsafe(self) -> None:
        retry = ModelRequest(parts=[RetryPromptPart(content="请改用代码", tool_call_id="c1")])
        messages: list[ModelMessage] = [_user("a"), _call("c1"), retry, _user("b")]

        assert is_tool_pair_safe_cut(messages, 2) is False
        assert is_tool_pair_safe_cut(messages, 3) is True

    def test_retry_feedback_without_id_split_from_call_is_unsafe(self) -> None:
        retry = ModelRequest(parts=[RetryPromptPart(content="参数不合法")])
        messages: list[ModelMessage] = [_user("a"), _call("c1"), retry, _user("b")]

        assert is_tool_pair_safe_cut(messages, 2) is False
        assert is_tool_pair_safe_cut(messages, 3) is True

    def test_matched_pair_wholly_kept_is_safe(self) -> None:
        messages: list[ModelMessage] = [_user("a"), _call("c1"), _result("c1"), _user("b")]

        assert is_tool_pair_safe_cut(messages, 1) is True


class TestSummaryTurnFailures:
    async def test_cancellation_propagates(self) -> None:
        async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError) as exc_info:
            await compact_messages(FunctionModel(_fn), _long_history(), budget=_budget(), timeout_seconds=5.0)

        assert type(exc_info.value) is asyncio.CancelledError

    async def test_protocol_error_becomes_summary_failed(self) -> None:
        async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise ModelTurnProtocolError("invalid_part", "parts[0]")

        outcome = await compact_messages(FunctionModel(_fn), _long_history(), budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_FAILED

    async def test_service_error_becomes_summary_failed(self) -> None:
        async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise AgentServiceError(AgentErrorCode.POLICY_DENIED, "denied", "compaction")

        outcome = await compact_messages(FunctionModel(_fn), _long_history(), budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_FAILED

    async def test_unexpected_error_becomes_summary_failed(self) -> None:
        async def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            raise RuntimeError("boom")

        messages = _long_history()
        outcome = await compact_messages(FunctionModel(_fn), messages, budget=_budget(), timeout_seconds=5.0)

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_FAILED
        assert outcome.messages == tuple(messages)


# ---------------------------------------------------------------------------
# H. 边界输入 / 增量锚点 / 内容渲染 / 绑定回退 / 授权连接
# ---------------------------------------------------------------------------


class TestCompactMessagesEdgeCases:
    async def test_invalid_estimate_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="estimated_input_tokens must be a non-negative int or None"):
            await compact_messages(
                TestModel(custom_output_text="摘要"),
                _long_history(),
                budget=_budget(),
                timeout_seconds=5.0,
                estimated_input_tokens=-1,
            )

    async def test_uncuttable_boundary_keeps_history(self) -> None:
        messages: list[ModelMessage] = [_call("c1"), _result("c1")]

        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=10_000,
            config=CompactionConfig(keep_messages=1),
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_BOUNDARY_UNCUTTABLE
        assert outcome.messages == tuple(messages)

    async def test_unbound_model_binding_keeps_history(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(compaction, "_model_binding_ref", lambda model, response: None)
        messages = _long_history()

        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"), messages, budget=_budget(), timeout_seconds=5.0
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_MODEL_UNBOUND
        assert outcome.messages == tuple(messages)

    def test_model_binding_falls_back_to_instance_name(self) -> None:
        response = ModelResponse(parts=[TextPart(content="x")], model_name=None)

        assert compaction._model_binding_ref(TestModel(), response) == "test"

    async def test_second_compaction_uses_previous_summary_anchor(self) -> None:
        prompts: list[str] = []

        def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            prompts.append(_render_text(messages))
            return ModelResponse(parts=[TextPart(content="增量摘要")])

        first = await compact_messages(FunctionModel(_fn), _long_history(), budget=_budget(), timeout_seconds=5.0)
        assert first.compressed is True

        second = await compact_messages(
            FunctionModel(_fn),
            first.messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=10_000,
        )

        assert second.compressed is True
        assert "<previous-summary>" in prompts[-1]
        assert "增量摘要" in prompts[-1]

    async def test_summary_prompt_renders_tool_and_retry_parts(self) -> None:
        prompts: list[str] = []

        def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            prompts.append(_render_text(messages))
            return ModelResponse(parts=[TextPart(content="摘要")])

        messages: list[ModelMessage] = [
            _user("x" * 20_000),
            _call("c1"),
            _result("c1", "K线结果"),
            ModelRequest(parts=[RetryPromptPart(content="请改用代码", tool_name="query_kline", tool_call_id="c1")]),
            _user("继续"),
            _assistant("好"),
            _user("结论"),
        ]

        outcome = await compact_messages(
            FunctionModel(_fn),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=10_000,
            config=CompactionConfig(keep_messages=2),
        )

        assert outcome.compressed is True
        prompt = next(item for item in prompts if "<messages>" in item)
        assert "[tool_call#1:query_kline]" in prompt
        assert "[tool_return#2:query_kline]" in prompt
        assert "[retry_feedback#3]" in prompt

    def test_non_text_content_is_marked_not_dropped(self) -> None:
        content = [
            TextContent(content="说明"),
            BinaryContent(data=b"\x00", media_type="application/octet-stream"),
            ImageUrl(url="https://example.com/x.png"),
        ]

        assert compaction._text_of_content(content) == "说明\n[非文本内容]\n[未知内容]"
        assert compaction._text_of_content(TextContent(content="纯文本")) == "纯文本"
        assert compaction._text_of_content(cast(Any, 42)) == "[非文本内容]"


class TestLeafCompactionConsent:
    def test_non_mapping_consent_uses_first_candidate_window(self) -> None:
        binding = _LeafBinding(_two_candidate_snapshot(), {"data_consent": "none"})

        capability = build_leaf_compaction(binding, scenario_id="agent_research", timeout_seconds=lambda: 5.0)

        assert isinstance(capability, ContextCompaction)
        assert capability.budget.context_window_tokens == 65_536

    def test_non_text_connection_id_uses_first_candidate_window(self) -> None:
        binding = _LeafBinding(_two_candidate_snapshot(), {"data_consent": {"connection_id": 123}})

        capability = build_leaf_compaction(binding, scenario_id="agent_research", timeout_seconds=lambda: 5.0)

        assert isinstance(capability, ContextCompaction)
        assert capability.budget.context_window_tokens == 65_536


class TestCapabilityBudgetUnknown:
    async def test_unknown_budget_leaves_request_untouched(self) -> None:
        outcomes: list[CompactionOutcome] = []
        calls: list[list[ModelMessage]] = []

        def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            calls.append(list(messages))
            return ModelResponse(parts=[TextPart(content="最终答复")])

        agent: Agent[None, str] = Agent(
            FunctionModel(_fn),
            output_type=str,
            capabilities=(
                ContextCompaction(budget=_budget(None), timeout_seconds=lambda: 5.0, on_outcome=outcomes.append),
            ),
        )

        result = await agent.run("继续", message_history=_long_history(), model_settings={"timeout": 5.0})

        assert result.output == "最终答复"
        assert len(calls) == 1  # 预算未知不发起摘要回合
        assert outcomes[-1].compressed is False
        assert outcomes[-1].reason == COMPACTION_BUDGET_UNKNOWN
        assert "x" * 100 in _render_text(calls[0])  # 原文未被改写


# ---------------------------------------------------------------------------
# I. 待摘要片段窗口（摘要模型自身窗口上限）
# ---------------------------------------------------------------------------


class TestSummarySegmentWindow:
    def test_segment_within_window_is_accepted(self) -> None:
        messages = _long_history()
        plan = plan_compaction(messages, keep_messages=6)
        assert plan.reason is None
        assert plan.cutoff == 4  # 历史共 10 条、保留尾部 6 条，摘要区间恰为前 4 条

        assert summarize_segment_within_window(messages, 4, _budget()) is True

    def test_segment_over_window_is_rejected(self) -> None:
        messages = _long_history(padding=40_000)
        plan = plan_compaction(messages, keep_messages=6)
        assert plan.reason is None
        assert plan.cutoff == 4  # 同一历史结构，边界不随填充长度变化

        assert summarize_segment_within_window(messages, 4, _budget()) is False

    def test_unknown_window_segment_is_not_claimed_safe(self) -> None:
        messages = _long_history()

        assert summarize_segment_within_window(messages, 4, _budget(None)) is False

    async def test_compact_messages_refuses_oversized_summary_request(self) -> None:
        """片段超摘要窗口时不发出必然超窗的摘要回合，显式保留原文（D7-02）。"""

        messages = _long_history(padding=40_000)
        outcome = await compact_messages(
            TestModel(custom_output_text="摘要"),
            messages,
            budget=_budget(),
            timeout_seconds=5.0,
            estimated_input_tokens=50_000,
        )

        assert outcome.compressed is False
        assert outcome.reason == COMPACTION_SUMMARY_SEGMENT_OVER_WINDOW
        assert outcome.messages == tuple(messages)


# ---------------------------------------------------------------------------
# J. 叶请求三段式门（准入 → 压缩 → 压缩后窗口复核，D7-02）
# ---------------------------------------------------------------------------


def _boundary(
    *,
    window: int = 8192,
    active: frozenset[tuple[str, str]] = frozenset({("src-1", "1")}),
    disabled: frozenset[tuple[str, str]] = frozenset(),
) -> ResearchBoundary:
    async def _policy(_ctx: Any) -> ResearchBoundaryPolicy:
        return ResearchBoundaryPolicy(
            mode=Mode.EXECUTE,
            allowed_tool_names=frozenset(),
            active_source_keys=active,
            disabled_source_keys=disabled,
            context_window_tokens=window,
            estimated_input_tokens=None,
            output_reserve_tokens=OUTPUT_RESERVE_TOKENS,
            tokenizer_model=None,
        )

    return ResearchBoundary(policy_provider=_policy)


def _rendering_model(calls: list[list[ModelMessage]], *, summary: str = "压缩摘要内容") -> FunctionModel:
    def _fn(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        calls.append(list(messages))
        if "<messages>" in _render_text(messages):
            return ModelResponse(parts=[TextPart(content=summary)])
        return ModelResponse(parts=[TextPart(content="最终答复")])

    return FunctionModel(_fn)


class TestLeafRequestPipeline:
    @pytest.fixture(autouse=True)
    def _deterministic_token_estimator(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """会话级 conftest 把 litellm 换成 MagicMock（token_counter 恒返回 1）。

        强制走字符启发式（CJK 1:1），使窗口判定按真实体量发生，而不是被 mock 数值
        掩盖（不 mock 被测代码本身）。
        """

        from services.ai_service import token_budget

        monkeypatch.setattr(token_budget, "_litellm_token_counter_error", True)

    async def test_over_window_history_is_compacted_before_window_gate(self) -> None:
        """超窗请求应由压缩挽救，而不是被边界先行终止（D7-02 顺序回归）。"""

        calls: list[list[ModelMessage]] = []
        guard = LeafRequestGuard(
            boundary=_boundary(),
            compaction=ContextCompaction(budget=_budget(), timeout_seconds=lambda: 5.0),
        )
        agent: Agent[None, str] = Agent(_rendering_model(calls), output_type=str, capabilities=(guard,))

        # harness 0.77.0 起 estimate_context_tokens 按真实分词（CJK 1:1）计量：
        # 待摘要片段须落在摘要窗口（8192）内，故用 6000 字符级历史（总量仍超
        # 可用输入预算 4192）保持「压缩挽救而非边界先行终止」的回归意图。
        result = await agent.run("继续", message_history=_varied_history(chars=6_000), model_settings={"timeout": 5.0})
        assert result.output == "最终答复"
        assert len(calls) == 2  # 摘要回合 + 主回合（边界未提前抛错）
        assert "压缩摘要内容" in _render_text(calls[1])
        assert "个股研究记录" * 100 not in _render_text(calls[1])
        assert "请给结论" in _render_text(calls[1])

    async def test_source_disabled_is_denied_before_any_summary_request(self) -> None:
        calls: list[list[ModelMessage]] = []
        key = ("src-1", "1")
        guard = LeafRequestGuard(
            boundary=_boundary(active=frozenset({key}), disabled=frozenset({key})),
            compaction=ContextCompaction(budget=_budget(), timeout_seconds=lambda: 5.0),
        )
        agent: Agent[None, str] = Agent(_rendering_model(calls), output_type=str, capabilities=(guard,))

        with pytest.raises(AgentServiceError) as exc_info:
            await agent.run("继续", message_history=_varied_history(), model_settings={"timeout": 5.0})

        assert exc_info.value.code is AgentErrorCode.POLICY_DENIED
        assert calls == []  # 零外发（摘要也不发）

    async def test_without_compaction_over_window_is_denied_by_final_gate(self) -> None:
        calls: list[list[ModelMessage]] = []
        guard = LeafRequestGuard(boundary=_boundary(), compaction=None)
        agent: Agent[None, str] = Agent(_rendering_model(calls), output_type=str, capabilities=(guard,))

        with pytest.raises(AgentServiceError) as exc_info:
            await agent.run("继续", message_history=_varied_history(), model_settings={"timeout": 5.0})

        assert exc_info.value.code is AgentErrorCode.POLICY_DENIED
        assert "context window exceeded" in exc_info.value.sanitized_message
        assert calls == []

    async def test_summary_segment_over_window_is_denied_without_outbound(self) -> None:
        calls: list[list[ModelMessage]] = []
        guard = LeafRequestGuard(
            boundary=_boundary(window=8192),
            compaction=ContextCompaction(budget=_budget(), timeout_seconds=lambda: 5.0),
        )
        agent: Agent[None, str] = Agent(_rendering_model(calls), output_type=str, capabilities=(guard,))
        messages = _varied_history(chars=40_000)

        with pytest.raises(AgentServiceError) as exc_info:
            await agent.run("继续", message_history=messages, model_settings={"timeout": 5.0})

        assert exc_info.value.code is AgentErrorCode.POLICY_DENIED
        assert "summary segment" in exc_info.value.sanitized_message
        assert calls == []
