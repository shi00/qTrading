"""Failover Configuration Panel Component

Provides UI for configuring LLM failover providers:
- Display failover list with credential status indicators
- Add/edit/delete failover providers
- Reorder priority with up/down arrows
- Batch credential validation
"""

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import flet as ft

from ui.components.settings_widgets import SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from utils.llm_providers import LLM_PROVIDERS, get_display_tag
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

logger = logging.getLogger(__name__)


@dataclass
class FailoverItem:
    provider: str
    model: str
    display_name: str
    has_credential: bool
    api_key_masked: str = ""

    def to_config_string(self) -> str:
        return f"{self.provider}/{self.model}"


class ProviderCredentialDialog(ft.AlertDialog):
    """Dialog for adding/editing a failover provider's credentials."""

    def __init__(
        self,
        page: ft.Page | None = None,
        on_confirm: Callable | None = None,
        on_test_connection: Callable[..., Awaitable[dict]] | None = None,
        edit_item: FailoverItem | None = None,
        existing_providers: list[str] | None = None,
        edit_credential: dict | None = None,
    ):
        self._on_confirm = on_confirm
        self._test_connection_callback = on_test_connection
        self._edit_item = edit_item
        self._existing_providers = existing_providers or []
        self._provider = edit_item.provider if edit_item else ""
        self._is_edit = edit_item is not None
        self._page_ref = page
        self._locale_subscription_id: object | None = None
        # 注入的 credential，避免 __init__ 中同步 keyring IO（R16）
        self._edit_credential = edit_credential

        super().__init__()

        self.page = page
        self._build_ui()
        if self._is_edit:
            self._populate_edit_data()

    def _build_ui(self):
        provider_options = []
        for pid, pinfo in LLM_PROVIDERS.items():
            if pid == "custom":
                continue
            if not self._is_edit and pid in self._existing_providers:
                continue
            provider_options.append(ft.dropdown.Option(key=pid, text=pinfo.get("name", pid)))

        self.provider_dropdown = ft.Dropdown(
            label=I18n.get("failover_select_provider"),
            options=provider_options,
            width=400,
            on_change=self._on_provider_change,
            disabled=self._is_edit,
        )

        self.model_dropdown = ft.Dropdown(
            label=I18n.get("failover_select_model"),
            options=[],
            width=400,
            on_change=self._on_model_dropdown_change,
        )

        self.custom_model_input = ft.TextField(
            label=I18n.get("llm_custom_model"),
            width=400,
            hint_text=I18n.get("failover_custom_model_hint"),
        )

        self.base_url_input = ft.TextField(
            label=I18n.get("failover_base_url_optional"),
            width=400,
            hint_text=I18n.get("failover_base_url_hint"),
        )

        self.api_key_input = ft.TextField(
            label=I18n.get("llm_api_key"),
            width=400,
            password=True,
            can_reveal_password=True,
        )

        self.links_row = ft.Row([], spacing=10, wrap=True)

        self.modal = True
        self.title = ft.Text(I18n.get("failover_dialog_title"))
        self.content = ft.Column(
            [
                self.provider_dropdown,
                self.model_dropdown,
                self.custom_model_input,
                self.base_url_input,
                self.api_key_input,
                self.links_row,
            ],
            tight=True,
            spacing=12,
            width=440,
        )
        self._cancel_btn = ft.TextButton(
            text=I18n.get("btn_cancel"),
            on_click=self._on_cancel,
        )
        self._test_btn = None
        if self._test_connection_callback:
            self._test_btn = ft.TextButton(
                text=I18n.get("failover_test_connection"),
                on_click=self._on_test_connection,
            )
        self._confirm_btn = ft.ElevatedButton(
            text=I18n.get("common_confirm"),
            on_click=self._on_confirm_click,
            style=AppStyles.primary_button(),
        )

        self.actions = [self._cancel_btn]
        if self._test_btn is not None:
            self.actions.append(self._test_btn)
        self.actions.append(self._confirm_btn)

    def _populate_edit_data(self):
        if self._edit_item is None:
            return
        self.provider_dropdown.value = self._edit_item.provider
        self._on_provider_change_internal(self._edit_item.provider)

        # 优先使用注入的 credential，避免 __init__ 中同步 keyring IO（R16）
        if self._edit_credential is not None:
            cred = self._edit_credential
        else:
            cred = ConfigHandler.get_provider_credential(self._edit_item.provider)
        self.model_dropdown.value = self._edit_item.model
        self.base_url_input.value = cred.get("base_url", "")
        if cred.get("api_key"):
            key = cred["api_key"]
            self.api_key_input.value = key
            self.api_key_masked = DataSanitizer.sanitize_token(key)

    def did_mount(self):
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):
        if self._locale_subscription_id is not None:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def refresh_locale(self):
        """Refresh i18n text on locale change (pure UI, preserves user input)."""
        try:
            self.title.value = I18n.get("failover_dialog_title")
            self.provider_dropdown.label = I18n.get("failover_select_provider")
            self.model_dropdown.label = I18n.get("failover_select_model")
            self.custom_model_input.label = I18n.get("llm_custom_model")
            self.custom_model_input.hint_text = I18n.get("failover_custom_model_hint")
            self.base_url_input.label = I18n.get("failover_base_url_optional")
            pinfo = LLM_PROVIDERS.get(self._provider, {})
            default_url = pinfo.get("base_url", "")
            self.base_url_input.hint_text = default_url or I18n.get("failover_base_url_hint")
            self.api_key_input.label = I18n.get("llm_api_key")

            self._cancel_btn.text = I18n.get("btn_cancel")
            if self._test_btn is not None:
                self._test_btn.text = I18n.get("failover_test_connection")
            self._confirm_btn.text = I18n.get("common_confirm")

            # 重建链接行（其文案依赖 i18n）
            self._update_links_row(self._provider)

            if self.page:
                self.update()
        except Exception as e:
            logger.warning("[ProviderCredentialDialog] refresh_locale failed: %s", e, exc_info=True)

    def _on_provider_change(self, e):
        provider = e.control.value
        self._on_provider_change_internal(provider)

    def _on_provider_change_internal(self, provider: str):
        self._provider = provider
        self.model_dropdown.options = []
        self.model_dropdown.value = None

        pinfo = LLM_PROVIDERS.get(provider, {})
        models = pinfo.get("models", [])
        for m in models:
            tag = m.get("tag", "")
            label = f"{m['id']}"
            display_tag = get_display_tag(tag)
            if display_tag:
                label += f" ({display_tag})"
            self.model_dropdown.options.append(ft.dropdown.Option(key=m["id"], text=label))

        default_url = pinfo.get("base_url", "")
        self.base_url_input.value = default_url
        self.base_url_input.hint_text = default_url or I18n.get("failover_base_url_hint")

        self._update_links_row(provider)
        if self.model_dropdown.page:
            self.model_dropdown.update()
        if self.base_url_input.page:
            self.base_url_input.update()
        if self.links_row.page and self.links_row.controls:
            self.links_row.update()

    def _on_model_dropdown_change(self, e):
        if e.control.value:
            self.custom_model_input.value = ""

    def _update_links_row(self, provider: str):
        self.links_row.controls = []
        pinfo = LLM_PROVIDERS.get(provider, {})
        console_url = pinfo.get("console_url")
        pricing_url = pinfo.get("pricing_url")
        models_url = pinfo.get("models_url")

        if console_url:
            self.links_row.controls.append(
                ft.TextButton(
                    text=I18n.get("llm_get_api_key"),
                    on_click=lambda _: self._open_url(console_url),
                )
            )
        if pricing_url:
            self.links_row.controls.append(
                ft.TextButton(
                    text=I18n.get("llm_view_pricing"),
                    on_click=lambda _: self._open_url(pricing_url),
                )
            )
        if models_url:
            self.links_row.controls.append(
                ft.TextButton(
                    text=I18n.get("llm_view_models"),
                    on_click=lambda _: self._open_url(models_url),
                )
            )

    def _open_url(self, url: str):
        if self.page:
            self.page.launch_url(url)

    @staticmethod
    def _normalize_base_url(url: str) -> str:
        """规范化 base_url，去除用户可能粘贴的 API 端点后缀。"""
        if not url:
            return ""
        url = url.strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        url = re.sub(r"/chat/completions$", "", url)
        url = re.sub(r"/completions$", "", url)
        url = re.sub(r"/embeddings$", "", url)
        return url

    def _on_cancel(self, e):
        self.open = False
        if self.page:
            self.page.close(self)

    async def _on_test_connection(self, e):
        provider = self._provider
        model = self.custom_model_input.value or self.model_dropdown.value
        base_url = self.base_url_input.value or ""
        api_key = self.api_key_input.value

        if not provider or not model or not api_key:
            return

        if not self._test_connection_callback:
            return

        try:
            result = await self._test_connection_callback(
                provider=provider,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )
            if result.get("success"):
                self._show_snack(I18n.get("failover_test_success"), AppColors.SUCCESS)
            else:
                self._show_snack(
                    I18n.get("failover_test_failed") + f": {result.get('error', '')}",
                    AppColors.ERROR,
                )
        except Exception as ex:
            logger.error("[ProviderCredentialDialog] test connection failed: %s", ex, exc_info=True)
            self._show_snack(
                I18n.get("failover_test_failed") + f": {DataSanitizer.sanitize_error(ex)}",
                AppColors.ERROR,
            )

    def _show_snack(self, msg: str, color: str):
        if self.page:
            # 清理旧的 SnackBar 避免累积
            self.page.overlay[:] = [s for s in self.page.overlay if not isinstance(s, ft.SnackBar)]
            snack = ft.SnackBar(ft.Text(msg), bgcolor=color)
            self.page.overlay.append(snack)
            snack.open = True
            self.page.update()

    def _on_confirm_click(self, e):
        provider = self._provider
        model = self.custom_model_input.value or self.model_dropdown.value
        base_url = self.base_url_input.value or ""
        api_key = self.api_key_input.value

        if not provider or not model:
            return

        # 规范化 base_url（去除 /chat/completions 等后缀）
        base_url = self._normalize_base_url(base_url)

        # 新增模式下要求 API Key 非空
        if not self._is_edit and not api_key:
            self._show_snack(I18n.get("llm_test_need_key"), AppColors.WARNING)
            return

        if self.page:
            self.page.run_task(self._do_confirm_click_async, provider, model, base_url, api_key)

    async def _do_confirm_click_async(self, provider: str, model: str, base_url: str, api_key: str | None):
        try:
            # 合并 5 次 IO 到单个闭包，减少 run_async 调用次数
            def _save_sync():
                # 编辑模式下清空 API Key 时查询原有凭证（用于警告提示）
                existing_cred = None
                if self._is_edit and not api_key:
                    existing_cred = ConfigHandler.get_provider_credential(provider)

                # 主供应商检查（不允许添加与主供应商相同的 failover）
                primary_provider = ConfigHandler.load_config().get("llm_provider", "")
                if provider == primary_provider:
                    return existing_cred, primary_provider

                # 保存凭证
                ConfigHandler.save_provider_credential(
                    provider=provider,
                    api_key=api_key,
                    base_url=base_url,
                    models=[model],
                )

                # 加载并更新 failover 列表
                failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
                new_entry = f"{provider}/{model}"
                if self._is_edit and self._edit_item is not None:
                    old_entry = self._edit_item.to_config_string()
                    failover_models = [new_entry if m == old_entry else m for m in failover_models]
                else:
                    if new_entry not in failover_models:
                        failover_models.append(new_entry)

                ConfigHandler.save_config({"llm_failover_models": failover_models})
                return existing_cred, primary_provider

            existing_cred, primary_provider = await ThreadPoolManager().run_async(TaskType.IO, _save_sync)

            # UI 更新（事件循环线程，安全）
            # 编辑模式下清空 API Key 时提示警告（允许用户有意清除）
            if self._is_edit and not api_key and existing_cred and existing_cred.get("api_key"):
                self._show_snack(I18n.get("failover_clear_key_warning"), AppColors.WARNING)

            if provider == primary_provider:
                self._show_snack(I18n.get("failover_primary_in_list"), AppColors.WARNING)
                return

            self.open = False
            if self.page:
                self.page.close(self)

            if self._on_confirm:
                self._on_confirm()
        except Exception as ex:
            logger.error(
                "[ProviderCredentialDialog] confirm failed: %s",
                DataSanitizer.sanitize_error(ex),
                exc_info=True,
            )
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)


