"""ui/app_layout.py 组件运行时测试 (Task 4.1).

覆盖:
1. R2/R16 红线守卫: CancelledError raise / page.run_task 调度
2. _do_tab_switch: 防抖完成后切换 tab / CancelledError raise / new_tab == current_tab 早返回
3. _on_nav_change: page None / page.run_task / selected == current_tab 早返回
4. _toggle_nav: nav_collapsed 状态切换

测试范式参考 test_system_tab.py (FakePage + component_renderer + _invoke helper +
_await_run_task_handler + asyncio.run 异步 handler).
现有 test_app_layout_contract.py 覆盖契约守护 + 模块级纯函数, 本文件补充运行时测试, 不重复覆盖.

Phase 10.2: ViewportState/resize 重渲染链删除 — _setup_resize/_cleanup_resize/resize 防抖
测试块随被测代码一并移除。
"""

import asyncio
import inspect
from typing import Any
from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 辅助函数
# ============================================================================


def _make_event(selected_index: int = 0) -> MagicMock:
    """构造 ft.ControlEvent mock, 支持 selected_index 属性."""
    e = MagicMock()
    e.control.selected_index = selected_index
    return e


def _invoke(handler: Any, *args: Any) -> None:
    """调用 Flet event handler (pyright safe).

    Flet 控件的 on_select/on_click 类型为 Optional[Callable], pyright 报 reportOptionalCall;
    且 stub 声明 0 参但运行时传入 ControlEvent, pyright 报 reportCallIssue。
    此 helper 用 Any 参数绕过两者。
    """
    handler(*args)


def _await_run_task_handler(page: Any) -> tuple[Any, tuple, dict]:
    """提取 page.run_task 最近一次调用的 handler 与参数。"""
    assert page.run_task.called, "page.run_task 未被调用"
    call = page.run_task.call_args
    handler = call.args[0]
    args = call.args[1:]
    kwargs = call.kwargs
    return handler, args, kwargs


def _rerender(env: dict) -> Any:
    """重新渲染组件并更新 env['result'].

    声明式范式下, on_change 触发 set_state 后需手动 render_once 让闭包捕获新 state,
    否则 event handler 中的 state 变量仍是旧值。
    """
    result = render_once(env["component"])
    env["result"] = result
    return result


def _make_fake_page() -> FakePage:
    """创建带 run_task + pubsub 的 fake page.

    P1-3 批次 2: 补 pubsub 属性 (AppLayout 订阅 TOPIC_NAVIGATE)。
    """
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.pubsub = MagicMock()  # type: ignore[method-assign]
    page.pubsub.subscribe_topic = MagicMock()  # type: ignore[method-assign]
    page.pubsub.unsubscribe_topic = MagicMock()  # type: ignore[method-assign]
    return page


# ============================================================================
# Fixture: 挂载 AppLayout
# ============================================================================


@pytest.fixture
def app_layout_env(mock_i18n_state, mock_app_colors_state, monkeypatch):
    """挂载 AppLayout, 返回 dict 含 component/page/result/mocks.

    Mock 外部依赖:
    - 6 个子视图 (HomeView/ScreenerView/BacktestView/DataExplorerView/TaskCenterView/SettingsView)
      替换为 MagicMock 避免触发各自 VM 渲染
    - I18n / AppColors 通过 fixture 注入 observable_state
    - UILogger 横切关注点
    """
    from ui import app_layout as mod

    # --- Mock 6 个子视图 (避免触发各自 VM 渲染) ---
    for view_name in [
        "HomeView",
        "ScreenerView",
        "BacktestView",
        "DataExplorerView",
        "TaskCenterView",
        "SettingsView",
    ]:
        monkeypatch.setattr(mod, view_name, MagicMock(return_value=MagicMock(name=view_name)))

    # --- Mock I18n.get (返回 key 而非真实 i18n 文案, 避免 locale 初始化依赖) ---
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr(mod, "I18n", mock_i18n)

    # --- Mock UILogger ---
    monkeypatch.setattr(mod, "UILogger", MagicMock())

    # --- 挂载组件 (run_mount_effects 触发 use_effect 挂载, 如 TOPIC_NAVIGATE 订阅) ---
    component = make_component(mod.AppLayout)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)

    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "mock_i18n": mock_i18n,
    }


def _get_nav_rail(env: dict) -> ft.NavigationRail:
    """从渲染树提取 NavigationRail (root.content.controls[0])."""
    result = env["result"]
    assert isinstance(result, ft.Container)
    row = result.content
    assert isinstance(row, ft.Row)
    nav_rail = row.controls[0]
    assert isinstance(nav_rail, ft.NavigationRail)
    return nav_rail


