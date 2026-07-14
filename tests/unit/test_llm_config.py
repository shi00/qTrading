"""
Unit Tests for LLM Configuration System
Targets: ConfigHandler (LLM), AIService (LiteLLM), LLM Providers
Coverage Goal: >90%
"""

import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.llm_providers import AZURE_DEFAULT_API_VERSION

pytestmark = pytest.mark.unit


@pytest.fixture
def isolated_config(tmp_path):
    """Create an isolated config file for each test with cache cleared"""
    import utils.config_handler as config_module
    from utils.config_handler import ConfigHandler

    unique_name = f"test_config_{uuid.uuid4().hex}.json"
    test_config_file = str(tmp_path / unique_name)
    original_config_file = config_module.CONFIG_FILE
    config_module.CONFIG_FILE = test_config_file

    ConfigHandler._clear_cache()

    if os.path.exists(test_config_file):
        os.remove(test_config_file)

    yield test_config_file

    ConfigHandler._clear_cache()
    config_module.CONFIG_FILE = original_config_file


@pytest.fixture(autouse=True)
def mock_keyring():
    """Mock keyring with an in-memory dictionary to isolate tests from OS keyring."""
    keyring_store = {}

    def get_password(service_name, username):
        return keyring_store.get((service_name, username))

    def set_password(service_name, username, password):
        keyring_store[(service_name, username)] = password

    def delete_password(service_name, username):
        if (service_name, username) in keyring_store:
            del keyring_store[(service_name, username)]

    with (
        patch("utils.config_handler.keyring.get_password", side_effect=get_password),
        patch("utils.config_handler.keyring.set_password", side_effect=set_password),
        patch("utils.config_handler.keyring.delete_password", side_effect=delete_password),
    ):
        yield keyring_store


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

    def test_get_provider_credential_fallback_to_primary_key(self, isolated_config):
        """Test: provider-specific credential lookup falls back to primary API key if not found."""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="primary-key",
        )

        cred = ConfigHandler.get_provider_credential("qwen")

        # When no provider-specific key exists, it falls back to the global primary key
        assert cred["api_key"] == "primary-key"
        assert cred["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def test_save_llm_config_strips_values_and_clears_blank_key(self, isolated_config, mock_keyring):
        """Test: save_llm_config normalizes user input and treats blank key as clear."""
        from utils.config_handler import ConfigHandler, KEYRING_SERVICE_NAME

        ConfigHandler.save_llm_config(
            provider=" deepseek ",
            model=" deepseek-v4-flash ",
            base_url=" https://api.deepseek.com ",
            api_key=" sk-test ",
        )
        assert mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key")] == "sk-test"

        ConfigHandler.save_llm_config(
            provider=" deepseek ",
            model=" deepseek-v4-flash ",
            base_url=" https://api.deepseek.com ",
            api_key="   ",
        )

        config = ConfigHandler.load_config()
        assert config.get("llm_provider") == "deepseek"
        assert config.get("llm_model") == "deepseek-v4-flash"
        assert config.get("llm_base_url") == "https://api.deepseek.com"
        assert (KEYRING_SERVICE_NAME, "ai_api_key") not in mock_keyring

    def test_save_llm_config_preserves_key_when_none(self, isolated_config, mock_keyring):
        """Test: save_llm_config with api_key=None preserves the existing key.

        This is the fix for the C3 bug where api_key or "" converted None to "",
        causing accidental key deletion when the user only changed provider/model
        without touching the API key input.
        """
        from utils.config_handler import ConfigHandler, KEYRING_SERVICE_NAME

        # First save: set up a key
        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-flash",
            base_url="https://api.deepseek.com",
            api_key="sk-original-key",
        )
        assert mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key")] == "sk-original-key"

        # Second save: change model but pass api_key=None (user didn't touch key input)
        ConfigHandler.save_llm_config(
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key=None,
        )

        # Key should be preserved
        assert mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key")] == "sk-original-key"
        config = ConfigHandler.load_config()
        assert config.get("llm_model") == "deepseek-v4-pro"


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


