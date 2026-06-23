import pytest
from unittest.mock import MagicMock, patch
import flet as ft

from app.startup_controller import StartupContext, StartupController, StartupState
from ui.startup_views import StartupViewRenderer, _get_localized_detail

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_controller():
    return MagicMock(spec=StartupController)


def _trigger_click(button):
    """Safely trigger Flet control on_click callback in tests."""
    assert button.on_click is not None
    # Pyright doesn't know button.on_click is callable or what event type it takes
    button.on_click(MagicMock())  # type: ignore[reportArgumentType, reportOptionalCall]


def _find_controls(control, control_type):
    """Recursively find all controls of a given type in a control tree."""
    found = []
    if isinstance(control, control_type):
        found.append(control)
    # Check standard container properties
    if hasattr(control, "controls") and isinstance(control.controls, list):
        for child in control.controls:
            found.extend(_find_controls(child, control_type))
    if hasattr(control, "content") and control.content:
        found.extend(_find_controls(control.content, control_type))
    if hasattr(control, "actions") and isinstance(control.actions, list):
        for child in control.actions:
            found.extend(_find_controls(child, control_type))
    return found


def _find_button_by_text(root, text: str):
    """Find a button with the given text recursively."""
    buttons = _find_controls(root, (ft.ElevatedButton, ft.TextButton))
    for btn in buttons:
        if getattr(btn, "text", "") == text:
            return btn
    return None


def test_get_localized_detail_empty():
    assert _get_localized_detail("") == ""
    assert _get_localized_detail(None) == ""  # type: ignore[reportArgumentType]


def test_get_localized_detail_classified():
    with (
        patch("utils.error_classifier.classify_error") as mock_classify,
        patch("utils.error_classifier.get_error_message") as mock_msg,
    ):
        mock_classify.return_value = {"message_key": "db_err_auth_failed"}
        mock_msg.return_value = "Localized Auth Failure"
        assert _get_localized_detail("auth failed") == "Localized Auth Failure"
        mock_classify.assert_called_once()
        mock_msg.assert_called_once()


def test_get_localized_detail_unknown():
    with (
        patch("utils.error_classifier.classify_error") as mock_classify,
        patch("utils.error_classifier.get_error_message") as mock_msg,
    ):
        mock_classify.return_value = {"message_key": "db_err_unknown"}
        assert _get_localized_detail("unknown detail") == "unknown detail"
        mock_msg.assert_not_called()


def test_get_localized_detail_exception():
    with patch("utils.error_classifier.classify_error", side_effect=RuntimeError("error")):
        assert _get_localized_detail("some error detail") == "some error detail"


def test_renderer_initialization(mock_page, mock_controller):
    show_dialog = MagicMock()
    hide_dialog = MagicMock()
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, hide_dialog, run_task)
    assert renderer._page == mock_page
    assert renderer._controller == mock_controller
    assert renderer._show_dialog == show_dialog
    assert renderer._hide_dialog == hide_dialog
    assert renderer._run_task == run_task
    assert renderer._current_dialog is None


def test_show_dialog_tracked(mock_page, mock_controller):
    show_dialog = MagicMock()
    hide_dialog = MagicMock()
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, hide_dialog, run_task)

    dialog1 = ft.AlertDialog(title=ft.Text("D1"))
    renderer._show_dialog_tracked(dialog1)
    show_dialog.assert_called_once_with(dialog1)
    assert renderer._current_dialog == dialog1

    dialog2 = ft.AlertDialog(title=ft.Text("D2"))
    renderer._show_dialog_tracked(dialog2)
    hide_dialog.assert_called_once_with(dialog1)
    show_dialog.assert_called_with(dialog2)
    assert renderer._current_dialog == dialog2


def test_on_state_change_routing(mock_page, mock_controller):
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), MagicMock())
    context = StartupContext()

    states = [
        (StartupState.LOADING, "_render_loading"),
        (StartupState.NEED_UPGRADE, "_render_upgrade_dialog"),
        (StartupState.UPGRADE_IN_PROGRESS, "_render_upgrade_in_progress"),
        (StartupState.UPGRADE_SUCCESS, "_render_upgrade_success"),
        (StartupState.UPGRADE_FAILED, "_render_upgrade_failed"),
        (StartupState.INIT_FAILED, "_render_error_view"),
        (StartupState.NEED_ONBOARDING, "_render_onboarding"),
        (StartupState.READY, "_render_main_app"),
    ]

    for state, method_name in states:
        with patch.object(renderer, method_name) as mock_method:
            if method_name in ("_render_upgrade_dialog", "_render_upgrade_failed", "_render_error_view"):
                renderer.on_state_change(state, context)
                mock_method.assert_called_once_with(context)
            else:
                renderer.on_state_change(state, context)
                mock_method.assert_called_once()


def test_render_loading(mock_page, mock_controller, mock_i18n):
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), MagicMock())
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_loading()
    assert len(mock_page.controls) > 0
    assert isinstance(mock_page.controls[-1], ft.Container)


def test_render_upgrade_dialog(mock_page, mock_controller, mock_i18n):
    show_dialog = MagicMock()
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, MagicMock(), run_task)

    context = StartupContext()
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_upgrade_dialog(context)

    show_dialog.assert_called_once()
    dialog = show_dialog.call_args[0][0]
    assert isinstance(dialog, ft.AlertDialog)
    assert dialog.modal is True

    # Test button click
    button = dialog.actions[0]
    assert isinstance(button, ft.ElevatedButton)
    _trigger_click(button)
    run_task.assert_called_once_with(mock_controller.upgrade)


