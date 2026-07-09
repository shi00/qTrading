"""OnboardingViewModel — MVVM-003 fix.

Extracts business logic from OnboardingWizard into a pure ViewModel.
Holds step navigation, validation, sync state, and config persistence.
No Flet control references.

Phase 2 改造: frozen dataclass state snapshot + subscribe/_notify。
保留 fn_* (View→VM 函数注入) 和 on_complete (异步完成回调)；
移除 on_step_changed / on_sync_progress / on_sync_state_changed /
on_validation_state_changed / on_schedule_time_normalized (替换为 state 字段)。
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from utils.config_handler import ConfigHandler
from utils.correlation import ensure_correlation_id
from utils.error_classifier import classify_error, get_error_message
from utils.thread_pool import TaskType, ThreadPoolManager
from data.data_processor import DataProcessor
from ui.viewmodels import Message

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Step config (pure data, shared with View)
# ------------------------------------------------------------------


@dataclass
class StepConfig:
    id: str
    name: str
    show_prev: bool
    show_next: bool
    next_text_key: str
    next_icon: str  # Must match an attribute name on ft.Icons (e.g. "ARROW_FORWARD")
    show_skip: bool = False
    skip_text_key: str = ""
    required: bool = False
    validate_before_next: bool = False
    block_on_missing_validator: bool = True


STEP_CONFIGS = [
    StepConfig(
        id="welcome",
        name="wizard_step_welcome",
        show_prev=False,
        show_next=True,
        next_text_key="wizard_btn_start",
        next_icon="ARROW_FORWARD",
        required=False,
        validate_before_next=False,
        block_on_missing_validator=False,
    ),
    StepConfig(
        id="database",
        name="wizard_step_database",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon="ARROW_FORWARD",
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="token",
        name="wizard_step_token",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon="ARROW_FORWARD",
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="cloud_ai",
        name="wizard_step_cloud_ai",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon="ARROW_FORWARD",
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="local_model",
        name="wizard_step_local_model",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon="ARROW_FORWARD",
        show_skip=True,
        skip_text_key="wizard_btn_skip",
        required=False,
        validate_before_next=True,
        block_on_missing_validator=False,
    ),
    StepConfig(
        id="data_sync",
        name="wizard_step_data_sync",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_next",
        next_icon="ARROW_FORWARD",
        required=False,
        validate_before_next=False,
        block_on_missing_validator=False,
    ),
    StepConfig(
        id="schedule",
        name="wizard_step_schedule",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_finish",
        next_icon="CHECK_CIRCLE",
        required=False,
        validate_before_next=True,
    ),
    StepConfig(
        id="complete",
        name="wizard_step_complete",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_start",
        next_icon="ROCKET_LAUNCH",
        required=False,
        validate_before_next=False,
        block_on_missing_validator=False,
    ),
]


# ------------------------------------------------------------------
# State snapshot (frozen dataclass)
# ------------------------------------------------------------------


@dataclass(frozen=True)
class OnboardingState:
    """OnboardingViewModel 的不可变状态快照。View 通过 subscribe 接收。"""

    current_step: int = 0
    sync_in_progress: bool = False
    validation_in_progress: bool = False
    sync_progress: float = 0.0
    sync_progress_message: Message | None = None
    schedule_enabled: bool = True
    schedule_time: str = "16:30"
    normalized_schedule_time: str = "16:30"


class OnboardingViewModel:
    """ViewModel for OnboardingWizard — MVVM-003 fix.

    Panel operations are injected as Callable via bind() to avoid
    ViewModel depending on Flet controls.
    """

    def __init__(self, data_processor: DataProcessor | None = None):
        self._data_processor = data_processor

        # --- Internal mutable state (not exposed via state snapshot) ---
        self._step_validated: dict[str, bool] = {}
        self._schedule_enabled: bool = True
        self._schedule_time: str = "16:30"

        # --- Panel operation callbacks (View injects, View→VM function) ---
        self.fn_validate_database: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_token: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_cloud_ai: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_local_model: Callable[[], Awaitable[bool]] | None = None
        self.fn_push_schedule_state: Callable[[], None] | None = None

        # --- Async completion callback (special: not a simple state notification) ---
        self.on_complete: Callable[[], Awaitable[None]] | None = None

        # --- State snapshot + subscribers ---
        self._state: OnboardingState = OnboardingState()
        self._subscribers: list[Callable[[OnboardingState], None]] = []

    # ------------------------------------------------------------------
    # State / subscribe / notify
    # ------------------------------------------------------------------

    @property
    def state(self) -> OnboardingState:
        return self._state

    def subscribe(self, callback: Callable[[OnboardingState], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

        return _unsubscribe

    def _notify(self) -> None:
        snapshot = self._state
        for cb in list(self._subscribers):
            cb(snapshot)

    def _set_state(self, **changes) -> None:
        self._state = replace(self._state, **changes)
        self._notify()

    # ------------------------------------------------------------------
    # Properties (compat with existing View tests, Phase 4 will remove setters)
    # ------------------------------------------------------------------

    @property
    def data_processor(self) -> DataProcessor:
        if self._data_processor is None:
            self._data_processor = DataProcessor()
        return self._data_processor

    @property
    def current_step(self) -> int:
        return self._state.current_step

    @current_step.setter
    def current_step(self, value: int) -> None:
        self._set_state(current_step=value)

    @property
    def sync_in_progress(self) -> bool:
        return self._state.sync_in_progress

    @sync_in_progress.setter
    def sync_in_progress(self, value: bool) -> None:
        self._set_state(sync_in_progress=value)

    @property
    def validation_in_progress(self) -> bool:
        return self._state.validation_in_progress

    @validation_in_progress.setter
    def validation_in_progress(self, value: bool) -> None:
        self._set_state(validation_in_progress=value)

    @property
    def step_validated(self) -> dict[str, bool]:
        return self._step_validated

    @property
    def normalized_schedule_time(self) -> str:
        return self._state.normalized_schedule_time

    # ------------------------------------------------------------------
    # bind / dispose
    # ------------------------------------------------------------------

    def bind(
        self,
        *,
        fn_validate_database: Callable[[], Awaitable[bool]],
        fn_validate_token: Callable[[], Awaitable[bool]],
        fn_validate_cloud_ai: Callable[[], Awaitable[bool]],
        fn_validate_local_model: Callable[[], Awaitable[bool]],
        fn_push_schedule_state: Callable[[], None],
        on_complete: Callable[[], Awaitable[None]],
    ):
        self.fn_validate_database = fn_validate_database
        self.fn_validate_token = fn_validate_token
        self.fn_validate_cloud_ai = fn_validate_cloud_ai
        self.fn_validate_local_model = fn_validate_local_model
        self.fn_push_schedule_state = fn_push_schedule_state
        self.on_complete = on_complete

    def dispose(self):
        if self._state.sync_in_progress:
            logger.warning(
                "[OnboardingVM] dispose() called while sync in progress; callbacks cleared, DataProcessor will self-complete"
            )
        self.fn_validate_database = None
        self.fn_validate_token = None
        self.fn_validate_cloud_ai = None
        self.fn_validate_local_model = None
        self.fn_push_schedule_state = None
        self.on_complete = None
        self._subscribers.clear()
        self._state = OnboardingState()

    # ------------------------------------------------------------------
    # Step Navigation
    # ------------------------------------------------------------------

    def invalidate_step(self, step_id: str):
        self._step_validated[step_id] = False

    async def next_step(self):
        config = STEP_CONFIGS[self._state.current_step]

        if config.validate_before_next:
            if not await self.validate_and_persist_current_step():
                return

        if config.id == "complete":
            if self.on_complete:
                await self.on_complete()
            return

        if self._state.current_step < len(STEP_CONFIGS) - 1:
            self.current_step = self._state.current_step + 1

    async def prev_step(self):
        config = STEP_CONFIGS[self._state.current_step]
        if config.validate_before_next:
            self._step_validated[config.id] = False

        if self._state.current_step > 0:
            self.current_step = self._state.current_step - 1

    async def skip_step(self):
        if self._state.current_step < len(STEP_CONFIGS) - 1:
            self.current_step = self._state.current_step + 1

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_and_persist_current_step(self) -> bool:
        ensure_correlation_id()
        config = STEP_CONFIGS[self._state.current_step]

        if self._step_validated.get(config.id, False):
            return True

        validators: dict[str, Callable[[], Awaitable[bool]] | None] = {
            "database": self.fn_validate_database,
            "token": self.fn_validate_token,
            "cloud_ai": self.fn_validate_cloud_ai,
            "local_model": self.fn_validate_local_model,
            "schedule": self._validate_and_save_schedule,
        }

        validator = validators.get(config.id)
        if validator is None:
            if config.block_on_missing_validator:
                logger.warning("[OnboardingVM] Validator for '%s' not bound, blocking step", config.id)
                return False
            return True

        self._set_validation_in_progress(True)
        try:
            result = await validator()
            if result:
                self._step_validated[config.id] = True
            return result
        finally:
            self._set_validation_in_progress(False)

    async def _validate_and_save_schedule(self) -> bool:
        if self.fn_push_schedule_state:
            self.fn_push_schedule_state()

        time_str = self._schedule_time.strip()
        enabled = self._schedule_enabled

        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            time_str = "16:30"
        else:
            try:
                hours, minutes = map(int, time_str.split(":"))
                if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                    time_str = "16:30"
            except ValueError:
                time_str = "16:30"

        self._schedule_time = time_str
        self._set_state(schedule_time=time_str, normalized_schedule_time=time_str)

        try:
            await ThreadPoolManager().run_async(
                TaskType.IO,
                ConfigHandler.save_config,
                {
                    "auto_update_enabled": enabled,
                    "auto_update_time": time_str,
                },
            )
            return True
        except Exception as e:
            logger.error("[OnboardingVM] Save schedule failed: %s", e, exc_info=True)
            return False

    def set_schedule_state(self, enabled: bool, time_str: str):
        self._schedule_enabled = enabled
        self._schedule_time = time_str
        self._set_state(
            schedule_enabled=enabled,
            schedule_time=time_str,
            normalized_schedule_time=time_str,
        )

    def _set_validation_in_progress(self, in_progress: bool):
        self.validation_in_progress = in_progress

    # ------------------------------------------------------------------
    # Data Sync
    # ------------------------------------------------------------------

    async def start_sync(self, quick: bool = False):
        ensure_correlation_id()
        self._set_state(
            sync_in_progress=True,
            sync_progress=0.0,
            sync_progress_message=Message("wizard_status_init"),
        )

        try:

            def progress_callback(current, total, message):
                self._set_state(sync_progress=current / 100, sync_progress_message=Message(key=message))

            result = await self.data_processor.initialize_system(
                progress_callback=progress_callback,
                quick=quick,
            )

            if result:
                self._set_state(
                    sync_progress=1.0,
                    sync_progress_message=Message("wizard_status_done"),
                )
                await asyncio.sleep(1)
                await self.next_step()
            else:
                self._set_state(
                    sync_progress=0.0,
                    sync_progress_message=Message("wizard_status_cancelled"),
                )

        except asyncio.CancelledError:
            logger.warning("[OnboardingVM] Sync cancelled during shutdown.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            self._set_state(
                sync_progress=0.0,
                sync_progress_message=Message(key=get_error_message(error_info)),
            )
        finally:
            self._set_state(sync_in_progress=False)

    async def cancel_sync(self):
        try:
            if self._data_processor:
                await self._data_processor.stop()
            self._set_state(
                sync_progress=0.0,
                sync_progress_message=Message("wizard_status_cancelled"),
            )
        except Exception as e:
            logger.warning("[OnboardingVM] Failed to cancel sync: %s", e, exc_info=True)
        finally:
            self._set_state(sync_in_progress=False)

    async def skip_sync(self):
        self._set_state(
            sync_progress=0.0,
            sync_progress_message=Message("wizard_status_skip"),
        )
        await self.next_step()

    # ------------------------------------------------------------------
    # Service Delegation (static, for ConfigPanel callbacks)
    # ------------------------------------------------------------------

    @staticmethod
    async def test_llm_connection(
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        **kwargs,
    ) -> dict:
        from services.ai_service import AIService

        return await AIService.test_connection(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    @staticmethod
    async def verify_local_model(model_path: str, config: dict) -> bool:
        from services.local_model_manager import LocalModelManager

        manager = await LocalModelManager.get_instance()
        return await manager.load_model(model_path, config, is_verification=True)
