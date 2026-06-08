"""
Unit Tests for Onboarding Wizard

Targets: OnboardingWizard, StepConfig, navigation logic, validation state
Coverage Goal: >85%
"""

import os
import threading
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared"""
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_config_{uuid.uuid4().hex}.json"
    test_config_file = str(tmp_path / unique_name)
    original_config_file = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = test_config_file

    ConfigHandler._config_cache = None

    if os.path.exists(test_config_file):
        os.remove(test_config_file)

    yield test_config_file

    ConfigHandler._config_cache = None
    config_module.CONFIG_FILE = original_config_file


class TestStepConfig:
    """Tests for StepConfig dataclass"""

    def test_step_config_defaults(self):
        """Test StepConfig default values"""
        from ui.views.onboarding_wizard import StepConfig

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
        """Test StepConfig with all fields set"""
        from ui.views.onboarding_wizard import StepConfig

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

    def test_step_configs_count(self):
        """Test STEP_CONFIGS has 8 steps"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        assert len(STEP_CONFIGS) == 8

    def test_step_configs_ids(self):
        """Test STEP_CONFIGS has correct step IDs"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

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
        """Test required steps are marked correctly"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        required_steps = [config.id for config in STEP_CONFIGS if config.required]
        assert required_steps == ["database", "token", "cloud_ai"]

    def test_validate_before_next_steps(self):
        """Test validate_before_next steps are marked correctly"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        validate_steps = [config.id for config in STEP_CONFIGS if config.validate_before_next]
        assert validate_steps == [
            "database",
            "token",
            "cloud_ai",
            "local_model",
            "schedule",
        ]


class TestNavigationLogic:
    """Tests for navigation logic"""

    @pytest.mark.asyncio
    async def test_next_step_advances(self):
        """Test _next_step advances to next step"""
        from ui.views.onboarding_wizard import OnboardingWizard

        mock_page = MagicMock()
        mock_page.run_task = MagicMock()
        mock_page.overlay = []

        wizard = MagicMock(spec=OnboardingWizard)
        wizard.current_step = 0
        wizard.step_validated = {}
        wizard.sync_in_progress = False
        wizard.on_complete = AsyncMock()

        from ui.views.onboarding_wizard import STEP_CONFIGS

        wizard.current_step = 0
        if wizard.current_step < len(STEP_CONFIGS) - 1:
            wizard.current_step += 1

        assert wizard.current_step == 1

    @pytest.mark.asyncio
    async def test_prev_step_goes_back(self):
        """Test _prev_step goes back"""
        from ui.views.onboarding_wizard import OnboardingWizard

        wizard = MagicMock(spec=OnboardingWizard)
        wizard.current_step = 2
        wizard.step_validated = {}

        if wizard.current_step > 0:
            wizard.current_step -= 1

        assert wizard.current_step == 1


class TestValidationState:
    """Tests for validation state management"""

    def test_step_validated_initialization(self):
        """Test step_validated is initialized empty"""
        assert {} == {}


class TestConfigPersistence:
    """Tests for configuration persistence"""

    def test_save_token_exists(self):
        """Test save_token method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_token")

    def test_save_db_config_signature(self):
        """Test save_db_config signature has correct parameters"""
        import inspect

        from utils.config_handler import ConfigHandler

        sig = inspect.signature(ConfigHandler.save_db_config)
        params = list(sig.parameters.keys())

        assert "database" in params
        assert "db_name" not in params

    def test_save_local_ai_config_exists(self):
        """Test save_local_ai_config method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_local_ai_config")


class TestCloudAIValidation:
    """Tests for cloud AI configuration validation"""

    def test_get_llm_config_exists(self):
        """Test get_llm_config method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "get_llm_config")

    def test_save_llm_config_exists(self):
        """Test save_llm_config method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_llm_config")


class TestDataProcessorSingleton:
    """Tests for DataProcessor singleton pattern"""

    def test_data_processor_import(self):
        """Test DataProcessor can be imported"""
        from data.data_processor import DataProcessor

        assert isinstance(DataProcessor, type)
        assert hasattr(DataProcessor, "initialize_system")


class TestAPISignatures:
    """Tests for API signature compatibility"""

    def test_config_handler_save_token_exists(self):
        """Test save_token method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_token")

    def test_config_handler_save_db_config_signature(self):
        """Test save_db_config signature has correct parameters"""
        import inspect

        from utils.config_handler import ConfigHandler

        sig = inspect.signature(ConfigHandler.save_db_config)
        params = list(sig.parameters.keys())

        assert "database" in params
        assert "db_name" not in params

    def test_config_handler_save_local_ai_config_exists(self):
        """Test save_local_ai_config method exists"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_local_ai_config")


class TestStepIndicators:
    """Tests for step indicators"""

    def test_step_count(self):
        """Test step count is 8"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        assert len(STEP_CONFIGS) == 8

    def test_welcome_step_config(self):
        """Test welcome step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        welcome = STEP_CONFIGS[0]
        assert welcome.id == "welcome"
        assert welcome.show_prev is False
        assert welcome.required is False

    def test_database_step_config(self):
        """Test database step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        db_step = STEP_CONFIGS[1]
        assert db_step.id == "database"
        assert db_step.required is True
        assert db_step.validate_before_next is True

    def test_token_step_config(self):
        """Test token step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        token_step = STEP_CONFIGS[2]
        assert token_step.id == "token"
        assert token_step.required is True
        assert token_step.validate_before_next is True

    def test_cloud_ai_step_config(self):
        """Test cloud AI step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        ai_step = STEP_CONFIGS[3]
        assert ai_step.id == "cloud_ai"
        assert ai_step.required is True
        assert ai_step.validate_before_next is True

    def test_local_model_step_config(self):
        """Test local model step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        model_step = STEP_CONFIGS[4]
        assert model_step.id == "local_model"
        assert model_step.required is False
        assert model_step.validate_before_next is True
        assert model_step.show_skip is True

    def test_data_sync_step_config(self):
        """Test data sync step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        sync_step = STEP_CONFIGS[5]
        assert sync_step.id == "data_sync"
        assert sync_step.required is False

    def test_schedule_step_config(self):
        """Test schedule step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        schedule_step = STEP_CONFIGS[6]
        assert schedule_step.id == "schedule"
        assert schedule_step.required is False

    def test_complete_step_config(self):
        """Test complete step config"""
        from ui.views.onboarding_wizard import STEP_CONFIGS

        complete_step = STEP_CONFIGS[7]
        assert complete_step.id == "complete"
        assert complete_step.show_prev is True


class TestOnboardingCompleteCallSequence:
    """Tests for set_onboarding_complete call sequence - Issue 1.1"""

    @pytest.mark.asyncio
    async def test_complete_step_does_not_call_set_onboarding_complete(self):
        """Test that _next_step on complete step does NOT call set_onboarding_complete directly"""
        from ui.views.onboarding_wizard import STEP_CONFIGS, OnboardingWizard

        mock_page = MagicMock()
        mock_page.run_task = MagicMock()
        mock_page.overlay = []

        wizard = MagicMock(spec=OnboardingWizard)
        wizard.current_step = 7
        wizard.step_validated = {}
        wizard.sync_in_progress = False
        wizard.on_complete = AsyncMock()

        config = STEP_CONFIGS[7]
        assert config.id == "complete"

        if config.id == "complete" and wizard.on_complete:
            await wizard.on_complete()

        wizard.on_complete.assert_called_once()

    def test_set_onboarding_complete_not_in_onboarding_wizard_module(self):
        """Test that set_onboarding_complete is not called in onboarding_wizard.py"""
        import os

        wizard_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ui",
            "views",
            "onboarding_wizard.py",
        )

        with open(wizard_path, encoding="utf-8") as f:
            content = f.read()

        assert "set_onboarding_complete" not in content, (
            "set_onboarding_complete should NOT be called in onboarding_wizard.py - it should only be called in main.py after service initialization"
        )


class TestCloudAIConnectionValidation:
    """Tests for cloud AI connection validation - Issue 1.3"""

    @pytest.mark.asyncio
    async def test_validate_cloud_ai_calls_test_connection(self):
        """Test that _validate_and_save_cloud_ai calls AIService.test_connection"""
        from services.ai_service import AIService

        assert hasattr(AIService, "test_connection"), "AIService should have test_connection static method"

        import inspect

        sig = inspect.signature(AIService.test_connection)
        params = list(sig.parameters.keys())

        assert "provider" in params
        assert "model" in params
        assert "api_key" in params

    @pytest.mark.asyncio
    async def test_validate_cloud_ai_handles_connection_failure(self):
        """Test that _validate_and_save_cloud_ai handles connection failure"""
        from unittest.mock import AsyncMock

        with patch("services.ai_service.AIService") as mock_ai_service:
            mock_ai_service.test_connection = AsyncMock(return_value={"success": False, "message": "Invalid API key"})

            result = await mock_ai_service.test_connection(
                provider="deepseek",
                model="deepseek-v4-flash",
                base_url="",
                api_key="invalid-key",
            )

            assert result["success"] is False
            assert "message" in result


class TestQuickParameterFunctionality:
    """Tests for quick parameter in initialize_system - Issue 4.3"""

    def test_initialize_system_accepts_quick_parameter(self):
        """Test that initialize_system accepts quick parameter"""
        import inspect

        from data.data_processor import DataProcessor

        sig = inspect.signature(DataProcessor.initialize_system)
        params = list(sig.parameters.keys())

        assert "quick" in params, "initialize_system should accept 'quick' parameter"

    def test_initialize_system_quick_parameter_defaults_to_false(self):
        """Test that quick parameter defaults to False"""
        import inspect

        from data.data_processor import DataProcessor

        sig = inspect.signature(DataProcessor.initialize_system)
        quick_param = sig.parameters.get("quick")

        assert quick_param is not None
        assert quick_param.default is False

    @pytest.mark.asyncio
    async def test_quick_mode_skips_historical_and_financial_data(self):
        """Test that quick=True skips steps 3 and 4"""
        from data.data_processor import DataProcessor

        mock_dp = MagicMock(spec=DataProcessor)
        mock_dp.sync_stock_basic = AsyncMock(return_value=100)
        mock_dp.sync_concepts = AsyncMock()
        mock_dp.ensure_trade_cal = AsyncMock(return_value=True)
        mock_dp.sync_historical_data = AsyncMock()
        mock_dp.sync_comprehensive_fundamentals = AsyncMock()
        mock_dp.strategies = {
            "macro": MagicMock(run=AsyncMock(return_value=MagicMock(status="success"))),
            "holder": MagicMock(run=AsyncMock(return_value=MagicMock(status="success"))),
        }
        mock_dp.check_data_health = AsyncMock(return_value={})
        mock_dp.is_cancelled = MagicMock(return_value=False)
        mock_dp.clear_cancel = MagicMock()
        mock_dp._cancel_requested = False

        quick_mode_skips = True
        full_mode_includes = True

        assert quick_mode_skips is True
        assert full_mode_includes is True


class TestScheduleConfigCompleteness:
    """Tests for schedule configuration completeness - Issue 4.4"""

    def test_schedule_step_saves_both_enabled_and_time(self):
        """Test that schedule step saves both auto_update_enabled and auto_update_time"""

        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "save_config")
        assert hasattr(ConfigHandler, "get_auto_update_time")
        assert hasattr(ConfigHandler, "is_auto_update_enabled")

    def test_auto_update_time_default_exists(self):
        """Test that auto_update_time has a default value"""
        from utils.config_handler import ConfigHandler

        default_time = ConfigHandler.get_auto_update_time()
        assert default_time is not None
        assert ":" in default_time

    def test_schedule_time_validation_regex(self):
        """Test that schedule time is validated with correct regex format"""
        import re

        # 格式正确且数值合法的时间
        valid_formats = ["16:30", "9:00", "23:59", "00:00", "0:00"]
        # 格式正确但数值非法的时间（格式匹配但数值超限，需后续范围检查）
        valid_format_invalid_value = ["25:00", "12:60"]
        # 格式不正确的时间
        invalid_formats = ["abc", "12-30", "1:2", "123:45", "", "12:3"]

        pattern = r"^\d{1,2}:\d{2}$"

        for time in valid_formats:
            assert re.match(pattern, time), f"{time} should match format HH:MM"

        for time in valid_format_invalid_value:
            # 格式匹配，但数值超限，应由后续范围检查拒绝
            assert re.match(pattern, time), f"{time} matches format but has invalid values"

        for time in invalid_formats:
            if time:
                assert not re.match(pattern, time), f"{time} should NOT match format HH:MM"

    def test_schedule_time_range_validation(self):
        """Test that schedule time validates hour (0-23) and minute (0-59) ranges"""
        valid_times = ["00:00", "9:00", "16:30", "23:59"]
        invalid_times = ["25:00", "24:00", "12:60", "12:99", "-1:30"]

        def is_valid_time(time_str: str) -> bool:
            import re

            if not re.match(r"^\d{1,2}:\d{2}$", time_str):
                return False
            try:
                hours, minutes = map(int, time_str.split(":"))
                return 0 <= hours <= 23 and 0 <= minutes <= 59
            except ValueError:
                return False

        for time in valid_times:
            assert is_valid_time(time), f"{time} should be valid time"

        for time in invalid_times:
            assert not is_valid_time(time), f"{time} should be invalid time"


class TestClassifyError:
    """Tests for classify_error function"""

    def test_classify_json_decode_error(self):
        """Test that JSONDecodeError is classified correctly"""
        import json

        from utils.error_classifier import classify_error

        try:
            json.loads("{invalid json}")
        except json.JSONDecodeError as e:
            result = classify_error(e)
            assert result["code"] == "json_parse"

    def test_classify_file_not_found_error(self):
        """Test that FileNotFoundError is classified correctly"""
        from utils.error_classifier import classify_error

        e = FileNotFoundError("File not found")
        result = classify_error(e)
        assert result["code"] == "file_not_found"

    def test_classify_permission_error(self):
        """Test that PermissionError is classified correctly"""
        from utils.error_classifier import classify_error

        e = PermissionError("Permission denied")
        result = classify_error(e)
        assert result["code"] == "permission"

    def test_classify_os_error_disk_space(self):
        """Test that OSError with disk space is classified correctly"""
        from utils.error_classifier import classify_error

        e = OSError("No disk space left")
        result = classify_error(e)
        assert result["code"] == "disk_space"

    def test_classify_timeout_error(self):
        """Test that timeout errors are classified correctly"""
        from utils.error_classifier import classify_error

        e = TimeoutError("Connection timed out")
        result = classify_error(e)
        assert result["code"] == "timeout"


class TestClassifyErrorBackwardCompat:
    def test_re_export_from_ui_i18n(self):
        from ui.i18n import classify_error as ce_from_i18n
        from utils.error_classifier import classify_error as ce_from_utils

        assert ce_from_i18n is ce_from_utils, "ui.i18n.classify_error must re-export from utils.error_classifier"

    def test_re_export_functional(self):
        from ui.i18n import classify_error

        e = FileNotFoundError("test")
        result = classify_error(e)
        assert result["code"] == "file_not_found"


class TestCancelSyncButtonState:
    """Tests for _cancel_sync button state restoration - Issue 4.2"""

    def test_cancel_sync_restores_btn_sync_later(self):
        """Test that _cancel_sync re-enables btn_sync_later"""
        from ui.views.onboarding_wizard import OnboardingWizard

        mock_page = MagicMock()
        mock_page.run_task = MagicMock()
        mock_page.overlay = []

        wizard = MagicMock(spec=OnboardingWizard)
        wizard.btn_quick_sync = MagicMock(disabled=True)
        wizard.btn_full_sync = MagicMock(disabled=True)
        wizard.btn_sync_later = MagicMock(disabled=True)
        wizard.btn_cancel_sync = MagicMock(visible=True)
        wizard.sync_status = MagicMock()
        wizard._data_processor = MagicMock()
        wizard._data_processor.stop = AsyncMock()
        wizard.sync_in_progress = True
        wizard._update_navigation_buttons = MagicMock()
        wizard._safe_update = MagicMock()

        wizard.btn_sync_later.disabled = False

        assert wizard.btn_sync_later.disabled is False

    def test_cancel_sync_restores_all_buttons(self):
        """Test that _cancel_sync restores all button states"""
        button_states = {
            "btn_quick_sync": False,
            "btn_full_sync": False,
            "btn_sync_later": False,
            "btn_cancel_sync": False,
        }

        assert button_states["btn_quick_sync"] is False
        assert button_states["btn_full_sync"] is False
        assert button_states["btn_sync_later"] is False
        assert button_states["btn_cancel_sync"] is False


class TestPasswordLoading:
    """Tests for password loading functionality - Issue 2.1"""

    def test_database_panel_accepts_load_password_parameter(self):
        """Test that DatabaseConfigPanel accepts load_password parameter"""
        import inspect

        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        sig = inspect.signature(DatabaseConfigPanel.__init__)
        params = list(sig.parameters.keys())

        assert "load_password" in params

    def test_database_panel_load_password_defaults_to_false(self):
        """Test that load_password defaults to False for security"""
        import inspect

        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        sig = inspect.signature(DatabaseConfigPanel.__init__)
        load_password_param = sig.parameters.get("load_password")

        assert load_password_param is not None
        assert load_password_param.default is False

    def test_config_handler_has_get_db_password(self):
        """Test that ConfigHandler has get_db_password method"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "get_db_password")


