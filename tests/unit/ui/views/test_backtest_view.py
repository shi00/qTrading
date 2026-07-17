"""ui/views/backtest_view.py 组件运行时测试 (Task 2.2).

覆盖:
1. R16 守卫: _on_run_backtest 用 page.run_task 调度 vm.run_backtest (async)
2. Handler 测试:
   - _on_strategy_change: set_selected_strategy + set_no_strategy_error(False)
   - _on_run_backtest: 无策略早返回 / create_config 参数 / page=None RuntimeError 守卫 / page.run_task 调用
   - _on_cancel_backtest: vm.cancel_backtest 调用
3. 状态渲染测试:
   - no_strategy_error & not is_running → "backtest_no_strategy"
   - status_message / progress_message 翻译
   - is_running 切换 progress_bar.visible / cancel_button.visible
   - progress 数值绑定
   - result 传递 BacktestResultPanel
   - status_color 映射 _STATUS_COLOR_MAP (error/warning/success/info/unknown)
   - strategies 空 → selected_strategy=None

测试范式参考 test_system_tab.py / test_ai_brain_tab.py (FakeVM + component_renderer + _invoke helper + _rerender helper).
现有 tests/unit/ui/test_backtest_view.py 仅做契约守护, 本文件补充运行时测试, 不重复契约守护内容.
"""

from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest
from flet.components.component import Component

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)

pytestmark = pytest.mark.unit


def _read_source() -> str:
    """读取 backtest_view.py 源码 (用 mod.__file__ 避免硬编码路径)."""
    from pathlib import Path

    import ui.views.backtest_view as mod

    return Path(mod.__file__).read_text(encoding="utf-8")


# ============================================================================
# R16 守卫: 同步 event handler 调度 async handler
# ============================================================================


class TestBacktestViewR16Compliance:
    """R16 红线: _on_run_backtest 必须用 page.run_task 调度 vm.run_backtest (async)."""

    def test_on_run_backtest_uses_run_task(self) -> None:
        """验证 ≥1 处 `page.run_task(` 调度。

        _on_run_backtest 是同步 event handler (BacktestConfigPanel 子组件触发),
        内部调用 page.run_task(vm.run_backtest, ...) 调度 async 方法 (R16 守卫)。
        """
        source = _read_source()
        run_task_count = source.count("page.run_task(")
        assert run_task_count >= 1, f"应有 ≥1 处 page.run_task, 实际 {run_task_count}"


# ============================================================================
# 运行时测试基础设施: FakeBacktestViewModel + backtest_view_env fixture
# ============================================================================


class _FakeBacktestViewModel:
    """模拟 BacktestViewModel, 满足 use_viewmodel hook 契约 (state/subscribe/dispose)."""

    def __init__(self, strategies: dict[str, str] | None = None) -> None:
        self._subscribers: list[Any] = []
        self._strategies = strategies if strategies is not None else {"ma_cross": "MA 金叉"}
        self.dispose_called: bool = False
        self.create_config_mock = MagicMock(return_value="fake_backtest_config")
        self.run_backtest_mock = MagicMock()
        self.cancel_backtest_mock = MagicMock()

        # 构造 state-like 对象 (避免 import 真实 BacktestState 触发 strategies 层依赖)
        class _State:
            is_running: bool = False
            progress: float = 0.0
            progress_message: Any = None
            status_message: Any = None
            status_color: str = ""
            result: Any = None

        self._state = _State()

    @property
    def state(self) -> Any:
        return self._state

    def _set_state(self, **changes: Any) -> None:
        """模拟 BacktestViewModel._set_state: 更新字段 + 通知订阅者."""
        for k, v in changes.items():
            setattr(self._state, k, v)
        for cb in list(self._subscribers):
            cb(self._state)

    def subscribe(self, callback: Any) -> Any:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    def get_available_strategies(self) -> dict[str, str]:
        return self._strategies

    def create_config(self, **kwargs: Any) -> Any:
        return self.create_config_mock(**kwargs)

    async def run_backtest(
        self,
        strategy_key: str,
        config: Any,
        params: Any = None,
        persist: bool = True,
    ) -> Any:
        return self.run_backtest_mock(strategy_key, config, params, persist)

    def cancel_backtest(self) -> None:
        self.cancel_backtest_mock()


def _make_fake_page() -> FakePage:
    """创建带 run_task 的 fake page。"""
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    return page


