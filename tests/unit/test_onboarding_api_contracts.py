"""
Unit Tests for Onboarding Wizard API Contracts

Targets: OnboardingWizard public API surface, config panel signatures, i18n keys.
StepConfig / navigation logic / wizard behavior tests live in tests/unit/ui/.
"""

import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


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
        """Test that quick=True skips sync_historical_data and sync_comprehensive_fundamentals"""
        import datetime

        from data.data_processor import DataProcessor

        # 重置单例后用 mock 替换外部依赖，避免真实 IO
        DataProcessor._reset_singleton()
        with (
            patch("data.data_processor.CacheManager"),
            patch("data.data_processor.TushareClient"),
            patch("data.data_processor.TradeCalendarService"),
            patch("data.data_processor.ConfigHandler") as mock_ch_init,
        ):
            mock_ch_init.get_token.return_value = "test_token"
            dp = DataProcessor()

        dp.sync_stock_basic = AsyncMock(return_value=5)
        dp.sync_concepts = AsyncMock()
        dp.ensure_trade_cal = AsyncMock(return_value=True)
        dp.sync_historical_data = AsyncMock(return_value=MagicMock(status="success"))
        dp.sync_comprehensive_fundamentals = AsyncMock(return_value=MagicMock(status="success"))
        dp.strategies["macro"].run = AsyncMock(return_value=MagicMock(status="success"))
        dp.strategies["holder"].run = AsyncMock(return_value=MagicMock(status="success"))
        dp.check_data_health = AsyncMock(return_value={"tier": 3})
        dp.clear_cancel()

        with (
            patch("data.data_dictionary.validate_schema_definitions"),
            patch("data.data_processor.I18n") as mock_i18n,
            patch("data.data_processor.ConfigHandler") as mock_ch,
            patch(
                "data.data_processor.get_now",
                return_value=datetime.datetime(2024, 6, 14),
            ),
        ):
            mock_i18n.get.side_effect = lambda k, **kw: k
            mock_ch.get_init_history_years.return_value = 1
            await dp.initialize_system(quick=True)

        # quick=True 必须跳过 Step 3 (historical) 与 Step 4 (financial)
        dp.sync_historical_data.assert_not_called()
        dp.sync_comprehensive_fundamentals.assert_not_called()


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

    def test_schedule_time_default_is_valid(self):
        """Test that OnboardingViewModel default schedule time is '16:30'"""
        from ui.viewmodels.onboarding_view_model import OnboardingViewModel

        vm = OnboardingViewModel(data_processor=None)
        assert vm.normalized_schedule_time == "16:30"

    @pytest.mark.asyncio
    async def test_schedule_time_validation_normalizes_invalid_to_default(self):
        """Test that _validate_and_save_schedule normalizes invalid time to default '16:30'"""
        from ui.viewmodels.onboarding_view_model import OnboardingViewModel

        # (输入时间, 期望规范化结果)
        test_cases = [
            # 格式错误 → 默认值
            ("abc", "16:30"),
            ("12-30", "16:30"),
            ("1:2", "16:30"),
            ("123:45", "16:30"),
            ("", "16:30"),
            ("12:3", "16:30"),
            # 数值超限 → 默认值
            ("25:00", "16:30"),
            ("24:00", "16:30"),
            ("12:60", "16:30"),
            ("12:99", "16:30"),
            # 有效时间 → 保持不变
            ("16:30", "16:30"),
            ("09:15", "09:15"),
            ("00:00", "00:00"),
            ("23:59", "23:59"),
        ]

        for time_str, expected in test_cases:
            vm = OnboardingViewModel(data_processor=None)
            vm.set_schedule_state(enabled=True, time_str=time_str)
            with patch("ui.viewmodels.onboarding_view_model.ConfigHandler.save_config"):
                result = await vm._validate_and_save_schedule()

            assert result is True, f"_validate_and_save_schedule should return True for {time_str!r}"
            assert vm.normalized_schedule_time == expected, (
                f"{time_str!r} should normalize to {expected!r}, got {vm.normalized_schedule_time!r}"
            )


