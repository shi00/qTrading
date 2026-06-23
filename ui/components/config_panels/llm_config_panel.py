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
import re
from collections.abc import Awaitable, Callable

import flet as ft

from ui.components.settings_widgets import SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from utils.llm_providers import (
    AZURE_API_VERSIONS,
    AZURE_DEFAULT_API_VERSION,
    LLM_PROVIDERS,
    get_display_tag,
    is_recommended_model,
)
from utils.sanitizers import DataSanitizer

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
        on_test_connection: Callback for connection test (required)
        on_save: Callback when save button is clicked (optional)
        on_reload_service: Callback to reload service after config save (optional)
        on_loading_change: Callback when loading state changes (optional)
        show_save_button: Whether to show the save button (default: False)
        compact: Whether to use compact layout for wizard (default: False)
    """

    _REFRESH_TIMEOUT = 10.0
    _MAX_CUSTOM_MODELS = 50

    def __init__(
        self,
        on_test_connection: Callable[..., Awaitable[dict]],
        on_save: Callable | None = None,
        on_reload_service: Callable[[], Awaitable[None]] | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_save_button: bool = False,
        compact: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.on_test_connection = on_test_connection
        self.on_save = on_save
        self.on_reload_service = on_reload_service
        self.on_loading_change = on_loading_change
        self._show_save_button = show_save_button
        self._compact = compact

        self._current_provider = "deepseek"
        self._is_azure = False
        self._api_key_modified = False
        self._is_verifying = False

        self._build_ui()

    def _build_ui(self):  # pragma: no cover
        input_width = 360
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

        self.status_icon = ft.Icon(visible=False, size=16)
        self.status_text = ft.Text(
            value="",
            size=12,
        )

        self.test_button = ft.ElevatedButton(
            text=I18n.get("llm_test_connection"),
            on_click=self._on_test_click,
            icon=ft.Icons.CABLE if ft.Icons else None,
            style=AppStyles.secondary_button(),
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
            style=AppStyles.primary_button(),
        )

        provider_row = ft.Row(
            controls=[
                self.provider_dropdown,
            ],
            alignment=ft.MainAxisAlignment.START,
        )

        self.model_row = ft.Row(
            controls=[
                self.model_dropdown,
                self.custom_model_input,
                self.refresh_models_button,
            ],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.END,
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

        self._links_row = self._build_links_row()

        action_buttons = ft.Row(
            controls=[
                self.test_button,
                self.save_button,
            ],
            alignment=ft.MainAxisAlignment.CENTER,
        )

        self.section_header = SectionHeader(I18n.get("settings_sec_ai"), title_key="settings_sec_ai")
        self.section_header.visible = not self._compact

        form_content = ft.Column(
            controls=[
                self.section_header,
                provider_row,
                self.model_row,
                self.base_url_input,
                self.api_key_input,
                self.azure_row,
                action_buttons,
                ft.Row(
                    [self.status_icon, self.status_text],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=5,
                ),
                self._links_row,
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

    @staticmethod
    def _get_provider_name(provider: dict, provider_id: str) -> str:
        if I18n.current_locale() == "zh_CN":
            return provider.get("name", provider_id)
        return provider.get("name_en", provider.get("name", provider_id))

    def _build_provider_options(self) -> list:  # pragma: no cover
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
                        text=self._get_provider_name(provider, provider_id),
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
                        text=self._get_provider_name(provider, provider_id),
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

    def _build_model_options(self, provider_id: str) -> list:  # pragma: no cover
        provider = LLM_PROVIDERS.get(provider_id, {})
        models = provider.get("models", [])

        options = []
        for model in models:
            text = model.get("name", model.get("id", ""))
            tag = model.get("tag", "")
            display_tag = I18n.get(get_display_tag(tag), default=get_display_tag(tag))
            if display_tag:
                text = f"{text} ({display_tag})"
            options.append(
                ft.dropdown.Option(
                    key=model.get("id"),
                    text=text,
                )
            )

        return options

    def _build_links_row(self) -> ft.Row:  # pragma: no cover
        provider = LLM_PROVIDERS.get(self._current_provider, {})

        links = []

        # 仅在向导特供的 compact 模式下采用紧凑内边距，释放高达 70px+ 的占用
        compact_btn_style = ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=4)) if self._compact else None

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
            alignment=ft.MainAxisAlignment.CENTER if self._compact else ft.MainAxisAlignment.START,
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
            self.azure_deployment_input.value = llm_config.get("azure_deployment_name", "") or model
            self.azure_version_input.value = llm_config.get("api_version", AZURE_DEFAULT_API_VERSION)
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
                    self._load_custom_model_history(provider)
                elif models:
                    recommended = next(
                        (m.get("id") for m in models if is_recommended_model(m)),
                        None,
                    )
                    self.model_dropdown.value = recommended or models[0].get("id")

            provider_config = LLM_PROVIDERS.get(provider, {})
            self.base_url_input.value = base_url or provider_config.get("base_url", "")

            self.refresh_models_button.visible = provider in MODELS_API_COMPATIBLE

        self.api_key_input.value = api_key
        self._api_key_modified = False

    def reload_config(self):  # pragma: no cover
        self._load_config()
        self._safe_update()

    def _on_api_key_change(self, e):
        self._api_key_modified = True

    def _show_azure_fields(self, show: bool):  # pragma: no cover
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
        provider_name = self._get_provider_name(provider, provider_id)

        self.model_dropdown.options = self._build_model_options(provider_id)
        self.model_dropdown.value = None

        # 尝试加载该供应商已存储的专属凭证（不回退到全局 Key，避免显示错误供应商的 Key）
        stored_cred = ConfigHandler.get_provider_credential(provider_id, fallback_to_global=False)
        stored_key = stored_cred.get("api_key", "") or ""
        stored_base_url = stored_cred.get("base_url", "")

        self.api_key_input.value = stored_key
        # Do NOT mark as modified when loading stored key - only user edits should trigger modification
        self._api_key_modified = False

        if provider_id == "azure":
            self._is_azure = True
            self._show_azure_fields(True)
            self.base_url_input.value = ""
            self.custom_model_input.visible = False
            self.refresh_models_button.visible = False
            self._show_info(I18n.get("llm_switch_provider_hint").format(provider=provider_name))
        elif provider_id == "custom":
            self._is_azure = False
            self._show_azure_fields(False)
            self.custom_model_input.visible = True
            self.model_dropdown.visible = False
            self.refresh_models_button.visible = True
            self.base_url_input.value = stored_base_url
            self.base_url_input.read_only = False
            self._show_info(I18n.get("llm_switch_provider_hint").format(provider=provider_name))
            self._load_custom_model_history(provider_id)
        else:
            self._is_azure = False
            self._show_azure_fields(False)
            self.custom_model_input.visible = False
            self.model_dropdown.visible = True
            self.refresh_models_button.visible = provider_id in MODELS_API_COMPATIBLE
            self.base_url_input.value = stored_base_url or provider.get("base_url", "")
            self.base_url_input.read_only = True
            self._show_info(I18n.get("llm_switch_provider_hint").format(provider=provider_name))

            models = provider.get("models", [])
            if models:
                recommended = next(
                    (m.get("id") for m in models if is_recommended_model(m)),
                    None,
                )
                self.model_dropdown.value = recommended or models[0].get("id")

        self._update_links_row()
        self.update()

    def _update_links_row(self):  # pragma: no cover
        provider = LLM_PROVIDERS.get(self._current_provider, {})

        links = []

        compact_btn_style = ft.ButtonStyle(padding=ft.padding.symmetric(horizontal=4)) if self._compact else None

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

        self._links_row.controls = links

    def _load_custom_model_history(self, provider_id: str):  # pragma: no cover
        """Load custom model history for the given provider."""
        llm_config = ConfigHandler.get_llm_config()
        custom_models = llm_config.get("custom_models", {})

        provider_models = custom_models.get(provider_id, [])

        self.custom_model_input.options = [ft.dropdown.Option(model_id) for model_id in provider_models]

    def _on_test_click(self, e):  # pragma: no cover
        if not self.page:
            return

        if self._is_verifying:
            self._show_warning(I18n.get("llm_testing_in_progress"))
            return

        self.page.run_task(self._on_llm_test_connection)

    def _acquire_verify_lock(self) -> bool:
        """尝试获取验证锁。返回 True 表示成功获取，False 表示已有验证在执行。"""
        if self._is_verifying:
            logger.warning("[LLMConfigPanel] Verification already in progress")
            return False
        self._is_verifying = True
        return True

    def _validate_azure_fields(self) -> tuple[bool, str, str, str | None]:
        """
        验证 Azure 专用字段，返回 (is_valid, resource_name, deployment_name, api_version)。

        验证失败时自动显示警告提示。
        """
        resource_name = self.azure_resource_input.value
        deployment_name = self.azure_deployment_input.value
        api_version = self.azure_version_input.value

        if not resource_name:
            self._show_warning(I18n.get("llm_azure_need_resource"))
            return False, "", "", ""
        if not deployment_name:
            self._show_warning(I18n.get("llm_azure_need_deployment"))
            return False, "", "", ""

        return True, resource_name, deployment_name, api_version

    async def _on_llm_test_connection(self):
        api_key = (self.api_key_input.value or "").strip()

        if not api_key:
            self._show_warning(I18n.get("llm_test_need_key"))
            return

        # Check if model is blank (whitespace-only counts as blank)
        model_raw = self.model_dropdown.value or self.custom_model_input.value
        model = (model_raw or "").strip()
        if not model:
            self._show_warning(I18n.get("llm_test_need_model"))
            return

        if not self._acquire_verify_lock():
            self._show_warning(I18n.get("llm_testing_in_progress"))
            return
        self._show_info(I18n.get("llm_testing"))
        self.test_button.disabled = True
        if self.on_loading_change:
            self.on_loading_change(True)
        self._safe_update()

        try:
            provider = self._current_provider

            kwargs = {}
            if self._is_azure:
                is_valid, resource_name, deployment_name, api_version = self._validate_azure_fields()
                if not is_valid:
                    return

                model = deployment_name
                if api_version:
                    kwargs["api_version"] = api_version
                kwargs["azure_resource_name"] = resource_name
                base_url = ""
            else:
                base_url = self.base_url_input.value or ""

            result = await self.on_test_connection(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

            if result.get("success"):
                self._show_success(I18n.get("llm_test_success"))
            else:
                self._show_error(I18n.get(result.get("message", "common_err_unknown")))

        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="llm")
            self._show_error(get_error_message(error_info))
            logger.error("[LLMConfigPanel] Test connection error: %s", DataSanitizer.sanitize_error(ex))

        finally:
            self._is_verifying = False
            self.test_button.disabled = False
            if self.on_loading_change:
                self.on_loading_change(False)
            self._safe_update()

    async def async_verify_connection(self) -> bool:
        provider = self._current_provider

        if self._is_azure:
            is_valid, resource_name, deployment_name, api_version = self._validate_azure_fields()
            if not is_valid:
                return False

            model = deployment_name
            kwargs: dict[str, object] = {"azure_resource_name": resource_name}
            if api_version:
                kwargs["api_version"] = api_version
            base_url = ""
        else:
            model = self.model_dropdown.value or self.custom_model_input.value or ""
            base_url = self._normalize_base_url(self.base_url_input.value or "")
            kwargs = {}

        api_key = self.api_key_input.value

        if not api_key:
            self._show_warning(I18n.get("llm_test_need_key"))
            return False

        if not provider or not model:
            self._show_error(I18n.get("wizard_err_provider_model_required"))
            return False

        if not self._acquire_verify_lock():
            return False

        self._set_loading_state(True)
        self._show_info(I18n.get("llm_testing"))

        try:
            result = await self.on_test_connection(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

            if result.get("success"):
                self._show_success(I18n.get("llm_test_success"))
                return True

            self._show_error(I18n.get(result.get("message", "common_err_unknown")))
            return False

        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="llm")
            self._show_error(get_error_message(error_info))
            logger.error("[LLMConfigPanel] Verify connection error: %s", DataSanitizer.sanitize_error(ex))
            return False

        finally:
            self._is_verifying = False
            self._set_loading_state(False)
            self._safe_update()

    def _set_loading_state(self, loading: bool):
        self.test_button.disabled = loading
        self.save_button.disabled = loading

        if self.on_loading_change:
            self.on_loading_change(loading)

    def _on_refresh_click(self, e):  # pragma: no cover
        if not self.page:
            return

        self.page.run_task(self._refresh_models)

    async def _refresh_models(self):  # pragma: no cover
        api_key = self.api_key_input.value
        raw_base_url = self.base_url_input.value
        base_url = self._normalize_base_url(raw_base_url or "")

        if not api_key:
            self._show_warning(I18n.get("llm_refresh_need_key"))
            return

        if not base_url:
            self._show_warning(I18n.get("llm_refresh_need_url"))
            return

        self._show_info(I18n.get("llm_refreshing"))
        self.refresh_models_button.disabled = True
        if self.on_loading_change:
            self.on_loading_change(True)
        self.update()

        try:
            import httpx
            from utils.proxy_manager import ProxyManager

            models_url = f"{base_url.rstrip('/')}/models"

            proxy_cfg = ProxyManager.get_httpx_proxy_config()
            async with httpx.AsyncClient(**proxy_cfg) as client:
                response = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=self._REFRESH_TIMEOUT,
                )
                response.raise_for_status()
                data = response.json()

            models = data.get("data", [])
            model_ids = sorted([m["id"] for m in models if m.get("id")])

            if not model_ids:
                self._show_warning(I18n.get("llm_refresh_empty"))
                return

            self.model_dropdown.options = [ft.dropdown.Option(m) for m in model_ids]

            if self.model_dropdown.value not in model_ids:
                self.model_dropdown.value = model_ids[0]

            self.model_dropdown.update()

            self._show_success(I18n.get("llm_refresh_success", count=len(model_ids)))

        except Exception as ex:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(ex, context="llm")
            self._show_error(get_error_message(error_info))
            logger.error("[LLMConfigPanel] Refresh models error: %s", DataSanitizer.sanitize_error(ex))

        finally:
            self.refresh_models_button.disabled = False
            if self.on_loading_change:
                self.on_loading_change(False)
            self.update()

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """
        Normalize base URL by stripping known API endpoint suffixes while preserving base path.

        Only removes trailing API endpoint paths (e.g., /chat/completions, /v1/chat/completions)
        that users might paste, but keeps essential base paths like /compatible-mode/v1, /api/paas/v4.

        Examples:
            https://api.deepseek.com/v1/chat/completions -> https://api.deepseek.com/v1
            https://api.openai.com/v1 -> https://api.openai.com/v1
            https://api.example.com/ -> https://api.example.com
            https://dashscope.aliyuncs.com/compatible-mode/v1 -> https://dashscope.aliyuncs.com/compatible-mode/v1
        """
        if not url:
            return ""

        url = url.strip().rstrip("/")

        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        # Strip known API endpoint suffixes that users might paste
        url = re.sub(r"/chat/completions$", "", url)
        url = re.sub(r"/completions$", "", url)
        url = re.sub(r"/embeddings$", "", url)

        return url

    def _get_current_base_url(self) -> str:
        if self._is_azure:
            return ""
        return self.base_url_input.value or ""

    def _on_save_click(self, e):  # pragma: no cover
        if not self.page:
            return

        self.page.run_task(self._save_config)

    def _build_custom_models_update(
        self, provider: str, model: str, is_azure: bool = False
    ) -> dict[str, list[str]] | None:
        if not model or is_azure:
            return None
        if provider != "custom" and model in [m.get("id") for m in LLM_PROVIDERS.get(provider, {}).get("models", [])]:
            return None
        llm_config = ConfigHandler.get_llm_config()
        custom_models = llm_config.get("custom_models", {})
        if provider not in custom_models:
            custom_models[provider] = []
        if model not in custom_models[provider]:
            custom_models[provider].append(model)
            custom_models[provider] = custom_models[provider][-self._MAX_CUSTOM_MODELS :]
        return custom_models

    @staticmethod
    def _remove_primary_from_failover(provider: str) -> None:
        failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
        primary_prefix = f"{provider}/"
        new_failover_models = [m for m in failover_models if not m.startswith(primary_prefix)]
        if len(new_failover_models) != len(failover_models):
            ConfigHandler.save_config({"llm_failover_models": new_failover_models})
            logger.info(
                "[LLMConfigPanel] Automatically removed primary provider %s models from failover list", provider
            )

    async def _save_config(self):
        provider = self._current_provider
        # Strip whitespace from api_key; if modified, use stripped value, else None
        # Note: (api_key_raw or "").strip() ensures empty input clears the stored key,
        # whereas the original api_key_raw.strip() would return None for empty input (keeping old key).
        api_key_raw = self.api_key_input.value
        api_key = (api_key_raw or "").strip() if self._api_key_modified else None

        kwargs = {}

        if self._is_azure:
            is_valid, resource_name, deployment_name, api_version = self._validate_azure_fields()
            if not is_valid:
                return

            model = deployment_name
            base_url = ""

            if api_version:
                kwargs["api_version"] = api_version
            kwargs["azure_resource_name"] = resource_name
            kwargs["azure_deployment_name"] = deployment_name
        else:
            model = self.model_dropdown.value or self.custom_model_input.value or ""
            base_url = self._normalize_base_url(self.base_url_input.value or "")

            custom_models_update = self._build_custom_models_update(provider or "", model, is_azure=False)
            if custom_models_update is not None:
                kwargs["custom_models"] = custom_models_update

        try:
            ConfigHandler.save_llm_config(
                provider=provider,
                model=model or "",
                base_url=base_url,
                api_key=api_key,
                **kwargs,
            )

            self._api_key_modified = False

            self._remove_primary_from_failover(provider)

            custom_models = kwargs.get("custom_models", ConfigHandler.get_llm_config().get("custom_models", {}))
            self._sync_provider_credential_to_failover(provider, api_key, base_url, custom_models.get(provider))

            if self.on_reload_service:
                await self.on_reload_service()

            self._show_success(I18n.get("settings_verify_success"))

            if self.on_save:
                self.on_save()

        except Exception as ex:
            from utils.error_classifier import classify_error

            classify_error(ex, context="llm")
            self._show_error(I18n.get("settings_save_failed"))
            logger.error("[LLMConfigPanel] Save config error: %s", DataSanitizer.sanitize_error(ex))

        self.update()

    @property
    def api_key_modified(self) -> bool:
        """Check if API key has been modified by user."""
        return self._api_key_modified

    def get_current_config(self) -> dict:
        """
        Get current configuration values from the panel.

        Returns:
            dict with provider, model, base_url, api_key, and Azure fields
        """
        provider = self._current_provider
        base_url = self._normalize_base_url(self.base_url_input.value or "")
        api_key = (self.api_key_input.value or "").strip()

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

        custom_models_update = self._build_custom_models_update(
            config["provider"], config["model"], is_azure=self._is_azure
        )
        if custom_models_update is not None:
            kwargs["custom_models"] = custom_models_update

        # 未修改 API Key 时传 None，避免不必要的重加密
        api_key_to_save = config["api_key"] if self._api_key_modified else None

        try:
            ConfigHandler.save_llm_config(
                provider=config["provider"],
                model=config["model"],
                base_url=config["base_url"],
                api_key=api_key_to_save,
                **kwargs,
            )
            self._api_key_modified = False
            self._remove_primary_from_failover(config["provider"])
            custom_models = kwargs.get("custom_models", ConfigHandler.get_llm_config().get("custom_models", {}))
            self._sync_provider_credential_to_failover(
                config["provider"],
                api_key_to_save,
                config["base_url"],
                custom_models.get(config["provider"]),
            )
            self._schedule_ai_service_reload()
            return True
        except Exception as e:
            logger.error("[LLMConfigPanel] Save current config error: %s", DataSanitizer.sanitize_error(e))
            return False

    def _schedule_ai_service_reload(self):
        """在保存配置后通过回调异步刷新服务运行时状态。

        save_current_config 是同步方法，无法 await 回调，
        因此通过 page.run_task 调度异步执行。
        """
        if not self.on_reload_service:
            return
        if not self.page:
            return
        try:
            self.page.run_task(self.on_reload_service)
        except Exception as e:
            logger.debug("[LLMConfigPanel] AIService reload scheduling skipped: %s", DataSanitizer.sanitize_error(e))

    @staticmethod
    def _sync_provider_credential_to_failover(
        provider: str,
        api_key: str | None,
        base_url: str,
        models: list[str] | None = None,
    ) -> None:
        """
        如果当前 provider 在 failover_models 中，自动同步凭证到 llm_provider_credentials。

        这样用户配置主供应商时，failover 凭证自动同步，无需额外配置。

        注意: api_key 为 None 表示用户未修改密钥，此时应读取现有凭证中的 key，
        避免用空字符串覆盖已存储的有效密钥。
        """
        try:
            failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
            for model in failover_models:
                if model.startswith(f"{provider}/"):
                    # api_key 为 None 表示未修改，读取现有凭证避免覆盖
                    effective_key = api_key
                    if effective_key is None:
                        existing_cred = ConfigHandler.get_provider_credential(provider)
                        effective_key = existing_cred.get("api_key", "")

                    ConfigHandler.save_provider_credential(
                        provider=provider,
                        api_key=effective_key,
                        base_url=base_url,
                        models=models,
                    )
                    logger.debug("[LLMConfigPanel] Synced credential to failover provider: %s", provider)
                    break
        except Exception as e:
            logger.debug("[LLMConfigPanel] Failed to sync failover credential: %s", DataSanitizer.sanitize_error(e))

    def _show_success(self, message: str):  # pragma: no cover
        self.status_text.value = message
        self.status_text.color = AppColors.SUCCESS
        self.status_icon.icon = ft.Icons.CHECK_CIRCLE  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.SUCCESS
        self.status_icon.visible = True
        self._safe_update()

    def _show_error(self, message: str):  # pragma: no cover
        self.status_text.value = message
        self.status_text.color = AppColors.ERROR
        self.status_icon.icon = ft.Icons.ERROR  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.ERROR
        self.status_icon.visible = True
        self._safe_update()

    def _show_warning(self, message: str):  # pragma: no cover
        self.status_text.value = message
        self.status_text.color = AppColors.WARNING
        self.status_icon.icon = ft.Icons.WARNING  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.WARNING
        self.status_icon.visible = True
        self._safe_update()

    def _show_info(self, message: str):  # pragma: no cover
        self.status_text.value = message
        self.status_text.color = AppColors.PRIMARY
        self.status_icon.icon = ft.Icons.INFO  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.PRIMARY
        self.status_icon.visible = True
        self._safe_update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug("[LLMConfigPanel] Safe update skipped: %s", DataSanitizer.sanitize_error(e))

    def did_mount(self):  # pragma: no cover
        I18n.subscribe(self._on_locale_change)

    def will_unmount(self):  # pragma: no cover
        I18n.unsubscribe(self._on_locale_change)

    def _on_locale_change(self, new_locale: str | None = None):  # pragma: no cover
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

        self.model_dropdown.options = self._build_model_options(self._current_provider)

        self._update_links_row()

        self.section_header.update_locale()

        self.update()
