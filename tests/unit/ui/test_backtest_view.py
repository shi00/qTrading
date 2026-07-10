"""BacktestView 单元测试"""

import logging
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import flet as ft
import polars as pl
import pytest

from strategies.backtest.config import BacktestConfig, BacktestResult
from ui.views.backtest_view import BacktestView, logger as view_logger

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_page() -> MagicMock:
    page = MagicMock(spec=ft.Page)
    page.run_task = MagicMock()
    page.update = MagicMock()
    return page


@pytest.fixture
def mock_result() -> BacktestResult:
    return BacktestResult(
        config=BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        ),
        strategy_name="test_strategy",
        params_snapshot={},
        nav_curve=pl.DataFrame(
            {
                "trade_date": [date(2024, 1, 1)],
                "nav": [1_000_000.0],
            }
        ),
        daily_returns=pl.Series([0.0]),
        benchmark_returns=pl.Series([0.0]),
        trades=pl.DataFrame(),
        positions=pl.DataFrame(),
        skipped_orders=pl.DataFrame(),
        metrics={"sharpe_ratio": 1.5},
        ic_series=pl.Series(),
        period_stats=pl.DataFrame(),
        data_warnings=(),
        failed_signal_dates=(),
        run_id="test_run",
        executed_at=datetime.now(),
        duration_ms=1000,
    )


class TestBacktestView:
    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_init(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {"strategy1": "策略1"}

        view = BacktestView(mock_page)

        assert view.vm == mock_vm
        assert view._selected_strategy == "strategy1"

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_build_content(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)

        content = view._build_content()

        assert isinstance(content, ft.Column)
        assert len(content.controls) > 0

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_load_strategies(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {
            "strategy1": "策略1",
            "strategy2": "策略2",
        }
        mock_config_panel = MagicMock()
        mock_config_panel_cls.return_value = mock_config_panel

        view = BacktestView(mock_page)

        assert len(view.strategy_dropdown.options) == 2
        assert view._selected_strategy == "strategy1"

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_load_strategies_empty(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)

        assert len(view.strategy_dropdown.options) == 0
        assert view._selected_strategy is None

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_strategy_change(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {"strategy1": "策略1"}
        mock_config_panel = MagicMock()
        mock_config_panel_cls.return_value = mock_config_panel

        view = BacktestView(mock_page)

        mock_event = MagicMock()
        mock_event.control.value = "strategy2"

        view._on_strategy_change(mock_event)

        assert view._selected_strategy == "strategy2"
        # set_strategy_key 已在 Phase 3.2.5 声明式重写中删除（死代码），
        # 声明式 config_panel 通过 on_run_backtest 回调一次性获取配置，无需策略 key 注入。

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_run_backtest_no_strategy(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view._on_run_backtest({"start_date": date(2024, 1, 1), "end_date": date(2024, 12, 31)})

        assert view.status_text.value == "mock_text"
        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_run_backtest_with_strategy(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {"strategy1": "策略1"}
        mock_vm.create_config.return_value = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        config = {
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 12, 31),
            "initial_capital": 1_000_000.0,
            "rebalance_freq": "signal",
            "max_position_count": 50,
            "commission_rate": 3e-4,
            "stamp_duty_rate": 1e-3,
            "slippage_bps": 5.0,
        }

        view._on_run_backtest(config)

        assert view.progress_bar.visible is True
        mock_vm.create_config.assert_called_once()
        mock_page.run_task.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_vm_update(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view._on_vm_update()

        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_vm_update_no_page(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = None

        view._on_vm_update()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_vm_status(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view._on_vm_status("Test Status", "blue")

        assert view.status_text.value == "Test Status"
        assert view.status_text.color == "blue"
        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_vm_progress(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view._on_vm_progress(0.5, "Processing...")

        assert view.progress_bar.value == 0.5
        assert view.progress_text.value == "Processing..."
        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_on_vm_result(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
        mock_result: BacktestResult,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}
        mock_result_panel = MagicMock()
        mock_result_panel_cls.return_value = mock_result_panel

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view._on_vm_result(mock_result)

        mock_result_panel.set_result.assert_called_once_with(mock_result)
        assert view.progress_bar.visible is False
        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_dispose(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)

        view.dispose()

        mock_vm.dispose.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_refresh_locale_cascades_to_sub_panels(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        """§5.8 规范 6：refresh_locale 级联调用 result_panel 的 refresh_locale。

        注：config_panel 在 Phase 3.2.5 已重写为声明式组件，通过
        ft.use_state(I18n.get_observable_state) 自动重渲染，不再参与级联。
        """
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        mock_config_panel = MagicMock()
        mock_config_panel_cls.return_value = mock_config_panel
        mock_result_panel = MagicMock()
        mock_result_panel_cls.return_value = mock_result_panel

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view.refresh_locale()

        # config_panel 已是声明式组件，不再级联；仅 result_panel 仍为命令式需级联
        mock_config_panel.refresh_locale.assert_not_called()
        mock_result_panel.refresh_locale.assert_called_once()
        view.update.assert_called_once()

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_refresh_locale_swallows_exception(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
        caplog,
    ) -> None:
        """refresh_locale 异常时不应抛出，应降级为 logger.warning。"""
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {}

        view = BacktestView(mock_page)
        view.page = mock_page
        # 实例化完成后再让 I18n.get 抛异常，触发 refresh_locale 的 try/except
        mock_i18n.side_effect = RuntimeError("i18n boom")

        with caplog.at_level(logging.WARNING, logger=view_logger.name):
            # 不应抛出异常
            view.refresh_locale()

        assert any("refresh_locale error" in r.message and "i18n boom" in r.message for r in caplog.records)

    @patch("ui.views.backtest_view.BacktestViewModel")
    @patch("ui.views.backtest_view.BacktestConfigPanel")
    @patch("ui.views.backtest_view.BacktestResultPanel")
    @patch("ui.views.backtest_view.I18n.get")
    def test_refresh_locale_preserves_dropdown_value(
        self,
        mock_i18n: MagicMock,
        mock_result_panel_cls: MagicMock,
        mock_config_panel_cls: MagicMock,
        mock_vm_cls: MagicMock,
        mock_page: MagicMock,
    ) -> None:
        """§5.8 规范 4：refresh_locale 重建 options 后 value 必须保留。"""
        mock_i18n.return_value = "mock_text"
        mock_vm = MagicMock()
        mock_vm_cls.return_value = mock_vm
        mock_vm.get_available_strategies.return_value = {"strategy1": "策略1"}

        view = BacktestView(mock_page)
        view.page = mock_page
        view.update = MagicMock()

        view.strategy_dropdown.value = "strategy1"
        original_value = view.strategy_dropdown.value
        view.refresh_locale()

        assert view.strategy_dropdown.value == original_value
        assert view.strategy_dropdown.options is not None
        assert len(view.strategy_dropdown.options) > 0
