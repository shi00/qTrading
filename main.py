import asyncio
import logging
import os
import sys
from collections.abc import Callable
from typing import Any

import flet as ft

from app.bootstrap import mask_sensitive
from app.error_logging import log_exception_with_severity
from app.startup_controller import StartupController
from app.window_lifecycle import (
    WindowDialogManager,
    build_locale_configuration,
    handle_disconnect,
    perform_upgrade_exit,
    perform_window_shutdown,
    setup_window_geometry,
)
from data.cache.cache_manager import CacheManager
from ui.components.flet_type_helpers import safe_controls, safe_on_click
from ui.components.toast_manager import ToastManager, ToastManagerView
from ui.i18n import I18n, get_observable_state
from ui.startup_views import StartupView, _StartupBridge
from ui.theme import apply_page_theme
from utils.config_handler import ConfigHandler
from utils.exception_hooks import install_asyncio_handler_for_loop, install_global_exception_hooks
from utils.log_decorators import UILogger
from utils.logger import setup_logging
from utils.proxy_manager import ProxyManager

logger = logging.getLogger(__name__)


@ft.component
def CloseConfirmDialog(
    on_cancel: Callable[[ft.ControlEvent], None],
    on_confirm: Callable[[ft.ControlEvent], None],
) -> ft.AlertDialog:
    """窗口关闭确认对话框 (声明式, i18n state 驱动自动重渲染).

    CLAUDE.md §3.2 MVVM: i18n 通过 ``ft.use_state(get_observable_state)``
    订阅, locale 切换时自动重渲染, 无需手动刷新控件。
    """
    ft.use_state(get_observable_state)
    return ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("exit_confirm_title")),
        content=ft.Text(I18n.get("exit_confirm_content")),
        actions=safe_controls(
            [
                ft.TextButton(I18n.get("common_cancel"), on_click=safe_on_click(on_cancel)),
                ft.TextButton(I18n.get("common_confirm"), on_click=safe_on_click(on_confirm)),
            ]
        ),
        actions_alignment=ft.MainAxisAlignment.END,
    )


