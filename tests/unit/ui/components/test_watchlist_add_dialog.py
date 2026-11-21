"""ui/components/watchlist_add_dialog.py 组件运行时测试。

覆盖用户 dismiss（Esc / 关闭路径）与消费方 open_state 的同步契约：
未同步时 Flet ``use_dialog`` 会在下一次重渲染时把对话框重新挂回 overlay。
另覆盖 R2-I13：重复关注 / 修改备注前按 ts_code 读取旧备注，先展示替换影响
再写入；读取中/失败/未完成读取均不得直接覆盖。
"""

from collections.abc import Callable
from typing import Any, cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from ui.components.watchlist_add_dialog import WatchlistAddDialog
from ui.viewmodels.watchlist_view_model import ExistingNoteState, StockSearchRow

pytestmark = pytest.mark.unit

_TS_CODE = "000001.SZ"
_STOCK_NAME = "平安银行"


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


# ---------------------------------------------------------------------------
# R2-I13: 重复关注 / 修改备注前的旧备注读取与替换确认
# ---------------------------------------------------------------------------


def _walk(root: Any) -> list[Any]:
    """递归展平控件树（controls/actions/content）。"""
    if not isinstance(root, ft.Control):
        return []
    out: list[Any] = [root]
    for attr in ("controls", "actions"):
        children = getattr(root, attr, None)
        if isinstance(children, list):
            for child in children:
                out.extend(_walk(child))
    content = getattr(root, "content", None)
    if isinstance(content, ft.Control):
        out.extend(_walk(content))
    return out


def _mount_add(
    *,
    existing_note: ExistingNoteState | None = None,
    on_add=None,
    on_load_existing_note=None,
):
    """挂载带一条搜索结果、已打开状态的对话框。

    ``I18n.get`` 由 ``mock_dialog_i18n`` fixture 打桩为返回 key 本身，
    便于按 key 断言替换提示文案。
    """
    component = make_component(
        WatchlistAddDialog,
        open_state=True,
        search_results=(StockSearchRow(ts_code=_TS_CODE, name=_STOCK_NAME),),
        existing_note=existing_note or ExistingNoteState(),
        on_add=on_add,
        on_load_existing_note=on_load_existing_note,
    )
    return component, run_mount_effects(component)


def _dialog(page: Any) -> ft.AlertDialog:
    dialog = page._dialogs.controls[-1]
    assert isinstance(dialog, ft.AlertDialog)
    return dialog


def _select_first_result(component, page) -> None:
    dialog = _dialog(page)
    clickable = [
        c
        for c in _walk(dialog)
        if isinstance(c, ft.Container) and callable(getattr(c, "on_click", None)) and isinstance(c.content, ft.Row)
    ]
    assert len(clickable) == 1, "应渲染一条可选搜索结果"
    # Flet on_click 的 Callable Union 含 0 参分支，pyright 按无参分支校验
    clickable[0].on_click(MagicMock())  # type: ignore[call-issue, reportOptionalCall]  # [reason: 同 test_watchlist_view 的 on_click 调用范式]
    render_once(component)


def _set_note(component, page, value: str) -> None:
    dialog = _dialog(page)
    fields = [c for c in _walk(dialog) if isinstance(c, ft.TextField)]
    note_field = next(f for f in fields if f.label == "watchlist_add_note_label")
    note_field.value = value
    event = MagicMock()
    event.control = note_field
    assert callable(note_field.on_change)
    note_field.on_change(event)  # type: ignore[call-issue]  # [reason: Flet on_change Union 含 0 参分支]
    render_once(component)


def _click_confirm(component, page) -> None:
    dialog = _dialog(page)
    confirm = next(c for c in dialog.actions if isinstance(c, ft.Button))
    assert callable(confirm.on_click)
    confirm.on_click(MagicMock())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支]
    render_once(component)


def _click_by_text(component, page, text: str) -> None:
    dialog = _dialog(page)
    buttons = [c for c in _walk(dialog) if isinstance(c, (ft.Button, ft.TextButton)) and c.content == text]
    assert len(buttons) == 1, f"应存在文本为 {text} 的按钮"
    assert callable(buttons[0].on_click)
    buttons[0].on_click(MagicMock())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支]
    render_once(component)


def _texts(page) -> list[str]:
    return [c.value for c in _walk(_dialog(page)) if isinstance(c, ft.Text)]


def _button_texts(page) -> list[str]:
    """按钮文案（``Button/TextButton.content`` 为字符串，不属于 ``ft.Text``）。"""
    return [
        c.content
        for c in _walk(_dialog(page))
        if isinstance(c, (ft.Button, ft.TextButton)) and isinstance(c.content, str)
    ]


def _loaded(note: str, ts_code: str = _TS_CODE) -> ExistingNoteState:
    return ExistingNoteState(ts_code=ts_code, status="loaded", note=note)


@pytest.fixture(autouse=True)
def mock_dialog_i18n(monkeypatch):
    """``I18n.get`` 打桩为返回 key 本身（文案断言不依赖 locale 文件内容）。"""
    mock = MagicMock()
    mock.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr("ui.components.watchlist_add_dialog.I18n", mock)
    return mock


