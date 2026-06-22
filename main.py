import asyncio
import logging
import os

import flet as ft

from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.exception_hooks import install_asyncio_handler_for_loop, install_global_exception_hooks
from utils.log_decorators import UILogger
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager
from utils.thread_pool import TaskType, ThreadPoolManager
from data.cache.cache_manager import CacheManager
from services.news_subscription_service import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from ui.theme import apply_page_theme
from ui.views.onboarding_wizard import OnboardingWizard
from app.bootstrap import check_onboarding_needed, initialize_services, mask_sensitive

logger = logging.getLogger(__name__)


def _get_localized_detail(detail: str) -> str:
    """Classify and return localized error message for database initialization details."""
    if not detail:
        return ""
    try:
        from utils.error_classifier import classify_error, get_error_message

        classified = classify_error(Exception(detail), context="db")
        if classified.get("message_key") != "db_err_unknown":
            return get_error_message(classified)
    except Exception as e:
        logger.warning("[Main] Failed to classify and localize error detail '%s': %s", detail, e)
    return detail


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

    cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window_icon = "icon.png"  # type: ignore[attr-defined]

    from utils.shutdown import ShutdownCoordinator

    coordinator = ShutdownCoordinator(page)
    close_confirm_dialog = None
    close_confirm_visible = False
    shutdown_requested = False
    active_dialog = None
    _scheduled_tasks: set = set()

    def _schedule_async(coro):
        if hasattr(page, "run_task"):
            page.run_task(coro)
            return
        task = asyncio.create_task(coro())
        _scheduled_tasks.add(task)
        task.add_done_callback(_scheduled_tasks.discard)

    async def _perform_window_shutdown():
        nonlocal shutdown_requested
        try:
            logger.info("[Main] Window close confirmed by user.")
            coordinator.start_watchdog()

            cleanup_ok = await coordinator.do_cleanup(timeout_s=20.0)

            try:
                if not _is_web_mode():
                    page.window.prevent_close = False
                    page.window.destroy()
            except Exception as e:
                logger.debug(f"Window destroy ignored: {e}")

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
        return active_dialog is close_confirm_dialog

    def _show_dialog(dialog):
        nonlocal active_dialog
        if hasattr(page, "open"):
            page.open(dialog)
            active_dialog = dialog
            return
        page.dialog = dialog  # type: ignore[attr-defined]
        dialog.open = True
        active_dialog = dialog
        page.update()

    def _hide_dialog(dialog):
        nonlocal active_dialog
        if hasattr(page, "close"):
            page.close(dialog)
            if active_dialog is dialog:
                active_dialog = None
            return
        dialog.open = False
        if active_dialog is dialog:
            active_dialog = None
        page.update()

    def _hide_close_confirm_dialog():
        nonlocal close_confirm_visible
        logger.debug(
            "[Main] Hiding close confirm dialog. visible=%s, dialog_exists=%s, dialog_open=%s, "
            "page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            close_confirm_dialog is not None,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )
        if close_confirm_dialog is None:
            return
        _hide_dialog(close_confirm_dialog)
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
        _schedule_async(_perform_window_shutdown)

    close_confirm_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("exit_confirm_title")),
        content=ft.Text(I18n.get("exit_confirm_content")),
        actions=[
            ft.TextButton(I18n.get("common_cancel"), on_click=_on_close_cancel),
            ft.TextButton(I18n.get("common_confirm"), on_click=_on_close_confirm),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def _show_close_confirm_dialog():
        nonlocal close_confirm_visible
        logger.debug(
            "[Main] Request to show close confirm dialog. visible=%s, shutdown_requested=%s, "
            "dialog_exists=%s, dialog_open=%s, page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            shutdown_requested,
            close_confirm_dialog is not None,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )
        if close_confirm_visible or shutdown_requested:
            logger.debug(
                "[Main] Skip showing close confirm dialog. visible=%s, shutdown_requested=%s",
                close_confirm_visible,
                shutdown_requested,
            )
            return
        _show_dialog(close_confirm_dialog)
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
    if not os.environ.get("E2E_TESTING"):
        page.on_disconnect = _on_disconnect

    def on_error(e):
        logger.error(f"[App] Unhandled UI Exception: {e}", exc_info=True)

    page.on_error = on_error

    if not _is_web_mode():
        page.window.min_width = 960
        page.window.min_height = 640
        if not page.window.width or page.window.width < 1200:
            page.window.width = 1280
            page.window.height = 800
        try:
            page.window.center()
        except Exception:  # noqa: BLE001
            pass

    page.padding = 0
    apply_page_theme(page)

    page.toast = ToastManager(page)  # type: ignore[attr-defined]

    def show_toast(message, type="info"):
        page.toast.show(message, type)  # type: ignore[attr-defined]

    page.show_toast = show_toast  # type: ignore[attr-defined]

    async def _init_services_and_start_app():
        result = await initialize_services(cache_manager, show_toast_fn=show_toast)

        if not result["success"]:
            if result.get("error") == "db_upgrade_needed":

                async def on_upgrade_click(e):
                    in_progress_dialog = ft.AlertDialog(
                        modal=True,
                        title=ft.Text(I18n.get("db_upgrade_in_progress_title")),
                        content=ft.Column(
                            [
                                ft.Text(I18n.get("db_upgrade_in_progress_content")),
                                ft.ProgressBar(width=300),
                            ],
                            spacing=10,
                        ),
                        actions=[],
                        actions_alignment=ft.MainAxisAlignment.END,
                    )
                    _show_dialog(in_progress_dialog)

                    try:
                        # 通过 CacheManager.init_db 而非直接调用 DatabaseMigrator，
                        # 确保 _schema_initialized 被正确设置
                        await cache_manager.init_db(force=True, auto_migrate=True)

                        _hide_dialog(in_progress_dialog)

                        success_dialog = ft.AlertDialog(
                            modal=True,
                            title=ft.Text(I18n.get("db_upgrade_success_title")),
                            content=ft.Text(I18n.get("db_upgrade_success_content")),
                            actions=[
                                ft.TextButton(
                                    I18n.get("common_ok"),
                                    on_click=lambda e: [
                                        _hide_dialog(success_dialog),
                                        page.run_task(_init_services_and_start_app),
                                    ],
                                ),
                            ],
                            actions_alignment=ft.MainAxisAlignment.END,
                        )
                        _show_dialog(success_dialog)
                    except Exception as upgrade_error:
                        _hide_dialog(in_progress_dialog)

                        error_str = str(upgrade_error)

                        async def on_exit_click(e):
                            logger.warning("[Main] User chose to exit after upgrade failure: %s", error_str)
                            _hide_dialog(error_dialog)
                            cleanup_ok = await coordinator.do_cleanup(timeout_s=5.0, step_timeout_s=1.0)
                            if not cleanup_ok:
                                logger.error("[Main] Cleanup incomplete after upgrade failure exit.")
                            try:
                                if not _is_web_mode():
                                    page.window.prevent_close = False
                                    page.window.destroy()
                            except Exception:  # noqa: BLE001
                                pass
                            coordinator._force_exit(1)

                        error_dialog = ft.AlertDialog(
                            modal=True,
                            title=ft.Text(I18n.get("db_upgrade_error_title")),
                            content=ft.Text(
                                I18n.get(
                                    "db_upgrade_error_content",
                                ).format(error=error_str)
                            ),
                            actions=[
                                ft.TextButton(
                                    I18n.get("exit_program"),
                                    on_click=lambda e: page.run_task(on_exit_click, e),
                                ),
                                ft.ElevatedButton(
                                    I18n.get("retry_upgrade"),
                                    on_click=lambda e: [
                                        logger.info("[Main] User chose to retry upgrade after failure."),
                                        _hide_dialog(error_dialog),
                                        page.run_task(on_upgrade_click, e),
                                    ],
                                ),
                            ],
                            actions_alignment=ft.MainAxisAlignment.END,
                        )
                        _show_dialog(error_dialog)

                upgrade_dialog = ft.AlertDialog(
                    modal=True,
                    title=ft.Text(I18n.get("db_upgrade_needed_title")),
                    content=ft.Text(I18n.get("db_upgrade_needed_content")),
                    actions=[
                        ft.ElevatedButton(
                            I18n.get("db_upgrade_btn"),
                            on_click=lambda e: page.run_task(on_upgrade_click, e),
                        ),
                    ],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
                _show_dialog(upgrade_dialog)

            elif result.get("error") in ("db_init_failed", "db_engine_missing", "task_manager_init_failed"):

                async def on_retry_click(e):
                    page.clean()
                    await _init_services_and_start_app()

                async def on_reconfigure_click(e):
                    page.clean()
                    await cache_manager.close()
                    await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, False)
                    wizard = OnboardingWizard(page, on_complete=on_onboarding_complete)
                    page.add(
                        ft.Container(
                            content=wizard,
                            expand=True,
                            padding=40,
                        ),
                    )

                def on_skip_click(e):
                    page.clean()
                    show_toast(I18n.get("warning_skip_db"), "warning")
                    from ui.app_layout import AppLayout

                    app_layout = AppLayout(page)
                    app_layout.show()

                page.add(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED, size=48),
                                ft.Text(
                                    I18n.get("error_db_init_failed")
                                    if result.get("error") != "db_engine_missing"
                                    else I18n.get("error_db_engine_missing"),
                                    size=20,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(
                                    _get_localized_detail(str(result.get("detail") or ""))[:200],
                                    color=ft.Colors.RED_400,
                                    size=14,
                                ),
                                ft.Row(
                                    [
                                        ft.ElevatedButton(
                                            I18n.get("retry"),
                                            icon=ft.Icons.REFRESH,
                                            on_click=lambda e: page.run_task(on_retry_click, e),
                                        ),
                                        ft.TextButton(
                                            I18n.get("db_reconfigure"),
                                            icon=ft.Icons.SETTINGS,
                                            on_click=lambda e: page.run_task(on_reconfigure_click, e),
                                        ),
                                        ft.TextButton(
                                            I18n.get("skip"),
                                            on_click=on_skip_click,
                                        ),
                                    ],
                                    alignment=ft.MainAxisAlignment.CENTER,
                                    spacing=20,
                                ),
                            ],
                            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=10,
                        ),
                        expand=True,
                        alignment=ft.alignment.center,
                    ),
                )
            return

        from ui.app_layout import AppLayout

        app_layout = AppLayout(page)

        def on_news_alert(msg):
            if hasattr(page, "toast") and page.toast:  # type: ignore[attr-defined]
                page.toast.show(f"📰 {msg}", toast_type="info")  # type: ignore[attr-defined]

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)

        app_layout.show()
        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, True)

    async def on_onboarding_complete():
        await _init_services_and_start_app()

    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()

    masked_token = mask_sensitive(token)
    masked_llm_key = mask_sensitive(llm_api_key)
    logger.debug(
        f"DB_URL configured: {bool(db_url)}, Token='{masked_token}', API_Key='{masked_llm_key}', Onboarding='{onboarding_complete}'"
    )

    if check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
        wizard = OnboardingWizard(page, on_complete=on_onboarding_complete)
        page.add(
            ft.Container(
                content=wizard,
                expand=True,
                padding=40,
            ),
        )
    else:
        await _init_services_and_start_app()


if __name__ == "__main__":  # pragma: no cover
    import multiprocessing
    import os

    multiprocessing.freeze_support()

    install_global_exception_hooks()

    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
