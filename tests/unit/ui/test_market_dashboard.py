"""ui/components/market_dashboard.py 声明式契约守护测试 (Phase B.1).

业务逻辑由消费方 ViewModel 单元测试覆盖。View 层测试聚焦于契约守护
（grep 检查禁止的命令式模式），参照 test_settings_widgets.py 模式。
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _source_without_docstrings(source: str) -> str:
    """移除模块/函数/类 docstring 后的源码，用于契约守护检查。"""
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
    import ui.components.market_dashboard as mod

    return _source_without_docstrings(Path(mod.__file__).read_text(encoding="utf-8"))


def _raw_source() -> str:
    """原始源码（含 docstring），用于正向契约检查。"""
    import ui.components.market_dashboard as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


class TestMarketDashboardContract:
    """MarketDashboard 声明式组件契约守护测试。"""

    def test_component_is_ft_component(self):
        """DoD: MarketDashboard 必须被 @ft.component 装饰。"""
        from ui.components.market_dashboard import MarketDashboard

        assert hasattr(MarketDashboard, "__wrapped__"), "MarketDashboard 必须用 @ft.component 装饰"

    def test_no_class_inheritance(self):
        """DoD: 禁止命令式 class 继承 Flet 控件。"""
        assert "class MarketDashboard(" not in _code_source()

    def test_no_did_mount(self):
        """DoD: 禁止命令式 did_mount 生命周期回调。"""
        assert "did_mount" not in _code_source()

    def test_no_will_unmount(self):
        """DoD: 禁止命令式 will_unmount 生命周期回调。"""
        assert "will_unmount" not in _code_source()

    def test_no_update_call(self):
        """DoD: 禁止命令式 .update()。"""
        assert ".update()" not in _code_source()

    def test_no_update_data(self):
        """DoD: 禁止命令式数据推送 API（改用 data prop 推送）。"""
        assert "update_data" not in _code_source()

    def test_no_update_theme(self):
        """DoD: 禁止命令式主题刷新（声明式通过 Observable state 自动重渲染）。"""
        assert "update_theme" not in _code_source()

    def test_no_update_locale(self):
        """DoD: 禁止命令式语言刷新（声明式通过 Observable state 自动重渲染）。"""
        assert "update_locale" not in _code_source()

    def test_no_last_data_cache(self):
        """DoD: 禁止数据缓存（声明式 state 驱动渲染）。"""
        assert "_last_data" not in _code_source()

    def test_no_concept_skeleton_pool(self):
        """DoD: 禁止概念卡回收池（skeleton + update 模式，改 state 驱动渲染）。"""
        assert "_build_concept_card_skeleton" not in _code_source()
        assert "_update_concept_card" not in _code_source()

    def test_no_page_ref(self):
        """DoD: 禁止 PageRefMixin/_page_ref。"""
        assert "PageRefMixin" not in _code_source()
        assert "_page_ref" not in _code_source()

    def test_subscribes_i18n(self):
        """DoD: 必须订阅 I18n.get_observable_state（i18n 自动重渲染）。"""
        assert "I18n.get_observable_state" in _raw_source()

    def test_subscribes_app_colors(self):
        """DoD: 必须订阅 AppColors.get_observable_state（theme 自动重渲染）。"""
        assert "AppColors.get_observable_state" in _raw_source()
