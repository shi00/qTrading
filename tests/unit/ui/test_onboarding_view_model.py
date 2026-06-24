"""Unit tests for OnboardingViewModel — MVVM-003 fix.

TDD RED phase: these tests define the expected ViewModel contract.
"""

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.unit.ui.conftest import set_page
from ui.viewmodels.onboarding_view_model import (
    OnboardingViewModel,
    STEP_CONFIGS,
    StepConfig,
)

pytestmark = pytest.mark.unit

# --- I18n mock (autouse, returns key as value) ---


@pytest.fixture(autouse=True)
def _mock_i18n():
    with patch("ui.viewmodels.onboarding_view_model.I18n") as mock_i18n:
        mock_i18n.get = MagicMock(side_effect=lambda key, **kwargs: key)
        yield mock_i18n


# --- Fixtures ---


@pytest.fixture
def vm():
    """Vanilla ViewModel with no deps injected."""
    return OnboardingViewModel()


@pytest.fixture
def bound_vm(vm):
    """ViewModel with all callbacks bound to MagicMocks or AsyncMocks."""
    vm.bind(
        fn_validate_database=AsyncMock(return_value=True),
        fn_validate_token=AsyncMock(return_value=True),
        fn_validate_cloud_ai=AsyncMock(return_value=True),
        fn_validate_local_model=AsyncMock(return_value=True),
        fn_push_schedule_state=MagicMock(),
        on_step_changed=MagicMock(),
        on_sync_progress=MagicMock(),
        on_sync_state_changed=MagicMock(),
        on_validation_state_changed=MagicMock(),
        on_complete=AsyncMock(),
        on_schedule_time_normalized=MagicMock(),
    )
    return vm


@pytest.fixture
def mock_data_processor():
    with patch("ui.viewmodels.onboarding_view_model.DataProcessor") as cls:
        instance = MagicMock()
        instance.initialize_system = AsyncMock(return_value={"success": True})
        instance.stop = AsyncMock()
        cls.return_value = instance
        yield instance


@pytest.fixture
def mock_config_handler():
    with patch("ui.viewmodels.onboarding_view_model.ConfigHandler") as cls:
        cls.save_config = MagicMock()
        yield cls


# =====================================================================
# Test: Init
# =====================================================================


class TestOnboardingVMInit:
    def test_default_initial_step(self, vm):
        assert vm.current_step == 0

    def test_default_step_validated_empty(self, vm):
        assert vm.step_validated == {}

    def test_default_sync_in_progress_false(self, vm):
        assert vm.sync_in_progress is False

    def test_default_validation_in_progress_false(self, vm):
        assert vm.validation_in_progress is False

    def test_data_processor_lazy_init(self, vm):
        assert vm._data_processor is None
        dp = vm.data_processor
        assert dp is not None
        assert vm._data_processor is not None

    def test_constructor_injection(self, mock_data_processor):
        vm = OnboardingViewModel(data_processor=mock_data_processor)
        assert vm._data_processor is mock_data_processor
        assert vm.data_processor is mock_data_processor

    def test_default_schedule_state(self, vm):
        assert vm._schedule_enabled is True
        assert vm._schedule_time == "16:30"

    def test_normalized_schedule_time_property(self, vm):
        assert vm.normalized_schedule_time == "16:30"


# =====================================================================
# Test: bind / dispose
# =====================================================================


class TestOnboardingVMBind:
    def test_bind_stores_callbacks(self, vm):
        cb = MagicMock()
        cb_async = AsyncMock()
        vm.bind(
            fn_validate_database=cb_async,
            fn_validate_token=cb_async,
            fn_validate_cloud_ai=cb_async,
            fn_validate_local_model=cb_async,
            fn_push_schedule_state=cb,
            on_step_changed=cb,
            on_sync_progress=cb,
            on_sync_state_changed=cb,
            on_validation_state_changed=cb,
            on_complete=cb_async,
            on_schedule_time_normalized=cb,
        )
        assert vm.fn_validate_database is cb_async
        assert vm.on_step_changed is cb
        assert vm.on_complete is cb_async

    def test_dispose_clears_callbacks(self, bound_vm):
        bound_vm.dispose()
        assert bound_vm.fn_validate_database is None
        assert bound_vm.fn_validate_token is None
        assert bound_vm.on_step_changed is None
        assert bound_vm.on_complete is None
        assert bound_vm.on_schedule_time_normalized is None


