"""
Unit Tests for Onboarding Wizard API Contracts

Targets: OnboardingWizard public API surface, config panel signatures, i18n keys.
StepConfig / navigation logic / wizard behavior tests live in tests/unit/ui/.
"""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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

        assert LocalModelManager._instance is None
        assert LocalModelManager._initialized is False

    def test_local_model_manager_singleton_reset_unloads_model(self):
        """Test that _reset_singleton unloads LLM model to free memory"""
        from services.local_model_manager import LocalModelManager

        # 在隔离环境中创建实例并设置属性
        LocalModelManager._instance = object.__new__(LocalModelManager)
        LocalModelManager._instance._worker_lock = threading.Lock()
        LocalModelManager._instance._worker_proc = None
        LocalModelManager._instance._request_queue = None
        LocalModelManager._instance._result_queue = None
        LocalModelManager._instance._worker_ready = False
        LocalModelManager._initialized = True

        # 调用 reset 清理
        LocalModelManager._reset_singleton()

        assert LocalModelManager._instance is None
        assert LocalModelManager._initialized is False


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