@ft.component
def RootView(
    controller: StartupController,
    bridge: _StartupBridge,
    run_task_fn: Callable[..., Any],
) -> ft.Stack:
    """应用根组件: StartupView + ToastManagerView 共享同一 renderer context.

    CLAUDE.md §3.2 MVVM + 声明式 UI: ``ToastManagerView`` 为 ``@ft.component``,
    调用时需 ``current_renderer()`` contextvar (Flet V1 ``@ft.component`` 装饰器
    在 ``component_decorator.py`` 内强制检查)。``page.overlay.append(ToastManagerView())``
    在 ``page.render()`` 之外执行会触发 ``RuntimeError: No current renderer is set``。

    本根组件通过 ``ft.Stack`` 将 ``ToastManagerView`` 与 ``StartupView`` 一起在
    ``page.render(RootView, ...)`` 内渲染, 共享同一 renderer。``ToastManagerView``
    返回的 ``ft.Container`` 继承 ``LayoutControl``, 其 ``right=20, bottom=20``
    属性作为 Stack 子项绝对定位, 视觉等价于原 ``page.overlay`` 挂载。
    """
    return ft.Stack(
        [
            StartupView(
                controller=controller,
                bridge=bridge,
                run_task_fn=run_task_fn,
            ),
            ToastManagerView(),
        ],
        expand=True,
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

    # Phase 2 §3.4：embedded 模式下启动 sidecar 并返回 URL（D15：不持久化到 config）
    from app.bootstrap import prepare_database_runtime

    # H2: prepare_database_runtime 失败时记 critical 日志并退出（不让 CacheManager 在无 DB 时启动）
    try:
        embedded_db_url = await prepare_database_runtime()
    except Exception as e:
        logger.critical("[Main] prepare_database_runtime failed: %s", e, exc_info=True)
        log_exception_with_severity(e, context="general", operation_label="prepare_database_runtime failed")
        sys.exit(1)

    ProxyManager.apply_smart_proxy_policy()

    I18n.initialize(ConfigHandler.get_locale())

    page.locale_configuration = build_locale_configuration(I18n.current_locale())

    # D15（pg-plan §22）：embedded 模式下用 override_db_url 包裹 CacheManager() 构造，
    # 不再依赖 save_db_config 持久化的 URL。
    if embedded_db_url:
        from data.persistence.db_url_override import override_db_url

        with override_db_url(embedded_db_url):
            cache_manager = CacheManager()
    else:
        cache_manager = CacheManager()

    page.title = I18n.get("app_title")
    page.window.icon = "icon.png"

    from utils.shutdown import ShutdownCoordinator

    coordinator = ShutdownCoordinator(page)

    def _is_web_mode() -> bool:
        return os.environ.get("FLET_FORCE_WEB_SERVER", "").lower() in ("true", "1", "yes")

    async def _perform_window_shutdown():
        try:
            await perform_window_shutdown(coordinator, page, is_web_mode_fn=_is_web_mode)
        finally:
            dialog_manager.shutdown_requested = False

    def _trigger_shutdown() -> None:
        page.run_task(_perform_window_shutdown)

    dialog_manager = WindowDialogManager(
        page,
        on_shutdown_request=_trigger_shutdown,
    )

    def _show_close_confirm_dialog():
        dialog = CloseConfirmDialog(dialog_manager._on_close_cancel, dialog_manager._on_close_confirm)
        dialog_manager._show_close_confirm_dialog(dialog)

    if not _is_web_mode():
        page.window.prevent_close = True

    async def _on_window_event(e):
        logger.debug(
            "[Main] Window event received. type=%s, close_confirm_visible=%s, shutdown_requested=%s",
            getattr(e, "type", None),
            dialog_manager.close_confirm_visible,
            dialog_manager.shutdown_requested,
        )
        if e.type == ft.WindowEventType.CLOSE:
            UILogger.log_action("MainWindow", action="close_request")
            _show_close_confirm_dialog()

    if not _is_web_mode():
        page.window.on_event = _on_window_event

    async def _on_disconnect(e):
        await handle_disconnect(coordinator, cleanup_done_fn=lambda: coordinator.cleanup_done)

    # E2E web 模式下多个浏览器 session 共享一个 Flet server 进程。
    # session 断开不应触发 shutdown cleanup（会销毁不可恢复的共享资源如 ThreadPool）。
    # 进程最终通过 proc.terminate() 清理。
    if os.environ.get("E2E_TESTING") != "true":
        page.on_disconnect = _on_disconnect

    def on_error(e):
        logger.error("[App] Unhandled UI Exception: %s", e, exc_info=True)

    page.on_error = on_error

    await setup_window_geometry(page, is_web_mode=_is_web_mode())

    page.padding = 0
    apply_page_theme(page)

    page.toast = ToastManager(page)  # type: ignore[attr-defined]  # [reason: 动态挂载 ToastManager 到 Page 实例，ft.Page 类型存根无 toast 属性]

    def show_toast(message, type="info", action_text=None, on_action=None):
        # P2-10: action_text/on_action 透传 ToastManager.show (导出引导"打开文件夹")
        page.toast.show(message, type, action_text=action_text, on_action=on_action)  # type: ignore[attr-defined]  # [reason: 访问动态挂载的 toast 属性，类型存根未声明]

    page.show_toast = show_toast  # type: ignore[attr-defined]  # [reason: 动态挂载 show_toast 函数到 Page 实例，供 UI 层通过 page.show_toast 调用]

    # --- Startup flow: delegate to StartupController + StartupViewRenderer ---

    async def _perform_upgrade_exit():
        """Cleanup and force exit after upgrade failure."""
        await perform_upgrade_exit(coordinator, page, is_web_mode_fn=_is_web_mode)

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

    page.render(
        RootView,
        controller=controller,
        bridge=bridge,
        run_task_fn=page.run_task,
    )

    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()

    masked_token = mask_sensitive(token)
    masked_llm_key = mask_sensitive(llm_api_key)
    logger.info(
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
