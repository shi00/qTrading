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
    run_unmount_effects,
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

    # --- Mock 7 个子视图 (避免触发各自 VM 渲染) ---
    for view_name in [
        "HomeView",
        "ScreenerView",
        "BacktestView",
        "DataExplorerView",
        "TaskCenterView",
        "SettingsView",
        "WatchlistView",
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

        # Mock 7 个子视图, 用 MagicMock 跟踪调用
        view_mocks: dict[str, MagicMock] = {}
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
            "WatchlistView",
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
        # 其他 6 个视图不应被调用
        for view_name in [
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
            "WatchlistView",
        ]:
            view_mocks[view_name].assert_not_called(), f"E2E 模式下 {view_name} 不应被构造"

    def test_normal_mode_constructs_all_views(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """正常模式下所有 7 个视图都被构造 (非 E2E 模式)."""
        from ui import app_layout as mod

        # Mock 7 个子视图
        view_mocks: dict[str, MagicMock] = {}
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
            "WatchlistView",
        ]:
            mock = MagicMock(return_value=MagicMock(name=view_name))
            view_mocks[view_name] = mock
            monkeypatch.setattr(mod, view_name, mock)

        # 不启用 E2E 模式, current_tab=MARKET (0)
        monkeypatch.delenv("E2E_TESTING", raising=False)
        component = make_component(mod._build_pages_stack, 0)
        render_once(component)

        # 正常模式下所有 7 个视图都被构造
        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
            "WatchlistView",
        ]:
            view_mocks[view_name].assert_called_once(), f"正常模式下 {view_name} 应被构造"


# ============================================================================
# _on_navigate 测试: TOPIC_NAVIGATE PubSub 事件处理
# ============================================================================


def _get_navigate_handler(env: dict) -> Any:
    """从 pubsub.subscribe_topic 调用中提取 _on_navigate handler."""
    page = env["page"]
    subscribe_calls = page.pubsub.subscribe_topic.call_args_list
    assert len(subscribe_calls) >= 1, "subscribe_topic 未被调用"
    for call in subscribe_calls:
        args, _ = call
        if args and args[0] == env["mod"].TOPIC_NAVIGATE:
            return args[1]
    raise AssertionError(f"未找到 {env['mod'].TOPIC_NAVIGATE} 订阅的 handler")


