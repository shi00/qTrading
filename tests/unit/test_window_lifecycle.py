"""Tests for app/window_lifecycle.

覆盖从 main.py 抽取的窗口生命周期相关函数与 WindowDialogManager 类：
- build_locale_configuration: locale 字符串解析与异常回退
- setup_window_geometry: 窗口几何属性设置与 center 错误处理
- WindowDialogManager: dialog 显示/隐藏、close confirm 流程
- perform_window_shutdown: coordinator cleanup + window destroy
- perform_upgrade_exit: upgrade 失败后的清理与强制退出
- handle_disconnect: disconnect 事件处理与 cleanup_done 短路
"""

# pyright: reportAttributeAccessIssue=false, reportArgumentType=false
# 本文件使用 _DummyPage / _DummyWindow / _FakeCoordinator 替身类对 Flet 与
# Coordinator 进行结构性 mock，pyright 无法验证替身与 ft.Page/Window 及
# ShutdownCoordinator 的兼容性。统一在此文件局部禁用相关告警以避免大量
# typing.cast 噪音，替身类的行为由测试用例本身验证。

from typing import Any, cast
from unittest.mock import AsyncMock, patch

import flet as ft
import pytest

from app.window_lifecycle import (
    WindowDialogManager,
    build_locale_configuration,
    handle_disconnect,
    perform_upgrade_exit,
    perform_window_shutdown,
    setup_window_geometry,
)


def _make_page() -> ft.Page:
    """Construct _DummyPage and cast to ft.Page for type-checking.

    _DummyPage is a structural stand-in; pyright cannot verify compatibility
    with ft.Page. Centralize the cast here to avoid per-call type: ignore.
    """
    return cast(ft.Page, _DummyPage())


class _DummyWindow:
    """Flet Page.window 替身，记录属性变更与 destroy/center 调用。"""

    def __init__(self) -> None:
        self.prevent_close = True
        self.min_width = 0
        self.min_height = 0
        self.width: int | None = None
        self.height: int | None = None
        self.destroy_calls = 0
        self.center_calls = 0
        self.center_exc: Exception | None = None
        self.destroy_exc: Exception | None = None

    async def destroy(self) -> None:
        self.destroy_calls += 1
        if self.destroy_exc is not None:
            raise self.destroy_exc

    async def center(self) -> None:
        self.center_calls += 1
        if self.center_exc is not None:
            raise self.center_exc


class _DummyPage:
    """Flet Page 替身，维护 dialog 栈与 run_task 调用记录。"""

    def __init__(self) -> None:
        self.window = _DummyWindow()
        self.dialog_stack: list[Any] = []
        self.run_task_handlers: list[Any] = []

    @property
    def active_dialog(self) -> Any:
        return self.dialog_stack[-1] if self.dialog_stack else None

    def show_dialog(self, dialog: Any) -> None:
        self.dialog_stack.append(dialog)

    def pop_dialog(self) -> Any:
        if not self.dialog_stack:
            return None
        return self.dialog_stack.pop()

    def run_task(self, handler: Any, *args: Any, **kwargs: Any) -> Any:
        self.run_task_handlers.append(handler)
        return None


class _FakeCoordinator:
    """ShutdownCoordinator 替身，记录调用与可控返回值。"""

    def __init__(
        self,
        *,
        cleanup_ok: bool = True,
        cleanup_done: bool = False,
    ) -> None:
        self.cleanup_done = cleanup_done
        self._cleanup_ok = cleanup_ok
        self.start_watchdog_calls = 0
        self.start_watchdog_args: list[float | None] = []
        self.cancel_watchdog_calls = 0
        self.do_cleanup_calls = 0
        self.do_cleanup_kwargs: dict[str, Any] = {}
        self.force_exit_codes: list[int] = []
        self.step_results: list[Any] = []

    def start_watchdog(self, timeout_s: float | None = None) -> None:
        self.start_watchdog_calls += 1
        self.start_watchdog_args.append(timeout_s)

    def cancel_watchdog(self) -> None:
        self.cancel_watchdog_calls += 1

    async def do_cleanup(self, **kwargs: Any) -> bool:
        self.do_cleanup_calls += 1
        self.do_cleanup_kwargs = dict(kwargs)
        return self._cleanup_ok

    def _force_exit(self, code: int) -> None:
        self.force_exit_codes.append(code)


