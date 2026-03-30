"""
LLM Configuration Panel Component

Provides a unified UI for configuring multiple LLM providers with:
- Dynamic provider selection
- Provider-specific model lists
- Azure OpenAI special handling
- Connection testing
- Dynamic model refresh
"""

import logging
from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from utils.config_handler import ConfigHandler
from utils.llm_providers import (
    AZURE_API_VERSIONS,
    AZURE_DEFAULT_API_VERSION,
    LLM_PROVIDERS,
)

logger = logging.getLogger(__name__)

MODELS_API_COMPATIBLE = {
    "openai",
    "deepseek",
    "qwen",
    "zhipu",
    "moonshot",
    "mistral",
    "minimax",
    "custom",
}


class LLMConfigPanel(ft.Container):
    """
    LLM Configuration Panel with multi-provider support.

    Features:
    - Provider dropdown with icons
    - Dynamic model dropdown based on provider
    - Azure-specific fields (resource name, deployment, api version)
    - Connection test button
    - Dynamic model refresh
    - i18n support with hot reload

    Args:
        on_save: Callback when save button is clicked (optional)
        on_test_connection: Callback for connection test (optional)
        show_save_button: Whether to show the save button (default: False)
        compact: Whether to use compact layout for wizard (default: False)
    """

    def __init__(
        self,
        on_save: Callable | None = None,
        on_test_connection: Callable | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_save_button: bool = False,
        compact: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.on_save = on_save
        self.on_test_connection = on_test_connection
        self.on_loading_change = on_loading_change
        self._show_save_button = show_save_button
        self._compact = compact

        self._current_provider = "deepseek"
        self._is_azure = False
        self._api_key_modified = False

        self._build_ui()

    def _build_ui(self):
        # 统一尺寸常量，避免硬编码
        input_width = 360
        refresh_icon_offset = input_width + 5
        container_width = input_width + 60

        self.provider_dropdown = ft.Dropdown(
            label=I18n.get("llm_select_provider"),
            options=self._build_provider_options(),
            value=self._current_provider,
            on_change=self._on_provider_change,
            width=input_width,
        )

        self.model_dropdown = ft.Dropdown(
            label=I18n.get("llm_select_model"),
            options=self._build_model_options(self._current_provider),
            width=input_width,
        )

        self.custom_model_input = ft.Dropdown(
            label=I18n.get("llm_custom_model"),
            visible=False,
            width=input_width,
            editable=True,
            options=[],
        )

        self.base_url_input = ft.TextField(
            label=I18n.get("llm_base_url"),
            value=LLM_PROVIDERS.get(self._current_provider, {}).get("base_url", ""),
            width=input_width,
        )

        self.api_key_input = ft.TextField(
            label=I18n.get("llm_api_key"),
            password=True,
            can_reveal_password=True,
            width=input_width,
            on_change=self._on_api_key_change,
        )

        self.azure_resource_input = ft.TextField(
            label=I18n.get("llm_azure_resource_name"),
            visible=False,
            width=input_width,
        )

        self.azure_deployment_input = ft.TextField(
            label=I18n.get("llm_azure_deployment_name"),
            visible=False,
            width=input_width,
        )

        self.azure_version_input = ft.Dropdown(
            label=I18n.get("llm_azure_api_version"),
            options=[ft.dropdown.Option(v) for v in AZURE_API_VERSIONS],
            value=AZURE_DEFAULT_API_VERSION,
            visible=False,
            width=input_width,
        )

        self.status_text = ft.Text(
            value="",
            color=ft.Colors.GREEN if ft.Colors else "green",
            size=12,
        )

        self.test_button = ft.ElevatedButton(
            text=I18n.get("llm_test_connection"),
            on_click=self._on_test_click,
            icon=ft.Icons.CABLE if ft.Icons else None,
        )

        self.refresh_models_button = ft.IconButton(
            icon=ft.Icons.REFRESH if ft.Icons else None,
            tooltip=I18n.get("llm_refresh_models"),
            on_click=self._on_refresh_click,
        )

        self.save_button = ft.ElevatedButton(
            text=I18n.get("settings_save_config"),
            on_click=self._on_save_click,
            icon=ft.Icons.SAVE if ft.Icons else None,
            visible=self._show_save_button,
        )

        compact_main_align = (
            ft.MainAxisAlignment.CENTER if self._compact else ft.MainAxisAlignment.START
        )
        compact_cross_align = (
            ft.CrossAxisAlignment.CENTER
            if self._compact
            else ft.CrossAxisAlignment.START
        )

        provider_row = ft.Row(
            controls=[
                self.provider_dropdown,
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        self.model_row = ft.Stack(
            controls=[
                self.model_dropdown,
                self.custom_model_input,
                ft.Container(
                    content=self.refresh_models_button,
                    left=refresh_icon_offset,
                    top=10,
                ),
            ],
            clip_behavior=ft.ClipBehavior.NONE,
        )

        self.azure_row = ft.Column(
            controls=[
                self.azure_resource_input,
                self.azure_deployment_input,
                self.azure_version_input,
            ],
            visible=False,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        links_row = self._build_links_row()

        action_buttons = ft.Row(
            controls=[
                self.test_button,
                self.save_button,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )

        header_text = ft.Text(
            value=I18n.get("settings_sec_ai"),
            size=16 if not self._compact else 14,
            weight=ft.FontWeight.BOLD,
            visible=not self._compact,
        )

        form_content = ft.Column(
            controls=[
                header_text,
                provider_row,
                self.model_row,
                self.base_url_input,
                self.api_key_input,
                self.azure_row,
                action_buttons,
                ft.Row([self.status_text], alignment=ft.MainAxisAlignment.CENTER),
                links_row,
            ],
            spacing=10 if not self._compact else 6,
            horizontal_alignment=ft.CrossAxisAlignment.START,
        )

        if self._compact:
            self.content = ft.Container(
                content=form_content,
                width=container_width,
                alignment=ft.alignment.center,
            )
        else:
            self.content = form_content

        self._load_config()

    def _build_provider_options(self) -> list:
        options = []

        domestic = ft.dropdown.Option(I18n.get("llm_provider_domestic"))
        domestic.disabled = True
        options.append(domestic)

        for provider_id in ["deepseek", "qwen", "zhipu", "moonshot", "minimax"]:
            provider = LLM_PROVIDERS.get(provider_id)
            if provider:
                options.append(
                    ft.dropdown.Option(
                        key=provider_id,
                        text=provider.get("name", provider_id),
                    )
                )

        international = ft.dropdown.Option(I18n.get("llm_provider_international"))
        international.disabled = True
        options.append(international)

        for provider_id in ["openai", "azure", "anthropic", "google", "mistral"]:
            provider = LLM_PROVIDERS.get(provider_id)
            if provider:
                options.append(
                    ft.dropdown.Option(
                        key=provider_id,
                        text=provider.get("name", provider_id),
                    )
                )

        custom = ft.dropdown.Option(I18n.get("llm_provider_custom_group"))
        custom.disabled = True
        options.append(custom)

        options.append(
            ft.dropdown.Option(
                key="custom",
                text=I18n.get("llm_provider_custom"),
            )
        )

        return options

    def _build_model_options(self, provider_id: str) -> list:
        provider = LLM_PROVIDERS.get(provider_id, {})
        models = provider.get("models", [])

        options = []
        for model in models:
            text = model.get("name", model.get("id", ""))
            tag = model.get("tag", "")
            if tag:
                text = f"{text} ({tag})"
            options.append(
                ft.dropdown.Option(
                    key=model.get("id"),
                    text=text,
                )
            )

        return options

    def _build_links_row(self) -> ft.Row:
        provider = LLM_PROVIDERS.get(self._current_provider, {})

        links = []

        # 仅在向导特供的 compact 模式下采用紧凑内边距，释放高达 70px+ 的占用
        compact_btn_style = (
            ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=4))
            if self._compact
            else None
        )

        console_url = provider.get("console_url")
        if console_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_get_api_key"),
                    url=console_url,
                    icon=ft.Icons.KEY if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        pricing_url = provider.get("pricing_url")
        if pricing_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_view_pricing"),
                    url=pricing_url,
                    icon=ft.Icons.ATTACH_MONEY if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        models_url = provider.get("models_url")
        if models_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_view_models"),
                    url=models_url,
                    icon=ft.Icons.LIST if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        return ft.Row(
            controls=links,
            alignment=ft.MainAxisAlignment.CENTER
            if self._compact
            else ft.MainAxisAlignment.START,
            wrap=not self._compact,
            spacing=8 if self._compact else 10,
        )

    def _load_config(self):
        llm_config = ConfigHandler.get_llm_config()

        provider = llm_config.get("provider", "deepseek")
        model = llm_config.get("model", "")
        base_url = llm_config.get("base_url", "")
        api_key = llm_config.get("api_key", "")

        self._current_provider = provider
        self.provider_dropdown.value = provider

        self.model_dropdown.options = self._build_model_options(provider)

        if provider == "azure":
            self._is_azure = True
            self.azure_resource_input.value = llm_config.get("azure_resource_name", "")
            self.azure_deployment_input.value = (
                llm_config.get("azure_deployment_name", "") or model
            )
            self.azure_version_input.value = llm_config.get(
                "api_version", AZURE_DEFAULT_API_VERSION
            )
            self._show_azure_fields(True)
            self.base_url_input.value = ""
            self.refresh_models_button.visible = False
        else:
            self._is_azure = False
            self._show_azure_fields(False)

            if provider == "custom":
                self._load_custom_model_history(provider)
                self.custom_model_input.visible = True
                self.model_dropdown.visible = False
                self.base_url_input.read_only = False
                self.custom_model_input.value = model
            else:
                models = LLM_PROVIDERS.get(provider, {}).get("models", [])
                model_ids = [m.get("id") for m in models]
                if model and model in model_ids:
                    self.model_dropdown.value = model
                elif model:
                    self.model_dropdown.visible = False
                    self.custom_model_input.visible = True
                    self.custom_model_input.value = model
                elif models:
                    recommended = next(
                        (m.get("id") for m in models if m.get("tag") == "推荐"), None
                    )
                    self.model_dropdown.value = recommended or models[0].get("id")

            provider_config = LLM_PROVIDERS.get(provider, {})
            self.base_url_input.value = base_url or provider_config.get("base_url", "")

            self.refresh_models_button.visible = provider in MODELS_API_COMPATIBLE

        self.api_key_input.value = api_key
        self._api_key_modified = False

    def _on_api_key_change(self, e):
        self._api_key_modified = True

    def _show_azure_fields(self, show: bool):
        self.azure_row.visible = show
        self.azure_resource_input.visible = show
        self.azure_deployment_input.visible = show
        self.azure_version_input.visible = show
        self.base_url_input.visible = not show
        self.model_row.visible = not show
        self.model_dropdown.visible = not show
        if show:
            self.custom_model_input.visible = False

    def _on_provider_change(self, e):
        provider_id = e.control.value
        self._current_provider = provider_id

        provider = LLM_PROVIDERS.get(provider_id, {})
        provider_name = provider.get("name", provider_id)

        self.model_dropdown.options = self._build_model_options(provider_id)
        self.model_dropdown.value = None

        self.api_key_input.value = ""
        self._api_key_modified = False

        if provider_id == "azure":
            self._is_azure = True
            self._show_azure_fields(True)
            self.base_url_input.value = ""
            self.custom_model_input.visible = False
            self.refresh_models_button.visible = False
            self.status_text.value = I18n.get("llm_switch_provider_hint").format(
                provider=provider_name
            )
        elif provider_id == "custom":
            self._is_azure = False
            self._show_azure_fields(False)
            self.custom_model_input.visible = True
            self.model_dropdown.visible = False
            self.refresh_models_button.visible = True
            self.base_url_input.value = ""
            self.base_url_input.read_only = False
            self.status_text.value = I18n.get("llm_switch_provider_hint").format(
                provider=provider_name
            )
            self._load_custom_model_history(provider_id)
        else:
            self._is_azure = False
            self._show_azure_fields(False)
            self.custom_model_input.visible = False
            self.model_dropdown.visible = True
            self.refresh_models_button.visible = provider_id in MODELS_API_COMPATIBLE
            self.base_url_input.value = provider.get("base_url", "")
            self.base_url_input.read_only = True
            self.status_text.value = I18n.get("llm_switch_provider_hint").format(
                provider=provider_name
            )

            models = provider.get("models", [])
            if models:
                recommended = next(
                    (m.get("id") for m in models if m.get("tag") == "推荐"), None
                )
                self.model_dropdown.value = recommended or models[0].get("id")

        self._update_links_row()
        self.update()

    def _update_links_row(self):
        provider = LLM_PROVIDERS.get(self._current_provider, {})

        links = []

        compact_btn_style = (
            ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=4))
            if self._compact
            else None
        )

        console_url = provider.get("console_url")
        if console_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_get_api_key"),
                    url=console_url,
                    icon=ft.Icons.KEY if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        pricing_url = provider.get("pricing_url")
        if pricing_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_view_pricing"),
                    url=pricing_url,
                    icon=ft.Icons.ATTACH_MONEY if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        models_url = provider.get("models_url")
        if models_url:
            links.append(
                ft.TextButton(
                    text=I18n.get("llm_view_models"),
                    url=models_url,
                    icon=ft.Icons.LIST if ft.Icons else None,
                    style=compact_btn_style,
                )
            )

        content_col = (
            self.content.content
            if isinstance(self.content, ft.Container)
            else self.content
        )
        links_row = content_col.controls[-1]
        links_row.controls = links

    def _load_custom_model_history(self, provider_id: str):
        """Load custom model history for the given provider."""
        llm_config = ConfigHandler.get_llm_config()
        custom_models = llm_config.get("custom_models", {})

        provider_models = custom_models.get(provider_id, [])

        self.custom_model_input.options = [
            ft.dropdown.Option(model_id) for model_id in provider_models
        ]

    def _on_test_click(self, e):
        if not self.page:
            return

        self.page.run_task(self._on_llm_test_connection)

    async def _on_llm_test_connection(self):
        api_key = self.api_key_input.value

        if not api_key:
            self.status_text.value = I18n.get("llm_test_need_key")
            self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
            self.update()
            return

        self.status_text.value = I18n.get("llm_testing")
        self.status_text.color = ft.Colors.BLUE if ft.Colors else "blue"
        self.test_button.disabled = True
        if self.on_loading_change:
            self.on_loading_change(True)
        self.update()

        try:
            provider = self._current_provider

            kwargs = {}
            if self._is_azure:
                resource_name = self.azure_resource_input.value
                deployment_name = self.azure_deployment_input.value
                api_version = self.azure_version_input.value

                if not resource_name:
                    self.status_text.value = I18n.get("llm_azure_need_resource")
                    self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
                    self.update()
                    return
                if not deployment_name:
                    self.status_text.value = I18n.get("llm_azure_need_deployment")
                    self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
                    self.update()
                    return

                model = deployment_name
                kwargs["api_version"] = api_version
                kwargs["azure_resource_name"] = resource_name
                base_url = ""
            else:
                model = self.model_dropdown.value or self.custom_model_input.value
                base_url = self.base_url_input.value

            if self.on_test_connection:
                result = await self.on_test_connection(
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    **kwargs,
                )
            else:
                from services.ai_service import AIService

                result = await AIService.test_connection(
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    **kwargs,
                )

            if result.get("success"):
                self.status_text.value = I18n.get("llm_test_success")
                self.status_text.color = ft.Colors.GREEN if ft.Colors else "green"
            else:
                self.status_text.value = (
                    f"{I18n.get('llm_test_failed')}: {result.get('message', '')}"
                )
                self.status_text.color = ft.Colors.RED if ft.Colors else "red"

        except Exception as ex:
            from services.ai_service import _classify_api_error

            error_info = _classify_api_error(ex)
            self.status_text.value = (
                f"{I18n.get('llm_test_failed')}: {error_info['message']}"
            )
            self.status_text.color = ft.Colors.RED if ft.Colors else "red"
            logger.error(f"[LLMConfigPanel] Test connection error: {ex}")

        finally:
            self.test_button.disabled = False
            if self.on_loading_change:
                self.on_loading_change(False)
            self.update()

    def _on_refresh_click(self, e):
        if not self.page:
            return

        self.page.run_task(self._refresh_models)

    async def _refresh_models(self):
        api_key = self.api_key_input.value
        raw_base_url = self.base_url_input.value
        base_url = self._normalize_base_url(raw_base_url)

        if not api_key:
            self.status_text.value = I18n.get("llm_refresh_need_key")
            self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
            self.update()
            return

        if not base_url:
            self.status_text.value = I18n.get("llm_refresh_need_url")
            self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
            self.update()
            return

        self.status_text.value = I18n.get("llm_refreshing")
        self.status_text.color = ft.Colors.BLUE if ft.Colors else "blue"
        self.refresh_models_button.disabled = True
        if self.on_loading_change:
            self.on_loading_change(True)
        self.update()

        try:
            import httpx

            models_url = f"{base_url.rstrip('/')}/models"

            async with httpx.AsyncClient() as client:
                response = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=10.0,
                )
                response.raise_for_status()
                data = response.json()

            models = data.get("data", [])
            model_ids = sorted([m["id"] for m in models if m.get("id")])

            if not model_ids:
                self.status_text.value = I18n.get("llm_refresh_empty")
                self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
                return

            self.model_dropdown.options = [ft.dropdown.Option(m) for m in model_ids]

            if self.model_dropdown.value not in model_ids:
                self.model_dropdown.value = model_ids[0]

            self.model_dropdown.update()

            self.status_text.value = I18n.get(
                "llm_refresh_success", count=len(model_ids)
            )
            self.status_text.color = ft.Colors.GREEN if ft.Colors else "green"

        except Exception as ex:
            from ui.i18n import classify_error

            error_info = classify_error(ex, context="llm")
            self.status_text.value = (
                f"{I18n.get('llm_refresh_failed')}: {error_info['message']}"
            )
            self.status_text.color = ft.Colors.RED if ft.Colors else "red"
            logger.error(f"[LLMConfigPanel] Refresh models error: {ex}")

        finally:
            self.refresh_models_button.disabled = False
            if self.on_loading_change:
                self.on_loading_change(False)
            self.update()

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """
        Normalize base URL to a clean API endpoint.

        Examples:
            https://api.deepseek.com/v1/chat/completions -> https://api.deepseek.com
            https://api.openai.com/v1 -> https://api.openai.com
            https://api.example.com/ -> https://api.example.com
        """
        if not url:
            return ""

        url = url.strip()

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        from urllib.parse import urlparse

        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"

        return base

    def _get_current_base_url(self) -> str:
        if self._is_azure:
            return ""
        return self.base_url_input.value

    def _on_save_click(self, e):
        if not self.page:
            return

        self.page.run_task(self._save_config)

    async def _save_config(self):
        provider = self._current_provider

        if self._api_key_modified:
            api_key = self.api_key_input.value
        else:
            api_key = None

        kwargs = {}

        if self._is_azure:
            resource_name = self.azure_resource_input.value
            deployment_name = self.azure_deployment_input.value
            api_version = self.azure_version_input.value

            if not resource_name:
                self.status_text.value = I18n.get("llm_azure_need_resource")
                self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
                self.update()
                return
            if not deployment_name:
                self.status_text.value = I18n.get("llm_azure_need_deployment")
                self.status_text.color = ft.Colors.ORANGE if ft.Colors else "orange"
                self.update()
                return

            model = deployment_name
            base_url = ""

            kwargs["api_version"] = api_version
            kwargs["azure_resource_name"] = resource_name
            kwargs["azure_deployment_name"] = deployment_name
        else:
            model = self.model_dropdown.value or self.custom_model_input.value
            base_url = self.base_url_input.value

            if model and (
                provider == "custom"
                or model
                not in [
                    m.get("id")
                    for m in LLM_PROVIDERS.get(provider, {}).get("models", [])
                ]
            ):
                llm_config = ConfigHandler.get_llm_config()
                custom_models = llm_config.get("custom_models", {})

                if provider not in custom_models:
                    custom_models[provider] = []

                if model not in custom_models[provider]:
                    custom_models[provider].append(model)
                    custom_models[provider] = custom_models[provider][-20:]

                kwargs["custom_models"] = custom_models

        try:
            ConfigHandler.save_llm_config(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

            self._api_key_modified = False

            from services.ai_service import AIService

            await AIService().reload_config()

            self.status_text.value = I18n.get("settings_verify_success")
            self.status_text.color = ft.Colors.GREEN if ft.Colors else "green"

            if self.on_save:
                self.on_save()

        except Exception as ex:
            from ui.i18n import classify_error

            error_info = classify_error(ex, context="llm")
            self.status_text.value = (
                f"{I18n.get('settings_save_failed')}: {error_info['message']}"
            )
            self.status_text.color = ft.Colors.RED if ft.Colors else "red"
            logger.error(f"[LLMConfigPanel] Save config error: {ex}")

        self.update()

    @property
    def api_key_modified(self) -> bool:
        """Check if API key has been modified by user."""
        return self._api_key_modified

    def get_current_config(self) -> dict:
        """
        Get current configuration values from the panel.

        Returns:
            dict with keys: provider, model, base_url, api_key, and Azure-specific fields
        """
        provider = self._current_provider
        base_url = self.base_url_input.value
        api_key = self.api_key_input.value

        result = {
            "provider": provider,
            "base_url": base_url,
            "api_key": api_key,
        }

        if self._is_azure:
            resource_name = self.azure_resource_input.value
            deployment_name = self.azure_deployment_input.value
            api_version = self.azure_version_input.value

            result["model"] = deployment_name
            result["api_version"] = api_version
            result["azure_resource_name"] = resource_name
            result["azure_deployment_name"] = deployment_name
        else:
            result["model"] = self.model_dropdown.value or self.custom_model_input.value

        return result

    def save_current_config(self) -> bool:
        """
        Save current configuration to ConfigHandler.
        This is a sync method for external callers.

        Returns:
            bool: True if saved successfully
        """
        config = self.get_current_config()

        kwargs = {}
        if self._is_azure:
            kwargs["api_version"] = config.get("api_version", AZURE_DEFAULT_API_VERSION)
            kwargs["azure_resource_name"] = config.get("azure_resource_name", "")
            kwargs["azure_deployment_name"] = config.get("azure_deployment_name", "")

        try:
            ConfigHandler.save_llm_config(
                provider=config["provider"],
                model=config["model"],
                base_url=config["base_url"],
                api_key=config["api_key"],
                **kwargs,
            )
            return True
        except Exception as e:
            logger.error(f"[LLMConfigPanel] Save current config error: {e}")
            return False

    def did_mount(self):
        I18n.subscribe(self._on_locale_change)

    def will_unmount(self):
        I18n.unsubscribe(self._on_locale_change)

    def _on_locale_change(self, new_locale: str = None):
        self.provider_dropdown.label = I18n.get("llm_select_provider")
        self.model_dropdown.label = I18n.get("llm_select_model")
        self.custom_model_input.label = I18n.get("llm_custom_model")
        self.base_url_input.label = I18n.get("llm_base_url")
        self.api_key_input.label = I18n.get("llm_api_key")
        self.azure_resource_input.label = I18n.get("llm_azure_resource_name")
        self.azure_deployment_input.label = I18n.get("llm_azure_deployment_name")
        self.azure_version_input.label = I18n.get("llm_azure_api_version")
        self.test_button.text = I18n.get("llm_test_connection")
        self.refresh_models_button.tooltip = I18n.get("llm_refresh_models")
        self.save_button.text = I18n.get("settings_save_config")

        self.provider_dropdown.options = self._build_provider_options()
        self.provider_dropdown.value = self._current_provider

        self._update_links_row()

        self.update()
