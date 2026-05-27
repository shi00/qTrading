from unittest.mock import MagicMock, patch

import pytest

from ui.components.config_panels.failover_config_panel import (
    FailoverConfigPanel,
    FailoverItem,
    ProviderCredentialDialog,
)


MOCK_PROVIDERS = {
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "models": [{"id": "deepseek-chat", "name": "DeepSeek Chat", "tag": "推荐"}],
        "console_url": "https://platform.deepseek.com",
    },
    "qwen": {
        "name": "通义千问",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "models": [{"id": "qwen-plus", "name": "Qwen Plus"}],
    },
    "custom": {
        "name": "Custom",
        "base_url": "",
        "models": [],
    },
}


@pytest.fixture
def mock_config_handler():
    with patch("ui.components.config_panels.failover_config_panel.ConfigHandler") as m:
        m.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen-plus"],
            "llm_provider": "deepseek",
        }
        m.get_provider_credential.return_value = {
            "api_key": "sk-test-qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "models": ["qwen-plus"],
        }
        m.save_provider_credential.return_value = True
        m.save_config.return_value = True
        m.validate_failover_credentials.return_value = []
        yield m


@pytest.fixture
def mock_i18n():
    with patch("ui.components.config_panels.failover_config_panel.I18n") as m:
        m.get.side_effect = lambda key, *a, **kw: key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_llm_providers():
    with patch("ui.components.config_panels.failover_config_panel.LLM_PROVIDERS", MOCK_PROVIDERS):
        yield


@pytest.fixture
def mock_app_colors():
    with patch("ui.components.config_panels.failover_config_panel.AppColors") as m:
        m.SUCCESS = "#4CAF50"
        m.WARNING = "#FF9800"
        m.ERROR = "#F44336"
        m.PRIMARY = "#6750A4"
        m.TEXT_HINT = "#888"
        yield m


@pytest.fixture
def mock_app_styles():
    with patch("ui.components.config_panels.failover_config_panel.AppStyles") as m:
        m.primary_button.return_value = MagicMock()
        m.secondary_button.return_value = MagicMock()
        yield m


@pytest.fixture
def mock_section_header():
    with patch("ui.components.config_panels.failover_config_panel.SectionHeader", lambda x: MagicMock()):
        yield


def _make_panel(mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page):
    panel = FailoverConfigPanel()
    panel.page = mock_page
    return panel


class TestFailoverItem:
    def test_to_config_string(self):
        item = FailoverItem(provider="qwen", model="qwen-plus", display_name="通义千问", has_credential=True)
        assert item.to_config_string() == "qwen/qwen-plus"

    def test_to_config_string_custom_model(self):
        item = FailoverItem(
            provider="deepseek", model="deepseek-v3-custom", display_name="DeepSeek", has_credential=False
        )
        assert item.to_config_string() == "deepseek/deepseek-v3-custom"