class FailoverConfigPanel(ft.Container):
    """Failover configuration panel with list management."""

    def __init__(
        self,
        on_test_connection: Callable[..., Awaitable[dict]],
        on_save: Callable | None = None,
    ):
        self.on_test_connection = on_test_connection
        self.on_save = on_save
        self._failover_items: list[FailoverItem] = []
        self._list_column = ft.Column([], spacing=8)
        super().__init__()
        self._build_ui()
        self._load_config()

    def _build_ui(self):
        self.header_row = ft.Row(
            [
                SectionHeader(I18n.get("failover_title")),
                ft.Icon(ft.Icons.BOLT, size=20, color=AppColors.PRIMARY),
                ft.Container(expand=True),
                ft.OutlinedButton(
                    text=I18n.get("failover_add_provider"),
                    icon=ft.Icons.ADD,
                    on_click=self._on_add_click,
                    style=AppStyles.secondary_button(),
                    height=36,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )

        self.hint_text = ft.Text(
            I18n.get("failover_hint"),
            size=11,
            color=AppColors.TEXT_HINT,
        )

        self.btn_validate = ft.OutlinedButton(
            text=I18n.get("failover_validate_all"),
            icon=ft.Icons.VERIFIED_USER,
            on_click=self._on_validate_all,
            style=AppStyles.secondary_button(),
            height=36,
        )

        self.btn_save = ft.ElevatedButton(
            text=I18n.get("settings_save_ai"),
            icon=ft.Icons.SAVE,
            on_click=self._on_save_click,
            style=AppStyles.primary_button(),
            height=36,
        )

        self.content = ft.Column(
            [
                self.header_row,
                self.hint_text,
                ft.Container(height=8),
                self._list_column,
                ft.Container(height=8),
                ft.Row(
                    [self.btn_validate, ft.Container(expand=True), self.btn_save],
                    alignment=ft.MainAxisAlignment.START,
                ),
            ],
            spacing=4,
        )

    def _load_config_sync(self) -> list[FailoverItem]:
        """同步加载配置（IO 部分），返回 FailoverItem 列表。"""
        config = ConfigHandler.load_config()
        failover_models = config.get("llm_failover_models", [])

        items: list[FailoverItem] = []
        for entry in failover_models:
            if "/" not in entry:
                continue
            provider, model = entry.split("/", 1)
            pinfo = LLM_PROVIDERS.get(provider, {})
            cred = ConfigHandler.get_provider_credential(provider)
            has_key = bool(cred.get("api_key"))
            key_masked = ""
            if has_key and cred["api_key"]:
                key_masked = DataSanitizer.sanitize_token(cred["api_key"])

            items.append(
                FailoverItem(
                    provider=provider,
                    model=model,
                    display_name=pinfo.get("name", provider),
                    has_credential=has_key,
                    api_key_masked=key_masked,
                )
            )
        return items

    def _load_config(self):
        """同步加载配置并渲染（仅用于 __init__，避免事件循环中调用）。"""
        self._failover_items = self._load_config_sync()
        self._render_list()

    async def _load_config_async(self):
        """异步加载配置并渲染（用于 async 方法，避免阻塞事件循环）。"""
        items = await ThreadPoolManager().run_async(TaskType.IO, self._load_config_sync)
        self._failover_items = items
        self._render_list()

    def _render_list(self):
        self._list_column.controls = []

        if not self._failover_items:
            self._list_column.controls.append(
                ft.Container(
                    content=ft.Text(
                        I18n.get("failover_empty_hint"),
                        size=12,
                        color=AppColors.TEXT_HINT,
                        italic=True,
                    ),
                    padding=20,
                    alignment=ft.alignment.center,
                )
            )
        else:
            for i, item in enumerate(self._failover_items):
                self._list_column.controls.append(self._build_list_item(i, item))

        self._safe_update()

    def _build_list_item(self, index: int, item: FailoverItem) -> ft.Container:
        status_icon = ft.Icon(
            ft.Icons.CHECK_CIRCLE,
            size=16,
            color=AppColors.SUCCESS if item.has_credential else AppColors.WARNING,
        )
        status_text = ft.Text(
            I18n.get("failover_credential_ok") if item.has_credential else I18n.get("failover_credential_missing"),
            size=11,
            color=AppColors.SUCCESS if item.has_credential else AppColors.WARNING,
        )

        btn_up = ft.IconButton(
            ft.Icons.ARROW_UPWARD,
            icon_size=16,
            on_click=lambda e, idx=index: self._on_move_up(idx),
            disabled=index == 0,
            tooltip=I18n.get("failover_move_up"),
        )
        btn_down = ft.IconButton(
            ft.Icons.ARROW_DOWNWARD,
            icon_size=16,
            on_click=lambda e, idx=index: self._on_move_down(idx),
            disabled=index == len(self._failover_items) - 1,
            tooltip=I18n.get("failover_move_down"),
        )
        btn_edit = ft.IconButton(
            ft.Icons.EDIT,
            icon_size=16,
            on_click=lambda e, idx=index: self._on_edit_item(idx),
            tooltip=I18n.get("failover_edit"),
        )
        btn_delete = ft.IconButton(
            ft.Icons.DELETE_OUTLINE,
            icon_size=16,
            on_click=lambda e, idx=index: self._on_delete_item(idx),
            tooltip=I18n.get("failover_delete"),
        )

        left_section = ft.Row(
            [
                ft.Text(f"{index + 1}.", size=13, weight=ft.FontWeight.BOLD, width=24),
                status_icon,
                ft.Text(
                    f"{item.display_name} / {item.model}",
                    size=13,
                    weight=ft.FontWeight.W_500,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        right_section = ft.Row(
            [btn_up, btn_down, btn_edit, btn_delete],
            spacing=0,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [left_section, ft.Container(expand=True), right_section],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Row([status_text], spacing=4),
                ],
                spacing=2,
            ),
            padding=ft.padding.symmetric(horizontal=12, vertical=8),
            border=ft.border.all(1, ft.Colors.OUTLINE),
            border_radius=6,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
        )

    def _on_add_click(self, e):
        if self.page:
            self.page.run_task(self._do_add_click_async)

    async def _do_add_click_async(self):
        try:
            existing = [item.provider for item in self._failover_items]
            primary_provider = await ThreadPoolManager().run_async(
                TaskType.IO, lambda: ConfigHandler.load_config().get("llm_provider", "")
            )
            if primary_provider and primary_provider not in existing:
                existing.append(primary_provider)
            dialog = ProviderCredentialDialog(
                page=self.page,
                on_confirm=self._on_dialog_confirmed,
                on_test_connection=self.on_test_connection,
                existing_providers=existing,
            )
            if self.page:
                self.page.open(dialog)
        except Exception as ex:
            logger.error("[FailoverConfigPanel] add click failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _on_edit_item(self, index: int):
        if self.page:
            self.page.run_task(self._do_edit_item_async, index)

    async def _do_edit_item_async(self, index: int):
        try:
            item = self._failover_items[index]
            # 先 offload 获取 credential，避免 dialog __init__ 中同步 keyring IO（R16）
            cred = await ThreadPoolManager().run_async(
                TaskType.IO, ConfigHandler.get_provider_credential, item.provider
            )
            dialog = ProviderCredentialDialog(
                page=self.page,
                on_confirm=self._on_dialog_confirmed,
                on_test_connection=self.on_test_connection,
                edit_item=item,
                edit_credential=cred,
            )
            if self.page:
                self.page.open(dialog)
        except Exception as ex:
            logger.error("[FailoverConfigPanel] edit item failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _on_delete_item(self, index: int):
        if self.page:
            self.page.run_task(self._do_delete_item_async, index)

    async def _do_delete_item_async(self, index: int):
        try:
            item = self._failover_items[index]
            entry = item.to_config_string()

            def _delete_sync():
                failover_models = ConfigHandler.load_config().get("llm_failover_models", [])
                if entry in failover_models:
                    failover_models.remove(entry)
                    ConfigHandler.save_config({"llm_failover_models": failover_models})

            await ThreadPoolManager().run_async(TaskType.IO, _delete_sync)
            await self._load_config_async()
        except Exception as ex:
            logger.error("[FailoverConfigPanel] delete item failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _on_move_up(self, index: int):
        if index <= 0:
            return
        if self.page:
            self.page.run_task(self._do_move_item_async, index, -1)

    def _on_move_down(self, index: int):
        if index >= len(self._failover_items) - 1:
            return
        if self.page:
            self.page.run_task(self._do_move_item_async, index, 1)

    async def _do_move_item_async(self, index: int, direction: int):
        target = index + direction
        # 先记录原始顺序，便于失败时回滚
        original_order = list(self._failover_items)
        try:
            self._failover_items[index], self._failover_items[target] = (
                self._failover_items[target],
                self._failover_items[index],
            )
            ordered = [item.to_config_string() for item in self._failover_items]
            await ThreadPoolManager().run_async(
                TaskType.IO, ConfigHandler.save_config, {"llm_failover_models": ordered}
            )
            self._render_list()
        except Exception as ex:
            # 回滚列表顺序，保持内存状态与配置一致
            self._failover_items = original_order
            self._render_list()
            logger.error("[FailoverConfigPanel] move item failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _persist_order(self):
        ordered = [item.to_config_string() for item in self._failover_items]
        ConfigHandler.save_config({"llm_failover_models": ordered})
        self._render_list()

    def _on_validate_all(self, e):
        if self.page:
            self.page.run_task(self._do_validate_all_async)

    async def _do_validate_all_async(self):
        try:
            missing = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.validate_failover_credentials)
            if missing:
                providers_str = ", ".join(missing)
                self._show_snack(
                    I18n.get("failover_validation_missing").format(providers=providers_str),
                    AppColors.WARNING,
                )
            else:
                self._show_snack(
                    I18n.get("failover_validation_complete"),
                    AppColors.SUCCESS,
                )
        except Exception as ex:
            logger.error("[FailoverConfigPanel] validate all failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _on_save_click(self, e):
        if self.on_save:
            self.on_save()
        self._show_snack(I18n.get("settings_verify_success"), AppColors.SUCCESS)

    def _on_dialog_confirmed(self):
        if self.page:
            self.page.run_task(self._do_dialog_confirmed_async)

    async def _do_dialog_confirmed_async(self):
        try:
            await self._load_config_async()
        except Exception as ex:
            logger.error("[FailoverConfigPanel] dialog confirmed reload failed: %s", ex, exc_info=True)
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _show_snack(self, msg: str, color: str):
        if self.page:
            # 清理旧的 SnackBar 避免累积
            self.page.overlay[:] = [s for s in self.page.overlay if not isinstance(s, ft.SnackBar)]
            snack = ft.SnackBar(ft.Text(msg), bgcolor=color)
            self.page.overlay.append(snack)
            snack.open = True
            self.page.update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug("[FailoverConfigPanel] Safe update skipped: %s", e, exc_info=True)

    def did_mount(self):
        I18n.subscribe(self._on_locale_change)

    def will_unmount(self):
        I18n.unsubscribe(self._on_locale_change)

    def _on_locale_change(self):
        try:
            self._build_ui()  # 仅重建 UI 控件文本，不重新加载配置
            # 注意：不调用 _load_config()，保留已有的 _failover_items 数据
            self._render_list()  # 用已有数据重新渲染列表
            self._safe_update()
        except Exception as e:
            logger.warning("[FailoverConfigPanel] _on_locale_change failed: %s", e, exc_info=True)

    def reload_config(self):
        self._load_config()