class TestOnNavigate:
    """_on_navigate PubSub 事件处理测试 (P1-1 覆盖率补缺)."""

    def test_unknown_topic_early_return(self, app_layout_env) -> None:
        """topic != TOPIC_NAVIGATE 时早返回, 不调 page.run_task."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        handler("unknown_topic", "screener")
        assert not page.run_task.called, "未知 topic 应早返回"

    def test_valid_target_invokes_run_task(self, app_layout_env) -> None:
        """合法 tab 名 + 不同 tab → page.run_task(_do_tab_switch, target)."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        # current_tab 默认 MARKET (0), 导航到 SCREENER (1)
        handler(env["mod"].TOPIC_NAVIGATE, "screener")
        run_task_calls = page.run_task.call_args_list
        assert len(run_task_calls) >= 1, "合法导航应调用 run_task"
        call = run_task_calls[0]
        handler_fn = call.args[0]
        args = call.args[1:]
        assert inspect.iscoroutinefunction(handler_fn), "handler 必须为协程函数"
        assert args == (1,), f"应传 target_tab=1 (SCREENER), 实际 args={args}"

    def test_unknown_target_keyerror_logged(self, app_layout_env) -> None:
        """非法 tab 名 → KeyError 捕获 + logger.warning, 不调 run_task."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch.object(env["mod"].logger, "warning") as mock_warn:
            handler(env["mod"].TOPIC_NAVIGATE, "nonexistent_tab")
            mock_warn.assert_called_once_with("[AppLayout] Unknown navigation target: %s", "nonexistent_tab")
        assert page.run_task.call_count == 0, "非法 tab 名不应调用 run_task"

    def test_same_tab_early_return(self, app_layout_env) -> None:
        """target_tab == current_tab 时早返回, 不调 run_task."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        # current_tab 默认 MARKET (0), 导航到 market → 相同 tab
        handler(env["mod"].TOPIC_NAVIGATE, "market")
        assert not page.run_task.called, "相同 tab 应早返回"

    def test_page_none_early_return(self, app_layout_env) -> None:
        """page=None 时早返回, 不抛异常."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch("ui.app_layout._get_page", return_value=None):
            handler(env["mod"].TOPIC_NAVIGATE, "screener")
        assert not page.run_task.called, "page=None 应早返回"


# ============================================================================
# _setup_navigate 测试: page=None 边界
# ============================================================================


class TestSetupNavigatePageNone:
    """_setup_navigate page=None 早返回测试 (P1-1 覆盖率补缺)."""

    def test_page_none_skips_subscribe(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        """page=None 时 _setup_navigate 早返回, pubsub.subscribe_topic 不被调用."""
        from ui import app_layout as mod

        for view_name in [
            "HomeView",
            "ScreenerView",
            "BacktestView",
            "DataExplorerView",
            "TaskCenterView",
            "SettingsView",
            "WatchlistView",
        ]:
            monkeypatch.setattr(mod, view_name, MagicMock(return_value=MagicMock(name=view_name)))
        monkeypatch.setattr(mod, "I18n", MagicMock(get=lambda key, *a, **kw: key))
        monkeypatch.setattr(mod, "UILogger", MagicMock())

        component = make_component(mod.AppLayout)
        # 不调用 run_mount_effects (会触发 _setup_navigate), 改为手动 patch _get_page 返回 None
        page = FakePage()
        page.pubsub = MagicMock()
        page.pubsub.subscribe_topic = MagicMock()
        page.pubsub.unsubscribe_topic = MagicMock()

        with patch("ui.app_layout._get_page", return_value=None):
            run_mount_effects(component, page=page)

        assert not page.pubsub.subscribe_topic.called, "page=None 时不应订阅 TOPIC_NAVIGATE"


# ============================================================================
# _cleanup_navigate 测试: unmount 取消订阅
# ============================================================================


class TestCleanupNavigate:
    """_cleanup_navigate unmount 取消订阅测试 (P1-1 覆盖率补缺)."""

    def test_unmount_calls_unsubscribe(self, app_layout_env) -> None:
        """组件 unmount 时 _cleanup_navigate 调用 page.pubsub.unsubscribe_topic."""
        env = app_layout_env
        component = env["component"]
        page = env["page"]
        page.pubsub.unsubscribe_topic.reset_mock()

        run_unmount_effects(component)

        page.pubsub.unsubscribe_topic.assert_called_once_with(env["mod"].TOPIC_NAVIGATE)


# ============================================================================
# UX-01: _parse_navigate_message 纯函数测试 (导航深链协议 "<tab>[:<subtab>]")
# ============================================================================


class TestParseNavigateMessage:
    """UX-01: _parse_navigate_message 解析 "<tab>[:<subtab>]" 协议."""

    @pytest.mark.parametrize(
        ("message", "expected"),
        [
            ("screener", ("screener", None)),
            ("settings:data", ("settings", "data")),
            ("Settings:DATA", ("Settings", "data")),  # tab 段原样 (upper 查 NavTabs) / subtab 段 lower
            ("a:b:c", ("", None)),  # 多段非法
            (":data", ("", None)),  # 空 tab 非法
            ("settings:", ("", None)),  # 空 subtab 非法
            ("", ("", None)),  # 空消息非法
        ],
    )
    def test_parse(self, message: str, expected: tuple[str, str | None]) -> None:
        """合法/非法消息格式解析."""
        from ui.app_layout import _parse_navigate_message

        assert _parse_navigate_message(message) == expected


# ============================================================================
# UX-01: _on_navigate 深链协议处理测试
# ============================================================================


class TestOnNavigateDeepLink:
    """UX-01: _on_navigate 深链 "<tab>:<subtab>" 协议处理测试.

    覆盖:
    - 深链切页: run_task(target) + SettingsView 收到 target_subtab
    - 重复深链: seq 递增 (函数式更新防 stale closure)
    - 未知子页: 降级切主 tab + target_subtab 保持 None
    - 格式非法: warning + 不 run_task
    - 非设置页带子页: 子页段忽略 + 正常切主 tab
    - UX-04: screener 段语义 = 股票代码, ScreenerView 收到 stock_filter_request,
      且不污染 settings request
    """

    def _render_pages_stack(self, env: dict) -> MagicMock:
        """重渲染 AppLayout 并渲染 _build_pages_stack 子组件.

        render_once 不递归渲染子 Component, 需手动渲染 _build_pages_stack
        才会调用 SettingsView/ScreenerView mock (携带最新 request props)。
        """
        result = render_once(env["component"])
        env["result"] = result
        body = result.content.controls[2]  # Row([nav_rail, VerticalDivider, body])
        stack_component = body.content
        render_once(stack_component)
        settings_mock = env["mod"].SettingsView
        assert "active" in settings_mock.call_args.kwargs, "SettingsView 应被 _build_pages_stack 构造 (含 active prop)"
        return settings_mock

    def test_deep_link_switches_tab_and_passes_subtab(self, app_layout_env) -> None:
        """MARKET → "settings:data": run_task(SETTINGS) + SettingsView 收到 target_subtab=("data",1)."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()
        env["mod"].SettingsView.reset_mock()

        handler(env["mod"].TOPIC_NAVIGATE, "settings:data")
        _, args, _ = _await_run_task_handler(page)
        assert args == (int(env["mod"].NavTabs.SETTINGS),), "深链应切换到 SETTINGS 主 tab"

        settings_mock = self._render_pages_stack(env)
        assert settings_mock.call_args.kwargs.get("target_subtab") == ("data", 1)

    def test_repeated_deep_link_increments_seq(self, app_layout_env) -> None:
        """两次 "settings:data" → seq 递增为 2 (函数式更新防 stale closure)."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        env["mod"].SettingsView.reset_mock()

        handler(env["mod"].TOPIC_NAVIGATE, "settings:data")
        handler(env["mod"].TOPIC_NAVIGATE, "settings:data")
        settings_mock = self._render_pages_stack(env)
        assert settings_mock.call_args.kwargs.get("target_subtab") == ("data", 2)

    def test_screener_deep_link_passes_stock_filter(self, app_layout_env) -> None:
        """UX-04: MARKET → "screener:000001": run_task(SCREENER) + ScreenerView 收到
        stock_filter_request=("000001", 1) + SettingsView target_subtab 为 None
        (防 settings request 污染回归)."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()
        env["mod"].ScreenerView.reset_mock()
        env["mod"].SettingsView.reset_mock()

        handler(env["mod"].TOPIC_NAVIGATE, "screener:000001")
        _, args, _ = _await_run_task_handler(page)
        assert args == (int(env["mod"].NavTabs.SCREENER),), "深链应切换到 SCREENER 主 tab"

        self._render_pages_stack(env)
        screener_mock = env["mod"].ScreenerView
        assert "active" in screener_mock.call_args.kwargs, "ScreenerView 应被 _build_pages_stack 构造 (含 active prop)"
        assert screener_mock.call_args.kwargs.get("stock_filter_request") == ("000001", 1)
        settings_mock = env["mod"].SettingsView
        assert settings_mock.call_args.kwargs.get("target_subtab") is None, "screener 深链不应污染 settings request"

    def test_screener_repeated_deep_link_increments_seq(self, app_layout_env) -> None:
        """UX-04: 两次 "screener:000001" → seq 递增为 2 (函数式更新防 stale closure)."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        env["mod"].ScreenerView.reset_mock()

        handler(env["mod"].TOPIC_NAVIGATE, "screener:000001")
        handler(env["mod"].TOPIC_NAVIGATE, "screener:000001")
        self._render_pages_stack(env)
        screener_mock = env["mod"].ScreenerView
        assert screener_mock.call_args.kwargs.get("stock_filter_request") == ("000001", 2)

    def test_screener_deep_link_code_with_suffix_normalized_lower(self, app_layout_env) -> None:
        """UX-04 契约锚定: "screener:000001.SZ" → 协议解析 .lower() 归一为 "000001.sz".

        匹配语义不受影响 (VM 层 str.contains case=False); 过滤框显示小写值
        为已知接受行为 (对抗检视 MINOR-2, 化妆级瑕疵记录在案).
        """
        env = app_layout_env
        handler = _get_navigate_handler(env)
        env["mod"].ScreenerView.reset_mock()

        handler(env["mod"].TOPIC_NAVIGATE, "screener:000001.SZ")
        self._render_pages_stack(env)
        screener_mock = env["mod"].ScreenerView
        assert screener_mock.call_args.kwargs.get("stock_filter_request") == ("000001.sz", 1)

    def test_unknown_subtab_falls_back_to_main_tab(self, app_layout_env) -> None:
        """ "settings:nonexistent" → warning + 仍切 settings + target_subtab 保持 None."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()
        env["mod"].SettingsView.reset_mock()

        with patch.object(env["mod"].logger, "warning") as mock_warn:
            handler(env["mod"].TOPIC_NAVIGATE, "settings:nonexistent")
            assert mock_warn.call_count == 1, "未知子页应记录 warning"
        _, args, _ = _await_run_task_handler(page)
        assert args == (int(env["mod"].NavTabs.SETTINGS),), "未知子页应降级切主 tab"

        settings_mock = self._render_pages_stack(env)
        assert settings_mock.call_args.kwargs.get("target_subtab") is None

    def test_invalid_format_message_ignored(self, app_layout_env) -> None:
        """ "settings:data:extra" → warning (含原消息) + 不 run_task."""
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()

        with patch.object(env["mod"].logger, "warning") as mock_warn:
            handler(env["mod"].TOPIC_NAVIGATE, "settings:data:extra")
            mock_warn.assert_called_once_with(
                "[AppLayout] Invalid navigation message format: %s", "settings:data:extra"
            )
        assert not page.run_task.called, "格式非法消息不应调用 run_task"

    def test_non_settings_tab_with_subtab_ignores_subtab(self, app_layout_env) -> None:
        """ "backtest:data" → warning + 正常切 backtest 主 tab + 两个 request prop 均为 None.

        UX-04 后 screener 段为合法深链 (股票代码), 降级语义样本改用 backtest
        (非当前 tab, 避开 same-tab 早返回路径)。
        """
        env = app_layout_env
        handler = _get_navigate_handler(env)
        page = env["page"]
        page.run_task.reset_mock()
        env["mod"].SettingsView.reset_mock()
        env["mod"].ScreenerView.reset_mock()

        with patch.object(env["mod"].logger, "warning") as mock_warn:
            handler(env["mod"].TOPIC_NAVIGATE, "backtest:data")
            assert mock_warn.call_count == 1, "未知深链 tab 带子页应记录 warning"
        _, args, _ = _await_run_task_handler(page)
        assert args == (int(env["mod"].NavTabs.BACKTEST),), "应正常切换到 BACKTEST 主 tab"

        self._render_pages_stack(env)
        settings_mock = env["mod"].SettingsView
        assert settings_mock.call_args.kwargs.get("target_subtab") is None
        screener_mock = env["mod"].ScreenerView
        assert screener_mock.call_args.kwargs.get("stock_filter_request") is None


