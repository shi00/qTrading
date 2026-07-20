"""BacktestViewModel 单元测试（state-based, §3.0.1 paradigm）"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.task_manager import TaskManager
from strategies.backtest.config import BacktestConfig
from ui.viewmodels import Message
from ui.viewmodels.backtest_view_model import BacktestState, BacktestViewModel

pytestmark = pytest.mark.unit


class TestBacktestViewModel:
    """BacktestViewModel 测试用例。"""

    def test_init(self):
        """测试初始化。"""
        vm = BacktestViewModel()
        assert vm.state.result is None
        assert vm.state.is_running is False

    def test_state_defaults(self):
        """VM 不再有回调属性；state 字段覆盖原回调承载的 UI 状态。"""
        vm = BacktestViewModel()
        assert vm.state.is_running is False
        assert vm.state.progress == 0.0
        assert vm.state.progress_message is None
        assert vm.state.status_message is None
        assert vm.state.status_color == ""
        assert vm.state.result is None

    def test_notifies_subscribers(self):
        """subscribe 接收状态快照；_set_state 触发通知。"""
        vm = BacktestViewModel()
        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))
        vm._set_state(is_running=True, status_color="info")
        assert len(snapshots) >= 1
        assert snapshots[-1].is_running is True
        assert snapshots[-1].status_color == "info"

    def test_init_assembles_default_engine_factory_and_strategy_lookup(self):
        """未显式注入 service 时，viewmodel 应装配默认 engine_factory 和 strategy_lookup。

        回归保障：若未来重构误删装配代码，本测试可捕获。
        """
        vm = BacktestViewModel()
        assert vm.service._engine_factory is not None
        assert vm.service._strategy_lookup is not None

        # strategy_lookup 应委托给 get_strategy_registry（未知 key 返回 None）
        assert vm.service._strategy_lookup("__nonexistent_key__") is None

        # engine_factory 调用应实例化 VectorBacktestEngine（验证延迟 import 装配正确）
        with patch("strategies.backtest.engine.VectorBacktestEngine") as mock_engine_cls:
            engine = vm.service._engine_factory(MagicMock(), MagicMock(), None)
            mock_engine_cls.assert_called_once()
            assert engine is mock_engine_cls.return_value

    def test_dispose(self):
        """测试资源清理。"""
        vm = BacktestViewModel()
        vm._set_state(is_running=True, status_color="info", progress=0.5)
        vm.dispose()

        assert vm.state.result is None
        assert vm.state.is_running is False
        assert vm.state.status_color == ""
        assert vm.state.progress == 0.0

    def test_dispose_cancels_running_task(self):
        """dispose() 必须先取消运行中任务再清引用，防止孤儿任务（R.1.1）。"""
        vm = BacktestViewModel()
        vm._task_id = "running_task_001"
        vm._set_state(is_running=True, progress=0.5, status_color="info")

        with patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls:
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm_cls.return_value = mock_tm

            vm.dispose()

            mock_tm.cancel_task.assert_called_once_with("running_task_001")

        assert vm._task_id is None
        assert vm.state.result is None
        assert vm.state.is_running is False
        assert vm.state.progress == 0.0
        assert vm.state.status_color == ""
        assert vm.state.status_message is None
        assert vm.state.progress_message is None

    def test_dispose_no_running_task_is_noop(self):
        """dispose() 在无运行任务时不应调用 cancel_task（幂等性，R.1.1）。"""
        vm = BacktestViewModel()

        with patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls:
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm_cls.return_value = mock_tm

            vm.dispose()

            mock_tm.cancel_task.assert_not_called()

        assert vm._task_id is None

    def test_dispose_is_idempotent(self):
        """dispose() 连续调用两次：第二次不应重复调 cancel_task（R.1.1 幂等性）。"""
        vm = BacktestViewModel()
        vm._task_id = "running_task_001"

        with patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls:
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm_cls.return_value = mock_tm

            vm.dispose()
            vm.dispose()

            mock_tm.cancel_task.assert_called_once_with("running_task_001")

        assert vm._task_id is None
        assert vm.state.result is None
        assert vm.state.is_running is False

    def test_is_running_via_state(self):
        """测试 is_running 通过 state 暴露（L771 合规, 无 property dual-track）。"""
        vm = BacktestViewModel()
        assert vm.state.is_running is False

        vm._set_state(is_running=True)
        assert vm.state.is_running is True

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
        vm._set_state(is_running=True)

        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        await vm.run_backtest("test_strategy", config)

        assert vm.state.status_color == "warning"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "backtest_already_running"

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
        mock_result = MagicMock()
        mock_result.duration_ms = 1500
        mock_result.metrics = {"sharpe_ratio": 1.5}
        vm.service.run_backtest = AsyncMock(return_value=mock_result)
        return vm, mock_result

    @pytest.mark.asyncio
    async def test_run_backtest_success_path(self):
        """测试回测成功执行完整路径。"""
        vm, mock_result = self._make_vm_with_mocks()

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        assert vm.state.is_running is True

        execution_result = await captured_factory(task_id="task_123")

        assert vm.state.result is mock_result
        assert vm.state.is_running is False
        assert vm.state.status_color == "success"
        assert execution_result is not None

    @pytest.mark.asyncio
    async def test_run_backtest_progress_callback(self):
        """测试回测进度回调。"""
        vm = BacktestViewModel()

        async def service_run(**kwargs):
            progress_cb = kwargs.get("progress_callback")
            if progress_cb:
                progress_cb(0.5, "halfway")
            return MagicMock(duration_ms=500, metrics={"sharpe_ratio": 2.0})

        vm.service.run_backtest = AsyncMock(side_effect=service_run)

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        await captured_factory(task_id="task_456")

        progress_snapshots = [s for s in snapshots if s.progress == 0.5]
        assert len(progress_snapshots) >= 1
        assert progress_snapshots[-1].progress_message is not None
        assert progress_snapshots[-1].progress_message.key == "halfway"
        assert progress_snapshots[-1].progress_message.params == {}

    @pytest.mark.asyncio
    async def test_run_backtest_exception_path(self):
        """测试回测执行异常路径。"""
        vm = BacktestViewModel()
        vm.service.run_backtest = AsyncMock(side_effect=RuntimeError("strategy crashed"))

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(RuntimeError, match="strategy crashed"):
            await captured_factory(task_id="task_789")

        assert vm.state.is_running is False
        assert vm.state.status_color == "error"
        assert vm.state.progress == 1.0
        # Both starting (info) and failed (error) states were observed
        assert any(s.status_color == "info" for s in snapshots)
        assert any(s.status_color == "error" for s in snapshots)

    @pytest.mark.asyncio
    async def test_run_backtest_task_rejected(self):
        """测试 TaskManager 拒绝任务（返回 None）。"""
        vm, _ = self._make_vm_with_mocks()

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value=None)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert vm.state.is_running is False
        assert vm.state.status_color == "warning"

    @pytest.mark.asyncio
    async def test_run_backtest_sets_running_state(self):
        """测试回测运行状态管理。"""
        vm, _ = self._make_vm_with_mocks()

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value="task_001")
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert vm.state.is_running is True
        assert vm.state.result is None
        assert vm.state.status_color == "info"
        assert vm.state.progress == 0.0

    @pytest.mark.asyncio
    async def test_run_backtest_no_callbacks(self):
        """测试无订阅时回测不报错。"""
        vm, _ = self._make_vm_with_mocks()

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(return_value="task_002")
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert vm.state.is_running is True

    @pytest.mark.asyncio
    async def test_run_backtest_exception_no_callbacks(self):
        """测试无订阅时回测异常不报错。"""
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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(ValueError, match="config error"):
            await captured_factory(task_id="task_err")

        assert vm.state.is_running is False

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

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None

        # Execute the coroutine — CancelledError propagates
        with pytest.raises(asyncio.CancelledError):
            await captured_factory(task_id="task_cancel")

        # After cancellation, is_running must be False
        assert vm.state.is_running is False
        # Result must remain None (no partial result)
        assert vm.state.result is None

        # Verify final progress was set to 1.0 (from finally block)
        assert vm.state.progress == 1.0

        # Simulate a late progress callback after cancellation
        if captured_progress_cb:
            captured_progress_cb(0.8, "late update")

        # The late callback should NOT have updated state — is_running is False,
        # meaning the guard in _progress_callback prevented the update
        assert vm.state.progress == 1.0
        assert vm.state.progress_message != Message("late update")

    @pytest.mark.asyncio
    async def test_run_backtest_failure_reverts_state_properly(self):
        """测试回测执行失败后状态正确恢复。"""
        vm = BacktestViewModel()
        vm.service.run_backtest = AsyncMock(side_effect=RuntimeError("strategy crashed"))

        snapshots: list = []
        vm.subscribe(lambda s: snapshots.append(s))

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
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

        assert captured_factory is not None
        with pytest.raises(RuntimeError, match="strategy crashed"):
            await captured_factory(task_id="task_fail")

        # Verify state reverts properly
        assert vm.state.is_running is False
        assert vm.state.result is None
        # Verify status was set to error
        assert vm.state.status_color == "error"
        assert vm.state.status_message is not None
        assert vm.state.status_message.key == "backtest_failed"
        # Verify progress was set to 1.0 (final state from finally block)
        assert vm.state.progress == 1.0


class TestBacktestViewModelCoverageGaps:
    """补充测试：覆盖剩余未覆盖分支/行（显式 service 注入、unsubscribe、_cancel_check）。"""

    def test_init_with_explicit_service_skips_default_factory(self):
        """显式注入 service 时，VM 应直接使用该 service，跳过默认 engine_factory 装配。

        覆盖 __init__ 中 `if service is None` 的 false 分支（行 61->78）。
        """
        mock_service = MagicMock()
        vm = BacktestViewModel(service=mock_service)

        assert vm.service is mock_service

    def test_subscribe_unsubscribe_removes_callback(self):
        """subscribe 返回的 unsubscribe 函数应从订阅列表移除回调，后续状态变更不再通知。

        覆盖 _unsubscribe 函数体（行 94-95）。
        """
        vm = BacktestViewModel()
        snapshots: list = []

        def cb(s):  # noqa: ANN001
            snapshots.append(s)

        unsubscribe = vm.subscribe(cb)

        vm._set_state(is_running=True)
        assert len(snapshots) == 1

        unsubscribe()

        vm._set_state(is_running=False)
        # 取消订阅后不再收到通知
        assert len(snapshots) == 1
        assert vm._subscribers == []

    def test_subscribe_unsubscribe_idempotent(self):
        """unsubscribe 重复调用不应抛异常（callback 已移除时跳过 remove）。

        覆盖 _unsubscribe 中 `if callback in self._subscribers` 的 false 分支。
        """
        vm = BacktestViewModel()

        def cb(s):  # noqa: ANN001
            pass

        unsubscribe = vm.subscribe(cb)

        unsubscribe()
        # 第二次调用：callback 已不在列表，不应抛异常
        unsubscribe()

        assert vm._subscribers == []

    @pytest.mark.asyncio
    async def test_run_backtest_invokes_cancel_check(self):
        """_execute_backtest 应将 _cancel_check 传给 service.run_backtest，且该回调委托给 TaskManager.is_cancelled。

        覆盖 _cancel_check 函数体（行 207）。验证 cancel_check 回调被 service 调用后，
        正确委托给 TaskManager().is_cancelled(task_id)。
        """
        vm = BacktestViewModel()

        captured_cancel_check: Callable[[], bool] | None = None

        async def service_run(**kwargs):
            nonlocal captured_cancel_check
            captured_cancel_check = kwargs.get("cancel_check")
            # 调用 cancel_check 验证其委托行为
            assert captured_cancel_check is not None
            result = captured_cancel_check()
            assert result is False
            return MagicMock(duration_ms=100, metrics={"sharpe_ratio": 1.0})

        vm.service.run_backtest = AsyncMock(side_effect=service_run)

        captured_factory: Callable[..., Awaitable[Any]] | None = None

        def capture_submit(name, task_type, coroutine_factory, cancellable=False, **kwargs):
            nonlocal captured_factory
            captured_factory = coroutine_factory
            return "task_cancel_check"

        config = BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))

        with (
            patch("ui.viewmodels.backtest_view_model.TaskManager") as mock_tm_cls,
            patch("ui.viewmodels.backtest_view_model.get_strategy_registry") as mock_registry,
        ):
            mock_tm = MagicMock(spec=TaskManager)
            mock_tm.submit_task = MagicMock(side_effect=capture_submit)
            mock_tm.is_cancelled = MagicMock(return_value=False)
            mock_tm_cls.return_value = mock_tm
            mock_registry.return_value = {"test_strategy": MagicMock(__name__="TestStrategy")}

            await vm.run_backtest("test_strategy", config)

            assert captured_factory is not None
            # 必须在 patch 作用域内执行，因为 _cancel_check 内部会再次调用 TaskManager()
            await captured_factory(task_id="task_cancel_check")

            # 验证 cancel_check 已委托给 TaskManager.is_cancelled
            mock_tm.is_cancelled.assert_called_once_with("task_cancel_check")


class TestBacktestStateEquality:
    """BacktestState 自定义 __eq__/__hash__ 合约测试。

    自定义 equality 的目的（frozen dataclass + L771 合规 + spec.md use_state setter 安全性）:
    - result 字段用 identity 比较，避免 BacktestResult.__eq__ 触发 DataFrame __eq__ 抛 TypeError
    - 非 BacktestState 类型返回 NotImplemented（Python 数据模型约定，让反射比较生效）
    - 自定义 __eq__ 会 disable 默认 __hash__，必须显式重定义才能保持 hashable
    """

    def test_eq_returns_not_implemented_for_non_backtest_state(self):
        """非 BacktestState 类型应返回 NotImplemented（不抛异常）。

        覆盖行 54-56: `if not isinstance(other, BacktestState): return NotImplemented`.
        """
        state = BacktestState()
        assert state.__eq__("not a state") is NotImplemented
        assert state.__eq__(123) is NotImplemented
        assert state.__eq__(None) is NotImplemented

    def test_eq_uses_identity_for_result_field(self):
        """result 字段用 identity 比较：不同对象不等，同一对象等。

        回归保障：若误将 `is` 改回 `==`，BacktestResult 内部 DataFrame __eq__
        会抛 TypeError，本测试可捕获。覆盖行 62: `self.result is other.result`.
        """
        result_a = MagicMock(name="result_a")
        result_b = MagicMock(name="result_b")
        state_a = BacktestState(result=result_a)
        state_b = BacktestState(result=result_b)
        state_c = BacktestState(result=result_a)  # 同一 result identity

        assert state_a != state_b  # 不同 result identity
        assert state_a == state_c  # 同一 result identity

    def test_eq_false_when_other_fields_differ(self):
        """任一非 result 字段不等则 __eq__ 返回 False。"""
        result = MagicMock()
        base = BacktestState(
            is_running=True,
            progress=0.5,
            progress_message=Message("prog"),
            status_message=Message("status"),
            status_color="info",
            result=result,
        )
        # 逐字段变更，验证每个字段都参与比较
        assert base != replace(base, is_running=False)
        assert base != replace(base, progress=0.6)
        assert base != replace(base, progress_message=Message("other"))
        assert base != replace(base, status_message=Message("other"))
        assert base != replace(base, status_color="warning")

    def test_hash_is_deterministic_and_usable_as_dict_key(self):
        """__hash__ 不抛异常、确定性、可作 dict key。

        覆盖行 66-75: 自定义 __hash__ 实现。
        自定义 __eq__ 会 disable 默认 __hash__，必须显式重定义才能 hashable。
        """
        result = MagicMock()
        state_a = BacktestState(is_running=True, progress=0.5, result=result)
        state_b = BacktestState(is_running=True, progress=0.5, result=result)

        assert hash(state_a) == hash(state_b)

        # 可作为 dict key（验证 __hash__ + __eq__ 一致性）
        d: dict = {state_a: "value"}
        assert d[state_b] == "value"


class TestBackgroundTaskLifecycle:
    """fire-and-forget 后台任务生命周期测试。

    覆盖 _on_background_task_done 三个分支（正常完成/cancelled/异常）
    与 dispose 取消未完成 background task 的孤儿防护（R.1.1）。
    """

    def test_on_background_task_done_normal_completion_discards_without_logging(self, caplog: pytest.LogCaptureFixture):
        """正常完成的任务：从 _background_tasks 移除且不记 error 日志。

        覆盖行 141 (`discard`) + 142-143 (`if task.cancelled(): return` false 分支)
        + 144-146 (`if exc is not None` false 分支).
        """
        import logging

        vm = BacktestViewModel()

        async def _noop() -> None:
            return None

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(_noop())
            vm._background_tasks.add(task)
            loop.run_until_complete(task)
            assert task.done()
            assert not task.cancelled()

            with caplog.at_level(logging.ERROR, logger="ui.viewmodels.backtest_view_model"):
                vm._on_background_task_done(task)

            assert task not in vm._background_tasks
            assert not any("Background task failed" in r.message for r in caplog.records)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_on_background_task_done_cancelled_does_not_log_error(self, caplog: pytest.LogCaptureFixture):
        """被取消的任务：不记录 error 日志（R2 — CancelledError 是正常取消传播）。

        覆盖行 142-143 (`if task.cancelled(): return` true 分支).
        """
        import logging

        vm = BacktestViewModel()

        async def _hang() -> None:
            await asyncio.Event().wait()  # 永不完成

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(_hang())
            vm._background_tasks.add(task)
            task.cancel()
            try:
                loop.run_until_complete(task)
            except asyncio.CancelledError:
                pass
            assert task.cancelled()

            with caplog.at_level(logging.ERROR, logger="ui.viewmodels.backtest_view_model"):
                vm._on_background_task_done(task)

            assert task not in vm._background_tasks
            assert not any("Background task failed" in r.message for r in caplog.records)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_on_background_task_done_with_exception_logs_error(self, caplog: pytest.LogCaptureFixture):
        """抛异常的任务：记录 error 日志并读取异常（避免 'Task exception was never retrieved'）。

        覆盖行 144-146 (`exc = task.exception(); if exc is not None: logger.error(...)`).
        """
        import logging

        vm = BacktestViewModel()

        async def _boom() -> None:
            raise RuntimeError("background task boom")

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            task = loop.create_task(_boom())
            vm._background_tasks.add(task)
            try:
                loop.run_until_complete(task)
            except RuntimeError:
                pass
            assert task.done()
            assert not task.cancelled()

            with caplog.at_level(logging.ERROR, logger="ui.viewmodels.backtest_view_model"):
                vm._on_background_task_done(task)

            assert task not in vm._background_tasks
            assert any(
                "Background task failed" in r.message and "background task boom" in r.message for r in caplog.records
            )
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_dispose_cancels_pending_background_tasks(self):
        """dispose() 必须取消未完成的 background task（R.1.1 孤儿任务防护）。

        覆盖行 124-126 (`for t in list(self._background_tasks): if not t.done(): t.cancel()`).
        """
        vm = BacktestViewModel()

        # 注入一个未完成的 fake task（避免真实事件循环依赖）
        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False
        vm._background_tasks.add(fake_task)

        vm.dispose()

        fake_task.cancel.assert_called_once_with()
        # _background_tasks 不立即 clear（done_callback 负责移除，见 NOTE(lazy) L127-130）
        # 但 dispose 已对每个未完成任务调用 cancel()

    def test_dispose_skips_done_background_tasks(self):
        """dispose() 对已完成的 background task 不重复 cancel（幂等性）。"""
        vm = BacktestViewModel()

        done_task = MagicMock(spec=asyncio.Task)
        done_task.done.return_value = True
        vm._background_tasks.add(done_task)

        vm.dispose()

        done_task.cancel.assert_not_called()


class TestSplitterWidthPersistence:
    """splitter 宽度的读写委托测试（P1-1/P2-1: View 经 VM 读写 ConfigHandler）。

    覆盖 get_splitter_width/persist_splitter_width 的全部代码路径，
    包括 R16 关键路径：同步签名包裹异步写盘，避免 Flet 事件处理器阻塞。
    """

    def test_get_splitter_width_delegates_to_config_handler(self):
        """get_splitter_width 应委托给 ConfigHandler.get_typed 并返回其结果。

        覆盖行 155-157. 同时验证 default 透传（mock 返回值就是 default 时也透传）。
        """
        vm = BacktestViewModel()

        with patch("utils.config_handler.ConfigHandler.get_typed", return_value=250) as mock_get:
            result = vm.get_splitter_width("backtest.splitter.left_width", 200)

        mock_get.assert_called_once_with("backtest.splitter.left_width", int, 200)
        assert result == 250

    def test_persist_splitter_width_no_running_loop_is_noop(self):
        """无 running loop 时静默跳过（不抛 RuntimeError）。

        覆盖行 177-180 (`except RuntimeError: return`).
        这是测试环境/CLI 启动前的合法场景。
        """
        vm = BacktestViewModel()

        # 无 running loop：不应抛异常、不应创建 task
        with (
            patch("utils.config_handler.ConfigHandler.set_typed") as mock_set,
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm_cls,
        ):
            vm.persist_splitter_width("backtest.splitter.left_width", 300)

        mock_set.assert_not_called()
        mock_tpm_cls.assert_not_called()
        assert vm._background_tasks == set()

    def test_persist_splitter_width_creates_background_task_and_writes(
        self,
    ):
        """有 running loop 时创建 background task 并经 ThreadPoolManager 异步写盘。

        覆盖行 166-175 + 181-183: _persist 协程定义、ThreadPoolManager.run_async 提交、
        task 加入 _background_tasks、add_done_callback 注册.
        这是 R16 关键路径：同步签名 → 异步写盘，避免 Flet 事件处理器阻塞。
        """
        vm = BacktestViewModel()

        async def _run_test():
            with (
                patch("utils.config_handler.ConfigHandler.set_typed", return_value=True) as mock_set,
                patch("utils.thread_pool.ThreadPoolManager") as mock_tpm_cls,
            ):
                mock_tpm = MagicMock()
                mock_tpm.run_async = AsyncMock(return_value=None)
                mock_tpm_cls.return_value = mock_tpm

                vm.persist_splitter_width("backtest.splitter.left_width", 350)

                # task 应已加入 _background_tasks
                assert len(vm._background_tasks) == 1
                task = next(iter(vm._background_tasks))
                assert isinstance(task, asyncio.Task)
                assert task in vm._background_tasks

                # 等待 task 完成
                await task

                # 验证 ThreadPoolManager.run_async 被调用，传入 TaskType.IO + set_typed + 参数
                from utils.thread_pool import TaskType

                mock_tpm.run_async.assert_called_once_with(
                    TaskType.IO,
                    mock_set,
                    "backtest.splitter.left_width",
                    350,
                )
                mock_set.assert_not_called()  # 由 run_async 执行，不直接调用

            # task 完成后 done_callback 应将其从 _background_tasks 移除
            assert vm._background_tasks == set()

        asyncio.run(_run_test())

    def test_persist_splitter_width_swallows_exception_as_debug_log(self, caplog: pytest.LogCaptureFixture):
        """写盘失败时异常应被吞为 debug 日志（fire-and-forget 契约：不向调用方抛）。

        覆盖行 172-175 (`except CancelledError: raise; except Exception: logger.debug(...)`).
        """
        import logging

        vm = BacktestViewModel()

        async def _run_test():
            with (
                patch("utils.config_handler.ConfigHandler.set_typed", side_effect=OSError("disk full")),
                patch("utils.thread_pool.ThreadPoolManager") as mock_tpm_cls,
            ):
                mock_tpm = MagicMock()
                mock_tpm.run_async = AsyncMock(side_effect=OSError("disk full"))
                mock_tpm_cls.return_value = mock_tpm

                with caplog.at_level(logging.DEBUG, logger="ui.viewmodels.backtest_view_model"):
                    vm.persist_splitter_width("backtest.splitter.left_width", 400)
                    # 等待 background task 完成
                    await asyncio.gather(*vm._background_tasks, return_exceptions=True)

                # 验证异常被吞为 debug 日志，未抛到调用方
                assert any("persist_splitter_width failed" in r.message for r in caplog.records)

        asyncio.run(_run_test())

    def test_persist_splitter_width_propagates_cancelled_error(self):
        """CancelledError 不应被通用 except 吞没（R2 红线）。

        覆盖行 172-173 (`except asyncio.CancelledError: raise`).
        """
        vm = BacktestViewModel()

        async def _run_test():
            with (
                patch("utils.config_handler.ConfigHandler.set_typed"),
                patch("utils.thread_pool.ThreadPoolManager") as mock_tpm_cls,
            ):
                mock_tpm = MagicMock()
                mock_tpm.run_async = AsyncMock(side_effect=asyncio.CancelledError())
                mock_tpm_cls.return_value = mock_tpm

                vm.persist_splitter_width("backtest.splitter.left_width", 400)
                # CancelledError 应传播出 _persist，task 状态为 cancelled
                done, pending = await asyncio.wait(vm._background_tasks, return_when=asyncio.ALL_COMPLETED)
                assert len(done) == 1
                task = done.pop()
                assert task.cancelled()

        asyncio.run(_run_test())
