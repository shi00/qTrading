"""窗口生命周期管理：从 main.py 抽取的窗口几何、对话框管理、shutdown/upgrade/disconnect 流程。

抽取目的：将 main.py 中依赖 Flet Page 和 ShutdownCoordinator 的闭包改为可测的纯函数与类，
为 main.py 重构（Task 6.3）做准备。

依赖关系：app 层 → utils 层（ShutdownCoordinator 类型注解、UILogger、log_exception_with_severity）
+ core 层（I18n 关闭进度对话框文案），不导入 ui/services/strategies/data，符合 R1 分层架构。
"""

import asyncio
import logging
from collections.abc import Callable

import flet as ft

from app.error_logging import log_exception_with_severity
from core.i18n import I18n
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
    1. 入口立即显示非阻塞进度对话框（无 actions，不可手动取消）
    2. 启动 watchdog
    3. 执行 cleanup（timeout=60s, step_timeout=35s）
    4. 非 web_mode 时销毁窗口（destroy 失败仅记录日志，不阻塞流程）
    5. cleanup 成功：cancel_watchdog，关闭对话框，返回 True
    6. cleanup 失败：log error，sleep 0.2s，force_exit(1)，对话框不关闭（进程即将退出），返回 False

    Returns:
        True 表示 cleanup 成功；False 表示 cleanup 不完整（已 force_exit）
    """
    logger.info("[Main] Window close confirmed by user.")
    # 立即显示关闭进度对话框，告知用户正在关闭数据库（避免窗口卡死无反馈）。
    # modal=True 阻止点击对话框外部关闭；无 actions 时用户无确认/取消按钮（仅靠进程退出自然消除）。
    shutdown_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("shutdown_in_progress_title")),
        content=ft.Text(I18n.get("shutdown_in_progress_content")),
    )
    page.show_dialog(shutdown_dialog)  # type: ignore[arg-type]  # [reason: ft.AlertDialog 为 DialogControl 子类，page.show_dialog 运行时接受]
    # Phase 2 Step 8 (_step8_stop_embedded_postgres, 35s) 加入后，步骤超时和 = 55s。
    # watchdog 70s + do_cleanup 60s 容纳 55s 步骤之和 + 5s margin，保证 Step 8 graceful stop 完整执行。
    coordinator.start_watchdog(70.0)
    try:
        cleanup_ok = await coordinator.do_cleanup(timeout_s=60.0, step_timeout_s=35.0)
    except asyncio.CancelledError:
        # do_cleanup 被取消（如 session 断开/外部取消）：走失败路径强制退出，
        # 避免对话框残留 + 进程悬挂 + shutdown_requested 被重置后重入。
        logger.warning("[Main] do_cleanup was cancelled, forcing process exit.")
        await asyncio.sleep(0.2)
        coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: ShutdownCoordinator._force_exit 为实例属性 callable，_force_exit 后 os._exit 强退不会执行到 raise]
        raise  # R2: 不吞没 CancelledError（_force_exit 被替换为非强退实现时兜底）
    except Exception as e:
        logger.error("[Main] do_cleanup raised unexpectedly: %s", e, exc_info=True)
        await asyncio.sleep(0.2)
        coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: 同上]
        return False
    if cleanup_ok:
        coordinator.cancel_watchdog()
        # cleanup 成功，先关闭进度对话框再销毁窗口（destroy 后 page 连接断开，pop_dialog 可能无效）
        page.pop_dialog()
        logger.info("[Main] Graceful window shutdown completed without force-exit.")
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
        return True
    # cleanup 失败：对话框不关闭，进程即将被 force_exit 强制退出（watchdog 兜底）
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
    # upgrade exit 路径：step_timeout_s=10.0 给 Step 8 一线机会（stdin.close + 快速 wait）
    # 而非 1.0s（旧值，Step 8 必然超时）。整体 timeout_s=5.0 仍约束总时长。
    cleanup_ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=10.0)
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
    # Phase 2 Step 8 加入后，disconnect 路径同步调整：watchdog 70s + do_cleanup 60s
    coordinator.start_watchdog(70)
    cleanup_ok = await coordinator.do_cleanup(timeout_s=60.0, step_timeout_s=35.0)
    if cleanup_done_fn():
        return
    if cleanup_ok:
        coordinator.cancel_watchdog()
        logger.info("[Main] External disconnect cleanup completed; waiting for runtime to terminate naturally.")
        return
    logger.error("[Main] External disconnect cleanup incomplete, forcing process exit.")
    await asyncio.sleep(0.2)
    coordinator._force_exit(1)  # type: ignore[attr-defined]  # [reason: ShutdownCoordinator._force_exit 为实例属性 callable，main.py 原逻辑亦直接访问]
