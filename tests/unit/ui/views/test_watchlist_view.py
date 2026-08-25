"""watchlist_view 单元测试 (FR-UX-004, Task 4.2).

测试策略（参考 test_home_view.py / test_task_center_view.py 范式）：
- 纯函数：``_get_page`` / ``_safe_show_toast`` / ``_build_watchlist_row`` 直接单测
- 组件运行时：``make_component`` + ``run_mount_effects`` + ``render_once`` 驱动声明式组件
- FakeViewModel：满足 ``use_viewmodel`` 契约（state/subscribe/dispose），不依赖真实 CacheManager
- R2 红线：CancelledError 必须 raise（不吞没）
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import flet as ft
import pytest
from flet.components.component import Component

from ui.viewmodels.watchlist_view_model import WatchlistRow, WatchlistState
from ui.views.watchlist_view import (
    GITHUB_ISSUES_URL,
    WatchlistView,
    _build_watchlist_row,
    _get_page,
    _safe_show_toast,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# FakeViewModel
# ---------------------------------------------------------------------------


class _FakeWatchlistViewModel:
    """满足 use_viewmodel 契约的 fake VM。"""

    def __init__(self, state: WatchlistState | None = None) -> None:
        self._state: WatchlistState = state or WatchlistState()
        self._subscribers: list[Any] = []
        self.dispose_called: bool = False
        self.subscribe_called: bool = False
        self.method_calls: list[tuple[str, dict[str, Any]]] = []

    @property
    def state(self) -> WatchlistState:
        return self._state

    def subscribe(self, callback: Any) -> Any:
        self.subscribe_called = True
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        for cb in self._subscribers:
            cb(self._state)

    def _set_state(self, **changes: Any) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    def dispose(self) -> None:
        self.dispose_called = True
        self._subscribers.clear()

    async def load_watchlist(self) -> None:
        self.method_calls.append(("load_watchlist", {}))

    async def remove_from_watchlist(self, ts_code: str) -> None:
        self.method_calls.append(("remove_from_watchlist", {"ts_code": ts_code}))


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _make_row(
    ts_code: str = "000001.SZ",
    stock_name: str = "平安银行",
    added_at: str = "2026-07-29",
    note: str = "",
) -> WatchlistRow:
    return WatchlistRow(
        ts_code=ts_code,
        stock_name=stock_name,
        added_at=added_at,
        note=note,
    )


def _collect_all_controls(root: ft.Control | None) -> list[ft.Control]:
    """深度优先遍历控件树（参考 test_task_center_view._collect_all_controls）。"""
    if root is None or not isinstance(root, ft.Control):
        return []
    result: list[ft.Control] = [root]
    for attr in ("controls", "items", "tabs"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                if child is not None:
                    result.extend(_collect_all_controls(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        result.extend(_collect_all_controls(content))
    return result


def _find_component_by_fn_name(root: Any, fn_name: str) -> Component | None:
    """在渲染树中按 fn.__name__ 查找 Component (子组件未展开时的定位)."""
    visited: set[int] = set()

    def _walk(c: Any) -> Component | None:
        if c is None or id(c) in visited:
            return None
        visited.add(id(c))
        if isinstance(c, Component):
            if getattr(c.fn, "__name__", "") == fn_name:
                return c
            for v in list(c.args) + list(c.kwargs.values()):
                result = _walk(v)
                if result is not None:
                    return result
        elif isinstance(c, list):
            for x in c:
                result = _walk(x)
                if result is not None:
                    return result
        elif isinstance(c, ft.Control):
            for attr in ("controls", "items", "tabs", "content"):
                children = getattr(c, attr, None)
                if isinstance(children, list):
                    for x in children:
                        result = _walk(x)
                        if result is not None:
                            return result
                elif children is not None:
                    result = _walk(children)
                    if result is not None:
                        return result
        return None

    return _walk(root)


def _click_icon_button(button: ft.IconButton) -> None:
    """触发 IconButton.on_click 回调（on_click 类型为 Optional[Callable]，需 None-guard 满足 pyright）。"""
    assert button.on_click is not None
    button.on_click(MagicMock())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支，pyright 推断为 0 参，运行时接收 ControlEvent]


def _rerender(component: Any) -> Any:
    """重新渲染组件 (声明式范式下 set_state 后需手动 render_once 让闭包捕获新 state)。

    参考 tests/unit/ui/views/settings_tabs/test_ai_brain_tab.py:_rerender。
    """
    from tests.unit.ui.component_renderer import render_once

    return render_once(component)


# ---------------------------------------------------------------------------
# 纯函数测试
# ---------------------------------------------------------------------------


class TestGetPage:
    def test_returns_none_outside_render_context(self) -> None:
        result = _get_page()
        assert result is None

    def test_returns_page_in_render_context(self) -> None:
        from flet.controls.context import _context_page
        from tests.unit.ui.component_renderer import FakePage

        fake_page = FakePage()
        token = _context_page.set(fake_page)
        try:
            result = _get_page()
            assert result is fake_page
        finally:
            _context_page.reset(token)


class TestSafeShowToast:
    def test_calls_show_toast_when_present(self) -> None:
        page = MagicMock()
        page.show_toast = MagicMock()
        _safe_show_toast(page, "test message", "success")
        page.show_toast.assert_called_once_with("test message", "success")

    def test_handles_missing_show_toast(self) -> None:
        page = MagicMock()
        page.show_toast = None
        # 不应抛异常
        _safe_show_toast(page, "test message", "info")

    def test_default_msg_type_is_info(self) -> None:
        page = MagicMock()
        page.show_toast = MagicMock()
        _safe_show_toast(page, "msg")
        page.show_toast.assert_called_once_with("msg", "info")


class TestBuildWatchlistRow:
    def test_with_note(self) -> None:
        row = _make_row(note="重点关注")
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        assert isinstance(container, ft.Container)
        # 验证回调绑定
        row_content = container.content
        assert isinstance(row_content, ft.Row)
        # 找到 IconButton 并触发回调
        icon_buttons = [c for c in row_content.controls if isinstance(c, ft.IconButton)]
        assert len(icon_buttons) == 1
        _click_icon_button(icon_buttons[0])
        on_remove.assert_called_once_with("000001.SZ")

    def test_without_note(self) -> None:
        row = _make_row(note="")
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        # 验证不渲染备注 Text（只有名称 + 子标题 2 个 Text）
        texts = _collect_all_controls(container)
        text_values = [getattr(t, "value", "") for t in texts if isinstance(t, ft.Text)]
        assert "重点关注" not in text_values

    def test_empty_stock_name_uses_ts_code(self) -> None:
        row = _make_row(stock_name="")
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        texts = _collect_all_controls(container)
        text_values = [getattr(t, "value", "") for t in texts if isinstance(t, ft.Text)]
        # stock_name 为空时 name = row.ts_code
        assert "000001.SZ" in text_values

    def test_with_added_at(self) -> None:
        row = _make_row(added_at="2026-01-15")
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        texts = _collect_all_controls(container)
        text_values = [getattr(t, "value", "") for t in texts if isinstance(t, ft.Text)]
        # 子标题包含 ts_code + added_at
        assert any("000001.SZ" in v and "2026-01-15" in v for v in text_values)

    def test_without_added_at(self) -> None:
        row = _make_row(added_at="")
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        texts = _collect_all_controls(container)
        text_values = [getattr(t, "value", "") for t in texts if isinstance(t, ft.Text)]
        # 子标题只有 ts_code
        sub_texts = [v for v in text_values if "000001.SZ" in v]
        assert any(v.strip() == "000001.SZ" for v in sub_texts)

    def test_watchlist_row_renders_view_button(self) -> None:
        """UX-04: on_view 传入时渲染 SEARCH_OUTLINED 查看按钮 (删除按钮前), 回调绑定 ts_code."""
        row = _make_row()
        on_remove = MagicMock()
        on_view = MagicMock()
        container = _build_watchlist_row(row, on_remove, on_view)
        row_content = container.content
        assert isinstance(row_content, ft.Row)
        icon_buttons = [c for c in row_content.controls if isinstance(c, ft.IconButton)]
        assert len(icon_buttons) == 2, "应含查看和删除两个按钮"
        view_button = icon_buttons[0]
        assert view_button.icon == ft.Icons.SEARCH_OUTLINED
        _click_icon_button(view_button)
        on_view.assert_called_once_with("000001.SZ")

    def test_build_row_without_on_view_no_button(self) -> None:
        """UX-04: on_view=None (默认) → 无查看按钮, 仅删除按钮 (位置参数兼容守护)."""
        row = _make_row()
        on_remove = MagicMock()
        container = _build_watchlist_row(row, on_remove)
        row_content = container.content
        icon_buttons = [c for c in row_content.controls if isinstance(c, ft.IconButton)]
        assert len(icon_buttons) == 1
        assert icon_buttons[0].icon == ft.Icons.DELETE_OUTLINE
        assert not any(b.icon == ft.Icons.SEARCH_OUTLINED for b in icon_buttons)


# ---------------------------------------------------------------------------
# 组件运行时测试
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_watchlist_vm(monkeypatch):
    """注入 FakeWatchlistViewModel 替代真实 VM, 并 Mock ConfirmDialog 捕获 on_confirm/on_cancel 回调。

    Mock ConfirmDialog 模式参考 test_ai_brain_tab.py (P1-4 批次 2):
    open_state=True 时捕获 on_confirm/on_cancel, 供测试手动触发确认/取消流程。
    """
    import ui.views.watchlist_view as watchlist_view_module

    fake_vm = _FakeWatchlistViewModel()
    monkeypatch.setattr(watchlist_view_module, "WatchlistViewModel", lambda: fake_vm)

    # Mock ConfirmDialog: 捕获 on_confirm/on_cancel 回调 (open_state=True 时)
    captured_callbacks: dict[str, Any] = {}

    def _fake_confirm_dialog(**kwargs: Any) -> Any:
        if kwargs.get("open_state"):
            captured_callbacks["on_confirm"] = kwargs.get("on_confirm")
            captured_callbacks["on_cancel"] = kwargs.get("on_cancel")
        return MagicMock(name="ConfirmDialog")

    monkeypatch.setattr(watchlist_view_module, "ConfirmDialog", _fake_confirm_dialog)
    fake_vm.captured_callbacks = captured_callbacks  # type: ignore[attr-defined]  # [reason: 测试桩动态挂载捕获 dict, 非 VM 契约属性]

    return fake_vm


@pytest.fixture
def mock_i18n_for_view(monkeypatch):
    """Mock I18n.get 返回 key 本身（不依赖 locale 文件）。"""
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr("ui.views.watchlist_view.I18n", mock_i18n)
    return mock_i18n


class TestWatchlistViewRendering:
    """测试 WatchlistView 渲染分支（loading/empty/list/error）。"""

    def test_renders_loading_state(self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once

        mock_watchlist_vm._state = WatchlistState(is_loading=True)
        component = make_component(WatchlistView, active=True)
        # run_mount_effects 会触发 _load_effect（调用 vm.load_watchlist）
        # 但 loading state 已预设，load_watchlist 是 no-op
        from tests.unit.ui.component_renderer import run_mount_effects

        run_mount_effects(component)
        result = render_once(component)

        controls = _collect_all_controls(result)
        assert any(isinstance(c, ft.ProgressRing) for c in controls)

    def test_renders_empty_state(self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        mock_watchlist_vm._state = WatchlistState(
            watchlist_rows=(),
            is_loading=False,
            load_error=None,
        )
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        result = render_once(component)

        controls = _collect_all_controls(result)
        # EmptyState 被 Renderer 作为子组件渲染（日志可见 _render_component 调用）
        # 验证不渲染 loading（ProgressRing）和 list（STAR_OUTLINED）状态
        assert not any(isinstance(c, ft.ProgressRing) for c in controls)
        assert not any(isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.STAR_OUTLINED for c in controls)

    def test_renders_list_with_rows(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        rows = (_make_row(ts_code="000001.SZ", stock_name="平安银行"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        result = render_once(component)

        controls = _collect_all_controls(result)
        # 列表渲染时应含 STAR_OUTLINED icon（非 STAR_BORDER empty state）
        assert any(isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.STAR_OUTLINED for c in controls)
        # 不应渲染 empty state icon
        assert not any(isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.STAR_BORDER for c in controls)

    def test_renders_error_state_on_complete_failure(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """Task 11.2: 完全失败 (rows 空 + load_error 非空) → ErrorState 替换 body."""
        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        from ui.viewmodels import Message

        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            load_error_detail="sanitized error detail",
            is_loading=False,
            watchlist_rows=(),
        )
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        result = render_once(component)

        # ErrorState 作为子组件渲染 (Component 未展开, 通过 fn name 定位)
        error_state = _find_component_by_fn_name(result, "ErrorState")
        assert error_state is not None
        # 验证 detail 参数传递
        assert error_state.kwargs.get("detail") == "sanitized error detail"
        # UX-03 (P2-09): 反馈问题 CTA 图标语义匹配 (不再固定 SETTINGS 误导)
        assert error_state.kwargs.get("cta_icon") == ft.Icons.FEEDBACK
        # 不渲染 EmptyState (无 EmptyState Component)
        empty_state = _find_component_by_fn_name(result, "EmptyState")
        assert empty_state is None

    def test_renders_error_banner_on_partial_failure(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """Task 11.2: 部分失败 (rows 非空 + load_error 非空) → 保留 error_banner + 列表."""
        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        from ui.viewmodels import Message

        rows = (_make_row(ts_code="000001.SZ", stock_name="平安银行"),)
        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            is_loading=False,
            watchlist_rows=rows,
        )
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        result = render_once(component)

        controls = _collect_all_controls(result)
        # 保留 error_banner (含 error key 的 Text)
        texts = [c for c in controls if isinstance(c, ft.Text)]
        assert any(getattr(t, "value", "") == "watchlist_load_failed" for t in texts)
        # 保留列表 (STAR_OUTLINED icon)
        assert any(isinstance(c, ft.Icon) and getattr(c, "icon", None) == ft.Icons.STAR_OUTLINED for c in controls)
        # 不渲染 ErrorState (无 ErrorState Component)
        error_state = _find_component_by_fn_name(result, "ErrorState")
        assert error_state is None


class TestWatchlistViewErrorStateCallbacks:
    """Task 11.2: ErrorState on_retry / on_cta 回调测试."""

    def test_on_retry_calls_vm_load_watchlist(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """on_retry 回调通过 page.run_task 调度 vm.load_watchlist."""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        from ui.viewmodels import Message

        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            is_loading=False,
            watchlist_rows=(),
        )
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[assignment]
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 定位 ErrorState Component, 提取 on_retry 回调
        error_state = _find_component_by_fn_name(result, "ErrorState")
        assert error_state is not None
        on_retry = error_state.kwargs.get("on_retry")
        assert on_retry is not None

        # 重置 mock (过滤 mount 时 load_watchlist 的调用)
        mock_watchlist_vm.method_calls.clear()
        page.run_task.reset_mock()

        # 触发 on_retry
        on_retry()

        # page.run_task 被调用, 传入 vm.load_watchlist
        assert page.run_task.call_args.args[0] == mock_watchlist_vm.load_watchlist

    def test_on_cta_calls_page_launch_url(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """on_cta 回调通过 async wrapper + page.run_task 调度 page.launch_url 打开 GitHub Issues.

        关键验证: handler 须通过 inspect.iscoroutinefunction 检查 (page.launch_url 被
        @deprecated 装饰器破坏 iscoroutinefunction 检测, 须用 async wrapper 包裹).
        """
        import inspect

        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        from ui.viewmodels import Message

        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            is_loading=False,
            watchlist_rows=(),
        )
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.launch_url = AsyncMock()  # type: ignore[assignment]
        page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[assignment]
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 定位 ErrorState Component, 提取 on_cta 回调
        error_state = _find_component_by_fn_name(result, "ErrorState")
        assert error_state is not None
        on_cta = error_state.kwargs.get("on_cta")
        assert on_cta is not None

        # 触发 on_cta
        on_cta()

        # page.run_task 被调用, 传入 async wrapper (须通过 iscoroutinefunction 检查)
        handler = page.run_task.call_args.args[0]
        assert inspect.iscoroutinefunction(handler), "handler 须为 coroutine function (通过 run_task 检查)"

        # 验证 async wrapper 内部调用 page.launch_url(GITHUB_ISSUES_URL)
        import asyncio

        asyncio.run(handler())
        page.launch_url.assert_called_once_with(GITHUB_ISSUES_URL)

    def test_on_retry_no_page_does_not_crash(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """page 为 None 时 on_retry 不应 crash (_get_page 返回 None 保护)."""
        from flet.controls.context import _context_page

        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        from ui.viewmodels import Message

        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            is_loading=False,
            watchlist_rows=(),
        )
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        _context_page.set(None)
        result = render_once(component)

        error_state = _find_component_by_fn_name(result, "ErrorState")
        assert error_state is not None  # noqa: weak-assertion no-crash 测试无显式终态断言, 此为 on_retry() 调用前置 sanity check
        on_retry = error_state.kwargs.get("on_retry")
        assert on_retry is not None  # noqa: weak-assertion no-crash 测试无显式终态断言, 此为 on_retry() 调用前置 sanity check
        # 触发 on_retry 不应抛异常 (page 为 None)
        on_retry()


class TestWatchlistViewViewStock:
    """UX-04 (P2-01): 行「查看」按钮深链跳选股页测试."""

    def test_view_button_click_navigates_to_screener(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """UX-04: 点击行「查看」按钮 → pubsub 广播深链 "screener:000001.SZ"."""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        from ui.views.watchlist_view import TOPIC_NAVIGATE

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.pubsub = MagicMock()  # type: ignore[attr-defined]  # [reason: FakePage 未声明 pubsub, 测试按需挂载 mock]
        run_mount_effects(component, page=page)
        result = render_once(component)

        view_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.SEARCH_OUTLINED
        ]
        assert len(view_buttons) == 1, "列表行应渲染查看按钮"
        # tooltip 为 i18n key (mock_i18n_for_view 返回 key 本身)
        assert view_buttons[0].tooltip == "watchlist_view_stock"

        _click_icon_button(view_buttons[0])

        page.pubsub.send_all_on_topic.assert_called_once_with(TOPIC_NAVIGATE, "screener:000001.SZ")

    def test_view_button_empty_code_falls_back_to_pure_tab_navigation(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """UX-04 R1-MINOR-4: ts_code 为空时降级纯 tab 导航 "screener".

        空代码若发 "screener:" 空段消息会被协议解析判非法整体吞掉, 导航失效.
        """
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        from ui.views.watchlist_view import TOPIC_NAVIGATE

        rows = (_make_row(ts_code=""),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.pubsub = MagicMock()  # type: ignore[attr-defined]  # [reason: FakePage 未声明 pubsub, 测试按需挂载 mock]
        run_mount_effects(component, page=page)
        result = render_once(component)

        view_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.SEARCH_OUTLINED
        ]
        assert len(view_buttons) == 1

        _click_icon_button(view_buttons[0])

        page.pubsub.send_all_on_topic.assert_called_once_with(TOPIC_NAVIGATE, "screener")


class TestWatchlistViewLoadEffect:
    """测试 _load_effect 行为。"""

    def test_load_called_when_active(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        from tests.unit.ui.component_renderer import make_component, run_mount_effects

        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)

        assert ("load_watchlist", {}) in mock_watchlist_vm.method_calls

    def test_load_not_called_when_inactive(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        from tests.unit.ui.component_renderer import make_component, run_mount_effects

        component = make_component(WatchlistView, active=False)
        run_mount_effects(component)

        assert ("load_watchlist", {}) not in mock_watchlist_vm.method_calls


class TestWatchlistViewRemoveCallback:
    """测试移除关注回调（含 R2 CancelledError 传播）。"""

    def test_on_remove_opens_confirm_dialog(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """点击删除按钮弹出 ConfirmDialog (不直接调 run_task 删除)."""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.run_task = MagicMock()
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 找到 IconButton 并触发 on_click
        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        assert len(icon_buttons) == 1
        _click_icon_button(icon_buttons[0])
        # 点击删除按钮不应直接调 run_task (改为打开 ConfirmDialog)
        assert not page.run_task.called
        # 重渲染让 ConfirmDialog 以 open_state=True 渲染并捕获 on_confirm 回调
        _rerender(component)
        on_confirm = mock_watchlist_vm.captured_callbacks.get("on_confirm")
        assert on_confirm is not None  # noqa: weak-assertion 验证 ConfirmDialog 已打开, on_confirm 为后续测试的前置 guard

    def test_confirm_remove_triggers_do_remove(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """on_confirm 触发 _do_confirm_remove → page.run_task(_do_remove, ts_code)."""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.run_task = MagicMock()
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 点击 IconButton → 打开 ConfirmDialog
        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        _click_icon_button(icon_buttons[0])
        _rerender(component)
        # 触发 on_confirm → _do_confirm_remove → page.run_task
        on_confirm = mock_watchlist_vm.captured_callbacks.get("on_confirm")
        assert on_confirm is not None
        on_confirm()
        # page.run_task 被调用, 第一个参数是 _do_remove 闭包, 第二个是 ts_code
        assert page.run_task.call_count == 1
        assert page.run_task.call_args is not None
        assert page.run_task.call_args.args[1] == "000001.SZ"

    def test_cancel_remove_does_not_trigger_do_remove(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """on_cancel 仅关闭对话框, 不调 page.run_task."""
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        page.run_task = MagicMock()
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 点击 IconButton → 打开 ConfirmDialog
        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        _click_icon_button(icon_buttons[0])
        _rerender(component)
        # 触发 on_cancel → _do_cancel_remove (不调 run_task)
        on_cancel = mock_watchlist_vm.captured_callbacks.get("on_cancel")
        assert on_cancel is not None
        on_cancel()
        assert not page.run_task.called

    def test_do_remove_success_calls_vm_and_toast(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        toast_calls: list[tuple[str, str]] = []
        page.show_toast = lambda msg, msg_type="info": toast_calls.append((msg, msg_type))  # type: ignore[assignment]
        run_mount_effects(component, page=page)
        result = render_once(component)

        # 找到移除按钮并触发回调
        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        _click_icon_button(icon_buttons[0])
        _rerender(component)
        # page.run_task 同步执行 _do_remove
        page.run_task = lambda func, *args, **kwargs: asyncio.run(func(*args, **kwargs))  # type: ignore[assignment]
        on_confirm = mock_watchlist_vm.captured_callbacks.get("on_confirm")
        assert on_confirm is not None
        on_confirm()

        assert ("remove_from_watchlist", {"ts_code": "000001.SZ"}) in mock_watchlist_vm.method_calls
        assert any("watchlist_removed" in msg for msg, _ in toast_calls)

    def test_do_remove_cancelled_error_propagates(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """R2 红线：CancelledError 必须 raise（不吞没）。"""

        async def _raise_cancelled(ts_code: str) -> None:
            raise asyncio.CancelledError()

        mock_watchlist_vm.remove_from_watchlist = _raise_cancelled  # type: ignore[assignment]

        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        run_mount_effects(component, page=page)
        result = render_once(component)

        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        _click_icon_button(icon_buttons[0])
        _rerender(component)
        page.run_task = lambda func, *args, **kwargs: asyncio.run(func(*args, **kwargs))  # type: ignore[assignment]
        on_confirm = mock_watchlist_vm.captured_callbacks.get("on_confirm")
        assert on_confirm is not None  # noqa: weak-assertion R2 前置 guard: on_confirm 为后续 pytest.raises 块的调用对象
        # R2 红线：CancelledError 必须 raise（不吞没）；pytest.raises 即为断言
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅验证 CancelledError 类型传播即可，pytest.raises 本身即为强断言
            on_confirm()

    def test_do_remove_exception_shows_error_toast(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """普通异常应显示错误 toast（不吞没，但也不 crash）。"""

        async def _raise_exc(ts_code: str) -> None:
            raise RuntimeError("remove failed")

        mock_watchlist_vm.remove_from_watchlist = _raise_exc  # type: ignore[assignment]

        from tests.unit.ui.component_renderer import (
            FakePage,
            make_component,
            render_once,
            run_mount_effects,
        )

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        page = FakePage()
        toast_calls: list[tuple[str, str]] = []
        page.show_toast = lambda msg, msg_type="info": toast_calls.append((msg, msg_type))  # type: ignore[assignment]
        run_mount_effects(component, page=page)
        result = render_once(component)

        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        _click_icon_button(icon_buttons[0])
        _rerender(component)
        page.run_task = lambda func, *args, **kwargs: asyncio.run(func(*args, **kwargs))  # type: ignore[assignment]
        on_confirm = mock_watchlist_vm.captured_callbacks.get("on_confirm")
        assert on_confirm is not None
        # 不应抛异常
        on_confirm()
        assert any("watchlist_remove_failed" in msg for msg, _ in toast_calls)

    def test_on_remove_no_page_does_not_crash(
        self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state
    ):
        """page 为 None 时 _on_remove 提前返回 (不 crash).

        state setter 隐式依赖 page 上下文; page 不可用时 _on_remove 提前返回不调 setter,
        ConfirmDialog 不会打开. on_click 回调在真实 Flet 运行时总在 page 上下文中触发,
        本测试模拟极端场景验证健壮性 (参考 home_view._refresh_clicked 的 page guard).
        """
        from flet.controls.context import _context_page

        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        rows = (_make_row(ts_code="000001.SZ"),)
        mock_watchlist_vm._state = WatchlistState(watchlist_rows=rows, is_loading=False)
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        # 清除 ContextVar 模拟 page 不可用
        _context_page.set(None)
        result = render_once(component)

        icon_buttons = [
            c
            for c in _collect_all_controls(result)
            if isinstance(c, ft.IconButton) and getattr(c, "icon", None) == ft.Icons.DELETE_OUTLINE
        ]
        # 触发 on_click 不应抛异常 (page=None 时 _on_remove 提前返回, 不调 state setter)
        _click_icon_button(icon_buttons[0])
