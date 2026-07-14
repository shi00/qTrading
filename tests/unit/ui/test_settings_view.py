"""ui/views/settings_view.py 声明式契约守护测试 (Phase C.3).

View 组合（@ft.component + use_state）由集成测试覆盖（flet_test_page fixture）。
本单元测试聚焦：
- 契约守护（grep 检查禁止的命令式模式）
- 纯辅助函数（_get_tab_button_style / _show_snack_impl / _build_tabs）行为
参照 test_settings_widgets.py / test_task_center_view.py 模式。
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from ui.theme import AppColors

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
    import ast

    tree = ast.parse(source)
    docstring_lines: set[int] = set()

    def _collect(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef | ast.Module,
    ) -> None:
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
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.views.settings_view as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.settings_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


def _trigger_callback(cb, event):
    """Safely trigger Flet optional callback in tests.

    Flet stubs declare callbacks (on_click/on_change/on_horizontal_drag_*/etc.)
    as Optional[Callable[[], None]], but runtime passes a ControlEvent.
    Centralize type narrowing + type: ignore here.
    """
    assert cb is not None
    cb(event)  # type: ignore[reportCallIssue, reason: Flet stub declares callbacks as 0-arg, but runtime passes event]


# ---------------------------------------------------------------------------
# 契约守护：声明式范式
# ---------------------------------------------------------------------------


class TestSettingsViewContract:
    """声明式契约守护：禁止命令式 API + 强制声明式范式。"""

    def test_settings_view_is_ft_component(self):
        """DoD: SettingsView 必须 @ft.component 装饰。"""
        from ui.views.settings_view import SettingsView

        assert hasattr(SettingsView, "__wrapped__"), "SettingsView 必须用 @ft.component 装饰"

    def test_no_class_inheritance(self):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert "class SettingsView(" not in _code_source()

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_safe_update(self):
        """DoD: 禁止命令式 _safe_update。"""
        assert "_safe_update" not in _code_source()

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale（声明式自动重渲染）。"""
        assert "refresh_locale" not in _code_source()

    def test_no_handle_resize(self):
        """DoD: 禁止命令式 handle_resize 级联（子组件自管）。"""
        assert "handle_resize" not in _code_source()

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change（声明式自动重渲染）。"""
        assert "on_locale_change" not in _code_source()

    def test_no_on_theme_change(self):
        """DoD: 禁止命令式 on_theme_change（声明式自动重渲染）。"""
        assert "on_theme_change" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_subscribes_i18n(self):
        """DoD: SettingsView 必须订阅 get_observable_state（i18n 自动重渲染）。"""
        assert "get_observable_state" in _raw_source()

    def test_uses_use_state_for_tab(self):
        """DoD: Tab 切换必须用 use_state 驱动（条件渲染）。"""
        assert "ft.use_state(0)" in _code_source() or "use_state(0)" in _code_source()

    def test_no_use_ref_cache(self):
        """DoD: 禁止 use_ref cache 命令式实例（直接调用子组件函数）。"""
        assert "use_ref" not in _code_source()

    def test_no_page_ref_property(self):
        """DoD: 禁止命令式 page_ref property（声明式用 ft.context.page）。"""
        assert "@property" not in _code_source()
        assert "def page_ref" not in _code_source()

    def test_uses_ft_context_page(self):
        """DoD: page 访问必须通过 ft.context.page（try/except 守卫）。"""
        assert "ft.context.page" in _code_source()


# ---------------------------------------------------------------------------
# 纯函数测试：_get_tab_button_style
# ---------------------------------------------------------------------------


class TestGetTabButtonStyle:
    """Tab 按钮样式工厂：选中/未选中两种状态。"""

    def test_selected_returns_button_style(self):
        style = _get_tab_button_style_safe(is_selected=True)
        assert isinstance(style, ft.ButtonStyle)

    def test_unselected_returns_button_style(self):
        style = _get_tab_button_style_safe(is_selected=False)
        assert isinstance(style, ft.ButtonStyle)


def _get_tab_button_style_safe(is_selected: bool) -> ft.ButtonStyle:
    """Wrap _get_tab_button_style to handle AppColors token resolution in tests."""
    from ui.views.settings_view import _get_tab_button_style

    return _get_tab_button_style(is_selected=is_selected)


# ---------------------------------------------------------------------------
# 纯函数测试：_show_snack_impl
# ---------------------------------------------------------------------------


class TestShowSnack:
    """_show_snack_impl：通过 page 引用触发 toast/snackbar fallback。

    SettingsView 渲染时捕获 page, 闭包调用 _show_snack_impl(page, ...)。
    本测试直接验证 _show_snack_impl 的纯逻辑 (不依赖 ft.context)。
    """

    def test_show_snack_no_page_returns_silently(self):
        """page=None 时静默返回 (对应渲染时 ft.context.page 抛 RuntimeError)."""
        from ui.views.settings_view import _show_snack_impl

        # 应静默返回，不抛异常
        _show_snack_impl(None, "msg")

    def test_show_snack_with_show_toast_info(self):
        """page.show_toast 存在时按默认 info 类型触发。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        mock_page.show_toast = MagicMock()
        _show_snack_impl(mock_page, "hello")
        mock_page.show_toast.assert_called_once_with("hello", type="info")

    def test_show_snack_with_error_color_calls_error(self):
        """color=AppColors.ERROR → msg_type=error。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        mock_page.show_toast = MagicMock()
        _show_snack_impl(mock_page, "err", color=AppColors.ERROR)
        mock_page.show_toast.assert_called_once_with("err", type="error")

    def test_show_snack_with_success_color_calls_success(self):
        """color=AppColors.SUCCESS → msg_type=success。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        mock_page.show_toast = MagicMock()
        _show_snack_impl(mock_page, "ok", color=AppColors.SUCCESS)
        mock_page.show_toast.assert_called_once_with("ok", type="success")

    def test_show_snack_with_warning_color_calls_warning(self):
        """color=AppColors.WARNING → msg_type=warning。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        mock_page.show_toast = MagicMock()
        _show_snack_impl(mock_page, "warn", color=AppColors.WARNING)
        mock_page.show_toast.assert_called_once_with("warn", type="warning")

    def test_show_snack_with_string_color_compat(self):
        """color="success" 字符串 → msg_type=success (兼容 database_tab 调用)。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        mock_page.show_toast = MagicMock()
        _show_snack_impl(mock_page, "saved", color="success")
        mock_page.show_toast.assert_called_once_with("saved", type="success")

    def test_show_snack_no_show_toast_logs_warning(self):
        """page 无 show_toast 方法时，降级为 logger.warning，不调 show_dialog。"""
        from ui.views.settings_view import _show_snack_impl

        mock_page = MagicMock()
        del mock_page.show_toast  # 删除属性，使 hasattr 返回 False
        mock_page.show_dialog = MagicMock()
        with patch("ui.views.settings_view.logger") as mock_logger:
            _show_snack_impl(mock_page, "fallback", color=ft.Colors.BLUE)
            mock_logger.warning.assert_called_once()
            mock_page.show_dialog.assert_not_called()

    def test_show_snack_none_page_returns_silently(self):
        """page 为 None 时静默返回。"""
        from ui.views.settings_view import _show_snack_impl

        # 应静默返回，不抛异常
        _show_snack_impl(None, "msg")


