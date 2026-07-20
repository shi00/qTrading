"""ui/app_layout.py 组件运行时测试 (Task 4.1).

覆盖:
1. R2/R16 红线守卫: CancelledError raise / page.run_task 调度
2. _do_tab_switch: 防抖完成后切换 tab / CancelledError raise / new_tab == current_tab 早返回
3. _on_nav_change: page None / page.run_task / selected == current_tab 早返回
4. _toggle_nav: nav_collapsed 状态切换
5. _setup_resize: page.on_resize 注册 / page None 早返回
6. resize 防抖: 多次 resize 取消旧任务 / debounce_task_ref 取消置 None
7. _cleanup_resize: 取消 pending + 置 None + page.on_resize = None

测试范式参考 test_system_tab.py (FakePage + component_renderer + _invoke helper +
_await_run_task_handler + asyncio.run 异步 handler).
现有 test_app_layout_contract.py 覆盖契约守护 + 模块级纯函数, 本文件补充运行时测试, 不重复覆盖.
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
    run_unmount_effects,
)

pytestmark = pytest.mark.unit


# ============================================================================
# 辅助函数
# ============================================================================


def _make_event(
    width: float = 800.0,
    height: float = 600.0,
    selected_index: int = 0,
) -> MagicMock:
    """构造 ft.ControlEvent mock, 支持 width/height/selected_index 属性."""
    e = MagicMock()
    e.control.selected_index = selected_index
    e.width = width
    e.height = height
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

    # --- 挂载组件 (run_mount_effects 触发 _setup_resize, 设置 page.on_resize) ---
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
        """R2: _do_tab_switch + _do_resize 必须有 CancelledError raise 守卫."""
        from pathlib import Path

        import ui.app_layout as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        cancelled_count = source.count("except asyncio.CancelledError")
        raise_count = source.count("raise  # R2")
        assert cancelled_count >= 2, f"应有 ≥2 处 CancelledError 守卫, 实际 {cancelled_count}"
        assert raise_count >= 2, f"应有 ≥2 处 raise # R2, 实际 {raise_count}"

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
# _setup_resize 测试: page.on_resize 注册 / page None 早返回
# ============================================================================


class TestSetupResize:
    """_setup_resize 行为测试.

    _setup_resize 在 use_effect 首次执行 (run_mount_effects), 已在 fixture 中触发.
    """

    def test_page_on_resize_registered_on_mount(self, app_layout_env) -> None:
        """挂载后 page.on_resize 应被赋值为 _on_resize 闭包."""
        env = app_layout_env
        page = env["page"]
        assert callable(page.on_resize), "挂载后 page.on_resize 应被注册"

    def test_page_none_skips_on_resize_registration(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """page=None 时 _setup_resize 早返回, page.on_resize 不被注册."""
        from ui import app_layout as mod

        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
        ]:
            monkeypatch.setattr(mod, view_name, MagicMock(return_value=MagicMock(name=view_name)))
        monkeypatch.setattr(mod, "UILogger", MagicMock())
        mock_i18n = MagicMock()
        mock_i18n.get.side_effect = lambda key, *a, **kw: key
        monkeypatch.setattr(mod, "I18n", mock_i18n)

        component = make_component(mod.AppLayout)
        page = _make_fake_page()
        page.on_resize = None  # type: ignore[attr-defined]  # 明确初始为 None

        with patch("ui.app_layout._get_page", return_value=None):
            run_mount_effects(component, page=page)

        assert page.on_resize is None, "page=None 时 page.on_resize 不应被注册"  # type: ignore[attr-defined]


# ============================================================================
# resize 防抖测试: 多次 resize 取消旧任务 / debounce_task_ref 置 None
# ============================================================================


class TestResizeDebounce:
    """resize 防抖行为测试.

    _on_resize 内部:
    1. if debounce_task_ref.current is not None: current.cancel()
    2. debounce_task_ref.current = page.run_task(_do_resize, w, h)
    """

    def test_multiple_resize_cancels_previous_task(self, app_layout_env) -> None:
        """多次 resize → 前一个 debounce_task 被 cancel.

        使用 side_effect 让每次 page.run_task 返回不同的 mock task,
        验证前一个 task.cancel 被调用.
        """
        env = app_layout_env
        page = env["page"]
        on_resize = page.on_resize
        assert on_resize is not None

        # 准备 3 个独立的 mock task, 每次 run_task 返回不同的
        tasks = [MagicMock(name=f"task_{i}") for i in range(3)]
        page.run_task = MagicMock(side_effect=tasks)  # type: ignore[method-assign]

        # 第一次 resize: current=None → 不调 cancel; run_task → current=task[0]
        _invoke(on_resize, _make_event(width=800.0, height=600.0))
        assert page.run_task.call_count == 1
        tasks[0].cancel.assert_not_called()

        # 第二次 resize: current=task[0] → task[0].cancel(); run_task → current=task[1]
        _invoke(on_resize, _make_event(width=900.0, height=700.0))
        assert page.run_task.call_count == 2
        tasks[0].cancel.assert_called_once()
        tasks[1].cancel.assert_not_called()

        # 第三次 resize: current=task[1] → task[1].cancel(); run_task → current=task[2]
        _invoke(on_resize, _make_event(width=1000.0, height=800.0))
        assert page.run_task.call_count == 3
        tasks[1].cancel.assert_called_once()
        tasks[2].cancel.assert_not_called()

    def test_do_resize_clears_debounce_task_ref_after_completion(self, app_layout_env) -> None:
        """_do_resize 防抖完成后 debounce_task_ref.current 置 None.

        通过观察后续 on_resize 不调 task.cancel 验证 current 已被置 None:
        - 第一次 resize: current=None → 不调 cancel; run_task → current=task1
        - await _do_resize 完成 → current=None
        - 第二次 resize: current=None → 不调 cancel; run_task → current=task2
        """
        env = app_layout_env
        page = env["page"]
        on_resize = page.on_resize
        assert on_resize is not None

        # 准备 2 个独立 task
        tasks = [MagicMock(name=f"task_{i}") for i in range(2)]
        page.run_task = MagicMock(side_effect=tasks)  # type: ignore[method-assign]

        # 第一次 resize: 触发 _do_resize(w, h) 调度
        _invoke(on_resize, _make_event(width=800.0, height=600.0))
        assert page.run_task.call_count == 1
        tasks[0].cancel.assert_not_called()

        # 提取 _do_resize handler 并真实 await (模拟防抖完成)
        handler, args, _ = _await_run_task_handler(page)
        asyncio.run(handler(*args))

        # _do_resize 完成后 debounce_task_ref.current = None
        # 第二次 resize: current=None → 不调 task[1].cancel
        _invoke(on_resize, _make_event(width=900.0, height=700.0))
        assert page.run_task.call_count == 2
        assert not tasks[1].cancel.called, "current 已被 _do_resize 置 None, 不应再调 cancel"

    def test_do_resize_cancelled_error_propagates(self, app_layout_env) -> None:
        """R2: _do_resize 中 CancelledError 必须 raise."""
        env = app_layout_env
        page = env["page"]
        on_resize = page.on_resize
        assert callable(on_resize)

        _invoke(on_resize, _make_event(width=800.0, height=600.0))
        handler, args, _ = _await_run_task_handler(page)

        with patch("ui.app_layout.asyncio.sleep", side_effect=asyncio.CancelledError()):
            with pytest.raises(asyncio.CancelledError):
                asyncio.run(handler(*args))


# ============================================================================
# _cleanup_resize 测试: 取消 pending + 置 None + page.on_resize 置 None
# ============================================================================


class TestCleanupResize:
    """_cleanup_resize 行为测试.

    _cleanup_resize 在组件 unmount 时被 use_effect 触发 (run_unmount_effects).
    """

    def test_cleanup_cancels_pending_debounce_task(self, app_layout_env) -> None:
        """unmount → _cleanup_resize 取消 pending debounce_task.

        先触发一次 resize 让 debounce_task_ref.current 非 None, 然后卸载组件,
        验证 task.cancel 被调用.
        """
        env = app_layout_env
        page = env["page"]
        on_resize = page.on_resize
        assert on_resize is not None

        # 触发 resize, debounce_task_ref.current = mock_task
        mock_task = MagicMock(name="pending_task")
        page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
        _invoke(on_resize, _make_event(width=800.0, height=600.0))

        # 卸载组件 → _cleanup_resize 触发
        run_unmount_effects(env["component"])

        assert mock_task.cancel.called, "_cleanup_resize 必须取消 pending task"

    def test_cleanup_resets_page_on_resize_to_none(self, app_layout_env) -> None:
        """unmount → _cleanup_resize 把 page.on_resize 置 None."""
        env = app_layout_env
        page = env["page"]
        assert page.on_resize is not None, "挂载后 on_resize 应非 None"

        run_unmount_effects(env["component"])

        assert page.on_resize is None, "_cleanup_resize 必须置 page.on_resize = None"

    def test_cleanup_safe_when_no_pending_task(self, app_layout_env) -> None:
        """无 pending task 时 _cleanup_resize 安全执行 (不抛异常)."""
        env = app_layout_env
        # 不触发 resize, 直接卸载
        run_unmount_effects(env["component"])
        # 验证 page.on_resize 已被置 None
        assert env["page"].on_resize is None
