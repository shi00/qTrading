"""Startup UI — 声明式组件 (Phase G.1).

从命令式 StartupViewRenderer class 重写为 @ft.component 范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 ``class StartupViewRenderer`` → ``@ft.component def StartupView()``
- controller 通过 _StartupBridge 桥接触发组件重渲染 (bridge.notify → set_state)
- 状态驱动渲染: state/context 用 use_state, 根据 StartupState 条件渲染
- dialog 管理: ``ft.use_dialog()`` 声明式挂载/卸载 (§10.1), dialog 由 state 驱动条件创建
- i18n 通过 ft.use_state(get_observable_state) 自动重渲染
- 移除 page.clean()/page.add()/page.update() 命令式调用
- page 访问: 不持有 page 引用 (controller 回调通过 run_task_fn 注入)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

# 架构例外 (§4.1): app 层应仅被 main.py 调用。此处的导入属于 main.py 启动流程
# 的延伸 (main.py 装配 StartupView/LoadingView), 不是 ui 层的正常业务导入。
# 已在 tests/unit/test_architecture_boundaries.py 的 KNOWN_EXCEPTIONS 中记录。
from app.bootstrap import EmbeddedPgStartupScenario
from app.startup_controller import StartupContext, StartupController, StartupState
from ui.components.flet_type_helpers import safe_controls, safe_on_click
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles

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


class _StartupBridge:
    """controller on_state_change → 声明式组件 set_state 的桥.

    main.py 创建空 bridge, 传给 controller.on_state_change 和 StartupView。
    StartupView 首次渲染时 (use_effect setup) 注入 dispatch (set_state);
    controller 的 on_state_change 调 bridge.notify 触发重渲染。

    时序安全: notify 在 dispatch 绑定前仅更新 state/context 快照,
    _setup_bridge 绑定 dispatch 后同步 bridge.state != state 的变更, 不丢失状态。
    """

    def __init__(self) -> None:
        self.dispatch: Callable[[StartupState, StartupContext], None] | None = None
        self.state: StartupState = StartupState.LOADING
        self.context: StartupContext = StartupContext()

    def notify(self, state: StartupState, context: StartupContext) -> None:
        self.state = state
        self.context = context
        if self.dispatch is not None:
            self.dispatch(state, context)


# --- 纯函数构建器 (可独立测试) ---


def _build_loading_view(scenario: EmbeddedPgStartupScenario | None = None) -> ft.Container:
    """构造 loading 启动视图.

    根据 scenario 显示差异化文案（UX 改进 spec §启动侧方案 A）：
    - ``None``：原有 "Initializing..." 文案（external 模式）
    - ``FIRST_RUN``：标题 ``startup_embedded_pg_first_run_title`` + 提示 ``startup_embedded_pg_first_run_hint``
    - ``NORMAL`` / ``UNKNOWN``：标题 ``startup_embedded_pg_normal_title`` + 提示 ``startup_embedded_pg_normal_hint``
    """
    children: list[ft.Control] = [ft.ProgressRing(width=40, height=40, stroke_width=3)]
    if scenario is None:
        # external 模式：原有 "Initializing..." 单行
        children.append(ft.Text(I18n.get("wizard_status_init") or "Initializing...", size=AppStyles.FONT_SIZE_TITLE))
    else:
        if scenario == EmbeddedPgStartupScenario.FIRST_RUN:
            title_key, hint_key = (
                "startup_embedded_pg_first_run_title",
                "startup_embedded_pg_first_run_hint",
            )
        else:
            # NORMAL / UNKNOWN 共用普通启动文案（spec §启动 UI 先渲染）
            title_key, hint_key = (
                "startup_embedded_pg_normal_title",
                "startup_embedded_pg_normal_hint",
            )
        children.append(
            ft.Text(
                I18n.get(title_key),
                size=AppStyles.FONT_SIZE_HEADLINE,
                weight=ft.FontWeight.BOLD,
            )
        )
        children.append(ft.Text(I18n.get(hint_key), size=AppStyles.FONT_SIZE_BODY, color=AppColors.TEXT_SECONDARY))
    return ft.Container(
        content=ft.Column(
            children,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.CENTER,
            spacing=20,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
    )


@ft.component
def LoadingView(scenario: EmbeddedPgStartupScenario | None = None) -> ft.Container:
    """启动期 LoadingView 独立组件（prepare_database_runtime 期间临时显示）。

    与 StartupView 的 LOADING 状态共用 ``_build_loading_view``，但独立挂载，
    避免依赖 StartupController（cache_manager 尚未构造）。

    调用场景：embedded 模式下，main.py 在 ``prepare_database_runtime`` 之前
    ``page.render(LoadingView, scenario=...)`` 渲染一帧，让用户看到反馈；
    ``prepare_database_runtime`` 完成后再 ``page.render(RootView, ...)`` 替换。

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - 不持有业务状态；scenario 作为 prop 推送
    """
    ft.use_state(get_observable_state)
    return _build_loading_view(scenario)


def _build_upgrade_dialog(on_upgrade: Callable[[ft.ControlEvent], None]) -> ft.AlertDialog:
    """构造 DB 升级确认对话框."""
    return ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("db_upgrade_needed_title")),
        content=ft.Text(I18n.get("db_upgrade_needed_content")),
        actions=safe_controls(
            [
                ft.Button(I18n.get("db_upgrade_btn"), on_click=safe_on_click(on_upgrade)),
            ]
        ),
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _build_upgrade_in_progress_dialog() -> ft.AlertDialog:
    """构造 DB 升级进行中对话框."""
    return ft.AlertDialog(
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


def _build_upgrade_success_dialog(on_ok: Callable[[ft.ControlEvent], None]) -> ft.AlertDialog:
    """构造 DB 升级成功对话框."""
    return ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("db_upgrade_success_title")),
        content=ft.Text(I18n.get("db_upgrade_success_content")),
        actions=safe_controls(
            [
                ft.TextButton(I18n.get("common_ok"), on_click=safe_on_click(on_ok)),
            ]
        ),
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _build_upgrade_failed_dialog(
    on_exit: Callable[[ft.ControlEvent], None],
    on_retry: Callable[[ft.ControlEvent], None],
) -> ft.AlertDialog:
    """构造 DB 升级失败对话框."""
    return ft.AlertDialog(
        modal=True,
        title=ft.Text(I18n.get("db_upgrade_error_title")),
        content=ft.Text(I18n.get("db_upgrade_error_content")),
        actions=safe_controls(
            [
                ft.TextButton(I18n.get("exit_program"), on_click=safe_on_click(on_exit)),
                ft.Button(I18n.get("retry_upgrade"), on_click=safe_on_click(on_retry)),
            ]
        ),
        actions_alignment=ft.MainAxisAlignment.END,
    )


def _build_error_view(
    context: StartupContext,
    on_retry: Callable[[ft.ControlEvent], None],
    on_reconfigure: Callable[[ft.ControlEvent], None],
    on_skip: Callable[[ft.ControlEvent], None],
) -> ft.Container:
    """构造启动错误视图 (db_init_failed / db_engine_missing / task_manager_init_failed)."""
    error = context.error or ""
    return ft.Container(
        content=ft.Column(
            safe_controls(
                [
                    ft.Icon(ft.Icons.ERROR_OUTLINE, color=AppColors.ERROR, size=AppStyles.ICON_SIZE_XL),
                    ft.Text(
                        I18n.get("error_db_init_failed")
                        if error != "db_engine_missing"
                        else I18n.get("error_db_engine_missing"),
                        size=AppStyles.FONT_SIZE_HEADLINE,
                        weight=ft.FontWeight.BOLD,
                    ),
                    ft.Text(
                        _get_localized_detail(context.detail or "")[:200],
                        color=AppColors.ERROR,
                        size=AppStyles.FONT_SIZE_LG,
                    ),
                    ft.Row(
                        safe_controls(
                            [
                                ft.Button(I18n.get("retry"), icon=ft.Icons.REFRESH, on_click=safe_on_click(on_retry)),
                                ft.TextButton(
                                    I18n.get("db_reconfigure"),
                                    icon=ft.Icons.SETTINGS,
                                    on_click=safe_on_click(on_reconfigure),
                                ),
                                ft.TextButton(I18n.get("skip"), on_click=safe_on_click(on_skip)),
                            ]
                        ),
                        alignment=ft.MainAxisAlignment.CENTER,
                        spacing=20,
                    ),
                ]
            ),
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=10,
        ),
        expand=True,
        alignment=ft.Alignment.CENTER,
    )


def _build_onboarding_view(on_complete: Callable[[], Any]) -> ft.Container:
    """构造 onboarding 向导视图."""
    from ui.views.onboarding_wizard import OnboardingWizard

    return ft.Container(
        content=OnboardingWizard(on_complete=on_complete),
        expand=True,
        padding=40,
    )


@ft.component
def StartupView(
    controller: StartupController,
    bridge: _StartupBridge,
    run_task_fn: Callable[..., Any],
) -> ft.Control:
    """启动期声明式组件 (Phase G.1).

    根据 StartupState 条件渲染 loading/error/onboarding/main_app;
    dialog 用 ``ft.use_dialog()`` 声明式挂载/卸载 (§10.1), 由 state 驱动条件创建;
    controller 通过 _StartupBridge 桥接触发重渲染。

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI:
    - i18n 通过 ft.use_state(get_observable_state) 自动重渲染
    - 状态驱动: state/context 用 use_state
    - 不持有 page 引用 (controller 回调通过 run_task_fn 注入)
    - 异步任务: run_task_fn 调度; R2 CancelledError 由 controller 内部处理
    """
    ft.use_state(get_observable_state)

    state, set_state = ft.use_state(bridge.state)
    context, set_context = ft.use_state(bridge.context)

    # --- bridge 注入 dispatch (controller → set_state 重渲染) ---
    def _setup_bridge() -> None:
        def _dispatch(new_state: StartupState, new_ctx: StartupContext) -> None:
            set_state(new_state)
            set_context(new_ctx)

        bridge.dispatch = _dispatch
        # 同步 dispatch 绑定前可能已发生的状态变更:
        # page.render() 调度 effects 但不同步执行, controller.start() (async)
        # 可能在 dispatch 绑定前调 bridge.notify 导致状态丢失。
        if bridge.state != state:
            _dispatch(bridge.state, bridge.context)

    def _cleanup_bridge() -> None:
        bridge.dispatch = None

    ft.use_effect(_setup_bridge, dependencies=[], cleanup=_cleanup_bridge)

    # --- dialog 管理 (ft.use_dialog 声明式, §10.1) ---
    # dialog 由 state 驱动条件创建; state 变化时旧 dialog 自动卸载, 新 dialog 自动挂载
    dialog: ft.AlertDialog | None = None
    if state == StartupState.NEED_UPGRADE:

        def _on_upgrade(e: ft.ControlEvent) -> None:
            run_task_fn(controller.upgrade)

        dialog = _build_upgrade_dialog(_on_upgrade)
    elif state == StartupState.UPGRADE_IN_PROGRESS:
        dialog = _build_upgrade_in_progress_dialog()
    elif state == StartupState.UPGRADE_SUCCESS:

        def _on_ok(e: ft.ControlEvent) -> None:
            run_task_fn(controller.proceed_after_upgrade_success)

        dialog = _build_upgrade_success_dialog(_on_ok)
    elif state == StartupState.UPGRADE_FAILED:

        def _on_exit(e: ft.ControlEvent) -> None:
            # NOTE(lazy): _on_exit 不触发 state 变化, dialog 在 exit cleanup (≤5s) 期间保持可见可交互.
            #   ceiling: exit cleanup 5s 窗口内 Retry 可点击, 与 force_exit 竞态.
            #   upgrade: 重写为 EXITING 状态时处理 (独立任务).
            controller.upgrade_exit()

        def _on_retry(e: ft.ControlEvent) -> None:
            run_task_fn(controller.upgrade_retry)

        dialog = _build_upgrade_failed_dialog(_on_exit, _on_retry)

    ft.use_dialog(dialog)

    # --- news alert 监听 (仅 READY 时注册, cleanup 必须退订避免泄漏) ---
    # CLAUDE.md §3.2 MVVM: View 不直调 NewsSubscriptionService, 经 HomeViewModel 命令转发
    # (合规范例 home_view_model.py:111). View 仅保留需要 page 访问的 toast 回调.
    news_alert_cb_ref = ft.use_ref(None)

    def _setup_news_alert() -> None:
        if state != StartupState.READY:
            return
        from ui.viewmodels.home_view_model import HomeViewModel

        def on_news_alert(msg: str) -> None:
            try:
                page = ft.context.page
                if page is not None and hasattr(page, "toast") and page.toast:  # type: ignore[attr-defined]  # [reason: 动态挂载 toast 属性, ft.Page 存根未声明]
                    page.toast.show(msg, toast_type="info")  # type: ignore[attr-defined]  # [reason: 动态挂载 toast 属性, ft.Page 存根未声明]
            except RuntimeError:
                pass

        news_alert_cb_ref.current = on_news_alert
        HomeViewModel.register_news_alert_listener(on_news_alert)

    def _cleanup_news_alert() -> None:
        cb = news_alert_cb_ref.current
        if cb is None:
            return
        from ui.viewmodels.home_view_model import HomeViewModel

        HomeViewModel.unregister_news_alert_listener(cb)
        news_alert_cb_ref.current = None

    ft.use_effect(_setup_news_alert, dependencies=[state], cleanup=_cleanup_news_alert)

    # --- 渲染 (state 驱动条件渲染) ---
    if state == StartupState.READY:
        from ui.app_layout import AppLayout

        return AppLayout()
    if state == StartupState.NEED_ONBOARDING:
        return _build_onboarding_view(controller.onboarding_complete)
    if state == StartupState.INIT_FAILED:

        def _on_retry(e: ft.ControlEvent) -> None:
            run_task_fn(controller.retry)

        def _on_reconfigure(e: ft.ControlEvent) -> None:
            run_task_fn(controller.reconfigure)

        def _on_skip(e: ft.ControlEvent) -> None:
            controller.skip()

        return _build_error_view(context, _on_retry, _on_reconfigure, _on_skip)
    # LOADING / NEED_UPGRADE / UPGRADE_* → loading 背景 (dialog 由 ft.use_dialog 声明式管理)
    # 传递 context.embedded_pg_scenario 以显示差异化文案（embedded PG 启动期）
    return _build_loading_view(scenario=context.embedded_pg_scenario)