# ---------------------------------------------------------------------------
# 纯函数测试：_build_tabs
# ---------------------------------------------------------------------------


class TestBuildTabs:
    """_build_tabs：实例化 6 个 tabs（patch 命令式 tabs 避免真实实例化）。"""

    @pytest.fixture(autouse=True)
    def _patch_all_tabs(self):
        """Patch 5 个命令式 tabs + 1 个声明式 DatabaseTab，避免真实实例化。"""
        with (
            patch("ui.views.settings_view.DataSourceTab") as self.mock_data,
            patch("ui.views.settings_view.DatabaseTab") as self.mock_db,
            patch("ui.views.settings_view.AIBrainTab") as self.mock_ai,
            patch("ui.views.settings_view.AutomationTab") as self.mock_auto,
            patch("ui.views.settings_view.NotificationsTab") as self.mock_notify,
            patch("ui.views.settings_view.SystemTab") as self.mock_system,
        ):
            self.mock_data.return_value = MagicMock(name="DataSourceTab")
            self.mock_db.return_value = MagicMock(name="DatabaseTab")
            self.mock_ai.return_value = MagicMock(name="AIBrainTab")
            self.mock_auto.return_value = MagicMock(name="AutomationTab")
            self.mock_notify.return_value = MagicMock(name="NotificationsTab")
            self.mock_system.return_value = MagicMock(name="SystemTab")
            yield

    def test_build_tabs_returns_6_tabs(self):
        from ui.views.settings_view import _build_tabs

        tabs = _build_tabs(MagicMock())
        assert len(tabs) == 6

    def test_build_tabs_calls_each_tab_constructor(self):
        from ui.views.settings_view import _build_tabs

        show_snack = MagicMock()
        _build_tabs(show_snack)

        self.mock_data.assert_called_once_with(show_snack)
        self.mock_db.assert_called_once_with(show_snack)
        self.mock_ai.assert_called_once_with(show_snack)
        self.mock_auto.assert_called_once_with(show_snack)
        # NotificationsTab 声明式重写后只接收 show_snack (Phase D.4, 无 page_ref)
        self.mock_notify.assert_called_once_with(show_snack)
        self.mock_system.assert_called_once_with(show_snack)

    def test_build_tabs_notifications_tab_receives_only_show_snack(self):
        """NotificationsTab 声明式重写后只接收 show_snack (Phase D.4, 无 page_ref)。"""
        from ui.views.settings_view import _build_tabs

        _build_tabs(MagicMock())

        self.mock_notify.assert_called_once()
        args = self.mock_notify.call_args[0]
        assert len(args) == 1, "NotificationsTab 声明式重写后不应接收 page_ref"


