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
    # 优雅退出全链路控制 (v4)
    # ============================================================
    _cleanup_done = False  # 防重入：确保清理逻辑只执行一次
    _watchdog_started = False  # 防重复启动 watchdog

    def _start_watchdog(timeout_s=10):
        """启动守护看门狗线程。幂等，只启动一次。"""
        nonlocal _watchdog_started
        if _watchdog_started:
            return
        _watchdog_started = True

        import os
        import threading

        def _force_exit():
            import time

            time.sleep(timeout_s)
            logger.warning(f"[Main] Watchdog timeout ({timeout_s}s) — forcing exit.")
            os._exit(0)

        threading.Thread(target=_force_exit, daemon=True).start()
        logger.info(f"[Main] Watchdog armed ({timeout_s}s).")

    async def _do_cleanup():
        """
        核心清理协程。停止所有后台服务，等待 DB 写入落地，关闭连接池。

        安全法则：
        - 绝不调用单例工厂（如 DataProcessor()），只访问 Class._instance
        - 不包含任何退出语句（sys.exit / os._exit），由调用方决定退出方式
        """
        nonlocal _cleanup_done
        if _cleanup_done:
            logger.info("[Main] Cleanup already completed, skipping.")
            return
        _cleanup_done = True

        logging.getLogger("asyncio").setLevel(logging.ERROR)
        logger.info("[Main] ========== Graceful Shutdown Initiated ==========")

        try:
            # ── Step 0: 取消 TaskManager 管理的所有应用级异步任务 ──
            logger.info("[Main] Step 0: Cancelling managed tasks...")
            from services.task_manager import TaskManager

            if TaskManager._instance is not None:
                await TaskManager._instance.cancel_all_running_async()

            # ── Step 1: 停止后台轮询服务 ──
            logger.info("[Main] Step 1: Stopping background services...")

            # scheduler 在模块导入时已实例化，直接安全调用
            if hasattr(scheduler, "scheduler") and scheduler.scheduler.running:
                logger.info("[Main]   - Scheduler")
                scheduler.stop()

            if NewsSubscriptionService._instance is not None:
                logger.info("[Main]   - NewsSubscriptionService")
                NewsSubscriptionService._instance.stop()

            if MarketDataService._instance is not None:
                logger.info("[Main]   - MarketDataService")
                MarketDataService._instance.stop()

            # 给 asyncio Task 响应取消信号的调度时间
            await asyncio.sleep(0.5)

            # ── Step 2: 停止数据处理引擎（取消所有同步策略） ──
            logger.info("[Main] Step 2: Stopping DataProcessor...")
            from data.data_processor import DataProcessor

            if DataProcessor._instance is not None:
                await DataProcessor._instance.stop()

            # ── Step 3: 等待 DB 写入落地 ──
            # DataProcessor.close() 原始实现在 stop() 和 cache.close() 之间
            # 等待 1.0s，让已提交但未完成的 DB 写入有时间执行 finally 块并释放连接。
            logger.info("[Main] Step 3: Waiting for pending DB writes to flush (1.0s)...")
            await asyncio.sleep(1.0)

            # ── Step 4: 清除 Toast Manager UI 残留 ──
            if hasattr(page, "toast") and getattr(page, "toast", None):
                try:
                    import inspect

                    if hasattr(page.toast, "stop_all"):  # type: ignore
                        res = page.toast.stop_all()  # type: ignore
                        if inspect.isawaitable(res):
                            await res
                except Exception:
                    pass

            # ── Step 5: 销毁异步数据库连接池 ──
            logger.info("[Main] Step 5: Disposing async DB engine...")
            if CacheManager._instance is not None and CacheManager._instance.engine is not None:
                await CacheManager._instance.close()
                logger.info("[Main]   - Async engine disposed.")
            else:
                logger.info("[Main]   - DB engine was never created, skipping.")

            # ── Step 6: 卸载本地 AI 模型（释放内存/显存） ──
            logger.info("[Main] Step 6: Unloading AI model...")
            try:
                from services.local_model_manager import LocalModelManager

                if LocalModelManager._instance is not None and LocalModelManager._instance._llm is not None:
                    LocalModelManager._instance.unload_model()
                    logger.info("[Main]   - Llama.cpp model evicted.")
            except Exception:
                pass

            # ── Step 7: 关闭线程池（最后执行，前面步骤可能依赖它） ──
            logger.info("[Main] Step 7: Shutting down Thread Pools...")
            from utils.thread_pool import ThreadPoolManager

            if ThreadPoolManager._instance is not None:
                ThreadPoolManager._instance.shutdown(wait=False)

        except Exception as ex:
            logger.error(f"[Main] Error during shutdown: {ex}", exc_info=True)

        logger.info("[Main] ========== Shutdown Sequence Complete ==========")

        # 刷写所有日志 handler，确保最后的消息不丢
        for handler in logging.root.handlers:
            try:
                handler.flush()
            except Exception:
                pass

    # ── 途径一：主退出路径 — 拦截窗口关闭事件 ──
    page.window.prevent_close = True

    async def _on_window_event(e):
        if e.type == ft.WindowEventType.CLOSE:
            logger.info("[Main] Window CLOSE event received.")
            _start_watchdog(10)

            await _do_cleanup()

            # 命令 Flet 销毁窗口（try/except 防止已损坏的 page 对象阻断退出）
            try:
                page.window.prevent_close = False
                page.window.destroy()
            except Exception:
                pass

            await asyncio.sleep(0.5)
            import sys

            sys.exit(0)

    page.window.on_event = _on_window_event

    # ── 途径二：兜底路径 — WebSocket 断开（外部 kill / 网络中断等） ──
    async def _on_disconnect(e):
        """
        Safety net.
        - 正常路径（途径一已执行）：_cleanup_done=True，直接跳过，
          途径一中的 os._exit(0) 会在 0.5s 后接管。
        - 外部断连路径（途径一未执行）：执行完整清理后主动退出。
        """
        was_window_path = _cleanup_done  # 进入时的快照

        _start_watchdog(10)
        await _do_cleanup()

        if not was_window_path:
            logger.info("[Main] External disconnect — exiting after cleanup.")
            await asyncio.sleep(0.5)
            import sys

            sys.exit(0)

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
                page.toast.show(f"📰 {msg}", type="info")  # type: ignore

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