class TestCustomModelsStructureConsistency:
    """验证 custom_models 返回结构与 Mock 一致，防止 Mock 与真实行为偏差"""

    def test_get_llm_config_returns_list_not_dict(self, isolated_config):
        """custom_models 应返回 list[str] 而非嵌套 dict"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_config(
            {
                "llm_custom_models": {"qwen": ["qwen-max", "qwen-plus"]},
                "llm_provider": "deepseek",
            }
        )

        llm_config = ConfigHandler.get_llm_config()
        custom_models = llm_config.get("custom_models", {})

        assert isinstance(custom_models.get("qwen"), list)
        assert all(isinstance(m, str) for m in custom_models.get("qwen", []))

    def test_get_llm_config_custom_models_is_deep_copy(self, isolated_config):
        """custom_models 应返回深拷贝，修改不影响缓存"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_config(
            {
                "llm_custom_models": {"qwen": ["qwen-max"]},
            }
        )

        config1 = ConfigHandler.get_llm_config()
        config1["custom_models"]["qwen"].append("qwen-turbo")

        config2 = ConfigHandler.get_llm_config()
        assert "qwen-turbo" not in config2["custom_models"]["qwen"]


class TestProviderCredentialRoundtrip:
    """验证 provider_credential 读写一致性"""

    def test_save_and_get_provider_credential_roundtrip(self, isolated_config, mock_keyring):
        """保存后读取应返回相同凭证"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="sk-test-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            models=["qwen-max"],
        )

        cred = ConfigHandler.get_provider_credential("qwen")

        assert cred["api_key"] == "sk-test-key"
        assert cred["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert "qwen-max" in cred.get("models", [])

    def test_get_provider_credential_fallback_to_default_base_url(self, isolated_config):
        """未配置 base_url 时返回供应商默认值"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_provider_credential(provider="qwen", api_key="sk-test")

        cred = ConfigHandler.get_provider_credential("qwen")

        assert cred["base_url"] is not None
        assert "dashscope" in cred["base_url"] or cred["base_url"] == ""

    def test_validate_failover_credentials_with_real_config(self, isolated_config, mock_keyring):
        """使用真实 ConfigHandler 验证 failover 凭证校验"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_config(
            {
                "llm_failover_models": ["qwen/qwen-max"],
            }
        )
        ConfigHandler.save_provider_credential(
            provider="qwen",
            api_key="sk-valid-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            models=["qwen-max"],
        )

        missing = ConfigHandler.validate_failover_credentials()
        assert "qwen" not in missing

    def test_validate_failover_credentials_missing_key(self, isolated_config, mock_keyring):
        """缺少 API Key 的供应商应出现在 missing 列表中"""
        from utils.config_handler import ConfigHandler

        ConfigHandler.save_config(
            {
                "llm_failover_models": ["openai/gpt-4o"],
            }
        )

        missing = ConfigHandler.validate_failover_credentials()
        assert "openai" in missing


class TestProviderCredentialFallbackToGlobal:
    """验证 get_provider_credential 的 fallback_to_global 参数"""

    def test_fallback_to_global_returns_primary_key(self, isolated_config, mock_keyring):
        """fallback_to_global=True 时回退到全局 API Key"""
        from utils.config_handler import ConfigHandler, KEYRING_SERVICE_NAME

        mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key")] = "global-key"

        cred = ConfigHandler.get_provider_credential("qwen", fallback_to_global=True)
        assert cred["api_key"] == "global-key"

    def test_no_fallback_returns_none_for_missing_provider_key(self, isolated_config, mock_keyring):
        """fallback_to_global=False 时不回退，返回 None"""
        from utils.config_handler import ConfigHandler, KEYRING_SERVICE_NAME

        mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key")] = "global-key"

        cred = ConfigHandler.get_provider_credential("qwen", fallback_to_global=False)
        assert cred["api_key"] is None

    def test_no_fallback_returns_provider_specific_key(self, isolated_config, mock_keyring):
        """fallback_to_global=False 时仍返回供应商专属 Key"""
        from utils.config_handler import ConfigHandler, KEYRING_SERVICE_NAME

        mock_keyring[(KEYRING_SERVICE_NAME, "ai_api_key_qwen")] = "qwen-specific-key"

        cred = ConfigHandler.get_provider_credential("qwen", fallback_to_global=False)
        assert cred["api_key"] == "qwen-specific-key"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
