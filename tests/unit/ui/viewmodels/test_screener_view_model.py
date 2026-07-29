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


# --- Task 3.3: save_results 失败分态 ---


class TestSaveResultsFailState:
    """Task 3.3: save_results 失败不再落入 screener_exec_error.

    失败时:
    - 结果集 (_full_results) 保留照常上屏
    - 状态栏提示 screener_done_unsaved (含 reason)
    - status_color 为 warning (非 error)
    """

    @pytest.fixture
    def vm_with_strategy(self, vm):
        """配置 vm + mock 策略 + 测试 context + save_results 抛异常."""
        import datetime as dt
        import pandas as pd
        from unittest.mock import AsyncMock

        # Mock 策略对象
        strategy = type("MockStrategy", (), {})()
        strategy.name_key = "strategy_test"
        strategy.filter = lambda ctx: pd.DataFrame(
            {"ts_code": ["000001.SZ", "000002.SZ"], "name": ["平安银行", "万科A"]},
        )
        vm.strategy_mgr.get_strategy.return_value = strategy

        # Mock data_processor.get_strategy_data 为 AsyncMock (await 返回 dict)
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": dt.date(2026, 7, 29),
            },
        )

        # Mock save_results 抛 RuntimeError
        vm.review_mgr.save_results = AsyncMock(side_effect=RuntimeError("DB connection lost"))
        return vm

    @staticmethod
    def _build_sync_submit_task_holder():
        """submit_task 同步调度 coro_factory 并通过 holder 暴露 task 供后续 await."""
        import asyncio

        holder = type("Holder", (), {"task": None})()

        def _sync_submit_task(*args, **kwargs):
            coro_factory = kwargs["coroutine_factory"]
            coro = coro_factory(task_id="test-task-id")
            holder.task = asyncio.ensure_future(coro)
            return "test-task-id"

        return holder, _sync_submit_task

    @pytest.mark.asyncio
    async def test_save_results_fail_shows_unsaved_status(self, vm_with_strategy):
        """save_results raise 时状态为 screener_done_unsaved."""
        from services.task_manager import TaskManager

        holder, _sync_submit = self._build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm_with_strategy.run_strategy("test_strategy")
            # 显式 await _execute_screening 完成
            assert holder.task is not None
            await holder.task

        state = vm_with_strategy.state
        assert state.status_message is not None
        assert state.status_message.key == "screener_done_unsaved"
        assert state.status_color == "warning"
        # reason 透传到 i18n params
        assert "DB connection lost" in state.status_message.params.get("reason", "")

    @pytest.mark.asyncio
    async def test_save_results_fail_retains_full_results(self, vm_with_strategy):
        """save_results 失败后 _full_results 保留 (照常上屏)."""
        from services.task_manager import TaskManager

        holder, _sync_submit = self._build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm_with_strategy.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        # 结果集保留, 含 2 行测试数据
        assert vm_with_strategy._full_results is not None
        assert len(vm_with_strategy._full_results) == 2
        assert "ts_code" in vm_with_strategy._full_results.columns

    @pytest.mark.asyncio
    async def test_save_results_success_shows_saved_status(self, vm):
        """正常路径: save_results 成功时状态仍为 screener_done_saved (回归测试)."""
        import datetime as dt
        import pandas as pd
        from unittest.mock import AsyncMock

        from services.task_manager import TaskManager

        strategy = type("MockStrategy", (), {})()
        strategy.name_key = "strategy_test"
        strategy.filter = lambda ctx: pd.DataFrame({"ts_code": ["000001.SZ"]})
        vm.strategy_mgr.get_strategy.return_value = strategy
        vm.data_processor.get_strategy_data = AsyncMock(
            return_value={
                "screening_data": pd.DataFrame({"ts_code": ["000001.SZ"]}),
                "trade_date": dt.date(2026, 7, 29),
            },
        )
        # save_results 成功
        vm.review_mgr.save_results = AsyncMock(return_value=None)

        holder, _sync_submit = self._build_sync_submit_task_holder()
        with (
            patch.object(TaskManager, "submit_task", side_effect=_sync_submit),
            patch.object(TaskManager, "update_progress"),
        ):
            await vm.run_strategy("test_strategy")
            assert holder.task is not None
            await holder.task

        state = vm.state
        assert state.status_message is not None
        assert state.status_message.key == "screener_done_saved"
        assert state.status_color == "success"
