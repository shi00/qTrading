from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft
import pytest

from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_ch_for_panels():
    with patch("ui.components.config_panels.tushare_config_panel.ConfigHandler") as m:
        m.get_token.return_value = ""
        m.save_token.return_value = True
        yield m


@pytest.fixture
def mock_config_handler_llm():
    with patch("ui.components.config_panels.llm_config_panel.ConfigHandler") as m:
        m.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        }
        m.save_llm_config.return_value = True
        yield m


@pytest.fixture
def mock_config_handler_local():
    with patch("ui.components.config_panels.local_model_config_panel.ConfigHandler") as m:
        m.get_local_ai_config.return_value = {
            "local_model_path": "",
            "local_model_timeout": 300,
            "n_threads": 4,
            "n_batch": 512,
            "n_ctx": 4096,
            "flash_attn": True,
            "n_gpu_layers": -1,
        }
        m.get_local_ai_timeout.return_value = 300
        m.save_local_ai_config.return_value = True
        yield m


@pytest.fixture
def mock_config_handler_db():
    with patch("ui.components.config_panels.database_config_panel.ConfigHandler") as m:
        m.get_db_config.return_value = {
            "host": "localhost",
            "port": 5432,
            "user": "postgres",
            "database": "astock",
        }
        m.get_db_password.return_value = ""
        m.save_db_config.return_value = True
        yield m


@pytest.fixture
def mock_i18n():
    with patch("ui.components.config_panels.tushare_config_panel.I18n") as m:
        m.get.side_effect = lambda key, **kw: key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_i18n_llm():
    with patch("ui.components.config_panels.llm_config_panel.I18n") as m:
        m.get.side_effect = lambda key, **kw: key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_i18n_local():
    with patch("ui.components.config_panels.local_model_config_panel.I18n") as m:
        m.get.side_effect = lambda key, **kw: key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_i18n_db():
    with patch("ui.components.config_panels.database_config_panel.I18n") as m:
        m.get.side_effect = lambda key, **kw: key
        m.subscribe.return_value = "sub_id"
        m.unsubscribe.return_value = None
        yield m


@pytest.fixture
def mock_llm_providers():
    with patch(
        "ui.components.config_panels.llm_config_panel.LLM_PROVIDERS",
        {
            "deepseek": {
                "name": "DeepSeek",
                "base_url": "https://api.deepseek.com",
                "models": [
                    {"id": "deepseek-chat", "name": "DeepSeek Chat", "tag": "推荐"},
                ],
                "console_url": "https://platform.deepseek.com",
            },
            "openai": {
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "models": [
                    {"id": "gpt-4o", "name": "GPT-4o"},
                ],
            },
            "azure": {
                "name": "Azure OpenAI",
                "base_url": "",
                "models": [],
            },
            "custom": {
                "name": "Custom",
                "base_url": "",
                "models": [],
            },
        },
    ):
        with patch(
            "ui.components.config_panels.llm_config_panel.AZURE_API_VERSIONS",
            ["2024-02-01"],
        ):
            with patch(
                "ui.components.config_panels.llm_config_panel.AZURE_DEFAULT_API_VERSION",
                "2024-02-01",
            ):
                yield


def _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, **kwargs):
    panel = TushareConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


def _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page, **kwargs):
    kwargs.setdefault("on_test_connection", AsyncMock(return_value={"success": True}))
    panel = LLMConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


def _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, **kwargs):
    kwargs.setdefault("on_verify_model", AsyncMock(return_value=True))
    panel = LocalModelConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


def _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, **kwargs):
    panel = DatabaseConfigPanel(**kwargs)
    panel.page = mock_page
    return panel


class TestTushareConfigPanel:
    def test_load_config_populates_token_from_saved(self, mock_ch_for_panels, mock_i18n, mock_page):
        mock_ch_for_panels.get_token.return_value = "saved_token_123"
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        assert panel.token_input.value == "saved_token_123"

    def test_load_config_defaults_empty_token(self, mock_ch_for_panels, mock_i18n, mock_page):
        mock_ch_for_panels.get_token.return_value = None
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        assert panel.token_input.value == ""

    def test_save_click_calls_on_save_with_config(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_save = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_save=on_save)
        panel.token_input.value = "my_token"
        panel._on_save_click(MagicMock())
        on_save.assert_called_once_with({"token": "my_token"})

    def test_get_current_config_returns_token(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.token_input.value = "  abc  "
        config = panel.get_current_config()
        assert config == {"token": "abc"}

    def test_set_config_updates_token_input(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.set_config({"token": "new_token"})
        assert panel.token_input.value == "new_token"

    def test_reload_config_refreshes_from_config_handler(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        mock_ch_for_panels.get_token.return_value = "refreshed_token"
        panel.reload_config()
        assert panel.token_input.value == "refreshed_token"

    @pytest.mark.asyncio
    async def test_verify_token_empty_shows_error(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.token_input.value = ""
        result = await panel.verify_token()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_token_success_saves_and_notifies(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_verify = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_verify_success=on_verify)
        panel.token_input.value = "valid_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api") as mock_pro_api,
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
        ):
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            mock_pro.trade_cal.return_value = MagicMock()

            mock_client_instance = MagicMock()
            mock_client_instance.set_token.return_value = False
            mock_client_cls.return_value = mock_client_instance

            result = await panel.verify_token()

        assert result is True
        mock_ch_for_panels.save_token.assert_called_once_with("valid_token")
        on_verify.assert_called_once_with("valid_token")

    @pytest.mark.asyncio
    async def test_verify_token_failure_returns_false(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.token_input.value = "bad_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api", side_effect=Exception("invalid token")),
        ):
            result = await panel.verify_token()

        assert result is False
        mock_ch_for_panels.save_token.assert_not_called()

    def test_on_input_change_calls_on_change(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_change = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_change=on_change)
        panel._on_input_change(MagicMock())
        on_change.assert_called_once()

    def test_save_button_visibility(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, show_save_button=True)
        assert panel.save_button.visible is True

    def test_save_button_hidden_when_disabled(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, show_save_button=False)
        assert panel.save_button.visible is False

    def test_standard_ui_inner_row_has_wrap_true(self, mock_ch_for_panels, mock_i18n, mock_page):
        """standard UI 模式下包含 token_input 和按钮的 Row 必须 wrap=True"""
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, compact=False)
        # _build_standard_ui 返回 ft.Row，其 controls[0] 是 ft.Column
        # Column.controls[0] 是包含 token_input + buttons 的 ft.Row
        outer_row = panel.content
        inner_col = outer_row.controls[0]
        token_buttons_row = inner_col.controls[0]
        assert token_buttons_row.wrap is True

    def test_compact_ui_not_affected_by_standard_ui_change(self, mock_ch_for_panels, mock_i18n, mock_page):
        """compact 模式下 _build_compact_ui 不应包含 wrap=True 的 token+按钮 Row（确保智能向导不受影响）"""
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, compact=True)
        # compact 模式 content 是 ft.Column
        assert isinstance(panel.content, ft.Column)
        # token_input 应直接作为 Column 子项（不在 wrap=True 的 Row 中）
        assert panel.token_input in panel.content.controls
        # 遍历所有 Row 子项，确认没有 wrap=True 的 token+按钮组合 Row
        for ctrl in panel.content.controls:
            if isinstance(ctrl, ft.Row):
                assert ctrl.wrap is False or ctrl.wrap is None


