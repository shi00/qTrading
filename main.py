import asyncio
import logging
import os
from collections.abc import Callable

import flet as ft

from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.exception_hooks import install_asyncio_handler_for_loop, install_global_exception_hooks
from utils.log_decorators import UILogger
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager
from data.cache.cache_manager import CacheManager
from ui.components.toast_manager import ToastManager
from ui.theme import apply_page_theme
from app.bootstrap import mask_sensitive
from app.startup_controller import StartupController
from ui.startup_views import StartupView, _StartupBridge

logger = logging.getLogger(__name__)


@ft.component
def CloseConfirmDialog(
    on_cancel: Callable[[ft.ControlEvent], None],
    on_confirm: Callable[[ft.ControlEvent], None],
) -> ft.AlertDialog:
    """窗口关闭确认对话框 (声明式, i18n state 驱动自动重渲染).

    CLAUDE.md §3.2 MVVM: i18n 通过 ``ft.use_state(I18n.get_observable_state)``
    订阅, locale 切换时自动重渲染, 无需手动刷新控件。
    """
    ft.use_state(I18n.get_observable_state)
    return ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("exit_confirm_title")),
        content=ft.Text(I18n.get("exit_confirm_content")),
        actions=[
            ft.TextButton(I18n.get("common_cancel"), on_click=on_cancel),
            ft.TextButton(I18n.get("common_confirm"), on_click=on_confirm),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )


