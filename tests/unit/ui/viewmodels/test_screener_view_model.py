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
