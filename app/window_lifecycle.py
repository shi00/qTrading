"""窗口生命周期管理：从 main.py 抽取的窗口几何、对话框管理、shutdown/upgrade/disconnect 流程。

抽取目的：将 main.py 中依赖 Flet Page 和 ShutdownCoordinator 的闭包改为可测的纯函数与类，
为 main.py 重构（Task 6.3）做准备。

依赖关系：app 层 → utils 层（ShutdownCoordinator 类型注解、UILogger、log_exception_with_severity），
不导入 ui/services/strategies/data，符合 R1 分层架构。
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from app.error_logging import log_exception_with_severity
from utils.log_decorators import UILogger
from utils.shutdown import ShutdownCoordinator

logger = logging.getLogger(__name__)


def build_locale_configuration(locale_str: str) -> ft.LocaleConfiguration:
    """从 locale 字符串构建 LocaleConfiguration.

    解析 "lang_COUNTRY" 格式字符串为 ft.Locale，异常时回退到 zh_CN。
    supported_locales 固定为 [zh_CN, en_US]，与 main.py 原逻辑一致。

    Args:
        locale_str: locale 字符串，如 "zh_CN" 或 "en_US"

    Returns:
        ft.LocaleConfiguration，包含 supported_locales 和 current_locale
    """
    try:
        lang, country = locale_str.split("_")
        current_locale = ft.Locale(lang, country)
    except (ValueError, AttributeError, IndexError):
        current_locale = ft.Locale("zh", "CN")
    return ft.LocaleConfiguration(
        supported_locales=[
            ft.Locale("zh", "CN"),
            ft.Locale("en", "US"),
        ],
        current_locale=current_locale,
    )


async def setup_window_geometry(page: ft.Page, *, is_web_mode: bool) -> None:
    """设置窗口几何属性并居中.

    非 web_mode 时设置 min_width/min_height/width/height 并调用 page.window.center()。
    center 失败时通过 log_exception_with_severity 记录，不传播异常。
    web_mode 时跳过所有窗口几何设置（浏览器模式由 Flet 自动管理）。

    Args:
        page: Flet Page 实例
        is_web_mode: 是否为 web 模式
    """
    if is_web_mode:
        return
    page.window.min_width = 1280
    page.window.min_height = 720
    if not page.window.width or page.window.width < 1280:
        page.window.width = 1280
        page.window.height = 800
    try:
        await page.window.center()
    except Exception as e:
        log_exception_with_severity(
            e,
            context="general",
            operation_label="Main window center failed",
        )


class WindowDialogManager:
    """窗口对话框管理器，封装 close confirm dialog 的状态与显示/隐藏逻辑.

    从 main.py 抽取，便于单元测试。main.py 调用方负责：
    1. 创建 CloseConfirmDialog 组件并传入 _show_close_confirm_dialog
    2. 注入 on_shutdown_request callback（通常为 lambda: page.run_task(perform_window_shutdown)）
    3. 在 shutdown 完成后重置 shutdown_requested = False（在 perform_window_shutdown 的 finally 中）
    """

    def __init__(
        self,
        page: ft.Page,
        *,
        on_shutdown_request: Callable[[], None] | None = None,
    ) -> None:
        self.page = page
        self.close_confirm_visible: bool = False
        self.shutdown_requested: bool = False
        self.active_dialog: ft.Control | None = None
        self.current_close_confirm_dialog: ft.Control | None = None
        self._on_shutdown_request = on_shutdown_request

    def _page_dialog_matches_close_confirm(self) -> bool:
        """检查当前 page 上的 dialog 是否为 close confirm dialog."""
        return self.active_dialog is self.current_close_confirm_dialog

    def _show_dialog(self, dialog: ft.Control) -> None:
        """显示对话框并记录为 active_dialog."""
        self.page.show_dialog(dialog)  # type: ignore[arg-type]  # [reason: ft.Control 基类涵盖 DialogControl 子类，运行时接受]
        self.active_dialog = dialog

    def _hide_dialog(self, dialog: ft.Control) -> None:
        """隐藏对话框，如果当前 active_dialog 是该 dialog 则清空."""
        self.page.pop_dialog()
        if self.active_dialog is dialog:
            self.active_dialog = None

    def _show_close_confirm_dialog(self, dialog: ft.Control) -> None:
        """显示关闭确认对话框（如果尚未显示且未在 shutdown 流程中）."""
        logger.debug(
            "[Main] Request to show close confirm dialog. visible=%s, shutdown_requested=%s, "
            "dialog_exists=%s, page_dialog_is_close_confirm=%s",
            self.close_confirm_visible,
            self.shutdown_requested,
            self.current_close_confirm_dialog is not None,
            self._page_dialog_matches_close_confirm(),
        )
        if self.close_confirm_visible or self.shutdown_requested:
            logger.debug(
                "[Main] Skip showing close confirm dialog. visible=%s, shutdown_requested=%s",
                self.close_confirm_visible,
                self.shutdown_requested,
            )
            return
        self.current_close_confirm_dialog = dialog
        self._show_dialog(dialog)
        self.close_confirm_visible = True

    def _hide_close_confirm_dialog(self) -> None:
        """隐藏关闭确认对话框（如果存在）."""
        logger.debug(
            "[Main] Hiding close confirm dialog. visible=%s, dialog_exists=%s, page_dialog_is_close_confirm=%s",
            self.close_confirm_visible,
            self.current_close_confirm_dialog is not None,
            self._page_dialog_matches_close_confirm(),
        )
        if self.current_close_confirm_dialog is None:
            return
        self._hide_dialog(self.current_close_confirm_dialog)
        self.current_close_confirm_dialog = None
        self.close_confirm_visible = False

    def _on_close_cancel(self, e: ft.ControlEvent) -> None:
        """关闭确认对话框的取消按钮回调."""
        UILogger.log_action("MainWindow", action="close_cancel")
        self._hide_close_confirm_dialog()

    def _on_close_confirm(self, e: ft.ControlEvent) -> None:
        """关闭确认对话框的确认按钮回调，触发 shutdown 流程."""
        self._hide_close_confirm_dialog()
        if self.shutdown_requested:
            return
        self.shutdown_requested = True
        UILogger.log_action("MainWindow", action="close_confirm")
        if self._on_shutdown_request is not None:
            self._on_shutdown_request()


async def perform_window_shutdown(
    coordinator: ShutdownCoordinator,
    page: ft.Page,
    *,
    is_web_mode_fn: Callable[[], bool],
) -> bool:
    """执行窗口关闭 shutdown 流程.

    流程：
    1. 启动 watchdog
    2. 执行 cleanup（timeout=20s）
    3. 非 web_mode 时销毁窗口（destroy 失败仅记录日志，不阻塞流程）
    4. cleanup 成功：cancel_watchdog，返回 True
    5. cleanup 失败：log error，sleep 0.2s，force_exit(1)，返回 False

    Returns:
        True 表示 cleanup 成功；False 表示 cleanup 不完整（已 force_exit）
    """
    logger.info("[Main] Window close confirmed by user.")
    coordinator.start_watchdog(60.0)
    cleanup_ok = await coordinator.do_cleanup(timeout_s=50.0, step_timeout_s=35.0)
    try:
        if not is_web_mode_fn():
            page.window.prevent_close = False
            await page.window.destroy()
    except Exception as e:
        log_exception_with_severity(
            e,
            context="general",
            operation_label="Main window destroy failed",
        )
    if cleanup_ok:
        coordinator.cancel_watchdog()
        logger.info("[Main] Graceful window shutdown completed without force-exit.")
        return True
    logger.error(
        "[Main] Graceful shutdown incomplete, forcing process exit. Step results: %s",
        [
            f"{r.name}(ok={r.ok}, timed_out={r.timed_out}, elapsed={r.elapsed_ms:.0f}ms"
            f"{', error=' + r.error if r.error else ''})"
            for r in coordinator.step_results
        ],
    )
    await asyncio.sleep(0.2)
    coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: ShutdownCoordinator._force_exit 为实例属性 callable，main.py 原逻辑亦直接访问]
    return False


async def perform_upgrade_exit(
    coordinator: ShutdownCoordinator,
    page: ft.Page,
    *,
    is_web_mode_fn: Callable[[], bool],
) -> None:
    """Upgrade 失败后的清理并强制退出.

    流程：
    1. 执行 cleanup（timeout=5s, step_timeout=1s）
    2. cleanup 失败仅记录日志
    3. 非 web_mode 时销毁窗口（destroy 失败仅记录日志）
    4. force_exit(1)
    """
    cleanup_ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=1.0)
    if not cleanup_ok:
        logger.error("[Main] Cleanup incomplete after upgrade failure exit.")
    try:
        if not is_web_mode_fn():
            page.window.prevent_close = False
            await page.window.destroy()
    except Exception as e:
        log_exception_with_severity(
            e,
            context="general",
            operation_label="Main window destroy failed during upgrade exit",
        )
    coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: ShutdownCoordinator._force_exit 为实例属性 callable，main.py 原逻辑亦直接访问]


async def handle_disconnect(
    coordinator: ShutdownCoordinator,
    *,
    cleanup_done_fn: Callable[[], bool],
) -> None:
    """处理外部 disconnect 事件.

    流程：
    1. 启动 watchdog（25s）
    2. 执行 cleanup（timeout=20s）
    3. 如果 cleanup_done_fn() 返回 True：直接返回（其他路径已处理）
    4. cleanup_ok=True：cancel_watchdog，log info，返回（等待 runtime 自然终止）
    5. cleanup_ok=False：log error，sleep 0.2s，force_exit(1)
    """
    coordinator.start_watchdog(60)
    cleanup_ok = await coordinator.do_cleanup(timeout_s=50.0, step_timeout_s=35.0)
    if cleanup_done_fn():
        return
    if cleanup_ok:
        coordinator.cancel_watchdog()
        logger.info("[Main] External disconnect cleanup completed; waiting for runtime to terminate naturally.")
        return
    logger.error("[Main] External disconnect cleanup incomplete, forcing process exit.")
    await asyncio.sleep(0.2)
    coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: ShutdownCoordinator._force_exit 为实例属性 callable，main.py 原逻辑亦直接访问]
