from typing import Any
from unittest.mock import MagicMock, create_autospec

import flet as ft
import pytest

from core.i18n import DEFAULT_LOCALE
from tests.unit.ui.mock_flet import MockFletPage
from ui.i18n import I18nState
from ui.theme import AppColors, AppColorsState, AppStyles, ThemeName
from utils.config_handler import ConfigHandler


@pytest.fixture(autouse=True)
def _v1_page_compat(monkeypatch):
    """Per-test V1 page 兼容桩（替代旧 mock_flet 全局桩，方案 §3.3.1）。

    V1 中 ``ft.Control.page`` 改为只读 property（通过 ``parent`` 链查找），
    ``Control.update()`` 要求控件已挂载。本 fixture 用 monkeypatch 作用域隔离地
    恢复 V0 兼容行为：page 可读写、未挂载 ``update()`` 静默返回。

    测试代码用 ``control.page = mock_page`` 注入 page（替代已删除的 page 注入 helper）。
    """
    # Any: fget 在 V1 只读 property 类型存根中推断为 None，运行时必为可调用对象
    original_page_get: Any = ft.Control.page.fget
    original_update: Any = ft.Control.update

    @property
    def page(self) -> ft.Page | None:
        mock_page = self.__dict__.get("_mock_page")
        if mock_page is not None:
            return mock_page
        try:
            return original_page_get(self)
        except RuntimeError:
            return None

    @page.setter
    def page(self, value: ft.Page | None) -> None:
        self.__dict__["_mock_page"] = value

    def update(self) -> None:
        if self.__dict__.get("_mock_page") is None:
            try:
                original_page_get(self)
            except RuntimeError:
                return
        original_update(self)

    monkeypatch.setattr(ft.Control, "page", page)
    monkeypatch.setattr(ft.Control, "update", update)


@pytest.fixture(autouse=True)
def _reset_context_page():
    """每个测试后清理 _context_page ContextVar，防止 FakePage 跨测试泄漏。

    ``attach_fake_page``（见 ``tests/unit/ui/component_renderer.py``）调用
    ``_context_page.set(FakePage)`` 修改 ContextVar，若不清理会跨测试泄漏，
    导致后续 UI 测试因 page 类型不匹配而失败。
    """
    yield
    from flet.controls.context import _context_page

    _context_page.set(None)


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
def mock_i18n_state(monkeypatch):
    """MockI18nState 注入 fixture：可控 locale 的 I18nState 实例（方案 §3.3.1）。

    声明式组件通过 ``ft.use_state(get_observable_state)`` 订阅，
    本 fixture 用 monkeypatch 注入 ``ui.i18n._i18n_state``，让 ``get_observable_state()``
    返回此 mock 实例。替代旧 ``I18n.subscribe`` mock（命令式 View 用，阶段 4 删除）。

    单测中 ``render_component`` 绕过 Renderer，``use_state`` 不会真正订阅，
    但 ``get_observable_state()`` 仍被调用以读取初始 locale。
    """
    state = I18nState(locale=DEFAULT_LOCALE)
    monkeypatch.setattr("ui.i18n._i18n_state", state)
    return state


@pytest.fixture
def mock_app_colors_state(monkeypatch):
    """MockAppColorsState 注入 fixture：可控 theme_name 的 AppColorsState 实例（方案 §3.3.1 H2）。

    声明式组件通过 ``ft.use_state(AppColors.get_observable_state)`` 订阅，
    本 fixture 用 monkeypatch 注入 ``AppColors._state``，让 ``get_observable_state()``
    返回此 mock 实例。替代旧 ``AppColors._listeners`` mock（命令式 View 用，阶段 4 删除）。
    """
    state = AppColorsState(theme_name=ThemeName.DARK)
    monkeypatch.setattr(AppColors, "_state", state)
    return state


@pytest.fixture
def mock_app_colors(mock_app_colors_state):
    """基于 create_autospec 的 AppColors mock（含 Observable state 注入，H2）。

    方法签名跟随 AppColors 类定义；颜色 token 从真实类复制，
    确保 token 重命名/删除时测试立即失败（P2-2）。

    依赖 ``mock_app_colors_state`` 注入 ``AppColors._state``，让声明式组件
    通过 ``AppColors.get_observable_state()`` 拿到 mock state（方案 §3.2 H2）。
    命令式 View 旧测试访问 ``mock_app_colors.subscribe``/``load_theme`` 不受影响。
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
        if isinstance(val, (str, int, float, dict)):
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
    m.is_ai_concept_schedule_enabled.return_value = False
    m.get_ai_concept_schedule_time.return_value = "20:00"
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
    m.get_tushare_point_tier.return_value = "points_5000"
    return m


def wrap_mock_page(mock_page):
    mock_page.show_toast = MagicMock()
    mock_page.run_task = MagicMock()
    mock_page.run_task.return_value = MagicMock()
    return mock_page