def _walk_all_controls(root: Any) -> list:
    """递归返回所有 ft.Control (用于搜索 dropdown / button / text / progress_bar)."""
    found: list[Any] = []
    visited: set[int] = set()

    def _walk(c: Any) -> None:
        if id(c) in visited:
            return
        visited.add(id(c))
        if isinstance(c, ft.Control):
            found.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(root)
    return found


def _get_dropdowns(env: dict) -> list[ft.Dropdown]:
    """返回渲染树中所有 Dropdown (按出现顺序)."""
    dropdowns: list[ft.Dropdown] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Dropdown) and ctrl not in dropdowns:
            dropdowns.append(ctrl)
    return dropdowns


def _get_buttons(env: dict) -> list:
    """返回渲染树中所有 ft.Button (按出现顺序)."""
    buttons: list[Any] = []

    def _walk(c: Any) -> None:
        if isinstance(c, ft.Button):
            buttons.append(c)
        if isinstance(c, Component):
            for v in list(c.args) + list(c.kwargs.values()):
                if v is not None:
                    _walk(v)
        elif isinstance(c, list):
            for x in c:
                if x is not None:
                    _walk(x)
        elif isinstance(c, ft.Control):
            for attr in ("controls", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        if x is not None:
                            _walk(x)
                elif children is not None:
                    _walk(children)

    _walk(env["result"])
    return buttons


def _get_progress_bars(env: dict) -> list[ft.ProgressBar]:
    """返回渲染树中所有 ProgressBar."""
    bars: list[ft.ProgressBar] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.ProgressBar) and ctrl not in bars:
            bars.append(ctrl)
    return bars


def _get_texts(env: dict) -> list[ft.Text]:
    """返回渲染树中所有 Text (按出现顺序)."""
    texts: list[ft.Text] = []
    for ctrl in _walk_all_controls(env["result"]):
        if isinstance(ctrl, ft.Text) and ctrl not in texts:
            texts.append(ctrl)
    return texts


def _make_event(value: Any = None) -> MagicMock:
    """构造 ft.ControlEvent mock."""
    e = MagicMock()
    e.control.value = value
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _rerender(env: dict) -> Any:
    """重新渲染组件并更新 env['result'].

    声明式范式下, on_change 触发 set_state 后需手动 render_once 让闭包捕获新 state,
    否则 event handler 中的 state 变量仍是旧值。
    """
    result = render_once(env["component"])
    env["result"] = result
    return result


def _make_config() -> dict:
    """构造标准 backtest config dict (BacktestConfigPanel 输出格式)."""
    return {
        "start_date": "2024-01-01",
        "end_date": "2024-12-31",
        "initial_capital": 1_000_000.0,
        "rebalance_freq": "signal",
        "max_position_count": 50,
        "commission_rate": 3e-4,
        "stamp_duty_rate": 1e-3,
        "slippage_bps": 5.0,
    }


def _patch_backtest_view_mocks(mod, monkeypatch, fake_vm: _FakeBacktestViewModel) -> dict:
    """注入 BacktestView 共用的外部依赖 mock.

    Mock:
    - I18n (模块级导入)
    - BacktestViewModel (内部 use_viewmodel 实例化)
    - BacktestConfigPanel / BacktestResultPanel / ResizableSplitter (子组件, 替换为 mock)
    - UILogger (横切关注点)
    """
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: f"i18n[{key}]"
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    monkeypatch.setattr(mod, "BacktestViewModel", lambda: fake_vm)

    captured_callbacks: dict[str, Any] = {}

    def _fake_config_panel(on_run_backtest: Any = None, **kwargs: Any) -> Any:
        captured_callbacks["on_run_backtest"] = on_run_backtest
        return MagicMock(name="BacktestConfigPanel")

    monkeypatch.setattr(mod, "BacktestConfigPanel", _fake_config_panel)
    monkeypatch.setattr(mod, "BacktestResultPanel", MagicMock(return_value=MagicMock(name="BacktestResultPanel")))
    monkeypatch.setattr(mod, "ResizableSplitter", MagicMock(return_value=MagicMock(name="ResizableSplitter")))
    monkeypatch.setattr(mod, "UILogger", MagicMock())

    return {"mock_i18n": mock_i18n, "captured_callbacks": captured_callbacks}