class TestLLMConfigPanel:
    def test_load_config_populates_fields_from_saved(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "sk-test",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.provider_dropdown.value == "deepseek"
        assert panel.api_key_input.value == "sk-test"

    def test_load_config_azure_shows_azure_fields(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "gpt-4",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "my-resource",
            "azure_deployment_name": "my-deployment",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel._is_azure is True
        assert panel.azure_resource_input.value == "my-resource"
        assert panel.azure_deployment_input.value == "my-deployment"

    def test_get_current_config_returns_all_fields(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.provider_dropdown.value = "deepseek"
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = "https://api.deepseek.com"
        panel.api_key_input.value = "sk-test"
        config = panel.get_current_config()
        assert config["provider"] == "deepseek"
        assert config["model"] == "deepseek-chat"
        assert config["base_url"] == "https://api.deepseek.com"
        assert config["api_key"] == "sk-test"

    def test_get_current_config_azure_includes_azure_fields(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "my-deploy",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        config = panel.get_current_config()
        assert config["model"] == "deploy"
        assert config["azure_resource_name"] == "res"
        assert config["azure_deployment_name"] == "deploy"

    @pytest.mark.asyncio
    async def test_save_config_calls_config_handler(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = True
        panel.api_key_input.value = "sk-new"
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = "https://api.deepseek.com"

        panel.on_reload_service = AsyncMock()
        with patch.object(panel, "update"):
            await panel._save_config()

        mock_config_handler_llm.save_llm_config.assert_called_once()
        call_kwargs = mock_config_handler_llm.save_llm_config.call_args
        assert call_kwargs.kwargs["provider"] == "deepseek"
        assert call_kwargs.kwargs["model"] == "deepseek-chat"
        assert call_kwargs.kwargs["api_key"] == "sk-new"

    @pytest.mark.asyncio
    async def test_save_config_azure_missing_resource_shows_warning(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "key",
            "azure_resource_name": "",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = True
        panel.api_key_input.value = "key"
        panel.azure_resource_input.value = ""
        panel.azure_deployment_input.value = "deploy"

        await panel._save_config()
        mock_config_handler_llm.save_llm_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_azure_missing_deployment_shows_warning(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "key",
            "azure_resource_name": "res",
            "azure_deployment_name": "",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = True
        panel.api_key_input.value = "key"
        panel.azure_resource_input.value = "res"
        panel.azure_deployment_input.value = ""

        await panel._save_config()
        mock_config_handler_llm.save_llm_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_test_connection_calls_on_test_connection_callback(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock(return_value={"success": True})
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = "https://api.deepseek.com"

        await panel._on_llm_test_connection()

        on_test.assert_called_once()
        call_kwargs = on_test.call_args.kwargs
        assert call_kwargs["provider"] == "deepseek"
        assert call_kwargs["model"] == "deepseek-chat"
        assert call_kwargs["api_key"] == "sk-test"

    @pytest.mark.asyncio
    async def test_test_connection_no_api_key_shows_warning(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock()
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = ""

        await panel._on_llm_test_connection()

        on_test.assert_not_called()

    def test_api_key_modified_tracks_changes(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.api_key_modified is False
        panel._on_api_key_change(MagicMock())
        assert panel.api_key_modified is True

    @pytest.mark.asyncio
    async def test_async_verify_connection_missing_provider_model_returns_false(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = None
        panel.custom_model_input.value = None

        with patch.object(
            mock_config_handler_llm,
            "get_llm_config",
            return_value={"api_key": "sk-test", "base_url": "https://api.deepseek.com"},
        ):
            result = await panel.async_verify_connection()

        assert result is False

    def test_save_current_config_calls_config_handler(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.provider_dropdown.value = "deepseek"
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = "https://api.deepseek.com"
        panel.api_key_input.value = "sk-test"

        result = panel.save_current_config()

        assert result is True
        mock_config_handler_llm.save_llm_config.assert_called_once()

    def test_normalize_base_url_strips_endpoint_suffix(self):
        """只剥离 API 端点后缀，保留基础路径 (Fix 2)"""
        # 剥离 /chat/completions 后缀
        assert (
            LLMConfigPanel._normalize_base_url("https://api.deepseek.com/v1/chat/completions")
            == "https://api.deepseek.com/v1"
        )
        # 剥离 /completions 后缀
        assert (
            LLMConfigPanel._normalize_base_url("https://api.openai.com/v1/completions") == "https://api.openai.com/v1"
        )

    def test_normalize_base_url_preserves_base_path(self):
        """保留供应商特有的基础路径 (Fix 2)"""
        # Qwen 兼容模式路径
        assert (
            LLMConfigPanel._normalize_base_url("https://dashscope.aliyuncs.com/compatible-mode/v1")
            == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        # Zhipu 智谱路径
        assert (
            LLMConfigPanel._normalize_base_url("https://open.bigmodel.cn/api/paas/v4")
            == "https://open.bigmodel.cn/api/paas/v4"
        )
        # Google Gemini 路径
        assert (
            LLMConfigPanel._normalize_base_url("https://generativelanguage.googleapis.com/v1beta")
            == "https://generativelanguage.googleapis.com/v1beta"
        )

    def test_normalize_base_url_empty_returns_empty(self):
        assert LLMConfigPanel._normalize_base_url("") == ""

    def test_provider_change_loads_key_without_marking_modified(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        mock_config_handler_llm.get_provider_credential.return_value = {
            "api_key": "sk-stored",
            "base_url": "https://api.deepseek.com",
        }

        panel._on_provider_change(MagicMock(control=MagicMock(value="deepseek")))

        assert panel.api_key_input.value == "sk-stored"
        assert panel.api_key_modified is False

    @pytest.mark.asyncio
    async def test_save_config_blank_modified_key_clears_key(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = True
        panel.api_key_input.value = "   "
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = " https://api.deepseek.com/v1/chat/completions "

        panel.on_reload_service = AsyncMock()
        with patch.object(panel, "update"):
            await panel._save_config()

        call_kwargs = mock_config_handler_llm.save_llm_config.call_args.kwargs
        assert call_kwargs["api_key"] == ""
        assert call_kwargs["base_url"] == "https://api.deepseek.com/v1"

    @pytest.mark.asyncio
    async def test_test_connection_blank_model_returns_without_calling_callback(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock(return_value={"success": True})
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = " sk-test "
        panel.model_dropdown.value = "  "
        panel.custom_model_input.value = ""

        await panel._on_llm_test_connection()

        on_test.assert_not_called()


class TestLocalModelConfigPanel:
    def test_load_config_populates_fields(self, mock_config_handler_local, mock_i18n_local, mock_page):
        mock_config_handler_local.get_local_ai_config.return_value = {
            "local_model_path": "/models/test.gguf",
            "local_model_timeout": 120,
            "n_threads": 8,
            "n_batch": 1024,
            "n_ctx": 8192,
            "flash_attn": False,
            "n_gpu_layers": 2,
        }
        mock_config_handler_local.get_local_ai_timeout.return_value = 120
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        assert panel.model_path_input.value == "/models/test.gguf"
        assert panel.threads_input.value == 8
        assert panel.batch_input.value == "1024"
        assert panel.ctx_input.value == "8192"
        assert panel.flash_attn_switch.value is False

    def test_load_config_gpu_auto_sets_switch(self, mock_config_handler_local, mock_i18n_local, mock_page):
        mock_config_handler_local.get_local_ai_config.return_value = {
            "local_model_path": "",
            "local_model_timeout": 300,
            "n_threads": 4,
            "n_batch": 512,
            "n_ctx": 4096,
            "flash_attn": True,
            "n_gpu_layers": -1,
        }
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        assert panel.gpu_auto_switch.value is True
        assert panel.gpu_layers_input.visible is False

    def test_save_config_calls_config_handler(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "120"
        panel.threads_input.value = 8
        panel.gpu_auto_switch.value = False
        panel.gpu_layers_input.value = 2
        panel.batch_input.value = "1024"
        panel.ctx_input.value = "8192"
        panel.flash_attn_switch.value = True

        result = panel.save_config()

        assert result is True
        mock_config_handler_local.save_local_ai_config.assert_called_once_with(
            model_path="/models/test.gguf",
            timeout=120,
            n_threads=8,
            n_batch=1024,
            n_ctx=8192,
            flash_attn=True,
            n_gpu_layers=2,
        )

    def test_save_config_gpu_auto_saves_negative_one(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"
        panel.threads_input.value = 4
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input.value = 0
        panel.batch_input.value = "512"
        panel.ctx_input.value = "4096"
        panel.flash_attn_switch.value = True

        panel.save_config()

        call_kwargs = mock_config_handler_local.save_local_ai_config.call_args.kwargs
        assert call_kwargs["n_gpu_layers"] == -1

    def test_save_config_returns_false_when_save_fails(self, mock_config_handler_local, mock_i18n_local, mock_page):
        """save_local_ai_config 返回 False 时 save_config 应返回 False"""
        mock_config_handler_local.save_local_ai_config.return_value = False
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "120"
        panel.threads_input.value = 8
        panel.gpu_auto_switch.value = False
        panel.gpu_layers_input.value = 2
        panel.batch_input.value = "1024"
        panel.ctx_input.value = "8192"
        panel.flash_attn_switch.value = True

        result = panel.save_config()

        assert result is False

    def test_get_current_config_returns_all_fields(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "  /path/model.gguf  "
        panel.timeout_input.value = "60"
        panel.threads_input.value = 6
        panel.gpu_auto_switch.value = False
        panel.gpu_layers_input.value = 10
        panel.batch_input.value = "2048"
        panel.ctx_input.value = "16384"
        panel.flash_attn_switch.value = False

        config = panel.get_current_config()

        assert config["model_path"] == "/path/model.gguf"
        assert config["timeout"] == 60
        assert config["n_threads"] == 6
        assert config["n_gpu_layers"] == 10
        assert config["n_batch"] == 2048
        assert config["n_ctx"] == 16384
        assert config["flash_attn"] is False

    def test_set_config_updates_all_fields(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.set_config(
            {
                "model_path": "/new/model.gguf",
                "timeout": 200,
                "n_threads": 12,
                "n_gpu_layers": -1,
                "n_batch": 4096,
                "n_ctx": 32768,
                "flash_attn": True,
            }
        )
        assert panel.model_path_input.value == "/new/model.gguf"
        assert panel.timeout_input.value == "200"
        assert panel.threads_input.value == 12
        assert panel.gpu_auto_switch.value is True
        assert panel.batch_input.value == "4096"
        assert panel.ctx_input.value == "32768"
        assert panel.flash_attn_switch.value is True

    @pytest.mark.asyncio
    async def test_verify_model_empty_path_returns_false(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = ""
        result = await panel.async_verify_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_model_nonexistent_path_returns_false(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/nonexistent/model.gguf"
        with patch("os.path.exists", return_value=False):
            result = await panel.async_verify_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_model_wrong_extension_returns_false(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/model.bin"
        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_model_invalid_timeout_returns_false(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/model.gguf"
        panel.timeout_input.value = "abc"
        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_model_timeout_out_of_range_returns_false(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/model.gguf"
        panel.timeout_input.value = "9999"
        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_model_success_returns_true(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"

        with (
            patch("os.path.exists", return_value=True),
            patch("os.path.isfile", return_value=True),
            patch("os.path.getsize", return_value=1024),
            patch("asyncio.sleep"),
            patch.object(panel, "_safe_update"),
        ):
            result = await panel.async_verify_model()

        assert result is True

    def test_on_save_click_calls_save_and_on_save(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_save = MagicMock()
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, on_save=on_save)
        panel.model_path_input.value = "/models/test.gguf"
        panel.timeout_input.value = "300"
        panel.threads_input.value = 4
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input.value = 0
        panel.batch_input.value = "512"
        panel.ctx_input.value = "4096"
        panel.flash_attn_switch.value = True

        panel._on_save_click(MagicMock())

        mock_config_handler_local.save_local_ai_config.assert_called_once()
        on_save.assert_called_once()

    def test_reload_config_refreshes_from_config_handler(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        mock_config_handler_local.get_local_ai_config.return_value = {
            "local_model_path": "/new/path.gguf",
            "local_model_timeout": 60,
            "n_threads": 2,
            "n_batch": 2048,
            "n_ctx": 8192,
            "flash_attn": False,
            "n_gpu_layers": 5,
        }
        mock_config_handler_local.get_local_ai_timeout.return_value = 60
        panel.reload_config()
        assert panel.model_path_input.value == "/new/path.gguf"
        assert panel.timeout_input.value == "60"
        assert panel.threads_input.value == 2


class TestDatabaseConfigPanel:
    def test_load_config_populates_fields(self, mock_config_handler_db, mock_i18n_db, mock_page):
        mock_config_handler_db.get_db_config.return_value = {
            "host": "db.example.com",
            "port": 3306,
            "user": "admin",
            "database": "mydb",
        }
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        assert panel.db_host_input.value == "db.example.com"
        assert panel.db_port_input.value == "3306"
        assert panel.db_user_input.value == "admin"
        assert panel.db_name_input.value == "mydb"

    def test_load_config_defaults(self, mock_config_handler_db, mock_i18n_db, mock_page):
        mock_config_handler_db.get_db_config.return_value = {}
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        assert panel.db_host_input.value == "localhost"
        assert panel.db_port_input.value == "5432"
        assert panel.db_user_input.value == "postgres"
        assert panel.db_name_input.value == "astock"

    def test_get_config_returns_all_fields(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "myhost"
        panel.db_port_input.value = "5433"
        panel.db_user_input.value = "myuser"
        panel.db_password_input.value = "mypass"
        panel.db_name_input.value = "mydb"
        panel.db_create_checkbox.value = True

        config = panel.get_config()

        assert config["host"] == "myhost"
        assert config["port"] == 5433
        assert config["user"] == "myuser"
        assert config["password"] == "mypass"
        assert config["database"] == "mydb"
        assert config["create_if_not_exists"] is True

    def test_set_config_updates_fields(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.set_config(
            {
                "host": "newhost",
                "port": 3306,
                "user": "newuser",
                "password": "newpass",
                "database": "newdb",
                "create_if_not_exists": False,
            }
        )
        assert panel.db_host_input.value == "newhost"
        assert panel.db_port_input.value == "3306"
        assert panel.db_user_input.value == "newuser"
        assert panel.db_password_input.value == "newpass"
        assert panel.db_name_input.value == "newdb"
        assert panel.db_create_checkbox.value is False

    def test_validate_empty_host_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = ""
        is_valid, error = panel.validate()
        assert is_valid is False

    def test_validate_empty_user_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_user_input.value = ""
        is_valid, error = panel.validate()
        assert is_valid is False

    def test_validate_empty_database_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_user_input.value = "postgres"
        panel.db_name_input.value = ""
        is_valid, error = panel.validate()
        assert is_valid is False

    def test_validate_invalid_port_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "abc"
        panel.db_user_input.value = "postgres"
        panel.db_name_input.value = "astock"
        is_valid, error = panel.validate()
        assert is_valid is False

    def test_validate_port_out_of_range_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "99999"
        panel.db_user_input.value = "postgres"
        panel.db_name_input.value = "astock"
        is_valid, error = panel.validate()
        assert is_valid is False

    def test_validate_valid_config_returns_true(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_name_input.value = "astock"
        is_valid, error = panel.validate()
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_test_connection_success(self, mock_config_handler_db, mock_i18n_db, mock_page):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS
        mock_result.message = "Connection successful"

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.get_database_info = AsyncMock(return_value=None)
            result = await panel.test_connection()

        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_database_not_found_with_create(
        self, mock_config_handler_db, mock_i18n_db, mock_page
    ):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        panel.db_create_checkbox.value = True

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "Database not found"

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await panel.test_connection()

        assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_database_not_found_without_create(
        self, mock_config_handler_db, mock_i18n_db, mock_page
    ):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        panel.db_create_checkbox.value = False

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND
        mock_result.message = "Database not found"

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await panel.test_connection()

        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_failure_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.AUTHENTICATION_ERROR
        mock_result.message = "Auth failed"

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            result = await panel.test_connection()

        assert result is False

    @pytest.mark.asyncio
    async def test_save_config_success_calls_config_handler(self, mock_config_handler_db, mock_i18n_db, mock_page):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        panel.db_create_checkbox.value = True

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, "OK"))
            result = await panel.save_config()

        assert result is True
        mock_config_handler_db.save_db_config.assert_called_once_with(
            host="localhost",
            port=5432,
            user="postgres",
            password="pass",
            database="astock",
        )

    @pytest.mark.asyncio
    async def test_save_config_creates_database_when_not_found(self, mock_config_handler_db, mock_i18n_db, mock_page):
        from data.persistence.db_config_service import ConnectionStatus

        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        panel.db_create_checkbox.value = True

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.DATABASE_NOT_FOUND

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.create_database = AsyncMock(return_value=(True, "Created"))
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, "OK"))
            result = await panel.save_config()

        assert result is True
        mock_svc.create_database.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_config_validation_failure_returns_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = ""
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_name_input.value = "astock"

        result = await panel.save_config()

        assert result is False
        mock_config_handler_db.save_db_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_save_config_calls_on_save_callback(self, mock_config_handler_db, mock_i18n_db, mock_page):
        from data.persistence.db_config_service import ConnectionStatus

        on_save = MagicMock()
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, on_save_callback=on_save)
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        panel.db_create_checkbox.value = False

        mock_result = MagicMock()
        mock_result.status = ConnectionStatus.SUCCESS

        with patch("ui.components.config_panels.database_config_panel.DatabaseConfigService") as mock_svc:
            mock_svc.test_connection = AsyncMock(return_value=mock_result)
            mock_svc.ensure_tables_exist = AsyncMock(return_value=(True, "OK"))
            await panel.save_config()

        on_save.assert_called_once()

    def test_reload_config_refreshes_from_config_handler(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        mock_config_handler_db.get_db_config.return_value = {
            "host": "newhost",
            "port": 3306,
            "user": "newuser",
            "database": "newdb",
        }
        panel.reload_config()
        assert panel.db_host_input.value == "newhost"
        assert panel.db_port_input.value == "3306"
        assert panel.db_user_input.value == "newuser"
        assert panel.db_name_input.value == "newdb"


class TestLLMConfigPanelExtended:
    def test_load_config_custom_provider(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "custom",
            "model": "my-custom-model",
            "base_url": "https://custom.api.com",
            "api_key": "sk-custom",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel._current_provider == "custom"
        assert panel.custom_model_input.visible is True
        assert panel.model_dropdown.visible is False
        assert panel.custom_model_input.value == "my-custom-model"

    def test_load_config_model_not_in_list_shows_custom_input(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "deepseek-unknown-model",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.custom_model_input.visible is True
        assert panel.model_dropdown.visible is False
        assert panel.custom_model_input.value == "deepseek-unknown-model"

    def test_load_config_no_model_selects_recommended(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.model_dropdown.value == "deepseek-chat"

    def test_load_config_azure_hides_refresh_button(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "my-deploy",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.refresh_models_button.visible is False

    def test_load_config_non_azure_shows_refresh_for_compatible(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel.refresh_models_button.visible is True

    def test_on_provider_change_to_azure(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        e = MagicMock()
        e.control.value = "azure"
        panel._on_provider_change(e)
        assert panel._is_azure is True
        assert panel._current_provider == "azure"
        assert panel.custom_model_input.visible is False
        assert panel.refresh_models_button.visible is False

    def test_on_provider_change_to_custom(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        e = MagicMock()
        e.control.value = "custom"
        panel._on_provider_change(e)
        assert panel._is_azure is False
        assert panel._current_provider == "custom"
        assert panel.custom_model_input.visible is True
        assert panel.model_dropdown.visible is False
        assert panel.refresh_models_button.visible is True

    def test_on_provider_change_to_openai(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        e = MagicMock()
        e.control.value = "openai"
        panel._on_provider_change(e)
        assert panel._is_azure is False
        assert panel._current_provider == "openai"
        assert panel.custom_model_input.visible is False
        assert panel.model_dropdown.visible is True
        assert panel.refresh_models_button.visible is True
        assert panel.model_dropdown.value == "gpt-4o"

    def test_on_provider_change_resets_api_key(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        """切换到新供应商（无已存储凭证）时清空 API Key"""
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = True
        panel.api_key_input.value = "old-key"
        # 模拟该供应商无已存储凭证
        mock_config_handler_llm.get_provider_credential.return_value = {
            "api_key": "",
            "base_url": "",
        }
        e = MagicMock()
        e.control.value = "openai"
        panel._on_provider_change(e)
        assert panel.api_key_input.value == ""
        assert panel._api_key_modified is False

    def test_on_provider_change_loads_stored_credential(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        """切换到已配置过的供应商时加载已存储的凭证 (Fix 3)"""
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        # 模拟该供应商之前作为 failover 配置过
        mock_config_handler_llm.get_provider_credential.return_value = {
            "api_key": "stored_openai_key",
            "base_url": "https://api.openai.com/v1",
        }
        e = MagicMock()
        e.control.value = "openai"
        panel._on_provider_change(e)
        assert panel.api_key_input.value == "stored_openai_key"
        # Loading stored credential should NOT mark as modified - only user edits trigger modification
        assert panel._api_key_modified is False
        assert panel.base_url_input.value == "https://api.openai.com/v1"

    @pytest.mark.asyncio
    async def test_on_llm_test_connection_azure_missing_resource(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        on_test = AsyncMock()
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = "azure-key"
        panel.azure_resource_input.value = ""
        panel.azure_deployment_input.value = "deploy"
        with patch.object(panel, "_safe_update"):
            await panel._on_llm_test_connection()
        on_test.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_llm_test_connection_azure_missing_deployment(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "azure-key"
        panel.azure_resource_input.value = "res"
        panel.azure_deployment_input.value = ""
        with patch.object(panel, "_safe_update"):
            await panel._on_llm_test_connection()

    @pytest.mark.asyncio
    async def test_on_llm_test_connection_azure_success(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock(return_value={"success": True})
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        # Set Azure mode - required for _on_llm_test_connection to use Azure branch
        panel._is_azure = True
        panel._current_provider = "azure"
        panel.api_key_input.value = "azure-key"
        panel.azure_resource_input.value = "res"
        panel.azure_deployment_input.value = "deploy"
        # Azure mode uses deployment_name as model; set it to pass model validation
        panel.model_dropdown.value = "deploy"
        with patch.object(panel, "_safe_update"):
            await panel._on_llm_test_connection()
        on_test.assert_called_once()
        call_kwargs = on_test.call_args.kwargs
        assert call_kwargs["provider"] == "azure"
        assert call_kwargs["azure_resource_name"] == "res"

    @pytest.mark.asyncio
    async def test_on_llm_test_connection_failure(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock(return_value={"success": False, "message": "auth_failed"})
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        with patch.object(panel, "_safe_update"):
            await panel._on_llm_test_connection()
        on_test.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_llm_test_connection_exception(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_test = AsyncMock(side_effect=Exception("network error"))
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=on_test,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        with (
            patch(
                "utils.error_classifier.classify_error",
                return_value={
                    "code": "network",
                    "message_key": "llm_err_network",
                    "should_retry": True,
                },
            ),
            patch("utils.error_classifier.get_error_message", return_value="Network error"),
            patch.object(panel, "_safe_update"),
        ):
            await panel._on_llm_test_connection()

    @pytest.mark.asyncio
    async def test_async_verify_connection_azure_missing_resource(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "azure-key"
        panel.azure_resource_input.value = ""
        panel.azure_deployment_input.value = "deploy"
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_azure_missing_deployment(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "azure-key"
        panel.azure_resource_input.value = "res"
        panel.azure_deployment_input.value = ""
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_no_api_key_returns_false(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        """空 API Key 时直接返回 False，不回退到已存 key (Fix 9)"""
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = ""
        panel.model_dropdown.value = "deepseek-chat"
        mock_config_handler_llm.get_llm_config.return_value = {
            "api_key": "saved-key",
            "base_url": "https://api.deepseek.com",
        }
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        # 空输入应直接返回 False，而非用已存 key 验证成功
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_no_api_key_no_saved(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = ""
        panel.model_dropdown.value = "deepseek-chat"
        mock_config_handler_llm.get_llm_config.return_value = {
            "api_key": "",
            "base_url": "",
        }
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_already_verifying(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        panel._is_verifying = True
        result = await panel.async_verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_success(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        panel.on_test_connection = AsyncMock(return_value={"success": True})
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is True
        assert panel._is_verifying is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_failure(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        panel.on_test_connection = AsyncMock(return_value={"success": False, "message": "auth failed"})
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is False

    @pytest.mark.asyncio
    async def test_async_verify_connection_exception(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        panel.on_test_connection = AsyncMock(side_effect=Exception("network error"))
        with (
            patch(
                "utils.error_classifier.classify_error",
                return_value={
                    "code": "network",
                    "message_key": "llm_err_network",
                    "should_retry": True,
                },
            ),
            patch("utils.error_classifier.get_error_message", return_value="Network error"),
            patch.object(panel, "_safe_update"),
        ):
            result = await panel.async_verify_connection()
        assert result is False

    def test_set_loading_state(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        on_loading = MagicMock()
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_loading_change=on_loading,
        )
        panel._set_loading_state(True)
        assert panel.test_button.disabled is True
        assert panel.save_button.disabled is True
        on_loading.assert_called_with(True)
        panel._set_loading_state(False)
        assert panel.test_button.disabled is False
        assert panel.save_button.disabled is False
        on_loading.assert_called_with(False)

    def test_get_current_base_url_azure(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "",
            "base_url": "",
            "api_key": "key",
            "azure_resource_name": "res",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        assert panel._get_current_base_url() == ""

    def test_get_current_base_url_non_azure(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.base_url_input.value = "https://api.deepseek.com"
        assert panel._get_current_base_url() == "https://api.deepseek.com"

    @pytest.mark.asyncio
    async def test_save_config_custom_model_saves_history(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "deepseek",
            "model": "",
            "base_url": "https://api.deepseek.com",
            "api_key": "",
            "custom_models": {},
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = False
        panel.model_dropdown.value = "deepseek-custom-model"
        panel.custom_model_input.value = "deepseek-custom-model"
        panel.on_reload_service = AsyncMock()
        with patch.object(panel, "update"):
            await panel._save_config()
        call_kwargs = mock_config_handler_llm.save_llm_config.call_args.kwargs
        assert "custom_models" in call_kwargs
        assert "deepseek-custom-model" in call_kwargs["custom_models"]["deepseek"]

    @pytest.mark.asyncio
    async def test_save_config_custom_provider_model(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "custom",
            "model": "my-model",
            "base_url": "https://custom.api.com",
            "api_key": "",
            "custom_models": {},
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = False
        panel.custom_model_input.value = "my-model"
        panel.on_reload_service = AsyncMock()
        with patch.object(panel, "update"):
            await panel._save_config()
        call_kwargs = mock_config_handler_llm.save_llm_config.call_args.kwargs
        assert "custom_models" in call_kwargs

    @pytest.mark.asyncio
    async def test_save_config_exception(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel._api_key_modified = False
        panel.model_dropdown.value = "deepseek-chat"
        mock_config_handler_llm.save_llm_config.side_effect = Exception("save error")
        with (
            patch("utils.error_classifier.classify_error", return_value="io"),
            patch("utils.error_classifier.get_error_message", return_value="Save error"),
            patch.object(panel, "update"),
        ):
            await panel._save_config()

    @pytest.mark.asyncio
    async def test_save_config_calls_on_save(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        on_save = MagicMock()
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_save=on_save,
        )
        panel._api_key_modified = False
        panel.model_dropdown.value = "deepseek-chat"
        panel.on_reload_service = AsyncMock()
        with patch.object(panel, "update"):
            await panel._save_config()
        on_save.assert_called_once()

    def test_save_current_config_exception(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.provider_dropdown.value = "deepseek"
        panel.model_dropdown.value = "deepseek-chat"
        panel.base_url_input.value = "https://api.deepseek.com"
        panel.api_key_input.value = "sk-test"
        mock_config_handler_llm.save_llm_config.side_effect = Exception("save error")
        result = panel.save_current_config()
        assert result is False

    def test_save_current_config_azure(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        mock_config_handler_llm.get_llm_config.return_value = {
            "provider": "azure",
            "model": "deploy",
            "base_url": "",
            "api_key": "azure-key",
            "azure_resource_name": "res",
            "azure_deployment_name": "deploy",
            "api_version": "2024-02-01",
        }
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        result = panel.save_current_config()
        assert result is True
        call_kwargs = mock_config_handler_llm.save_llm_config.call_args.kwargs
        assert call_kwargs["api_version"] == "2024-02-01"
        assert call_kwargs["azure_resource_name"] == "res"

    def test_safe_update_with_page(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        with patch.object(panel, "update"):
            panel._safe_update()

    def test_safe_update_without_page(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.page = None
        panel._safe_update()

    def test_safe_update_exception(self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page):
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        with patch.object(panel, "update", side_effect=Exception("update error")):
            panel._safe_update()

    def test_on_locale_change_preserves_dropdown_values(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        """§5.8 规范 4：_on_locale_change 重建 options 后 provider_dropdown 与 model_dropdown 的 value 必须保留。"""
        panel = _make_llm_panel(mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page)
        panel.provider_dropdown.value = "deepseek"
        panel.model_dropdown.value = "deepseek-chat"

        saved_provider = panel.provider_dropdown.value
        saved_model = panel.model_dropdown.value
        panel._on_locale_change()

        assert panel.provider_dropdown.value == saved_provider
        assert panel.provider_dropdown.value == "deepseek"
        assert panel.provider_dropdown.options is not None
        assert len(panel.provider_dropdown.options) > 0

        assert panel.model_dropdown.value == saved_model
        assert panel.model_dropdown.value == "deepseek-chat"
        assert panel.model_dropdown.options is not None
        assert len(panel.model_dropdown.options) > 0


class TestDatabaseConfigPanelExtended:
    def test_load_password_enabled(self, mock_config_handler_db, mock_i18n_db, mock_page):
        mock_config_handler_db.get_db_password.return_value = "saved_password"
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, load_password=True)
        assert panel.db_password_input.value == "saved_password"

    def test_load_password_disabled(self, mock_config_handler_db, mock_i18n_db, mock_page):
        mock_config_handler_db.get_db_password.return_value = "saved_password"
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, load_password=False)
        assert panel.db_password_input.value == ""

    def test_did_mount_subscribes_locale(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.did_mount()
        mock_i18n_db.subscribe.assert_called_once()
        assert panel._locale_subscription_id == "sub_id"

    def test_will_unmount_unsubscribes_locale(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._locale_subscription_id = "sub_id"
        panel.will_unmount()
        mock_i18n_db.unsubscribe.assert_called_once_with("sub_id")
        assert panel._locale_subscription_id is None

    def test_will_unmount_no_subscription(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._locale_subscription_id = None
        panel.will_unmount()
        mock_i18n_db.unsubscribe.assert_not_called()

    def test_on_locale_change_preserves_values(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel.db_host_input.value = "myhost"
        panel.db_port_input.value = "5433"
        panel.db_user_input.value = "myuser"
        panel.db_password_input.value = "mypass"
        panel.db_name_input.value = "mydb"
        panel.db_create_checkbox.value = False
        panel._on_locale_change("zh_CN")
        assert panel.db_host_input.value == "myhost"
        assert panel.db_port_input.value == "5433"
        assert panel.db_user_input.value == "myuser"
        assert panel.db_password_input.value == "mypass"
        assert panel.db_name_input.value == "mydb"
        assert panel.db_create_checkbox.value is False

    def test_on_locale_change_exception_handled(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        mock_i18n_db.get.side_effect = RuntimeError("i18n error")
        panel._on_locale_change("zh_CN")

    def test_on_input_change_calls_callback(self, mock_config_handler_db, mock_i18n_db, mock_page):
        on_change = MagicMock()
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, on_change=on_change)
        panel._on_input_change(None)
        on_change.assert_called_once()

    def test_test_connection_already_verifying(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._is_verifying = True
        panel.db_host_input.value = "localhost"
        panel.db_port_input.value = "5432"
        panel.db_user_input.value = "postgres"
        panel.db_password_input.value = "pass"
        panel.db_name_input.value = "astock"
        import asyncio

        result = asyncio.run(panel.test_connection())
        assert result is False

    def test_show_success_updates_status(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._show_success("Success message")
        assert panel.status_text.value == "Success message"

    def test_show_error_updates_status(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._show_error("Error message")
        assert panel.status_text.value == "Error message"

    def test_show_warning_updates_status(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page)
        panel._show_warning("Warning message")
        assert panel.status_text.value == "Warning message"

    def test_compact_mode(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, compact=True)
        assert panel.compact is True

    def test_show_header_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, show_header=False)
        assert panel.show_header is False

    def test_show_save_button_false(self, mock_config_handler_db, mock_i18n_db, mock_page):
        panel = _make_db_panel(mock_config_handler_db, mock_i18n_db, mock_page, show_save_button=False)
        assert panel._show_save_button is False


class TestLocalModelConfigPanelExtended:
    def test_compact_mode(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, compact=True)
        assert panel._compact is True

    def test_show_save_button(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, show_save_button=True)
        assert panel._show_save_button is True

    def test_on_gpu_auto_change(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.gpu_auto_switch.value = False
        panel._on_gpu_auto_change(None)
        assert panel.gpu_layers_input.visible is True

    def test_on_gpu_auto_change_calls_on_change(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_change = MagicMock()
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, on_change=on_change)
        panel._on_gpu_auto_change(None)
        on_change.assert_called_once()

    def test_on_select_file_click(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.did_mount()
        with patch.object(panel.file_picker, "pick_files"):
            panel._on_select_file_click(None)
            panel.file_picker.pick_files.assert_called_once()

    def test_on_file_picked(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        mock_event = MagicMock()
        mock_event.files = [MagicMock(path="/path/to/model.gguf")]
        panel._on_file_picked(mock_event)
        assert panel.model_path_input.value == "/path/to/model.gguf"

    def test_on_file_picked_calls_on_change(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_change = MagicMock()
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, on_change=on_change)
        mock_event = MagicMock()
        mock_event.files = [MagicMock(path="/path/to/model.gguf")]
        panel._on_file_picked(mock_event)
        on_change.assert_called_once()

    def test_on_save_click_calls_on_save(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_save = MagicMock()
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, on_save=on_save)
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input.value = "300"
        panel.threads_input.value = 4
        panel.gpu_auto_switch.value = True
        panel.gpu_layers_input.value = 0
        panel.batch_input.value = "512"
        panel.ctx_input.value = "4096"
        panel.flash_attn_switch.value = True
        panel._on_save_click(None)
        on_save.assert_called_once()

    def test_did_mount_adds_file_picker(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.did_mount()
        assert panel.file_picker in mock_page.overlay

    def test_will_unmount_removes_file_picker(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.did_mount()
        panel.will_unmount()

    def test_will_unmount_no_file_picker(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.will_unmount()

    def test_on_input_change_calls_on_change(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_change = MagicMock()
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page, on_change=on_change)
        panel._on_input_change(None)
        on_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_verify_model_exception(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel.model_path_input.value = "/path/to/model.gguf"
        panel.timeout_input.value = "300"
        panel.on_verify_model = AsyncMock(side_effect=Exception("load error"))
        with patch("os.path.exists", return_value=True):
            result = await panel.async_verify_model()
        assert result is False

    def test_show_success(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel._show_success("Success message")
        assert panel.status_text.value == "Success message"

    def test_show_error(self, mock_config_handler_local, mock_i18n_local, mock_page):
        panel = _make_local_panel(mock_config_handler_local, mock_i18n_local, mock_page)
        panel._show_error("Error message")
        assert panel.status_text.value == "Error message"

    def test_set_loading_state(self, mock_config_handler_local, mock_i18n_local, mock_page):
        on_loading = MagicMock()
        panel = _make_local_panel(
            mock_config_handler_local,
            mock_i18n_local,
            mock_page,
            on_loading_change=on_loading,
            show_internal_loading=True,
        )
        panel._set_loading_state(True)
        assert panel.progress_indicator.visible is True
        assert panel.verify_button.disabled is True
        on_loading.assert_called_with(True)


class TestTushareConfigPanelExtended:
    def test_compact_mode(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, compact=True)
        assert panel._compact is True

    def test_show_save_button_false(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, show_save_button=False)
        assert panel.save_button.visible is False

    def test_show_register_link_false(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, show_register_link=False)
        assert panel._show_register_link is False

    def test_on_register_click(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        with patch("webbrowser.open_new_tab") as mock_open:
            panel._on_register_click(None)
            mock_open.assert_called_once()

    def test_did_mount_subscribes_locale(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.did_mount()
        mock_i18n.subscribe.assert_called_once()

    def test_will_unmount_unsubscribes_locale(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._locale_subscription_id = "sub_id"
        panel.will_unmount()
        mock_i18n.unsubscribe.assert_called_once_with("sub_id")

    def test_will_unmount_no_subscription(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._locale_subscription_id = None
        panel.will_unmount()
        mock_i18n.unsubscribe.assert_not_called()

    def test_set_loading_state(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_loading = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_loading_change=on_loading)
        panel._set_loading_state(True)
        assert panel.verify_button.disabled is True
        on_loading.assert_called_with(True)

    def test_set_loading_state_internal_disabled(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_loading = MagicMock()
        panel = _make_tushare_panel(
            mock_ch_for_panels,
            mock_i18n,
            mock_page,
            on_loading_change=on_loading,
            show_internal_loading=False,
        )
        panel._set_loading_state(True)
        on_loading.assert_called_with(True)

    def test_show_success(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._show_success("Success message")
        assert panel.status_text.value == "Success message"

    def test_show_error(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._show_error("Error message")
        assert panel.status_text.value == "Error message"

    def test_show_warning(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._show_warning("Warning message")
        assert panel.status_text.value == "Warning message"

    def test_safe_update(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._safe_update()

    def test_safe_update_no_page(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.page = None
        panel._safe_update()

    def test_refresh_locale(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.refresh_locale()
        mock_i18n.get.assert_called()  # 多次调用预期 (多个标签翻译)

    def test_compact_mode_hint_text(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, compact=True)
        assert panel.token_input.hint_text is not None

    @pytest.mark.asyncio
    async def test_verify_token_already_verifying(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.token_input.value = "valid_token"
        panel._is_verifying = True
        result = await panel.verify_token()
        assert result is False

    @pytest.mark.asyncio
    async def test_verify_token_with_api_probe_success(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_verify = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_verify_success=on_verify)
        panel.token_input.value = "valid_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api") as mock_pro_api,
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
        ):
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            mock_pro.trade_cal.return_value = MagicMock()

            mock_client_instance = MagicMock()
            mock_client_instance.set_token.return_value = True
            mock_client_instance.probe_api_capabilities = AsyncMock(return_value={"daily": True, "index": True})
            mock_client_cls.return_value = mock_client_instance

            mock_sm.return_value.invalidate_dependency_cache = MagicMock()

            result = await panel.verify_token()

        assert result is True
        mock_ch_for_panels.save_token.assert_called_once_with("valid_token")
        on_verify.assert_called_once_with("valid_token")

    @pytest.mark.asyncio
    async def test_verify_token_with_api_probe_restricted_apis(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_verify = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_verify_success=on_verify)
        panel.token_input.value = "valid_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api") as mock_pro_api,
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
        ):
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            mock_pro.trade_cal.return_value = MagicMock()

            mock_client_instance = MagicMock()
            mock_client_instance.set_token.return_value = True
            mock_client_instance.probe_api_capabilities = AsyncMock(return_value={"daily": True, "premium": False})
            mock_client_cls.return_value = mock_client_instance

            mock_sm.return_value.invalidate_dependency_cache = MagicMock()

            result = await panel.verify_token()

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_token_with_api_probe_exception(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_verify = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_verify_success=on_verify)
        panel.token_input.value = "valid_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api") as mock_pro_api,
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
        ):
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            mock_pro.trade_cal.return_value = MagicMock()

            mock_client_instance = MagicMock()
            mock_client_instance.set_token.return_value = True
            mock_client_instance.probe_api_capabilities = AsyncMock(side_effect=Exception("probe error"))
            mock_client_cls.return_value = mock_client_instance

            result = await panel.verify_token()

        assert result is True

    @pytest.mark.asyncio
    async def test_verify_token_with_api_probe_empty_results(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_verify = MagicMock()
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page, on_verify_success=on_verify)
        panel.token_input.value = "valid_token"

        with (
            patch("tushare.set_token"),
            patch("tushare.pro_api") as mock_pro_api,
            patch("data.external.tushare_client.TushareClient") as mock_client_cls,
            patch("strategies.all_strategies.StrategyManager") as mock_sm,
        ):
            mock_pro = MagicMock()
            mock_pro_api.return_value = mock_pro
            mock_pro.trade_cal.return_value = MagicMock()

            mock_client_instance = MagicMock()
            mock_client_instance.set_token.return_value = True
            mock_client_instance.probe_api_capabilities = AsyncMock(return_value={})
            mock_client_cls.return_value = mock_client_instance

            mock_sm.return_value.invalidate_dependency_cache = MagicMock()

            result = await panel.verify_token()

        assert result is True

    def test_on_verify_click_no_page(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel.page = None
        panel._on_verify_click(MagicMock())

    def test_on_verify_click_already_verifying(self, mock_ch_for_panels, mock_i18n, mock_page):
        panel = _make_tushare_panel(mock_ch_for_panels, mock_i18n, mock_page)
        panel._is_verifying = True
        panel._on_verify_click(MagicMock())
        assert panel.status_text.value != "" or panel._is_verifying is True

    def test_set_loading_state_with_callback(self, mock_ch_for_panels, mock_i18n, mock_page):
        on_loading = MagicMock()
        panel = _make_tushare_panel(
            mock_ch_for_panels,
            mock_i18n,
            mock_page,
            on_loading_change=on_loading,
            show_internal_loading=False,
        )
        panel._set_loading_state(True)
        on_loading.assert_called_with(True)


class TestCallbackInjection:
    """验证回调注入机制：Component 通过回调调用 Service，而非直接导入。"""

    @pytest.mark.asyncio
    async def test_on_test_connection_callback_called_with_correct_params(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_callback = AsyncMock(return_value={"success": True})
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=mock_callback,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        with patch.object(panel, "_safe_update"):
            await panel._on_llm_test_connection()
        mock_callback.assert_called_once()
        call_kwargs = mock_callback.call_args
        assert call_kwargs.kwargs["provider"] == "deepseek"
        assert call_kwargs.kwargs["model"] == "deepseek-chat"
        assert call_kwargs.kwargs["api_key"] == "sk-test"

    @pytest.mark.asyncio
    async def test_on_reload_service_called_after_save(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_reload = AsyncMock()
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_reload_service=mock_reload,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        with patch.object(panel, "_safe_update"):
            await panel._save_config()
        mock_reload.assert_called_once()

    @pytest.mark.asyncio
    async def test_on_verify_model_callback_called_with_correct_params(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        mock_callback = AsyncMock(return_value=True)
        panel = _make_local_panel(
            mock_config_handler_local,
            mock_i18n_local,
            mock_page,
            on_verify_model=mock_callback,
        )
        panel.model_path_input.value = "C:/path/to/model.gguf"
        with (
            patch("os.path.exists", return_value=True),
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_show_success"),
        ):
            await panel.async_verify_model()
        mock_callback.assert_called_once()
        call_args = mock_callback.call_args
        assert call_args.args[0] == "C:/path/to/model.gguf"
        assert isinstance(call_args.args[1], dict)

    @pytest.mark.asyncio
    async def test_async_verify_connection_uses_callback_not_service(
        self, mock_config_handler_llm, mock_i18n_llm, mock_llm_providers, mock_page
    ):
        mock_callback = AsyncMock(return_value={"success": True})
        panel = _make_llm_panel(
            mock_config_handler_llm,
            mock_i18n_llm,
            mock_llm_providers,
            mock_page,
            on_test_connection=mock_callback,
        )
        panel.api_key_input.value = "sk-test"
        panel.model_dropdown.value = "deepseek-chat"
        with patch.object(panel, "_safe_update"):
            result = await panel.async_verify_connection()
        assert result is True
        mock_callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_verify_model_uses_callback_not_service(
        self, mock_config_handler_local, mock_i18n_local, mock_page
    ):
        mock_callback = AsyncMock(return_value=True)
        panel = _make_local_panel(
            mock_config_handler_local,
            mock_i18n_local,
            mock_page,
            on_verify_model=mock_callback,
        )
        panel.model_path_input.value = "C:/path/to/model.gguf"
        with (
            patch("os.path.exists", return_value=True),
            patch.object(panel, "_safe_update"),
            patch.object(panel, "_show_success"),
        ):
            result = await panel.async_verify_model()
        assert result is True
        mock_callback.assert_called_once()