def test_render_upgrade_in_progress(mock_page, mock_controller, mock_i18n):
    show_dialog = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, MagicMock(), MagicMock())
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_upgrade_in_progress()
    show_dialog.assert_called_once()
    dialog = show_dialog.call_args[0][0]
    assert isinstance(dialog.content, ft.Column)
    assert any(isinstance(c, ft.ProgressBar) for c in dialog.content.controls)


def test_render_upgrade_success(mock_page, mock_controller, mock_i18n):
    show_dialog = MagicMock()
    hide_dialog = MagicMock()
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, hide_dialog, run_task)

    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_upgrade_success()

    show_dialog.assert_called_once()
    dialog = show_dialog.call_args[0][0]
    button = dialog.actions[0]
    _trigger_click(button)
    hide_dialog.assert_called_once_with(dialog)
    run_task.assert_called_once_with(mock_controller.proceed_after_upgrade_success)


def test_render_upgrade_failed(mock_page, mock_controller, mock_i18n):
    show_dialog = MagicMock()
    hide_dialog = MagicMock()
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, show_dialog, hide_dialog, run_task)

    context = StartupContext()
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_upgrade_failed(context)

    show_dialog.assert_called_once()
    dialog = show_dialog.call_args[0][0]
    # exit_program button
    btn_exit = dialog.actions[0]
    _trigger_click(btn_exit)
    hide_dialog.assert_called_with(dialog)
    mock_controller.upgrade_exit.assert_called_once()

    # retry button
    btn_retry = dialog.actions[1]
    _trigger_click(btn_retry)
    hide_dialog.assert_called_with(dialog)
    run_task.assert_called_once_with(mock_controller.upgrade_retry)


def test_render_error_view_db_init_failed(mock_page, mock_controller, mock_i18n):
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), run_task)

    context = StartupContext(error="db_init_failed", detail="connection error")
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_error_view(context)

    # Use robust traversal helpers to find controls instead of brittle index access
    btn_retry = _find_button_by_text(mock_page, "retry")
    btn_reconfig = _find_button_by_text(mock_page, "db_reconfigure")
    btn_skip = _find_button_by_text(mock_page, "skip")

    assert btn_retry is not None
    assert btn_reconfig is not None
    assert btn_skip is not None

    _trigger_click(btn_retry)
    run_task.assert_any_call(mock_controller.retry)

    _trigger_click(btn_reconfig)
    run_task.assert_any_call(mock_controller.reconfigure)

    _trigger_click(btn_skip)
    mock_controller.skip.assert_called_once()


def test_render_error_view_engine_missing(mock_page, mock_controller, mock_i18n):
    run_task = MagicMock()
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), run_task)

    context = StartupContext(error="db_engine_missing", detail=None)
    with patch("ui.startup_views.I18n", mock_i18n):
        renderer._render_error_view(context)

    # Check that error msg text is error_db_engine_missing using robust traversal helper
    texts = _find_controls(mock_page, ft.Text)
    text_values = {t.value for t in texts if hasattr(t, "value")}
    assert "error_db_engine_missing" in text_values


def test_render_onboarding(mock_page, mock_controller):
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), MagicMock())
    mock_wizard_cls = MagicMock()

    with patch("ui.views.onboarding_wizard.OnboardingWizard", mock_wizard_cls):
        renderer._render_onboarding()

    mock_wizard_cls.assert_called_once_with(mock_page, on_complete=mock_controller.onboarding_complete)
    assert len(mock_page.controls) > 0


def test_render_main_app(mock_page, mock_controller):
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), MagicMock())
    mock_layout = MagicMock()
    mock_news_svc = MagicMock()

    with (
        patch("ui.app_layout.AppLayout", return_value=mock_layout),
        patch("services.news_subscription_service.NewsSubscriptionService", return_value=mock_news_svc),
    ):
        renderer._render_main_app()

    mock_layout.show.assert_called_once()
    mock_news_svc.add_listener.assert_called_once()

    # Verify nested news alert callback when page has toast
    on_news_alert_fn = mock_news_svc.add_listener.call_args[0][0]
    mock_page.toast = MagicMock()
    on_news_alert_fn("New stock alert!")
    mock_page.toast.show.assert_called_once_with("📰 New stock alert!", toast_type="info")


def test_render_main_app_no_toast(mock_page, mock_controller):
    renderer = StartupViewRenderer(mock_page, mock_controller, MagicMock(), MagicMock(), MagicMock())
    mock_layout = MagicMock()
    mock_news_svc = MagicMock()

    with (
        patch("ui.app_layout.AppLayout", return_value=mock_layout),
        patch("services.news_subscription_service.NewsSubscriptionService", return_value=mock_news_svc),
    ):
        renderer._render_main_app()

    on_news_alert_fn = mock_news_svc.add_listener.call_args[0][0]

    # Verify nested news alert callback when page doesn't have toast
    if hasattr(mock_page, "toast"):
        delattr(mock_page, "toast")
    # This should not raise an error
    on_news_alert_fn("New stock alert!")