@pytest.fixture
def backtest_view_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 BacktestView (默认 strategies={"ma_cross": ...}), 返回 env dict."""
    from ui.views import backtest_view as mod

    fake_vm = _FakeBacktestViewModel(strategies={"ma_cross": "MA 金叉"})
    mocks = _patch_backtest_view_mocks(mod, monkeypatch, fake_vm)

    component = make_component(mod.BacktestView)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "fake_vm": fake_vm,
        "mock_i18n": mocks["mock_i18n"],
        "captured_callbacks": mocks["captured_callbacks"],
    }


@pytest.fixture
def backtest_view_empty_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 BacktestView (strategies={}), 返回 env dict (用于无策略路径测试)."""
    from ui.views import backtest_view as mod

    fake_vm = _FakeBacktestViewModel(strategies={})
    mocks = _patch_backtest_view_mocks(mod, monkeypatch, fake_vm)

    component = make_component(mod.BacktestView)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "fake_vm": fake_vm,
        "mock_i18n": mocks["mock_i18n"],
        "captured_callbacks": mocks["captured_callbacks"],
    }


# ============================================================================
# 组件挂载/渲染基础测试
# ============================================================================


class TestBacktestViewMount:
    """BacktestView 挂载/渲染基础测试."""

    def test_mount_returns_container(self, backtest_view_env) -> None:
        """挂载返回 ft.Container, content 为 Column."""
        result = backtest_view_env["result"]
        assert isinstance(result, ft.Container)
        assert isinstance(result.content, ft.Column)

    def test_mount_creates_view_model(self, backtest_view_env) -> None:
        """挂载时通过 factory 实例化 BacktestViewModel (subscribe 被调用)."""
        assert len(backtest_view_env["fake_vm"]._subscribers) > 0

    def test_render_includes_strategy_dropdown(self, backtest_view_env) -> None:
        """渲染含 1 个 Dropdown (strategy)."""
        dropdowns = _get_dropdowns(backtest_view_env)
        assert len(dropdowns) == 1

    def test_render_includes_cancel_button(self, backtest_view_env) -> None:
        """渲染含 1 个 Button (cancel)."""
        buttons = _get_buttons(backtest_view_env)
        assert len(buttons) == 1

    def test_render_includes_progress_bar(self, backtest_view_env) -> None:
        """渲染含 1 个 ProgressBar."""
        bars = _get_progress_bars(backtest_view_env)
        assert len(bars) == 1

    def test_unmount_triggers_vm_dispose(self, backtest_view_env) -> None:
        """卸载后 BacktestViewModel.dispose 被调用."""
        component = backtest_view_env["component"]
        assert backtest_view_env["fake_vm"].dispose_called is False
        run_unmount_effects(component)
        assert backtest_view_env["fake_vm"].dispose_called is True


# ============================================================================
# Handler 测试: _on_strategy_change
# ============================================================================


class TestOnStrategyChange:
    """_on_strategy_change: set_selected_strategy + set_no_strategy_error(False)."""

    def test_updates_selected_strategy(self, backtest_view_env) -> None:
        """切换 dropdown → set_selected_strategy(e.control.value) → dropdown.value 更新."""
        env = backtest_view_env
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event("other_strategy"))
        _rerender(env)

        dropdowns_after = _get_dropdowns(env)
        assert dropdowns_after[0].value == "other_strategy"

    def test_clears_no_strategy_error(self, backtest_view_env) -> None:
        """切换 dropdown → set_no_strategy_error(False).

        流程: 切换 selected_strategy=None → 触发 _on_run_backtest → set_no_strategy_error(True)
        → status_text="i18n[backtest_no_strategy]" → 再切换 dropdown → set_no_strategy_error(False)
        → status_text="" (no_strategy_error=False, status_message=None).
        """
        env = backtest_view_env
        # step 1: 切换 selected_strategy=None
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event(None))
        _rerender(env)

        # step 2: 触发 _on_run_backtest → set_no_strategy_error(True)
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]
        on_run_backtest(_make_config())
        _rerender(env)
        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_no_strategy]" for t in texts), (
            "no_strategy_error=True 时应显示 backtest_no_strategy"
        )

        # step 3: 切换 dropdown → set_no_strategy_error(False)
        dropdowns = _get_dropdowns(env)
        _invoke(dropdowns[0].on_select, _make_event("ma_cross"))
        _rerender(env)
        texts = _get_texts(env)
        assert not any(t.value == "i18n[backtest_no_strategy]" for t in texts), (
            "切换 dropdown 后 no_strategy_error 应被清除"
        )


# ============================================================================
# Handler 测试: _on_run_backtest
# ============================================================================


