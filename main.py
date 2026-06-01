import asyncio
import logging
import os

import flet as ft

from app.bootstrap import check_onboarding_needed, initialize_services, mask_sensitive
from data.persistence.db_migrator import DatabaseMigrator
from data.cache.cache_manager import CacheManager
from data.external.news_subscription import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from core.i18n import I18n
from ui.theme import apply_page_theme
from ui.views.onboarding_wizard import OnboardingWizard
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager
from utils.exception_hooks import install_asyncio_handler_for_loop, install_global_exception_hooks

logger = logging.getLogger(__name__)


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

    I18n.initialize()

    cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window_icon = "icon.png"  # type: ignore[attr-defined]

    from utils.shutdown import ShutdownCoordinator

    coordinator = ShutdownCoordinator(page, watchdog_timeout_s=15.0)
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

            cleanup_ok = await coordinator.do_cleanup(timeout_s=12.0, step_timeout_s=2.0)

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
        logger.info(
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
        logger.info(
            "[Main] Close confirm dialog hidden. visible=%s, dialog_open=%s, page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )

    def _on_close_cancel(_):
        logger.info("[Main] Window close canceled by user.")
        _hide_close_confirm_dialog()

    def _on_close_confirm(_):
        nonlocal shutdown_requested
        _hide_close_confirm_dialog()
        if shutdown_requested:
            return
        shutdown_requested = True
        _schedule_async(_perform_window_shutdown)

    close_confirm_dialog = ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("exit_confirm_title", default="确认退出")),
        content=ft.Text(
            I18n.get(
                "exit_confirm_content",
                default="确认关闭程序吗？后台任务将停止并执行清理。",
            )
        ),
        actions=[
            ft.TextButton(I18n.get("common_cancel", default="取消"), on_click=_on_close_cancel),
            ft.TextButton(I18n.get("common_confirm", default="确认"), on_click=_on_close_confirm),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )

    def _show_close_confirm_dialog():
        nonlocal close_confirm_visible
        logger.info(
            "[Main] Request to show close confirm dialog. visible=%s, shutdown_requested=%s, "
            "dialog_exists=%s, dialog_open=%s, page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            shutdown_requested,
            close_confirm_dialog is not None,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )
        if close_confirm_visible or shutdown_requested:
            logger.info(
                "[Main] Skip showing close confirm dialog. visible=%s, shutdown_requested=%s, "
                "dialog_open=%s, page_dialog_is_close_confirm=%s",
                close_confirm_visible,
                shutdown_requested,
                getattr(close_confirm_dialog, "open", None),
                _page_dialog_matches_close_confirm(),
            )
            return
        _show_dialog(close_confirm_dialog)
        close_confirm_visible = True
        logger.info(
            "[Main] Close confirm dialog assigned before page.update(). visible=%s, dialog_open=%s, "
            "page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )
        logger.info(
            "[Main] Close confirm dialog show request completed. visible=%s, dialog_open=%s, "
            "page_dialog_is_close_confirm=%s",
            close_confirm_visible,
            getattr(close_confirm_dialog, "open", None),
            _page_dialog_matches_close_confirm(),
        )

    def _is_web_mode() -> bool:
        return os.environ.get("FLET_FORCE_WEB_SERVER", "").lower() in ("true", "1", "yes")

    if not _is_web_mode():
        page.window.prevent_close = True

    async def _on_window_event(e):
        logger.info(
            "[Main] Window event received. type=%s, close_confirm_visible=%s, shutdown_requested=%s, "
            "page_dialog_is_close_confirm=%s, dialog_open=%s",
            getattr(e, "type", None),
            close_confirm_visible,
            shutdown_requested,
            _page_dialog_matches_close_confirm(),
            getattr(close_confirm_dialog, "open", None),
        )
        if e.type == ft.WindowEventType.CLOSE:
            logger.info("[Main] Window CLOSE event received.")
            _show_close_confirm_dialog()

    if not _is_web_mode():
        page.window.on_event = _on_window_event

    async def _on_disconnect(e):
        coordinator.start_watchdog(15)
        cleanup_ok = await coordinator.do_cleanup(timeout_s=12.0, step_timeout_s=2.0)

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
                        title=ft.Text(I18n.get("db_upgrade_in_progress_title", default="正在升级数据库...")),
                        content=ft.Column(
                            [
                                ft.Text(
                                    I18n.get(
                                        "db_upgrade_in_progress_content", default="数据库升级正在进行中，请勿关闭程序。"
                                    )
                                ),
                                ft.ProgressBar(width=300),
                            ],
                            spacing=10,
                        ),
                        actions=[],
                        actions_alignment=ft.MainAxisAlignment.END,
                    )
                    _show_dialog(in_progress_dialog)

                    try:
                        await DatabaseMigrator.init_db(cache_manager.engine, auto_migrate=True)

                        _hide_dialog(in_progress_dialog)

                        success_dialog = ft.AlertDialog(
                            modal=True,
                            title=ft.Text(I18n.get("db_upgrade_success_title", default="升级成功")),
                            content=ft.Text(
                                I18n.get("db_upgrade_success_content", default="数据库已成功升级到最新版本。")
                            ),
                            actions=[
                                ft.TextButton(
                                    I18n.get("common_ok", default="确定"),
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
                            title=ft.Text(I18n.get("db_upgrade_error_title", default="升级失败")),
                            content=ft.Text(
                                I18n.get(
                                    "db_upgrade_error_content",
                                    default="数据库升级失败: {error}\n\n请查看日志文件或联系技术支持。",
                                ).format(error=error_str)
                            ),
                            actions=[
                                ft.TextButton(
                                    I18n.get("exit_program", default="退出程序"),
                                    on_click=lambda e: page.run_task(on_exit_click, e),
                                ),
                                ft.ElevatedButton(
                                    I18n.get("retry_upgrade", default="重试升级"),
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
                    title=ft.Text(I18n.get("db_upgrade_needed_title", default="数据库需要升级")),
                    content=ft.Text(
                        I18n.get(
                            "db_upgrade_needed_content",
                            default="应用检测到数据库版本较旧，需要升级后才能继续使用。\n\n升级将优化数据存储结构以支持最新功能。\n\n⚠️ 升级过程中请勿关闭程序。",
                        )
                    ),
                    actions=[
                        ft.ElevatedButton(
                            I18n.get("db_upgrade_btn", default="立即升级"),
                            on_click=lambda e: page.run_task(on_upgrade_click, e),
                        ),
                    ],
                    actions_alignment=ft.MainAxisAlignment.END,
                )
                _show_dialog(upgrade_dialog)

            elif result.get("error") == "db_init_failed":

                async def on_retry_click(e):
                    page.clean()
                    await _init_services_and_start_app()

                def on_skip_click(e):
                    page.clean()
                    show_toast(I18n.get("warning_skip_db", default="跳过数据库初始化，部分功能不可用"), "warning")
                    from ui.app_layout import AppLayout

                    app_layout = AppLayout(page)
                    app_layout.show()

                page.add(
                    ft.Container(
                        content=ft.Column(
                            [
                                ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED, size=48),
                                ft.Text(
                                    I18n.get("error_db_init_failed", default="数据库初始化失败"),
                                    size=20,
                                    weight=ft.FontWeight.BOLD,
                                ),
                                ft.Text(str(result.get("detail", ""))[:200], color=ft.Colors.RED_400, size=14),
                                ft.Row(
                                    [
                                        ft.ElevatedButton(
                                            I18n.get("retry", default="重试"),
                                            icon=ft.Icons.REFRESH,
                                            on_click=lambda e: page.run_task(on_retry_click, e),
                                        ),
                                        ft.TextButton(
                                            I18n.get("skip", default="跳过"),
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

    async def on_onboarding_complete():
        await _init_services_and_start_app()
        ConfigHandler.set_onboarding_complete(True)

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
