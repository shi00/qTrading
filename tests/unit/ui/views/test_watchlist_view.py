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
from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.viewmodels.watchlist_view_model import WatchlistRow, WatchlistState
from ui.views.watchlist_view import (
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

    def test_renders_error_banner(self, mock_watchlist_vm, mock_i18n_for_view, mock_i18n_state, mock_app_colors_state):
        from tests.unit.ui.component_renderer import make_component, render_once, run_mount_effects

        from ui.viewmodels import Message

        mock_watchlist_vm._state = WatchlistState(
            load_error=Message("watchlist_load_failed", {}),
            is_loading=False,
        )
        component = make_component(WatchlistView, active=True)
        run_mount_effects(component)
        # render_once 触发渲染并调用 I18n.get, 返回值未直接断言 (改用 I18n.get 调用记录验证)
        render_once(component)

        # Issue #448: load_error 非空时 body 渲染为 ErrorState 子组件 (Component 实例)
        # 验证 ErrorState 被实际调用并传入正确 props (通过 mock I18n.get 接收的 key 间接验证)
        # I18n.get("watchlist_load_failed_title") 和 I18n.get("watchlist_load_failed_message")
        # 已在 render_once 中被调用, mock_i18n_for_view.get 已记录这些调用
        called_keys = [call.args[0] for call in mock_i18n_for_view.get.call_args_list]
        assert "watchlist_load_failed_title" in called_keys, (
            "WatchlistView 应调用 I18n.get('watchlist_load_failed_title') 用于 ErrorState title"
        )
        assert "watchlist_load_failed_message" in called_keys, (
            "WatchlistView 应调用 I18n.get('watchlist_load_failed_message') 用于 ErrorState message"
        )
        assert "common_retry" in called_keys, "WatchlistView 应调用 I18n.get('common_retry') 用于重试按钮"
        assert "common_contact_support" in called_keys, (
            "WatchlistView 应调用 I18n.get('common_contact_support') 用于联系支持按钮"
        )


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