class TestFailoverConfigPanelLoadConfig:
    def test_load_config_populates_items(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        assert len(panel._failover_items) == 1
        assert panel._failover_items[0].provider == "qwen"
        assert panel._failover_items[0].model == "qwen-plus"

    def test_load_config_empty_failover(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        assert len(panel._failover_items) == 0

    def test_load_config_skips_invalid_entries(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen-plus", "invalid_entry", "deepseek/deepseek-chat"],
            "llm_provider": "deepseek",
        }
        mock_config_handler.get_provider_credential.side_effect = lambda p: {
            "api_key": "key" if p == "qwen" else None,
            "base_url": "",
            "models": [],
        }
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        assert len(panel._failover_items) == 2


class TestFailoverConfigPanelDelete:
    def test_delete_removes_entry(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_delete_item(0)
        mock_config_handler.save_config.assert_called()
        saved_data = mock_config_handler.save_config.call_args[0][0]
        assert "qwen/qwen-plus" not in saved_data.get("llm_failover_models", [])


class TestFailoverConfigPanelMove:
    def test_move_up_swaps_order(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen-plus", "deepseek/deepseek-chat"],
            "llm_provider": "openai",
        }
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "key",
            "base_url": "",
            "models": [],
        }
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_move_up(1)
        mock_config_handler.save_config.assert_called()
        saved_data = mock_config_handler.save_config.call_args[0][0]
        assert saved_data["llm_failover_models"][0] == "deepseek/deepseek-chat"

    def test_move_up_first_item_noop(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        initial_call_count = mock_config_handler.save_config.call_count
        panel._on_move_up(0)
        assert mock_config_handler.save_config.call_count == initial_call_count

    def test_move_down_swaps_order(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": ["qwen/qwen-plus", "deepseek/deepseek-chat"],
            "llm_provider": "openai",
        }
        mock_config_handler.get_provider_credential.return_value = {
            "api_key": "key",
            "base_url": "",
            "models": [],
        }
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_move_down(0)
        mock_config_handler.save_config.assert_called()
        saved_data = mock_config_handler.save_config.call_args[0][0]
        assert saved_data["llm_failover_models"][0] == "deepseek/deepseek-chat"


class TestFailoverConfigPanelValidate:
    def test_validate_all_no_missing(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.validate_failover_credentials.return_value = []
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_validate_all(MagicMock())
        mock_config_handler.validate_failover_credentials.assert_called_once()

    def test_validate_all_with_missing(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.validate_failover_credentials.return_value = ["qwen"]
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_validate_all(MagicMock())
        mock_config_handler.validate_failover_credentials.assert_called_once()


class TestFailoverConfigPanelAdd:
    def test_add_click_opens_dialog(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        initial_overlay_count = len(mock_page.overlay)
        panel._on_add_click(MagicMock())
        assert len(mock_page.overlay) == initial_overlay_count + 1

    def test_add_excludes_primary_provider(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        panel = _make_panel(
            mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
        )
        panel._on_add_click(MagicMock())
        dialog = mock_page.overlay[-1]
        assert isinstance(dialog, ProviderCredentialDialog)
        existing = dialog._existing_providers
        assert "deepseek" in existing


class TestProviderCredentialDialog:
    def test_confirm_saves_credential_and_entry(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        dialog = ProviderCredentialDialog(
            page=mock_page,
            on_confirm=MagicMock(),
            existing_providers=["deepseek"],
        )
        dialog._provider = "qwen"
        dialog.model_dropdown.value = "qwen-plus"
        dialog.custom_model_input.value = ""
        dialog.base_url_input.value = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        dialog.api_key_input.value = "sk-qwen-key"

        dialog._on_confirm_click(MagicMock())

        mock_config_handler.save_provider_credential.assert_called_once_with(
            provider="qwen",
            api_key="sk-qwen-key",
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            models=["qwen-plus"],
        )
        mock_config_handler.save_config.assert_called()

    def test_confirm_blocks_primary_provider(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        mock_config_handler.load_config.return_value = {
            "llm_failover_models": [],
            "llm_provider": "deepseek",
        }
        dialog = ProviderCredentialDialog(
            page=mock_page,
            on_confirm=MagicMock(),
            existing_providers=[],
        )
        dialog._provider = "deepseek"
        dialog.model_dropdown.value = "deepseek-chat"
        dialog.custom_model_input.value = ""
        dialog.base_url_input.value = ""
        dialog.api_key_input.value = "sk-key"

        dialog._on_confirm_click(MagicMock())

        mock_config_handler.save_provider_credential.assert_not_called()

    def test_confirm_custom_model(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        dialog = ProviderCredentialDialog(
            page=mock_page,
            on_confirm=MagicMock(),
            existing_providers=[],
        )
        dialog._provider = "qwen"
        dialog.model_dropdown.value = None
        dialog.custom_model_input.value = "qwen-custom-model"
        dialog.base_url_input.value = ""
        dialog.api_key_input.value = "sk-key"

        dialog._on_confirm_click(MagicMock())

        mock_config_handler.save_provider_credential.assert_called_once_with(
            provider="qwen",
            api_key="sk-key",
            base_url="",
            models=["qwen-custom-model"],
        )

    def test_confirm_no_provider_noop(
        self, mock_config_handler, mock_i18n, mock_llm_providers, mock_app_colors, mock_app_styles, mock_page
    ):
        dialog = ProviderCredentialDialog(
            page=mock_page,
            on_confirm=MagicMock(),
            existing_providers=[],
        )
        dialog._provider = ""
        dialog.custom_model_input.value = "some-model"

        dialog._on_confirm_click(MagicMock())

        mock_config_handler.save_provider_credential.assert_not_called()
