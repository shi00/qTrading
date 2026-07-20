"""StartupView 声明式组件单测 (Phase G.1).

从命令式 StartupViewRenderer 测试重写为声明式范式测试:
- 纯函数构建器 (_build_*) 独立测试 (无状态, 可直接调用)
- _StartupBridge 桥接行为测试 (纯 Python class)
- _get_localized_detail 保留
- StartupView 组件契约守护 (@ft.component 装饰)
- 有状态组件 (use_state/use_effect) 的渲染测试走集成测试, 不在此覆盖
"""

# pyright: reportAttributeAccessIssue=false
# 本文件含测试替身/mock/monkey-patch 模式，触发 动态属性访问（mock/stub/monkey-patch）。
# pyright 无法验证替身类与生产类型的兼容性，统一在此文件局部禁用相关告警，
# 测试行为由测试用例本身验证。

from unittest.mock import MagicMock, patch

import flet as ft
import pytest

from app.startup_controller import StartupContext, StartupController, StartupState
from ui.startup_views import (
    StartupView,
    _StartupBridge,
    _build_error_view,
    _build_loading_view,
    _build_onboarding_view,
    _build_upgrade_dialog,
    _build_upgrade_failed_dialog,
    _build_upgrade_in_progress_dialog,
    _build_upgrade_success_dialog,
    _get_localized_detail,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_controller():
    return MagicMock(spec=StartupController)


def _trigger_click(button):
    """Safely trigger Flet control on_click callback in tests."""
    assert button.on_click is not None
    button.on_click(MagicMock())  # type: ignore[reportArgumentType, reportOptionalCall]


def _find_controls(control, control_type):
    """Recursively find all controls of a given type in a control tree.

    跳过非 ft.Control 对象 (避免 MagicMock 下 getattr/hasattr 自动生成子节点致无限递归)。
    """
    found = []
    if not isinstance(control, ft.Control):
        return found
    if isinstance(control, control_type):
        found.append(control)
    if hasattr(control, "controls") and isinstance(control.controls, list):
        for child in control.controls:
            found.extend(_find_controls(child, control_type))
    if hasattr(control, "content") and isinstance(control.content, ft.Control):
        found.extend(_find_controls(control.content, control_type))
    if hasattr(control, "actions") and isinstance(control.actions, list):
        for child in control.actions:
            found.extend(_find_controls(child, control_type))
    return found


def _find_button_by_text(root, text: str):
    """Find a button with the given text recursively."""
    buttons = _find_controls(root, (ft.Button, ft.TextButton))
    for btn in buttons:
        # V1: ft.Button/ft.TextButton 用 content 存储文本（V0 用 text）
        btn_text = getattr(btn, "content", "")
        if btn_text == text:
            return btn
    return None


# --- _get_localized_detail ---


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


# --- _StartupBridge ---


def test_bridge_initial_state():
    bridge = _StartupBridge()
    assert bridge.state == StartupState.LOADING
    assert bridge.context == StartupContext()
    assert bridge.dispatch is None


def test_bridge_notify_updates_state_context():
    bridge = _StartupBridge()
    ctx = StartupContext(error="db_init_failed", detail="conn error")
    bridge.notify(StartupState.INIT_FAILED, ctx)
    assert bridge.state == StartupState.INIT_FAILED
    assert bridge.context is ctx


def test_bridge_notify_no_dispatch_when_unbound():
    """dispatch 未绑定时 notify 仅更新快照, 不报错."""
    bridge = _StartupBridge()
    bridge.notify(StartupState.READY, StartupContext())
    assert bridge.state == StartupState.READY
    assert bridge.dispatch is None


def test_bridge_notify_calls_dispatch_when_bound():
    """dispatch 绑定后 notify 触发 dispatch (controller → set_state 重渲染)."""
    bridge = _StartupBridge()
    calls: list[tuple[StartupState, StartupContext]] = []
    bridge.dispatch = lambda s, c: calls.append((s, c))

    ctx = StartupContext(error="err")
    bridge.notify(StartupState.INIT_FAILED, ctx)
    assert calls == [(StartupState.INIT_FAILED, ctx)]
    assert bridge.state == StartupState.INIT_FAILED


# --- 纯函数构建器 ---


def test_build_loading_view(mock_i18n):
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view()
    assert isinstance(view, ft.Container)
    assert len(_find_controls(view, ft.ProgressRing)) == 1
    assert len(_find_controls(view, ft.Text)) == 1


def test_build_upgrade_dialog(mock_i18n):
    on_upgrade = MagicMock()
    with patch("ui.startup_views.I18n", mock_i18n):
        dialog = _build_upgrade_dialog(on_upgrade)
    assert isinstance(dialog, ft.AlertDialog)
    assert dialog.modal is True
    button = dialog.actions[0]
    assert isinstance(button, ft.Button)
    _trigger_click(button)
    on_upgrade.assert_called_once()


def test_build_upgrade_in_progress_dialog(mock_i18n):
    with patch("ui.startup_views.I18n", mock_i18n):
        dialog = _build_upgrade_in_progress_dialog()
    assert isinstance(dialog, ft.AlertDialog)
    assert isinstance(dialog.content, ft.Column)
    assert any(isinstance(c, ft.ProgressBar) for c in dialog.content.controls)


def test_build_upgrade_success_dialog(mock_i18n):
    on_ok = MagicMock()
    with patch("ui.startup_views.I18n", mock_i18n):
        dialog = _build_upgrade_success_dialog(on_ok)
    assert isinstance(dialog, ft.AlertDialog)
    button = dialog.actions[0]
    assert isinstance(button, ft.TextButton)
    _trigger_click(button)
    on_ok.assert_called_once()


def test_build_upgrade_failed_dialog(mock_i18n):
    on_exit = MagicMock()
    on_retry = MagicMock()
    with patch("ui.startup_views.I18n", mock_i18n):
        dialog = _build_upgrade_failed_dialog(on_exit, on_retry)
    assert isinstance(dialog, ft.AlertDialog)
    btn_exit = dialog.actions[0]
    btn_retry = dialog.actions[1]
    _trigger_click(btn_exit)
    on_exit.assert_called_once()
    _trigger_click(btn_retry)
    on_retry.assert_called_once()


def test_build_error_view_db_init_failed(mock_i18n):
    on_retry = MagicMock()
    on_reconfigure = MagicMock()
    on_skip = MagicMock()
    context = StartupContext(error="db_init_failed", detail="connection error")
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_error_view(context, on_retry, on_reconfigure, on_skip)
    assert isinstance(view, ft.Container)

    btn_retry = _find_button_by_text(view, "retry")
    btn_reconfig = _find_button_by_text(view, "db_reconfigure")
    btn_skip = _find_button_by_text(view, "skip")
    assert btn_retry is not None
    assert btn_reconfig is not None
    assert btn_skip is not None

    _trigger_click(btn_retry)
    on_retry.assert_called_once()
    _trigger_click(btn_reconfig)
    on_reconfigure.assert_called_once()
    _trigger_click(btn_skip)
    on_skip.assert_called_once()


def test_build_error_view_engine_missing(mock_i18n):
    context = StartupContext(error="db_engine_missing", detail=None)
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_error_view(context, MagicMock(), MagicMock(), MagicMock())
    texts = _find_controls(view, ft.Text)
    text_values = {t.value for t in texts if hasattr(t, "value")}
    assert "error_db_engine_missing" in text_values


def test_build_onboarding_view(mock_controller):
    mock_wizard_cls = MagicMock()
    with patch("ui.views.onboarding_wizard.OnboardingWizard", mock_wizard_cls):
        view = _build_onboarding_view(mock_controller.onboarding_complete)
    assert isinstance(view, ft.Container)
    mock_wizard_cls.assert_called_once_with(on_complete=mock_controller.onboarding_complete)


# --- StartupView 契约守护 ---


def test_startup_view_is_ft_component():
    """StartupView 必须用 @ft.component 装饰 (声明式契约守护)."""
    assert hasattr(StartupView, "__wrapped__"), "StartupView 必须用 @ft.component 装饰"


def test_startup_view_uses_use_dialog():
    """DoD: dialog 必须通过 ft.use_dialog() 声明式管理 (§10.1), 禁止 show_dialog_fn/hide_dialog_fn 回归。"""
    from pathlib import Path

    import ui.startup_views as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "ft.use_dialog(" in source, "必须使用 ft.use_dialog() 声明式管理 dialog"
    assert "show_dialog_fn" not in source, "禁止 show_dialog_fn 命令式回调注入"
    assert "hide_dialog_fn" not in source, "禁止 hide_dialog_fn 命令式回调注入"
    assert "current_dialog_ref" not in source, "禁止 current_dialog_ref 命令式 ref 管理"
    assert "_setup_dialog" not in source, "禁止 _setup_dialog 命令式 use_effect"
    assert "page.show_dialog" not in source, "禁止 page.show_dialog 命令式 API"
    assert "page.pop_dialog" not in source, "禁止 page.pop_dialog 命令式 API"


def test_startup_view_does_not_directly_call_news_subscription_service():
    """P2-2 (CLAUDE.md §3.2 MVVM): View 不直调 NewsSubscriptionService,
    监听注册/退订必须经 HomeViewModel 命令转发 (合规范例 home_view_model.py:111).

    检查实际违规模式 (import / 实例化调用), 允许注释中提及类名作为开发者提示.
    """
    from pathlib import Path

    import ui.startup_views as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    # 禁止直接 import (函数级 lazy import 或模块级)
    assert "from services.news_subscription_service import" not in source, (
        "startup_views.py 禁止 import NewsSubscriptionService (P2-2), 应通过 HomeViewModel 命令转发"
    )
    assert "import services.news_subscription_service" not in source, (
        "startup_views.py 禁止 import services.news_subscription_service (P2-2)"
    )
    # 禁止直接实例化调用
    assert "NewsSubscriptionService()" not in source, (
        "startup_views.py 禁止直接实例化 NewsSubscriptionService (P2-2), "
        "应通过 HomeViewModel.register_news_alert_listener / unregister_news_alert_listener 命令转发"
    )
    # P2-2: 必须通过 HomeViewModel 静态命令转发 (不创建临时实例)
    assert "HomeViewModel.register_news_alert_listener" in source, (
        "必须通过 HomeViewModel.register_news_alert_listener 注册新闻告警监听 (P2-2)"
    )
    assert "HomeViewModel.unregister_news_alert_listener" in source, (
        "必须通过 HomeViewModel.unregister_news_alert_listener 退订新闻告警监听 (P2-2)"
    )
    # 禁止临时实例化 HomeViewModel 仅用于转发命令 (P2-2)
    assert "HomeViewModel().register_news_alert_listener" not in source, (
        "禁止临时实例化 HomeViewModel 仅用于转发命令 (P2-2), 应改为静态调用"
    )
    assert "HomeViewModel().unregister_news_alert_listener" not in source, (
        "禁止临时实例化 HomeViewModel 仅用于转发命令 (P2-2), 应改为静态调用"
    )