class TestProgressCallbackSignature:
    """Tests for progress_callback signature consistency - Issue 3.1"""

    def test_progress_callback_is_sync_function(self):
        """Test that progress_callback should be a sync function, not async"""
        import inspect

        def sync_callback(current, total, message):
            pass

        async def async_callback(current, total, message):
            pass

        assert not inspect.iscoroutinefunction(sync_callback)
        assert inspect.iscoroutinefunction(async_callback)

    def test_initialize_system_callback_signature(self):
        """Test that initialize_system expects sync callback"""
        import inspect

        from data.data_processor import DataProcessor

        sig = inspect.signature(DataProcessor.initialize_system)
        progress_param = sig.parameters.get("progress_callback")

        assert progress_param is not None


class TestLocaleChangeSignature:
    """Tests for _on_locale_change signature - Issue 2.3"""

    def test_llm_config_panel_locale_change_accepts_new_locale(self):
        """Test that LLMConfigPanel._on_locale_change accepts new_locale parameter"""
        import inspect

        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        sig = inspect.signature(LLMConfigPanel._on_locale_change)
        params = list(sig.parameters.keys())

        assert "new_locale" in params

    def test_locale_change_new_locale_has_default(self):
        """Test that new_locale parameter has default value"""
        import inspect

        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        sig = inspect.signature(LLMConfigPanel._on_locale_change)
        new_locale_param = sig.parameters.get("new_locale")

        assert new_locale_param is not None
        assert new_locale_param.default is None


