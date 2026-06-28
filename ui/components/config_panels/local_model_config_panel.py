"""
Local Model Configuration Panel Component

Provides a unified UI for configuring local GGUF models with:
- Model file path selection
- Inference timeout
- Advanced settings (threads, GPU layers, batch size, context window, flash attention)
- Model verification
- i18n support with hot reload
"""

import logging
import os
from collections.abc import Awaitable, Callable

import flet as ft

from ui.components.settings_widgets import SectionHeader
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

_INPUT_WIDTH_SMALL = 190


class LocalModelConfigPanel(ft.Container):
    """
    Local Model Configuration Panel.

    Features:
    - Model file path with file picker
    - Inference timeout
    - Advanced settings (expandable)
    - Model verification
    - i18n support with hot reload

    Args:
        on_verify_model: Callback to verify a local model (required)
        on_verify_success: Callback when verification succeeds (optional)
        on_save: Callback when configuration is saved (optional)
        on_change: Callback when any input changes (optional)
        on_loading_change: Callback when loading state changes (optional)
        show_save_button: Whether to show the save button (default: False)
        compact: Whether to use compact layout for wizard (default: False)
        show_internal_loading: Whether to show internal loading indicator (default: True)
    """

    def __init__(  # pragma: no cover
        self,
        on_verify_model: Callable[[str, dict], Awaitable[bool]],
        on_verify_success: Callable | None = None,
        on_save: Callable | None = None,
        on_change: Callable | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_save_button: bool = False,
        compact: bool = False,
        show_internal_loading: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.on_verify_model = on_verify_model
        self.on_verify_success = on_verify_success
        self.on_save = on_save
        self.on_change = on_change
        self.on_loading_change = on_loading_change
        self._show_save_button = show_save_button
        self._compact = compact
        self._show_internal_loading = show_internal_loading
        self._locale_subscription_id = None
        self._is_verifying = False

        self._build_ui()

    def _build_ui(self):
        local_cfg = ConfigHandler.get_local_ai_config()

        self.model_path_input = ft.TextField(
            label=I18n.get("settings_local_model_path"),
            value=local_cfg.get("local_model_path", ""),
            expand=True,
            hint_text="C:/path/to/model.gguf",
            read_only=False,
            on_change=self._on_input_change,
        )

        self.btn_select_file = ft.OutlinedButton(
            text=I18n.get("settings_btn_select_file"),
            icon=ft.Icons.FOLDER_OPEN,
            on_click=self._on_select_file_click,
        )

        timeout_val = ConfigHandler.get_local_ai_timeout()
        self.timeout_input = ft.TextField(
            label=I18n.get("settings_local_ai_timeout"),
            value=str(timeout_val) if timeout_val is not None else "",
            width=_INPUT_WIDTH_SMALL,
            keyboard_type=ft.KeyboardType.NUMBER,
            hint_text="300",
            on_change=self._on_input_change,
        )

        self.threads_input = ft.Slider(
            min=1,
            max=16,
            divisions=15,
            value=local_cfg.get("n_threads", 4),
            label="{value}",
            tooltip=str(local_cfg.get("n_threads", 4)),
            on_change=self._on_input_change,
        )

        current_gpu_layers = local_cfg.get("n_gpu_layers", -1)
        is_gpu_auto = current_gpu_layers == -1

        self.gpu_auto_switch = ft.Switch(
            label=I18n.get("settings_local_gpu_auto"),
            value=is_gpu_auto,
            on_change=self._on_gpu_auto_change,
        )

        self.gpu_layers_input = ft.Slider(
            min=0,
            max=100,
            divisions=100,
            value=current_gpu_layers if not is_gpu_auto else 0,
            label="{value}",
            tooltip=str(current_gpu_layers if not is_gpu_auto else 0),
            visible=not is_gpu_auto,
            on_change=self._on_input_change,
        )

        self.batch_input = ft.Dropdown(
            label=I18n.get("settings_local_batch"),
            value=str(local_cfg.get("n_batch", 512)),
            options=[ft.dropdown.Option(str(x)) for x in [512, 1024, 2048, 4096]],
            width=_INPUT_WIDTH_SMALL,
            on_change=self._on_input_change,
        )

        self.ctx_input = ft.Dropdown(
            label=I18n.get("settings_local_ctx"),
            value=str(local_cfg.get("n_ctx", 4096)),
            options=[ft.dropdown.Option(str(x)) for x in [2048, 4096, 8192, 16384, 32768]],
            width=_INPUT_WIDTH_SMALL,
            on_change=self._on_input_change,
        )

        self.flash_attn_switch = ft.Switch(
            label=I18n.get("settings_local_flash_attn"),
            value=local_cfg.get("flash_attn", True),
            on_change=self._on_input_change,
        )

        self.status_icon = ft.Icon(visible=False, size=16)
        self.status_text = ft.Text(
            value="",
            size=12,
        )

        self.progress_indicator = ft.ProgressRing(
            visible=False,
            width=20,
            height=20,
            stroke_width=2,
        )

        self.verify_button = ft.ElevatedButton(
            text=I18n.get("wizard_btn_verify_model"),
            on_click=self._on_verify_click,
            icon=ft.Icons.CHECK_CIRCLE,
            style=AppStyles.secondary_button(),
        )

        self.save_button = ft.ElevatedButton(
            text=I18n.get("settings_save_config"),
            on_click=self._on_save_click,
            icon=ft.Icons.SAVE,
            visible=self._show_save_button,
            style=AppStyles.primary_button(),
        )

        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)

        self.advanced_tile = ft.ExpansionTile(
            title=ft.Text(
                I18n.get("ai_advanced_settings"),
                size=14 if not self._compact else 12,
                weight=ft.FontWeight.BOLD,
            ),
            subtitle=ft.Text(
                I18n.get("settings_hint_restart"),
                size=11,
                color=AppColors.WARNING,
            ),
            controls=[
                ft.Container(height=10),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("settings_local_threads"),
                                    size=12,
                                ),
                                self.threads_input,
                            ],
                            col={"sm": 12, "md": 6},
                        ),
                        ft.Column(
                            [
                                ft.Text(
                                    I18n.get("settings_local_gpu_layers"),
                                    size=12,
                                ),
                                self.gpu_auto_switch,
                                self.gpu_layers_input,
                            ],
                            col={"sm": 12, "md": 6},
                        ),
                        ft.Column(
                            [self.batch_input],
                            col={"sm": 6, "md": 4},
                        ),
                        ft.Column(
                            [self.ctx_input],
                            col={"sm": 6, "md": 4},
                        ),
                        ft.Column(
                            [self.timeout_input],
                            col={"sm": 12, "md": 4},
                        ),
                        ft.Column(
                            [self.flash_attn_switch],
                            col={"sm": 12, "md": 12},
                        ),
                    ],
                    run_spacing=15,
                ),
            ],
            initially_expanded=False,
        )

        header_text = SectionHeader(I18n.get("settings_sec_local_ai"))
        header_text.visible = not self._compact

        desc_text = ft.Text(
            value=I18n.get("settings_local_ai_desc"),
            size=12,
            color=AppColors.TEXT_SECONDARY,
            visible=not self._compact,
        )

        compact_main_align = ft.MainAxisAlignment.CENTER if self._compact else ft.MainAxisAlignment.START
        compact_cross_align = ft.CrossAxisAlignment.CENTER if self._compact else ft.CrossAxisAlignment.START

        action_buttons = ft.Row(
            controls=[
                self.verify_button,
                self.save_button,
                self.progress_indicator,
            ],
            alignment=compact_main_align,
        )

        form_content = ft.Column(
            controls=[
                header_text,
                desc_text,
                ft.Container(height=10) if not self._compact else ft.Container(height=5),
                ft.Row(
                    [
                        self.model_path_input,
                        self.btn_select_file,
                    ],
                    spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.END,
                    alignment=compact_main_align,
                ),
                ft.Container(height=10) if not self._compact else ft.Container(height=5),
                self.advanced_tile,
                ft.Container(height=10) if not self._compact else ft.Container(height=5),
                action_buttons,
                ft.Row(
                    [self.status_icon, self.status_text],
                    alignment=compact_main_align,
                    spacing=5,
                ),
            ],
            spacing=10 if not self._compact else 6,
            horizontal_alignment=compact_cross_align,
        )

        if self._compact:
            self.content = ft.Container(
                content=form_content,
                width=550,
                alignment=ft.alignment.center,
            )
        else:
            self.content = form_content

    def _on_input_change(self, e):
        if e is not None:
            try:
                if isinstance(e.control, ft.Slider):
                    val = e.control.value
                    e.control.tooltip = str(int(val) if val == int(val) else round(val, 2))
                    e.control.update()
            except AttributeError:
                pass

        if self.on_change:
            self.on_change()

    def _on_gpu_auto_change(self, e):
        self.gpu_layers_input.visible = not self.gpu_auto_switch.value
        self._safe_update()
        if self.on_change:
            self.on_change()

    def reload_config(self):  # pragma: no cover
        local_cfg = ConfigHandler.get_local_ai_config()
        self.model_path_input.value = local_cfg.get("local_model_path", "")
        timeout_val = ConfigHandler.get_local_ai_timeout()
        self.timeout_input.value = str(timeout_val) if timeout_val is not None else ""
        self.threads_input.value = local_cfg.get("n_threads", 4)
        current_gpu_layers = local_cfg.get("n_gpu_layers", -1)
        is_gpu_auto = current_gpu_layers == -1
        self.gpu_auto_switch.value = is_gpu_auto
        self.gpu_layers_input.value = current_gpu_layers if not is_gpu_auto else 0
        self.gpu_layers_input.visible = not is_gpu_auto
        self.batch_input.value = str(local_cfg.get("n_batch", 512))
        self.ctx_input.value = str(local_cfg.get("n_ctx", 4096))
        self.flash_attn_switch.value = local_cfg.get("flash_attn", True)
        self._safe_update()

    def _on_select_file_click(self, e):
        if self.page:
            self.file_picker.pick_files(
                allowed_extensions=["gguf"],
                dialog_title=I18n.get("settings_btn_select_file"),
            )

    def _on_file_picked(self, e: ft.FilePickerResultEvent):
        if e.files and len(e.files) > 0:
            self.model_path_input.value = e.files[0].path
            self._safe_update()
            if self.on_change:
                self.on_change()

    def _on_verify_click(self, e):
        if self._is_verifying:
            self._show_warning(I18n.get("wizard_model_verifying"))
            return

        if self.page:
            self.page.run_task(self._async_verify_and_notify)

    async def _async_verify_and_notify(
        self,
    ):  # pragma: no cover — UI notification wrapper; async_verify_model() tested separately
        result = await self.async_verify_model()
        if result and self.on_verify_success:
            self.on_verify_success()

    def _on_save_click(self, e):
        result = self.save_config()
        if result:
            self._show_success(I18n.get("wizard_model_configured"))
            if self.on_save:
                self.on_save()

    def verify_model(self) -> bool:  # pragma: no cover — stub method; async_verify_model() is the real implementation
        return False

    async def async_verify_model(self) -> bool:
        model_path = (self.model_path_input.value or "").strip()

        if not model_path:
            self._show_error(I18n.get("wizard_err_model_required"))
            return False

        if not os.path.exists(model_path):
            self._show_error(I18n.get("wizard_err_model_not_found"))
            return False

        if not model_path.lower().endswith(".gguf"):
            self._show_error(I18n.get("wizard_err_model_format"))
            return False

        timeout_str = (self.timeout_input.value or "").strip()
        try:
            timeout = int(timeout_str) if timeout_str else 300
            if not (0 < timeout <= 3600):
                raise ValueError("Range")
        except ValueError:
            self._show_error(
                I18n.get("ai_snack_invalid_range").format(
                    field=I18n.get("settings_local_ai_timeout"),
                    min=1,
                    max=3600,
                )
            )
            return False

        if self._is_verifying:
            logger.warning("[LocalModelConfigPanel] Verification already in progress")
            return False

        self._is_verifying = True
        self._show_warning(I18n.get("wizard_model_loading"))
        self._set_loading_state(True)

        try:
            import asyncio

            # Ensure Flet renders the loading mask even if the
            # model is cached and returns instantly
            await asyncio.sleep(0.5)

            config = self.get_current_config()
            success = await self.on_verify_model(model_path, config)

            if not success:
                self._show_error(I18n.get("wizard_err_model_load_failed"))
                return False

            self._show_success(I18n.get("wizard_model_configured"))
            return True

        except Exception as e:
            logger.error("[LocalModelConfigPanel] Model verification failed: %s", e, exc_info=True)
            self._show_error(I18n.get("wizard_err_model_load_failed"))
            return False

        finally:
            self._is_verifying = False
            self._set_loading_state(False)
            self._safe_update()

    def _set_loading_state(self, loading: bool):
        if self._show_internal_loading:
            self.progress_indicator.visible = loading
            self.verify_button.disabled = loading
            self.save_button.disabled = loading
            self.btn_select_file.disabled = loading
            self.model_path_input.disabled = loading

        if self.on_loading_change:
            self.on_loading_change(loading)

    def save_config(self) -> bool:
        model_path = (self.model_path_input.value or "").strip()
        timeout_str = (self.timeout_input.value or "").strip()
        timeout = int(timeout_str) if timeout_str else 300

        # 限制 timeout 范围
        timeout = max(1, min(timeout, 3600))

        gpu_layers = -1 if self.gpu_auto_switch.value else int(self.gpu_layers_input.value or 0)

        try:
            success = ConfigHandler.save_local_ai_config(
                model_path=model_path,
                timeout=timeout,
                n_threads=int(self.threads_input.value or 4),
                n_batch=int(self.batch_input.value or 512),
                n_ctx=int(self.ctx_input.value or 2048),
                flash_attn=self.flash_attn_switch.value,
                n_gpu_layers=gpu_layers,
            )
        except (ValueError, TypeError) as e:
            logger.error("[LocalModelConfigPanel] Invalid config values: %s", e, exc_info=True)
            return False

        if not success:
            return False

        # 提交验证模式（如果活跃）—— 仅 Onboarding 流程走此路径
        from services.local_model_manager import LocalModelManager

        LocalModelManager.commit_verification_if_active()

        return True

    def get_current_config(self) -> dict:
        gpu_layers = -1 if self.gpu_auto_switch.value else int(self.gpu_layers_input.value or 0)

        return {
            "model_path": (self.model_path_input.value or "").strip(),
            "timeout": int(self.timeout_input.value or 0) if (self.timeout_input.value or "").strip() else 300,
            "n_threads": int(self.threads_input.value or 4),
            "n_gpu_layers": gpu_layers,
            "n_batch": int(self.batch_input.value or 512),
            "n_ctx": int(self.ctx_input.value or 2048),
            "flash_attn": self.flash_attn_switch.value,
        }

    def set_config(self, config: dict):  # pragma: no cover
        self.model_path_input.value = config.get("model_path", "")
        self.timeout_input.value = str(config.get("timeout", 300))
        self.threads_input.value = config.get("n_threads", 4)

        gpu_layers = config.get("n_gpu_layers", 0)
        self.gpu_auto_switch.value = gpu_layers == -1
        self.gpu_layers_input.value = gpu_layers if gpu_layers != -1 else 0
        self.gpu_layers_input.tooltip = str(self.gpu_layers_input.value)
        self.gpu_layers_input.visible = not self.gpu_auto_switch.value

        self.batch_input.value = str(config.get("n_batch", 512))
        self.ctx_input.value = str(config.get("n_ctx", 4096))
        self.flash_attn_switch.value = config.get("flash_attn", True)

        self._safe_update()

    def _show_success(self, message: str):
        self.status_text.value = message
        self.status_text.color = AppColors.SUCCESS
        self.status_icon.icon = ft.Icons.CHECK_CIRCLE  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.SUCCESS
        self.status_icon.visible = True
        self._safe_update()

    def _show_error(self, message: str):
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

    def _safe_update(self):  # pragma: no cover
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug("Safe update skipped: %s", e, exc_info=True)

    def did_mount(self):
        if self.page:
            self.page.overlay.append(self.file_picker)
            self.page.update()

        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[LocalModelConfigPanel] Subscribed to locale changes")

    def will_unmount(self):
        if self.page and getattr(self, "file_picker", None) in self.page.overlay:
            self.page.overlay.remove(self.file_picker)
            self.page.update()

        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[LocalModelConfigPanel] Unsubscribed from locale changes")

        # 清理未提交的验证状态
        from services.local_model_manager import LocalModelManager

        LocalModelManager.cancel_verification_if_active()

    def _on_locale_change(self, new_locale: str | None = None):  # pragma: no cover
        try:
            saved_values = {
                "model_path": self.model_path_input.value,
                "timeout": self.timeout_input.value,
                "threads": self.threads_input.value,
                "gpu_auto": self.gpu_auto_switch.value,
                "gpu_layers": self.gpu_layers_input.value,
                "batch": self.batch_input.value,
                "ctx": self.ctx_input.value,
                "flash_attn": self.flash_attn_switch.value,
                "advanced_expanded": getattr(self, "advanced_tile", None)
                and getattr(self.advanced_tile, "expanded", False),
                "status_visible": getattr(self, "status_icon", None) and self.status_icon.visible,
                "status_text": getattr(self, "status_text", None) and self.status_text.value or "",
                "status_color": getattr(self, "status_text", None) and self.status_text.color,
                "status_icon_name": getattr(self, "status_icon", None) and getattr(self.status_icon, "icon", None),
            }

            # 保存旧的 file_picker 引用，用于从 overlay 中移除
            old_file_picker = getattr(self, "file_picker", None)

            self._build_ui()

            self.model_path_input.value = saved_values["model_path"]
            self.timeout_input.value = saved_values["timeout"]
            self.threads_input.value = saved_values["threads"]
            self.threads_input.tooltip = str(saved_values["threads"])
            self.gpu_auto_switch.value = saved_values["gpu_auto"]
            self.gpu_layers_input.value = saved_values["gpu_layers"]
            self.gpu_layers_input.tooltip = str(saved_values["gpu_layers"])
            self.gpu_layers_input.visible = not saved_values["gpu_auto"]
            self.batch_input.value = saved_values["batch"]
            self.ctx_input.value = saved_values["ctx"]
            self.flash_attn_switch.value = saved_values["flash_attn"]

            # 恢复高级设置展开状态
            if saved_values["advanced_expanded"] and hasattr(self, "advanced_tile"):
                self.advanced_tile.expanded = True  # type: ignore[reportAttributeAccessIssue]  # Flet ExpansionTile.expanded is writable at runtime

            # 恢复状态提示
            if saved_values["status_visible"]:
                self.status_icon.visible = True
                self.status_text.value = saved_values["status_text"]
                self.status_text.color = saved_values["status_color"]
                self.status_icon.icon = saved_values["status_icon_name"]  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
                self.status_icon.color = saved_values["status_color"]

            # 更新 page.overlay 中的 file_picker：移除旧的，添加新的
            if self.page:
                if old_file_picker and old_file_picker in self.page.overlay:
                    self.page.overlay.remove(old_file_picker)
                self.page.overlay.append(self.file_picker)
                self.page.update()

            self._safe_update()
        except Exception as e:
            logger.warning("[LocalModelConfigPanel] Failed to update locale: %s", e, exc_info=True)