# =====================================================================
# Test: Step Navigation
# =====================================================================


class TestOnboardingVMNavigation:
    async def test_next_step_advances(self, bound_vm):
        await bound_vm.next_step()
        assert bound_vm.current_step == 1
        bound_vm.on_step_changed.assert_called_once()

    async def test_next_step_validates_required(self, bound_vm):
        bound_vm.current_step = 1  # database step
        bound_vm.validate_and_persist_current_step = AsyncMock(return_value=True)
        await bound_vm.next_step()
        bound_vm.validate_and_persist_current_step.assert_awaited_once()

    async def test_next_step_blocks_on_validation_failure(self, bound_vm):
        bound_vm.current_step = 1
        bound_vm.validate_and_persist_current_step = AsyncMock(return_value=False)
        await bound_vm.next_step()
        assert bound_vm.current_step == 1
        bound_vm.on_step_changed.assert_not_called()

    async def test_next_step_on_complete_calls_callback(self, bound_vm):
        bound_vm.current_step = 7  # complete step
        await bound_vm.next_step()
        bound_vm.on_complete.assert_awaited_once()

    async def test_next_step_does_not_exceed_max(self, bound_vm):
        bound_vm.current_step = 7
        # on_complete returns, doesn't advance
        await bound_vm.next_step()
        assert bound_vm.current_step == 7

    async def test_prev_step_goes_back(self, bound_vm):
        bound_vm.current_step = 3
        await bound_vm.prev_step()
        assert bound_vm.current_step == 2
        bound_vm.on_step_changed.assert_called_once()

    async def test_prev_step_does_not_go_below_zero(self, bound_vm):
        bound_vm.current_step = 0
        await bound_vm.prev_step()
        assert bound_vm.current_step == 0
        bound_vm.on_step_changed.assert_not_called()

    async def test_prev_step_resets_validation(self, bound_vm):
        bound_vm.current_step = 1  # database step validates_before_next
        bound_vm.step_validated["database"] = True
        await bound_vm.prev_step()
        assert bound_vm.step_validated["database"] is False

    async def test_skip_step_advances(self, bound_vm):
        bound_vm.current_step = 4  # local_model step
        await bound_vm.skip_step()
        assert bound_vm.current_step == 5
        bound_vm.on_step_changed.assert_called_once()

    async def test_skip_step_does_not_exceed_max(self, bound_vm):
        bound_vm.current_step = 7
        await bound_vm.skip_step()
        assert bound_vm.current_step == 7

    def test_invalidate_step(self, bound_vm):
        bound_vm.step_validated["database"] = True
        bound_vm.invalidate_step("database")
        assert bound_vm.step_validated["database"] is False


# =====================================================================
# Test: Validation
# =====================================================================


