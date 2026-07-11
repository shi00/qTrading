"""ui/app_layout.py 声明式契约守护测试 (Phase F.4).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref/
   _view_cache/PageRefMixin/schedule_resize/_handle_resize/_on_locale_change/refresh_locale)
2. 模块级纯函数测试 (_build_view/_build_nav_destinations/_get_page)

业务逻辑覆盖 (tab 切换 + resize 防抖 + 子视图消费) 由集成测试
(flet_test_page fixture) 承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

import contextlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码,用于契约守护检查。

    避免源码 docstring 中提及被禁止的方法名 (作为变更说明) 导致字符串匹配误判。
    """
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module) -> None:
        body = getattr(node, "body", None)
        if not body:
            return
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
            end_lineno = first.end_lineno or first.lineno
            docstring_lines.update(range(first.lineno, end_lineno + 1))

    _collect(tree)  # type: ignore[arg-type]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            _collect(node)  # type: ignore[arg-type]

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.app_layout as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.app_layout as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (AppLayout)
# ============================================================================


class TestAppLayoutContract:
    """AppLayout 声明式契约守护测试 (Phase F.4)。"""

    def test_app_layout_is_ft_component(self):
        """DoD: AppLayout 必须被 @ft.component 装饰。"""
        from ui.app_layout import AppLayout

        assert hasattr(AppLayout, "__wrapped__"), "AppLayout 必须用 @ft.component 装饰"

    def test_app_layout_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "AppLayout 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class AppLayout(" not in _code_source(), "AppLayout 不应是 class (命令式)"

    def test_signature_returns_container(self):
        """DoD: 函数签名必须为 def AppLayout(...) -> ft.Container。"""
        assert "def AppLayout(" in _code_source(), "必须是函数定义"
        assert "-> ft.Container" in _code_source(), "返回类型必须为 ft.Container"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change (声明式用 ft.use_state 自动重渲染)。"""
        assert "_on_locale_change" not in _code_source(), "不应使用 _on_locale_change (声明式自动重渲染)"

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme (声明式通过 Observable state 自动重渲染)。"""
        assert "update_theme" not in _code_source(), "不应使用 update_theme (声明式自动重渲染)"

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale (声明式自动重渲染)。"""
        assert "refresh_locale" not in _code_source(), "不应使用 refresh_locale (声明式自动重渲染)"

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联 (子组件自管)。"""
        assert "handle_resize" not in _code_source(), "不应使用 handle_resize (命令式)"

    def test_no_schedule_resize(self):
        """DoD: 禁止命令式 schedule_resize (声明式用 use_effect + page.on_resize)。"""
        assert "schedule_resize" not in _code_source(), "不应使用 schedule_resize (命令式)"

    def test_no_handle_resize_method(self):
        """DoD: 禁止命令式 _handle_resize 防抖方法 (声明式用 use_effect 闭包防抖)。"""
        assert "_handle_resize" not in _code_source(), "不应使用 _handle_resize (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

    def test_no_page_ref_mixin(self):
        """DoD: 禁止 PageRefMixin (最后一个历史控件消除, 用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"

    def test_no_page_ref(self):
        """DoD: 禁止 _page_ref / weakref (用 ft.context.page)。"""
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

    def test_no_view_cache(self):
        """DoD: 禁止 _view_cache 命令式视图缓存 (声明式直接函数调用消费)。"""
        assert "_view_cache" not in _code_source(), "不应使用 _view_cache (声明式直接函数调用)"

    def test_no_use_ref_cache(self):
        """DoD: 禁止 use_ref cache 命令式实例。"""
        assert "ft.use_ref" not in _code_source(), "不应直接使用 ft.use_ref"

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 I18n.get_observable_state (i18n 自动重渲染)。"""
        assert "I18n.get_observable_state" in _raw_source(), "必须订阅 I18n.get_observable_state"

    def test_subscribes_theme(self):
        """DoD: 必须订阅 AppColors.get_observable_state (theme 自动重渲染)。"""
        assert "AppColors.get_observable_state" in _raw_source(), "必须订阅 AppColors.get_observable_state"

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page (try/except 守卫)。"""
        assert "ft.context.page" in _code_source(), "page 访问必须通过 ft.context.page"

    def test_uses_use_effect_for_resize(self):
        """DoD: resize 必须用 use_effect + page.on_resize。"""
        assert "ft.use_effect" in _code_source(), "必须使用 ft.use_effect 设置 resize"
        assert "page.on_resize" in _code_source(), "必须设置 page.on_resize"

    def test_consumes_subviews_via_function_call(self):
        """DoD: 6 个子视图必须用函数调用消费 (HomeView()/ScreenerView()/...)。"""
        source = _code_source()
        for view_name in [
            "HomeView()",
            "ScreenerView()",
            "BacktestView()",
            "DataExplorerView()",
            "TaskCenterView()",
            "SettingsView()",
        ]:
            assert view_name in source, f"必须函数调用消费 {view_name}"

    def test_no_change_tab_method(self):
        """DoD: 禁止命令式 change_tab / _execute_tab_switch (声明式用 use_state)。"""
        assert "def change_tab" not in _code_source(), "不应使用 change_tab (声明式用 use_state)"
        assert "_execute_tab_switch" not in _code_source(), "不应使用 _execute_tab_switch (命令式)"

    def test_no_run_strategy_from_home(self):
        """DoD: 禁止命令式 run_strategy_from_home (HomeView 不再使用此回调)。"""
        assert "run_strategy_from_home" not in _code_source(), "不应使用 run_strategy_from_home (命令式)"

    def test_no_show_method(self):
        """DoD: 禁止命令式 show() 方法 (声明式由消费方 page.add 挂载)。"""
        assert "def show" not in _code_source(), "不应使用 show() 方法 (声明式由消费方挂载)"

    def test_no_init_ui_method(self):
        """DoD: 禁止命令式 _init_ui 方法 (声明式在函数体内直接渲染)。"""
        assert "_init_ui" not in _code_source(), "不应使用 _init_ui (命令式)"

    def test_app_layout_signature_no_page_param(self):
        """DoD: AppLayout 签名不应包含 page 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.app_layout import AppLayout

        sig = inspect.signature(AppLayout.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page" not in params, "AppLayout 不应接收 page 参数"
        assert "page_ref" not in params, "AppLayout 不应接收 page_ref 参数"


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestBuildView:
    """_build_view 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.patches = [
            patch("ui.app_layout.I18n", self.mock_i18n),
            patch("ui.app_layout.HomeView", MagicMock()),
            patch("ui.app_layout.ScreenerView", MagicMock()),
            patch("ui.app_layout.BacktestView", MagicMock()),
            patch("ui.app_layout.DataExplorerView", MagicMock()),
            patch("ui.app_layout.TaskCenterView", MagicMock()),
            patch("ui.app_layout.SettingsView", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_market_tab_returns_home_view(self):
        """NavTabs.MARKET 索引返回 HomeView() 实例。"""
        from ui.app_layout import NavTabs, _build_view

        view = _build_view(NavTabs.MARKET)
        assert view is not None

    def test_screener_tab_returns_screener_view(self):
        """NavTabs.SCREENER 索引返回 ScreenerView() 实例。"""
        from ui.app_layout import NavTabs, _build_view

        view = _build_view(NavTabs.SCREENER)
        assert view is not None

    def test_all_tabs_return_non_none(self):
        """所有 NavTabs 索引返回非 None 对象。"""
        from ui.app_layout import NavTabs, _build_view

        for tab in NavTabs:
            view = _build_view(int(tab))
            assert view is not None, f"tab {tab.name} 应返回非 None 视图"

    def test_unknown_index_returns_text(self):
        """未知索引返回 ft.Text 兜底。"""
        from ui.app_layout import _build_view

        view = _build_view(99)
        assert isinstance(view, ft.Text)


class TestBuildNavDestinations:
    """_build_nav_destinations 模块级纯函数测试。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        self.patches = [
            patch("ui.app_layout.I18n", self.mock_i18n),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def test_returns_six_destinations(self):
        """返回 6 个 NavigationRailDestination (market/screener/backtest/data/tasks/settings)。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        assert len(destinations) == 6

    def test_destinations_are_correct_type(self):
        """返回值必须是 ft.NavigationRailDestination 实例。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        for dest in destinations:
            assert isinstance(dest, ft.NavigationRailDestination)

    def test_labels_match_nav_keys(self):
        """每个 destination 的 label 文本对应 i18n key。"""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations()
        expected_keys = [
            "nav_market",
            "nav_screener",
            "nav_backtest",
            "nav_data",
            "nav_tasks",
            "nav_settings",
        ]
        for dest, key in zip(destinations, expected_keys, strict=True):
            # label 是 ft.Text 控件, 文本通过 .value 访问
            assert dest.label.value == key


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        """ft.context.page 可用时返回 page 实例。"""
        from ui.app_layout import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.app_layout.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.app_layout import _get_page

        with patch("ui.app_layout.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None


class TestNavTabs:
    """NavTabs IntEnum 契约测试。"""

    def test_nav_tabs_has_six_members(self):
        """NavTabs 必须有 6 个成员 (MARKET/SCREENER/BACKTEST/DATA/TASKS/SETTINGS)。"""
        from ui.app_layout import NavTabs

        assert len(NavTabs) == 6

    def test_nav_tabs_values_are_sequential(self):
        """NavTabs 值必须从 0 开始连续 (NavigationRail selected_index 依赖)。"""
        from ui.app_layout import NavTabs

        values = [int(tab) for tab in NavTabs]
        assert values == [0, 1, 2, 3, 4, 5]