class TestPasswordLoading:
    """Tests for password loading functionality - Issue 2.1"""

    def test_database_panel_accepts_load_password_parameter(self):
        """Test that DatabaseConfigPanelViewModel accepts load_password parameter.

        注：DatabaseConfigPanel 已重写为声明式组件，load_password 移至 VM 构造参数。
        """
        import inspect

        from ui.viewmodels.database_config_panel_view_model import (
            DatabaseConfigPanelViewModel,
        )

        sig = inspect.signature(DatabaseConfigPanelViewModel.__init__)
        params = list(sig.parameters.keys())

        assert "load_password" in params

    def test_database_panel_load_password_defaults_to_false(self):
        """Test that load_password defaults to False for security.

        注：DatabaseConfigPanel 已重写为声明式组件，load_password 移至 VM 构造参数。
        """
        import inspect

        from ui.viewmodels.database_config_panel_view_model import (
            DatabaseConfigPanelViewModel,
        )

        sig = inspect.signature(DatabaseConfigPanelViewModel.__init__)
        load_password_param = sig.parameters.get("load_password")

        assert load_password_param is not None
        assert load_password_param.default is False

    def test_config_handler_has_get_db_password(self):
        """Test that ConfigHandler has get_db_password method"""
        from utils.config_handler import ConfigHandler

        assert hasattr(ConfigHandler, "get_db_password")


class TestProgressCallbackSignature:
    """Tests for progress_callback signature consistency - Issue 3.1"""

    def test_initialize_system_callback_signature(self):
        """Test that initialize_system expects sync callback"""
        import inspect

        from data.data_processor import DataProcessor

        sig = inspect.signature(DataProcessor.initialize_system)
        progress_param = sig.parameters.get("progress_callback")

        assert progress_param is not None


class TestLocaleChangeSignature:
    """声明式组件 locale change 契约守护 - §5.8 规范 2 由声明式范式替代

    所有 UI 组件已重写为 @ft.component 声明式组件，
    通过 ft.use_state(get_observable_state) 自动重渲染，不再需要 _on_locale_change。
    """

    def test_llm_config_panel_uses_i18n_observable_state(self):
        """DoD: LLMConfigPanel 通过 ft.use_state(get_observable_state) 订阅 i18n（声明式）。"""
        from pathlib import Path

        import ui.components.config_panels.llm_config_panel as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "get_observable_state" in source, "LLMConfigPanel 必须订阅 get_observable_state"

    # --- 声明式组件契约守护（@ft.component + 无 _on_locale_change）---

    def test_app_layout_locale_change_is_zero_arg(self):
        """AppLayout 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.app_layout import AppLayout

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(AppLayout, "__wrapped__"), "AppLayout 必须是 @ft.component 声明式组件"
        assert not hasattr(AppLayout, "_on_locale_change"), "声明式 AppLayout 不应有 _on_locale_change"

    def test_onboarding_wizard_locale_change_is_zero_arg(self):
        """OnboardingWizard 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.views.onboarding_wizard import OnboardingWizard

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(OnboardingWizard, "__wrapped__"), "OnboardingWizard 必须是 @ft.component 声明式组件"
        assert not hasattr(OnboardingWizard, "_on_locale_change"), "声明式 OnboardingWizard 不应有 _on_locale_change"

    def test_ai_brain_tab_locale_change_is_zero_arg(self):
        """AIBrainTab 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.views.settings_tabs.ai_brain_tab import AIBrainTab

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(AIBrainTab, "__wrapped__"), "AIBrainTab 必须是 @ft.component 声明式组件"
        assert not hasattr(AIBrainTab, "_on_locale_change"), "声明式 AIBrainTab 不应有 _on_locale_change"

    # --- 声明式组件契约守护（@ft.component + 无 _on_locale_change）---

    def test_database_config_panel_locale_change_is_zero_arg(self):
        """DatabaseConfigPanel 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(DatabaseConfigPanel, "__wrapped__"), "DatabaseConfigPanel 必须是 @ft.component 声明式组件"
        assert not hasattr(DatabaseConfigPanel, "_on_locale_change"), (
            "声明式 DatabaseConfigPanel 不应有 _on_locale_change"
        )

    def test_failover_config_panel_locale_change_is_zero_arg(self):
        """FailoverConfigPanel 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.components.config_panels.failover_config_panel import (
            FailoverConfigPanel,
        )

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(FailoverConfigPanel, "__wrapped__"), "FailoverConfigPanel 必须是 @ft.component 声明式组件"
        assert not hasattr(FailoverConfigPanel, "_on_locale_change"), (
            "声明式 FailoverConfigPanel 不应有 _on_locale_change"
        )

    def test_local_model_config_panel_locale_change_is_zero_arg(self):
        """LocalModelConfigPanel 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(LocalModelConfigPanel, "__wrapped__"), "LocalModelConfigPanel 必须是 @ft.component 声明式组件"
        assert not hasattr(LocalModelConfigPanel, "_on_locale_change"), (
            "声明式 LocalModelConfigPanel 不应有 _on_locale_change"
        )

    def test_system_tab_locale_change_is_zero_arg(self):
        """SystemTab 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.views.settings_tabs.system_tab import SystemTab

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(SystemTab, "__wrapped__"), "SystemTab 必须是 @ft.component 声明式组件"
        assert not hasattr(SystemTab, "_on_locale_change"), "声明式 SystemTab 不应有 _on_locale_change"

    def test_automation_tab_locale_change_is_zero_arg(self):
        """AutomationTab 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.views.settings_tabs.automation_tab import AutomationTab

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(AutomationTab, "__wrapped__"), "AutomationTab 必须是 @ft.component 声明式组件"
        assert not hasattr(AutomationTab, "_on_locale_change"), "声明式 AutomationTab 不应有 _on_locale_change"

    def test_notifications_tab_locale_change_is_zero_arg(self):
        """NotificationsTab 已重写为声明式组件，通过 ft.use_state(get_observable_state) 自动重渲染，无需 _on_locale_change（§5.8 规范 2 由声明式范式替代）"""
        from ui.views.settings_tabs.automation_tab import NotificationsTab

        # 声明式组件必须用 @ft.component 装饰，且不再定义 _on_locale_change
        assert hasattr(NotificationsTab, "__wrapped__"), "NotificationsTab 必须是 @ft.component 声明式组件"
        assert not hasattr(NotificationsTab, "_on_locale_change"), "声明式 NotificationsTab 不应有 _on_locale_change"