class TestOnboardingVMValidation:
    async def test_validate_skips_if_already_validated(self, bound_vm):
        bound_vm.current_step = 1
        bound_vm.step_validated["database"] = True
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_database.assert_not_awaited()

    async def test_validate_returns_true_for_no_validator(self, bound_vm):
        bound_vm.current_step = 0  # welcome step
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True

    async def test_validate_calls_database_fn(self, bound_vm):
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_database.assert_awaited_once()
        assert bound_vm.step_validated["database"] is True

    async def test_validate_calls_token_fn(self, bound_vm):
        bound_vm.current_step = 2
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_token.assert_awaited_once()
        assert bound_vm.step_validated["token"] is True

    async def test_validate_calls_cloud_ai_fn(self, bound_vm):
        bound_vm.current_step = 3
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_cloud_ai.assert_awaited_once()
        assert bound_vm.step_validated["cloud_ai"] is True

    async def test_validate_calls_local_model_fn(self, bound_vm):
        bound_vm.current_step = 4
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        bound_vm.fn_validate_local_model.assert_awaited_once()
        assert bound_vm.step_validated["local_model"] is True

    async def test_validate_database_failure(self, bound_vm):
        bound_vm.fn_validate_database = AsyncMock(return_value=False)
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is False
        assert bound_vm.step_validated.get("database") is not True

    async def test_validation_sets_validation_in_progress(self, bound_vm):
        bound_vm.current_step = 1
        await bound_vm.validate_and_persist_current_step()
        assert bound_vm.validation_in_progress is False  # finally resets
        bound_vm.on_validation_state_changed.assert_any_call()
        # Called twice: True then False
        assert bound_vm.on_validation_state_changed.call_count == 2

    async def test_validation_clears_progress_on_exception(self, bound_vm):
        bound_vm.fn_validate_database = AsyncMock(side_effect=RuntimeError("BOOM"))
        bound_vm.current_step = 1
        with pytest.raises(RuntimeError, match="BOOM"):
            await bound_vm.validate_and_persist_current_step()
        assert bound_vm.validation_in_progress is False


# =====================================================================
# Test: Schedule Validation
# =====================================================================


