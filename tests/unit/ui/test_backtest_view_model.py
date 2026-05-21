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

    def test_dispose(self):
        """测试资源清理。"""
        vm = BacktestViewModel()
        vm.bind(on_update=MagicMock(), on_status=MagicMock())
        vm.dispose()

        assert vm.on_update is None
        assert vm.on_status is None
        assert vm.result is None

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
    async def test_load_historical_result(self):
        """测试加载历史回测结果。"""
        vm = BacktestViewModel()
        vm.service.get_result = AsyncMock(return_value={"run_id": "test123", "metrics": {}})

        result = await vm.load_historical_result("test123")

        assert result is not None
        assert result["run_id"] == "test123"
        vm.service.get_result.assert_called_once_with("test123")
