from unittest.mock import MagicMock, create_autospec

import flet as ft
import pytest

from tests.unit.ui.mock_flet import MockFletPage
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler


@pytest.fixture
def mock_page():
    return MockFletPage()


@pytest.fixture
def mock_i18n():
    m = MagicMock()
    m.get.side_effect = lambda key, *a, **kw: key
    m.get_language_options.return_value = [("zh_CN", "中文"), ("en_US", "English")]
    m.get_language_label.return_value = "语言 / Language"
    m.subscribe = MagicMock(return_value="sub_id")
    m.unsubscribe = MagicMock()
    return m


@pytest.fixture
def mock_app_colors():
    """基于 create_autospec 的 AppColors mock。

    方法签名跟随 AppColors 类定义；颜色 token 从真实类复制，
    确保 token 重命名/删除时测试立即失败（P2-2）。
    """
    m = create_autospec(AppColors, instance=True)
    # 复制所有公开数据属性（颜色字符串、常量），保持与生产单一真相源对齐
    for attr in dir(AppColors):
        if attr.startswith("_"):
            continue
        val = getattr(AppColors, attr, None)
        if isinstance(val, (str, int, float)):
            setattr(m, attr, val)
    return m


@pytest.fixture
def mock_app_styles():
    """基于 create_autospec 的 AppStyles mock。

    方法签名跟随 AppStyles 类定义；尺寸常量从真实类复制（P2-2）。
    """
    m = create_autospec(AppStyles, instance=True)
    for attr in dir(AppStyles):
        if attr.startswith("_"):
            continue
        val = getattr(AppStyles, attr, None)
        if isinstance(val, (str, int, float)):
            setattr(m, attr, val)
    # 样式工厂方法返回真实 ButtonStyle/dict，避免组件渲染时类型不匹配
    m.primary_button = MagicMock(return_value=ft.ButtonStyle())
    m.outline_button = MagicMock(return_value=ft.ButtonStyle())
    m.accent_button = MagicMock(return_value=ft.ButtonStyle())
    m.secondary_button = MagicMock(return_value=ft.ButtonStyle())
    m.dashboard_card = MagicMock(return_value={"padding": 10})
    m.card = MagicMock(return_value={"padding": 10})
    m.data_table_row = MagicMock(return_value="#1E1E1E")
    m.price_change_color = MagicMock(return_value="#4CAF50")
    return m


@pytest.fixture
def mock_config_handler():
    """基于 create_autospec 的 ConfigHandler mock。

    P2-2b: 使用 create_autospec 替代裸 MagicMock，使 mock 自动跟随
    ConfigHandler 的方法签名。生产侧重命名/删除方法时，mock 访问会
    立即抛 AttributeError，避免静默失效。
    仅对测试真正读取的方法赋具体返回值，其余方法返回带 spec 的 MagicMock。
    """
    m = create_autospec(ConfigHandler, instance=False)
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
    m.get_tushare_point_tier.return_value = "custom"
    return m


def set_page(control, page):
    control._Control__page = page


def wrap_mock_page(mock_page):
    mock_page.show_toast = MagicMock()
    mock_page.run_task = MagicMock()
    mock_page.run_task.return_value = MagicMock()
    return mock_page
