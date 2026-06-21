"""BacktestViewModel 单元测试"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig
from ui.viewmodels.backtest_view_model import BacktestViewModel

pytestmark = pytest.mark.unit


class TestBacktestViewModel:
    """BacktestViewModel 测试用例。"""

    def test_init(self):
        """测试初始化。"""
        vm = BacktestViewModel()
        assert vm.result is None
        assert vm.is_running is False

    def test_bind_callbacks(self):
        """测试绑定回调。"""
        vm = BacktestViewModel()
        on_update = MagicMock()  # spec omitted: callback function
        on_status = MagicMock()  # spec omitted: callback function
        on_progress = MagicMock()  # spec omitted: callback function
        on_result = MagicMock()  # spec omitted: callback function

        vm.bind(
            on_update=on_update,
            on_status=on_status,
            on_progress=on_progress,
            on_result=on_result,
        )

        assert vm.on_update == on_update
        assert vm.on_status == on_status
        assert vm.on_progress == on_progress
        assert vm.on_result == on_result

    def test_bind_partial_callbacks(self):
        """测试部分绑定回调。"""
        vm = BacktestViewModel()
        on_status = MagicMock()  # spec omitted: callback function

        vm.bind(on_status=on_status)

        assert vm.on_status == on_status
        assert vm.on_update is None
        assert vm.on_progress is None
        assert vm.on_result is None

    def test_dispose(self):
        """测试资源清理。"""
        vm = BacktestViewModel()
        vm.bind(on_update=MagicMock(), on_status=MagicMock())  # spec omitted: callback functions
        vm.dispose()

        assert vm.on_update is None
        assert vm.on_status is None
        assert vm.result is None

    def test_dispose_clears_all_callbacks(self):
        """测试 dispose 清理所有回调。"""
        vm = BacktestViewModel()
        vm.bind(
            on_update=MagicMock(),  # spec omitted: callback function
            on_status=MagicMock(),  # spec omitted: callback function
            on_progress=MagicMock(),  # spec omitted: callback function
            on_result=MagicMock(),  # spec omitted: callback function
        )
        vm.dispose()

        assert vm.on_update is None
        assert vm.on_status is None
        assert vm.on_progress is None
        assert vm.on_result is None

    def test_result_property(self):
        """测试 result 属性。"""
        vm = BacktestViewModel()
        assert vm.result is None

        mock_result = (
            MagicMock()
        )  # spec omitted: BacktestResult frozen dataclass, fields lack defaults so spec blocks attribute access
        vm._result = mock_result
        assert vm.result == mock_result

    def test_is_running_property(self):
        """测试 is_running 属性。"""
        vm = BacktestViewModel()
        assert vm.is_running is False

        vm._is_running = True
        assert vm.is_running is True

    def test_create_config(self):
        """测试创建配置。"""
        vm = BacktestViewModel()
        config = vm.create_config(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=500_000.0,
            commission_rate=5e-4,
            rebalance_freq="weekly",
        )

        assert isinstance(config, BacktestConfig)
        assert config.start_date == date(2024, 1, 1)
        assert config.end_date == date(2024, 12, 31)
        assert config.initial_capital == 500_000.0
        assert config.commission_rate == 5e-4
        assert config.rebalance_freq == "weekly"

    def test_create_config_default_values(self):
        """测试创建配置默认值。"""
        vm = BacktestViewModel()
        config = vm.create_config(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        assert config.initial_capital == 1_000_000.0
        assert config.commission_rate == 3e-4
        assert config.commission_min == 5.0
        assert config.stamp_duty_rate == 1e-3
        assert config.slippage_bps == 5.0
        assert config.rebalance_freq == "signal"
        assert config.max_position_count == 50
        assert config.benchmark_code == "000300.SH"
        assert config.risk_free_rate == 0.02

    def test_create_config_custom_values(self):
        """测试创建配置自定义值。"""
        vm = BacktestViewModel()
        config = vm.create_config(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=2_000_000.0,
            commission_rate=5e-4,
            commission_min=10.0,
            stamp_duty_rate=2e-3,
            slippage_bps=10.0,
            rebalance_freq="daily",
            max_position_count=100,
            benchmark_code="000905.SH",
            risk_free_rate=0.03,
        )

        assert config.initial_capital == 2_000_000.0
        assert config.commission_rate == 5e-4
        assert config.commission_min == 10.0
        assert config.stamp_duty_rate == 2e-3
        assert config.slippage_bps == 10.0
        assert config.rebalance_freq == "daily"
        assert config.max_position_count == 100
        assert config.benchmark_code == "000905.SH"
        assert config.risk_free_rate == 0.03

    @patch("strategies.all_strategies.StrategyManager")
    def test_get_available_strategies(self, mock_manager):
        """测试获取可用策略列表。"""
        mock_manager.return_value.get_all_names.return_value = {"test_strategy": "测试策略"}

        vm = BacktestViewModel()
        strategies = vm.get_available_strategies()

        assert "test_strategy" in strategies
        assert strategies["test_strategy"] == "测试策略"

    @patch("strategies.all_strategies.StrategyManager")
    def test_get_available_strategies_empty(self, mock_manager):
        """测试获取空策略列表。"""
        mock_manager.return_value.get_all_names.return_value = {}

        vm = BacktestViewModel()
        strategies = vm.get_available_strategies()

        assert strategies == {}

    @patch("strategies.all_strategies.StrategyManager")
    def test_get_available_strategies_multiple(self, mock_manager):
        """测试获取多个策略列表。"""
        mock_manager.return_value.get_all_names.return_value = {
            "strategy1": "策略1",
            "strategy2": "策略2",
        }

        vm = BacktestViewModel()
        strategies = vm.get_available_strategies()

        assert len(strategies) == 2
        assert strategies["strategy1"] == "策略1"
        assert strategies["strategy2"] == "策略2"

    @pytest.mark.asyncio
    async def test_run_backtest_already_running(self):
        """测试回测已在运行时的处理。"""
        vm = BacktestViewModel()
        vm._is_running = True

        on_status = MagicMock()  # spec omitted: callback function
        vm.bind(on_status=on_status)

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        await vm.run_backtest("test_strategy", config)

        on_status.assert_called_once()
        assert "运行中" in on_status.call_args[0][0] or "already" in on_status.call_args[0][0].lower()

    @pytest.mark.asyncio
    async def test_get_historical_results(self):
        """测试获取历史回测结果。"""
        vm = BacktestViewModel()
        vm.service.list_results = AsyncMock(return_value=[{"run_id": "test123"}])

        results = await vm.get_historical_results()

        assert len(results) == 1
        assert results[0]["run_id"] == "test123"
        vm.service.list_results.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_historical_results_with_filter(self):
        """测试获取历史回测结果（带策略过滤）。"""
        vm = BacktestViewModel()
        vm.service.list_results = AsyncMock(return_value=[{"run_id": "test123"}])

        results = await vm.get_historical_results(strategy_name="test_strategy", limit=10)

        assert len(results) == 1
        vm.service.list_results.assert_called_once_with(strategy_name="test_strategy", limit=10)

    @pytest.mark.asyncio
    async def test_get_historical_results_empty(self):
        """测试获取空历史回测结果。"""
        vm = BacktestViewModel()
        vm.service.list_results = AsyncMock(return_value=[])

        results = await vm.get_historical_results()

        assert results == []

    @pytest.mark.asyncio
    async def test_load_historical_result(self):
        """测试加载历史回测结果。"""
        vm = BacktestViewModel()
        vm.service.get_result = AsyncMock(return_value={"run_id": "test123", "metrics": {}})

        result = await vm.load_historical_result("test123")

        assert result is not None
        assert result["run_id"] == "test123"
        vm.service.get_result.assert_called_once_with("test123")

    @pytest.mark.asyncio
    async def test_load_historical_result_not_found(self):
        """测试加载不存在的历史回测结果。"""
        vm = BacktestViewModel()
        vm.service.get_result = AsyncMock(return_value=None)

        result = await vm.load_historical_result("nonexistent")

        assert result is None
        vm.service.get_result.assert_called_once_with("nonexistent")


class TestBacktestViewModelRunBacktest:
    """BacktestViewModel.run_backtest 核心路径测试。"""

    def _make_vm_with_mocks(self):
        vm = BacktestViewModel()
        mock_result = (
            MagicMock()
        )  # spec omitted: BacktestResult frozen dataclass, fields lack defaults so spec blocks attribute access
        mock_result.duration_ms = 1500
        mock_result.metrics = {"sharpe_ratio": 1.5}
        vm.service.run_backtest = AsyncMock(return_value=mock_result)
        return vm, mock_result

    @pytest.mark.asyncio
    async def test_run_backtest_success_path(self):
        """测试回测成功执行完整路径。"""
        vm, mock_result = self._make_vm_with_mocks()

        on_update = MagicMock()  # spec omitted: callback function
        on_status = MagicMock()  # spec omitted: callback function
        on_progress = MagicMock()  # spec omitted: callback function
        on_result = MagicMock()  # spec omitted: callback function
        vm.bind(
            on_update=on_update,
            on_status=on_status,
            on_progress=on_progress,
            on_result=on_result,
        )

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_123"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm.update_progress = MagicMock()
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        assert vm._is_running is True

        execution_result = await captured_factory(task_id="task_123")

        assert vm.result == mock_result
        assert vm._is_running is False
        on_result.assert_called_once_with(mock_result)
        on_update.assert_called_once()
        assert execution_result is not None

    @pytest.mark.asyncio
    async def test_run_backtest_progress_callback(self):
        """测试回测进度回调。"""
        vm = BacktestViewModel()

        async def service_run(**kwargs):
            progress_cb = kwargs.get("progress_callback")
            if progress_cb:
                progress_cb(0.5, "halfway")
            return MagicMock(
                duration_ms=500, metrics={"sharpe_ratio": 2.0}
            )  # spec omitted: BacktestResult frozen dataclass

        vm.service.run_backtest = AsyncMock(side_effect=service_run)

        on_progress = MagicMock()  # spec omitted: callback function
        vm.bind(on_progress=on_progress)

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_456"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm.update_progress = MagicMock()
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        await captured_factory(task_id="task_456")

        progress_calls = [c for c in on_progress.call_args_list if len(c[0]) >= 2 and c[0][0] == 0.5]
        assert len(progress_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_backtest_exception_path(self):
        """测试回测执行异常路径。"""
        vm = BacktestViewModel()
        vm.service.run_backtest = AsyncMock(side_effect=RuntimeError("strategy crashed"))

        on_status = MagicMock()  # spec omitted: callback function
        on_progress = MagicMock()  # spec omitted: callback function
        vm.bind(on_status=on_status, on_progress=on_progress)

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_789"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(RuntimeError, match="strategy crashed"):
            await captured_factory(task_id="task_789")

        assert vm._is_running is False
        on_status.assert_called()
        status_call_args = on_status.call_args[0]
        assert "red" in status_call_args
        final_progress_calls = [c for c in on_progress.call_args_list if c[0][0] == 1.0]
        assert len(final_progress_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_backtest_task_rejected(self):
        """测试 TaskManager 拒绝任务（返回 None）。"""
        vm, _ = self._make_vm_with_mocks()

        on_status = MagicMock()  # spec omitted: callback function
        vm.bind(on_status=on_status)

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value=None)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert vm._is_running is False
        rejected_calls = [c for c in on_status.call_args_list if "orange" in str(c)]
        assert len(rejected_calls) >= 1

    @pytest.mark.asyncio
    async def test_run_backtest_sets_running_state(self):
        """测试回测运行状态管理。"""
        vm, _ = self._make_vm_with_mocks()

        on_status = MagicMock()  # spec omitted: callback function
        on_progress = MagicMock()  # spec omitted: callback function
        vm.bind(on_status=on_status, on_progress=on_progress)

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value="task_001")
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert vm._is_running is True
        assert vm._result is None
        starting_calls = [c for c in on_status.call_args_list if "blue" in str(c)]
        assert len(starting_calls) >= 1
        on_progress.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_backtest_no_callbacks(self):
        """测试无回调时回测不报错。"""
        vm, _ = self._make_vm_with_mocks()

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value="task_002")
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert vm._is_running is True

    @pytest.mark.asyncio
    async def test_run_backtest_exception_no_callbacks(self):
        """测试无回调时回测异常不报错。"""
        vm = BacktestViewModel()
        vm.service.run_backtest = AsyncMock(side_effect=ValueError("config error"))

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_err"

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(ValueError, match="config error"):
            await captured_factory(task_id="task_err")

        assert vm._is_running is False

    @pytest.mark.asyncio
    async def test_progress_not_updated_after_task_cancellation(self):
        """测试任务取消后进度回调不更新状态。"""
        vm = BacktestViewModel()

        captured_progress_cb: Callable[[float, str], None] | None = None

        async def service_run(**kwargs):
            nonlocal captured_progress_cb
            captured_progress_cb = kwargs.get("progress_callback")
            if captured_progress_cb:
                captured_progress_cb(0.3, "processing")
            raise asyncio.CancelledError()

        vm.service.run_backtest = AsyncMock(side_effect=service_run)

        on_progress = MagicMock()  # spec omitted: callback function
        vm.bind(on_progress=on_progress)

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_cancel"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm.update_progress = MagicMock()
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None

        # Execute the coroutine — CancelledError propagates
        with pytest.raises(asyncio.CancelledError):
            await captured_factory(task_id="task_cancel")

        # After cancellation, is_running must be False
        assert vm._is_running is False
        # Result must remain None (no partial result)
        assert vm._result is None

        # Record the last progress value from the finally block (1.0)
        final_progress_calls = [c for c in on_progress.call_args_list if c[0][0] == 1.0]
        assert len(final_progress_calls) >= 1

        # Simulate a late progress callback after cancellation
        if captured_progress_cb:
            captured_progress_cb(0.8, "late update")

        # Verify that the late progress callback did update on_progress
        # (current behavior: the callback closure does not guard against post-cancellation calls)
        late_calls = [c for c in on_progress.call_args_list if len(c[0]) >= 2 and c[0][0] == 0.8]
        # The late callback should NOT have updated progress — _is_running is False,
        # meaning the task is done and no further state changes should occur
        assert len(late_calls) == 0

    @pytest.mark.asyncio
    async def test_run_backtest_failure_reverts_state_properly(self):
        """测试回测执行失败后状态正确恢复。"""
        vm = BacktestViewModel()
        vm.service.run_backtest = AsyncMock(side_effect=RuntimeError("strategy crashed"))

        on_status = MagicMock()  # spec omitted: callback function
        on_progress = MagicMock()  # spec omitted: callback function
        on_result = MagicMock()  # spec omitted: callback function
        vm.bind(on_status=on_status, on_progress=on_progress, on_result=on_result)

        captured_factory: Callable[[str], Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_fail"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {
                "test_strategy": MagicMock(__name__="TestStrategy")
            }  # spec omitted: strategy class, dynamic interface

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(RuntimeError, match="strategy crashed"):
            await captured_factory(task_id="task_fail")

        # Verify state reverts properly
        assert vm._is_running is False
        assert vm._result is None
        # Verify on_result was never called (no partial result)
        on_result.assert_not_called()
        # Verify on_status called with error i18n key (backtest_failed) and red color
        error_status_calls = [args for args, _ in on_status.call_args_list if len(args) >= 2 and args[1] == "red"]
        assert len(error_status_calls) >= 1
        error_msg = error_status_calls[-1][0]
        assert "backtest_failed" in error_msg or "回测失败" in error_msg or "Backtest failed" in error_msg
        # Verify on_progress called with 1.0 (final state from finally block)
        final_progress_calls = [c for c in on_progress.call_args_list if c[0][0] == 1.0]
        assert len(final_progress_calls) >= 1
