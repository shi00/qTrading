"""
Unit Tests for LLM Configuration System
Targets: ConfigHandler (LLM), AIService (LiteLLM), LLMConfigPanel
Coverage Goal: >90%
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_providers import AZURE_DEFAULT_API_VERSION


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


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset all singletons before and after each test"""
    try:
        from utils.config_handler import ConfigHandler

        ConfigHandler._config_cache = None
    except (ImportError, AttributeError):
        pass
    try:
        import services.ai_service as ai_service_module

        ai_service_module.AIService._instance = None
    except (ImportError, AttributeError):
        pass
    yield
    try:
        from utils.config_handler import ConfigHandler

        ConfigHandler._config_cache = None
    except (ImportError, AttributeError):
        pass
    try:
        import services.ai_service as ai_service_module

        ai_service_module.AIService._instance = None
    except (ImportError, AttributeError):
        pass


class TestConfigHandlerLLM:
    """Tests for ConfigHandler LLM configuration methods"""

    def test_save_llm_config_basic(self, isolated_config):
        """Test: save_llm_config saves basic provider config correctly"""
        from utils.config_handler import ConfigHandler

        result = ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="sk-test-key",
        )

        assert result is True

        config = ConfigHandler.load_config()
        assert config.get("llm_provider") == "deepseek"
        assert config.get("llm_model") == "deepseek-v4-flash"
        assert config.get("llm_base_url") == "https://api.deepseek.com"

    def test_save_llm_config_azure(self, isolated_config):
        """Test: save_llm_config saves Azure-specific fields correctly in nested structure"""
        from utils.config_handler import ConfigHandler

        result = ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4",
            base_url="https://myresource.openai.azure.com",
            api_key="azure-key",
            api_version="2024-08-01-preview",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        assert result is True

        config = ConfigHandler.load_config()
        assert config.get("llm_provider") == "azure"

        provider_extras = config.get("llm_provider_extras", {})
        assert "azure" in provider_extras
        assert provider_extras["azure"].get("api_version") == "2024-08-01-preview"
        assert provider_extras["azure"].get("resource_name") == "myresource"
        assert provider_extras["azure"].get("deployment_name") == "gpt-5.4-deployment"

    def test_get_llm_config_defaults(self, isolated_config):
        """Test: get_llm_config returns correct defaults for new installation"""
        from utils.config_handler import ConfigHandler

        with patch("utils.config_handler.keyring.get_password", return_value=None):
            config = ConfigHandler.get_llm_config()

            assert config["provider"] == "deepseek"
            assert config["model"] == "deepseek-v4-flash"
            assert config["base_url"] == "https://api.deepseek.com"
            assert config["api_key"] == ""
            assert config["api_version"] == AZURE_DEFAULT_API_VERSION
            assert config["azure_resource_name"] == ""
            assert config["azure_deployment_name"] == ""

    def test_get_llm_config_azure(self, isolated_config):
        """Test: get_llm_config returns Azure config correctly"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4-deployment",
            base_url="https://myresource.openai.azure.com",
            api_key="azure-key",
            api_version="2024-08-01-preview",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        with patch("utils.config_handler.keyring.get_password", return_value="azure-key"):
            config = ConfigHandler.get_llm_config()

            assert config["provider"] == "azure"
            assert config["model"] == "gpt-5.4-deployment"
            assert config["azure_resource_name"] == "myresource"
            assert config["azure_deployment_name"] == "gpt-5.4-deployment"
            assert config["api_version"] == "2024-08-01-preview"

    def test_get_llm_config_backward_compatibility(self, isolated_config):
        """Test: get_llm_config reads old flat fields for backward compatibility"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_config(
            {
                "llm_provider": "azure",
                "llm_model": "gpt-5.4-deployment",
                "llm_base_url": "https://myresource.openai.azure.com",
                "llm_api_version": "2024-08-01-preview",
                "llm_azure_resource_name": "myresource",
                "llm_azure_deployment_name": "gpt-5.4-deployment",
            }
        )

        with patch("utils.config_handler.keyring.get_password", return_value="azure-key"):
            config = ConfigHandler.get_llm_config()

            assert config["provider"] == "azure"
            assert config["model"] == "gpt-5.4-deployment"
            assert config["azure_resource_name"] == "myresource"
            assert config["azure_deployment_name"] == "gpt-5.4-deployment"
            assert config["api_version"] == "2024-08-01-preview"

    def test_get_llm_provider(self, isolated_config):
        """Test: get_llm_provider returns current provider"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="openai",
            model="gpt-5.4",
            base_url="https://api.openai.com",
            api_key="test-key",
        )

        provider = ConfigHandler.get_llm_provider()
        assert provider == "openai"

    def test_switch_provider_clears_extras(self, isolated_config):
        """Test: switching provider clears old provider extras"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4-deployment",
            base_url="",
            api_key="azure-key",
            api_version="2024-08-01-preview",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        config = ConfigHandler.load_config()
        assert "azure" in config.get("llm_provider_extras", {})

        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="deepseek-key",
        )

        config = ConfigHandler.load_config()
        assert config.get("llm_provider_extras") == {}


