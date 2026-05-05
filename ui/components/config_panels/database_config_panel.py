"""
Database Configuration Panel Component

Provides a unified UI for database configuration with:
- Connection parameters (host, port, user, password, database)
- Connection testing
- Database creation option
- i18n support with hot reload
"""

import logging
from collections.abc import Callable

import flet as ft

from data.persistence.db_config_service import (
    ConnectionStatus,
    DatabaseConfigService,
)
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)


class DatabaseConfigPanel(ft.Container):
    """
    Database Configuration Panel.

    Features:
    - Connection parameters input
    - Connection testing
    - Database creation option
    - i18n support with hot reload

    Args:
        on_save_callback: Called after successful save (optional)
        on_test_success_callback: Called after successful connection test (optional)
        on_change: Callback when any input changes (optional)
        show_header: Whether to show section headers (default: True)
        compact: Whether to use compact layout (default: False)
        show_save_button: Whether to show the save button (default: True)
    """

    def __init__(
        self,
        on_save_callback: Callable | None = None,
        on_test_success_callback: Callable | None = None,
        on_change: Callable | None = None,
        on_loading_change: Callable[[bool], None] | None = None,
        show_header: bool = True,
        compact: bool = False,
        show_save_button: bool = True,
        load_password: bool = False,
        **kwargs,
    ):
        super().__init__(**kwargs)

        self.on_save_callback = on_save_callback
        self.on_test_success_callback = on_test_success_callback
        self.on_change = on_change
        self.on_loading_change = on_loading_change
        self.show_header = show_header
        self.compact = compact
        self._show_save_button = show_save_button
        self._load_password = load_password
        self._locale_subscription_id = None
        self._is_verifying = False

        self._init_controls()
        self._load_config()

        self.content = self._build_ui()

    def _init_controls(self):
        input_width = 280
        port_width = 90
        db_name_width = 380

        user_pass_width = 185

        self.db_host_input = ft.TextField(
            label=I18n.get("db_host"),
            width=input_width,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            hint_text="localhost",
            on_change=self._on_input_change,
        )
        self.db_port_input = ft.TextField(
            label=I18n.get("db_port"),
            width=port_width,
            keyboard_type=ft.KeyboardType.NUMBER,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            hint_text="5432",
            on_change=self._on_input_change,
        )
        self.db_user_input = ft.TextField(
            label=I18n.get("db_user"),
            width=user_pass_width,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            hint_text="postgres",
            on_change=self._on_input_change,
        )
        self.db_password_input = ft.TextField(
            label=I18n.get("db_password"),
            password=True,
            can_reveal_password=True,
            width=user_pass_width,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            on_change=self._on_input_change,
        )
        self.db_name_input = ft.TextField(
            label=I18n.get("db_name"),
            width=db_name_width,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            hint_text="astock",
            on_change=self._on_input_change,
        )

        self.db_create_checkbox = ft.Checkbox(
            label=I18n.get("db_create_if_not_exists"),
            value=True,
            fill_color=AppColors.PRIMARY,
            on_change=self._on_input_change,
        )

        self.status_icon = ft.Icon(visible=False, size=16)
        self.status_text = ft.Text(
            "",
            size=12,
            color=AppColors.TEXT_SECONDARY,
        )

        self.db_info_text = ft.Text(
            "",
            size=11,
            color=AppColors.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )

        self.btn_test = ft.ElevatedButton(
            I18n.get("db_test_connection"),
            icon=ft.Icons.POWER,
            on_click=self._on_test_click,
            style=AppStyles.secondary_button(),
        )

        self.btn_save = ft.ElevatedButton(
            I18n.get("common_save"),
            icon=ft.Icons.SAVE,
            on_click=self._on_save_click,
            style=AppStyles.primary_button(),
            visible=self._show_save_button,
        )

    def _load_config(self):
        db_config = ConfigHandler.get_db_config()

        self.db_host_input.value = db_config.get("host", "localhost")
        self.db_port_input.value = str(db_config.get("port", 5432))
        self.db_user_input.value = db_config.get("user", "postgres")
        if self._load_password:
            password = ConfigHandler.get_db_password()
            self.db_password_input.value = password or ""
        else:
            self.db_password_input.value = ""
        self.db_name_input.value = db_config.get("database", "astock")

    def reload_config(self):
        self._load_config()
        self._safe_update()

    def _build_ui(self):
        children = []

        if self.show_header:
            children.append(
                ft.Text(
                    I18n.get("db_connection_settings"),
                    size=16,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                )
            )
            children.append(ft.Container(height=15))

        form_content = ft.Column(
            [
                ft.Row(
                    [self.db_host_input, self.db_port_input],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                ),
                ft.Container(height=12),
                ft.Row(
                    [self.db_user_input, self.db_password_input],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=10,
                ),
                ft.Container(height=12),
                ft.Row(
                    [self.db_name_input],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Container(height=16),
                ft.Row(
                    [self.db_create_checkbox],
                    alignment=ft.MainAxisAlignment.CENTER,
                ),
                ft.Container(height=20),
                ft.Row(
                    [self.btn_test, self.btn_save],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=15,
                ),
                ft.Container(height=12),
                ft.Row(
                    [self.status_icon, self.status_text],
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=5,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

        children.append(form_content)

        if self.show_header:
            children.extend(
                [
                    ft.Container(height=25),
                    ft.Text(
                        I18n.get("db_info"),
                        size=14,
                        weight=ft.FontWeight.W_500,
                        color=AppColors.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Container(height=10),
                    ft.Row(
                        [self.db_info_text],
                        alignment=ft.MainAxisAlignment.CENTER,
                    ),
                ]
            )

        return ft.Column(
            children,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _on_input_change(self, e):
        if self.on_change:
            self.on_change()

    def _on_test_click(self, e):
        if self._is_verifying:
            self._show_warning(I18n.get("db_testing_in_progress"))
            return

        if self.page:
            self.page.run_task(self.test_connection)

    def _on_save_click(self, e):
        if self.page:
            self.page.run_task(self.save_config)

    def get_config(self) -> dict:
        return {
            "host": (self.db_host_input.value or "").strip(),
            "port": int((self.db_port_input.value or "").strip() or 5432),
            "user": (self.db_user_input.value or "").strip(),
            "password": self.db_password_input.value,
            "database": (self.db_name_input.value or "").strip(),
            "create_if_not_exists": self.db_create_checkbox.value,
        }

    def set_config(self, config: dict):
        self.db_host_input.value = config.get("host", "localhost")
        self.db_port_input.value = str(config.get("port", 5432))
        self.db_user_input.value = config.get("user", "postgres")
        self.db_password_input.value = config.get("password", "")
        self.db_name_input.value = config.get("database", "astock")
        self.db_create_checkbox.value = config.get("create_if_not_exists", False)
        self._safe_update()

    def validate(self) -> tuple[bool, str]:
        host = self.db_host_input.value.strip()
        if not host:
            return False, I18n.get("wizard_err_host_required", default="Host is required")

        try:
            port = int(self.db_port_input.value.strip() or 5432)
            if not (1 <= port <= 65535):
                return False, I18n.get(
                    "wizard_err_port_range",
                    default="Port must be between 1 and 65535",
                )
        except ValueError:
            return False, I18n.get("wizard_err_port_number", default="Port must be a number")

        user = self.db_user_input.value.strip()
        if not user:
            return False, I18n.get("wizard_err_user_required", default="Username is required")

        database = self.db_name_input.value.strip()
        if not database:
            return False, I18n.get("wizard_err_db_required", default="Database name is required")

        return True, ""

    async def test_connection(self) -> bool:
        is_valid, error = self.validate()
        if not is_valid:
            self._show_error(error)
            return False

        if self._is_verifying:
            self._show_warning(I18n.get("db_testing_in_progress"))
            return False

        self._is_verifying = True
        self._show_warning(I18n.get("db_testing"))
        self.btn_test.disabled = True
        if self.on_loading_change:
            self.on_loading_change(True)

        try:
            config = self.get_config()

            result = await DatabaseConfigService.test_connection(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if result.status == ConnectionStatus.SUCCESS:
                self._show_success(result.message)

                info = await DatabaseConfigService.get_database_info(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )
                if info:
                    self.db_info_text.value = f"Version: {info.version}\nSize: {info.size}\nTables: {info.table_count}"

                if self.on_test_success_callback:
                    self.on_test_success_callback(config)

                return True

            elif result.status == ConnectionStatus.DATABASE_NOT_FOUND:
                if self.db_create_checkbox.value:
                    self._show_warning(
                        I18n.get(
                            "db_will_create",
                            default="Database not found. Will create on save.",
                        )
                    )
                    return True
                else:
                    self._show_error(result.message)
                    return False
            else:
                self._show_error(result.message)
                return False

        except ValueError as e:
            from utils.error_classifier import classify_error

            error_info = classify_error(e, context="db")
            self._show_error(error_info["message"])
            return False
        except Exception as e:
            from utils.error_classifier import classify_error

            error_info = classify_error(e, context="db")
            self._show_error(error_info["message"])
            return False
        finally:
            self._is_verifying = False
            self.btn_test.disabled = False
            if self.on_loading_change:
                self.on_loading_change(False)
            self._safe_update()

    async def save_config(self) -> bool:
        is_valid, error = self.validate()
        if not is_valid:
            self._show_error(error)
            return False

        self._show_warning(I18n.get("db_saving"))
        self.btn_save.disabled = True

        try:
            config = self.get_config()

            result = await DatabaseConfigService.test_connection(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if result.status == ConnectionStatus.DATABASE_NOT_FOUND and config["create_if_not_exists"]:
                success, msg = await DatabaseConfigService.create_database(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )
                if not success:
                    self._show_error(msg)
                    return False
            elif result.status != ConnectionStatus.SUCCESS:
                self._show_error(result.message)
                return False

            self._show_warning(I18n.get("db_creating_tables"))

            success, msg = await DatabaseConfigService.ensure_tables_exist(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            if not success:
                self._show_error(msg)
                return False

            ConfigHandler.save_db_config(
                host=config["host"],
                port=config["port"],
                user=config["user"],
                password=config["password"],
                database=config["database"],
            )

            self._show_success(I18n.get("db_msg_saved"))

            if self.on_save_callback:
                self.on_save_callback(config)

            return True

        except Exception as e:
            from utils.error_classifier import classify_error

            error_info = classify_error(e, context="db")
            self._show_error(error_info["message"])
            return False
        finally:
            self.btn_save.disabled = False
            self._safe_update()

    def _show_success(self, message: str):
        self.status_text.value = message
        self.status_text.color = AppColors.SUCCESS
        self.status_icon.icon = ft.Icons.CHECK_CIRCLE
        self.status_icon.color = AppColors.SUCCESS
        self.status_icon.visible = True
        self._safe_update()

    def _show_error(self, message: str):
        self.status_text.value = message
        self.status_text.color = AppColors.ERROR
        self.status_icon.icon = ft.Icons.ERROR
        self.status_icon.color = AppColors.ERROR
        self.status_icon.visible = True
        self._safe_update()

    def _show_warning(self, message: str):
        self.status_text.value = message
        self.status_text.color = AppColors.WARNING
        self.status_icon.icon = ft.Icons.WARNING
        self.status_icon.color = AppColors.WARNING
        self.status_icon.visible = True
        self._safe_update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception as e:
            logger.debug(f"Safe update skipped: {e}")

    def did_mount(self):
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[DatabaseConfigPanel] Subscribed to locale changes")

    def will_unmount(self):
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[DatabaseConfigPanel] Unsubscribed from locale changes")

    def _on_locale_change(self, new_locale: str = None):
        try:
            saved_values = {
                "host": self.db_host_input.value,
                "port": self.db_port_input.value,
                "user": self.db_user_input.value,
                "password": self.db_password_input.value,
                "database": self.db_name_input.value,
                "create_if_not_exists": self.db_create_checkbox.value,
            }

            self._init_controls()
            self._load_config()

            self.db_host_input.value = saved_values["host"]
            self.db_port_input.value = saved_values["port"]
            self.db_user_input.value = saved_values["user"]
            self.db_password_input.value = saved_values["password"]
            self.db_name_input.value = saved_values["database"]
            self.db_create_checkbox.value = saved_values["create_if_not_exists"]

            self.content = self._build_ui()
            self._safe_update()
        except Exception as e:
            logger.warning(f"[DatabaseConfigPanel] Failed to update locale: {e}")