# ============================================================================
# build_locale_configuration
# ============================================================================


class TestBuildLocaleConfiguration:
    def test_normal_zh_CN(self) -> None:
        config = build_locale_configuration("zh_CN")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "zh"
        assert config.current_locale.country_code == "CN"
        assert len(config.supported_locales) == 2

    def test_normal_en_US(self) -> None:
        config = build_locale_configuration("en_US")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "en"
        assert config.current_locale.country_code == "US"

    def test_supported_locales_contains_zh_and_en(self) -> None:
        config = build_locale_configuration("zh_CN")
        langs = {(loc.language_code, loc.country_code) for loc in config.supported_locales}
        assert ("zh", "CN") in langs
        assert ("en", "US") in langs

    def test_invalid_locale_falls_back_to_zh_CN(self) -> None:
        """无下划线的字符串 split 后只能解包 1 个值，触发 ValueError."""
        config = build_locale_configuration("invalid")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "zh"
        assert config.current_locale.country_code == "CN"

    def test_empty_string_falls_back_to_zh_CN(self) -> None:
        config = build_locale_configuration("")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "zh"
        assert config.current_locale.country_code == "CN"

    def test_single_word_falls_back_to_zh_CN(self) -> None:
        """无下划线的字符串 split 后只能解包 1 个值，触发 ValueError."""
        config = build_locale_configuration("xyz")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "zh"
        assert config.current_locale.country_code == "CN"

    def test_three_part_string_falls_back_to_zh_CN(self) -> None:
        """三段字符串 split 后 3 个值无法解包到 2 个变量，触发 ValueError."""
        config = build_locale_configuration("a_b_c")
        assert config.current_locale is not None
        assert config.current_locale.language_code == "zh"
        assert config.current_locale.country_code == "CN"


# ============================================================================
# setup_window_geometry
# ============================================================================


class TestSetupWindowGeometry:
    @pytest.mark.asyncio
    async def test_normal_path_sets_min_dimensions_and_centers(self) -> None:
        page = _make_page()
        await setup_window_geometry(page, is_web_mode=False)
        assert page.window.min_width == 1280
        assert page.window.min_height == 720
        assert page.window.center_calls == 1

    @pytest.mark.asyncio
    async def test_normal_path_sets_width_when_none(self) -> None:
        page = _make_page()
        page.window.width = None
        page.window.height = None
        await setup_window_geometry(page, is_web_mode=False)
        assert page.window.width == 1280
        assert page.window.height == 800

    @pytest.mark.asyncio
    async def test_normal_path_sets_width_when_below_minimum(self) -> None:
        page = _make_page()
        page.window.width = 800
        page.window.height = 600
        await setup_window_geometry(page, is_web_mode=False)
        assert page.window.width == 1280
        assert page.window.height == 800

    @pytest.mark.asyncio
    async def test_existing_valid_width_preserved(self) -> None:
        """width >= 1280 时保留用户设置的尺寸."""
        page = _make_page()
        page.window.width = 1920
        page.window.height = 1080
        await setup_window_geometry(page, is_web_mode=False)
        assert page.window.width == 1920
        assert page.window.height == 1080

    @pytest.mark.asyncio
    async def test_web_mode_skips_geometry_setup(self) -> None:
        page = _make_page()
        await setup_window_geometry(page, is_web_mode=True)
        assert page.window.min_width == 0
        assert page.window.min_height == 0
        assert page.window.center_calls == 0

    @pytest.mark.asyncio
    async def test_center_failure_logs_via_log_exception_with_severity(self) -> None:
        page = _make_page()
        page.window.center_exc = RuntimeError("center boom")
        with patch("app.window_lifecycle.log_exception_with_severity") as mock_log:
            await setup_window_geometry(page, is_web_mode=False)
            mock_log.assert_called_once_with(
                page.window.center_exc,
                context="general",
                operation_label="Main window center failed",
            )


# ============================================================================
# WindowDialogManager
# ============================================================================