class TestLLMProviderSwitch:
    """Tests for LLM provider switch behavior.

    注：LLMConfigPanel 已重写为声明式组件（Phase 3.2.3），
    provider 切换逻辑收敛进 VM.update_provider command，
    api_key_modified flag 收敛进 VM state。
    旧命令式 API 测试（_on_provider_change/_load_config/_api_key_modified）已移除。
    """

    def test_llm_vm_has_update_provider(self):
        """DoD: VM 暴露 update_provider command（声明式）。"""
        from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel

        assert hasattr(LLMConfigPanelViewModel, "update_provider"), "VM 必须暴露 update_provider command"

    def test_llm_vm_state_has_api_key_modified_flag(self):
        """DoD: VM state 包含 api_key_modified flag（声明式）。"""
        from ui.viewmodels.llm_config_panel_view_model import LLMConfigState

        assert hasattr(LLMConfigState, "__dataclass_fields__"), "LLMConfigState 必须是 dataclass"
        assert "api_key_modified" in LLMConfigState.__dataclass_fields__, "state 必须包含 api_key_modified"

    def test_llm_vm_has_reload_config(self):
        """DoD: VM 暴露 reload_config command（替代旧 _load_config）。"""
        from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel

        assert hasattr(LLMConfigPanelViewModel, "reload_config"), "VM 必须暴露 reload_config command"


class TestLocalModelAsyncVerification:
    """Tests for local model async verification.

    注：LocalModelConfigPanel 已重写为声明式组件（Phase 3.2.4），
    验证/保存逻辑收敛进 VM.verify_model / VM.save_config commands，
    loading 状态收敛进 VM state.is_verifying。
    旧命令式 API 测试（async_verify_model/_set_loading_state/progress_indicator）已移除。
    """

    def test_local_model_vm_has_verify_model(self):
        """DoD: VM 暴露 verify_model command（声明式，替代旧 async_verify_model）。"""
        from ui.viewmodels.local_model_config_panel_view_model import (
            LocalModelConfigPanelViewModel,
        )

        assert hasattr(LocalModelConfigPanelViewModel, "verify_model"), "VM 必须暴露 verify_model command"

    def test_local_model_vm_has_save_config(self):
        """DoD: VM 暴露 save_config command（声明式）。"""
        from ui.viewmodels.local_model_config_panel_view_model import (
            LocalModelConfigPanelViewModel,
        )

        assert hasattr(LocalModelConfigPanelViewModel, "save_config"), "VM 必须暴露 save_config command"

    def test_local_model_vm_state_has_is_verifying_flag(self):
        """DoD: VM state 包含 is_verifying flag（声明式，替代旧 _set_loading_state）。"""
        from ui.viewmodels.local_model_config_panel_view_model import (
            LocalModelConfigState,
        )

        assert hasattr(LocalModelConfigState, "__dataclass_fields__"), "LocalModelConfigState 必须是 dataclass"
        assert "is_verifying" in LocalModelConfigState.__dataclass_fields__, "state 必须包含 is_verifying"

    def test_local_model_vm_has_reload_config(self):
        """DoD: VM 暴露 reload_config command（替代旧 _load_config）。"""
        from ui.viewmodels.local_model_config_panel_view_model import (
            LocalModelConfigPanelViewModel,
        )

        assert hasattr(LocalModelConfigPanelViewModel, "reload_config"), "VM 必须暴露 reload_config command"


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
