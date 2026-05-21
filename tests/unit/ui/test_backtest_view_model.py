"""BacktestViewModel 单元测试"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from strategies.backtest.config import BacktestConfig
from ui.viewmodels.backtest_view_model import BacktestViewModel


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
        on_update = MagicMock()
        on_status = MagicMock()
        on_progress = MagicMock()
        on_result = MagicMock()

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
        on_status = MagicMock()

        vm.bind(on_status=on_status)

        assert vm.on_status == on_status
        assert vm.on_update is None
        assert vm.on_progress is None
        assert vm.on_result is None

    def test_dispose(self):
        """测试资源清理。"""
        vm = BacktestViewModel()
        vm.bind(on_update=MagicMock(), on_status=MagicMock())
        vm.dispose()

        assert vm.on_update is None
        assert vm.on_status is None
        assert vm.result is None

    def test_dispose_clears_all_callbacks(self):
        """测试 dispose 清理所有回调。"""
        vm = BacktestViewModel()
        vm.bind(
            on_update=MagicMock(),
            on_status=MagicMock(),
            on_progress=MagicMock(),
            on_result=MagicMock(),
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

        mock_result = MagicMock()
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

    @patch("ui.viewmodels.backtest_view_model.get_strategy_registry")
    def test_get_available_strategies(self, mock_registry):
        """测试获取可用策略列表。"""
        mock_strategy_cls = MagicMock()
        mock_strategy_cls.return_value.name = "测试策略"
        mock_registry.return_value = {"test_strategy": mock_strategy_cls}

        vm = BacktestViewModel()
        strategies = vm.get_available_strategies()

        assert "test_strategy" in strategies
        assert strategies["test_strategy"] == "测试策略"

    @patch("ui.viewmodels.backtest_view_model.get_strategy_registry")
    def test_get_available_strategies_empty(self, mock_registry):
        """测试获取空策略列表。"""
        mock_registry.return_value = {}

        vm = BacktestViewModel()
        strategies = vm.get_available_strategies()

        assert strategies == {}

    @patch("ui.viewmodels.backtest_view_model.get_strategy_registry")
    def test_get_available_strategies_multiple(self, mock_registry):
        """测试获取多个策略列表。"""
        mock_strategy_cls1 = MagicMock()
        mock_strategy_cls1.return_value.name = "策略1"
        mock_strategy_cls2 = MagicMock()
        mock_strategy_cls2.return_value.name = "策略2"
        mock_registry.return_value = {
            "strategy1": mock_strategy_cls1,
            "strategy2": mock_strategy_cls2,
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

        on_status = MagicMock()
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