class TestOnboardingVMScheduleValidation:
    def _setup_schedule_vm(self, bound_vm, mock_ch):
        """Setup VM with mock push_state and schedule time/enabled."""
        bound_vm.current_step = 6  # schedule step
        bound_vm.set_schedule_state(enabled=True, time_str="16:30")
        return bound_vm

    async def test_valid_time(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm.step_validated["schedule"] is True
        bound_vm.on_schedule_time_normalized.assert_called_with("16:30")

    async def test_invalid_time_defaults(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=True, time_str="25:99")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm._schedule_time == "16:30"
        bound_vm.on_schedule_time_normalized.assert_called_with("16:30")

    async def test_empty_time_defaults(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=True, time_str="")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        assert bound_vm._schedule_time == "16:30"

    async def test_saves_to_config_handler(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=False, time_str="08:00")
        await bound_vm.validate_and_persist_current_step()
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": False,
                "auto_update_time": "08:00",
            }
        )

    async def test_pushes_schedule_state_before_validation(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        await bound_vm.validate_and_persist_current_step()
        bound_vm.fn_push_schedule_state.assert_called_once()

    async def test_disabled_schedule(self, bound_vm, mock_config_handler):
        self._setup_schedule_vm(bound_vm, mock_config_handler)
        bound_vm.set_schedule_state(enabled=False, time_str="16:30")
        result = await bound_vm.validate_and_persist_current_step()
        assert result is True
        mock_config_handler.save_config.assert_called_once_with(
            {
                "auto_update_enabled": False,
                "auto_update_time": "16:30",
            }
        )

    async def test_set_schedule_state(self, vm):
        vm.set_schedule_state(enabled=False, time_str="09:00")
        assert vm._schedule_enabled is False
        assert vm._schedule_time == "09:00"
        assert vm.normalized_schedule_time == "09:00"


# =====================================================================
# Test: Data Sync
# =====================================================================


class TestOnboardingVMSync:
    @pytest.fixture
    def sync_vm(self, bound_vm, mock_data_processor, mock_config_handler):
        bound_vm._data_processor = mock_data_processor
        return bound_vm

    async def test_start_sync_sets_state(self, sync_vm):
        await sync_vm.start_sync(quick=True)
        assert sync_vm.sync_in_progress is False  # finally resets
        sync_vm.on_sync_state_changed.assert_any_call()

    async def test_start_sync_calls_initialize_system(self, sync_vm, mock_data_processor):
        await sync_vm.start_sync(quick=True)
        mock_data_processor.initialize_system.assert_awaited_once()
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert call_kwargs["quick"] is True

    async def test_start_sync_full(self, sync_vm, mock_data_processor):
        await sync_vm.start_sync(quick=False)
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert call_kwargs["quick"] is False

    async def test_start_sync_success_advances(self, sync_vm, mock_data_processor):
        sync_vm.current_step = 5  # data_sync step
        sync_vm.next_step = AsyncMock()
        with patch("ui.viewmodels.onboarding_view_model.asyncio.sleep", new_callable=AsyncMock):
            await sync_vm.start_sync(quick=True)
        sync_vm.next_step.assert_awaited_once()
        sync_vm.on_sync_progress.assert_any_call(1.0, "wizard_status_done")

    async def test_start_sync_success_no_double_end_notification(self, sync_vm, mock_data_processor):
        """on_sync_state_changed must be called exactly twice on success: once for start, once for end.
        Before the fix, it was called 3 times (start + manual end + finally end)."""
        sync_vm.current_step = 5
        sync_vm.next_step = AsyncMock()
        with patch("ui.viewmodels.onboarding_view_model.asyncio.sleep", new_callable=AsyncMock):
            await sync_vm.start_sync(quick=True)
        # 2 calls: sync_in_progress=True at start, sync_in_progress=False in finally
        assert sync_vm.on_sync_state_changed.call_count == 2

    async def test_start_sync_cancelled_result(self, sync_vm, mock_data_processor):
        mock_data_processor.initialize_system = AsyncMock(return_value=False)
        sync_vm.next_step = AsyncMock()
        await sync_vm.start_sync(quick=True)
        sync_vm.next_step.assert_not_awaited()
        sync_vm.on_sync_progress.assert_any_call(0, "wizard_status_cancelled")

    async def test_start_sync_exception(self, sync_vm, mock_data_processor):
        mock_data_processor.initialize_system = AsyncMock(side_effect=RuntimeError("sync failed"))
        with (
            patch(
                "ui.viewmodels.onboarding_view_model.classify_error",
                return_value={"type": "general"},
            ),
            patch(
                "ui.viewmodels.onboarding_view_model.get_error_message",
                return_value="Error occurred",
            ),
        ):
            await sync_vm.start_sync(quick=True)
        assert sync_vm.sync_in_progress is False
        sync_vm.on_sync_state_changed.assert_any_call()

    async def test_start_sync_progress_callback(self, sync_vm, mock_data_processor):
        await sync_vm.start_sync(quick=True)
        call_kwargs = mock_data_processor.initialize_system.call_args[1]
        assert "progress_callback" in call_kwargs
        cb = call_kwargs["progress_callback"]
        cb(75, 100, "Three quarters")
        sync_vm.on_sync_progress.assert_any_call(0.75, "Three quarters")

    async def test_cancel_sync(self, sync_vm, mock_data_processor):
        await sync_vm.cancel_sync()
        mock_data_processor.stop.assert_awaited_once()
        assert sync_vm.sync_in_progress is False

    async def test_cancel_sync_no_processor(self, sync_vm):
        sync_vm._data_processor = None
        await sync_vm.cancel_sync()
        assert sync_vm.sync_in_progress is False

    async def test_cancel_sync_exception(self, sync_vm, mock_data_processor):
        mock_data_processor.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        await sync_vm.cancel_sync()
        assert sync_vm.sync_in_progress is False
        sync_vm.on_sync_state_changed.assert_any_call()

    async def test_skip_sync(self, sync_vm):
        sync_vm.next_step = AsyncMock()
        await sync_vm.skip_sync()
        sync_vm.on_sync_progress.assert_any_call(0, "wizard_status_skip")
        sync_vm.next_step.assert_awaited_once()

    async def test_start_sync_quick_check_progress_init(self, sync_vm):
        await sync_vm.start_sync(quick=True)
        sync_vm.on_sync_progress.assert_any_call(0, "wizard_status_init")


# =====================================================================
# Test: Service Delegation
# =====================================================================


class TestOnboardingVMServiceDelegation:
    async def test_test_llm_connection(self):
        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            result = await OnboardingViewModel.test_llm_connection(
                provider="deepseek",
                model="chat",
                base_url="https://x",
                api_key="k",
            )
            mock_ai.test_connection.assert_awaited_once_with(
                provider="deepseek",
                model="chat",
                base_url="https://x",
                api_key="k",
            )
            assert result == {"success": True}

    async def test_test_llm_connection_with_kwargs(self):
        with patch("services.ai_service.AIService") as mock_ai:
            mock_ai.test_connection = AsyncMock(return_value={"success": True})
            result = await OnboardingViewModel.test_llm_connection(
                provider="azure",
                model="gpt4",
                base_url="https://az",
                api_key="k",
                azure_resource_name="res",
            )
            assert result == {"success": True}
            call_kwargs = mock_ai.test_connection.call_args[1]
            assert call_kwargs["azure_resource_name"] == "res"

    async def test_verify_local_model(self):
        with patch("services.local_model_manager.LocalModelManager") as mock_mgr_cls:
            mock_mgr = MagicMock()
            mock_mgr.load_model = AsyncMock(return_value=True)
            mock_mgr_cls.get_instance = AsyncMock(return_value=mock_mgr)
            result = await OnboardingViewModel.verify_local_model("/path/model.gguf", {"ctx": 2048})
            assert result is True
            mock_mgr.load_model.assert_awaited_once_with("/path/model.gguf", {"ctx": 2048}, is_verification=True)


# =====================================================================
# Test: validation_in_progress state transitions
# =====================================================================


class TestOnboardingVMValidationState:
    async def test_set_validation_in_progress_notifies(self, bound_vm):
        bound_vm._set_validation_in_progress(True)
        assert bound_vm.validation_in_progress is True
        bound_vm.on_validation_state_changed.assert_called_once()

    async def test_clear_validation_in_progress_notifies(self, bound_vm):
        bound_vm._set_validation_in_progress(False)
        assert bound_vm.validation_in_progress is False
        bound_vm.on_validation_state_changed.assert_called_once()


# =====================================================================
# Test: Unbound validator blocks step (P2 fix)
# =====================================================================


class TestOnboardingVMUnboundValidator:
    async def test_unbound_database_validator_blocks(self, vm):
        """fn_validate_database=None must block the database step."""
        vm.current_step = 1  # database
        result = await vm.validate_and_persist_current_step()
        assert result is False

    async def test_unbound_token_validator_blocks(self, vm):
        vm.current_step = 2  # token
        result = await vm.validate_and_persist_current_step()
        assert result is False

    async def test_unbound_cloud_ai_validator_blocks(self, vm):
        vm.current_step = 3  # cloud_ai
        result = await vm.validate_and_persist_current_step()
        assert result is False

    async def test_unbound_local_model_validator_passes(self, vm):
        """local_model is optional (block_on_missing_validator=False); unbound validator allows pass."""
        vm.current_step = 4  # local_model
        result = await vm.validate_and_persist_current_step()
        assert result is True

    async def test_dispose_then_validate_blocks(self, bound_vm):
        """After dispose(), injected validators are None → must block."""
        bound_vm.dispose()
        bound_vm.current_step = 1
        result = await bound_vm.validate_and_persist_current_step()
        assert result is False

    async def test_welcome_step_passes_without_validator(self, vm):
        """Welcome step has no validator and should still pass."""
        vm.current_step = 0
        result = await vm.validate_and_persist_current_step()
        assert result is True

    async def test_data_sync_step_passes_without_validator(self, vm):
        """data_sync step has no validator and should still pass."""
        vm.current_step = 5
        result = await vm.validate_and_persist_current_step()
        assert result is True


class TestStepConfigContract:
    """StepConfig 数据结构与 STEP_CONFIGS 常量契约（合并自 tests/unit/test_onboarding_wizard.py）。"""

    def test_step_config_defaults(self):
        config = StepConfig(
            id="test",
            name="test_name",
            show_prev=True,
            show_next=True,
            next_text_key="next",
            next_icon="arrow_forward",
        )
        assert config.show_skip is False
        assert config.skip_text_key == ""
        assert config.required is False
        assert config.validate_before_next is False

    def test_step_config_all_fields(self):
        config = StepConfig(
            id="test",
            name="test_name",
            show_prev=True,
            show_next=True,
            next_text_key="next",
            next_icon="arrow_forward",
            show_skip=True,
            skip_text_key="skip",
            required=True,
            validate_before_next=True,
        )
        assert config.id == "test"
        assert config.required is True
        assert config.validate_before_next is True
        assert config.show_skip is True

    def test_step_configs_ids(self):
        expected_ids = [
            "welcome",
            "database",
            "token",
            "cloud_ai",
            "local_model",
            "data_sync",
            "schedule",
            "complete",
        ]
        actual_ids = [config.id for config in STEP_CONFIGS]
        assert actual_ids == expected_ids

    def test_required_steps(self):
        required_steps = [config.id for config in STEP_CONFIGS if config.required]
        assert required_steps == ["database", "token", "cloud_ai"]

    def test_validate_before_next_steps(self):
        validate_steps = [config.id for config in STEP_CONFIGS if config.validate_before_next]
        assert validate_steps == [
            "database",
            "token",
            "cloud_ai",
            "local_model",
            "schedule",
        ]

    def test_step_configs_structure(self):
        # 首步是 welcome
        assert STEP_CONFIGS[0].id == "welcome"
        # 含 database 步
        assert any(c.id == "database" for c in STEP_CONFIGS)
        # 末步是 complete
        assert STEP_CONFIGS[-1].id == "complete"

    def test_welcome_step_no_prev(self):
        assert STEP_CONFIGS[0].show_prev is False

    def test_welcome_step_has_next(self):
        assert STEP_CONFIGS[0].show_next is True

    def test_local_model_step_has_skip(self):
        assert STEP_CONFIGS[4].show_skip is True


# =====================================================================
# Test: OnboardingWizard Module Contract (merged from test_onboarding_wizard.py)
# =====================================================================


class TestOnboardingWizardModuleContract:
    """onboarding_wizard.py 模块契约测试（合并自 tests/unit/ui/test_onboarding_wizard.py）。"""

    def test_set_onboarding_complete_not_in_onboarding_wizard_module(self):
        """set_onboarding_complete 不应在 onboarding_wizard.py 中调用，仅 main.py 调用。"""
        from pathlib import Path

        wizard_path = Path(__file__).resolve().parents[3] / "ui" / "views" / "onboarding_wizard.py"
        content = wizard_path.read_text(encoding="utf-8")
        assert "set_onboarding_complete" not in content, (
            "set_onboarding_complete should NOT be called in onboarding_wizard.py - "
            "it should only be called in main.py after service initialization"
        )


# =====================================================================
# Test: OnboardingWizard View (merged from test_onboarding_wizard.py)
# =====================================================================


class _OnboardingWizardBase:
    """OnboardingWizard View 测试基类，提供统一的 mock fixture 和工厂方法。"""

    patches: list

    @pytest.fixture(autouse=True)
    def _setup(self, mock_i18n, mock_app_colors, mock_app_styles):
        self.mock_i18n = mock_i18n
        self.mock_ac = mock_app_colors
        self.mock_as = mock_app_styles
        self.mock_ch = MagicMock()
        self.patches = [
            patch("ui.views.onboarding_wizard.I18n", self.mock_i18n),
            patch("ui.views.onboarding_wizard.AppColors", self.mock_ac),
            patch("ui.views.onboarding_wizard.AppStyles", self.mock_as),
            patch("ui.views.onboarding_wizard.ConfigHandler", self.mock_ch),
            patch("ui.viewmodels.onboarding_view_model.DataProcessor"),
            patch("ui.views.onboarding_wizard.DatabaseConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.TushareConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.LLMConfigPanel", MagicMock()),
            patch("ui.views.onboarding_wizard.LocalModelConfigPanel", MagicMock()),
        ]
        with contextlib.ExitStack() as stack:
            for p in self.patches:
                stack.enter_context(p)
            yield

    def _make_wizard(self, mock_page, on_complete=None):
        from ui.views.onboarding_wizard import OnboardingWizard

        return OnboardingWizard(mock_page, on_complete=on_complete)


class TestOnboardingWizardNavigation(_OnboardingWizardBase):
    """OnboardingWizard View 导航行为测试。"""

    def test_initial_step_is_zero(self, mock_page):
        wizard = self._make_wizard(mock_page)
        assert wizard.vm.current_step == 0

    async def test_next_step_advances(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._update_wizard = MagicMock()
        await wizard._next_step()
        assert wizard.vm.current_step == 1

    async def test_next_step_blocks_on_validation_failure(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 1
        wizard.vm.validate_and_persist_current_step = AsyncMock(return_value=False)
        await wizard._next_step()
        assert wizard.vm.current_step == 1

    async def test_prev_step_does_not_go_below_zero(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        await wizard._prev_step()
        assert wizard.vm.current_step == 0


class TestOnboardingWizardRendering(_OnboardingWizardBase):
    """OnboardingWizard View 渲染行为测试。"""

    def test_update_wizard_updates_step_container(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 1
        wizard._update_wizard()
        assert wizard.step_container.content == wizard.steps_content[1]

    def test_update_wizard_shows_indicators_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard()
        assert wizard.step_indicators.visible is True

    def test_update_wizard_hides_indicators_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_hides_indicators_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 7
        wizard._update_wizard()
        assert wizard.step_indicators.visible is False

    def test_update_wizard_shows_header_for_welcome(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 0
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_shows_header_for_complete(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 7
        wizard._update_wizard()
        assert wizard.header_container.visible is True

    def test_update_wizard_hides_header_for_config_steps(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard()
        assert wizard.header_container.visible is False

    def test_on_vm_step_changed_triggers_update_wizard(self, mock_page):
        """VM 步骤变更回调触发 View 更新"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.current_step = 3
        wizard._update_wizard = MagicMock()
        wizard._on_vm_step_changed()
        wizard._update_wizard.assert_called_once()


class TestOnboardingWizardI18n(_OnboardingWizardBase):
    """OnboardingWizard View 国际化行为测试。"""

    def test_on_locale_change_updates_header_title(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_title = wizard.header_title
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if key == "wizard_welcome_title" else key
        wizard._on_locale_change("en_US")
        assert original_title.value == "en_wizard_welcome_title"
        assert wizard.header_title is original_title

    def test_on_locale_change_updates_header_desc(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_desc = wizard.header_desc
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: (
            f"en_{key}" if key == "wizard_welcome_desc_with_time" else key
        )
        wizard._on_locale_change("en_US")
        assert original_desc.value == "en_wizard_welcome_desc_with_time"
        assert wizard.header_desc is original_desc

    def test_on_locale_change_updates_gradient_guide_text(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_text = wizard.gradient_guide_text
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if key == "wizard_welcome_guide" else key
        wizard._on_locale_change("en_US")
        assert original_text.value == "en_wizard_welcome_guide"
        assert wizard.gradient_guide_text is original_text

    def test_header_title_is_in_ui_tree(self, mock_page):
        wizard = self._make_wizard(mock_page)
        header_column = wizard.header_container
        assert wizard.header_title in header_column.controls
        assert wizard.header_desc in header_column.controls

    def test_on_language_change_wizard_preserves_header_reference(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_header_container = wizard.header_container
        original_header_title = wizard.header_title
        original_header_desc = wizard.header_desc
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        assert wizard.header_container is original_header_container
        assert wizard.header_title is original_header_title
        assert wizard.header_desc is original_header_desc

    def test_on_language_change_wizard_updates_header_title_directly(self, mock_page):
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        original_title = wizard.header_title
        original_desc = wizard.header_desc
        self.mock_i18n.get.side_effect = lambda key, *a, **kw: f"en_{key}" if "welcome" in key else key
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        assert original_title.value == "en_wizard_welcome_title"
        assert original_desc.value == "en_wizard_welcome_desc_with_time"
        assert wizard.header_title is original_title
        self.mock_ch.set_locale.assert_called_with("en_US")

    def test_on_language_change_wizard_updates_locale_configuration(self, mock_page):
        import flet as ft

        mock_page.locale_configuration = MagicMock()
        mock_page.locale_configuration.current_locale = ft.Locale("zh", "CN")
        mock_page.update = MagicMock()

        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        self.mock_i18n.current_locale.return_value = "en_US"
        wizard._on_language_change_wizard(MagicMock())

        assert mock_page.locale_configuration.current_locale.language_code == "en"
        assert mock_page.locale_configuration.current_locale.country_code == "US"
        mock_page.update.assert_called()

    def test_on_language_change_wizard_persist_failure_skips_i18n_set(self, mock_page):
        """ConfigHandler.set_locale 返回 False 时，不切换 I18n，回滚 dropdown。"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._safe_update = MagicMock()
        self.mock_ch.set_locale.return_value = False
        self.mock_i18n.current_locale.return_value = "zh_CN"
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"

        wizard._on_language_change_wizard(MagicMock())

        self.mock_i18n.set_locale.assert_not_called()
        assert wizard.wizard_language_dropdown.value == "zh_CN"

    def test_language_change_rebinds_panel_callbacks(self, mock_page):
        """语言切换后 VM 回调指向新面板"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.wizard_language_dropdown = MagicMock()
        wizard.wizard_language_dropdown.value = "en_US"
        wizard._on_language_change_wizard(MagicMock())
        # 验证回调已更新为新面板的方法
        assert wizard.vm.fn_validate_database is not None
        assert wizard.vm.fn_validate_database is wizard.database_panel.save_config
        assert wizard.vm.fn_validate_token is wizard.tushare_panel.verify_token


class TestOnboardingWizardLifecycle(_OnboardingWizardBase):
    """OnboardingWizard View 生命周期行为测试。"""

    def test_on_mount_subscribes_i18n(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard._on_mount()
        self.mock_i18n.subscribe.assert_called_once()
        assert wizard._locale_subscription_id == "sub_id"

    def test_on_unmount_unsubscribes_i18n(self, mock_page):
        wizard = self._make_wizard(mock_page)
        wizard._locale_subscription_id = "sub_id"
        wizard._on_unmount()
        self.mock_i18n.unsubscribe.assert_called_once_with("sub_id")
        assert wizard._locale_subscription_id is None

    async def test_cleanup_vm_cancels_sync_and_disposes(self, mock_page):
        """卸载时取消进行中的同步并清理 VM"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.sync_in_progress = True
        wizard.vm.cancel_sync = AsyncMock()
        wizard.vm.dispose = MagicMock()
        await wizard._cleanup_vm()
        wizard.vm.cancel_sync.assert_awaited_once()
        wizard.vm.dispose.assert_called_once()

    async def test_cleanup_vm_disposes_without_sync(self, mock_page):
        """卸载时无进行中同步，直接清理 VM"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.sync_in_progress = False
        wizard.vm.cancel_sync = AsyncMock()
        wizard.vm.dispose = MagicMock()
        await wizard._cleanup_vm()
        wizard.vm.cancel_sync.assert_not_awaited()
        wizard.vm.dispose.assert_called_once()


class TestOnboardingWizardLoading(_OnboardingWizardBase):
    """OnboardingWizard View 加载遮罩行为测试。"""

    def test_on_panel_loading_change_shows_overlay_when_loading(self, mock_page):
        """面板加载中 → 遮罩显示"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._on_panel_loading_change(True)
        assert wizard.loading_overlay.visible is True

    def test_on_panel_loading_change_hides_overlay_when_not_loading_and_not_validating(self, mock_page):
        """面板加载完成 + VM 校验完成 → 遮罩隐藏"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard.vm.validation_in_progress = False
        wizard._on_panel_loading_change(False)
        assert wizard.loading_overlay.visible is False

    def test_on_panel_loading_change_keeps_overlay_when_validating(self, mock_page):
        """面板加载完成但 VM 校验中 → 遮罩保持显示"""
        wizard = self._make_wizard(mock_page)
        set_page(wizard, mock_page)
        wizard._show_loading_overlay(True)
        wizard.vm.validation_in_progress = True
        wizard._on_panel_loading_change(False)
        assert wizard.loading_overlay.visible is True