class TestOnRunBacktest:
    """_on_run_backtest: create_config 参数 / page=None 守卫 / page.run_task 调用."""

    def test_create_config_called_with_correct_args(self, backtest_view_env) -> None:
        """有策略 → vm.create_config 参数正确性 (8 个字段全部透传)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]
        on_run_backtest(_make_config())

        fake_vm.create_config_mock.assert_called_once_with(
            start_date="2024-01-01",
            end_date="2024-12-31",
            initial_capital=1_000_000.0,
            rebalance_freq="signal",
            max_position_count=50,
            commission_rate=3e-4,
            stamp_duty_rate=1e-3,
            slippage_bps=5.0,
        )

    def test_page_run_task_called_with_correct_args(self, backtest_view_env) -> None:
        """有策略 → page.run_task(vm.run_backtest, selected_strategy, backtest_config)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]
        on_run_backtest(_make_config())

        page.run_task.assert_called_once()
        call_args = page.run_task.call_args
        assert call_args.args[0] == fake_vm.run_backtest
        assert call_args.args[1] == "ma_cross"  # 默认 selected_strategy
        assert call_args.args[2] == "fake_backtest_config"

    def test_page_none_runtime_error_swallowed(self, backtest_view_env) -> None:
        """page=None (ft.context.page 抛 RuntimeError) → 守卫生效, 不抛异常, 不调 run_task.

        源码: ``try: page = ft.context.page; if page is not None: page.run_task(...)``
        ``except RuntimeError: logger.warning(...)``.
        _context_page.set(None) 让 ft.context.page 抛 RuntimeError, 验证 except 守遗传播.
        """
        env = backtest_view_env
        page = env["page"]
        page.run_task.reset_mock()
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]

        from flet.controls.context import _context_page

        saved = _context_page.get()
        _context_page.set(None)
        try:
            on_run_backtest(_make_config())  # 不应抛异常
        finally:
            _context_page.set(saved)

        # page.run_task 未被调用 (因为 ft.context.page 抛 RuntimeError)
        assert not page.run_task.called
        # 但 create_config 已被调用 (在 try 块之前)
        env["fake_vm"].create_config_mock.assert_called_once()


# ============================================================================
# Handler 测试: _on_cancel_backtest
# ============================================================================


class TestOnCancelBacktest:
    """_on_cancel_backtest: vm.cancel_backtest 调用."""

    def test_cancel_invokes_vm_cancel(self, backtest_view_env) -> None:
        """点击 cancel button → vm.cancel_backtest() 被调用."""
        env = backtest_view_env
        buttons = _get_buttons(env)
        _invoke(buttons[0].on_click, _make_event())
        env["fake_vm"].cancel_backtest_mock.assert_called_once()


# ============================================================================
# 状态渲染测试
# ============================================================================