# ---------------------------------------------------------------------------
# _TAB_CONFIG 配置守护
# ---------------------------------------------------------------------------


class TestTabConfig:
    """_TAB_CONFIG 顺序与 _build_tabs 返回顺序一致。"""

    def test_tab_config_has_6_entries(self):
        from ui.views.settings_view import _TAB_CONFIG

        assert len(_TAB_CONFIG) == 6

    def test_tab_config_entries_are_tuples(self):
        from ui.views.settings_view import _TAB_CONFIG

        for entry in _TAB_CONFIG:
            assert isinstance(entry, tuple)
            assert len(entry) == 2
            key, icon = entry
            assert isinstance(key, str)
            # ft.Icons 是 IntEnum 成员
            assert isinstance(icon, int)  # ft.Icons 是 IntEnum


# ---------------------------------------------------------------------------
# 组件体测试: SettingsView (覆盖 107-189 行 @ft.component 函数体)
# ---------------------------------------------------------------------------


def _collect_controls(root: object) -> list[ft.Control]:
    """深度优先遍历控件树, 返回所有 ft.Control 实例。

    跳过 MagicMock / 非 ft.Control 对象 (避免无限递归, 见 tab_body 中 mock tab)。
    """
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[ft.Control] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_collect_controls(child))
    content = getattr(root, "content", None)
    if content is not None:
        result.extend(_collect_controls(content))
    return result


def _find_buttons(root: object) -> list[ft.Button]:
    """查找所有 ft.Button (tab_buttons)。"""
    return [c for c in _collect_controls(root) if isinstance(c, ft.Button)]


def _make_tab_event(data: object) -> MagicMock:
    """构造 fake ControlEvent, control.data = data。"""
    e = MagicMock()
    e.control = MagicMock()
    e.control.data = data
    return e


