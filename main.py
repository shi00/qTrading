import asyncio
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import flet as ft

from app.bootstrap import EmbeddedPgStartupScenario, mask_sensitive


def _trace_log(msg: str) -> None:
    """E2E 诊断专用：直接写文件，绕过 logging/stdout，避免 Flet IPC 或缓冲吞没日志。"""
    try:
        from pathlib import Path

        trace_path = Path("logs") / "main_trace.log"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%H:%M:%S')}.{int(time.time() * 1000) % 1000:03d}] {msg}\n")
            f.flush()
    except Exception:  # noqa: BLE001
        pass


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


async def _wait_for_user_action(retry_event: threading.Event, exit_event: threading.Event) -> str:
    """等待用户点击 Retry 或 Exit，返回 ``"retry"`` 或 ``"exit"``。

    用 threading.Event + asyncio.to_thread 实现跨线程安全等待
    （Flet on_click 回调可能在 Flet 内部线程，不在事件循环线程）。

    finally 中先 set 两个 event 再 cancel task：asyncio.to_thread 的取消语义是
    协程取消时底层线程不取消，threading.Event.wait() 无超时会永久阻塞，
    必须通过 set event 唤醒线程避免线程泄漏（P1-1 修复）。

    注意：result 必须在 finally set event 之前记录，否则 exit_event.is_set()
    总是 True 导致永远返回 "exit"。
    """
    retry_task = asyncio.create_task(asyncio.to_thread(retry_event.wait))
    exit_task = asyncio.create_task(asyncio.to_thread(exit_event.wait))
    result = "retry"
    try:
        await asyncio.wait(
            [retry_task, exit_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        result = "exit" if exit_event.is_set() else "retry"
    finally:
        # 先 set 两个 event 唤醒可能仍在阻塞的线程（避免线程泄漏），再 cancel task
        retry_event.set()
        exit_event.set()
        for t in (retry_task, exit_task):
            if not t.done():
                t.cancel()
    return result


async def _wait_for_event_or_timeout(event: threading.Event, timeout: float) -> str:
    """等待 event 被 set 或超时，返回 ``"event"`` 或 ``"timeout"``（P2-4）。

    用 threading.Event + asyncio.to_thread + asyncio.sleep 实现可中断的退避等待：
    - event 被 set → 立即返回 ``"event"``（用户点 Exit 中断退避）
    - sleep 完成 → 返回 ``"timeout"``（退避结束，继续重试）

    finally 中 set event 唤醒可能仍在阻塞的 ``event.wait()`` 线程，避免线程泄漏
    （与 ``_wait_for_user_action`` 同样的线程安全策略）。

    CancelledError 正确传播（R2 红线），finally 仍会 set event 避免线程泄漏。
    """
    event_task = asyncio.create_task(asyncio.to_thread(event.wait))
    sleep_task = asyncio.create_task(asyncio.sleep(timeout))
    result = "timeout"
    try:
        await asyncio.wait(
            [event_task, sleep_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        result = "event" if event.is_set() else "timeout"
    finally:
        # 先 set event 唤醒可能仍在阻塞的 event.wait() 线程，再 cancel task
        event.set()
        for t in (event_task, sleep_task):
            if not t.done():
                t.cancel()
    return result


async def _prepare_db_with_retry(
    page: ft.Page,
    scenario: EmbeddedPgStartupScenario | None,
) -> str | None:
    """封装 prepare_database_runtime 的重试逻辑（P0-1 + P2-1）。

    失败时渲染 PreInitErrorView，用户可选择 Retry 或 Exit。
    - Retry: 指数退避等待后重试 prepare_database_runtime（P2-1）
    - Exit: ``sys.exit(0)``（sidecar 已被 ``_reset_singleton`` 清理，无资源泄漏）

    P2-1: 指数退避（1s, 2s, 4s, 8s, 16s, 30s 上限）给瞬时故障恢复时间；
    连续失败 ≥3 次时 PreInitErrorView 追加诊断提示 + 日志路径，引导用户查看日志。

    E2E / Web 模式保持原 ``sys.exit(1)`` 行为，避免无头浏览器等待 UI 交互超时
    或 Web 多 session 状态冲突。

    Raises:
        CancelledError: 用户在错误页关窗口或退避等待期间，main(page) 协程被取消，
            CancelledError 正确传播（R2 红线），sidecar 由 ``--parent-pid`` 兜底自杀。
    """
    failure_count = 0
    while True:
        try:
            from app.bootstrap import prepare_database_runtime

            return await prepare_database_runtime()
        except Exception as e:
            failure_count += 1
            logger.critical(
                "[Main] prepare_database_runtime failed (attempt %d): %s",
                failure_count,
                e,
                exc_info=True,
            )
            log_exception_with_severity(
                e,
                context="general",
                operation_label="prepare_database_runtime failed",
            )

            # E2E / Web 模式：失败立即退出，避免无头浏览器/UI 多 session 场景复杂化
            if os.environ.get("E2E_TESTING") == "true" or os.environ.get("FLET_FORCE_WEB_SERVER", "").lower() in (
                "true",
                "1",
                "yes",
            ):
                sys.exit(1)

            from ui.startup_views import PreInitErrorView
            from utils.sanitizers import DataSanitizer

            error_message = DataSanitizer.sanitize_error(e)
            log_dir_hint = _resolve_embedded_pg_log_dir_hint()
            retry_event = threading.Event()
            exit_event = threading.Event()

            def on_retry(_e: ft.ControlEvent, _ev: threading.Event = retry_event) -> None:
                _ev.set()

            def on_exit(_e: ft.ControlEvent, _ev: threading.Event = exit_event) -> None:
                _ev.set()

            page.render(
                PreInitErrorView,
                error_message=error_message,
                on_retry=on_retry,
                on_exit=on_exit,
                failure_count=failure_count,
                log_dir_hint=log_dir_hint,
            )

            action = await _wait_for_user_action(retry_event, exit_event)

            if action == "exit":
                logger.info("[Main] User chose to exit from PreInitErrorView")
                sys.exit(0)

            # Retry 路径：先渲染 LoadingView 让用户立即看到"正在重试"反馈，
            # 再进入退避等待（避免退避期间仍显示错误页让用户误以为卡死）
            from ui.startup_views import LoadingView

            # P2-1: 指数退避（2^(n-1)，上限 30s），给瞬时故障恢复时间
            # P2-4: 退避期间显示倒计时 + Exit 按钮，用户可中断等待
            backoff = min(2 ** (failure_count - 1), 30)
            logger.info("[Main] Retrying prepare_database_runtime after %ss backoff", backoff)

            backoff_exit_event = threading.Event()

            def on_backoff_exit(_e: ft.ControlEvent, _ev: threading.Event = backoff_exit_event) -> None:
                _ev.set()

            page.render(
                LoadingView,
                scenario=scenario,
                retry_backoff_seconds=backoff,
                failure_count=failure_count,
                on_exit=on_backoff_exit,
            )
            await asyncio.sleep(0.05)  # 让 Flet 刷新一帧

            # P2-4: 可中断的退避等待
            # - exit_event 被 set → 用户点 Exit → sys.exit(0)
            # - 超时 → 继续重试
            # - CancelledError → 正确传播（R2 红线）
            action = await _wait_for_event_or_timeout(backoff_exit_event, backoff)
            if action == "event":
                logger.info("[Main] User chose to exit during backoff wait")
                sys.exit(0)


def _resolve_embedded_pg_log_dir_hint() -> str | None:
    """P2-1: 解析 embedded PG 日志目录路径，供 PreInitErrorView 诊断提示。

    优先级：
    1. ``AppConfig.embedded_pg_log_dir``（用户显式配置）
    2. ``<platformdirs.user_data_dir>/postgres-logs``（embedded PG 默认日志目录）

    解析失败时返回 ``None``（PreInitErrorView 不显示日志路径）。
    """
    try:
        from pathlib import Path

        from utils.config_handler import ConfigHandler
        from utils.config_models import AppConfig

        config = AppConfig.model_validate(ConfigHandler.load_config())
        if config.embedded_pg_log_dir:
            return config.embedded_pg_log_dir
        import platformdirs

        return str(Path(platformdirs.user_data_dir("qTrading")) / "postgres-logs")
    except Exception as e:
        logger.warning("[Main] failed to resolve embedded PG log dir hint: %s", e, exc_info=True)
        return None


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
    _trace_log("[RootView] constructing StartupView + ToastManagerView")
    stack = ft.Stack(
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
    _trace_log("[RootView] construction complete, returning Stack")
    return stack


async def main(page: ft.Page):
    setup_logging()
    _trace_log("[Main] main(page) entered")

    from utils.correlation import ensure_correlation_id

    ensure_correlation_id()

    try:
        loop = asyncio.get_running_loop()
        install_asyncio_handler_for_loop(loop)
    except RuntimeError:
        pass

    ConfigHandler.ensure_defaults()

    # UX 改进 spec §启动侧方案 A：embedded 模式下提前渲染 LoadingView
    # 必须先初始化 i18n / 窗口几何 / 主题 / toast，否则 page.render(LoadingView) 无文案可显示。
    # external 模式下 prepare_database_runtime() 立即返回 None，提前这几步对用户无感知。
    I18n.initialize(ConfigHandler.get_locale())
    page.locale_configuration = build_locale_configuration(I18n.current_locale())
    page.title = I18n.get("app_title")
    page.window.icon = "icon.png"

    is_web_mode = os.environ.get("FLET_FORCE_WEB_SERVER", "").lower() in ("true", "1", "yes")
    await setup_window_geometry(page, is_web_mode=is_web_mode)
    page.padding = 0
    apply_page_theme(page)
    page.toast = ToastManager(page)  # type: ignore[attr-defined]  # [reason: 动态挂载 ToastManager 到 Page 实例，ft.Page 类型存根无 toast 属性]

    # 启动场景检测（external 模式或未启用 embedded PG 时返回 None，跳过 LoadingView 提前渲染）
    from app.bootstrap import detect_embedded_pg_startup_scenario
    from utils.config_models import AppConfig

    config_for_detect = AppConfig.model_validate(ConfigHandler.load_config())
    try:
        scenario = detect_embedded_pg_startup_scenario(config_for_detect)
    except Exception as e:
        # detect 为 UX 增强函数，失败不应阻塞启动；降级为 UNKNOWN 让 LoadingView 仍渲染
        # （detect 抛异常仅在 embedded 模式下，external 模式第一行即返回 None 不抛异常）
        logger.warning("[Main] detect_embedded_pg_startup_scenario failed, fallback to UNKNOWN: %s", e, exc_info=True)
        scenario = EmbeddedPgStartupScenario.UNKNOWN

    # embedded 模式：先渲染 LoadingView 一帧让用户看到反馈，再进入 prepare_database_runtime 阻塞等待
    if scenario is not None:
        from ui.startup_views import LoadingView

        page.render(LoadingView, scenario=scenario)
        await asyncio.sleep(0.05)  # 让 Flet 刷新一帧（spec SubTask 3.3）

    # Phase 2 §3.4：embedded 模式下启动 sidecar 并返回 URL（D15：不持久化到 config）
    # P0-1: prepare_database_runtime 失败不直接 sys.exit，显示 PreInitErrorView 供用户 Retry/Exit
    embedded_db_url = await _prepare_db_with_retry(page, scenario)

    ProxyManager.apply_smart_proxy_policy()

    # D15（pg-plan §22）：embedded 模式下永久设置 config.DB_URL（运行时变量，不持久化到
    # config 文件，不设 DATABASE_URL 环境变量避免污染子进程）。
    # ConfigHandler.get_db_url() Priority 3 兜底返回 embedded URL。
    #
    # P2-5: embedded 成功时通过 ContextVar(Priority 0) 强制 URL，覆盖
    # DATABASE_URL env var(Priority 1) 与 onboard 后 db_host(Priority 2)。
    # 典型 bug 场景：用户 shell profile 残留旧 DATABASE_URL 指向废弃主机，
    # 导致 CacheManager 连错库、初始化 DB 报错。用 ContextVar(Priority 0)
    # 保证即使 env var 存在，embedded URL 也胜出。不做 try/finally reset：
    # ContextVar 是 per-asyncio-task 的，main(page) task 销毁时上下文即 GC；
    # Flet Web 多 session 间各有独立 task context，不会互相污染。
    if embedded_db_url:
        import config

        config.DB_URL = embedded_db_url

        ConfigHandler._db_url_override.set(embedded_db_url)
        logger.info(
            "[Main] Embedded DB URL ContextVar override active; "
            "DATABASE_URL env var and persisted db_host will be ignored"
        )
    cache_manager = CacheManager()

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
        embedded_pg_scenario=scenario,
    )

    logger.info("[Main] Before page.render(RootView)")
    _trace_log("[Main] Before page.render(RootView)")
    try:
        page.render(
            RootView,
            controller=controller,
            bridge=bridge,
            run_task_fn=page.run_task,
        )
    except Exception as render_exc:
        _trace_log(f"[Main] page.render raised: {type(render_exc).__name__}: {render_exc}")
        logger.exception("[Main] page.render(RootView) raised exception")
        raise
    _trace_log("[Main] After page.render(RootView)")
    logger.info("[Main] After page.render(RootView)")

    logger.info("[Main] Before ConfigHandler calls")
    db_url = ConfigHandler.get_db_url()
    token = ConfigHandler.get_token()
    llm_api_key = ConfigHandler.get_llm_config().get("api_key")
    onboarding_complete = ConfigHandler.is_onboarding_complete()
    logger.info("[Main] After ConfigHandler calls")

    masked_token = mask_sensitive(token)
    masked_llm_key = mask_sensitive(llm_api_key)
    logger.info(
        "DB_URL configured: %s, Token='%s', API_Key='%s', Onboarding='%s'",
        bool(db_url),
        masked_token,
        masked_llm_key,
        onboarding_complete,
    )

    logger.info("[Main] Before controller.start()")
    _trace_log("[Main] Before controller.start()")
    await controller.start(db_url, token, llm_api_key, onboarding_complete)
    _trace_log("[Main] After controller.start()")
    logger.info("[Main] After controller.start()")

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
        # E2E 强制 CanvasKit：Flet 0.86.x 默认 skwasm 在 headless Windows CI 上
        # 渲染管线卡死（字体测量 GPU stall 后无 frame 产出），main 分支一直用
        # CanvasKit 且 E2E 稳定通过。被 3cff3ab1 调试改动误删，现恢复。
        run_kwargs["web_renderer"] = ft.WebRenderer.CANVAS_KIT
    ft.run(**run_kwargs)
