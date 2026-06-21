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
import pytest


pytestmark = pytest.mark.unit


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

    def test_all_models_have_valid_context(self):
        for pid, provider in LLM_PROVIDERS.items():
            for model in provider["models"]:
                assert "context" in model, f"Provider {pid} model {model.get('id', '?')} missing 'context'"
                assert isinstance(model["context"], int), f"Provider {pid} model {model['id']} context should be int"
                assert model["context"] > 0, f"Provider {pid} model {model['id']} context must be positive"

    def test_all_models_have_valid_id(self):
        for pid, provider in LLM_PROVIDERS.items():
            for model in provider["models"]:
                assert "id" in model, f"Provider {pid} model missing 'id'"
                assert model["id"], f"Provider {pid} has model with empty id"
                assert " " not in model["id"], f"Provider {pid} model id '{model['id']}' contains spaces"

    def test_all_models_have_name(self):
        for pid, provider in LLM_PROVIDERS.items():
            for model in provider["models"]:
                assert "name" in model, f"Provider {pid} model {model.get('id', '?')} missing 'name'"
                assert model["name"], f"Provider {pid} model {model['id']} has empty name"

    def test_all_models_have_tag(self):
        for pid, provider in LLM_PROVIDERS.items():
            for model in provider["models"]:
                assert "tag" in model, f"Provider {pid} model {model.get('id', '?')} missing 'tag'"

    def test_no_duplicate_model_ids_within_provider(self):
        for pid, provider in LLM_PROVIDERS.items():
            model_ids = [m["id"] for m in provider["models"]]
            assert len(model_ids) == len(set(model_ids)), f"Provider {pid} has duplicate model IDs"

    def test_azure_has_empty_models_list(self):
        azure = LLM_PROVIDERS.get("azure")
        assert azure is not None
        assert isinstance(azure["models"], list)
        assert len(azure["models"]) == 0

    def test_custom_has_empty_models_list(self):
        custom = LLM_PROVIDERS.get("custom")
        assert custom is not None
        assert isinstance(custom["models"], list)
        assert len(custom["models"]) == 0


class TestGetDisplayTag:
    def test_string_tag_returned_as_is(self):
        from utils.llm_providers import get_display_tag

        assert get_display_tag("旗舰") == "旗舰"

    def test_list_tag_returns_first_non_internal(self):
        from utils.llm_providers import get_display_tag

        assert get_display_tag(["旗舰", "reasoning"]) == "旗舰"

    def test_list_tag_only_internal_returns_empty(self):
        from utils.llm_providers import get_display_tag

        assert get_display_tag(["reasoning"]) == ""

    def test_list_tag_single_element(self):
        from utils.llm_providers import get_display_tag

        assert get_display_tag(["推荐"]) == "推荐"

    def test_empty_list_returns_empty(self):
        from utils.llm_providers import get_display_tag

        assert get_display_tag([]) == ""


class TestIsRecommendedModel:
    def test_string_tag_recommended(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": "推荐"}) is True

    def test_string_tag_not_recommended(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": "旗舰"}) is False

    def test_list_tag_contains_recommended(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": ["推荐", "reasoning"]}) is True

    def test_list_tag_no_recommended(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": ["旗舰", "reasoning"]}) is False

    def test_no_tag_field(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x"}) is False

    def test_none_tag(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": None}) is False

    def test_empty_string_tag(self):
        from utils.llm_providers import is_recommended_model

        assert is_recommended_model({"id": "x", "tag": ""}) is False


class TestLLMProviderName:
    """Tests for LLM provider name (merged from tests/unit/test_onboarding_wizard.py)."""

    def test_qwen_provider_name_is_correct(self):
        """Test that qwen provider name is '通义千问' not '阿里云通义千问'"""
        qwen = LLM_PROVIDERS.get("qwen", {})
        assert qwen.get("name") == "通义千问"
