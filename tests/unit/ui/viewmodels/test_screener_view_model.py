"""ScreenerViewModel stream card 管理单元测试 (P1-3)。

测试 VM stream card 生命周期方法 (state-driven, 不依赖 Flet 渲染)。
"""

from unittest.mock import patch

import pytest

from ui.viewmodels.screener_view_model import (
    ScreenerViewModel,
    StreamCard,
    _MAX_LOG_CARDS,
)

pytestmark = pytest.mark.unit


# --- Fixtures ---


@pytest.fixture
def vm():
    """ScreenerViewModel with mocked dependencies."""
    with (
        patch("ui.viewmodels.screener_view_model.DataProcessor"),
        patch("ui.viewmodels.screener_view_model.StrategyManager"),
        patch("ui.viewmodels.screener_view_model.ReviewManager"),
    ):
        return ScreenerViewModel()


# --- StreamCard dataclass ---


class TestStreamCard:
    """StreamCard frozen dataclass 不可变性。"""

    def test_default_values(self):
        card = StreamCard(name="test")
        assert card.name == "test"
        assert card.reasoning == ""
        assert card.content == ""
        assert card.is_analyzing is False

    def test_frozen(self):
        card = StreamCard(name="test")
        with pytest.raises(AttributeError):
            card.name = "other"  # type: ignore[misc]


# --- start_stream_card ---


class TestStartStreamCard:
    """start_stream_card 创建卡片 + buffer。"""

    def test_creates_streaming_card(self, vm):
        vm.start_stream_card("贵州茅台", is_analyzing=False)
        assert len(vm.state.stream_cards) == 1
        card = vm.state.stream_cards[0]
        assert card.name == "贵州茅台"
        assert card.is_analyzing is False
        assert "贵州茅台" in vm._stream_buffers

    def test_creates_analyzing_card(self, vm):
        vm.start_stream_card("贵州茅台", is_analyzing=True)
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is True

    def test_max_cards_truncation(self, vm):
        for i in range(_MAX_LOG_CARDS + 5):
            vm.start_stream_card(f"stock_{i}")
        assert len(vm.state.stream_cards) == _MAX_LOG_CARDS
        # 保留最后 _MAX_LOG_CARDS 张
        assert vm.state.stream_cards[0].name == "stock_5"
        assert vm.state.stream_cards[-1].name == f"stock_{_MAX_LOG_CARDS + 4}"


# --- append_stream_chunk + throttle ---


class TestAppendStreamChunk:
    """append_stream_chunk 累积 chunk + 节流 flush。"""

    def test_first_chunk_flushes_immediately(self, vm):
        vm.start_stream_card("test")
        vm.append_stream_chunk("test", "hello", is_reasoning=False)
        card = vm.state.stream_cards[0]
        assert card.content == "hello"

    def test_reasoning_chunk_accumulates(self, vm):
        vm.start_stream_card("test")
        vm.append_stream_chunk("test", "thinking...", is_reasoning=True)
        card = vm.state.stream_cards[0]
        assert card.reasoning == "thinking..."

    def test_throttled_chunks_pending(self, vm):
        """快速连续 chunk: 第一个 flush, 后续 pending。"""
        vm.start_stream_card("test")
        vm.append_stream_chunk("test", "a", is_reasoning=False)
        # 立即发送第二个 chunk (within throttle interval)
        vm.append_stream_chunk("test", "b", is_reasoning=False)
        # buffer 中已累积 "ab", 但 state 可能只 flush 了 "a"
        buf = vm._stream_buffers["test"]
        assert buf["content"] == "ab"
        assert buf["pending"] is True

    def test_nonexistent_card_ignored(self, vm):
        """不存在的卡片名忽略 chunk。"""
        vm.append_stream_chunk("nonexistent", "data", is_reasoning=False)
        assert len(vm.state.stream_cards) == 0


# --- finalize_stream_card ---


class TestFinalizeStreamCard:
    """finalize_stream_card 强制 flush pending。"""

    def test_flushes_pending(self, vm):
        vm.start_stream_card("test")
        vm.append_stream_chunk("test", "a", is_reasoning=False)
        vm.append_stream_chunk("test", "b", is_reasoning=False)  # pending
        vm.finalize_stream_card("test")
        card = vm.state.stream_cards[0]
        assert card.content == "ab"
        buf = vm._stream_buffers["test"]
        assert buf["pending"] is False

    def test_no_pending_noop(self, vm):
        vm.start_stream_card("test")
        # 无 chunk, 无 pending, finalize 不报错
        vm.finalize_stream_card("test")

    def test_finalize_clears_is_analyzing(self, vm):
        """finalize 后卡片 is_analyzing=False (流式内容已就绪)。"""
        vm.start_stream_card("test", is_analyzing=True)
        assert vm.state.stream_cards[0].is_analyzing is True
        vm.append_stream_chunk("test", "data", is_reasoning=False)
        vm.append_stream_chunk("test", "more", is_reasoning=False)  # pending
        vm.finalize_stream_card("test")
        assert vm.state.stream_cards[0].is_analyzing is False


# --- clear_stream_cards ---


class TestClearStreamCards:
    """clear_stream_cards 清空卡片 + buffer。"""

    def test_clears_cards_and_buffers(self, vm):
        vm.start_stream_card("a")
        vm.start_stream_card("b")
        assert len(vm.state.stream_cards) == 2
        assert len(vm._stream_buffers) == 2
        vm.clear_stream_cards()
        assert vm.state.stream_cards == ()
        assert len(vm._stream_buffers) == 0

    def test_clear_empty_noop(self, vm):
        vm.clear_stream_cards()
        assert vm.state.stream_cards == ()


