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

import asyncio
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


def test_build_loading_view_scenario_none_keeps_original_text(mock_i18n):
    """scenario=None（external 模式）→ 显示原有 wizard_status_init 文案，仅 1 个 Text."""
    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=None)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 1
    assert texts[0].value == "wizard_status_init"


def test_build_loading_view_scenario_first_run(mock_i18n):
    """FIRST_RUN → 标题 startup_embedded_pg_first_run_title + 提示 startup_embedded_pg_first_run_hint."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.FIRST_RUN)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 2
    assert texts[0].value == "startup_embedded_pg_first_run_title"
    assert texts[1].value == "startup_embedded_pg_first_run_hint"


def test_build_loading_view_scenario_normal(mock_i18n):
    """NORMAL → 标题 startup_embedded_pg_normal_title + 提示 startup_embedded_pg_normal_hint."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.NORMAL)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 2
    assert texts[0].value == "startup_embedded_pg_normal_title"
    assert texts[1].value == "startup_embedded_pg_normal_hint"


def test_build_loading_view_scenario_unknown_uses_normal_text(mock_i18n):
    """UNKNOWN → 与 NORMAL 共用普通启动文案（保守文案）."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.UNKNOWN)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 2
    assert texts[0].value == "startup_embedded_pg_normal_title"
    assert texts[1].value == "startup_embedded_pg_normal_hint"


# --- P1-2: LoadingView 已等待时间反馈 ---


def test_build_loading_view_first_run_with_elapsed_shows_elapsed(mock_i18n):
    """P1-2: FIRST_RUN + elapsed_seconds=5 → 显示 \"已等待 5s\" 文本."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.FIRST_RUN, elapsed_seconds=5)
    texts = _find_controls(view, ft.Text)
    # 标题 + 提示 + 已等待时间 = 3 个 Text
    assert len(texts) == 3
    # 第三个 Text 是已等待时间（i18n key 格式化后返回 key 本身，mock_i18n.get 返回 key）
    assert "startup_embedded_pg_elapsed_seconds" in texts[2].value


def test_build_loading_view_first_run_zero_elapsed_no_show(mock_i18n):
    """P1-2: FIRST_RUN + elapsed_seconds=0 → 不显示已等待时间（避免初始闪烁）."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.FIRST_RUN, elapsed_seconds=0)
    texts = _find_controls(view, ft.Text)
    # 仅标题 + 提示 = 2 个 Text，无已等待时间
    assert len(texts) == 2


def test_build_loading_view_normal_no_elapsed(mock_i18n):
    """P1-2: NORMAL + elapsed_seconds=5 → 不显示已等待时间（2-5s 等待不需要反馈）."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.NORMAL, elapsed_seconds=5)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 2


def test_build_loading_view_unknown_no_elapsed(mock_i18n):
    """P1-2: UNKNOWN + elapsed_seconds=5 → 不显示已等待时间（保守文案）."""
    from app.bootstrap import EmbeddedPgStartupScenario

    with patch("ui.startup_views.I18n", mock_i18n):
        view = _build_loading_view(scenario=EmbeddedPgStartupScenario.UNKNOWN, elapsed_seconds=5)
    texts = _find_controls(view, ft.Text)
    assert len(texts) == 2


def test_loading_view_is_ft_component():
    """LoadingView 必须用 @ft.component 装饰（声明式契约守护）."""
    from ui.startup_views import LoadingView

    assert hasattr(LoadingView, "__wrapped__"), "LoadingView 必须用 @ft.component 装饰"


def test_loading_view_component_starts_timer_on_mount(mock_i18n_state, mock_app_colors_state):
    """P1-2: LoadingView 挂载时启动定时器（page.run_task 被调用）."""
    from tests.unit.ui.component_renderer import (
        FakePage,
        make_component,
        run_mount_effects,
    )
    from ui.startup_views import LoadingView

    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    component = make_component(LoadingView, scenario=None)
    run_mount_effects(component, page=page)

    assert page.session.scheduled_effects, "use_effect setup 应被调度"
    # page.run_task 被调用启动定时器
    assert page.run_task.called  # noqa: weak-assertion <验证 page.run_task 被调用是测试目标本身，mock 无返回值可进一步断言>


