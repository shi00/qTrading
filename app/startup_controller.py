"""Startup flow state machine. Zero Flet dependency, fully unit-testable."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, auto

from app.bootstrap import check_onboarding_needed, initialize_services

logger = logging.getLogger(__name__)


class StartupState(Enum):
    LOADING = auto()
    NEED_UPGRADE = auto()
    UPGRADE_IN_PROGRESS = auto()
    UPGRADE_SUCCESS = auto()
    UPGRADE_FAILED = auto()
    INIT_FAILED = auto()
    NEED_ONBOARDING = auto()
    READY = auto()


@dataclass
class StartupContext:
    """Extra context passed alongside state transitions."""

    error: str | None = None
    detail: str | None = None
    current_rev: str | None = None
    head_rev: str | None = None


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
    ):
        self._cache_manager = cache_manager
        self._on_state_change = on_state_change
        self._on_show_toast = on_show_toast
        self._on_exit = on_exit
        self._state = StartupState.LOADING
        self._context = StartupContext()

    @property
    def state(self) -> StartupState:
        return self._state

    @property
    def context(self) -> StartupContext:
        return self._context

    def _transition(self, new_state: StartupState, **context_kwargs):
        self._state = new_state
        self._context = StartupContext(**context_kwargs)
        self._on_state_change(new_state, self._context)

    # --- Entry point ---

    async def start(self, db_url, token, llm_api_key, onboarding_complete):
        """Determine if onboarding is needed, then init services."""
        if check_onboarding_needed(db_url, token, llm_api_key, onboarding_complete):
            self._transition(StartupState.NEED_ONBOARDING)
            return
        await self._init_services()

    # --- Core: call initialize_services and branch on result ---

    async def _init_services(self):
        self._transition(StartupState.LOADING)
        try:
            result = await initialize_services(self._cache_manager, show_toast_fn=self._on_show_toast)
        except Exception as e:
            logger.error("[Startup] initialize_services raised exception: %s", e, exc_info=True)
            self._transition(StartupState.INIT_FAILED, error="init_exception", detail=str(e))
            return

        if result["success"]:
            from utils.thread_pool import TaskType, ThreadPoolManager
            from utils.config_handler import ConfigHandler

            await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, True)
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
        from utils.thread_pool import TaskType, ThreadPoolManager
        from utils.config_handler import ConfigHandler

        await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_onboarding_complete, False)
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
            logger.error("[Startup] DB upgrade failed: %s", e, exc_info=True)
            self._transition(StartupState.UPGRADE_FAILED, error="db_upgrade_failed", detail=str(e))

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