# --- Adapter methods ---


class TestAdapters:
    """_on_stream_start_adapter / _on_card_start_adapter 适配 strategy 契约。"""

    def test_stream_start_adapter_returns_callable_with_final_flush(self, vm):
        on_chunk = vm._on_stream_start_adapter("test")
        assert callable(on_chunk)
        assert hasattr(on_chunk, "final_flush")
        assert len(vm.state.stream_cards) == 1
        assert vm.state.stream_cards[0].is_analyzing is False

    def test_stream_start_adapter_chunk_flow(self, vm):
        on_chunk = vm._on_stream_start_adapter("test")
        on_chunk("hello", is_reasoning=False)
        on_chunk(" world", is_reasoning=False)
        on_chunk.final_flush()
        card = vm.state.stream_cards[0]
        assert card.content == "hello world"

    def test_card_start_adapter_creates_analyzing_card(self, vm):
        vm._on_card_start_adapter("test")
        assert len(vm.state.stream_cards) == 1
        assert vm.state.stream_cards[0].is_analyzing is True


# --- clear_filters (P1-3 批次 2) ---


class TestClearFilters:
    """P1-3 批次 2 #71: clear_filters 命令."""

    def test_clear_filters_resets_sort_and_pagination(self, vm):
        """clear_filters 重置 sort_column/sort_ascending/page_no/tier_hint."""
        vm._set_state(
            page_no=5,
            sort_column="ai_score",
            sort_ascending=False,
            tier_hint="sys_strategy_tier_hint",
        )
        vm.clear_filters()
        assert vm.state.page_no == 1
        assert vm.state.sort_column is None
        assert vm.state.sort_ascending is True
        assert vm.state.tier_hint is None

    def test_clear_filters_does_not_clear_full_results(self, vm):
        """clear_filters 保留 _full_results (用户可参考上次结果)."""
        import pandas as pd

        vm._full_results = pd.DataFrame({"ts_code": ["000001"]})
        vm.clear_filters()
        assert vm._full_results is not None
        assert len(vm._full_results) == 1


# --- Non-streaming concurrency placeholder (Task 3.1: AI 并发占位卡假死修复) ---


class TestNonStreamingConcurrencyPlaceholder:
    """Task 3.1: 非流式并发模式 (concurrency>1) 下占位卡状态闭环.

    复现路径: ai_mixin 在 concurrency>1 时调 ``on_card_start`` (VM:_on_card_start_adapter)
    创建 ``is_analyzing=True`` 占位卡; 单股分析完成后调 ``on_result`` (VM:_on_ai_result_stream)
    传入最终结果。修复前 _on_ai_result_stream 仅更新 logs/_ai_buffer, 占位卡永远停留在
    ``is_analyzing=True`` (假死)。修复后应命中占位卡并写入最终 reasoning/content.
    """

    def test_placeholder_card_finalized_on_result_stream(self, vm):
        """concurrency>1 路径: 结果到达后占位卡 is_analyzing=False 且含最终内容."""
        # 1. 模拟 ai_mixin 在 concurrency>1 时调 on_card_start 创建占位卡
        vm._on_card_start_adapter("贵州茅台")
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is True
        assert card.reasoning == ""
        assert card.content == ""

        # 2. 模拟 ai_mixin 调 on_result 传入最终结果 (row_data 字段对齐 _build_result_row)
        vm._on_ai_result_stream(
            {
                "name": "贵州茅台",
                "ai_score": 75.0,
                "thinking": "技术面多头排列，量能放大",
                "ai_reason": "最终分析：建议关注",
            }
        )

        # 3. 断言占位卡已终结 (is_analyzing=False) 且写入最终 reasoning/content
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is False, "占位卡未终结 (假死)"
        assert card.reasoning == "技术面多头排列，量能放大"
        assert card.content == "最终分析：建议关注"

    def test_placeholder_card_unknown_name_does_not_crash(self, vm):
        """结果 name 不匹配任何占位卡时不应崩溃 (边界)."""
        vm._on_card_start_adapter("贵州茅台")
        vm._on_ai_result_stream(
            {
                "name": "其他股票",
                "ai_score": 60.0,
                "thinking": "思考",
                "ai_reason": "分析",
            }
        )
        # 不匹配的占位卡不应被误更新
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is True
        assert card.reasoning == ""

    def test_streaming_card_not_overwritten_by_result_stream(self, vm):
        """流式卡 (concurrency=1, is_analyzing=False) 不应被 _on_ai_result_stream 覆盖.

        流式卡的内容由 chunk 流式写入 + finalize_stream_card 终结; _on_ai_result_stream
        仅用于并发模式的占位卡终结, 不应破坏流式路径行为 (任务要求: 不破坏流式路径).
        """
        # 1. 模拟 concurrency=1 流式路径: on_stream_start 创建流式卡 (is_analyzing=False)
        on_chunk = vm._on_stream_start_adapter("贵州茅台")
        on_chunk("流式内容已写入", is_reasoning=False)
        on_chunk.final_flush()
        original_content = vm.state.stream_cards[0].content
        assert original_content == "流式内容已写入"

        # 2. 调 _on_ai_result_stream (流式路径也会被调用, 但不应覆盖流式卡内容)
        vm._on_ai_result_stream(
            {
                "name": "贵州茅台",
                "ai_score": 75.0,
                "thinking": "思考",
                "ai_reason": "最终分析",
            }
        )

        # 3. 流式卡内容不被覆盖 (流式路径行为不变)
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is False
        assert card.content == original_content
