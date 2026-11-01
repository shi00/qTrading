"""ui/components/watchlist_add_dialog.py 组件运行时测试。

覆盖用户 dismiss（Esc / 关闭路径）与消费方 open_state 的同步契约：
未同步时 Flet ``use_dialog`` 会在下一次重渲染时把对话框重新挂回 overlay。
"""

from collections.abc import Callable
from typing import cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.watchlist_add_dialog import WatchlistAddDialog

pytestmark = pytest.mark.unit


def _dismiss(dialog: ft.AlertDialog) -> None:
    """按 Flet 真实 dismiss 事件（Esc / 关闭路径）触发 ``on_dismiss``。

    ``AlertDialog.on_dismiss`` 的签名为 ``Callable[[], Any] |
    Callable[[Event[DialogControl]], Any] | None``；pyright 对该可调用联合类型
    按无参分支校验，故显式选取「带事件」分支并构造对话框自身发出的
    ``Event[DialogControl]``，与运行时 Flet 的调用方式一致。
    """
    assert dialog.on_dismiss is not None
    handler = cast(Callable[[ft.Event[ft.DialogControl]], None], dialog.on_dismiss)
    handler(ft.Event[ft.DialogControl]("dismiss", dialog))


def _mount(*, open_state: bool, is_submitting: bool = False, on_close=None):
    component = make_component(
        WatchlistAddDialog,
        open_state=open_state,
        is_submitting=is_submitting,
        on_close=on_close,
    )
    return component, run_mount_effects(component)


class TestWatchlistAddDialogDismiss:
    """dismiss 事件与关闭回调 / 提交中守卫。"""

    def test_on_dismiss_invokes_on_close(self, mock_i18n_state, mock_app_colors_state):
        """用户 dismiss 按取消处理，调用 on_close 同步消费方状态。"""
        on_close = MagicMock()
        component, page = _mount(open_state=True, on_close=on_close)
        dialog = page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        _dismiss(dialog)
        # dismiss 按取消处理：on_close 收到且仅收到一次无参调用
        on_close.assert_called_once_with()
        run_unmount_effects(component)

    def test_on_dismiss_ignored_while_submitting(self, mock_i18n_state, mock_app_colors_state):
        """F01：写入执行中 dismiss 不得关闭（沿 _on_cancel 早返回守卫）。"""
        on_close = MagicMock()
        component, page = _mount(open_state=True, is_submitting=True, on_close=on_close)
        dialog = page._dialogs.controls[-1]
        assert isinstance(dialog, ft.AlertDialog)
        _dismiss(dialog)
        on_close.assert_not_called()
        run_unmount_effects(component)

    def test_closed_state_does_not_mount_dialog(self, mock_i18n_state, mock_app_colors_state):
        component, page = _mount(open_state=False)
        assert page._dialogs.controls == []
        run_unmount_effects(component)