class TestAIServiceLiteLLM:
    """Tests for AIService LiteLLM integration"""

    def test_litellm_not_available_graceful_degradation(self, isolated_config):
        """Test: AIService gracefully handles missing LiteLLM"""
        import services.ai_service as ai_service_module
        from services.ai_service import AIService

        original_litellm = ai_service_module.LITELLM_AVAILABLE
        ai_service_module.LITELLM_AVAILABLE = False
        AIService._instance = None

        try:
            service = AIService()
            assert service._is_cloud_configured is False
        finally:
            ai_service_module.LITELLM_AVAILABLE = original_litellm
            AIService._instance = None

    def test_setup_client_azure_missing_resource_name(self, isolated_config):
        """Test: _setup_client handles Azure missing resource name"""
        from services.ai_service import AIService
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4",
            base_url="",
            api_key="azure-key",
            azure_resource_name="",
            azure_deployment_name="gpt-5.4-deployment",
        )

        AIService._instance = None
        service = AIService()

        assert service._is_cloud_configured is False

    def test_setup_client_azure_missing_deployment_name(self, isolated_config):
        """Test: _setup_client handles Azure missing deployment name"""
        from services.ai_service import AIService
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="",
            base_url="",
            api_key="azure-key",
            azure_resource_name="myresource",
            azure_deployment_name="",
        )

        AIService._instance = None
        service = AIService()

        assert service._is_cloud_configured is False

    def test_setup_client_azure_builds_correct_url(self, isolated_config):
        """Test: _setup_client builds correct Azure URL"""
        from services.ai_service import AIService
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4-deployment",
            base_url="",
            api_key="azure-key",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        AIService._instance = None

        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            service = AIService()
            assert service._litellm_config is not None
            assert "myresource.openai.azure.com" in service._litellm_config.get("base_url", "")

    def test_setup_client_missing_api_key(self, isolated_config):
        """Test: _setup_client handles missing API key"""
        from services.ai_service import AIService
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="",
        )

        AIService._instance = None
        service = AIService()

        assert service._is_cloud_configured is False

    @pytest.mark.asyncio
    async def test_chat_completion_returns_response(self, isolated_config):
        """Test: _chat_completion returns valid response"""
        from services.ai_service import AIService
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="test-key",
        )

        AIService._instance = None

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "Test response"}'

        with patch("services.ai_service.LITELLM_AVAILABLE", True):
            service = AIService()
            service._is_cloud_configured = True
            service._litellm_config = {
                "provider": "deepseek",
                "model": "deepseek-v4-flash",
                "api_key": "test-key",
                "base_url": "https://api.deepseek.com",
            }

            with patch("services.ai_service.acompletion", new_callable=AsyncMock) as mock_acompletion:
                mock_acompletion.return_value = mock_response

                result = await service._chat_completion([{"role": "user", "content": "Hello"}], json_mode=True)

                assert result == {"result": "Test response"}

    @pytest.mark.asyncio
    async def test_test_connection_missing_api_key(self, isolated_config):
        """Test: test_connection returns failure for missing API key"""
        from services.ai_service import AIService

        result = await AIService.test_connection(api_key="")
        assert result["success"] is False
        assert "API Key is empty" in result["message"]

    @pytest.mark.asyncio
    async def test_test_connection_litellm_not_available(self, isolated_config):
        """Test: test_connection returns failure when LiteLLM not available"""
        import services.ai_service as ai_service_module
        from services.ai_service import AIService

        original_litellm = ai_service_module.LITELLM_AVAILABLE
        ai_service_module.LITELLM_AVAILABLE = False

        try:
            result = await AIService.test_connection(api_key="test-key", model="gpt-5.4")
            assert result["success"] is False
            assert "LiteLLM not installed" in result["message"]
        finally:
            ai_service_module.LITELLM_AVAILABLE = original_litellm


