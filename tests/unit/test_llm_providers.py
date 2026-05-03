from unittest.mock import patch
from pathlib import Path

from utils.llm_providers import (
    get_provider_by_id,
    get_model_info,
    get_all_providers,
    get_providers_by_category,
    get_provider_icon_path,
    get_provider_icon,
    LLM_PROVIDERS,
    PROVIDER_CATEGORIES,
    AZURE_DEFAULT_API_VERSION,
    AZURE_API_VERSIONS,
)


class TestGetProviderById:
    def test_existing_provider(self):
        provider = get_provider_by_id("deepseek")
        assert provider is not None
        assert "name" in provider

    def test_nonexistent_provider_returns_custom(self):
        provider = get_provider_by_id("nonexistent_provider")
        assert provider is not None
        assert provider.get("id") == "custom" or "name" in provider

    def test_custom_provider(self):
        provider = get_provider_by_id("custom")
        assert provider is not None


class TestGetModelInfo:
    def test_existing_model(self):
        deepseek = get_provider_by_id("deepseek")
        models = deepseek.get("models", [])
        if models:
            model = get_model_info("deepseek", models[0]["id"])
            assert "id" in model

    def test_nonexistent_model(self):
        model = get_model_info("deepseek", "nonexistent_model")
        assert model["id"] == "nonexistent_model"

    def test_nonexistent_provider(self):
        model = get_model_info("nonexistent", "some_model")
        assert model["id"] == "some_model"


class TestGetAllProviders:
    def test_returns_dict(self):
        providers = get_all_providers()
        assert isinstance(providers, dict)

    def test_has_deepseek(self):
        providers = get_all_providers()
        assert "deepseek" in providers

    def test_has_custom(self):
        providers = get_all_providers()
        assert "custom" in providers


class TestGetProvidersByCategory:
    def test_existing_category(self):
        for cat in PROVIDER_CATEGORIES:
            providers = get_providers_by_category(cat)
            assert isinstance(providers, list)

    def test_nonexistent_category(self):
        providers = get_providers_by_category("nonexistent_category")
        assert providers == []


class TestGetProviderIconPath:
    def test_nonexistent_icon_returns_default(self):
        result = get_provider_icon_path("nonexistent_icon.png")
        assert "custom.png" in result

    @patch.object(Path, "exists", return_value=True)
    def test_existing_icon(self, mock_exists):
        result = get_provider_icon_path("deepseek.png")
        assert "deepseek.png" in result


class TestGetProviderIcon:
    def test_deepseek_icon(self):
        result = get_provider_icon("deepseek")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_nonexistent_provider_icon(self):
        result = get_provider_icon("nonexistent_provider")
        assert isinstance(result, str)


class TestLLMProviderConstants:
    def test_azure_default_api_version(self):
        assert AZURE_DEFAULT_API_VERSION == "2025-04-01-preview"

    def test_azure_api_versions_is_list(self):
        assert isinstance(AZURE_API_VERSIONS, list)
        assert len(AZURE_API_VERSIONS) >= 1

    def test_llm_providers_has_deepseek(self):
        assert "deepseek" in LLM_PROVIDERS

    def test_llm_providers_has_openai(self):
        assert "openai" in LLM_PROVIDERS

    def test_llm_providers_has_custom(self):
        assert "custom" in LLM_PROVIDERS

    def test_provider_has_required_keys(self):
        required_keys = {"name", "base_url", "models", "icon"}
        for pid, provider in LLM_PROVIDERS.items():
            missing = required_keys - set(provider.keys())
            assert not missing, f"Provider {pid} missing keys: {missing}"

    def test_provider_models_is_list(self):
        for pid, provider in LLM_PROVIDERS.items():
            assert isinstance(provider["models"], list), f"Provider {pid} models should be a list"

    def test_provider_model_has_id(self):
        for pid, provider in LLM_PROVIDERS.items():
            for model in provider["models"]:
                assert "id" in model, f"Provider {pid} model missing 'id'"

    def test_provider_categories_has_domestic(self):
        assert "domestic" in PROVIDER_CATEGORIES

    def test_provider_categories_has_international(self):
        assert "international" in PROVIDER_CATEGORIES

    def test_provider_categories_has_custom(self):
        assert "custom" in PROVIDER_CATEGORIES