class TestSettingsViewComponentBody:
    """SettingsView 组件体测试: 渲染结构 + tab 切换 + show_snack 闭包。"""

    @pytest.fixture(autouse=True)
    def _patch_all_tabs(self):
        """Patch 6 个 tabs 避免真实实例化。"""
        with (
            patch("ui.views.settings_view.DataSourceTab") as self.mock_data,
            patch("ui.views.settings_view.DatabaseTab") as self.mock_db,
            patch("ui.views.settings_view.AIBrainTab") as self.mock_ai,
            patch("ui.views.settings_view.AutomationTab") as self.mock_auto,
            patch("ui.views.settings_view.NotificationsTab") as self.mock_notify,
            patch("ui.views.settings_view.SystemTab") as self.mock_system,
        ):
            self.mock_data.return_value = MagicMock(name="DataSourceTab")
            self.mock_db.return_value = MagicMock(name="DatabaseTab")
            self.mock_ai.return_value = MagicMock(name="AIBrainTab")
            self.mock_auto.return_value = MagicMock(name="AutomationTab")
            self.mock_notify.return_value = MagicMock(name="NotificationsTab")
            self.mock_system.return_value = MagicMock(name="SystemTab")
            yield

    def _mount(self, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import (
            make_component,
            render_once,
            run_mount_effects,
        )
        from ui.views.settings_view import SettingsView

        component = make_component(SettingsView)
        run_mount_effects(component)
        return component, render_once(component)

    def test_mount_returns_container(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """挂载 SettingsView 返回 ft.Container。"""
        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        assert isinstance(result, ft.Container)

    def test_mount_renders_six_tab_buttons(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """挂载后 tab_bar 包含 6 个 ft.Button (每个 tab 一个)。"""
        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        assert len(buttons) == 6

    def test_mount_first_tab_selected_by_default(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """默认 current_tab=0, 第一个 button 应使用 selected 样式 (bgcolor=PRIMARY)。"""
        from ui.theme import AppColors

        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        # 第一个 button 是 selected, style.bgcolor 应为 AppColors.PRIMARY
        first_style = buttons[0].style
        assert first_style is not None
        # ButtonStyle.bgcolor 在 V1 可能是 dict 或 attribute
        bgcolor = getattr(first_style, "bgcolor", None)
        # 若 bgcolor 是字典 (Flet 0.85 ButtonStyle 支持 dict 访问)
        if bgcolor is None and isinstance(first_style, dict):
            bgcolor = first_style.get("bgcolor")
        # 容忍两种表示: 直接属性或 dict
        assert bgcolor == AppColors.PRIMARY or bgcolor == {ft.ControlState.DEFAULT: AppColors.PRIMARY}

    def test_tab_click_switches_active_tab(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """点击 tab 2 (data="2") → set_current_tab(2), 重渲染后第 3 个 button selected。"""
        from tests.unit.ui.component_renderer import render_once
        from ui.theme import AppColors

        component, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        # 点击 data="2" 的 button
        _trigger_callback(buttons[2].on_click, _make_tab_event("2"))
        # 重渲染
        result = render_once(component)
        buttons = _find_buttons(result)
        # 第 3 个 button (idx=2) 应为 selected
        third_style = buttons[2].style
        bgcolor = getattr(third_style, "bgcolor", None)
        if bgcolor is None and isinstance(third_style, dict):
            bgcolor = third_style.get("bgcolor")
        assert bgcolor == AppColors.PRIMARY or bgcolor == {ft.ControlState.DEFAULT: AppColors.PRIMARY}

    def test_tab_click_invalid_data_logs_warning(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """data="invalid" → int() 抛 ValueError → logger.warning + 不切换 tab。"""
        component, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        with patch("ui.views.settings_view.logger") as mock_logger:
            _trigger_callback(buttons[0].on_click, _make_tab_event("invalid"))
            mock_logger.warning.assert_called_once()

    def test_tab_click_out_of_range_logs_warning(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """data="99" 超出 tabs 范围 → logger.warning + 不切换 tab。"""
        component, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        with patch("ui.views.settings_view.logger") as mock_logger:
            _trigger_callback(buttons[0].on_click, _make_tab_event("99"))
            mock_logger.warning.assert_called_once()

    def test_tab_click_none_data_logs_warning(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """data=None → int(None) 抛 TypeError → logger.warning。"""
        component, result = self._mount(mock_i18n_state, mock_app_colors_state)
        buttons = _find_buttons(result)
        with patch("ui.views.settings_view.logger") as mock_logger:
            _trigger_callback(buttons[0].on_click, _make_tab_event(None))
            mock_logger.warning.assert_called_once()

    def test_mount_renders_header_title(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """挂载后包含 header_title Text (size=24, weight=BOLD)。"""
        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        texts = [c for c in _collect_controls(result) if isinstance(c, ft.Text)]
        # 至少有一个 size=24 的 Text (header)
        assert any(getattr(t, "size", None) == 24 for t in texts)

    def test_mount_renders_divider(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """挂载后包含 Divider (header 与 tab_body 之间)。"""
        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        dividers = [c for c in _collect_controls(result) if isinstance(c, ft.Divider)]
        assert len(dividers) >= 1

    def test_mount_renders_tab_body_container(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """挂载后 tab_body 是 ft.Container, content 为第一个 tab 实例。"""
        _, result = self._mount(mock_i18n_state, mock_app_colors_state)
        containers = [c for c in _collect_controls(result) if isinstance(c, ft.Container)]
        # 至少有 tab_bar / tab_body / 根 Container
        assert len(containers) >= 2

    def test_show_snack_closure_calls_impl_with_captured_page(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """show_snack 闭包应调用 _show_snack_impl 传入渲染时捕获的 page。

        通过 patch _show_snack_impl 验证闭包路径生效。
        """
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            run_mount_effects,
        )
        from ui.views.settings_view import SettingsView

        component = make_component(SettingsView)
        fake_page = FakePage()
        fake_page.show_toast = MagicMock()  # type: ignore[method-assign]
        run_mount_effects(component, page=fake_page)

        # tabs[0] = DataSourceTab(show_snack) — 验证 show_snack 闭包通过 captured page 调用
        # run_mount_effects 内部触发首次渲染, _build_tabs 已被调用
        self.mock_data.assert_called()
        show_snack_closure = self.mock_data.call_args[0][0]
        # 调用闭包 → 应通过 fake_page.show_toast 触发 (因 _show_snack_impl 接收 page)
        show_snack_closure("test message", color="error")
        fake_page.show_toast.assert_called_once_with("test message", type="error")

    def test_show_snack_closure_silently_when_no_page(
        self,
        mock_i18n_state,
        mock_app_colors_state,
    ):
        """无 page (RuntimeError) 时 _show_snack 闭包静默返回。"""
        # 不调用 attach_fake_page, ft.context.page 抛 RuntimeError → _page=None
        from tests.unit.ui.component_renderer import (
            make_component,
            run_mount_effects,
        )
        from ui.views.settings_view import SettingsView

        component = make_component(SettingsView)
        run_mount_effects(component)  # 无 page

        # show_snack 闭包捕获 _page=None, 调用应静默返回
        self.mock_data.assert_called()
        show_snack_closure = self.mock_data.call_args[0][0]
        # 不抛异常即可
        show_snack_closure("msg")
