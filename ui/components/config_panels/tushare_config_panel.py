"""
Tushare Token Configuration Panel Component

Provides a unified UI for Tushare Token configuration with:
- Token input with password reveal
- Token verification
- Register link
- i18n support with hot reload
"""

import asyncio
import logging
import webbrowser
from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

_TUSHARE_REGISTER_URL = "https://tushare.pro/register?reg=728426"


class TushareConfigPanel(ft.Container):
    """
    Tushare Token Configuration Panel.

    Features:
    - Token input with password reveal
    - Token verification
    - Register link (optional)
    - i18n support with hot reload

    Args:
        on_verify_success: Callback when verification succeeds (optional)
        on_save: Callback when save button is clicked (optional)
        on_change: Callback when input changes (optional)
        on_loading_change: Callback for loading state change (optional)
        show_save_button: Whether to show the save button (default: True)
        compact: Whether to use compact layout for wizard (default: False)
        show_register_link: Whether to show register link (default: True)
        show_internal_loading: Whether to show internal loading state (default: True)
    """

    def __init__(
        self,
        on_verify_success: Callable[[str], None] | None = None,
        on_save: Callable[[dict], None] | None = None,
        on_change: Callable[[], None] | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_save_button: bool = True,
        compact: bool = False,
        show_register_link: bool = True,
        show_internal_loading: bool = True,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.on_verify_success = on_verify_success
        self.on_save = on_save
        self.on_change = on_change
        self.on_loading_change = on_loading_change
        self._show_save_button = show_save_button
        self._compact = compact
        self._show_register_link = show_register_link
        self._show_internal_loading = show_internal_loading
        self._is_verifying = False
        self._locale_subscription_id = None

        self._init_controls()
        self.content = self._build_ui()

    def _init_controls(self):
        saved_token = ConfigHandler.get_token() or ""

        self.token_input = ft.TextField(
            label=I18n.get("tushare_token_label"),
            password=True,
            can_reveal_password=True,
            value=saved_token,
            on_change=self._on_input_change,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
        )

        if self._compact:
            self.token_input.width = AppStyles.CONTROL_WIDTH_LG
            self.token_input.hint_text = I18n.get("tushare_token_hint")

        self.verify_button = ft.ElevatedButton(
            text=I18n.get("tushare_verify"),
            icon=ft.Icons.VERIFIED_USER_OUTLINED,
            on_click=self._on_verify_click,
            style=AppStyles.secondary_button(),
        )

        self.save_button = ft.ElevatedButton(
            text=I18n.get("tushare_save"),
            icon=ft.Icons.SAVE_OUTLINED,
            on_click=self._on_save_click,
            style=AppStyles.secondary_button(),
            visible=self._show_save_button,
        )

        self.status_icon = ft.Icon(
            ft.Icons.CIRCLE,
            color=AppColors.TEXT_HINT,
            size=12,
            visible=False,
        )

        self.status_text = ft.Text(
            "",
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )

        self.register_link = ft.TextButton(
            text=I18n.get("tushare_register"),
            icon=ft.Icons.OPEN_IN_NEW,
            on_click=self._on_register_click,
            style=ft.ButtonStyle(
                color=AppColors.PRIMARY,
            ),
        )

    def reload_config(self):
        saved_token = ConfigHandler.get_token() or ""
        self.token_input.value = saved_token
        self._safe_update()

    def _build_ui(self) -> ft.Control:
        if self._compact:
            return self._build_compact_ui()
        return self._build_standard_ui()

    def _build_compact_ui(self) -> ft.Column:
        controls = [
            self.token_input,
            ft.Container(height=10),
            ft.Row(
                [self.verify_button],
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            ft.Container(height=5),
            ft.Row(
                [self.status_icon, self.status_text],
                alignment=ft.MainAxisAlignment.CENTER,
                spacing=5,
            ),
        ]

        if self._show_register_link:
            controls.extend(
                [
                    ft.Container(height=15),
                    ft.Row(
                        [
                            ft.Text(
                                I18n.get("tushare_no_token"),
                                size=12,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            self.register_link,
                        ],
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=5,
                    ),
                ]
            )

        return ft.Column(
            controls,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_standard_ui(self) -> ft.Row:
        buttons = [self.verify_button]
        if self._show_save_button:
            buttons.append(self.save_button)

        return ft.Row(
            [
                ft.Column(
                    [
                        ft.Row(
                            [self.token_input] + buttons,
                            alignment=ft.MainAxisAlignment.START,
                            spacing=10,
                        ),
                        ft.Row(
                            [self.status_icon, self.status_text],
                            spacing=5,
                        ),
                    ],
                    spacing=5,
                    expand=True,
                ),
            ],
            alignment=ft.MainAxisAlignment.START,
        )

    def _on_input_change(self, e):
        if self.on_change:
            self.on_change()

    def _on_verify_click(self, e):
        if self._is_verifying:
            self._show_warning(I18n.get("tushare_verifying_in_progress"))
            return

        if self.page:
            self.page.run_task(self.verify_token)

    def _on_save_click(self, e):
        config = self.get_current_config()
        if self.on_save:
            self.on_save(config)

    def _on_register_click(self, e):
        webbrowser.open_new_tab(_TUSHARE_REGISTER_URL)

    def _set_loading_state(self, loading: bool):
        if self.on_loading_change:
            self.on_loading_change(loading)

        if self._show_internal_loading:
            self.verify_button.disabled = loading
            self.token_input.disabled = loading
            self.save_button.disabled = loading

            if loading:
                self.status_text.value = I18n.get("tushare_verifying")
                self.status_text.color = AppColors.WARNING
                self.status_icon.icon = ft.Icons.HOURGLASS_TOP  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
                self.status_icon.color = AppColors.WARNING
                self.status_icon.visible = True

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

    def _show_warning(self, message: str):
        self.status_text.value = message
        self.status_text.color = AppColors.WARNING
        self.status_icon.icon = ft.Icons.WARNING  # type: ignore[reportAttributeAccessIssue]  # Flet Icon.icon is writable at runtime
        self.status_icon.color = AppColors.WARNING
        self.status_icon.visible = True
        self._safe_update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug(f"[TushareConfig] UI update skipped: {exc}")

    async def verify_token(self) -> bool:
        token = (self.token_input.value or "").strip()

        if not token:
            self._show_error(I18n.get("tushare_token_required"))
            return False

        if self._is_verifying:
            logger.warning("[TushareConfigPanel] Verification already in progress")
            return False

        self._is_verifying = True
        self._set_loading_state(True)

        try:
            import tushare as ts

            ts.set_token(token)
            # 显式传 token，避免依赖 tushare SDK 全局状态（~/tk.csv 或环境变量）
            temp_pro = ts.pro_api(token=token, timeout=ConfigHandler.get_tushare_timeout())
            await asyncio.to_thread(
                temp_pro.trade_cal,
                exchange="SSE",
                start_date="20250101",
                end_date="20250101",
            )

            from data.external.tushare_client import TushareClient
            from strategies.all_strategies import StrategyManager

            ConfigHandler.save_token(token)

            client = TushareClient()
            needs_probe = client.set_token(token)

            if needs_probe:
                try:
                    logger.info("[TushareConfigPanel] Probing API capabilities...")
                    probe_results = await client.probe_api_capabilities()

                    StrategyManager().invalidate_dependency_cache()

                    available_apis = [api for api, status in probe_results.items() if status is True]
                    unavailable_apis = [api for api, status in probe_results.items() if status is False]

                    if unavailable_apis:
                        warning_msg = f"{I18n.get('tushare_verify_success')} — {I18n.get('tushare_restricted_apis')}: {', '.join(unavailable_apis)}"
                        self._show_warning(warning_msg)
                        logger.warning(f"[TushareConfigPanel] Restricted APIs: {unavailable_apis}")
                    elif available_apis:
                        self._show_success(I18n.get("tushare_verify_success"))
                        logger.info(f"[TushareConfigPanel] All probed APIs available: {len(available_apis)}")
                    else:
                        self._show_warning(
                            f"{I18n.get('tushare_verify_success')} — {I18n.get('tushare_probe_unknown')}"
                        )
                except Exception as probe_exc:
                    logger.warning(
                        f"[TushareConfigPanel] Capability probe failed (non-critical): {probe_exc}",
                        exc_info=True,
                    )
                    self._show_success(f"{I18n.get('tushare_verify_success')} — {I18n.get('tushare_probe_unknown')}")
            else:
                self._show_success(I18n.get("tushare_verify_success"))

            if self.on_verify_success:
                self.on_verify_success(token)

            return True

        except Exception as e:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(e, context="token")
            self._show_error(get_error_message(error_info))
            logger.error(
                "[TushareConfigPanel] Token verification failed: %s",
                DataSanitizer.sanitize_error(e),
                exc_info=True,
            )
            return False

        finally:
            self._is_verifying = False
            self._set_loading_state(False)

    def get_current_config(self) -> dict:
        return {
            "token": (self.token_input.value or "").strip(),
        }

    def set_config(self, config: dict):
        if "token" in config:
            self.token_input.value = config["token"]
            self._safe_update()

    def load_config(self):
        saved_token = ConfigHandler.get_token() or ""
        self.token_input.value = saved_token
        self._safe_update()

    def refresh_locale(self):
        self.token_input.label = I18n.get("tushare_token_label")
        if self._compact:
            self.token_input.hint_text = I18n.get("tushare_token_hint")
        self.verify_button.text = I18n.get("tushare_verify")
        self.save_button.text = I18n.get("tushare_save")
        self.register_link.text = I18n.get("tushare_register")
        self._safe_update()

    def did_mount(self):
        self._locale_subscription_id = I18n.subscribe(self.refresh_locale)

    def will_unmount(self):
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