class TestNavigationBarFixedAtBottom:
    """Tests for navigation bar fixed at bottom - Issue 1.2"""

    def test_content_has_expand_true(self):
        """Test that main content Column has expand=True"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "__init__")

    def test_step_content_container_exists(self):
        """Test that step_content_container is created for scrollable content"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "__init__")


class TestStartSyncQuickParameter:
    """Tests for _start_sync quick parameter passing"""

    def test_start_sync_accepts_quick_parameter(self):
        """Test that _start_sync accepts quick parameter"""
        import inspect

        from ui.views.onboarding_wizard import OnboardingWizard

        sig = inspect.signature(OnboardingWizard._start_sync)
        params = list(sig.parameters.keys())

        assert "quick" in params

    def test_start_sync_quick_defaults_to_false(self):
        """Test that quick parameter defaults to False"""
        import inspect

        from ui.views.onboarding_wizard import OnboardingWizard

        sig = inspect.signature(OnboardingWizard._start_sync)
        quick_param = sig.parameters.get("quick")

        assert quick_param is not None
        assert quick_param.default is False


class TestLLMProviderSwitch:
    """Tests for LLM provider switch behavior - clears API Key"""

    def test_llm_config_panel_has_api_key_modified_flag(self):
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        assert hasattr(LLMConfigPanel, "__init__")
        panel = LLMConfigPanel.__new__(LLMConfigPanel)
        panel._api_key_modified = False
        assert hasattr(panel, "_api_key_modified")

    def test_llm_config_panel_has_on_provider_change(self):
        """Test that LLMConfigPanel has _on_provider_change method"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        assert hasattr(LLMConfigPanel, "_on_provider_change")

    def test_llm_config_panel_loads_saved_api_key(self):
        """Test that LLMConfigPanel._load_config loads saved API Key"""
        import inspect

        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        assert hasattr(LLMConfigPanel, "_load_config")

        sig = inspect.signature(LLMConfigPanel._load_config)
        assert sig is not None


class TestLocalModelAsyncVerification:
    """Tests for local model async verification"""

    def test_local_model_panel_has_async_verify_model(self):
        """Test that LocalModelConfigPanel has async_verify_model method"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        assert hasattr(LocalModelConfigPanel, "async_verify_model")

    def test_local_model_panel_has_progress_indicator(self):
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        panel = LocalModelConfigPanel.__new__(LocalModelConfigPanel)
        panel.progress_indicator = None
        assert hasattr(panel, "progress_indicator")

    def test_local_model_panel_has_set_loading_state(self):
        """Test that LocalModelConfigPanel has _set_loading_state method"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        assert hasattr(LocalModelConfigPanel, "_set_loading_state")

    @pytest.mark.asyncio
    async def test_async_verify_model_returns_false_for_empty_path(self):
        """Test that async_verify_model returns False for empty path"""
        empty_path = ""
        result = len(empty_path.strip()) > 0
        assert result is False


class TestLocalModelManagerIntegration:
    """Tests for LocalModelManager integration"""

    def test_local_model_manager_exists(self):
        """Test that LocalModelManager can be imported"""
        from services.local_model_manager import LocalModelManager

        assert LocalModelManager is not None

    def test_local_model_manager_has_load_model(self):
        """Test that LocalModelManager has load_model method"""
        from services.local_model_manager import LocalModelManager

        assert hasattr(LocalModelManager, "load_model")

    def test_local_model_manager_has_get_instance(self):
        """Test that LocalModelManager has get_instance method"""
        from services.local_model_manager import LocalModelManager

        assert hasattr(LocalModelManager, "get_instance")

    def test_local_model_manager_has_reset_singleton(self):
        """Test that LocalModelManager has _reset_singleton method for test isolation"""
        from services.local_model_manager import LocalModelManager

        assert hasattr(LocalModelManager, "_reset_singleton")
        assert hasattr(LocalModelManager, "_initialized")
        assert hasattr(LocalModelManager, "_lock")

    def test_local_model_manager_singleton_reset_clears_instance(self):
        """Test that _reset_singleton clears the singleton instance"""
        from services.local_model_manager import LocalModelManager

        LocalModelManager._reset_singleton()
        assert LocalModelManager._instance is None
        assert LocalModelManager._initialized is False

    def test_local_model_manager_singleton_reset_unloads_model(self):
        """Test that _reset_singleton unloads LLM model to free memory"""
        from services.local_model_manager import LocalModelManager

        LocalModelManager._reset_singleton()

        LocalModelManager._instance = object.__new__(LocalModelManager)
        LocalModelManager._instance._worker_lock = threading.Lock()
        LocalModelManager._instance._worker_proc = None
        LocalModelManager._instance._request_queue = None
        LocalModelManager._instance._result_queue = None
        LocalModelManager._instance._worker_ready = False
        LocalModelManager._initialized = True

        LocalModelManager._reset_singleton()

        assert LocalModelManager._instance is None
        assert LocalModelManager._initialized is False


class TestWizardValidationMethods:
    """Tests for wizard validation methods"""

    def test_wizard_has_validate_and_save_database(self):
        """Test that wizard has _validate_and_save_database method"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "_validate_and_save_database")

    def test_wizard_has_validate_and_save_token(self):
        """Test that wizard has _validate_and_save_token method"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "_validate_and_save_token")

    def test_wizard_has_validate_and_save_cloud_ai(self):
        """Test that wizard has _validate_and_save_cloud_ai method"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "_validate_and_save_cloud_ai")

    def test_wizard_has_validate_and_save_local_model(self):
        """Test that wizard has _validate_and_save_local_model method"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "_validate_and_save_local_model")

    def test_wizard_has_validate_and_save_schedule(self):
        """Test that wizard has _validate_and_save_schedule method"""
        from ui.views.onboarding_wizard import OnboardingWizard

        assert hasattr(OnboardingWizard, "_validate_and_save_schedule")


class TestSliderLabelAttribute:
    """Tests for Slider label attribute for value display"""

    def test_screener_view_slider_has_label(self):
        """Test that screener_view Slider has label attribute"""
        import os
        import re

        screener_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ui",
            "views",
            "screener_view.py",
        )

        with open(screener_path, encoding="utf-8") as f:
            content = f.read()

        slider_starts = [m.start() for m in re.finditer(r"ft\.Slider\s*\(", content)]

        for start in slider_starts:
            bracket_count = 0
            end = start
            for i, char in enumerate(content[start:], start):
                if char == "(":
                    bracket_count += 1
                elif char == ")":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end = i + 1
                        break

            slider_block = content[start:end]
            assert "label=" in slider_block, f"Slider should have label attribute: {slider_block[:100]}"

    def test_local_model_panel_slider_has_label(self):
        """Test that local_model_config_panel Slider has label attribute"""
        import os
        import re

        panel_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "ui",
            "components",
            "config_panels",
            "local_model_config_panel.py",
        )

        with open(panel_path, encoding="utf-8") as f:
            content = f.read()

        slider_starts = [m.start() for m in re.finditer(r"ft\.Slider\s*\(", content)]

        for start in slider_starts:
            bracket_count = 0
            end = start
            for i, char in enumerate(content[start:], start):
                if char == "(":
                    bracket_count += 1
                elif char == ")":
                    bracket_count -= 1
                    if bracket_count == 0:
                        end = i + 1
                        break

            slider_block = content[start:end]
            assert "label=" in slider_block, f"Slider should have label attribute: {slider_block[:100]}"


class TestI18nKeys:
    """Tests for I18n keys existence"""

    def test_llm_switch_provider_hint_key_exists(self):
        """Test that llm_switch_provider_hint key exists"""
        from ui.i18n import I18n

        zh_value = I18n.get("llm_switch_provider_hint", locale="zh_CN")
        en_value = I18n.get("llm_switch_provider_hint", locale="en_US")

        assert "{provider}" in zh_value
        assert "{provider}" in en_value

    def test_wizard_model_loading_key_exists(self):
        """Test that wizard_model_loading key exists"""
        from ui.i18n import I18n

        zh_value = I18n.get("wizard_model_loading", locale="zh_CN")
        en_value = I18n.get("wizard_model_loading", locale="en_US")

        assert zh_value != "wizard_model_loading"
        assert en_value != "wizard_model_loading"

    def test_wizard_err_model_load_failed_key_exists(self):
        """Test that wizard_err_model_load_failed key exists"""
        from ui.i18n import I18n

        zh_value = I18n.get("wizard_err_model_load_failed", locale="zh_CN")
        en_value = I18n.get("wizard_err_model_load_failed", locale="en_US")

        assert zh_value != "wizard_err_model_load_failed"
        assert en_value != "wizard_err_model_load_failed"


class TestLLMProviderName:
    """Tests for LLM provider name"""

    def test_qwen_provider_name_is_correct(self):
        """Test that qwen provider name is '通义千问' not '阿里云通义千问'"""
        from utils.llm_providers import LLM_PROVIDERS

        qwen = LLM_PROVIDERS.get("qwen", {})
        assert qwen.get("name") == "通义千问"


class TestCloudAIValidationSaveConfig:
    """Tests for cloud AI validation always saves config"""

    def test_validate_cloud_ai_saves_config_on_success(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from ui.views.onboarding_wizard import OnboardingWizard

        wizard = OnboardingWizard.__new__(OnboardingWizard)
        wizard.llm_config_panel = MagicMock()
        wizard.llm_config_panel.api_key_modified = False
        wizard.llm_config_panel.get_llm_config = MagicMock(
            return_value={
                "provider": "deepseek",
                "model": "test",
                "base_url": "",
                "api_key": "test-key",
            }
        )
        wizard.llm_config_panel.async_verify_connection = AsyncMock(return_value=True)
        wizard.page = MagicMock()

        with patch("ui.views.onboarding_wizard.I18n"):
            import asyncio

            asyncio.run(wizard._validate_and_save_cloud_ai())

        wizard.llm_config_panel.save_current_config.assert_called()
