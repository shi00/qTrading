import asyncio  # pragma: no cover
import logging  # pragma: no cover

import flet as ft  # pragma: no cover

from data.cache.cache_manager import CacheManager  # pragma: no cover
from data.domain_services.market_data_service import MarketDataService  # pragma: no cover
from data.external.news_subscription import NewsSubscriptionService  # pragma: no cover
from ui.components.toast_manager import ToastManager  # pragma: no cover
from core.i18n import I18n  # pragma: no cover
from ui.theme import apply_page_theme  # pragma: no cover
from ui.views.onboarding_wizard import OnboardingWizard  # pragma: no cover
from utils.config_handler import ConfigHandler  # pragma: no cover
from utils.logger import setup_logging  # pragma: no cover
from utils.proxy_manager import ProxyManager  # pragma: no cover
from utils.scheduler_service import SchedulerService  # pragma: no cover

logger = logging.getLogger(__name__)  # pragma: no cover


async def main(page: ft.Page):  # pragma: no cover
    setup_logging()  # pragma: no cover

    ConfigHandler.ensure_defaults()  # pragma: no cover

    ProxyManager.apply_smart_proxy_policy()  # pragma: no cover

    I18n.initialize()  # pragma: no cover

    cache_manager = CacheManager()  # pragma: no cover

    page.title = I18n.get("app_title")  # pragma: no cover
    page.window_icon = "icon.png"  # type: ignore[attr-defined]  # pragma: no cover

    # ============================================================  # pragma: no cover
    # 优雅退出全链路控制 (v5) — 使用 ShutdownCoordinator  # pragma: no cover
    # ============================================================  # pragma: no cover
    from utils.shutdown import ShutdownCoordinator  # pragma: no cover

    coordinator = ShutdownCoordinator(page, watchdog_timeout_s=15.0)  # pragma: no cover
    close_confirm_dialog = None  # pragma: no cover
    close_confirm_visible = False  # pragma: no cover
    shutdown_requested = False  # pragma: no cover
    active_dialog = None  # pragma: no cover
    _scheduled_tasks: set = set()  # pragma: no cover

    def _schedule_async(coro):  # pragma: no cover
        if hasattr(page, "run_task"):  # pragma: no cover
            page.run_task(coro)  # pragma: no cover
            return  # pragma: no cover
        task = asyncio.create_task(coro())  # pragma: no cover
        _scheduled_tasks.add(task)  # pragma: no cover
        task.add_done_callback(_scheduled_tasks.discard)  # pragma: no cover

    async def _perform_window_shutdown():  # pragma: no cover
        nonlocal shutdown_requested  # pragma: no cover
        try:  # pragma: no cover
            logger.info("[Main] Window close confirmed by user.")  # pragma: no cover
            coordinator.start_watchdog()  # pragma: no cover

            cleanup_ok = await coordinator.do_cleanup(timeout_s=12.0, step_timeout_s=2.0)  # pragma: no cover

            try:  # pragma: no cover
                page.window.prevent_close = False  # pragma: no cover
                page.window.destroy()  # pragma: no cover
            except Exception as e:  # pragma: no cover
                logger.debug(f"Window destroy ignored: {e}")  # pragma: no cover

            if cleanup_ok:  # pragma: no cover
                coordinator.cancel_watchdog()  # pragma: no cover
                logger.info("[Main] Graceful window shutdown completed without force-exit.")  # pragma: no cover
                return  # pragma: no cover

            logger.error(  # pragma: no cover
                "[Main] Graceful shutdown incomplete, forcing process exit. Step results: %s",  # pragma: no cover
                [  # pragma: no cover
                    f"{r.name}(ok={r.ok}, timed_out={r.timed_out}, elapsed={r.elapsed_ms:.0f}ms"  # pragma: no cover
                    f"{', error=' + r.error if r.error else ''})"  # pragma: no cover
                    for r in coordinator.step_results  # pragma: no cover
                ],  # pragma: no cover
            )  # pragma: no cover
            await asyncio.sleep(0.2)  # pragma: no cover
            coordinator._force_exit(1)  # pragma: no cover
        finally:  # pragma: no cover
            shutdown_requested = False  # pragma: no cover

    def _page_dialog_matches_close_confirm() -> bool:  # pragma: no cover
        return active_dialog is close_confirm_dialog  # pragma: no cover

    def _show_dialog(dialog):  # pragma: no cover
        nonlocal active_dialog  # pragma: no cover
        if hasattr(page, "open"):  # pragma: no cover
            page.open(dialog)  # pragma: no cover
            active_dialog = dialog  # pragma: no cover
            return  # pragma: no cover
        # Fallback for older/test page implementations.  # pragma: no cover
        page.dialog = dialog  # pragma: no cover
        dialog.open = True  # pragma: no cover
        active_dialog = dialog  # pragma: no cover
        page.update()  # pragma: no cover

    def _hide_dialog(dialog):  # pragma: no cover
        nonlocal active_dialog  # pragma: no cover
        if hasattr(page, "close"):  # pragma: no cover
            page.close(dialog)  # pragma: no cover
            if active_dialog is dialog:  # pragma: no cover
                active_dialog = None  # pragma: no cover
            return  # pragma: no cover
        dialog.open = False  # pragma: no cover
        if active_dialog is dialog:  # pragma: no cover
            active_dialog = None  # pragma: no cover
        page.update()  # pragma: no cover

    def _hide_close_confirm_dialog():  # pragma: no cover
        nonlocal close_confirm_visible  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Hiding close confirm dialog. visible=%s, dialog_exists=%s, dialog_open=%s, "  # pragma: no cover
            "page_dialog_is_close_confirm=%s",  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            close_confirm_dialog is not None,  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
        )  # pragma: no cover
        if close_confirm_dialog is None:  # pragma: no cover
            return  # pragma: no cover
        _hide_dialog(close_confirm_dialog)  # pragma: no cover
        close_confirm_visible = False  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Close confirm dialog hidden. visible=%s, dialog_open=%s, page_dialog_is_close_confirm=%s",  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
        )  # pragma: no cover

    def _on_close_cancel(_):  # pragma: no cover
        logger.info("[Main] Window close canceled by user.")  # pragma: no cover
        _hide_close_confirm_dialog()  # pragma: no cover

    def _on_close_confirm(_):  # pragma: no cover
        nonlocal shutdown_requested  # pragma: no cover
        _hide_close_confirm_dialog()  # pragma: no cover
        if shutdown_requested:  # pragma: no cover
            return  # pragma: no cover
        shutdown_requested = True  # pragma: no cover
        _schedule_async(_perform_window_shutdown)  # pragma: no cover

    close_confirm_dialog = ft.AlertDialog(  # pragma: no cover
        modal=True,  # pragma: no cover
        title=ft.Text(I18n.get("exit_confirm_title", default="确认退出")),  # pragma: no cover
        content=ft.Text(  # pragma: no cover
            I18n.get(  # pragma: no cover
                "exit_confirm_content",  # pragma: no cover
                default="确认关闭程序吗？后台任务将停止并执行清理。",  # pragma: no cover
            )  # pragma: no cover
        ),  # pragma: no cover
        actions=[  # pragma: no cover
            ft.TextButton(I18n.get("common_cancel", default="取消"), on_click=_on_close_cancel),  # pragma: no cover
            ft.TextButton(I18n.get("common_confirm", default="确认"), on_click=_on_close_confirm),  # pragma: no cover
        ],  # pragma: no cover
        actions_alignment=ft.MainAxisAlignment.END,  # pragma: no cover
    )  # pragma: no cover

    def _show_close_confirm_dialog():  # pragma: no cover
        nonlocal close_confirm_visible  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Request to show close confirm dialog. visible=%s, shutdown_requested=%s, "  # pragma: no cover
            "dialog_exists=%s, dialog_open=%s, page_dialog_is_close_confirm=%s",  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            shutdown_requested,  # pragma: no cover
            close_confirm_dialog is not None,  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
        )  # pragma: no cover
        if close_confirm_visible or shutdown_requested:  # pragma: no cover
            logger.info(  # pragma: no cover
                "[Main] Skip showing close confirm dialog. visible=%s, shutdown_requested=%s, "  # pragma: no cover
                "dialog_open=%s, page_dialog_is_close_confirm=%s",  # pragma: no cover
                close_confirm_visible,  # pragma: no cover
                shutdown_requested,  # pragma: no cover
                getattr(close_confirm_dialog, "open", None),  # pragma: no cover
                _page_dialog_matches_close_confirm(),  # pragma: no cover
            )  # pragma: no cover
            return  # pragma: no cover
        _show_dialog(close_confirm_dialog)  # pragma: no cover
        close_confirm_visible = True  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Close confirm dialog assigned before page.update(). visible=%s, dialog_open=%s, "  # pragma: no cover
            "page_dialog_is_close_confirm=%s",  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
        )  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Close confirm dialog show request completed. visible=%s, dialog_open=%s, "  # pragma: no cover
            "page_dialog_is_close_confirm=%s",  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
        )  # pragma: no cover

    # ── 途径一：主退出路径 — 拦截窗口关闭事件 ──  # pragma: no cover
    page.window.prevent_close = True  # pragma: no cover

    async def _on_window_event(e):  # pragma: no cover
        logger.info(  # pragma: no cover
            "[Main] Window event received. type=%s, close_confirm_visible=%s, shutdown_requested=%s, "  # pragma: no cover
            "page_dialog_is_close_confirm=%s, dialog_open=%s",  # pragma: no cover
            getattr(e, "type", None),  # pragma: no cover
            close_confirm_visible,  # pragma: no cover
            shutdown_requested,  # pragma: no cover
            _page_dialog_matches_close_confirm(),  # pragma: no cover
            getattr(close_confirm_dialog, "open", None),  # pragma: no cover
        )  # pragma: no cover
        if e.type == ft.WindowEventType.CLOSE:  # pragma: no cover
            logger.info("[Main] Window CLOSE event received.")  # pragma: no cover
            _show_close_confirm_dialog()  # pragma: no cover

    page.window.on_event = _on_window_event  # pragma: no cover

    # ── 途径二：兜底路径 — WebSocket 断开（外部 kill / 网络中断等） ──  # pragma: no cover
    async def _on_disconnect(e):  # pragma: no cover
        coordinator.start_watchdog(15)  # pragma: no cover
        cleanup_ok = await coordinator.do_cleanup(timeout_s=12.0, step_timeout_s=2.0)  # pragma: no cover

        if not coordinator.cleanup_done:  # pragma: no cover
            if cleanup_ok:  # pragma: no cover
                coordinator.cancel_watchdog()  # pragma: no cover
                logger.info(
                    "[Main] External disconnect cleanup completed; waiting for runtime to terminate naturally."
                )  # pragma: no cover
                return  # pragma: no cover
            logger.error("[Main] External disconnect cleanup incomplete, forcing process exit.")  # pragma: no cover
            await asyncio.sleep(0.2)  # pragma: no cover
            coordinator._force_exit(1)  # pragma: no cover

    page.on_disconnect = _on_disconnect  # pragma: no cover

    def on_error(e):  # pragma: no cover
        logger.error(f"[App] Unhandled UI Exception: {e}", exc_info=True)  # pragma: no cover

    page.on_error = on_error  # pragma: no cover

    page.window.min_width = 960  # pragma: no cover
    page.window.min_height = 640  # pragma: no cover
    if not page.window.width or page.window.width < 1200:  # pragma: no cover
        page.window.width = 1280  # pragma: no cover
        page.window.height = 800  # pragma: no cover
    page.window.center()  # pragma: no cover

    page.padding = 0  # pragma: no cover
    apply_page_theme(page)  # pragma: no cover

    page.toast = ToastManager(page)  # type: ignore[attr-defined]  # pragma: no cover

    def show_toast(message, type="info"):  # pragma: no cover
        page.toast.show(message, type)  # type: ignore[attr-defined]  # pragma: no cover

    page.show_toast = show_toast  # type: ignore[attr-defined]  # pragma: no cover

    async def _init_services_and_start_app():  # pragma: no cover
        """Initialize all services and start the app."""  # pragma: no cover
        try:  # pragma: no cover
            await cache_manager.init_db()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.error(f"[Main] Database initialization failed: {e}", exc_info=True)  # pragma: no cover
            show_toast(I18n.get("error_db_init_failed", default=f"数据库初始化失败: {e}"), "error")  # pragma: no cover

            async def on_retry_click(e):  # pragma: no cover
                page.clean()  # pragma: no cover
                await _init_services_and_start_app()  # pragma: no cover

            def on_skip_click(e):  # pragma: no cover
                page.clean()  # pragma: no cover
                show_toast(
                    I18n.get("warning_skip_db", default="跳过数据库初始化，部分功能不可用"), "warning"
                )  # pragma: no cover
                from ui.app_layout import AppLayout  # pragma: no cover

                app_layout = AppLayout(page)  # pragma: no cover
                app_layout.show()  # pragma: no cover

            page.add(  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Icon(ft.icons.ERROR_OUTLINE, color=ft.colors.RED, size=48),  # pragma: no cover
                            ft.Text(  # pragma: no cover
                                I18n.get("error_db_init_failed", default="数据库初始化失败"),  # pragma: no cover
                                size=20,  # pragma: no cover
                                weight=ft.FontWeight.BOLD,  # pragma: no cover
                            ),  # pragma: no cover
                            ft.Text(str(e)[:200], color=ft.colors.RED_400, size=14),  # pragma: no cover
                            ft.Row(  # pragma: no cover
                                [  # pragma: no cover
                                    ft.ElevatedButton(  # pragma: no cover
                                        I18n.get("retry", default="重试"),  # pragma: no cover
                                        icon=ft.icons.REFRESH,  # pragma: no cover
                                        on_click=lambda e: page.run_task(on_retry_click, e),  # pragma: no cover
                                    ),  # pragma: no cover
                                    ft.TextButton(  # pragma: no cover
                                        I18n.get("skip", default="跳过"),  # pragma: no cover
                                        on_click=on_skip_click,  # pragma: no cover
                                    ),  # pragma: no cover
                                ],  # pragma: no cover
                                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                                spacing=20,  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                        spacing=10,  # pragma: no cover
                    ),  # pragma: no cover
                    expand=True,  # pragma: no cover
                    alignment=ft.alignment.center,  # pragma: no cover
                ),  # pragma: no cover
            )  # pragma: no cover
            return  # pragma: no cover

        from data.persistence.metadata_manager import MetaDataManager  # pragma: no cover

        MetaDataManager.preload_aliases()  # pragma: no cover

        if cache_manager.engine is None:  # pragma: no cover
            logger.error("[Main] Database engine not created after init_db().")  # pragma: no cover
            show_toast(
                I18n.get("error_db_engine_missing", default="数据库引擎未创建，请检查配置"), "error"
            )  # pragma: no cover
            return  # pragma: no cover

        from services.task_manager import TaskManager  # pragma: no cover

        try:  # pragma: no cover
            await TaskManager().init_db()  # pragma: no cover
        except Exception as e:  # pragma: no cover
            logger.error(f"[Main] TaskManager init failed: {e}", exc_info=True)  # pragma: no cover
            show_toast(
                I18n.get("error_db_init_failed", default=f"TaskManager 初始化失败: {e}"), "error"
            )  # pragma: no cover
            return  # pragma: no cover

        SchedulerService().start()  # pragma: no cover

        from ui.app_layout import AppLayout  # pragma: no cover

        app_layout = AppLayout(page)  # pragma: no cover

        def on_news_alert(msg):  # pragma: no cover
            if hasattr(page, "toast") and page.toast:  # type: ignore[attr-defined]  # pragma: no cover
                page.toast.show(f"📰 {msg}", toast_type="info")  # type: ignore[attr-defined]  # pragma: no cover

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)  # pragma: no cover

        NewsSubscriptionService().start()  # pragma: no cover
        MarketDataService().start()  # pragma: no cover

        app_layout.show()  # pragma: no cover

    async def on_onboarding_complete():  # pragma: no cover
        """Callback when onboarding wizard completes."""  # pragma: no cover
        await _init_services_and_start_app()  # pragma: no cover
        ConfigHandler.set_onboarding_complete(True)  # pragma: no cover

    db_url = ConfigHandler.get_db_url()  # pragma: no cover
    token = ConfigHandler.get_token()  # pragma: no cover
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")  # pragma: no cover
    onboarding_complete = ConfigHandler.is_onboarding_complete()  # pragma: no cover

    masked_token = f"{token[:4]}****" if token and len(token) > 4 else "None"  # pragma: no cover
    masked_llm_key = f"{llm_api_key[:4]}****" if llm_api_key and len(llm_api_key) > 4 else "None"  # pragma: no cover
    logger.debug(  # pragma: no cover
        f"DB_URL configured: {bool(db_url)}, Token='{masked_token}', API_Key='{masked_llm_key}', Onboarding='{onboarding_complete}'"  # pragma: no cover
    )  # pragma: no cover

    if not db_url or not token or not llm_api_key or not onboarding_complete:  # pragma: no cover
        wizard = OnboardingWizard(page, on_complete=on_onboarding_complete)  # pragma: no cover
        page.add(  # pragma: no cover
            ft.Container(  # pragma: no cover
                content=wizard,  # pragma: no cover
                expand=True,  # pragma: no cover
                padding=40,  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover
    else:  # pragma: no cover
        await _init_services_and_start_app()  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    import multiprocessing  # pragma: no cover
    import os  # pragma: no cover

    multiprocessing.freeze_support()  # pragma: no cover

    # Ensure assets are loaded correctly relative to this script,  # pragma: no cover
    # preventing errors if run from a different working directory.  # pragma: no cover
    assets = os.path.join(os.path.dirname(__file__), "assets")  # pragma: no cover
    ft.app(target=main, assets_dir=assets)  # pragma: no cover