def test_loading_view_component_cancels_timer_on_unmount(mock_i18n_state, mock_app_colors_state):
    """P1-2: LoadingView 卸载时取消定时器（task.cancel 被调用，R2 红线）."""
    from tests.unit.ui.component_renderer import (
        FakePage,
        make_component,
        run_mount_effects,
        run_unmount_effects,
    )
    from ui.startup_views import LoadingView

    page = FakePage()
    mock_task = MagicMock()
    page.run_task = MagicMock(return_value=mock_task)  # type: ignore[method-assign]
    component = make_component(LoadingView, scenario=None)
    run_mount_effects(component, page=page)

    # 卸载组件，触发 cleanup
    run_unmount_effects(component)

    # cleanup 应取消 task
    mock_task.cancel.assert_called_once()  # noqa: weak-assertion <验证 task.cancel 被调用是 R2 红线测试目标本身>


def test_get_page_returns_none_outside_render_context():
    """L41-42: 非渲染上下文调用 _get_page() 返回 None (不抛 RuntimeError).

    conftest.py ``_reset_context_page`` autouse fixture 已清理 ``_context_page``，
    此处直接调用 ``_get_page()`` 应进入 except 分支返回 None。
    覆盖 ``_get_page`` 的 RuntimeError except 分支（diff-coverage 补齐）。
    """
    from ui.startup_views import _get_page

    assert _get_page() is None


def test_loading_view_setup_timer_early_return_when_no_page(mock_i18n_state, mock_app_colors_state):
    """L170: LoadingView mount 时若 _get_page() 返回 None, 早退不启动定时器.

    覆盖 ``_setup_timer`` 中 ``if page is None: return`` 的早退分支
    （diff-coverage 补齐）。
    """
    from tests.unit.ui.component_renderer import (
        FakePage,
        make_component,
        run_mount_effects,
    )
    from ui.startup_views import LoadingView

    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    component = make_component(LoadingView, scenario=None)

    # mock _get_page 返回 None, 模拟非渲染上下文 (如 page 尚未挂载)
    with patch("ui.startup_views._get_page", return_value=None):
        run_mount_effects(component, page=page)

    # page.run_task 不应被调用 (page 为 None 时早退)
    assert not page.run_task.called  # noqa: weak-assertion <验证 page.run_task 未被调用是早退分支测试目标本身>


def test_loading_view_tick_coroutine_runs_and_propagates_cancel(mock_i18n_state, mock_app_colors_state):
    """L173-179: _tick 协程每秒更新计数器, CancelledError 被传播 (R2 红线).

    覆盖 ``_tick`` 协程体 (try/while/await/set/except/raise)。
    通过捕获 ``page.run_task`` 接收的 coro, 在事件循环中执行验证 R2 红线。
    """
    from tests.unit.ui.component_renderer import (
        FakePage,
        make_component,
        run_mount_effects,
    )
    from ui.startup_views import LoadingView

    page = FakePage()
    captured_tick_fn: list = []
    mock_task = MagicMock()

    def fake_run_task(tick_fn):
        captured_tick_fn.append(tick_fn)
        return mock_task

    page.run_task = fake_run_task  # type: ignore[method-assign]

    component = make_component(LoadingView, scenario=None)
    run_mount_effects(component, page=page)

    assert captured_tick_fn, "_tick 函数应被传给 page.run_task"  # noqa: weak-assertion <验证 tick_fn 被捕获是后续执行的前提>

    # 在事件循环中执行 _tick 协程, 验证 CancelledError 被传播 (R2 红线)
    async def run_test():
        task = asyncio.ensure_future(captured_tick_fn[0]())
        # 让 _tick 进入 await asyncio.sleep(1)
        await asyncio.sleep(0.1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):  # noqa: weak-assertion R2 红线契约仅验证 CancelledError 类型传播即可, raises 即充分
            await task

    asyncio.run(run_test())


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
