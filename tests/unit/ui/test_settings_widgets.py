"""ui/components/settings_widgets.py 声明式契约守护测试 (Phase A.1).

业务逻辑由消费方 ViewModel 单元测试覆盖。View 层测试聚焦于契约守护
（grep 检查禁止的命令式模式），参照 test_config_panels.py 模式。
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
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
            _collect(node)

    lines = source.splitlines()
    code_lines = [line for i, line in enumerate(lines, 1) if i not in docstring_lines]
    return "\n".join(code_lines)


def _code_source() -> str:
    """源码（去除 docstring），用于禁止模式检查。"""
    import ui.components.settings_widgets as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.settings_widgets as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


_COMPONENTS = [
    "DashboardCard",
    "MetricCard",
    "ActionChip",
    "StatusBadge",
    "SectionHeader",
    "SettingRow",
]


class TestSettingsWidgetsContract:
    """6 个声明式组件的契约守护测试。"""

    @pytest.mark.parametrize("name", _COMPONENTS)
    def test_component_is_ft_component(self, name):
        """DoD: 每个组件必须被 @ft.component 装饰。"""
        from ui.components import settings_widgets as mod

        fn = getattr(mod, name)
        assert hasattr(fn, "__wrapped__"), f"{name} 必须用 @ft.component 装饰"

    @pytest.mark.parametrize("name", _COMPONENTS)
    def test_no_class_inheritance(self, name):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert f"class {name}(" not in _code_source(), f"{name} 不应是 class（命令式）"

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

    def test_no_set_value(self):
        """DoD: 禁止命令式 set_value（改用 props 推送）。"""
        assert "set_value" not in _code_source()

    def test_no_set_label(self):
        """DoD: 禁止命令式 set_label（改用 props 推送）。"""
        assert "set_label" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式 update_theme（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_no_set_loading(self):
        """DoD: 禁止命令式 set_loading（改用 is_loading prop 推送）。"""
        assert "set_loading" not in _code_source()

    def test_no_set_text(self):
        """DoD: 禁止命令式 set_text（改用 props 推送）。"""
        assert "set_text" not in _code_source()

    def test_no_update_locale(self):
        """DoD: 禁止命令式 update_locale（声明式通过 Observable state 自动重渲染）。"""
        assert "update_locale" not in _code_source()

    def test_no_refresh_locale(self):
        """DoD: 禁止命令式 refresh_locale（声明式自动重渲染）。"""
        assert "refresh_locale" not in _code_source()

    def test_no_on_locale_change(self):
        """DoD: 禁止命令式 _on_locale_change（声明式自动重渲染）。"""
        assert "_on_locale_change" not in _code_source()

    def test_metric_card_subscribes_app_colors(self):
        """DoD: MetricCard 必须订阅 AppColors.get_observable_state（trend 用 Layer 2 色）。"""
        assert "AppColors.get_observable_state" in _raw_source()

    def test_section_header_subscribes_i18n(self):
        """DoD: SectionHeader 必须订阅 get_observable_state（title_key 重渲染）。"""
        assert "get_observable_state" in _raw_source()

    def test_setting_row_subscribes_i18n(self):
        """DoD: SettingRow 必须订阅 get_observable_state（title_key/subtitle_key 重渲染）。"""
        assert "get_observable_state" in _raw_source()
