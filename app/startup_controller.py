"""Startup flow state machine. Zero Flet dependency, fully unit-testable."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from app.bootstrap import check_onboarding_needed, initialize_services
from core.startup_types import EmbeddedPgStartupScenario, StartupContext, StartupState
from utils.error_classifier import log_classified
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)


class StartupController:
    """
    Startup flow state machine.

    Holds the startup state and business logic (DB init, cache close,
    onboarding reset). Notifies a renderer via ``on_state_change`` callback.
    Zero Flet dependency — can be unit-tested without any UI mocks.
    """

    def __init__(
        self,
        cache_manager,
        on_state_change: Callable[[StartupState, StartupContext], None],
        on_show_toast: Callable[[str, str], None] | None = None,
        on_exit: Callable[[], None] | None = None,
        embedded_pg_scenario: EmbeddedPgStartupScenario | None = None,
    ):
        self._cache_manager = cache_manager
        self._on_state_change = on_state_change
        self._on_show_toast = on_show_toast
        self._on_exit = on_exit
        self._state = StartupState.LOADING
        # UX 改进 spec §启动侧方案 A：保留 embedded_pg_scenario 到初始 context，
        # 供 StartupView 的 LOADING 状态显示差异化文案。_transition 会跨状态保留此字段。
        self._context = StartupContext(embedded_pg_scenario=embedded_pg_scenario)
        # Phase 2A.1 Task 2A.1.9：保存 initialize_services 返回的 fire-and-forget
        # auto probe 任务，供 main.py 注册到 ShutdownCoordinator。
        self._auto_probe_task: asyncio.Task | None = None
        # review01-A9: per-session 服务初始化状态（替代 bootstrap 模块级 flag）。
        # StartupController 每次 run(page) 新建实例，此状态天然 per-session 隔离，
        # 避免 Flet Web 多 session 共享进程时模块级 flag 导致"引擎就绪、服务未启动"。
        self._services_initialized = False

    @property
    def state(self) -> StartupState:
        return self._state

    @property
    def context(self) -> StartupContext:
        return self._context

    @property
    def auto_probe_task(self) -> asyncio.Task | None:
        """Phase 2A.1 Task 2A.1.9：暴露给 main.py 以便注册到 ShutdownCoordinator。"""
        return self._auto_probe_task

    def _transition(self, new_state: StartupState, **context_kwargs):
        self._state = new_state
        # UX 改进 spec §启动侧方案 A：embedded_pg_scenario 跨状态转换保留
        # （由 __init__ 注入，controller 内部状态转换不重置此字段）
        if "embedded_pg_scenario" not in context_kwargs:
            context_kwargs["embedded_pg_scenario"] = self._context.embedded_pg_scenario
        self._context = StartupContext(**context_kwargs)
        self._on_state_change(new_state, self._context)

    # --- Entry point ---

    async def start(self, db_url, token, llm_api_key, onboarding_complete):
        """Determine if onboarding is needed, then init services."""
        logger.info("[Startup] start() called, checking onboarding...")
        if check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
            logger.info("[Startup] Onboarding needed, transitioning to NEED_ONBOARDING.")
            self._transition(StartupState.NEED_ONBOARDING)
            return
        logger.info("[Startup] Onboarding not needed, calling _init_services().")
        await self._init_services()

    # --- Core: call initialize_services and branch on result ---

    async def _init_services(self):
        self._transition(StartupState.LOADING)
        try:
            logger.info("[Startup] Calling initialize_services()...")
            result = await initialize_services(
                self._cache_manager,
                show_toast_fn=self._on_show_toast,
                services_initialized=self._services_initialized,
            )
            logger.info(
                "[Startup] initialize_services() returned: success=%s, error=%s",
                result.get("success"),
                result.get("error"),
            )
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Startup] initialize_services raised exception (%s): %s",
                exc_info=True,
            )
            self._transition(StartupState.INIT_FAILED, error="init_exception", detail=DataSanitizer.sanitize_error(e))
            return

        # Phase 2A.1 Task 2A.1.9：保存 auto_probe_task 以便 main.py 注册到 ShutdownCoordinator
        self._auto_probe_task = result.get("auto_probe_task")

        if result["success"]:
            # review01-A9: per-session 置位初始化状态（替代 bootstrap 模块级 flag）
            self._services_initialized = True
            from utils.thread_pool import TaskType, ThreadPoolManager
            from utils.config_handler import ConfigHandler

            # app-004 举一反三：set_onboarding_complete(True) 失败不阻塞启动
            # （服务已初始化成功，仅 onboarding_complete 标志未持久化），
            # 记录 warning 后仍 transition 到 READY，用户可正常使用。
            # 下次启动时可能重新走 onboarding 流程，但不影响当前会话。
            try:
                await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, True)
            except Exception as e:
                logger.warning(
                    "[Startup] set_onboarding_complete(True) failed after successful init, continuing to READY: %s",
                    DataSanitizer.sanitize_error(e),
                    exc_info=True,
                )
            self._transition(StartupState.READY)
            return

        error = result.get("error")
        detail = result.get("detail")
        current_rev = result.get("current_rev")
        head_rev = result.get("head_rev")

        if error == "db_upgrade_needed":
            self._transition(
                StartupState.NEED_UPGRADE,
                error=error,
                detail=detail,
                current_rev=current_rev,
                head_rev=head_rev,
            )
        elif error in ("db_init_failed", "db_engine_missing", "task_manager_init_failed"):
            self._transition(StartupState.INIT_FAILED, error=error, detail=detail)
        else:
            self._transition(StartupState.INIT_FAILED, error=error, detail=detail)

    # --- User actions (called by renderer button callbacks) ---

    async def retry(self):
        """User clicked Retry on the error page."""
        await self._init_services()

    async def reconfigure(self):
        """User clicked Reconfigure: close DB, reset onboarding, show wizard."""
        self._transition(StartupState.LOADING)
        await self._cache_manager.close()
        # review01-A9: per-session 重置初始化状态（替代 bootstrap 模块级 flag），
        # 允许用户完成 onboarding 后重新执行 initialize_services。
        self._services_initialized = False
        from utils.thread_pool import TaskType, ThreadPoolManager
        from utils.config_handler import ConfigHandler

        # app-004: set_onboarding_complete(False) 失败时 transition 到 INIT_FAILED，
        # 避免 state machine 停留 LOADING 导致用户看到无限 loading。
        try:
            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, False)
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Startup] set_onboarding_complete(False) failed during reconfigure (%s): %s",
                exc_info=True,
            )
            self._transition(
                StartupState.INIT_FAILED,
                error="reconfigure_failed",
                detail=DataSanitizer.sanitize_error(e),
            )
            return
        self._transition(StartupState.NEED_ONBOARDING)

    def skip(self):
        """User clicked Skip: enter main app without DB."""
        if self._on_show_toast:
            self._on_show_toast("warning_skip_db", "warning")
        self._transition(StartupState.READY)

    async def upgrade(self):
        """User clicked Upgrade: run DB migration."""
        self._transition(StartupState.UPGRADE_IN_PROGRESS)
        try:
            await self._cache_manager.init_db(force=True, auto_migrate=True)
            self._transition(StartupState.UPGRADE_SUCCESS)
        except Exception as e:
            log_classified(
                logger,
                e,
                "general",
                "[Startup] DB upgrade failed (%s): %s",
                exc_info=True,
            )
            self._transition(
                StartupState.UPGRADE_FAILED, error="db_upgrade_failed", detail=DataSanitizer.sanitize_error(e)
            )

    async def proceed_after_upgrade_success(self):
        """User acknowledged upgrade success dialog: re-init services."""
        await self._init_services()

    async def upgrade_retry(self):
        """User clicked Retry on upgrade failure dialog."""
        await self.upgrade()

    def upgrade_exit(self):
        """User clicked Exit on upgrade failure dialog."""
        if self._on_exit:
            self._on_exit()

    async def onboarding_complete(self):
        """Onboarding wizard finished: init services."""
        await self._init_services()
