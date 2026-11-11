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
from datetime import date
from pathlib import Path

import flet as ft
import pytest

from strategies.backtest.config import BacktestConfig
from ui.i18n import I18n
from ui.viewmodels.backtest_view_model import LastRunSnapshot
from ui.views import backtest_view as backtest_view_module
from ui.views.backtest_view import BacktestView, _build_retry_hint

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
        assert "page" not in params, "BacktestView 不应接收 page 参数"
        assert "page_ref" not in params, "BacktestView 不应接收 page_ref 参数"

    def test_source_has_ft_component_decorator(self):
        """源码必须有 @ft.component 装饰器, 且保留声明式签名。

        F13 后签名多 prop (``active`` + ``prefill_request``), 改为语义断言
        (``__wrapped__`` 装饰器标记 + inspect 参数名/返回标注), 不依赖源码文本
        形态: 既避免模块 docstring 中 ``def BacktestView()`` 造成文本误命中,
        也避免下次新增 prop 因换行/签名变化再次误报。
        """
        assert "@ft.component" in _source_text()
        assert hasattr(BacktestView, "__wrapped__"), "BacktestView 必须用 @ft.component 装饰"
        sig = inspect.signature(BacktestView)
        assert sig.return_annotation is ft.Container, "BacktestView 必须返回 ft.Container"
        assert "active" in sig.parameters, "BacktestView 必须保留 active 参数"
        assert "prefill_request" in sig.parameters, "BacktestView 必须接收 prefill_request 透传参数"

    def test_uses_use_viewmodel(self):
        """必须通过 use_viewmodel 消费 BacktestViewModel。"""
        src = _source_text()
        assert "from ui.hooks import use_viewmodel" in src
        assert "use_viewmodel(BacktestViewModel)" in src

    def test_subscribes_i18n_and_theme_observable_state(self):
        """必须订阅 i18n + theme observable state 以自动重渲染。"""
        src = _source_text()
        assert "ft.use_state(get_observable_state)" in src
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


class TestRetryParamsSnapshotContract:
    """UIV-BT-01: 重试必须消费 VM last_run 快照, 不得读取可变透传草稿。"""

    @staticmethod
    def _retry_block() -> str:
        src = _source_text()
        start = src.index("def _on_retry_backtest")
        end = src.index("def _on_cancel_backtest", start)
        return src[start:end]

    def test_retry_does_not_read_prefilled_params_draft(self):
        """retry 源码不得引用 _prefilled_params (可变草稿), 必须读 VM 快照。"""
        block = self._retry_block()
        assert "_prefilled_params.current" not in block, "重试不得从可变透传草稿取参数"
        assert "last_run_summary" in block, "重试必须消费 VM 记录的上次提交快照"

    def test_submit_records_params_snapshot(self):
        """提交时须把 params 一并记入 VM last_run 快照。"""
        src = _source_text()
        assert "vm.record_last_run(" in src
        call = src.split("vm.record_last_run(", 1)[1].split(")", 1)[0]
        assert "backtest_config" in call
        assert "_prefilled_params.current" in call, "首次提交须把 params 写入快照"


class TestRetryHint:
    """下拉选择与重试对象不一致时，显式提示将重试哪一次运行。"""

    @staticmethod
    def _last_run(strategy_key: str = "strategy_a") -> LastRunSnapshot:
        return LastRunSnapshot(
            strategy_key,
            BacktestConfig(start_date=date(2024, 1, 1), end_date=date(2024, 1, 31)),
            None,
        )

    def test_no_hint_without_last_run(self) -> None:
        assert _build_retry_hint(None, (("strategy_a", "backtest_failed_title"),), "strategy_b") is None

    def test_no_hint_when_selection_matches_last_run(self) -> None:
        assert _build_retry_hint(self._last_run(), (("strategy_a", "backtest_failed_title"),), "strategy_a") is None

    def test_hint_names_last_run_when_selection_differs(self) -> None:
        hint = _build_retry_hint(
            self._last_run("strategy_a"),
            (("strategy_a", "backtest_failed_title"), ("strategy_b", "backtest_failed_message")),
            "strategy_b",
        )
        name = I18n.get("backtest_failed_title")
        expected = I18n.get("backtest_retry_last_run_hint", strategy_name=name)
        assert hint == expected
        assert name is not None
        assert hint is not None
        assert name in hint

    def test_hint_falls_back_to_strategy_key_when_not_listed(self) -> None:
        hint = _build_retry_hint(self._last_run("removed_strategy"), (), "strategy_b")
        assert hint is not None
        assert "removed_strategy" in hint

    def test_error_state_receives_retry_hint(self) -> None:
        """View→ErrorState 接线契约（本文件为契约守护，渲染行为由
        tests/unit/ui/components/test_state_views.py 的 TestErrorStateRender 行为级覆盖）。

        契约要求：View 须调用 _build_retry_hint 并把结果经 retry_hint 传入 ErrorState，
        且入参为上次运行快照 + 当前下拉三要素，缺一即视为接线断裂。
        """
        src = _source_text()
        assert "retry_hint=_build_retry_hint(" in src
        assert "state.last_run_summary" in src
        assert "state.available_strategies" in src
        assert "state.selected_strategy_key" in src