def _get_collapse_btn(env: dict) -> ft.IconButton:
    """从渲染树提取 collapse_btn (nav_rail.leading.content.controls[0])."""
    nav_rail = _get_nav_rail(env)
    brand_header = nav_rail.leading
    assert isinstance(brand_header, ft.Container)
    column = brand_header.content
    assert isinstance(column, ft.Column)
    collapse_btn = column.controls[0]
    assert isinstance(collapse_btn, ft.IconButton)
    return collapse_btn


# ============================================================================
# R2/R16 红线守护 (源码 grep 式)
# ============================================================================


class TestAppLayoutR2R16Compliance:
    """R2/R16 红线: CancelledError raise / page.run_task 调度."""

    def test_r2_cancelled_error_raise_guards(self) -> None:
        """R2: _do_tab_switch 必须有 CancelledError raise 守卫."""
        from pathlib import Path

        import ui.app_layout as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        cancelled_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_count >= 1, f"应有 ≥1 处 CancelledError 守卫, 实际 {cancelled_count}"
        assert raise_count >= 1, f"应有 ≥1 处 raise # R2, 实际 {raise_count}"

    def test_r16_on_nav_change_uses_run_task(self) -> None:
        """R16: _on_nav_change 必须用 page.run_task 调度 _do_tab_switch."""
        from pathlib import Path

        import ui.app_layout as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "page.run_task(_do_tab_switch" in source, "_on_nav_change 必须用 page.run_task 调度"


# ============================================================================
# _on_nav_change 测试: page None / page.run_task / 早返回
# ============================================================================


