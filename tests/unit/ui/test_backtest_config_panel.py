"""BacktestConfigPanel 单元测试"""

from datetime import date, timedelta
from unittest.mock import MagicMock, PropertyMock, patch

import flet as ft
import pytest

from ui.components.backtest.backtest_config_panel import BacktestConfigPanel


@pytest.fixture
def mock_page() -> MagicMock:
    page = MagicMock()
    page.open_dialog = MagicMock()
    return page


@pytest.fixture
def mock_callback() -> MagicMock:
    return MagicMock()


@pytest.fixture
def panel(mock_callback: MagicMock) -> BacktestConfigPanel:
    with patch("ui.components.backtest.backtest_config_panel.I18n.get") as mock_i18n:
        mock_i18n.return_value = "mock_text"
        return BacktestConfigPanel(on_run_backtest=mock_callback)


class TestBacktestConfigPanel:
    def test_init_with_callback(self, mock_callback: MagicMock) -> None:
        with patch("ui.components.backtest.backtest_config_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            panel = BacktestConfigPanel(on_run_backtest=mock_callback)

        assert panel.on_run_backtest == mock_callback
        assert panel._strategy_key is None

    def test_init_with_strategy_key(self, mock_callback: MagicMock) -> None:
        with patch("ui.components.backtest.backtest_config_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            panel = BacktestConfigPanel(on_run_backtest=mock_callback, strategy_key="test_strategy")

        assert panel._strategy_key == "test_strategy"

    def test_build_content(self, panel: BacktestConfigPanel) -> None:
        content = panel._build_content()

        assert isinstance(content, ft.Column)
        assert len(content.controls) > 0

    def test_get_config_default_values(self, panel: BacktestConfigPanel) -> None:
        panel.initial_capital_input.value = "1000000"
        panel.max_position_input.value = "50"
        panel.rebalance_dropdown.value = "signal"
        panel.commission_slider.value = 3
        panel.stamp_duty_auto_checkbox.value = False
        panel.stamp_duty_slider.value = 1
        panel.slippage_slider.value = 5

        config = panel.get_config()

        assert config["initial_capital"] == 1_000_000.0
        assert config["max_position_count"] == 50
        assert config["rebalance_freq"] == "signal"
        assert config["commission_rate"] == 3 / 10000
        assert config["stamp_duty_rate"] == 1 / 1000
        assert config["slippage_bps"] == 5.0

    def test_get_config_custom_values(self, panel: BacktestConfigPanel) -> None:
        panel.initial_capital_input.value = "500000"
        panel.max_position_input.value = "30"
        panel.rebalance_dropdown.value = "weekly"
        panel.commission_slider.value = 5
        panel.stamp_duty_auto_checkbox.value = False
        panel.stamp_duty_slider.value = 2
        panel.slippage_slider.value = 10

        config = panel.get_config()

        assert config["initial_capital"] == 500_000.0
        assert config["max_position_count"] == 30
        assert config["rebalance_freq"] == "weekly"
        assert config["commission_rate"] == 5 / 10000
        assert config["stamp_duty_rate"] == 2 / 1000
        assert config["slippage_bps"] == 10.0

    def test_get_config_invalid_initial_capital(self, panel: BacktestConfigPanel) -> None:
        panel.initial_capital_input.value = "invalid_number"

        config = panel.get_config()

        assert config["initial_capital"] == 1_000_000.0

    def test_get_config_invalid_max_positions(self, panel: BacktestConfigPanel) -> None:
        panel.max_position_input.value = "invalid_number"

        config = panel.get_config()

        assert config["max_position_count"] == 50

    def test_get_config_empty_initial_capital(self, panel: BacktestConfigPanel) -> None:
        panel.initial_capital_input.value = ""

        config = panel.get_config()

        assert config["initial_capital"] == 1_000_000.0

    def test_get_config_empty_max_positions(self, panel: BacktestConfigPanel) -> None:
        panel.max_position_input.value = ""

        config = panel.get_config()

        assert config["max_position_count"] == 50

    def test_get_config_slider_fallback_values(self, panel: BacktestConfigPanel) -> None:
        panel.commission_slider.value = 0
        panel.stamp_duty_auto_checkbox.value = False
        panel.stamp_duty_slider.value = 1
        panel.slippage_slider.value = 0

        config = panel.get_config()

        assert config["commission_rate"] == 3 / 10000
        assert config["stamp_duty_rate"] == 1 / 1000
        assert config["slippage_bps"] == 5.0

    def test_get_config_none_rebalance_freq(self, panel: BacktestConfigPanel) -> None:
        panel.rebalance_dropdown.value = None

        config = panel.get_config()

        assert config["rebalance_freq"] == "signal"

    def test_on_run_click_with_callback(self, panel: BacktestConfigPanel, mock_callback: MagicMock) -> None:
        mock_event = MagicMock()

        panel._on_run_click(mock_event)

        mock_callback.assert_called_once()
        config = mock_callback.call_args[0][0]
        assert "start_date" in config
        assert "end_date" in config
        assert "initial_capital" in config

    def test_on_run_click_without_callback(self) -> None:
        with patch("ui.components.backtest.backtest_config_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_text"
            panel = BacktestConfigPanel(on_run_backtest=None)

        mock_event = MagicMock()

        panel._on_run_click(mock_event)

    def test_on_start_date_change(self, panel: BacktestConfigPanel, mock_page: MagicMock) -> None:
        panel.page = mock_page
        panel.start_date_btn.page = mock_page
        panel.start_date_btn.update = MagicMock()

        new_date = date(2024, 6, 1)
        mock_event = MagicMock()
        mock_event.control.value = new_date

        panel._on_start_date_change(mock_event)

        assert panel.start_date_value == new_date
        assert panel.start_date_btn.text == "2024-06-01"
        panel.start_date_btn.update.assert_called_once()

    def test_on_start_date_change_none_value(self, panel: BacktestConfigPanel) -> None:
        original_date = panel.start_date_value
        mock_event = MagicMock()
        mock_event.control.value = None

        panel._on_start_date_change(mock_event)

        assert panel.start_date_value == original_date

    def test_on_end_date_change(self, panel: BacktestConfigPanel, mock_page: MagicMock) -> None:
        panel.page = mock_page
        panel.end_date_btn.page = mock_page
        panel.end_date_btn.update = MagicMock()

        new_date = date(2024, 12, 31)
        mock_event = MagicMock()
        mock_event.control.value = new_date

        panel._on_end_date_change(mock_event)

        assert panel.end_date_value == new_date
        assert panel.end_date_btn.text == "2024-12-31"
        panel.end_date_btn.update.assert_called_once()

    def test_on_end_date_change_none_value(self, panel: BacktestConfigPanel) -> None:
        original_date = panel.end_date_value
        mock_event = MagicMock()
        mock_event.control.value = None

        panel._on_end_date_change(mock_event)

        assert panel.end_date_value == original_date

    def test_set_strategy_key(self, panel: BacktestConfigPanel) -> None:
        panel.set_strategy_key("new_strategy")

        assert panel._strategy_key == "new_strategy"

    def test_default_date_range(self, panel: BacktestConfigPanel) -> None:
        today = date.today()
        one_year_ago = today - timedelta(days=365)

        assert panel.start_date_value == one_year_ago
        assert panel.end_date_value == today

    def test_on_stamp_duty_auto_change_to_auto(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_slider.update = MagicMock()
        panel.stamp_duty_text.update = MagicMock()
        panel.page = MagicMock()

        mock_event = MagicMock()
        mock_event.control.value = True

        with patch("ui.components.backtest.backtest_config_panel.I18n.get") as mock_i18n:
            mock_i18n.return_value = "mock_auto_text"
            panel._on_stamp_duty_auto_change(mock_event)

        assert panel.stamp_duty_slider.disabled is True
        assert panel.stamp_duty_text.value == "mock_auto_text"
        panel.stamp_duty_slider.update.assert_called_once()
        panel.stamp_duty_text.update.assert_called_once()

    def test_on_stamp_duty_auto_change_to_manual(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_slider.value = 1.0
        panel.stamp_duty_slider.update = MagicMock()
        panel.stamp_duty_text.update = MagicMock()
        panel.page = MagicMock()

        mock_event = MagicMock()
        mock_event.control.value = False

        panel._on_stamp_duty_auto_change(mock_event)

        assert panel.stamp_duty_slider.disabled is False
        assert panel.stamp_duty_text.value == "1.0‰"
        panel.stamp_duty_slider.update.assert_called_once()
        panel.stamp_duty_text.update.assert_called_once()

    def test_on_stamp_duty_auto_change_with_page(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_slider.update = MagicMock()
        panel.stamp_duty_text.update = MagicMock()
        panel.page = MagicMock()

        mock_event = MagicMock()
        mock_event.control.value = True

        panel._on_stamp_duty_auto_change(mock_event)

        panel.stamp_duty_slider.update.assert_called_once()
        panel.stamp_duty_text.update.assert_called_once()

    def test_on_stamp_duty_auto_change_without_page(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_slider.update = MagicMock()
        panel.stamp_duty_text.update = MagicMock()
        panel.page = None

        mock_event = MagicMock()
        mock_event.control.value = True

        panel._on_stamp_duty_auto_change(mock_event)

        panel.stamp_duty_slider.update.assert_not_called()
        panel.stamp_duty_text.update.assert_not_called()

    def test_on_stamp_duty_slider_change_manual(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_auto_checkbox.value = False
        panel.stamp_duty_text.update = MagicMock()
        panel.page = MagicMock()

        mock_event = MagicMock()
        mock_event.control.value = 1.5

        panel._on_stamp_duty_slider_change(mock_event)

        assert panel.stamp_duty_text.value == "1.5‰"
        panel.stamp_duty_text.update.assert_called_once()

    def test_on_stamp_duty_slider_change_auto_mode(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_auto_checkbox.value = True
        panel.stamp_duty_text.update = MagicMock()
        panel.page = MagicMock()

        mock_event = MagicMock()
        mock_event.control.value = 1.5

        panel._on_stamp_duty_slider_change(mock_event)

        panel.stamp_duty_text.update.assert_not_called()

    def test_on_stamp_duty_slider_change_without_page(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_auto_checkbox.value = False
        panel.stamp_duty_text.update = MagicMock()
        panel.page = None

        mock_event = MagicMock()
        mock_event.control.value = 1.5

        panel._on_stamp_duty_slider_change(mock_event)

        assert panel.stamp_duty_text.value == "1.5‰"
        panel.stamp_duty_text.update.assert_not_called()

    def test_get_config_stamp_duty_auto_true(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_auto_checkbox.value = True

        config = panel.get_config()

        assert config["stamp_duty_rate"] is None

    def test_get_config_stamp_duty_slider_none(self, panel: BacktestConfigPanel) -> None:
        panel.stamp_duty_auto_checkbox.value = False
        type(panel.stamp_duty_slider).value = PropertyMock(return_value=None)

        config = panel.get_config()

        assert config["stamp_duty_rate"] is None

        del type(panel.stamp_duty_slider).value