class TestWindowDialogManagerShowHideDialog:
    def test_show_dialog_sets_active_dialog_and_calls_page_show_dialog(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("test"))
        manager._show_dialog(dialog)
        assert page.active_dialog is dialog
        assert manager.active_dialog is dialog

    def test_hide_dialog_calls_pop_dialog_and_clears_active(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("test"))
        manager._show_dialog(dialog)
        manager._hide_dialog(dialog)
        assert manager.active_dialog is None
        assert page.dialog_stack == []

    def test_hide_dialog_does_not_clear_active_when_dialog_mismatches(self) -> None:
        """弹出 dialog A 后再弹出 B，隐藏 A 时不应清空 active_dialog=B."""
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog_a = ft.AlertDialog(title=ft.Text("A"))
        dialog_b = ft.AlertDialog(title=ft.Text("B"))
        manager._show_dialog(dialog_a)
        manager._show_dialog(dialog_b)
        manager._hide_dialog(dialog_a)  # active is dialog_b, mismatch
        assert manager.active_dialog is dialog_b


class TestWindowDialogManagerCloseConfirm:
    def test_show_close_confirm_dialog_sets_visible_and_active(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        assert manager.close_confirm_visible is True
        assert manager.current_close_confirm_dialog is dialog
        assert manager.active_dialog is dialog
        assert page.active_dialog is dialog

    def test_show_close_confirm_dialog_skips_when_already_visible(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog1 = ft.AlertDialog(title=ft.Text("first"))
        dialog2 = ft.AlertDialog(title=ft.Text("second"))
        manager._show_close_confirm_dialog(dialog1)
        manager._show_close_confirm_dialog(dialog2)
        # 第二次被跳过，仍是第一个 dialog
        assert manager.current_close_confirm_dialog is dialog1
        assert page.dialog_stack == [dialog1]

    def test_show_close_confirm_dialog_skips_when_shutdown_requested(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        manager.shutdown_requested = True
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        assert manager.close_confirm_visible is False
        assert manager.current_close_confirm_dialog is None
        assert page.dialog_stack == []

    def test_hide_close_confirm_dialog_clears_state(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        manager._hide_close_confirm_dialog()
        assert manager.close_confirm_visible is False
        assert manager.current_close_confirm_dialog is None
        assert manager.active_dialog is None
        assert page.dialog_stack == []

    def test_hide_close_confirm_dialog_no_op_when_no_dialog(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        manager._hide_close_confirm_dialog()  # 不应抛异常
        assert manager.close_confirm_visible is False
        assert page.dialog_stack == []

    def test_on_close_cancel_hides_dialog(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        event = ft.Event(name="click", data="", control=page)
        with patch("app.window_lifecycle.UILogger.log_action") as mock_log:
            manager._on_close_cancel(event)
            mock_log.assert_called_once_with("MainWindow", action="close_cancel")
        assert manager.close_confirm_visible is False
        assert page.dialog_stack == []

    def test_on_close_confirm_triggers_shutdown_request(self) -> None:
        page = _make_page()
        shutdown_calls: list[None] = []
        manager = WindowDialogManager(page, on_shutdown_request=lambda: shutdown_calls.append(None))
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        event = ft.Event(name="click", data="", control=page)
        with patch("app.window_lifecycle.UILogger.log_action") as mock_log:
            manager._on_close_confirm(event)
            mock_log.assert_called_once_with("MainWindow", action="close_confirm")
        assert manager.shutdown_requested is True
        assert manager.close_confirm_visible is False
        assert len(shutdown_calls) == 1

    def test_on_close_confirm_skips_when_shutdown_already_requested(self) -> None:
        """重复 confirm 不会重复触发 shutdown."""
        page = _make_page()
        shutdown_calls: list[None] = []
        manager = WindowDialogManager(page, on_shutdown_request=lambda: shutdown_calls.append(None))
        manager.shutdown_requested = True
        event = ft.Event(name="click", data="", control=page)
        manager._on_close_confirm(event)
        assert len(shutdown_calls) == 0

    def test_on_close_confirm_without_shutdown_callback_does_not_raise(self) -> None:
        """未注入 on_shutdown_request 时不抛异常（仅设置标志位）."""
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        event = ft.Event(name="click", data="", control=page)
        with patch("app.window_lifecycle.UILogger.log_action"):
            manager._on_close_confirm(event)
        assert manager.shutdown_requested is True


class TestWindowDialogManagerPageDialogMatches:
    def test_matches_when_active_dialog_is_close_confirm(self) -> None:
        page = _make_page()
        manager = WindowDialogManager(page)
        dialog = ft.AlertDialog(title=ft.Text("close"))
        manager._show_close_confirm_dialog(dialog)
        assert manager._page_dialog_matches_close_confirm() is True

    def test_returns_true_when_both_none(self) -> None:
        """初始状态 active_dialog=None, current_close_confirm_dialog=None 时返回 True.

        与 main.py 原逻辑 `active_dialog is current_close_confirm_dialog` 一致：
        两者均为 None 时 `None is None` 为 True（仅用于 debug log，不影响功能）。
        """
        page = _make_page()
        manager = WindowDialogManager(page)
        assert manager._page_dialog_matches_close_confirm() is True

    def test_no_match_when_active_dialog_is_other(self) -> None:
        """显示 close confirm 后被其他 dialog 覆盖 active_dialog."""
        page = _make_page()
        manager = WindowDialogManager(page)
        close_dialog = ft.AlertDialog(title=ft.Text("close"))
        other_dialog = ft.AlertDialog(title=ft.Text("other"))
        manager._show_close_confirm_dialog(close_dialog)
        manager._show_dialog(other_dialog)  # active_dialog 变为 other_dialog
        assert manager._page_dialog_matches_close_confirm() is False


# ============================================================================
# perform_window_shutdown
# ============================================================================


class TestPerformWindowShutdown:
    @pytest.mark.asyncio
    async def test_success_path_cancels_watchdog_and_returns_true(self) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=True)
        result = await perform_window_shutdown(
            coordinator,
            page,
            is_web_mode_fn=lambda: False,
        )
        assert result is True
        assert coordinator.start_watchdog_calls == 1
        assert coordinator.start_watchdog_args == [None]
        assert coordinator.do_cleanup_calls == 1
        assert coordinator.do_cleanup_kwargs == {"timeout_s": 20.0}
        assert coordinator.cancel_watchdog_calls == 1
        assert coordinator.force_exit_codes == []
        assert page.window.destroy_calls == 1
        assert page.window.prevent_close is False

    @pytest.mark.asyncio
    async def test_web_mode_does_not_destroy_window(self) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=True)
        await perform_window_shutdown(
            coordinator,
            page,
            is_web_mode_fn=lambda: True,
        )
        assert page.window.destroy_calls == 0
        # prevent_close 未被修改
        assert page.window.prevent_close is True

    @pytest.mark.asyncio
    async def test_cleanup_failure_force_exits_and_returns_false(self) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=False)
        with patch("app.window_lifecycle.asyncio.sleep", new_callable=AsyncMock):
            result = await perform_window_shutdown(
                coordinator,
                page,
                is_web_mode_fn=lambda: False,
            )
        assert result is False
        assert coordinator.cancel_watchdog_calls == 0
        assert coordinator.force_exit_codes == [1]

    @pytest.mark.asyncio
    async def test_window_destroy_failure_logged_via_log_exception_with_severity(self) -> None:
        page = _make_page()
        page.window.destroy_exc = RuntimeError("destroy boom")
        coordinator = _FakeCoordinator(cleanup_ok=True)
        with patch("app.window_lifecycle.log_exception_with_severity") as mock_log:
            await perform_window_shutdown(
                coordinator,
                page,
                is_web_mode_fn=lambda: False,
            )
            mock_log.assert_called_once_with(
                page.window.destroy_exc,
                context="general",
                operation_label="Main window destroy failed",
            )
        # 即使 destroy 失败，cleanup_ok=True 仍 cancel_watchdog 并返回 True
        assert coordinator.cancel_watchdog_calls == 1


# ============================================================================
# perform_upgrade_exit
# ============================================================================


class TestPerformUpgradeExit:
    @pytest.mark.asyncio
    async def test_success_path_cleans_up_and_force_exits(self) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=True)
        await perform_upgrade_exit(
            coordinator,
            page,
            is_web_mode_fn=lambda: False,
        )
        assert coordinator.do_cleanup_calls == 1
        assert coordinator.do_cleanup_kwargs == {"timeout_s": 5.0, "step_timeout_s": 1.0}
        assert page.window.destroy_calls == 1
        assert page.window.prevent_close is False
        assert coordinator.force_exit_codes == [1]

    @pytest.mark.asyncio
    async def test_web_mode_does_not_destroy_window(self) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=True)
        await perform_upgrade_exit(
            coordinator,
            page,
            is_web_mode_fn=lambda: True,
        )
        assert page.window.destroy_calls == 0

    @pytest.mark.asyncio
    async def test_cleanup_failure_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        page = _make_page()
        coordinator = _FakeCoordinator(cleanup_ok=False)
        with patch("app.window_lifecycle.asyncio.sleep", new_callable=AsyncMock):
            await perform_upgrade_exit(
                coordinator,
                page,
                is_web_mode_fn=lambda: True,
            )
        assert coordinator.force_exit_codes == [1]
        assert any("Cleanup incomplete after upgrade failure exit" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_window_destroy_failure_logged(self) -> None:
        page = _make_page()
        page.window.destroy_exc = RuntimeError("destroy fail")
        coordinator = _FakeCoordinator(cleanup_ok=True)
        with patch("app.window_lifecycle.log_exception_with_severity") as mock_log:
            await perform_upgrade_exit(
                coordinator,
                page,
                is_web_mode_fn=lambda: False,
            )
            mock_log.assert_called_once_with(
                page.window.destroy_exc,
                context="general",
                operation_label="Main window destroy failed during upgrade exit",
            )
        assert coordinator.force_exit_codes == [1]


# ============================================================================
# handle_disconnect
# ============================================================================


class TestHandleDisconnect:
    @pytest.mark.asyncio
    async def test_cleanup_done_short_circuits_without_force_exit(self) -> None:
        """cleanup_done_fn() == True 时直接返回，不进入 cancel/force_exit 分支."""
        coordinator = _FakeCoordinator(cleanup_ok=True, cleanup_done=True)
        await handle_disconnect(
            coordinator,
            cleanup_done_fn=lambda: True,
        )
        assert coordinator.start_watchdog_calls == 1
        assert coordinator.start_watchdog_args == [25]
        assert coordinator.do_cleanup_calls == 1
        assert coordinator.do_cleanup_kwargs == {"timeout_s": 20.0}
        # cleanup_done=True 短路返回，不调用 cancel_watchdog/force_exit
        assert coordinator.cancel_watchdog_calls == 0
        assert coordinator.force_exit_codes == []

    @pytest.mark.asyncio
    async def test_cleanup_ok_cancels_watchdog(self) -> None:
        """cleanup_done=False 且 cleanup_ok=True：cancel_watchdog 并 log info."""
        coordinator = _FakeCoordinator(cleanup_ok=True, cleanup_done=False)
        await handle_disconnect(
            coordinator,
            cleanup_done_fn=lambda: False,
        )
        assert coordinator.cancel_watchdog_calls == 1
        assert coordinator.force_exit_codes == []

    @pytest.mark.asyncio
    async def test_cleanup_failure_force_exits(self) -> None:
        """cleanup_done=False 且 cleanup_ok=False：log error 并 force_exit(1)."""
        coordinator = _FakeCoordinator(cleanup_ok=False, cleanup_done=False)
        with patch("app.window_lifecycle.asyncio.sleep", new_callable=AsyncMock):
            await handle_disconnect(
                coordinator,
                cleanup_done_fn=lambda: False,
            )
        assert coordinator.cancel_watchdog_calls == 0
        assert coordinator.force_exit_codes == [1]

    @pytest.mark.asyncio
    async def test_start_watchdog_uses_25_second_timeout(self) -> None:
        """disconnect 路径下 watchdog 超时为 25s（区别于 shutdown 路径的默认 25s）."""
        coordinator = _FakeCoordinator(cleanup_ok=True, cleanup_done=True)
        await handle_disconnect(
            coordinator,
            cleanup_done_fn=lambda: True,
        )
        assert coordinator.start_watchdog_args == [25]
