from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.mock_flet import MockFletPage


@pytest.fixture
def mock_page():
    return MockFletPage()


@pytest.fixture
def mock_i18n():
    m = MagicMock()
    m.get.side_effect = lambda key, *a, **kw: key
    m.subscribe = MagicMock(return_value="sub_id")
    m.unsubscribe = MagicMock()
    return m


@pytest.fixture
def mock_app_colors():
    m = MagicMock()
    m.TEXT_PRIMARY = "#FFF"
    m.TEXT_SECONDARY = "#AAA"
    m.TEXT_ON_PRIMARY = "#FFF"
    m.TEXT_HINT = "#888"
    m.PRIMARY = "#6750A4"
    m.PRIMARY_DARK = "#0D47A1"
    m.PRIMARY_LIGHT = "#BBDEFB"
    m.ERROR = "#F44336"
    m.SUCCESS = "#4CAF50"
    m.WARNING = "#FF9800"
    m.INFO = "#2196F3"
    m.ACCENT = "#BB86FC"
    m.ACCENT_HOVER = "#004D40"
    m.DIVIDER = "#333"
    m.BORDER = "#444"
    m.SURFACE = "#1E1E1E"
    m.SURFACE_VARIANT = "#2D2D2D"
    m.BACKGROUND = "#121212"
    m.INPUT_BG = "#2D2D2D"
    m.INPUT_BORDER = "#424242"
    m.INPUT_TEXT = "#FFFFFF"
    m.UP = "#F44336"
    m.DOWN = "#4CAF50"
    m.UP_RED = "#F44336"
    m.DOWN_GREEN = "#4CAF50"
    m.RISE = "#F44336"
    m.FALL = "#4CAF50"
    m.TABLE_HEADER_BG = "#252526"
    m.TABLE_HEADER_TEXT = "#E0E0E0"
    m.TABLE_ROW_ODD = "#1E1E1E"
    m.TABLE_ROW_EVEN = "#181818"
    m.TABLE_CELL_TEXT = "#CCCCCC"
    m.TABLE_CELL_NUMERIC = "#FFFFFF"
    m.TABLE_BORDER = "#333333"
    m.TABLE_GRID = "#2C2C2C"
    m.TABLE_GRID_V = "#2C2C2C"
    m.TABLE_GRID_H = "#2C2C2C"
    m.LOG_BG = "#000000"
    m.LOG_TEXT = "#CCCCCC"
    m.subscribe = MagicMock()
    m.unsubscribe = MagicMock()
    return m


@pytest.fixture
def mock_app_styles():
    m = MagicMock()
    m.CONTROL_WIDTH_MD = 300
    m.CONTROL_WIDTH_SM = 150
    m.CONTROL_WIDTH_LG = 400
    m.primary_button.return_value = ft.ButtonStyle()
    m.outline_button.return_value = ft.ButtonStyle()
    m.accent_button.return_value = ft.ButtonStyle()
    m.secondary_button.return_value = ft.ButtonStyle()
    m.dashboard_card.return_value = {"padding": 10}
    m.card.return_value = {"padding": 10}
    return m


@pytest.fixture
def mock_config_handler():
    m = MagicMock()
    m.is_auto_update_enabled.return_value = False
    m.get_auto_update_time.return_value = "16:30"
    m.is_doubao_schedule_enabled.return_value = False
    m.get_doubao_schedule_time.return_value = "20:00"
    m.get_tushare_api_limit.return_value = 200
    m.get_no_proxy_domains.return_value = []
    m.get_max_cpu_workers.return_value = 4
    m.get_db_connection_pool_size.return_value = 5
    m.get_db_max_overflow.return_value = 10
    m.get_db_pool_timeout.return_value = 30
    m.get_ai_max_candidates.return_value = 30
    m.get_strategy_min_turnover.return_value = 2.0
    m.get_ai_max_concurrent_analysis.return_value = 3
    m.get_ai_system_prompt.return_value = "prompt"
    m.get_ai_news_prompt.return_value = "news"
    m.get_init_history_years.return_value = 3
    m.get_sync_max_concurrent_heavy.return_value = 4
    m.get_log_level.return_value = "INFO"
    m.get_theme_name.return_value = "dark"
    return m


def set_page(control, page):
    control._Control__page = page


def wrap_mock_page(mock_page):
    mock_page.show_toast = MagicMock()
    mock_page.run_task = MagicMock()
    mock_page.run_task.return_value = MagicMock()
    return mock_page
