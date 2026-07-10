"""BacktestView 声明式契约守护测试（Phase C.2）。

守护声明式契约（CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook）:
- @ft.component 函数组件（非 class ft.Container 子类）
- use_viewmodel 消费 VM（state snapshot + commands）
- 无命令式 API（did_mount/will_unmount/refresh_locale/handle_resize/_refresh_result_panel/.update()）
- page 访问用 ft.context.page（无 PageRefMixin/_page_ref）
- BacktestConfigPanel/BacktestResultPanel 作为子组件函数调用（props 推送）

View 组合（@ft.component + use_viewmodel）有状态，由集成测试覆盖，本文件只做契约守护。
"""

import inspect
from pathlib import Path

import pytest

from ui.views import backtest_view as backtest_view_module
from ui.views.backtest_view import BacktestView

pytestmark = pytest.mark.unit

_SOURCE_FILE = Path(backtest_view_module.__file__)


def _source_text() -> str:
    return _SOURCE_FILE.read_text(encoding="utf-8")


class TestBacktestViewDeclarativeContract:
    """守护 BacktestView 声明式契约，防止命令式回退。"""

    def test_is_function_not_class(self):
        """BacktestView 必须是函数组件，非 ft.Container 子类。"""
        assert inspect.isfunction(BacktestView), "BacktestView 必须是函数（@ft.component），而非类"
        assert not inspect.isclass(BacktestView)

    def test_no_page_parameter(self):
        """声明式组件无 page 参数（page 通过 ft.context.page 访问）。"""
        params = list(inspect.signature(BacktestView).parameters.keys())
        assert params == [], f"BacktestView 不应有参数，实际: {params}"

    def test_source_has_ft_component_decorator(self):
        """源码必须有 @ft.component 装饰器。"""
        src = _source_text()
        assert "@ft.component" in src
        assert "def BacktestView() -> ft.Container:" in src

    def test_uses_use_viewmodel(self):
        """必须通过 use_viewmodel 消费 BacktestViewModel。"""
        src = _source_text()
        assert "from ui.hooks import use_viewmodel" in src
        assert "use_viewmodel(BacktestViewModel)" in src

    def test_subscribes_i18n_and_theme_observable_state(self):
        """必须订阅 i18n + theme observable state 以自动重渲染。"""
        src = _source_text()
        assert "ft.use_state(I18n.get_observable_state)" in src
        assert "ft.use_state(AppColors.get_observable_state)" in src

    def test_uses_ft_context_page(self):
        """page 访问必须用 ft.context.page（禁止 PageRefMixin/_page_ref）。"""
        assert "ft.context.page" in _source_text()

    def test_consumes_declarative_subcomponents(self):
        """必须以函数调用方式消费声明式子组件（props 推送）。"""
        src = _source_text()
        assert "BacktestConfigPanel(" in src
        assert "BacktestResultPanel(" in src
        assert "ResizableSplitter(" in src

    @pytest.mark.parametrize(
        "forbidden",
        [
            "class BacktestView(",
            "def did_mount(",
            "def will_unmount(",
            "def refresh_locale(",
            "def handle_resize(",
            "def _refresh_result_panel(",
            "def _fixed_vertical_chrome_height(",
            "_page_ref",
            "PageRefMixin",
            "self.update()",
            "refresh_dropdown_options",
        ],
    )
    def test_no_imperative_api_in_source(self, forbidden: str):
        """源码不得残留任何命令式 API（防止回退）。"""
        assert forbidden not in _source_text(), f"命令式 API 残留: {forbidden}"

    def test_no_run_backtest_inline_in_view(self):
        """回测执行委托 VM（View 不直接持有 _start_backtest 异步方法）。"""
        assert "def _start_backtest(" not in _source_text()
        assert "_on_vm_update" not in _source_text()
        assert "_on_vm_status" not in _source_text()
        assert "_on_vm_progress" not in _source_text()
        assert "_on_vm_result" not in _source_text()