class TestLLMConfigPanel:
    """Tests for LLMConfigPanel UI component"""

    def test_get_current_config_standard_provider(self, isolated_config):
        """Test: get_current_config returns correct config for standard provider"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="test-key",
        )

        panel = LLMConfigPanel(show_save_button=False, compact=True)

        config = panel.get_current_config()

        assert config["provider"] == "deepseek"
        assert config["model"] == "deepseek-v4-flash"
        assert config["base_url"] == "https://api.deepseek.com"

    def test_get_current_config_azure_provider(self, isolated_config):
        """Test: get_current_config returns correct config for Azure provider

        Note: After refactoring, UI layer no longer constructs Azure URL.
        The azure_resource_name is passed to service layer for URL assembly.
        """
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4-deployment",
            base_url="https://myresource.openai.azure.com",
            api_key="azure-key",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        panel = LLMConfigPanel(show_save_button=False, compact=True)

        panel._current_provider = "azure"
        panel._is_azure = True
        panel.azure_resource_input.value = "myresource"
        panel.azure_deployment_input.value = "gpt-5.4-deployment"
        panel.azure_version_input.value = "2024-08-01-preview"
        panel.api_key_input.value = "azure-key"

        config = panel.get_current_config()

        assert config["provider"] == "azure"
        assert config["model"] == "gpt-5.4-deployment"
        assert config["azure_resource_name"] == "myresource"
        assert config["azure_deployment_name"] == "gpt-5.4-deployment"
        assert config["api_version"] == "2024-08-01-preview"

    def test_save_current_config(self, isolated_config):
        """Test: save_current_config persists config correctly"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel
        from utils.config_handler import ConfigHandler

        panel = LLMConfigPanel(show_save_button=False, compact=True)

        panel._current_provider = "openai"
        panel.model_dropdown.value = "gpt-5.4"
        panel.base_url_input.value = "https://api.openai.com"
        panel.api_key_input.value = "openai-key"

        result = panel.save_current_config()

        assert result is True

        config = ConfigHandler.get_llm_config()
        assert config["provider"] == "openai"
        assert config["model"] == "gpt-5.4"

    def test_show_save_button_parameter(self, isolated_config):
        """Test: show_save_button parameter controls button visibility"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel_with_button = LLMConfigPanel(show_save_button=True)
        assert panel_with_button.save_button.visible is True

        panel_without_button = LLMConfigPanel(show_save_button=False)
        assert panel_without_button.save_button.visible is False

    def test_compact_parameter(self, isolated_config):
        """Test: compact parameter adjusts layout spacing"""
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        panel_compact = LLMConfigPanel(compact=True)
        assert panel_compact._compact is True

        panel_normal = LLMConfigPanel(compact=False)
        assert panel_normal._compact is False


class TestLLMProviders:
    """Tests for LLM_PROVIDERS configuration data"""

    def test_provider_data_structure(self):
        """Test: LLM_PROVIDERS has correct structure for all providers"""
        from utils.llm_providers import LLM_PROVIDERS

        required_keys = ["name", "base_url", "models", "key_prefix"]

        for provider_id, provider_data in LLM_PROVIDERS.items():
            for key in required_keys:
                assert key in provider_data, f"Provider {provider_id} missing key {key}"

    def test_azure_provider_config(self):
        """Test: Azure provider has azure_config flag"""
        from utils.llm_providers import LLM_PROVIDERS

        azure_config = LLM_PROVIDERS.get("azure")
        assert azure_config is not None
        assert azure_config.get("azure_config") is True
        assert azure_config.get("base_url") == ""

    def test_deepseek_provider_models(self):
        """Test: DeepSeek provider has expected models"""
        from utils.llm_providers import LLM_PROVIDERS

        deepseek = LLM_PROVIDERS.get("deepseek")
        assert deepseek is not None

        models = deepseek.get("models", [])
        model_ids = [m.get("id") for m in models]

        assert "deepseek-v4-pro" in model_ids
        assert "deepseek-v4-flash" in model_ids

    def test_provider_categories(self):
        """Test: PROVIDER_CATEGORIES contains all expected providers"""
        from utils.llm_providers import LLM_PROVIDERS, PROVIDER_CATEGORIES

        all_categorized = (
            PROVIDER_CATEGORIES.get("domestic", [])
            + PROVIDER_CATEGORIES.get("international", [])
            + PROVIDER_CATEGORIES.get("custom", [])
        )

        for provider_id in LLM_PROVIDERS:
            assert provider_id in all_categorized, f"Provider {provider_id} not in any category"


class TestIntegration:
    """Integration tests for LLM configuration flow"""

    def test_full_config_roundtrip(self, isolated_config):
        """Test: Config can be saved and retrieved correctly"""
        from utils.config_handler import ConfigHandler

        original_config = {
            "provider": "anthropic",
            "model": "claude-opus-4-7",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test",
        }

        ConfigHandler.save_llm_config(
            provider=original_config["provider"],
            model=original_config["model"],
            base_url=original_config["base_url"],
            api_key=original_config["api_key"],
        )

        with patch(
            "utils.config_handler.keyring.get_password",
            return_value=original_config["api_key"],
        ):
            retrieved = ConfigHandler.get_llm_config()

            assert retrieved["provider"] == original_config["provider"]
            assert retrieved["model"] == original_config["model"]
            assert retrieved["base_url"] == original_config["base_url"]

    def test_azure_full_config_roundtrip(self, isolated_config):
        """Test: Azure config can be saved and retrieved correctly"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="azure",
            model="gpt-5.4-deployment",
            base_url="https://myresource.openai.azure.com",
            api_key="azure-test-key",
            api_version="2024-08-01-preview",
            azure_resource_name="myresource",
            azure_deployment_name="gpt-5.4-deployment",
        )

        with patch("utils.config_handler.keyring.get_password", return_value="azure-test-key"):
            config = ConfigHandler.get_llm_config()

            assert config["provider"] == "azure"
            assert config["model"] == "gpt-5.4-deployment"
            assert config["azure_resource_name"] == "myresource"
            assert config["azure_deployment_name"] == "gpt-5.4-deployment"
            assert config["api_version"] == "2024-08-01-preview"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
