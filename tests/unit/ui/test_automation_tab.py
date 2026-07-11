"""ui/views/settings_tabs/automation_tab.py 声明式契约守护测试 (Phase D.4).

声明式重写后 View 层测试聚焦:
1. 契约守护 (grep 检查禁止的命令式模式: class 继承/did_mount/.update()/weakref page_ref)
2. 模块级纯函数测试 (_build_time_options/_build_search_engine_options/
   _build_interval_options/_get_schedule_status_text/_get_page)

业务逻辑覆盖（ConfigHandler 读写 + 异常路径 + 异步保存）由集成测试
（flet_test_page fixture）承担, 声明式组件含 use_state 在无 renderer 下抛 RuntimeError。
"""

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
    import ui.views.settings_tabs.automation_tab as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.views.settings_tabs.automation_tab as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# 契约守护：声明式范式 (AutomationTab + NotificationsTab)
# ============================================================================


class TestAutomationTabContract:
    """AutomationTab 声明式契约守护测试 (Phase D.4)。"""

    def test_automation_tab_is_ft_component(self):
        """DoD: AutomationTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.automation_tab import AutomationTab

        assert hasattr(AutomationTab, "__wrapped__"), "AutomationTab 必须用 @ft.component 装饰"

    def test_automation_tab_uses_ft_component(self):
        """DoD: 必须使用 @ft.component 装饰。"""
        assert "@ft.component" in _raw_source(), "AutomationTab 必须用 @ft.component 装饰"

    def test_no_class_container(self):
        """DoD: 禁止命令式 class 继承 ft.Container。"""
        assert "class AutomationTab(" not in _code_source(), "AutomationTab 不应是 class (命令式)"
        assert "class NotificationsTab(" not in _code_source(), "NotificationsTab 不应是 class (命令式)"

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source(), "不应使用 did_mount (命令式)"

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source(), "不应使用 will_unmount (命令式)"

    def test_no_safe_update(self):
        """DoD: 禁止命令式 .update() / _safe_update()。"""
        assert ".update()" not in _code_source(), "不应使用 .update() (命令式)"
        assert "_safe_update" not in _code_source(), "不应使用 _safe_update (命令式)"

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

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin / _page_ref / weakref (用 ft.context.page)。"""
        assert "PageRefMixin" not in _code_source(), "不应使用 PageRefMixin"
        assert "_page_ref" not in _code_source(), "不应使用 _page_ref"
        assert "weakref" not in _code_source(), "不应使用 weakref"

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

    def test_notifications_tab_is_ft_component(self):
        """DoD: NotificationsTab 必须被 @ft.component 装饰。"""
        from ui.views.settings_tabs.automation_tab import NotificationsTab

        assert hasattr(NotificationsTab, "__wrapped__"), "NotificationsTab 必须用 @ft.component 装饰"

    def test_notifications_tab_no_page_ref_param(self):
        """DoD: NotificationsTab 签名不应包含 page_ref 参数 (声明式用 ft.context.page)。"""
        import inspect

        from ui.views.settings_tabs.automation_tab import NotificationsTab

        sig = inspect.signature(NotificationsTab.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page_ref" not in params, "NotificationsTab 不应接收 page_ref 参数"
        assert "show_snack_callback" in params, "NotificationsTab 必须接收 show_snack_callback"

    def test_automation_tab_no_page_ref_param(self):
        """DoD: AutomationTab 签名不应包含 page_ref 参数。"""
        import inspect

        from ui.views.settings_tabs.automation_tab import AutomationTab

        sig = inspect.signature(AutomationTab.__wrapped__)
        params = list(sig.parameters.keys())
        assert "page_ref" not in params, "AutomationTab 不应接收 page_ref 参数"


# ============================================================================
# R2 CancelledError 传播契约 (CLAUDE.md §3 红线 R2)
# ============================================================================


class TestR2CancelledErrorPropagation:
    """R2 红线: asyncio.CancelledError 必须显式 raise, 不被 except Exception 吞没。"""

    def test_has_cancelled_error_guard(self):
        """DoD: 含 await 的 async handler 必须有 except asyncio.CancelledError: raise。"""
        code = _raw_source()
        assert "except asyncio.CancelledError:" in code, "必须有 CancelledError 捕获"
        assert "raise  # R2" in code, "CancelledError 必须 raise (R2)"

    def test_cancelled_error_guard_count_meets_threshold(self):
        """DoD: automation_tab 含 7 个 async handler, CancelledError 守卫应 >= 7 处。"""
        code = _raw_source()
        guard_count = code.count("except asyncio.CancelledError:")
        assert guard_count >= 7, f"R2 违规: automation_tab 应至少 7 处 CancelledError 守卫, 实际 {guard_count}"

    def test_no_bare_exception_swallows_cancelled_error(self):
        """DoD: except Exception 前必须有 except asyncio.CancelledError 守卫。"""
        code = _raw_source()
        except_exception_count = code.count("except Exception")
        cancelled_guard_count = code.count("except asyncio.CancelledError")
        assert cancelled_guard_count >= except_exception_count, (
            f"R2 违规: {except_exception_count} 处 except Exception 但仅 {cancelled_guard_count} 处 CancelledError 守卫"
        )


# ============================================================================
# 模块级纯函数测试
# ============================================================================


class TestBuildTimeOptions:
    """_build_time_options 模块级纯函数测试。"""

    def test_returns_six_options(self):
        from ui.views.settings_tabs.automation_tab import _build_time_options

        options = _build_time_options()
        assert len(options) == 6

    def test_option_keys_correct(self):
        from ui.views.settings_tabs.automation_tab import _build_time_options

        options = _build_time_options()
        keys = [opt.key for opt in options]
        assert keys == ["15:30", "16:00", "16:30", "17:00", "18:00", "20:00"]

    def test_options_are_dropdown_option_instances(self):
        from ui.views.settings_tabs.automation_tab import _build_time_options

        options = _build_time_options()
        for opt in options:
            assert isinstance(opt, ft.dropdown.Option)


class TestBuildSearchEngineOptions:
    """_build_search_engine_options 模块级纯函数测试。"""

    def test_returns_two_options(self):
        from ui.views.settings_tabs.automation_tab import _build_search_engine_options

        options = _build_search_engine_options()
        assert len(options) == 2

    def test_option_keys_correct(self):
        from ui.views.settings_tabs.automation_tab import _build_search_engine_options

        options = _build_search_engine_options()
        keys = [opt.key for opt in options]
        assert keys == ["search_std", "search_pro"]


class TestBuildIntervalOptions:
    """_build_interval_options 模块级纯函数测试。"""

    def test_returns_four_options(self):
        from ui.views.settings_tabs.automation_tab import _build_interval_options

        options = _build_interval_options()
        assert len(options) == 4

    def test_option_keys_correct(self):
        from ui.views.settings_tabs.automation_tab import _build_interval_options

        options = _build_interval_options()
        keys = [opt.key for opt in options]
        assert keys == ["30", "60", "300", "900"]


class TestGetScheduleStatusText:
    """_get_schedule_status_text 模块级纯函数测试。"""

    def test_enabled_returns_on_key(self):
        from ui.views.settings_tabs.automation_tab import _get_schedule_status_text

        with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
            mock_i18n.get.return_value = "已开启"
            assert _get_schedule_status_text(True) == "已开启"
            mock_i18n.get.assert_called_once_with("settings_status_auto_on")

    def test_disabled_returns_off_key(self):
        from ui.views.settings_tabs.automation_tab import _get_schedule_status_text

        with patch("ui.views.settings_tabs.automation_tab.I18n") as mock_i18n:
            mock_i18n.get.return_value = "已关闭"
            assert _get_schedule_status_text(False) == "已关闭"
            mock_i18n.get.assert_called_once_with("settings_status_auto_off")


class TestGetPage:
    """_get_page 模块级纯函数测试 (ft.context.page 守卫)。"""

    def test_returns_page_when_context_available(self):
        from ui.views.settings_tabs.automation_tab import _get_page

        mock_page = MagicMock(name="page")
        with patch("ui.views.settings_tabs.automation_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: mock_page)
            assert _get_page() is mock_page

    def test_returns_none_when_runtime_error(self):
        """ft.context.page 抛 RuntimeError 时返回 None (未在渲染上下文)。"""
        from ui.views.settings_tabs.automation_tab import _get_page

        with patch("ui.views.settings_tabs.automation_tab.ft.context") as mock_ctx:
            type(mock_ctx).page = property(lambda self: (_ for _ in ()).throw(RuntimeError("no ctx")))
            assert _get_page() is None
