import asyncio
import logging

import flet as ft

from data.cache.cache_manager import CacheManager
from data.domain_services.market_data_service import MarketDataService
from data.external.news_subscription import NewsSubscriptionService
from ui.components.toast_manager import ToastManager
from ui.i18n import I18n
from ui.theme import apply_page_theme
from ui.views.onboarding_wizard import OnboardingWizard
from utils.config_handler import ConfigHandler
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager
from utils.scheduler_service import scheduler

logger = logging.getLogger(__name__)


async def main(page: ft.Page):
    setup_logging()

    ConfigHandler.ensure_defaults()

    ProxyManager.apply_smart_proxy_policy()

    I18n.initialize()

    cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window_icon = "icon.png"  # type: ignore

    # ============================================================
    # 优雅退出全链路控制 (v5) — 使用 ShutdownCoordinator
    # ============================================================
    from utils.shutdown import ShutdownCoordinator

    coordinator = ShutdownCoordinator(page)
    close_confirm_dialog = None
    close_confirm_visible = False
    shutdown_requested = False

    def _schedule_async(coro):
        if hasattr(page, "run_task"):
            # Flet page can schedule coroutines bound to UI loop.
            page.run_task(coro)
            return
        asyncio.create_task(coro())

    async def _perform_window_shutdown():
        nonlocal shutdown_requested
        try:
            logger.info("[Main] Window close confirmed by user.")
            coordinator.start_watchdog(10)

            cleanup_ok = await coordinator.do_cleanup(timeout_s=8.0, step_timeout_s=3.0)

            try:
                page.window.prevent_close = False
                page.window.destroy()
            except Exception:
                pass

            if cleanup_ok:
                coordinator.cancel_watchdog()
                logger.info("[Main] Graceful window shutdown completed without force-exit.")
                return

            logger.error("[Main] Graceful shutdown incomplete, forcing process exit.")
            await asyncio.sleep(0.2)
            import os

            os._exit(0)
        finally:
            shutdown_requested = False

    def _hide_close_confirm_dialog():
        nonlocal close_confirm_visible
        if close_confirm_dialog is None:
            return
        close_confirm_dialog.open = False
        close_confirm_visible = False
        page.update()

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
        if close_confirm_visible or shutdown_requested:
            return
        page.dialog = close_confirm_dialog
        close_confirm_dialog.open = True
        close_confirm_visible = True
        page.update()

    # ── 途径一：主退出路径 — 拦截窗口关闭事件 ──
    page.window.prevent_close = True

    async def _on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            logger.info("[Main] Window CLOSE event received.")
            _show_close_confirm_dialog()

    page.window.on_event = _on_window_event

    # ── 途径二：兜底路径 — WebSocket 断开（外部 kill / 网络中断等） ──
    async def _on_disconnect(e):
        was_window_path = coordinator.cleanup_done

        coordinator.start_watchdog(10)
        cleanup_ok = await coordinator.do_cleanup(timeout_s=8.0, step_timeout_s=3.0)

        if not was_window_path:
            if cleanup_ok:
                coordinator.cancel_watchdog()
                logger.info("[Main] External disconnect cleanup completed; waiting for runtime to terminate naturally.")
                return
            logger.error("[Main] External disconnect cleanup incomplete, forcing process exit.")
            await asyncio.sleep(0.2)
            import os

            os._exit(0)

    page.on_disconnect = _on_disconnect

    def on_error(e):
        logger.error(f"[App] Unhandled UI Exception: {e}", exc_info=True)

    page.on_error = on_error

    page.window.min_width = 960
    page.window.min_height = 640
    if not page.window.width or page.window.width < 1200:
        page.window.width = 1280
        page.window.height = 800
    page.window.center()

    page.padding = 0
    apply_page_theme(page)

    page.toast = ToastManager(page)  # type: ignore

    def show_toast(message, type="info"):
        page.toast.show(message, type)  # type: ignore

    page.show_toast = show_toast  # type: ignore

    async def _init_services_and_start_app():
        """Initialize all services and start the app."""
        try:
            await cache_manager.init_db()
        except Exception as e:
            logger.error(f"[Main] Database initialization failed: {e}", exc_info=True)
            show_toast(I18n.get("error_db_init_failed", default=f"数据库初始化失败: {e}"), "error")
            page.add(
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.icons.ERROR_OUTLINE, color=ft.colors.RED, size=48),
                            ft.Text(
                                I18n.get("error_db_init_failed", default="数据库初始化失败"),
                                size=20,
                                weight=ft.FontWeight.BOLD,
                            ),
                            ft.Text(str(e)[:200], color=ft.colors.RED_400, size=14),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                        spacing=10,
                    ),
                    expand=True,
                    alignment=ft.alignment.center,
                ),
            )
            return

        if cache_manager.engine is None:
            logger.error("[Main] Database engine not created after init_db().")
            show_toast(I18n.get("error_db_engine_missing", default="数据库引擎未创建，请检查配置"), "error")
            return

        from services.task_manager import TaskManager

        try:
            await TaskManager().init_db()
        except Exception as e:
            logger.error(f"[Main] TaskManager init failed: {e}", exc_info=True)
            show_toast(I18n.get("error_db_init_failed", default=f"TaskManager 初始化失败: {e}"), "error")
            return

        scheduler.start()

        from ui.app_layout import AppLayout

        app_layout = AppLayout(page)

        def on_news_alert(msg):
            if hasattr(page, "toast") and page.toast:  # type: ignore
                page.toast.show(f"📰 {msg}", toast_type="info")  # type: ignore

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)

        NewsSubscriptionService().start()
        MarketDataService().start()

        app_layout.show()

    async def on_onboarding_complete():
        """Callback when onboarding wizard completes."""
        await _init_services_and_start_app()
        ConfigHandler.set_onboarding_complete(True)

    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()

    masked_token = f"{token[:4]}****" if token and len(token) > 4 else "None"
    masked_llm_key = f"{llm_api_key[:4]}****" if llm_api_key and len(llm_api_key) > 4 else "None"
    logger.debug(
        f"DB_URL configured: {bool(db_url)}, Token='{masked_token}', API_Key='{masked_llm_key}', Onboarding='{onboarding_complete}'"
    )

    if not db_url or not token or not llm_api_key or not onboarding_complete:
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


if __name__ == "__main__":
    import os

    # Ensure assets are loaded correctly relative to this script,
    # preventing errors if run from a different working directory.
    assets = os.path.join(os.path.dirname(__file__), "assets")
    ft.app(target=main, assets_dir=assets)
