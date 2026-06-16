"""Unit tests for OnboardingViewModel — MVVM-003 fix.

TDD RED phase: these tests define the expected ViewModel contract.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ui.viewmodels.onboarding_view_model import OnboardingViewModel, STEP_CONFIGS, StepConfig


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
            patch("ui.viewmodels.onboarding_view_model.classify_error", return_value={"type": "general"}),
            patch("ui.viewmodels.onboarding_view_model.get_error_message", return_value="Error occurred"),
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
            mock_mgr.load_model.assert_awaited_once_with("/path/model.gguf", {"ctx": 2048})


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
        assert validate_steps == ["database", "token", "cloud_ai", "local_model", "schedule"]
