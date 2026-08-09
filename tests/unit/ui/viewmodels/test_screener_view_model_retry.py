"""ScreenerViewModel UX-2.3 单股重试机制单元测试。

覆盖：
1. _on_card_error: 失败卡片标记 error 状态
2. retry_single_stock: 防抖/策略一致性/无 context/成功/异常/CancelledError/无 retry_single
3. schedule_retry: 重试中跳过/无 loop 跳过/正常创建 task
4. select_strategy 在重试中：取消重试任务 + 清空上下文

测试范式参考 test_screener_view_model.py (vm fixture + patch 依赖)。
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ui.viewmodels.screener_view_model import ScreenerViewModel, StreamCard

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


def _make_analyzing_card(name: str = "贵州茅台") -> StreamCard:
    """构造 is_analyzing=True 的占位卡。"""
    return StreamCard(name=name, is_analyzing=True)


def _make_error_card(name: str = "贵州茅台", error: str = "timeout") -> StreamCard:
    """构造 error 状态的卡片。"""
    return StreamCard(name=name, error=error, is_analyzing=False)


# ============================================================================
# 1. _on_card_error
# ============================================================================


class TestOnCardError:
    """_on_card_error: 失败卡片标记 error 状态，is_analyzing=False。"""

    def test_marks_analyzing_card_as_error(self, vm):
        """is_analyzing=True 的卡片 → error=传入消息, is_analyzing=False。"""
        vm._set_state(stream_cards=(_make_analyzing_card("贵州茅台"),))
        vm._on_card_error("贵州茅台", "LLM timeout")
        card = vm.state.stream_cards[0]
        assert card.error == "LLM timeout"
        assert card.is_analyzing is False

    def test_does_not_touch_non_analyzing_card(self, vm):
        """is_analyzing=False 的卡片 → 不变（避免覆盖已完成卡片）。"""
        card = StreamCard(name="贵州茅台", content="已有结果", is_analyzing=False)
        vm._set_state(stream_cards=(card,))
        vm._on_card_error("贵州茅台", "err")
        # 不变
        assert vm.state.stream_cards[0].error is None
        assert vm.state.stream_cards[0].content == "已有结果"

    def test_missing_card_is_noop(self, vm):
        """目标卡片不存在 → 不抛异常，state 不变。"""
        vm._set_state(stream_cards=(_make_analyzing_card("其他股票"),))
        vm._on_card_error("不存在", "err")
        assert len(vm.state.stream_cards) == 1
        assert vm.state.stream_cards[0].name == "其他股票"


# ============================================================================
# 2. retry_single_stock
# ============================================================================


class TestRetrySingleStock:
    """retry_single_stock: 防抖/策略一致性/无 context/成功/异常/CancelledError。"""

    def test_debounce_rejects_when_already_retrying(self, vm):
        """_retrying=True → 直接返回，不调用策略。"""
        vm._retrying = True
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"
        vm._set_state(selected_strategy="strategy_x")

        # 不应抛异常，不应调用策略
        asyncio.run(vm.retry_single_stock("贵州茅台"))
        # 验证没有创建任何卡片变更
        assert vm._retrying is True

    def test_strategy_mismatch_shows_warning(self, vm):
        """_last_strategy_key != state.selected_strategy → warning 状态。"""
        vm._retrying = False
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "old_strategy"
        vm._set_state(selected_strategy="new_strategy")

        asyncio.run(vm.retry_single_stock("贵州茅台"))
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "ai_retry_strategy_changed"
        assert vm.state.status_color == "warning"
        # 没有进入重试状态
        assert vm._retrying is False
        assert vm.state.is_retrying is False

    def test_no_last_ai_context_returns_early(self, vm):
        """_last_ai_context=None → 直接返回，不进入重试。"""
        vm._retrying = False
        vm._last_ai_context = None
        vm._last_strategy_key = "strategy_x"
        vm._set_state(selected_strategy="strategy_x")

        asyncio.run(vm.retry_single_stock("贵州茅台"))
        assert vm._retrying is False
        assert vm.state.is_retrying is False

    def test_success_calls_strategy_retry_single(self, vm):
        """成功路径：调用 strategy.retry_single(name, context)。"""
        # 准备 state：一张失败卡片
        vm._set_state(
            stream_cards=(_make_error_card("贵州茅台", "timeout"),),
            selected_strategy="strategy_x",
        )
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"

        # 用真实 async 函数 + 调用记录，避免 AsyncMock 产生未 await 的 coroutine
        call_records: list[tuple] = []

        async def _record_retry_single(*args, **kwargs):
            call_records.append((args, kwargs))

        strategy = MagicMock()
        strategy.retry_single = _record_retry_single
        strategy.has_retry_single = True  # hasattr 检查
        vm.strategy_mgr.get_strategy.return_value = strategy

        asyncio.run(vm.retry_single_stock("贵州茅台"))

        # 验证策略被调用
        assert len(call_records) == 1
        args, kwargs = call_records[0]
        assert args[0] == "贵州茅台"
        # retry_context 含 strategy_key 透传
        assert args[1]["strategy_key"] == "strategy_x"
        # _task_id=None 不关联 TaskManager
        assert args[1]["_task_id"] is None
        # 重试状态已清理
        assert vm._retrying is False
        assert vm.state.is_retrying is False
        # 失败卡片已转为重试中占位卡（error=None, is_analyzing=True）
        card = vm.state.stream_cards[0]
        assert card.error is None
        assert card.is_analyzing is True

    def test_strategy_exception_shows_error_status(self, vm):
        """A-1: 策略抛异常 → 卡片恢复 error 状态 + status_bar error，重试状态清理。"""
        vm._set_state(
            stream_cards=(_make_error_card("贵州茅台", "old error"),),
            selected_strategy="strategy_x",
        )
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"

        # 用真实 async 函数代替 AsyncMock(side_effect=Exception)，避免
        # AsyncMock 异常路径产生未 await 的 coroutine (RuntimeWarning)
        async def _raise_runtime(*args, **kwargs):
            raise RuntimeError("network down")

        strategy = MagicMock()
        strategy.retry_single = _raise_runtime
        vm.strategy_mgr.get_strategy.return_value = strategy

        asyncio.run(vm.retry_single_stock("贵州茅台"))

        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "ai_retry_failed"
        assert vm.state.status_color == "error"
        assert vm._retrying is False
        assert vm.state.is_retrying is False
        # A-1: 卡片应恢复为 error 状态（非 is_analyzing=True 假死）
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is False
        assert card.error is not None  # 异常消息经 DataSanitizer 脱敏后写入

    def test_cancelled_error_propagates_r2(self, vm):
        """R2: CancelledError 必须重新抛出，不被吞没。"""
        vm._set_state(
            stream_cards=(_make_error_card("贵州茅台"),),
            selected_strategy="strategy_x",
        )
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"

        # 用真实 async 函数代替 AsyncMock(side_effect=CancelledError)，避免
        # AsyncMock 异常路径产生未 await 的 coroutine (RuntimeWarning)
        async def _raise_cancelled(*args, **kwargs):
            raise asyncio.CancelledError()

        strategy = MagicMock()
        strategy.retry_single = _raise_cancelled
        vm.strategy_mgr.get_strategy.return_value = strategy

        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion state cleanup verified below
            asyncio.run(vm.retry_single_stock("贵州茅台"))

        # finally 仍清理重试状态
        assert vm._retrying is False
        assert vm.state.is_retrying is False

    def test_strategy_without_retry_single_keeps_error_card(self, vm):
        """R1-6: 策略无 retry_single 方法 → 卡片保持 error 状态，不转为 analyzing。"""
        vm._set_state(
            stream_cards=(_make_error_card("贵州茅台"),),
            selected_strategy="strategy_x",
        )
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"

        # 策略对象没有 retry_single 属性
        strategy = MagicMock(spec=[])
        vm.strategy_mgr.get_strategy.return_value = strategy

        asyncio.run(vm.retry_single_stock("贵州茅台"))

        assert vm._retrying is False
        assert vm.state.is_retrying is False
        # R1-6: 卡片保持 error 状态（未被转为 is_analyzing=True）
        card = vm.state.stream_cards[0]
        assert card.is_analyzing is False
        assert card.error == "timeout"


# ============================================================================
# 3. schedule_retry
# ============================================================================


class TestScheduleRetry:
    """schedule_retry: 同步签名，内部 loop.create_task 提交。"""

    def test_retrying_skips_scheduling(self, vm):
        """_retrying=True → 直接返回，不创建 task。"""
        vm._retrying = True
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        vm._main_loop = loop

        vm.schedule_retry("贵州茅台")

        loop.create_task.assert_not_called()
        assert len(vm._background_tasks) == 0

    def test_no_loop_skips_scheduling(self, vm):
        """无事件循环 → 静默返回（测试环境/disposed）。"""
        vm._retrying = False
        vm._main_loop = None

        vm.schedule_retry("贵州茅台")

        assert len(vm._background_tasks) == 0

    def test_disposed_skips_scheduling(self, vm):
        """_disposed=True → _get_loop_or_none 返回 None，静默跳过。"""
        vm._retrying = False
        vm._disposed = True
        loop = MagicMock(spec=asyncio.AbstractEventLoop)
        loop.is_running.return_value = True
        vm._main_loop = loop

        vm.schedule_retry("贵州茅台")

        assert len(vm._background_tasks) == 0

    def test_normal_scheduling_creates_task(self, vm):
        """正常路径：创建 task 加入 _background_tasks。

        用 mock loop 避免 _get_loop_or_none 的 is_running() 检查（新创建的
        事件循环 is_running() 返回 False 会被判定为不可用）。
        schedule_retry 内部调用 retry_single_stock(name) 产生 coroutine 传给
        loop.create_task；loop 是 mock 不会真正调度，需手动 close(coroutine)
        避免 "coroutine never awaited" RuntimeWarning。
        """
        vm._retrying = False

        mock_task = MagicMock(spec=asyncio.Task)

        def _create_task_and_close_coro(coro):
            coro.close()  # 避免未 await 的 coroutine 警告
            return mock_task

        mock_loop = MagicMock(spec=asyncio.AbstractEventLoop)
        mock_loop.create_task.side_effect = _create_task_and_close_coro

        with patch.object(vm, "_get_loop_or_none", return_value=mock_loop):
            vm.schedule_retry("贵州茅台")

        mock_loop.create_task.assert_called_once()  # noqa: weak-assertion coroutine closed in side_effect, args unverifiable
        assert mock_task in vm._background_tasks
        # done_callback 已注册（验证回调函数为 _on_background_task_done）
        mock_task.add_done_callback.assert_called_once_with(vm._on_background_task_done)


# ============================================================================
# 4. select_strategy 在重试中
# ============================================================================


class TestSelectStrategyDuringRetry:
    """重试中切换策略：取消重试任务 + 清空上下文。"""

    def test_cancels_retry_task_on_strategy_switch(self, vm):
        """_retrying=True 时切换策略 → 仅取消重试 task（_retry_task），清空上下文。"""
        vm._retrying = True
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "old_strategy"

        # Mock 一个进行中的重试 task
        retry_task = MagicMock(spec=asyncio.Task)
        retry_task.done.return_value = False
        vm._retry_task = retry_task

        vm.select_strategy("new_strategy")

        retry_task.cancel.assert_called_once()  # noqa: weak-assertion cancel() takes no args, strongest assertion
        assert vm._retrying is False
        assert vm._last_ai_context is None
        assert vm._last_strategy_key is None
        assert vm.state.is_retrying is False
        assert vm.state.selected_strategy == "new_strategy"

    def test_only_cancels_retry_task_not_other_background_tasks(self, vm):
        """_retrying=True 时切换策略 → 不触碰其它后台任务（P0-2 范围收敛）。"""
        vm._retrying = True
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "old_strategy"

        # 重试 task + 一个无关后台任务（如 persist_splitter_width）
        retry_task = MagicMock(spec=asyncio.Task)
        retry_task.done.return_value = False
        vm._retry_task = retry_task
        other_task = MagicMock(spec=asyncio.Task)
        other_task.done.return_value = False
        vm._background_tasks.add(other_task)

        vm.select_strategy("new_strategy")

        retry_task.cancel.assert_called_once()  # noqa: weak-assertion cancel() 无参数，确认被精确调用一次即可
        other_task.cancel.assert_not_called()  # 无关后台任务不被取消
        assert vm._retry_task is None

    def test_no_cancel_when_not_retrying(self, vm):
        """_retrying=False 时切换策略 → 不触碰 _retry_task / 其它后台任务。"""
        vm._retrying = False
        task = MagicMock(spec=asyncio.Task)
        vm._retry_task = task
        vm._background_tasks.add(task)

        vm.select_strategy("new_strategy")

        task.cancel.assert_not_called()
        assert vm.state.selected_strategy == "new_strategy"

    def test_dispose_clears_retry_state(self, vm):
        """dispose 清空 _last_ai_context / _last_strategy_key / _retrying (P2-2)。"""
        vm._retrying = True
        vm._last_ai_context = {"on_result": lambda r: None}
        vm._last_strategy_key = "strategy_x"

        vm.dispose()

        assert vm._retrying is False
        assert vm._last_ai_context is None
        assert vm._last_strategy_key is None