class TestExistingNoteConfirmation:
    """R2-I13：旧备注必须被读取并显式确认后才允许替换。"""

    def test_select_triggers_existing_note_read(self, mock_i18n_state, mock_app_colors_state) -> None:
        on_load = MagicMock()
        component, page = _mount_add(on_load_existing_note=on_load)
        _select_first_result(component, page)
        on_load.assert_called_once_with(_TS_CODE)
        run_unmount_effects(component)

    def test_new_stock_adds_directly(self, mock_i18n_state, mock_app_colors_state) -> None:
        """未关注（旧备注为空）：无需二次确认，直接写入。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded(""), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "  新备注  ")
        _click_confirm(component, page)
        on_add.assert_called_once_with(_TS_CODE, _STOCK_NAME, "新备注")
        assert "watchlist_note_replace_title" not in _texts(page)
        run_unmount_effects(component)

    def test_existing_note_shows_replace_impact_without_writing(self, mock_i18n_state, mock_app_colors_state) -> None:
        """旧备注与新备注不同：先展示旧内容与替换影响，不写入。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("旧备注"), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "新备注")
        _click_confirm(component, page)
        on_add.assert_not_called()
        texts = _texts(page)
        assert "watchlist_note_replace_title" in texts
        assert "watchlist_note_replace_old" in texts
        assert "watchlist_note_replace_new" in texts
        buttons = _button_texts(page)
        assert "watchlist_note_replace_confirm" in buttons
        assert "watchlist_note_replace_cancel" in buttons
        run_unmount_effects(component)

    def test_confirm_replace_submits_new_note(self, mock_i18n_state, mock_app_colors_state) -> None:
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("旧备注"), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "新备注")
        _click_confirm(component, page)
        _click_by_text(component, page, "watchlist_note_replace_confirm")
        on_add.assert_called_once_with(_TS_CODE, _STOCK_NAME, "新备注")
        run_unmount_effects(component)

    def test_back_to_edit_keeps_original_content(self, mock_i18n_state, mock_app_colors_state) -> None:
        """返回修改：不写入（原备注未被修改）。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("旧备注"), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "新备注")
        _click_confirm(component, page)
        _click_by_text(component, page, "watchlist_note_replace_cancel")
        on_add.assert_not_called()
        assert "watchlist_note_replace_title" not in _texts(page)
        run_unmount_effects(component)

    def test_empty_new_note_warns_about_clearing(self, mock_i18n_state, mock_app_colors_state) -> None:
        """空备注会清空旧备注，同样必须确认。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("旧备注"), on_add=on_add)
        _select_first_result(component, page)
        _click_confirm(component, page)
        on_add.assert_not_called()
        assert "watchlist_note_replace_new_cleared" in _texts(page)
        run_unmount_effects(component)

    def test_same_note_skips_confirmation(self, mock_i18n_state, mock_app_colors_state) -> None:
        """备注未变化：无内容损失，直接写入。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("重点"), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "重点")
        _click_confirm(component, page)
        on_add.assert_called_once_with(_TS_CODE, _STOCK_NAME, "重点")
        run_unmount_effects(component)

    def test_read_failure_blocks_write_and_retries(self, mock_i18n_state, mock_app_colors_state) -> None:
        """读取旧备注失败：不得当作「无旧备注」直接覆盖，改为重试读取。"""
        on_add = MagicMock()
        on_load = MagicMock()
        failed = ExistingNoteState(ts_code=_TS_CODE, status="failed")
        component, page = _mount_add(existing_note=failed, on_add=on_add, on_load_existing_note=on_load)
        _select_first_result(component, page)
        on_load.reset_mock()
        _click_confirm(component, page)
        on_add.assert_not_called()
        on_load.assert_called_once_with(_TS_CODE)
        assert "watchlist_note_check_failed" in _texts(page)
        run_unmount_effects(component)

    def test_loading_blocks_write(self, mock_i18n_state, mock_app_colors_state) -> None:
        on_add = MagicMock()
        loading = ExistingNoteState(ts_code=_TS_CODE, status="loading")
        component, page = _mount_add(existing_note=loading, on_add=on_add)
        _select_first_result(component, page)
        _click_confirm(component, page)
        on_add.assert_not_called()
        assert "watchlist_note_checking" in _texts(page)
        run_unmount_effects(component)

    def test_note_of_other_stock_triggers_read(self, mock_i18n_state, mock_app_colors_state) -> None:
        """旧备注属于另一只股票：不得据此判断，重新读取当前选中股票。"""
        on_add = MagicMock()
        on_load = MagicMock()
        stale = _loaded("别的股票备注", ts_code="600000.SH")
        component, page = _mount_add(existing_note=stale, on_add=on_add, on_load_existing_note=on_load)
        _select_first_result(component, page)
        on_load.reset_mock()
        _click_confirm(component, page)
        on_add.assert_not_called()
        on_load.assert_called_once_with(_TS_CODE)
        run_unmount_effects(component)

    def test_editing_note_after_confirmation_resets_panel(self, mock_i18n_state, mock_app_colors_state) -> None:
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded("旧备注"), on_add=on_add)
        _select_first_result(component, page)
        _set_note(component, page, "新备注")
        _click_confirm(component, page)
        assert "watchlist_note_replace_title" in _texts(page)
        _set_note(component, page, "再改一次")
        assert "watchlist_note_replace_title" not in _texts(page)
        on_add.assert_not_called()
        run_unmount_effects(component)

    def test_double_click_confirm_submits_once(self, mock_i18n_state, mock_app_colors_state) -> None:
        """同一帧内重复点击确认不得重复提交。"""
        on_add = MagicMock()
        component, page = _mount_add(existing_note=_loaded(""), on_add=on_add)
        _select_first_result(component, page)
        dialog = _dialog(page)
        confirm = next(c for c in dialog.actions if isinstance(c, ft.Button))
        assert callable(confirm.on_click)
        confirm.on_click(MagicMock())  # type: ignore[call-issue]  # [reason: Flet on_click Union 含 0 参分支]
        confirm.on_click(MagicMock())  # type: ignore[call-issue]  # [reason: 同帧第二次点击验证提交锁]
        on_add.assert_called_once_with(_TS_CODE, _STOCK_NAME, "")
        run_unmount_effects(component)