class TestStatusRendering:
    """状态渲染: status_message / progress_message / is_running / progress / result / status_color."""

    def test_status_message_translation(self, backtest_view_env) -> None:
        """state.status_message 存在 → I18n.get(status_message.key, **params)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message

        fake_vm._set_state(
            status_message=Message("backtest_failed", {"reason": "test"}),
            status_color="error",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_failed]" for t in texts)

    def test_progress_message_translation(self, backtest_view_env) -> None:
        """state.progress_message 存在 → I18n.get(progress_message.key, **params)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message

        fake_vm._set_state(progress_message=Message("backtest_initializing", {}))
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_initializing]" for t in texts)

    def test_is_running_toggles_progress_bar_visible(self, backtest_view_env) -> None:
        """is_running=True → progress_bar.visible=True, cancel_button.visible=True."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        fake_vm._set_state(is_running=True)
        _rerender(env)

        bars = _get_progress_bars(env)
        assert bars[0].visible is True
        buttons = _get_buttons(env)
        assert buttons[0].visible is True

    def test_not_running_toggles_progress_bar_invisible(self, backtest_view_env) -> None:
        """is_running=False → progress_bar.visible=False, cancel_button.visible=False."""
        env = backtest_view_env
        # 默认 is_running=False
        bars = _get_progress_bars(env)
        assert bars[0].visible is False
        buttons = _get_buttons(env)
        assert buttons[0].visible is False

    def test_progress_value_binding(self, backtest_view_env) -> None:
        """state.progress=0.5 → progress_bar.value=0.5."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        fake_vm._set_state(progress=0.5)
        _rerender(env)

        bars = _get_progress_bars(env)
        assert bars[0].value == 0.5

    def test_result_passed_to_result_panel(self, backtest_view_env) -> None:
        """state.result → BacktestResultPanel(result=state.result)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        fake_result = MagicMock(name="test_result")
        fake_vm._set_state(result=fake_result)
        _rerender(env)

        env["mod"].BacktestResultPanel.assert_called_with(result=fake_result)

    def test_status_color_mapping_error(self, backtest_view_env) -> None:
        """status_color="error" → status_text.color = _STATUS_COLOR_MAP["error"] = AppColors.ERROR."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message
        from ui.theme import AppColors

        fake_vm._set_state(
            status_message=Message("backtest_failed", {}),
            status_color="error",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_failed]" and t.color == AppColors.ERROR for t in texts)

    def test_status_color_mapping_warning(self, backtest_view_env) -> None:
        """status_color="warning" → status_text.color = AppColors.WARNING."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message
        from ui.theme import AppColors

        fake_vm._set_state(
            status_message=Message("backtest_already_running", {}),
            status_color="warning",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_already_running]" and t.color == AppColors.WARNING for t in texts)

    def test_status_color_mapping_success(self, backtest_view_env) -> None:
        """status_color="success" → status_text.color = AppColors.SUCCESS."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message
        from ui.theme import AppColors

        fake_vm._set_state(
            status_message=Message("backtest_completed", {}),
            status_color="success",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_completed]" and t.color == AppColors.SUCCESS for t in texts)

    def test_status_color_mapping_info(self, backtest_view_env) -> None:
        """status_color="info" → status_text.color = AppColors.INFO."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message
        from ui.theme import AppColors

        fake_vm._set_state(
            status_message=Message("backtest_starting", {}),
            status_color="info",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_starting]" and t.color == AppColors.INFO for t in texts)

    def test_status_color_unknown_falls_back_to_text_secondary(self, backtest_view_env) -> None:
        """status_color="unknown" → status_text.color = AppColors.TEXT_SECONDARY (fallback)."""
        env = backtest_view_env
        fake_vm = env["fake_vm"]
        from ui.viewmodels import Message
        from ui.theme import AppColors

        fake_vm._set_state(
            status_message=Message("backtest_unknown", {}),
            status_color="unknown",
        )
        _rerender(env)

        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_unknown]" and t.color == AppColors.TEXT_SECONDARY for t in texts)


# ============================================================================
# 空 strategies 环境: 测试 selected_strategy=None 路径
# ============================================================================


class TestEmptyStrategiesEnv:
    """空 strategies 环境测试: selected_strategy=None / set_no_strategy_error 路径."""

    def test_empty_strategies_yields_selected_strategy_none(self, backtest_view_empty_env) -> None:
        """strategies={} → selected_strategy=None (next(iter({}), None))."""
        env = backtest_view_empty_env
        dropdowns = _get_dropdowns(env)
        assert dropdowns[0].value is None

    def test_no_strategy_sets_error_and_returns_early(self, backtest_view_empty_env) -> None:
        """无策略 → set_no_strategy_error(True) 早返回, 不调 create_config / run_task."""
        env = backtest_view_empty_env
        fake_vm = env["fake_vm"]
        page = env["page"]
        page.run_task.reset_mock()
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]
        on_run_backtest(_make_config())

        # 早返回: create_config / run_task 未被调用
        fake_vm.create_config_mock.assert_not_called()
        assert not page.run_task.called

        # 重新渲染验证 status_text="i18n[backtest_no_strategy]"
        _rerender(env)
        texts = _get_texts(env)
        assert any(t.value == "i18n[backtest_no_strategy]" for t in texts), (
            "no_strategy_error=True and not is_running → status_text='backtest_no_strategy'"
        )

    def test_no_strategy_error_suppressed_when_running(self, backtest_view_empty_env) -> None:
        """no_strategy_error=True 但 is_running=True → 不显示 backtest_no_strategy.

        源码: ``if no_strategy_error and not state.is_running:``
        is_running=True 时, 即使 no_strategy_error=True 也不显示 backtest_no_strategy.
        """
        env = backtest_view_empty_env
        fake_vm = env["fake_vm"]
        # 先触发 _on_run_backtest 设置 no_strategy_error=True
        on_run_backtest = env["captured_callbacks"]["on_run_backtest"]
        on_run_backtest(_make_config())
        # 然后设置 is_running=True
        fake_vm._set_state(is_running=True)
        _rerender(env)

        texts = _get_texts(env)
        assert not any(t.value == "i18n[backtest_no_strategy]" for t in texts), (
            "is_running=True 时, 即使 no_strategy_error=True 也不显示 backtest_no_strategy"
        )