class TestOnNavChange:
    """_on_nav_change 行为测试."""

    def test_selected_equals_current_tab_early_return(self, app_layout_env) -> None:
        """selected == int(current_tab) 时早返回, 不调 page.run_task."""
        env = app_layout_env
        nav_rail = _get_nav_rail(env)
        page = env["page"]
        page.run_task.reset_mock()

        # current_tab 默认为 NavTabs.MARKET (0)
        _invoke(nav_rail.on_change, _make_event(selected_index=0))
        assert not page.run_task.called, "selected == current_tab 应早返回"

    def test_page_none_early_return(self, app_layout_env) -> None:
        """page=None 时早返回, 不抛异常."""
        env = app_layout_env
        nav_rail = _get_nav_rail(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.app_layout._get_page", return_value=None):
            _invoke(nav_rail.on_change, _make_event(selected_index=1))
        assert not page.run_task.called, "page=None 应早返回"

    def test_page_available_invokes_run_task(self, app_layout_env) -> None:
        """page 可用且 selected != current_tab → page.run_task(_do_tab_switch, selected)."""
        env = app_layout_env
        nav_rail = _get_nav_rail(env)
        page = env["page"]
        page.run_task.reset_mock()

        _invoke(nav_rail.on_change, _make_event(selected_index=1))
        handler, args, _ = _await_run_task_handler(page)
        assert inspect.iscoroutinefunction(handler), "handler 必须为协程函数"
        assert args == (1,), f"应传 selected=1, 实际 args={args}"


# ============================================================================
# _do_tab_switch 测试: 防抖完成 / CancelledError R2 守卫 / new_tab == current_tab
# ============================================================================


class TestDoTabSwitch:
    """_do_tab_switch 异步 handler 测试."""

    def _trigger(self, env, selected: int = 1) -> tuple:
        nav_rail = _get_nav_rail(env)
        page = env["page"]
        page.run_task.reset_mock()
        _invoke(nav_rail.on_change, _make_event(selected_index=selected))
        return _await_run_task_handler(page)

    def test_success_path_switches_tab(self, app_layout_env) -> None:
        """防抖完成后真正切换 tab: set_current_tab(new_tab) + UILogger.log_action."""
        env = app_layout_env
        handler, args, _ = self._trigger(env, selected=2)
        asyncio.run(handler(*args))

        # 重新渲染让 state 变化反映到控件树
        _rerender(env)
        nav_rail = _get_nav_rail(env)
        assert nav_rail.selected_index == 2, "set_current_tab(2) 后 selected_index 应为 2"
        env["mod"].UILogger.log_action.assert_called_with("AppLayout", "Navigate", "tab=backtest")

    def test_cancelled_error_propagates(self, app_layout_env) -> None:
        """R2: CancelledError 必须传播, 不被吞没."""
        env = app_layout_env
        handler, args, _ = self._trigger(env, selected=1)
        with patch("ui.app_layout.asyncio.sleep", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(handler(*args))

    def test_same_tab_does_not_switch(self, app_layout_env) -> None:
        """new_tab == current_tab 时不切换: set_current_tab 不被调用 (防抖完成后早返回)."""
        env = app_layout_env
        # current_tab 默认为 0 (MARKET); 模拟防抖期间用户取消, new_tab 仍是 0
        # 通过直接 await handler(0) 验证 (绕过 _on_nav_change 的 selected==current 早返回)
        nav_rail = _get_nav_rail(env)
        page = env["page"]
        page.run_task.reset_mock()
        # 用一个非 0 的 selected 触发 _on_nav_change (绕过外层早返回)
        _invoke(nav_rail.on_change, _make_event(selected_index=1))
        handler, _, _ = _await_run_task_handler(page)

        # patch current_tab 为 1, 然后 await handler(1) 验证 new_tab == current_tab 不切换
        # 但 current_tab 是闭包变量, 难以 patch。改用直接调用 handler(current_tab) 验证
        # handler 内部 if new_tab != current_tab 检查; current_tab 默认 0, 调 handler(0) 不切换
        env["mod"].UILogger.log_action.reset_mock()
        asyncio.run(handler(0))  # new_tab == current_tab (0) → 不切换
        assert not env["mod"].UILogger.log_action.called, "new_tab == current_tab 不应调用 log_action"


# ============================================================================
# _toggle_nav 测试: nav_collapsed 状态切换
# ============================================================================


class TestToggleNav:
    """_toggle_nav nav_collapsed 状态切换测试."""

    def test_toggle_nav_flips_collapsed_state(self, app_layout_env) -> None:
        """点击 collapse_btn → nav_collapsed 翻转, nav_rail.extended 跟随变化."""
        env = app_layout_env
        collapse_btn = _get_collapse_btn(env)
        nav_rail_before = _get_nav_rail(env)
        assert nav_rail_before.extended is True, "初始 nav_collapsed=False → extended=True"

        _invoke(collapse_btn.on_click, _make_event())

        _rerender(env)
        nav_rail_after = _get_nav_rail(env)
        assert nav_rail_after.extended is False, "toggle 后 nav_collapsed=True → extended=False"

        # 再次 toggle 应回到 extended
        collapse_btn = _get_collapse_btn(env)
        _invoke(collapse_btn.on_click, _make_event())
        _rerender(env)
        nav_rail_final = _get_nav_rail(env)
        assert nav_rail_final.extended is True, "再次 toggle → extended=True"


# ============================================================================
# E2E 模式: _build_pages_stack 只构造激活视图, 非激活视图用空 Container
# ============================================================================


class TestBuildPagesStackE2E:
    """``_build_pages_stack`` E2E 模式惰性构造测试 (覆盖 app_layout.py L91-98).

    E2E 模式 (``E2E_TESTING=true``) 下, 非激活视图不应调用 view_factory(),
    避免 VM 构造链 (DataSourceViewModel → AIService → litellm import 18s+) 阻塞 MainThread.

    直接渲染 ``_build_pages_stack`` Component (不通过 AppLayout 间接渲染),
    因为 ``render_once`` 不递归渲染子 Component。
    """

    def test_e2e_mode_skips_non_active_view_construction(
        self, mock_i18n_state, mock_app_colors_state, monkeypatch
    ) -> None:
        """E2E 模式下非激活视图的 factory 不被调用 (只构造 current_tab=MARKET 对应的 HomeView)."""
        from ui import app_layout as mod

        # Mock 6 个子视图, 用 MagicMock 跟踪调用
        view_mocks: dict[str, MagicMock] = {}
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
        ]:
            mock = MagicMock(return_value=MagicMock(name=view_name))
            view_mocks[view_name] = mock
            monkeypatch.setattr(mod, view_name, mock)

        # 启用 E2E 模式, current_tab=MARKET (0) → 只构造 HomeView
        monkeypatch.setenv("E2E_TESTING", "true")
        try:
            component = make_component(mod._build_pages_stack, 0)
            render_once(component)
        finally:
            monkeypatch.delenv("E2E_TESTING", raising=False)

        # E2E 模式 + current_tab=MARKET (0): 只有 HomeView 被构造 (active=True)
        view_mocks["HomeView"].assert_called_once_with(active=True)
        # 其他 5 个视图不应被调用
        for view_name in ["ScreenerView", "BacktestView", "DataExplorerView", "TaskCenterView", "SettingsView"]:
            view_mocks[view_name].assert_not_called(), f"E2E 模式下 {view_name} 不应被构造"

    def test_normal_mode_constructs_all_views(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """正常模式下所有 6 个视图都被构造 (非 E2E 模式)."""
        from ui import app_layout as mod

        # Mock 6 个子视图
        view_mocks: dict[str, MagicMock] = {}
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
        ]:
            mock = MagicMock(return_value=MagicMock(name=view_name))
            view_mocks[view_name] = mock
            monkeypatch.setattr(mod, view_name, mock)

        # 不启用 E2E 模式, current_tab=MARKET (0)
        monkeypatch.delenv("E2E_TESTING", raising=False)
        component = make_component(mod._build_pages_stack, 0)
        render_once(component)

        # 正常模式下所有 6 个视图都被构造
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
        ]:
            view_mocks[view_name].assert_called_once(), f"正常模式下 {view_name} 应被构造"