async def main(page: ft.Page):
    setup_logging()

    from utils.correlation import ensure_correlation_id

    ensure_correlation_id()

    try:
        loop = asyncio.get_running_loop()
        install_asyncio_handler_for_loop(loop)
    except RuntimeError:
        pass

    ConfigHandler.ensure_defaults()

    ProxyManager.apply_smart_proxy_policy()

    I18n.initialize(ConfigHandler.get_locale())

    try:
        lang, country = I18n.current_locale().split("_")
        current_locale = ft.Locale(lang, country)
    except (ValueError, AttributeError, IndexError):
        current_locale = ft.Locale("zh", "CN")

    page.locale_configuration = ft.LocaleConfiguration(
        supported_locales=[
            ft.Locale("zh", "CN"),
            ft.Locale("en", "US"),
        ],
        current_locale=current_locale,
    )

    cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window.icon = "icon.png"

    from utils.shutdown import ShutdownCoordinator

    coordinator = ShutdownCoordinator(page)
    close_confirm_visible = False
    shutdown_requested = False
    active_dialog = None
    current_close_confirm_dialog = None

    async def _perform_window_shutdown():
        nonlocal shutdown_requested
        try:
            logger.info("[Main] Window close confirmed by user.")
            coordinator.start_watchdog()

            cleanup_ok = await coordinator.do_cleanup(timeout_s=20.0)

            try:
                if not _is_web_mode():
                    page.window.prevent_close = False
                    await page.window.destroy()
            except Exception as e:
                logger.debug("Window destroy ignored: %s", e)

            if cleanup_ok:
                coordinator.cancel_watchdog()
                logger.info("[Main] Graceful window shutdown completed without force-exit.")
                return

            logger.error(
                "[Main] Graceful shutdown incomplete, forcing process exit. Step results: %s",
                [
                    f"{r.name}(ok={r.ok}, timed_out={r.timed_out}, elapsed={r.elapsed_ms:.0f}ms"
                    f"{', error=' + r.error if r.error else ''})"
                    for r in coordinator.step_results
                ],
            )
            await asyncio.sleep(0.2)
            coordinator._force_exit(1)
        finally:
            shutdown_requested = False

    def _page_dialog_matches_close_confirm() -> bool:
        return active_dialog is current_close_confirm_dialog

    def _show_dialog(dialog):
        nonlocal active_dialog
        page.show_dialog(dialog)
        active_dialog = dialog

    def _hide_dialog(dialog):
        nonlocal active_dialog
        page.pop_dialog()
        if active_dialog is dialog:
            active_dialog = None

    def _hide_close_confirm_dialog():
        nonlocal close_confirm_visible, current_close_confirm_dialog
        logger.debug(
            "[Main] Hiding close confirm dialog. visible=%s, dialog_exists=%s, page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            current_close_confirm_dialog is not None,
            _page_dialog_matches_close_confirm(),
        )
        if current_close_confirm_dialog is None:
            return
        _hide_dialog(current_close_confirm_dialog)
        current_close_confirm_dialog = None
        close_confirm_visible = False

    def _on_close_cancel(_):
        UILogger.log_action("MainWindow", action="close_cancel")
        _hide_close_confirm_dialog()

    def _on_close_confirm(_):
        nonlocal shutdown_requested
        _hide_close_confirm_dialog()
        if shutdown_requested:
            return
        shutdown_requested = True
        UILogger.log_action("MainWindow", action="close_confirm")
        page.run_task(_perform_window_shutdown)

    def _show_close_confirm_dialog():
        nonlocal close_confirm_visible, current_close_confirm_dialog
        logger.debug(
            "[Main] Request to show close confirm dialog. visible=%s, shutdown_requested=%s, "
            "dialog_exists=%s, page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            shutdown_requested,
            current_close_confirm_dialog is not None,
            _page_dialog_matches_close_confirm(),
        )
        if close_confirm_visible or shutdown_requested:
            logger.debug(
                "[Main] Skip showing close confirm dialog. visible=%s, shutdown_requested=%s",
                close_confirm_visible,
                shutdown_requested,
            )
            return
        current_close_confirm_dialog = CloseConfirmDialog(_on_close_cancel, _on_close_confirm)
        _show_dialog(current_close_confirm_dialog)
        close_confirm_visible = True

    def _is_web_mode() -> bool:
        return os.environ.get("FLET_FORCE_WEB_SERVER", "").lower() in ("true", "1", "yes")

    if not _is_web_mode():
        page.window.prevent_close = True

    async def _on_window_event(e):
        logger.debug(
            "[Main] Window event received. type=%s, close_confirm_visible=%s, shutdown_requested=%s",
            getattr(e, "type", None),
            close_confirm_visible,
            shutdown_requested,
        )
        if e.type == ft.WindowEventType.CLOSE:
            UILogger.log_action("MainWindow", action="close_request")
            _show_close_confirm_dialog()

    if not _is_web_mode():
        page.window.on_event = _on_window_event

    async def _on_disconnect(e):
        coordinator.start_watchdog(25)
        cleanup_ok = await coordinator.do_cleanup(timeout_s=20.0)

        if not coordinator.cleanup_done:
            if cleanup_ok:
                coordinator.cancel_watchdog()
                logger.info("[Main] External disconnect cleanup completed; waiting for runtime to terminate naturally.")
                return
            logger.error("[Main] External disconnect cleanup incomplete, forcing process exit.")
            await asyncio.sleep(0.2)
            coordinator._force_exit(1)

    # E2E web 模式下多个浏览器 session 共享一个 Flet server 进程。
    # session 断开不应触发 shutdown cleanup（会销毁不可恢复的共享资源如 ThreadPool）。
    # 进程最终通过 proc.terminate() 清理。
    if os.environ.get("E2E_TESTING") != "true":
        page.on_disconnect = _on_disconnect

    def on_error(e):
        logger.error("[App] Unhandled UI Exception: %s", e, exc_info=True)

    page.on_error = on_error

    if not _is_web_mode():
        page.window.min_width = 1280
        page.window.min_height = 720
        if not page.window.width or page.window.width < 1280:
            page.window.width = 1280
            page.window.height = 800
        try:
            await page.window.center()
        except Exception:  # noqa: BLE001
            pass

    page.padding = 0
    apply_page_theme(page)

    page.toast = ToastManager(page)  # type: ignore[attr-defined]  # [reason: 动态挂载 ToastManager 到 Page 实例，ft.Page 类型存根无 toast 属性]

    def show_toast(message, type="info"):
        page.toast.show(message, type)  # type: ignore[attr-defined]  # [reason: 访问动态挂载的 toast 属性，类型存根未声明]

    page.show_toast = show_toast  # type: ignore[attr-defined]  # [reason: 动态挂载 show_toast 函数到 Page 实例，供 UI 层通过 page.show_toast 调用]

    # --- Startup flow: delegate to StartupController + StartupViewRenderer ---

    async def _perform_upgrade_exit():
        """Cleanup and force exit after upgrade failure."""
        cleanup_ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=1.0)
        if not cleanup_ok:
            logger.error("[Main] Cleanup incomplete after upgrade failure exit.")
        try:
            if not _is_web_mode():
                page.window.prevent_close = False
                await page.window.destroy()
        except Exception:  # noqa: BLE001
            pass
        coordinator._force_exit(1)

    def _on_show_toast(message_key, toast_type="info"):
        """Wrap show_toast to resolve i18n keys before displaying."""
        show_toast(I18n.get(message_key), toast_type)

    bridge = _StartupBridge()
    controller = StartupController(
        cache_manager=cache_manager,
        on_state_change=bridge.notify,
        on_show_toast=_on_show_toast,
        on_exit=lambda: page.run_task(_perform_upgrade_exit),  # type: ignore[arg-type]  # [reason: page.run_task 返回 Task，on_exit 回调期望 None，返回值被忽略]
    )

    page.add(
        StartupView(
            controller=controller,
            bridge=bridge,
            show_dialog_fn=_show_dialog,
            hide_dialog_fn=_hide_dialog,
            run_task_fn=page.run_task,
        )
    )

    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()

    masked_token = mask_sensitive(token)
    masked_llm_key = mask_sensitive(llm_api_key)
    logger.debug(
        "DB_URL configured: %s, Token='%s', API_Key='%s', Onboarding='%s'",
        bool(db_url),
        masked_token,
        masked_llm_key,
        onboarding_complete,
    )

    await controller.start(db_url, token, llm_api_key, onboarding_complete)

    # Phase 2A.1 Task 2A.1.9：注册启动期 auto probe 任务到 ShutdownCoordinator
    # （仅在 initialize_services 成功执行后非 None；onboarding 路径不创建 task）
    auto_probe_task = controller.auto_probe_task
    if auto_probe_task is not None and not auto_probe_task.done():
        coordinator.register_task(auto_probe_task)


if __name__ == "__main__":  # pragma: no cover
    import multiprocessing
    import os

    multiprocessing.freeze_support()

    install_global_exception_hooks()

    assets = os.path.join(os.path.dirname(__file__), "assets")
    run_kwargs = {"main": main, "assets_dir": assets}
    if os.environ.get("E2E_TESTING") == "true":
        run_kwargs["web_renderer"] = ft.WebRenderer.CANVAS_KIT
    ft.run(**run_kwargs)