# ============================================================================
# _build_nav_destinations(running_count > 0) 测试: nav_tasks 角标分支
# ============================================================================


class TestBuildNavDestinationsWithBadge:
    """_build_nav_destinations running_count > 0 角标构造测试 (P1-1 覆盖率补缺)."""

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n):
        self.mock_i18n = mock_i18n
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: key
        with patch("ui.app_layout.I18n", self.mock_i18n):
            yield

    def test_running_count_positive_adds_badge_to_nav_tasks(self):
        """running_count > 0 时 nav_tasks 项 icon 为 ft.Stack (带角标), 其他项仍为 IconData."""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations(running_count=5)
        assert len(destinations) == 7

        # nav_tasks 是第 5 项 (index=4), running_count > 0 → icon 为 ft.Stack
        nav_tasks_dest = destinations[4]
        assert isinstance(nav_tasks_dest.icon, ft.Stack), "running_count > 0 时 nav_tasks icon 应为 ft.Stack (带角标)"

        # 其他项 icon 仍为 str (ft.IconData)
        for i, dest in enumerate(destinations):
            if i != 4:
                assert not isinstance(dest.icon, ft.Stack), f"第 {i} 项不应有角标"

    def test_running_count_zero_no_badge(self):
        """running_count=0 时所有项 icon 均为 IconData (无角标)."""
        from ui.app_layout import _build_nav_destinations

        destinations = _build_nav_destinations(running_count=0)
        for dest in destinations:
            assert not isinstance(dest.icon, ft.Stack), "running_count=0 时不应有角标"
