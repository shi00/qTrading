"""Startup UI renderer — maps StartupState to Flet controls. Pure UI, no business logic."""

from __future__ import annotations

import logging

import flet as ft

# 架构例外（§4.1）：app 层应仅被 main.py 调用。此处的导入属于 main.py 启动流程
# 的延伸（main.py:18 装配 StartupViewRenderer），不是 ui 层的正常业务导入。
# 已在 tests/unit/test_architecture_boundaries.py 的 KNOWN_EXCEPTIONS 中记录。
from app.startup_controller import StartupContext, StartupController, StartupState
from core.i18n import I18n

logger = logging.getLogger(__name__)


def _get_localized_detail(detail: str) -> str:
    """Classify and return localized error message for database initialization details."""
    if not detail:
        return ""
    try:
        from utils.error_classifier import classify_error, get_error_message

        classified = classify_error(Exception(detail), context="db")
        if classified.get("message_key") != "db_err_unknown":
            return get_error_message(classified)
    except Exception as e:
        logger.warning("[StartupView] Failed to classify error detail '%s': %s", detail, e, exc_info=True)
    return detail


class StartupViewRenderer:
    """
    Renders startup UI based on StartupController state changes.

    Receives state transitions from the controller and constructs the
    corresponding Flet controls. Button callbacks delegate back to the
    controller's action methods.
    """

    def __init__(
        self,
        page: ft.Page,
        controller: StartupController,
        show_dialog_fn,
        hide_dialog_fn,
        run_task_fn,
    ):
        self._page = page
        self._controller = controller
        self._show_dialog = show_dialog_fn
        self._hide_dialog = hide_dialog_fn
        self._run_task = run_task_fn
        self._current_dialog = None

    def _show_dialog_tracked(self, dialog):
        """Show a dialog, hiding any previously shown dialog first."""
        if self._current_dialog is not None:
            self._hide_dialog(self._current_dialog)
        self._show_dialog(dialog)
        self._current_dialog = dialog

    def on_state_change(self, state: StartupState, context: StartupContext):
        """Called by controller on every state transition."""
        if state == StartupState.LOADING:
            self._render_loading()
        elif state == StartupState.NEED_UPGRADE:
            self._render_upgrade_dialog(context)
        elif state == StartupState.UPGRADE_IN_PROGRESS:
            self._render_upgrade_in_progress()
        elif state == StartupState.UPGRADE_SUCCESS:
            self._render_upgrade_success()
        elif state == StartupState.UPGRADE_FAILED:
            self._render_upgrade_failed(context)
        elif state == StartupState.INIT_FAILED:
            self._render_error_view(context)
        elif state == StartupState.NEED_ONBOARDING:
            self._render_onboarding()
        elif state == StartupState.READY:
            self._render_main_app()

    # --- Loading ---

    def _render_loading(self):
        self._page.clean()
        self._page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.ProgressRing(width=40, height=40, stroke_width=3),
                        ft.Text(I18n.get("wizard_status_init") or "Initializing...", size=16),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    alignment=ft.MainAxisAlignment.CENTER,
                    spacing=20,
                ),
                expand=True,
                alignment=ft.Alignment.CENTER,
            )
        )
        self._page.update()

    # --- Upgrade flow ---

    def _render_upgrade_dialog(self, context: StartupContext):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("db_upgrade_needed_title")),
            content=ft.Text(I18n.get("db_upgrade_needed_content")),
            actions=[
                ft.Button(
                    I18n.get("db_upgrade_btn"),
                    on_click=lambda e: self._run_task(self._controller.upgrade),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog_tracked(dialog)

    def _render_upgrade_in_progress(self):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("db_upgrade_in_progress_title")),
            content=ft.Column(
                [
                    ft.Text(I18n.get("db_upgrade_in_progress_content")),
                    ft.ProgressBar(width=300),
                ],
                spacing=10,
            ),
            actions=[],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog_tracked(dialog)

    def _render_upgrade_success(self):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("db_upgrade_success_title")),
            content=ft.Text(I18n.get("db_upgrade_success_content")),
            actions=[
                ft.TextButton(
                    I18n.get("common_ok"),
                    on_click=lambda e: [
                        self._hide_dialog(dialog),
                        self._run_task(self._controller.proceed_after_upgrade_success),
                    ],
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog_tracked(dialog)

    def _render_upgrade_failed(self, context: StartupContext):
        dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(I18n.get("db_upgrade_error_title")),
            content=ft.Text(I18n.get("db_upgrade_error_content")),
            actions=[
                ft.TextButton(
                    I18n.get("exit_program"),
                    on_click=lambda e: [
                        self._hide_dialog(dialog),
                        self._controller.upgrade_exit(),
                    ],
                ),
                ft.Button(
                    I18n.get("retry_upgrade"),
                    on_click=lambda e: [
                        self._hide_dialog(dialog),
                        self._run_task(self._controller.upgrade_retry),
                    ],
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        self._show_dialog_tracked(dialog)

    # --- Error view (db_init_failed / db_engine_missing / task_manager_init_failed) ---

    def _render_error_view(self, context: StartupContext):
        error = context.error or ""
        self._page.controls.clear()
        self._page.add(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED, size=48),
                        ft.Text(
                            I18n.get("error_db_init_failed")
                            if error != "db_engine_missing"
                            else I18n.get("error_db_engine_missing"),
                            size=20,
                            weight=ft.FontWeight.BOLD,
                        ),
                        ft.Text(
                            _get_localized_detail(context.detail or "")[:200],
                            color=ft.Colors.RED_400,
                            size=14,
                        ),
                        ft.Row(
                            [
                                ft.Button(
                                    I18n.get("retry"),
                                    icon=ft.Icons.REFRESH,
                                    on_click=lambda e: self._run_task(self._controller.retry),
                                ),
                                ft.TextButton(
                                    I18n.get("db_reconfigure"),
                                    icon=ft.Icons.SETTINGS,
                                    on_click=lambda e: self._run_task(self._controller.reconfigure),
                                ),
                                ft.TextButton(
                                    I18n.get("skip"),
                                    on_click=lambda e: self._controller.skip(),
                                ),
                            ],
                            alignment=ft.MainAxisAlignment.CENTER,
                            spacing=20,
                        ),
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    spacing=10,
                ),
                expand=True,
                alignment=ft.Alignment.CENTER,
            ),
        )

    # --- Onboarding ---

    def _render_onboarding(self):
        from ui.views.onboarding_wizard import OnboardingWizard

        self._page.controls.clear()
        wizard = OnboardingWizard(on_complete=self._controller.onboarding_complete)
        self._page.add(
            ft.Container(
                content=wizard,
                expand=True,
                padding=40,
            ),
        )
        self._page.update()

    # --- Main app (READY) ---

    def _render_main_app(self):
        from ui.app_layout import AppLayout
        from services.news_subscription_service import NewsSubscriptionService

        app_layout = AppLayout(self._page)

        def on_news_alert(msg):
            if hasattr(self._page, "toast") and self._page.toast:
                self._page.toast.show(f"📰 {msg}", toast_type="info")

        NewsSubscriptionService().add_listener(on_news_alert, is_alert=True)
        app_layout.show()
