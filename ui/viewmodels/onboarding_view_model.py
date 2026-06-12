"""OnboardingViewModel — MVVM-003 fix.

Extracts business logic from OnboardingWizard into a pure ViewModel.
Holds step navigation, validation, sync state, and config persistence.
No Flet control references.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from data.data_processor import DataProcessor
from core.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.correlation import ensure_correlation_id
from utils.error_classifier import classify_error, get_error_message

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


class OnboardingViewModel:
    """ViewModel for OnboardingWizard — MVVM-003 fix.

    Panel operations are injected as Callable via bind() to avoid
    ViewModel depending on Flet controls.
    """

    def __init__(self, data_processor: DataProcessor | None = None):
        self._data_processor = data_processor

        # --- Step navigation state ---
        self.current_step: int = 0
        self.step_validated: dict[str, bool] = {}

        # --- Sync state ---
        self.sync_in_progress: bool = False
        self.validation_in_progress: bool = False

        # --- Panel operation callbacks (View injects) ---
        self.fn_validate_database: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_token: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_cloud_ai: Callable[[], Awaitable[bool]] | None = None
        self.fn_validate_local_model: Callable[[], Awaitable[bool]] | None = None

        # --- Push schedule state from View controls into VM ---
        self.fn_push_schedule_state: Callable[[], None] | None = None

        # --- View notification callbacks ---
        self.on_step_changed: Callable[[], None] | None = None
        self.on_sync_progress: Callable[[float, str], None] | None = None
        self.on_sync_state_changed: Callable[[], None] | None = None
        self.on_validation_state_changed: Callable[[], None] | None = None
        self.on_complete: Callable[[], Awaitable[None]] | None = None
        self.on_schedule_time_normalized: Callable[[str], None] | None = None

        # --- Schedule state (pushed from View) ---
        self._schedule_enabled: bool = True
        self._schedule_time: str = "16:30"

    @property
    def data_processor(self) -> DataProcessor:
        if self._data_processor is None:
            self._data_processor = DataProcessor()
        return self._data_processor

    @property
    def normalized_schedule_time(self) -> str:
        return self._schedule_time

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
        on_step_changed: Callable[[], None],
        on_sync_progress: Callable[[float, str], None],
        on_sync_state_changed: Callable[[], None],
        on_validation_state_changed: Callable[[], None],
        on_complete: Callable[[], Awaitable[None]],
        on_schedule_time_normalized: Callable[[str], None],
    ):
        self.fn_validate_database = fn_validate_database
        self.fn_validate_token = fn_validate_token
        self.fn_validate_cloud_ai = fn_validate_cloud_ai
        self.fn_validate_local_model = fn_validate_local_model
        self.fn_push_schedule_state = fn_push_schedule_state
        self.on_step_changed = on_step_changed
        self.on_sync_progress = on_sync_progress
        self.on_sync_state_changed = on_sync_state_changed
        self.on_validation_state_changed = on_validation_state_changed
        self.on_complete = on_complete
        self.on_schedule_time_normalized = on_schedule_time_normalized

    def dispose(self):
        if self.sync_in_progress:
            logger.warning(
                "[OnboardingVM] dispose() called while sync in progress; callbacks cleared, DataProcessor will self-complete"
            )
        self.fn_validate_database = None
        self.fn_validate_token = None
        self.fn_validate_cloud_ai = None
        self.fn_validate_local_model = None
        self.fn_push_schedule_state = None
        self.on_step_changed = None
        self.on_sync_progress = None
        self.on_sync_state_changed = None
        self.on_validation_state_changed = None
        self.on_complete = None
        self.on_schedule_time_normalized = None

    # ------------------------------------------------------------------
    # Step Navigation
    # ------------------------------------------------------------------

    def invalidate_step(self, step_id: str):
        self.step_validated[step_id] = False

    async def next_step(self):
        config = STEP_CONFIGS[self.current_step]

        if config.validate_before_next:
            if not await self.validate_and_persist_current_step():
                return

        if config.id == "complete":
            if self.on_complete:
                await self.on_complete()
            return

        if self.current_step < len(STEP_CONFIGS) - 1:
            self.current_step += 1
            if self.on_step_changed:
                self.on_step_changed()

    async def prev_step(self):
        config = STEP_CONFIGS[self.current_step]
        if config.validate_before_next:
            self.step_validated[config.id] = False

        if self.current_step > 0:
            self.current_step -= 1
            if self.on_step_changed:
                self.on_step_changed()

    async def skip_step(self):
        if self.current_step < len(STEP_CONFIGS) - 1:
            self.current_step += 1
            if self.on_step_changed:
                self.on_step_changed()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def validate_and_persist_current_step(self) -> bool:
        ensure_correlation_id()
        config = STEP_CONFIGS[self.current_step]

        if self.step_validated.get(config.id, False):
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
                logger.warning(f"[OnboardingVM] Validator for '{config.id}' not bound, blocking step")
                return False
            return True

        self._set_validation_in_progress(True)
        try:
            result = await validator()
            if result:
                self.step_validated[config.id] = True
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
        if self.on_schedule_time_normalized:
            self.on_schedule_time_normalized(time_str)

        ConfigHandler.save_config(
            {
                "auto_update_enabled": enabled,
                "auto_update_time": time_str,
            }
        )
        return True

    def set_schedule_state(self, enabled: bool, time_str: str):
        self._schedule_enabled = enabled
        self._schedule_time = time_str

    def _set_validation_in_progress(self, in_progress: bool):
        self.validation_in_progress = in_progress
        if self.on_validation_state_changed:
            self.on_validation_state_changed()

    # ------------------------------------------------------------------
    # Data Sync
    # ------------------------------------------------------------------

    async def start_sync(self, quick: bool = False):
        ensure_correlation_id()
        self.sync_in_progress = True
        if self.on_sync_state_changed:
            self.on_sync_state_changed()

        if self.on_sync_progress:
            self.on_sync_progress(0, I18n.get("wizard_status_init"))

        try:

            def progress_callback(current, total, message):
                if self.on_sync_progress:
                    self.on_sync_progress(current / 100, message)

            result = await self.data_processor.initialize_system(
                progress_callback=progress_callback,
                quick=quick,
            )

            if result:
                if self.on_sync_progress:
                    self.on_sync_progress(1.0, I18n.get("wizard_status_done"))
                await asyncio.sleep(1)
                await self.next_step()
            else:
                if self.on_sync_progress:
                    self.on_sync_progress(0, I18n.get("wizard_status_cancelled"))

        except asyncio.CancelledError:
            logger.warning("[OnboardingVM] Sync cancelled during shutdown.")
            raise
        except Exception as e:
            error_info = classify_error(e, context="general")
            if self.on_sync_progress:
                self.on_sync_progress(0, get_error_message(error_info))
        finally:
            self.sync_in_progress = False
            if self.on_sync_state_changed:
                self.on_sync_state_changed()

    async def cancel_sync(self):
        try:
            if self._data_processor:
                await self._data_processor.stop()
            if self.on_sync_progress:
                self.on_sync_progress(0, I18n.get("wizard_status_cancelled"))
        except Exception as e:
            logger.warning(f"[OnboardingVM] Failed to cancel sync: {e}")
        finally:
            self.sync_in_progress = False
            if self.on_sync_state_changed:
                self.on_sync_state_changed()

    async def skip_sync(self):
        if self.on_sync_progress:
            self.on_sync_progress(0, I18n.get("wizard_status_skip"))
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
        return await manager.load_model(model_path, config)
