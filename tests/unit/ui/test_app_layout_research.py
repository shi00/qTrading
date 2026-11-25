"""AppLayout 研究观察层运行时测试（检视 P02）。

覆盖验收点：
1. 壳层公共区有研究入口，展开在不新增第八导航的前提下挂非模态观察组件；
2. 导航点击（含当前业务项）/ Ctrl+1—7 只收起观察层：不清草稿、不停止/取消研究；
3. 统一决定经仲裁管线（旧 Dialog 打开/他人占用时拒绝，不触发 facade 写调用）；
4. 观察面板只渲染状态与显式回调（不绑定键盘批准）。
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
    run_unmount_effects,
)
from core.agent_types import stable_id
from services.agent_service.business_research import snapshot_source
from ui import agent_decision_arbiter
from ui.viewmodels import research_observer_view_model as vm_mod
from ui.viewmodels.business_research_view_model import BusinessResearchRequest
from ui.viewmodels.research_observer_view_model import (
    ExecutionNodeView,
    LoadedArtifactBody,
    PendingDecision,
    ResearchObserverState,
    SessionArtifactView,
    SessionDraftView,
    SessionMessageView,
)

pytestmark = pytest.mark.unit


def _decision(decision_id: str = "d-1", *, kind: str = "user_choice") -> Any:
    return SimpleNamespace(
        decision_id=decision_id,
        execution_id="e-1",
        round_id="r-1",
        decision_kind=kind,
        revision=2,
        created_at="2026-11-17T01:02:03.000000Z",
        expires_at=None,
        question_payload='{"question": "选哪个？"}',
        answer_command_id=None,
    )


class _FakeService:
    """最小 facade 替身：记录只读查询与答复写调用。"""

    def __init__(self) -> None:
        self.search_calls: list[Any] = []
        self.read_calls: list[Any] = []
        self.answer_calls: list[Any] = []
        self.change_calls: list[Any] = []
        self.source_registrations: list[Any] = []
        self.draft_calls: list[Any] = []

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.search_calls.append((query, user_scope))
        return SimpleNamespace(
            hits=(SimpleNamespace(session_id="s-1"),),
            read_moment="2026-11-17T03:00:00.000000Z",
        )

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_calls.append((query, user_scope))
        return SimpleNamespace(
            summary=SimpleNamespace(
                title="研究 A",
                updated_at="2026-11-17T02:00:00.000000Z",
                draft_revision=0,
                cleaned_at=None,
            ),
            executions=(
                SimpleNamespace(
                    execution_id="e-1",
                    display_state="running",
                    parent_execution_id=None,
                    model_attempt_count=0,
                    tool_call_count=0,
                    active_ms=0,
                    goal_coverage="unknown",
                    ended_at=None,
                ),
            ),
            messages=SimpleNamespace(items=(), has_more=False, next_cursor=None),
            artifacts=SimpleNamespace(items=(), has_more=False, next_cursor=None),
            decisions=(_decision(),),
        )

    async def read_artifact(self, query: Any, *, user_scope: str) -> Any:
        return SimpleNamespace(
            body_text="完整正文",
            next_cursor=None,
            body_available=True,
            unavailable_reason=None,
        )

    async def answer(self, request: Any) -> Any:
        self.answer_calls.append(request)
        return SimpleNamespace(delivery_state=SimpleNamespace(value="not_delivered"))

    async def change_session(self, command: Any) -> Any:
        self.change_calls.append(command)
        return SimpleNamespace(session_id=command.session_id)

    async def register_draft_sources(self, **kwargs: Any) -> Any:
        self.source_registrations.append(kwargs)
        return SimpleNamespace()

    async def save_draft(self, command: Any) -> Any:
        self.draft_calls.append(command)
        return SimpleNamespace(draft_revision=1)


class _FakeDecisionVm:
    """决定管线单测用的最小 VM：只记录 submit_answer 调用（含归属）。"""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, str, str]] = []

    async def submit_answer(self, decision_id: str, *, execution_id: str, round_id: str) -> None:
        self.submitted.append((decision_id, execution_id, round_id))


@pytest.fixture(autouse=True)
def _reset_research_shared() -> Iterator[None]:
    from ui.viewmodels.business_research_view_model import reset_shared_business_research

    vm_mod.reset_shared_research_observer()
    reset_shared_business_research()
    agent_decision_arbiter.get_decision_arbiter().reset()
    yield
    vm_mod.reset_shared_research_observer()
    reset_shared_business_research()
    agent_decision_arbiter.get_decision_arbiter().reset()


def _make_fake_page() -> Any:
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.schedule_update = MagicMock()  # type: ignore[method-assign]
    page.pubsub = MagicMock()  # type: ignore[method-assign]
    page.pubsub.subscribe_topic = MagicMock()  # type: ignore[method-assign]
    page.pubsub.unsubscribe_topic = MagicMock()  # type: ignore[method-assign]
    return page


def _mount(
    agent_wiring: Any | None,
    *,
    service: _FakeService | None,
    mock_i18n_state: Any,
    mock_app_colors_state: Any,
    monkeypatch: Any,
) -> dict:
    from ui import app_layout as mod

    for view_name in [
        "HomeView",
        "ScreenerView",
        "BacktestView",
        "DataExplorerView",
        "TaskCenterView",
        "SettingsView",
        "WatchlistView",
    ]:
        monkeypatch.setattr(mod, view_name, MagicMock(return_value=MagicMock(name=view_name)))
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr(mod, "I18n", mock_i18n)
    monkeypatch.setattr(mod, "UILogger", MagicMock())

    component = make_component(mod.AppLayout, agent_wiring=agent_wiring)
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)
    return {
        "mod": mod,
        "component": component,
        "page": page,
        "result": result,
        "service": service,
    }


def _rerender(env: dict) -> Any:
    env["result"] = render_once(env["component"])
    return env["result"]


def _body_row(env: dict) -> ft.Row:
    column = env["result"].content
    assert isinstance(column, ft.Column)
    body_region = column.controls[0]
    assert isinstance(body_region, ft.Container)
    row = body_region.content
    assert isinstance(row, ft.Row)
    return row


def _nav_rail(env: dict) -> ft.NavigationRail:
    rail = _body_row(env).controls[0]
    assert isinstance(rail, ft.NavigationRail)
    return rail


def _brand_column(env: dict) -> ft.Column:
    leading = _nav_rail(env).leading
    assert isinstance(leading, ft.Container)
    column = leading.content
    assert isinstance(column, ft.Column)
    return column


def _research_button(env: dict) -> ft.IconButton:
    button = _brand_column(env).controls[3]
    assert isinstance(button, ft.IconButton)
    return button


def _comparison_button(env: dict) -> ft.IconButton:
    button = _brand_column(env).controls[4]
    assert isinstance(button, ft.IconButton)
    return button


def _workbench_button(env: dict) -> ft.IconButton:
    """W-18 研究工作台入口（位于对比入口之后，保持既有按钮索引不变）。"""

    button = _brand_column(env).controls[5]
    assert isinstance(button, ft.IconButton)
    return button


def _observation_control(env: dict) -> Any | None:
    """观察层在父 Row 中以子组件（Component）形式挂载；收起后不在 controls 内。"""

    tail = _body_row(env).controls[-1]
    return tail if type(tail).__name__ == "Component" else None


def _invoke(handler: Any, event: Any) -> None:
    """按 Flet 事件签名调用处理器（测试替身事件，避免声明式控件类型噪声）。"""

    assert handler is not None
    handler(event)


def _open_panel(env: dict) -> None:
    _invoke(_research_button(env).on_click, MagicMock())
    _rerender(env)


def _drive_last_run_task(env: dict) -> None:
    scheduled = env["page"].run_task.call_args.args[0]
    assert callable(scheduled), "调度项应为可执行协程函数"
    asyncio.run(scheduled())


def _start_research_handler(env: dict) -> Any:
    """业务页注入的准备回调（壳层下发给业务页的 ``on_start_research``）。

    页面栈为子组件（单测不展开渲染），准备入口以位置参数传入，与
    ``_build_pages_stack(..., on_start_research, mcp_lifecycle)`` 签名对应。
    """

    body_region = _body_row(env).controls[2]
    assert isinstance(body_region, ft.Container)
    stack: Any = body_region.content
    handler = stack.args[4] if stack.args else stack.kwargs.get("on_start_research")
    assert callable(handler), "壳层应把准备入口注入业务页"
    return handler


def _capture_prepare(env: dict) -> tuple[Any, tuple[Any, ...]]:
    """取出最近一次「准备草稿」调度（协程函数 + 参数），供手动驱动。"""

    call = env["page"].run_task.call_args
    scheduled = call.args[0]
    assert callable(scheduled), "调度项应为可执行协程函数"
    return scheduled, call.args[1:]


def _business_request() -> BusinessResearchRequest:
    return BusinessResearchRequest(
        source_module="news",
        object_kind="news_item",
        object_id="news-1",
        display_name="资讯 A",
        summary=(("news_id", "news-1"),),
        available_at="2026-11-18T01:00:00.000000Z",
    )


def _expected_prepared_session_id() -> str:
    """按服务层同一规则推导准备回执的精确会话标识（强断言用）。"""

    source = snapshot_source(_business_request().as_snapshot(), user_scope="local-desktop").source
    return stable_id("session", ["local-desktop", source.source_id, source.version])


def _shared_vm() -> Any:
    vm = vm_mod._shared_vm
    assert vm is not None
    return vm


def _iter_controls(root: Any) -> Iterator[Any]:
    if not isinstance(root, ft.Control):
        return
    yield root
    for attr in ("content", "controls", "leading", "trailing"):
        value = getattr(root, attr, None)
        if isinstance(value, ft.Control):
            yield from _iter_controls(value)
        elif isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, ft.Control):
                    yield from _iter_controls(item)


def _render_panel(
    state: ResearchObserverState,
    *,
    notice: str | None = None,
    monkeypatch: Any,
    callbacks: dict | None = None,
) -> ft.Container:
    from ui.views import (
        research_observer_artifacts as artifacts_mod,
        research_observer_composer as composer_mod,
        research_observer_conversation as conversation_mod,
        research_observer_decisions as decisions_mod,
        research_observer_panel as panel_mod,
        research_observer_session_list as sessions_mod,
        research_observer_widgets as widgets_mod,
    )

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    # 面板已拆分为多个子视图模块：替身需覆盖全部渲染模块，否则子视图取到真实文案。
    for module in (
        panel_mod,
        artifacts_mod,
        composer_mod,
        conversation_mod,
        decisions_mod,
        sessions_mod,
        widgets_mod,
    ):
        monkeypatch.setattr(module, "I18n", mock_i18n)
    cbs = callbacks or {}
    component = make_component(
        panel_mod.ResearchObserverPanel,
        state=state,
        notice=notice,
        on_close=cbs.get("on_close", lambda: None),
        on_refresh=cbs.get("on_refresh", lambda: None),
        on_draft_change=cbs.get("on_draft_change", lambda *_a: None),
        on_submit=cbs.get("on_submit", lambda *_a: None),
        on_toggle_execution=cbs.get("on_toggle_execution", lambda *_a: None),
        on_select_execution=cbs.get("on_select_execution", lambda *_a: None),
        on_load_artifact=cbs.get("on_load_artifact", lambda *_a: None),
        on_load_draft=cbs.get("on_load_draft", lambda *_a: None),
    )
    run_mount_effects(component)
    result = render_once(component)
    assert isinstance(result, ft.Container)
    return result


def _find_button(panel: ft.Container, label: str) -> ft.TextButton:
    for control in _iter_controls(panel):
        if isinstance(control, ft.TextButton):
            content = control.content
            if isinstance(content, ft.Text) and content.value == label:
                return control
    raise AssertionError(f"未找到按钮 {label}")


def _find_field(panel: ft.Container) -> ft.TextField:
    for control in _iter_controls(panel):
        if isinstance(control, ft.TextField) and str(control.key).startswith("agent_research_answer_"):
            return control
    raise AssertionError("未找到答复输入框")


def _find_icon_button(panel: ft.Container, tooltip: str) -> ft.IconButton:
    for control in _iter_controls(panel):
        if isinstance(control, ft.IconButton) and control.tooltip == tooltip:
            return control
    raise AssertionError(f"未找到图标按钮 {tooltip}")


def _assert_text_present(panel: ft.Container, value: str) -> None:
    for control in _iter_controls(panel):
        if isinstance(control, ft.Text) and control.value == value:
            return
    raise AssertionError(f"未找到文本 {value}")


@pytest.fixture
def layout_env(mock_i18n_state, mock_app_colors_state, monkeypatch) -> dict:
    service = _FakeService()
    return _mount(
        SimpleNamespace(service=service, ready=True),
        service=service,
        mock_i18n_state=mock_i18n_state,
        mock_app_colors_state=mock_app_colors_state,
        monkeypatch=monkeypatch,
    )


class TestShellResearchEntry:
    def test_seven_nav_items_unchanged(self, layout_env) -> None:
        assert len(_nav_rail(layout_env).destinations) == 7

    def test_research_button_opens_non_modal_observation(self, layout_env) -> None:
        assert _observation_control(layout_env) is None
        _open_panel(layout_env)
        assert _observation_control(layout_env) is not None, "展开后应挂非模态观察层"
        assert len(_nav_rail(layout_env).destinations) == 7, "不得新增第八导航"
        assert layout_env["page"].run_task.call_args.args[0] == _shared_vm().refresh, "展开应调度共享 VM 的只读刷新"

    def test_pending_count_indicator_after_refresh(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        column = layout_env["result"].content
        status_bar = column.controls[1]
        values = [getattr(c, "value", None) for c in _iter_controls(status_bar)]
        assert "agent_research_pending_fmt" in values

    def test_observation_surface_callbacks_are_read_only(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        assert type(panel).__name__ == "Component"
        vm = _shared_vm()

        panel.kwargs["on_toggle_execution"]("e-1")
        assert vm.state.collapsed_execution_ids == ("e-1",)

        panel.kwargs["on_select_execution"]("e-1")
        assert vm.state.selected_execution_id == "e-1"
        panel.kwargs["on_select_execution"](None)
        assert vm.state.selected_execution_id is None

        before = layout_env["page"].run_task.call_count
        panel.kwargs["on_load_artifact"]("a-1", 1, "e-1", "r-1")
        assert layout_env["page"].run_task.call_count == before + 1
        assert layout_env["page"].run_task.call_args.args[0] == vm.load_artifact
        assert layout_env["service"].answer_calls == [], "观察面入口不得触发决定提交"

    def test_draft_load_uses_card_bound_identity_not_current_target(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        vm = _shared_vm()
        vm._set_state(session_draft_revision=3)
        _rerender(layout_env)

        panel = _observation_control(layout_env)
        assert panel is not None
        # 卡片渲染后 VM 目标被另一卡片替换（尚未重渲染）：仍按被点卡片的旧身份读回
        vm._set_state(session_id="s-2", session_draft_revision=5)
        panel.kwargs["on_load_draft"]("s-1", 3)

        scheduled = layout_env["page"].run_task.call_args
        assert scheduled.args[0] == vm.load_draft
        assert scheduled.args[1:] == ("s-1", 3)

    def test_session_callbacks_route_to_vm_commands(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        vm = _shared_vm()
        page = layout_env["page"]

        before = page.run_task.call_count
        panel.kwargs["on_load_sessions"]()
        assert page.run_task.call_count == before + 1
        assert page.run_task.call_args.args[0] == vm.load_sessions
        _drive_last_run_task(layout_env)
        assert vm.state.sessions, "会话列表入口应把列表读进状态"

        panel.kwargs["on_new_session_title"]("新会话")
        assert vm.state.new_session_title == "新会话"
        panel.kwargs["on_begin_rename"](vm.state.sessions[0].session_id)
        panel.kwargs["on_rename_text"]("改名后的标题")
        assert vm.state.rename_text == "改名后的标题"
        panel.kwargs["on_cancel_rename"]()
        assert vm.state.rename_target_session_id is None

        for name, command, arg in (
            ("on_open_session", vm.open_session, "s-1"),
            ("on_create_session", vm.create_session, None),
            ("on_commit_rename", vm.commit_rename, None),
            ("on_pin_session", vm.pin_session, "s-1"),
            ("on_archive_session", vm.archive_session, "s-1"),
        ):
            before = page.run_task.call_count
            if arg is None:
                panel.kwargs[name]()
                expected = [command]
            else:
                panel.kwargs[name](arg)
                expected = [command, arg]
            assert page.run_task.call_count == before + 1
            assert list(page.run_task.call_args.args) == expected

        panel.kwargs["on_draft_change"]("d-1", "草稿")
        assert vm.state.draft_for("d-1") == "草稿"

        before = page.run_task.call_count
        panel.kwargs["on_retry_delivery"]("d-1")
        assert page.run_task.call_count == before + 1
        assert list(page.run_task.call_args.args) == [vm.retry_delivery, "d-1"]


class TestCollapseKeepsDraftAndExecution:
    def test_nav_click_collapses_without_clearing_draft(self, layout_env) -> None:
        _open_panel(layout_env)
        vm = _shared_vm()
        vm.set_answer_draft("d-1", "我的草稿")

        handler = _nav_rail(layout_env).on_change
        _invoke(handler, SimpleNamespace(control=SimpleNamespace(selected_index=2)))
        _rerender(layout_env)

        assert _observation_control(layout_env) is None, "业务导航只收起观察层"
        assert not vm.disposed, "收起观察层不得销毁共享 VM"
        assert vm.state.draft_for("d-1") == "我的草稿", "收起不得清稿"
        assert vm.state.submitting is False, "收起不得控制执行"
        assert layout_env["service"] is not None
        assert layout_env["service"].answer_calls == [], "收起不得触发任何决定/控制写调用"

    def test_same_tab_click_still_collapses(self, layout_env) -> None:
        _open_panel(layout_env)
        handler = _nav_rail(layout_env).on_change
        _invoke(handler, SimpleNamespace(control=SimpleNamespace(selected_index=0)))  # 当前即 MARKET
        _rerender(layout_env)
        assert _observation_control(layout_env) is None

    def test_ctrl_digit_collapses_observation(self, layout_env) -> None:
        _open_panel(layout_env)
        page = layout_env["page"]
        _invoke(
            page.on_keyboard_event,
            SimpleNamespace(ctrl=True, alt=False, meta=False, shift=False, key="3"),
        )
        _rerender(layout_env)
        assert _observation_control(layout_env) is None


class TestDraftReceiptRespectsNavigationIntent:
    """N08：建稿回执迟到时核对最新导航意图（已离开不重开、不改当前页）。

    观察层收起后，迟到回执不得据过期操作重新展开；仅在用户仍停留在发起页
    （期间无任何导航）时才展开并导航到回执给出的精确会话。
    """

    def test_late_receipt_after_leaving_does_not_reopen(self, layout_env) -> None:
        _start_research_handler(layout_env)(_business_request())
        scheduled, args = _capture_prepare(layout_env)

        # 用户在准备回执到达前导航离开（业务导航收起观察层，切页经防抖任务调度）
        _invoke(_nav_rail(layout_env).on_change, SimpleNamespace(control=SimpleNamespace(selected_index=2)))
        page = layout_env["page"]
        page.run_task.reset_mock()

        asyncio.run(scheduled(*args))
        _rerender(layout_env)

        assert _observation_control(layout_env) is None, "已离开发起页：迟到回执不得重开观察层"
        assert _shared_vm().state.session_id is None, "已离开时不得导航到回执会话"
        assert page.run_task.call_args_list == [], "迟到回执不得代用户切页/派发任务"

    def test_late_receipt_after_leaving_and_returning_does_not_reopen(self, layout_env) -> None:
        _start_research_handler(layout_env)(_business_request())
        scheduled, args = _capture_prepare(layout_env)

        rail = _nav_rail(layout_env)
        _invoke(rail.on_change, SimpleNamespace(control=SimpleNamespace(selected_index=2)))
        _invoke(rail.on_change, SimpleNamespace(control=SimpleNamespace(selected_index=0)))

        asyncio.run(scheduled(*args))
        _rerender(layout_env)

        assert _observation_control(layout_env) is None, "期间发生过导航即视为已离开，不重开"
        assert _shared_vm().state.session_id is None

    def test_receipt_while_still_on_origin_page_opens_panel(self, layout_env) -> None:
        _start_research_handler(layout_env)(_business_request())
        scheduled, args = _capture_prepare(layout_env)

        asyncio.run(scheduled(*args))
        _rerender(layout_env)

        panel = _observation_control(layout_env)
        assert panel is not None, "仍在原页：回执应更新原面板"
        assert panel.kwargs["notice"] == "agent_research_draft_prepared"
        assert _shared_vm().state.session_id == _expected_prepared_session_id()


class TestObservationPanelView:
    def _state(self, *, decisions: tuple[PendingDecision, ...]) -> ResearchObserverState:
        return ResearchObserverState(
            available=True,
            session_id="s-1",
            session_title="研究 A",
            executions=(("e-1", "running"),),
            decisions=decisions,
            pending_count=len(decisions),
        )

    def test_panel_header_shows_current_session_title(self, monkeypatch) -> None:
        panel = _render_panel(
            ResearchObserverState(
                available=True,
                session_id="s-1",
                session_title="研究 A",
                composer_available=True,
            ),
            monkeypatch=monkeypatch,
        )
        header = panel.content.controls[0]
        header_texts = [control.value for control in _iter_controls(header) if isinstance(control, ft.Text)]
        assert "agent_research_session_fmt" in header_texts, "当前会话标题必须在面板标题区渲染"

    def test_unavailable_state_is_honest(self, monkeypatch) -> None:
        panel = _render_panel(
            ResearchObserverState(available=False, unavailable_reason_key="agent_research_not_wired"),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "agent_research_not_wired")

    def test_answerable_decision_wires_explicit_callbacks(self, monkeypatch) -> None:
        drafted: list[tuple[str, str]] = []
        submitted: list[tuple[str, str, str]] = []
        decision = PendingDecision(
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
            decision_kind="user_choice",
            revision=2,
            created_at="2026-11-17T01:02:03.000000Z",
            expires_at=None,
            question_text="选哪个？",
        )
        panel = _render_panel(
            self._state(decisions=(decision,)),
            monkeypatch=monkeypatch,
            callbacks={
                "on_draft_change": lambda decision_id, text: drafted.append((decision_id, text)),
                "on_submit": lambda decision_id, execution_id, round_id: submitted.append(
                    (decision_id, execution_id, round_id)
                ),
            },
        )

        field = _find_field(panel)
        _invoke(field.on_change, SimpleNamespace(control=SimpleNamespace(value="选择第一家")))
        assert drafted == [("d-1", "选择第一家")]

        button = _find_button(panel, "agent_research_submit")
        _invoke(button.on_click, MagicMock())
        assert submitted == [("d-1", "e-1", "r-1")]

    def test_authorization_decision_has_no_submit_entry(self, monkeypatch) -> None:
        decision = PendingDecision(
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
            decision_kind="tool_grant",
            revision=2,
            created_at="2026-11-17T01:02:03.000000Z",
            expires_at=None,
            question_text=None,
        )
        panel = _render_panel(self._state(decisions=(decision,)), monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_decision_dedicated")
        for control in _iter_controls(panel):
            if isinstance(control, ft.TextButton):
                content = control.content
                assert not (isinstance(content, ft.Text) and content.value == "agent_research_submit")

    def test_decision_card_shows_expiry(self, monkeypatch) -> None:
        decision = PendingDecision(
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
            decision_kind="user_choice",
            revision=2,
            created_at="2026-11-17T01:02:03.000000Z",
            expires_at="2026-11-19T00:00:00.000000Z",
            question_text=None,
        )
        panel = _render_panel(self._state(decisions=(decision,)), monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_decision_expires_fmt")

    def test_loading_and_error_states_render_honestly(self, monkeypatch) -> None:
        state = ResearchObserverState(
            available=True,
            loading=True,
            error_key="agent_research_refresh_failed",
            last_error_code="upstream_timeout",
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)

        assert any(isinstance(control, ft.ProgressRing) for control in _iter_controls(panel))
        _assert_text_present(panel, "agent_research_refresh_failed")
        _assert_text_present(panel, "agent_research_error_fmt")

    def test_no_session_without_loading_shows_explicit_hint(self, monkeypatch) -> None:
        panel = _render_panel(
            ResearchObserverState(available=True, session_id=None, loading=False),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "agent_research_no_session")

    def test_empty_decisions_and_last_update_render(self, monkeypatch) -> None:
        state = ResearchObserverState(
            available=True,
            session_id="s-1",
            session_title="研究 A",
            decisions=(),
            last_updated_at="2026-11-18T01:00:00.000000Z",
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_no_decisions")
        _assert_text_present(panel, "agent_research_last_update_fmt")

    def test_notice_renders_above_body(self, monkeypatch) -> None:
        panel = _render_panel(
            ResearchObserverState(available=True, session_id=None),
            notice="决定提交失败，请重试",
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "决定提交失败，请重试")


class TestObservationSurfaceView:
    """R2-I03：主会话区 / 只读子任务树 / 成果读取入口（切页只改观察位置）。"""

    def _surface_state(self, **overrides: Any) -> ResearchObserverState:
        base: dict[str, Any] = {
            "available": True,
            "session_id": "s-1",
            "session_title": "研究 A",
            "executions": (("e-1", "running"),),
            "execution_nodes": (
                ExecutionNodeView(
                    execution_id="e-1",
                    parent_execution_id=None,
                    display_state="running",
                    goal_coverage="partial",
                    model_attempt_count=2,
                    tool_call_count=3,
                    active_ms=4200,
                    depth=0,
                    has_children=True,
                ),
            ),
            "messages": (
                SessionMessageView(
                    message_id="m-1",
                    role="assistant",
                    body_text="研究结论：候选 A",
                    body_available=True,
                    execution_id="e-1",
                    occurred_at="2026-11-17T01:30:00.000000Z",
                ),
                SessionMessageView(
                    message_id="m-2",
                    role="tool",
                    body_text=None,
                    body_available=False,
                    execution_id="e-1",
                    occurred_at="2026-11-17T01:31:00.000000Z",
                ),
            ),
            "artifacts": (
                SessionArtifactView(
                    artifact_id="a-1",
                    artifact_version=2,
                    artifact_kind="report",
                    execution_id="e-1",
                    round_id="r-1",
                    summary="候选 A 评分",
                    body_text=None,
                    body_available=False,
                    published_at="2026-11-17T01:40:00.000000Z",
                ),
            ),
        }
        base.update(overrides)
        return ResearchObserverState(**base)

    def test_messages_render_with_honest_body_marker(self, monkeypatch) -> None:
        panel = _render_panel(self._surface_state(), monkeypatch=monkeypatch)
        _assert_text_present(panel, "研究结论：候选 A")
        _assert_text_present(panel, "agent_research_message_body_unloaded")

    def test_execution_tree_renders_progress_and_toggle_callback(self, monkeypatch) -> None:
        toggled: list[str] = []
        panel = _render_panel(
            self._surface_state(),
            monkeypatch=monkeypatch,
            callbacks={"on_toggle_execution": lambda execution_id: toggled.append(execution_id)},
        )
        _assert_text_present(panel, "agent_research_execution_progress_fmt")
        button = _find_icon_button(panel, "agent_research_collapse")
        _invoke(button.on_click, MagicMock())
        assert toggled == ["e-1"]

    def test_select_execution_invokes_read_only_callback(self, monkeypatch) -> None:
        selected: list[str | None] = []
        panel = _render_panel(
            self._surface_state(),
            monkeypatch=monkeypatch,
            callbacks={"on_select_execution": lambda execution_id: selected.append(execution_id)},
        )
        button = _find_icon_button(panel, "agent_research_observe")
        _invoke(button.on_click, MagicMock())
        assert selected == ["e-1"]

    def test_selected_position_shows_all_button(self, monkeypatch) -> None:
        selected: list[str | None] = []
        panel = _render_panel(
            self._surface_state(selected_execution_id="e-1"),
            monkeypatch=monkeypatch,
            callbacks={"on_select_execution": lambda execution_id: selected.append(execution_id)},
        )
        button = _find_icon_button(panel, "agent_research_observe_all")
        _invoke(button.on_click, MagicMock())
        assert selected == [None]

    def test_artifact_load_button_forwards_exact_identity(self, monkeypatch) -> None:
        loaded: list[tuple[str, int, str, str]] = []
        panel = _render_panel(
            self._surface_state(),
            monkeypatch=monkeypatch,
            callbacks={
                "on_load_artifact": lambda artifact_id, version, execution_id, round_id: loaded.append(
                    (artifact_id, version, execution_id, round_id)
                )
            },
        )
        button = _find_icon_button(panel, "agent_research_artifact_load")
        _invoke(button.on_click, MagicMock())
        assert loaded == [("a-1", 2, "e-1", "r-1")]

    def test_loaded_artifact_body_and_unavailable_reason_render(self, monkeypatch) -> None:
        state = self._surface_state(
            artifact_bodies=(
                LoadedArtifactBody(
                    key="a-1:2",
                    body_text="完整成果正文",
                    body_available=True,
                    unavailable_reason=None,
                    truncated=False,
                ),
                LoadedArtifactBody(
                    key="a-2:1",
                    body_text=None,
                    body_available=False,
                    unavailable_reason="body_not_saved",
                    truncated=False,
                ),
            ),
            artifacts=(
                SessionArtifactView(
                    artifact_id="a-1",
                    artifact_version=2,
                    artifact_kind="report",
                    execution_id="e-1",
                    round_id="r-1",
                    summary="候选 A 评分",
                    body_text=None,
                    body_available=False,
                    published_at=None,
                ),
                SessionArtifactView(
                    artifact_id="a-2",
                    artifact_version=1,
                    artifact_kind="report",
                    execution_id="e-1",
                    round_id="r-1",
                    summary="候选 B 评分",
                    body_text=None,
                    body_available=False,
                    published_at=None,
                ),
            ),
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)
        _assert_text_present(panel, "完整成果正文")
        _assert_text_present(panel, "agent_research_artifact_body_not_saved")

    def test_inline_artifact_body_renders_without_load_entry(self, monkeypatch) -> None:
        state = self._surface_state(
            artifacts=(
                SessionArtifactView(
                    artifact_id="a-3",
                    artifact_version=1,
                    artifact_kind="report",
                    execution_id="e-1",
                    round_id="r-1",
                    summary="候选 C 评分",
                    body_text="首屏内联正文",
                    body_available=True,
                    published_at=None,
                ),
            )
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)
        _assert_text_present(panel, "首屏内联正文")
        tooltips = [control.tooltip for control in _iter_controls(panel) if isinstance(control, ft.IconButton)]
        assert "agent_research_artifact_load" not in tooltips

    def test_empty_artifacts_and_messages_state_is_explicit(self, monkeypatch) -> None:
        panel = _render_panel(
            self._surface_state(messages=(), artifacts=(), execution_nodes=()),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "agent_research_no_messages")
        _assert_text_present(panel, "agent_research_no_artifacts")
        _assert_text_present(panel, "agent_research_no_executions")


class TestDecisionSubmissionPipeline:
    def test_happy_path_runs_vm_then_releases_arbiter(self) -> None:
        page = _make_fake_page()
        vm = _FakeDecisionVm()

        refusal = agent_decision_arbiter.submit_decision(
            page=page,
            vm=vm,
            owner="research_observer",
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
        )

        assert refusal is None
        assert page.run_task.call_count == 1
        asyncio.run(page.run_task.call_args.args[0]())
        assert vm.submitted == [("d-1", "e-1", "r-1")]
        assert agent_decision_arbiter.get_decision_arbiter().owner is None

    def test_open_dialog_refuses_without_scheduling(self) -> None:
        page = _make_fake_page()
        page._dialogs.controls = [SimpleNamespace(open=True)]
        vm = _FakeDecisionVm()

        refusal = agent_decision_arbiter.submit_decision(
            page=page,
            vm=vm,
            owner="research_observer",
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
        )

        assert refusal == agent_decision_arbiter.REFUSAL_DIALOG_OPEN
        assert page.run_task.call_args_list == []
        assert vm.submitted == []

    def test_second_surface_is_rejected(self) -> None:
        page = _make_fake_page()
        vm = _FakeDecisionVm()
        arbiter = agent_decision_arbiter.get_decision_arbiter()
        assert (
            arbiter.begin_submit(
                "other_surface", "d-9", execution_id="e-9", round_id="r-9", has_open_modal_dialog=False
            )
            is None
        )

        refusal = agent_decision_arbiter.submit_decision(
            page=page,
            vm=vm,
            owner="research_observer",
            decision_id="d-1",
            execution_id="e-1",
            round_id="r-1",
        )

        assert refusal == agent_decision_arbiter.REFUSAL_OWNER_CONFLICT
        assert page.run_task.call_args_list == []
        arbiter.end_submit("other_surface")


class TestObservationPanelHonestBranches:
    """面板如实标注分支：未知角色 / 叶子执行节点 / 正文不可用 / 会话草稿卡。"""

    def _state(self, **overrides: Any) -> ResearchObserverState:
        base: dict[str, Any] = {"available": True, "session_id": "s-1", "session_title": "研究 A"}
        base.update(overrides)
        return ResearchObserverState(**base)

    def _artifact(self, **overrides: Any) -> SessionArtifactView:
        base: dict[str, Any] = {
            "artifact_id": "a-1",
            "artifact_version": 1,
            "artifact_kind": "report",
            "execution_id": "e-1",
            "round_id": "r-1",
            "summary": None,
            "body_text": None,
            "body_available": False,
            "published_at": None,
        }
        base.update(overrides)
        return SessionArtifactView(**base)

    def _draft(self, **overrides: Any) -> SessionDraftView:
        base: dict[str, Any] = {
            "session_id": "s-1",
            "draft_revision": 3,
            "text": "草稿正文",
            "mode": "discuss",
            "purpose": "concept",
            "research_date": "2026-11-13",
            "cutoff": "2026-11-14",
            "sources": ("business_snapshot:screener_candidate:600519.SH@abc123",),
            "skills": ("skill-a@1",),
            "mcp_services": ("market@2",),
            "customized_fields": ("mode",),
            "model_selection": "ms",
            "send_target": "st",
            "object_context": "oc",
            "role_prompt_selection": "rp",
            "is_empty": False,
            "cleaned": False,
            "archived": False,
            "transferred": False,
            "read_at": "2026-11-17T04:00:00.000000Z",
        }
        base.update(overrides)
        return SessionDraftView(**base)

    def _tooltips(self, panel: ft.Container) -> list[Any]:
        return [control.tooltip for control in _iter_controls(panel) if isinstance(control, ft.IconButton)]

    def _assert_text_absent(self, panel: ft.Container, value: str) -> None:
        values = [control.value for control in _iter_controls(panel) if isinstance(control, ft.Text)]
        assert value not in values, f"不应渲染文本 {value}"

    def test_unknown_role_message_is_labelled_not_invented(self, monkeypatch) -> None:
        message = SessionMessageView(
            message_id="m-1",
            role=None,
            body_text="会话级说明",
            body_available=True,
            execution_id=None,
            occurred_at="2026-11-17T01:30:00.000000Z",
        )
        panel = _render_panel(self._state(messages=(message,)), monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_role_unknown")
        _assert_text_present(panel, "会话级说明")

    def test_leaf_execution_uses_subdirectory_marker_without_toggle(self, monkeypatch) -> None:
        leaf = ExecutionNodeView(
            execution_id="e-2",
            parent_execution_id="e-1",
            display_state="complete",
            goal_coverage="full",
            model_attempt_count=1,
            tool_call_count=0,
            active_ms=10,
            depth=1,
            has_children=False,
        )
        panel = _render_panel(self._state(execution_nodes=(leaf,)), monkeypatch=monkeypatch)

        icons = [control.icon for control in _iter_controls(panel) if isinstance(control, ft.Icon)]
        assert ft.Icons.SUBDIRECTORY_ARROW_RIGHT in icons, "叶子节点应使用子目录标记而非展开按钮"
        tooltips = self._tooltips(panel)
        assert "agent_research_collapse" not in tooltips
        assert "agent_research_expand" not in tooltips
        _assert_text_present(panel, "agent_research_execution_progress_fmt")

    def test_unavailable_body_without_reason_states_body_unloaded(self, monkeypatch) -> None:
        state = self._state(
            artifacts=(self._artifact(),),
            artifact_bodies=(
                LoadedArtifactBody(
                    key="a-1:1",
                    body_text=None,
                    body_available=False,
                    unavailable_reason=None,
                    truncated=False,
                ),
            ),
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_artifact_body_unloaded")

    def test_unknown_unavailable_reason_is_shown_as_raw_code(self, monkeypatch) -> None:
        state = self._state(
            artifacts=(self._artifact(),),
            artifact_bodies=(
                LoadedArtifactBody(
                    key="a-1:1",
                    body_text=None,
                    body_available=False,
                    unavailable_reason="body_timeout",
                    truncated=False,
                ),
            ),
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)
        _assert_text_present(panel, "agent_research_artifact_unavailable_fmt")

    def test_loaded_body_truncated_marker_without_text_row(self, monkeypatch) -> None:
        state = self._state(
            artifacts=(self._artifact(),),
            artifact_bodies=(
                LoadedArtifactBody(
                    key="a-1:1",
                    body_text=None,
                    body_available=True,
                    unavailable_reason=None,
                    truncated=True,
                ),
            ),
        )
        panel = _render_panel(state, monkeypatch=monkeypatch)

        _assert_text_present(panel, "agent_research_artifact_truncated")
        # 已载入（即使正文为空串/None）不再给「读取正文」入口
        assert "agent_research_artifact_load" not in self._tooltips(panel)

    def test_artifact_body_pending_shows_progress_not_load_entry(self, monkeypatch) -> None:
        state = self._state(artifacts=(self._artifact(),), artifact_body_pending_keys=("a-1:1",))
        panel = _render_panel(state, monkeypatch=monkeypatch)

        assert any(isinstance(control, ft.ProgressRing) for control in _iter_controls(panel))
        assert "agent_research_artifact_load" not in self._tooltips(panel)

    def test_session_draft_pending_card_shows_progress_and_revision(self, monkeypatch) -> None:
        panel = _render_panel(
            self._state(session_draft_revision=3, draft_pending=True),
            monkeypatch=monkeypatch,
        )

        _assert_text_present(panel, "agent_research_draft_revision_fmt")
        _assert_text_present(panel, "agent_research_draft_loading")
        assert any(isinstance(control, ft.ProgressRing) for control in _iter_controls(panel))
        assert "agent_research_draft_load" not in self._tooltips(panel)

    def test_session_draft_load_button_binds_card_identity(self, monkeypatch) -> None:
        loaded: list[tuple[str, int]] = []
        panel = _render_panel(
            self._state(session_draft_revision=3),
            monkeypatch=monkeypatch,
            callbacks={"on_load_draft": lambda session_id, revision: loaded.append((session_id, revision))},
        )

        button = _find_icon_button(panel, "agent_research_draft_load")
        _invoke(button.on_click, MagicMock())
        # 读回必须绑定本卡渲染时的会话/稿版，而非点击时 VM 的当前目标（N16）
        assert loaded == [("s-1", 3)]
        # 尚未读回载荷：不渲染草稿详情（不伪造空正文）
        self._assert_text_absent(panel, "agent_research_draft_none")

    def test_session_draft_error_code_is_shown(self, monkeypatch) -> None:
        panel = _render_panel(
            self._state(session_draft_revision=3, draft_error_code="REVISION_CONFLICT"),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "agent_research_draft_error_fmt")

    def test_draft_detail_rows_render_body_selection_and_sources(self, monkeypatch) -> None:
        draft = self._draft(archived=True, is_empty=True)
        panel = _render_panel(
            self._state(session_draft_revision=3, session_draft=draft),
            monkeypatch=monkeypatch,
        )

        _assert_text_present(panel, "草稿正文")
        _assert_text_present(panel, "agent_research_draft_archived")
        _assert_text_present(panel, "agent_research_draft_empty")
        _assert_text_present(panel, "agent_research_draft_meta_fmt")
        _assert_text_present(panel, "agent_research_draft_sources_fmt")
        _assert_text_present(panel, "agent_research_draft_skills_fmt")
        _assert_text_present(panel, "agent_research_draft_mcp_fmt")
        _assert_text_present(panel, "agent_research_draft_customized_fmt")
        _assert_text_present(panel, "agent_research_draft_model_selection_fmt")
        _assert_text_present(panel, "agent_research_draft_send_target_fmt")
        _assert_text_present(panel, "agent_research_draft_object_context_fmt")
        _assert_text_present(panel, "agent_research_draft_role_prompt_fmt")

        # 非空稿不显示「空稿」提示（is_empty=False 分支）
        non_empty = _render_panel(
            self._state(session_draft_revision=3, session_draft=self._draft(text="正文")),
            monkeypatch=monkeypatch,
        )
        self._assert_text_absent(non_empty, "agent_research_draft_empty")

    def test_blank_draft_body_and_absent_selection_are_not_rendered(self, monkeypatch) -> None:
        draft = self._draft(
            text="",
            is_empty=True,
            sources=(),
            skills=(),
            mcp_services=(),
            customized_fields=(),
            model_selection=None,
            send_target=None,
            object_context=None,
            role_prompt_selection=None,
        )
        panel = _render_panel(
            self._state(session_draft_revision=3, session_draft=draft),
            monkeypatch=monkeypatch,
        )

        _assert_text_present(panel, "agent_research_draft_body_empty")
        for absent in (
            "agent_research_draft_sources_fmt",
            "agent_research_draft_skills_fmt",
            "agent_research_draft_mcp_fmt",
            "agent_research_draft_customized_fmt",
            "agent_research_draft_model_selection_fmt",
            "agent_research_draft_send_target_fmt",
            "agent_research_draft_object_context_fmt",
            "agent_research_draft_role_prompt_fmt",
        ):
            self._assert_text_absent(panel, absent)

    def test_cleaned_transferred_and_missing_draft_are_distinguished(self, monkeypatch) -> None:
        cleaned = _render_panel(
            self._state(session_draft_revision=3, session_draft=self._draft(text=None, cleaned=True)),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(cleaned, "agent_research_draft_cleaned")

        transferred = _render_panel(
            self._state(session_draft_revision=3, session_draft=self._draft(text=None, transferred=True)),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(transferred, "agent_research_draft_transferred")

        missing = _render_panel(
            self._state(session_draft_revision=3, session_draft=self._draft(text=None)),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(missing, "agent_research_draft_none")

    def test_more_items_marker_rendered_for_messages_and_artifacts(self, monkeypatch) -> None:
        panel = _render_panel(
            self._state(messages_has_more=True, artifacts_has_more=True),
            monkeypatch=monkeypatch,
        )
        markers = [
            control.value
            for control in _iter_controls(panel)
            if isinstance(control, ft.Text) and control.value == "agent_research_more_items"
        ]
        assert len(markers) == 2, "消息与成果各有一条「更多」提示"


def _pending_decision(decision_id: str, *, question: str | None = "选哪个？") -> PendingDecision:
    """面板渲染用决定投影（user_choice，可答复）。"""

    return PendingDecision(
        decision_id=decision_id,
        execution_id="e-1",
        round_id="r-1",
        decision_kind="user_choice",
        revision=2,
        created_at="2026-11-17T01:02:03.000000Z",
        expires_at=None,
        question_text=question,
    )


def _answer_fields(panel: ft.Container) -> dict[str, ft.TextField]:
    """按 key 索引答复输入框（验证输入态绑定身份而非列表位置）。"""

    fields: dict[str, ft.TextField] = {}
    for control in _iter_controls(panel):
        if (
            isinstance(control, ft.TextField)
            and control.key is not None
            and str(control.key).startswith("agent_research_answer_")
        ):
            fields[str(control.key)] = control
    return fields


class TestDesktopInputEvidence:
    """I88：生产桌面输入的组件级替代证据（无 GUI 环境下不得伪称 GUI 实测）。

    覆盖返焦、IME/CJK 草稿往返、Enter/Esc 不批准不关闭、输入框身份绑定决定。
    """

    def test_close_returns_focus_to_research_entry(self, layout_env) -> None:
        _open_panel(layout_env)
        page = layout_env["page"]
        toggle = _research_button(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        page.run_task.reset_mock()

        panel.kwargs["on_close"]()

        _rerender(layout_env)
        assert _observation_control(layout_env) is None, "关闭应卸载观察层"
        assert page.run_task.call_count == 1
        assert page.run_task.call_args.args[0] == toggle.focus, "关闭后焦点应回到观察入口（返焦）"

    def test_nav_collapse_leaves_focus_with_navigation(self, layout_env) -> None:
        _open_panel(layout_env)
        page = layout_env["page"]
        toggle = _research_button(layout_env)
        page.run_task.reset_mock()

        _invoke(_nav_rail(layout_env).on_change, SimpleNamespace(control=SimpleNamespace(selected_index=2)))

        scheduled = [call.args[0] for call in page.run_task.call_args_list if call.args]
        assert toggle.focus not in scheduled, "导航收起不返焦（焦点应留给导航目标）"
        # P1-05：收起时顺带一次只读刷新（让其他会话的待办进入徽标），
        # 除刷新外只调度切页，不附加任何焦点动作。
        assert [getattr(item, "__name__", "") for item in scheduled] == ["refresh", "_do_tab_switch"]

    def test_ime_composition_draft_round_trips_after_collapse(self, layout_env, monkeypatch) -> None:
        _open_panel(layout_env)
        vm = _shared_vm()
        vm.set_answer_draft("d-1", "选中方案")  # IME 组合串上屏后的 CJK 文本

        _invoke(_nav_rail(layout_env).on_change, SimpleNamespace(control=SimpleNamespace(selected_index=1)))
        _rerender(layout_env)
        assert _observation_control(layout_env) is None, "导航收起观察层"

        _open_panel(layout_env)
        _drive_last_run_task(layout_env)  # 重开后的只读刷新：决定列表重新载入
        _rerender(layout_env)
        reopened = _observation_control(layout_env)
        assert reopened is not None
        assert reopened.kwargs["state"].draft_for("d-1") == "选中方案", "收起/重开后草稿保真"
        rendered = _render_panel(reopened.kwargs["state"], monkeypatch=monkeypatch)
        assert _find_field(rendered).value == "选中方案", "输入框按状态快照回填草稿"
        assert layout_env["service"].answer_calls == [], "收起/重开不得触发答复提交"

    def test_enter_and_escape_do_not_submit_or_close(self, layout_env) -> None:
        _open_panel(layout_env)
        vm = _shared_vm()
        vm.set_answer_draft("d-1", "选中方案")
        page = layout_env["page"]
        page.run_task.reset_mock()

        for key, ctrl in (("Enter", False), ("Enter", True), ("Escape", False), ("Escape", True)):
            _invoke(
                page.on_keyboard_event,
                SimpleNamespace(ctrl=ctrl, alt=False, meta=False, shift=False, key=key),
            )

        _rerender(layout_env)
        assert _observation_control(layout_env) is not None, "Enter/Esc 不得关闭观察层"
        assert page.run_task.call_args_list == [], "Enter/Esc 不得派发切页"
        assert layout_env["service"].answer_calls == [], "键盘（含 IME 上屏 Enter）不得提交决定"
        assert vm.state.draft_for("d-1") == "选中方案", "键盘事件不得改动草稿"
        assert vm.state.submitting is False

    def test_answer_field_binds_decision_identity_across_reorder(self, monkeypatch) -> None:
        d1, d2 = _pending_decision("d-1"), _pending_decision("d-2")
        base = ResearchObserverState(
            available=True,
            session_id="s-1",
            session_title="研究 A",
            decisions=(d1, d2),
            pending_count=2,
            answer_drafts=(("d-1", "甲方案"), ("d-2", "乙方案")),
        )
        expected = {"agent_research_answer_d-1": "甲方案", "agent_research_answer_d-2": "乙方案"}

        first = _answer_fields(_render_panel(base, monkeypatch=monkeypatch))
        assert {key: field.value for key, field in first.items()} == expected

        reordered = _answer_fields(_render_panel(replace(base, decisions=(d2, d1)), monkeypatch=monkeypatch))
        assert {key: field.value for key, field in reordered.items()} == expected, (
            "决定重排后输入态仍按决定身份绑定，不按列表位置（避免 IME 组合串串卡）"
        )


class TestDesktopInputLatencyBaseline:
    """输入路径 P95 基准（固定负载、无 GUI 依赖的替代证据）。

    xdist 并行下计时不可横向比较（须 ``-n0`` 复测）；阈值取宽松冒烟界，
    不是生产 SLA。失败消息完整输出 p50/p95/max 与全部样本（保留失败样本）。
    """

    _WARMUP = 3
    _SAMPLES = 40
    _VM_P95_LIMIT_MS = 50.0  # 与 PerfThreshold.MEMORY_COMPUTE 同量级（单次内存命令）
    _PANEL_RENDER_P95_LIMIT_MS = 500.0

    @classmethod
    def _samples_ms(cls, func: Any) -> list[float]:
        for _ in range(cls._WARMUP):
            func()
        samples: list[float] = []
        for _ in range(cls._SAMPLES):
            started = time.perf_counter()
            func()
            samples.append((time.perf_counter() - started) * 1000.0)
        return samples

    @staticmethod
    def _assert_p95(label: str, samples_ms: list[float], limit_ms: float) -> None:
        from scripts.probe.news_match_perf import percentile

        p50 = percentile(samples_ms, 0.50)
        p95 = percentile(samples_ms, 0.95)
        detail = (
            f"{label}: p50={p50:.3f}ms p95={p95:.3f}ms max={max(samples_ms):.3f}ms "
            f"limit={limit_ms:.0f}ms samples_ms={[round(v, 3) for v in samples_ms]}"
        )
        assert p95 <= limit_ms, detail

    def test_answer_draft_update_p95(self, layout_env) -> None:
        _open_panel(layout_env)
        vm = _shared_vm()
        counter = iter(range(1_000_000))

        samples = self._samples_ms(lambda: vm.set_answer_draft("d-1", f"草稿{next(counter)}"))
        self._assert_p95("set_answer_draft", samples, self._VM_P95_LIMIT_MS)

    def test_panel_render_p95(self, layout_env, monkeypatch) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)  # 刷新出可答复决定，覆盖输入框渲染路径
        _rerender(layout_env)
        vm = _shared_vm()
        vm.set_answer_draft("d-1", "选中方案")
        state = vm.state

        samples = self._samples_ms(lambda: _render_panel(state, monkeypatch=monkeypatch))
        self._assert_p95("ResearchObserverPanel render", samples, self._PANEL_RENDER_P95_LIMIT_MS)


def test_shortcut_key_normalization_tolerates_blank_and_variants() -> None:
    from ui.app_layout import _normalize_shortcut_key

    assert _normalize_shortcut_key(None) == ""
    assert _normalize_shortcut_key("   ") == "", "空白按键标签应归一化为空（不匹配任何快捷键）"
    assert _normalize_shortcut_key("Numpad3") == "3"
    assert _normalize_shortcut_key("KeyA") == "A"


class TestW12ToolbarRouting:
    """W-12：模型/角色、费用与耗时工具栏在壳层的调度接线（只读命令）。"""

    def test_toolbar_callbacks_schedule_vm_commands(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        vm = _shared_vm()
        page = layout_env["page"]

        for name, command in (
            ("on_load_mcp_scope", vm.load_mcp_scope),
            ("on_load_model_selection", vm.load_model_selection),
            ("on_refresh_usage", vm.refresh_usage),
            ("on_refresh_budget", vm.refresh_budget_hint),
        ):
            page.run_task.reset_mock()
            panel.kwargs[name]()
            assert page.run_task.call_args.args[0] == command

        page.run_task.reset_mock()
        panel.kwargs["on_select_model"]("conn-a")
        assert list(page.run_task.call_args.args) == [vm.select_model_connection, "conn-a"]

        page.run_task.reset_mock()
        panel.kwargs["on_select_model"]("")
        assert page.run_task.call_args_list == [], "空连接不得调度选择命令"

        # 角色目录尚未加载：未知角色如实拒绝，不静默换成默认角色
        panel.kwargs["on_select_role"]("main")
        assert vm.state.role_selection_error_key == "agent_research_role_selection_unknown"


class TestDraftComposerAndDecisionHandlers:
    """壳层草稿/首问/计划/决定入口：异步调度到 VM，不在事件处理器内同步 IO。"""

    def test_draft_and_composer_handlers_use_current_session(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        vm = _shared_vm()
        page = layout_env["page"]
        assert vm.state.session_id == "s-1", "刷新后应已选中会话"
        vm._set_state(composer_available=True)

        page.run_task.reset_mock()
        panel.kwargs["on_load_draft"]("s-1", 3)
        assert list(page.run_task.call_args.args) == [vm.load_draft, "s-1", 3]

        page.run_task.reset_mock()
        panel.kwargs["on_send"]()
        assert page.run_task.call_args.args[0] == vm.send_first_question

        page.run_task.reset_mock()
        panel.kwargs["on_start_plan"]()
        assert page.run_task.call_args.args[0] == vm.start_confirmed_plan

    def test_decision_submit_routes_through_arbiter(self, layout_env) -> None:
        _open_panel(layout_env)
        _drive_last_run_task(layout_env)
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        vm = _shared_vm()
        page = layout_env["page"]

        vm.set_answer_draft("d-1", "选择第一家")
        page.run_task.reset_mock()
        panel.kwargs["on_submit"]("d-1", "e-1", "r-1")
        assert page.run_task.call_count == 1, "提交必须经仲裁层调度，不就地执行"
        asyncio.run(page.run_task.call_args.args[0]())
        assert [request.decision_id for request in layout_env["service"].answer_calls] == ["d-1"]


class TestComparisonPanelWiring:
    """F37：对比面板在壳层的挂载与异步命令调度（独立面板，不影响观察层）。"""

    def test_comparison_toggle_mounts_panel_and_schedules_loads(self, layout_env) -> None:
        page = layout_env["page"]
        page.run_task.reset_mock()
        _invoke(_comparison_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)

        tail = _body_row(layout_env).controls[-1]
        assert type(tail).__name__ == "Component", "展开后应挂对比面板"
        comparison_vm = tail.kwargs["on_lane"].__self__
        assert [call.args[0] for call in page.run_task.call_args_list] == [comparison_vm.load_connections]

        page.run_task.reset_mock()
        tail.kwargs["on_refresh"]()
        assert page.run_task.call_args.args[0] == comparison_vm.refresh

        # 收起后不再挂载
        _invoke(_comparison_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)
        assert type(_body_row(layout_env).controls[-1]).__name__ != "Component"

        # 已有组身份时，重新展开同时刷新组投影
        comparison_vm._set_state(group_ref="g-1")
        _rerender(layout_env)
        page.run_task.reset_mock()
        _invoke(_comparison_button(layout_env).on_click, MagicMock())
        scheduled = [getattr(call.args[0], "__name__", "") for call in page.run_task.call_args_list]
        assert scheduled == ["load_connections", "refresh"]


class TestWorkbenchPanelWiring:
    """W-18：四视角研究工作台在壳层的挂载（只读投影，不派发研究）。"""

    def test_workbench_toggle_mounts_panel_and_keeps_drafts(self, layout_env) -> None:
        from ui.viewmodels.research_workbench_view_model import ResearchWorkbenchViewModel

        page = layout_env["page"]
        page.run_task.reset_mock()

        _invoke(_workbench_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)

        tail = _body_row(layout_env).controls[-1]
        assert type(tail).__name__ == "Component", "展开后应挂研究工作台"
        assert isinstance(tail.kwargs["on_perspective"].__self__, ResearchWorkbenchViewModel)
        assert tail.kwargs["workbench_state"].perspective == "overview"
        assert page.run_task.call_args_list == [], "工作台展开不得调度异步命令"

        # 视角切换只换渲染分支，草稿与选中由 VM 持有
        tail.kwargs["on_perspective"]("evidence")
        _rerender(layout_env)
        tail = _body_row(layout_env).controls[-1]
        assert tail.kwargs["workbench_state"].perspective == "evidence"

        # 「＋指令与引用」菜单回调写入查询草稿
        tail.kwargs["on_command"]()
        tail.kwargs["on_skill"]()
        _rerender(layout_env)
        tail = _body_row(layout_env).controls[-1]
        assert tail.kwargs["workbench_state"].overview_query == "/ @skill"

        # 收起后不再挂载
        tail.kwargs["on_close"]()
        _rerender(layout_env)
        assert type(_body_row(layout_env).controls[-1]).__name__ != "Component"


class TestHandlersWithoutPage:
    """无渲染上下文（page 不可得）时所有入口安全空转，不派发、不抛错。"""

    def test_handlers_are_noops_without_page(self, monkeypatch, layout_env) -> None:
        from ui import app_layout as mod

        _open_panel(layout_env)
        panel = _observation_control(layout_env)
        assert panel is not None
        assert panel.kwargs["state"].available is True, "已接线时观察层应可用"
        page = layout_env["page"]
        page.run_task.reset_mock()
        monkeypatch.setattr(mod, "_get_page", lambda: None)

        panel.kwargs["on_close"]()
        for name, args in (
            ("on_load_mcp_scope", ()),
            ("on_load_model_selection", ()),
            ("on_refresh_usage", ()),
            ("on_refresh_budget", ()),
            ("on_select_model", ("conn-a",)),
            ("on_load_sessions", ()),
            ("on_open_session", ("s-1",)),
            ("on_create_session", ()),
            ("on_commit_rename", ()),
            ("on_pin_session", ("s-1",)),
            ("on_archive_session", ("s-1",)),
            ("on_load_artifact", ("a-1", 1, "e-1", "r-1")),
            ("on_load_draft", ("s-1", 3)),
            ("on_send", ()),
            ("on_start_plan", ()),
            ("on_submit", ("d-1", "e-1", "r-1")),
            ("on_retry_delivery", ("d-1",)),
        ):
            panel.kwargs[name](*args)
        _invoke(page.on_keyboard_event, SimpleNamespace(ctrl=True, alt=False, meta=False, shift=False, key="3"))
        assert page.run_task.call_args_list == []


class TestPrepareResearchEarlyReturns:
    """建稿回执的早退分支：无提示 / 非建稿成功提示都不重开、不导航。"""

    def _business_vm(self) -> Any:
        from ui.viewmodels import business_research_view_model as business_mod

        vm = business_mod._shared_vm
        assert vm is not None
        return vm

    def test_missing_notice_does_not_open_panel(self, monkeypatch, layout_env) -> None:
        vm = self._business_vm()

        async def _silent_prepare(*_args: Any, **_kwargs: Any) -> None:
            return

        monkeypatch.setattr(vm, "prepare", _silent_prepare)
        _start_research_handler(layout_env)(_business_request())
        scheduled, args = _capture_prepare(layout_env)
        layout_env["page"].run_task.reset_mock()

        asyncio.run(scheduled(*args))
        _rerender(layout_env)

        assert _observation_control(layout_env) is None
        assert layout_env["page"].run_task.call_args_list == []

    def test_non_prepared_notice_does_not_open_panel(self, monkeypatch, layout_env) -> None:
        vm = self._business_vm()

        async def _source_unavailable(*_args: Any, **_kwargs: Any) -> None:
            vm._set_state(notice_key="agent_research_source_unavailable", last_session_id="s-9")

        monkeypatch.setattr(vm, "prepare", _source_unavailable)
        _start_research_handler(layout_env)(_business_request())
        scheduled, args = _capture_prepare(layout_env)
        layout_env["page"].run_task.reset_mock()

        asyncio.run(scheduled(*args))
        _rerender(layout_env)

        assert _observation_control(layout_env) is None, "非建稿成功提示不得重开观察层"
        assert _shared_vm().state.session_id is None, "不得导航到非建稿回执的会话"
        assert layout_env["page"].run_task.call_args_list == []


class TestShellLifecycleSafety:
    """快捷键聚焦与卸载清理：清理幂等，卸载后残留订阅回调安全忽略。"""

    def test_focus_shortcut_and_cleanup_are_idempotent(self, layout_env) -> None:
        from ui.pubsub_topics import TOPIC_NAVIGATE

        page = layout_env["page"]
        page.run_task.reset_mock()
        _invoke(page.on_keyboard_event, SimpleNamespace(ctrl=True, alt=False, meta=False, shift=False, key="f"))
        assert page.run_task.call_count == 1, "Ctrl+F 应把焦点还给导航栏切换按钮"

        run_unmount_effects(layout_env["component"])

        handler = page.pubsub.subscribe_topic.call_args.args[1]
        handler(TOPIC_NAVIGATE, "market")  # 订阅句柄已清空：安全忽略迟到消息

    def test_mount_without_page_cleans_up_without_side_effects(
        self, mock_i18n_state, mock_app_colors_state, monkeypatch
    ) -> None:
        from ui import app_layout as mod

        monkeypatch.setattr(mod, "_get_page", lambda: None)
        egress_audit = MagicMock()
        egress_audit.return_value.add_listener.return_value = None
        monkeypatch.setattr("utils.egress_audit.EgressAudit", egress_audit)
        env = _mount(
            None,
            service=None,
            mock_i18n_state=mock_i18n_state,
            mock_app_colors_state=mock_app_colors_state,
            monkeypatch=monkeypatch,
        )

        _invoke(_research_button(env).on_click, MagicMock())  # 无 page：只切可见性，不调度刷新
        _rerender(env)
        assert env["page"].run_task.call_args_list == []

        run_unmount_effects(env["component"])  # 清理阶段无 page / 无退订句柄：安全空转


def _open_panel_with_session(env: dict) -> Any:
    """展开观察层并驱动首轮只读刷新（会话/执行节点进入 state）。"""

    _open_panel(env)
    _drive_last_run_task(env)
    _rerender(env)
    panel = _observation_control(env)
    assert panel is not None
    return panel


class TestShellCallbackWiring:
    """壳层语义入口接线（W-01 执行控制 / 只读读取 / 对比面板 / 业务准备）。

    这些回调都是壳层闭包：验证它们只调度共享 VM 的精确命令（不直连服务）、
    未知输入不猜命令、失败/无提示时不伪装成功。
    """

    def test_control_callbacks_route_to_vm_commands(self, layout_env) -> None:
        panel = _open_panel_with_session(layout_env)
        vm = _shared_vm()
        page = layout_env["page"]

        for kind, command in (
            ("pause", vm.pause_execution),
            ("resume", vm.resume_execution),
            ("withdraw_queued", vm.withdraw_execution),
        ):
            page.run_task.reset_mock()
            panel.kwargs["on_control_execution"](kind, "e-1")
            assert list(page.run_task.call_args.args) == [command, "e-1"]

        page.run_task.reset_mock()
        panel.kwargs["on_control_execution"]("unknown_kind", "e-1")
        assert page.run_task.call_args_list == [], "未知控制类型不派发（不猜命令）"

        page.run_task.reset_mock()
        panel.kwargs["on_retry_execution"]("e-1", "n-1")
        assert list(page.run_task.call_args.args) == [vm.retry_execution, "e-1", "n-1"]

        panel.kwargs["on_prompt_control_reason"]("e-1", "terminate")
        assert vm.state.control_reason_execution_id == "e-1"
        assert vm.state.control_reason_kind == "terminate"

        panel.kwargs["on_control_reason_change"]("用户终止：不再需要该路径")
        assert vm.state.control_reason_text == "用户终止：不再需要该路径"

        page.run_task.reset_mock()
        panel.kwargs["on_control_reason_submit"]()
        assert page.run_task.call_args.args[0] == vm.submit_control_reason

        panel.kwargs["on_control_reason_cancel"]()
        assert vm.state.control_reason_execution_id is None

    def test_control_commands_without_page_context_are_noop(self, layout_env) -> None:
        from flet.controls.context import _context_page

        panel = _open_panel_with_session(layout_env)
        page = layout_env["page"]
        page.run_task.reset_mock()

        token = _context_page.set(cast(Any, None))
        try:
            assert panel.kwargs["on_control_execution"]("pause", "e-1") is None
            assert panel.kwargs["on_retry_execution"]("e-1", "n-1") is None
        finally:
            _context_page.reset(token)

        assert page.run_task.call_args_list == [], "无 page 上下文时不得调度命令"

    def test_read_only_entry_points_schedule_vm_commands(self, layout_env) -> None:
        panel = _open_panel_with_session(layout_env)
        vm = _shared_vm()
        page = layout_env["page"]
        assert vm.state.session_id is not None

        page.run_task.reset_mock()
        panel.kwargs["on_load_mcp_scope"]()
        assert list(page.run_task.call_args.args) == [vm.load_mcp_scope]

        page.run_task.reset_mock()
        panel.kwargs["on_load_draft"](vm.state.session_id, 3)
        assert list(page.run_task.call_args.args) == [vm.load_draft, vm.state.session_id, 3]

        page.run_task.reset_mock()
        panel.kwargs["on_retry_delivery"]("d-1")
        assert list(page.run_task.call_args.args) == [vm.retry_delivery, "d-1"]

    def test_composer_callbacks_schedule_with_intake(self, mock_i18n_state, mock_app_colors_state, monkeypatch) -> None:
        service = _FakeService()
        intake = SimpleNamespace(start_question=MagicMock(), start_plan=MagicMock())
        env = _mount(
            SimpleNamespace(service=service, ready=True, intake=intake),
            service=service,
            mock_i18n_state=mock_i18n_state,
            mock_app_colors_state=mock_app_colors_state,
            monkeypatch=monkeypatch,
        )
        panel = _open_panel_with_session(env)
        vm = _shared_vm()
        page = env["page"]
        assert vm.state.composer_available is True

        page.run_task.reset_mock()
        panel.kwargs["on_send"]()
        assert page.run_task.call_args.args[0] == vm.send_first_question

        page.run_task.reset_mock()
        panel.kwargs["on_start_plan"]()
        assert page.run_task.call_args.args[0] == vm.start_confirmed_plan

    def test_submit_decision_runs_arbitrated_answer(self, layout_env) -> None:
        panel = _open_panel_with_session(layout_env)
        vm = _shared_vm()
        service = layout_env["service"]
        vm.set_answer_draft("d-1", "选择第一家")

        panel.kwargs["on_submit"]("d-1", "e-1", "r-1")
        _drive_last_run_task(layout_env)

        assert [request.decision_id for request in service.answer_calls] == ["d-1"]
        assert vm.state.submitting is False
        # 回执未带命令身份：如实标未交付（R21），不伪装已投递
        assert vm.state.last_answer_delivery_state == "not_delivered"
        assert vm.state.last_answer_delivery_reason == "delivery_command_missing"

    def test_comparison_toggle_opens_panel_and_schedules_reads(self, layout_env) -> None:
        from ui.viewmodels.research_comparison_view_model import ResearchComparisonViewModel

        env = layout_env
        page = env["page"]
        toggle = _comparison_button(env)

        _invoke(toggle.on_click, MagicMock())
        _rerender(env)
        panel = cast(Any, _body_row(env).controls[-1])
        assert type(panel).__name__ == "Component", "展开后应挂对比面板"

        scheduled = page.run_task.call_args.args[0]
        assert isinstance(scheduled.__self__, ResearchComparisonViewModel)
        assert scheduled.__name__ == "load_connections"

        page.run_task.reset_mock()
        panel.kwargs["on_submit"]()
        scheduled = page.run_task.call_args.args[0]
        assert isinstance(scheduled.__self__, ResearchComparisonViewModel)
        assert scheduled.__name__ == "submit"

        _invoke(_comparison_button(env).on_click, MagicMock())
        _rerender(env)
        assert _body_row(env).controls[-1] is not panel, "再次点击应收起对比面板"

    def test_start_research_without_notice_keeps_panel_closed(
        self, mock_i18n_state, mock_app_colors_state, monkeypatch
    ) -> None:
        from ui import app_layout as mod

        prepared: list[tuple[Any, Any]] = []

        class _BusinessVm:
            state = SimpleNamespace(notice_key=None, last_session_id=None)

            def subscribe(self, _listener: Any) -> Any:
                return lambda: None

            def dispose(self) -> None:
                return None

            async def prepare(self, request: Any, *, backtest_reader: Any = None) -> None:
                prepared.append((request, backtest_reader))

        business_vm = _BusinessVm()
        monkeypatch.setattr(mod, "shared_business_research", lambda *a, **kw: business_vm)
        captured: dict[str, Any] = {}

        def _capture_pages(_tab, _settings, _screener, _backtest, start_research, *_rest):
            captured["start_research"] = start_research
            return ft.Row([])

        monkeypatch.setattr(mod, "_build_pages_stack", _capture_pages)
        service = _FakeService()
        env = _mount(
            SimpleNamespace(service=service, ready=True),
            service=service,
            mock_i18n_state=mock_i18n_state,
            mock_app_colors_state=mock_app_colors_state,
            monkeypatch=monkeypatch,
        )

        request = cast(Any, SimpleNamespace(kind="business"))
        captured["start_research"](request, None)
        call = env["page"].run_task.call_args
        asyncio.run(call.args[0](*call.args[1:]))

        assert prepared == [(request, None)]
        _rerender(env)
        assert _observation_control(env) is None, "无提示不自动展开观察层"

    def test_start_research_prepared_notice_opens_receipt_session(
        self, mock_i18n_state, mock_app_colors_state, monkeypatch
    ) -> None:
        from ui import app_layout as mod
        from ui.viewmodels.business_research_view_model import NOTICE_DRAFT_PREPARED

        class _BusinessVm:
            state = SimpleNamespace(notice_key=NOTICE_DRAFT_PREPARED, last_session_id="s-1")

            def subscribe(self, _listener: Any) -> Any:
                return lambda: None

            def dispose(self) -> None:
                return None

            async def prepare(self, request: Any, *, backtest_reader: Any = None) -> None:
                return None

        business_vm = _BusinessVm()
        monkeypatch.setattr(mod, "shared_business_research", lambda *a, **kw: business_vm)
        captured: dict[str, Any] = {}

        def _capture_pages(_tab, _settings, _screener, _backtest, start_research, *_rest):
            captured["start_research"] = start_research
            return ft.Row([])

        monkeypatch.setattr(mod, "_build_pages_stack", _capture_pages)
        service = _FakeService()
        env = _mount(
            SimpleNamespace(service=service, ready=True),
            service=service,
            mock_i18n_state=mock_i18n_state,
            mock_app_colors_state=mock_app_colors_state,
            monkeypatch=monkeypatch,
        )

        request = cast(Any, SimpleNamespace(kind="business"))
        captured["start_research"](request, None)
        call = env["page"].run_task.call_args
        asyncio.run(call.args[0](*call.args[1:]))
        _rerender(env)

        assert type(_observation_control(env)).__name__ == "Component", "草稿已准备时展开观察层"
        assert service.read_calls[-1][0].session_id == "s-1", "按回执会话精确导航，不取历史首条"


class TestHistoryEntry:
    """W-04：历史面板入口（目录/检索/清理/停止使用）可展开收起，且不新增导航。"""

    def _history_button(self, env: dict) -> ft.IconButton:
        column = env["result"].content
        assert isinstance(column, ft.Column)
        status_bar = column.controls[1]
        for control in _iter_controls(status_bar):
            if isinstance(control, ft.IconButton) and control.tooltip == "agent_research_history_toggle":
                return control
        raise AssertionError("未找到历史入口按钮")

    def test_history_panel_toggles_without_extra_nav(self, layout_env) -> None:
        assert _observation_control(layout_env) is None
        _invoke(self._history_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)
        assert _observation_control(layout_env) is not None, "展开后应挂历史面板"
        assert len(_nav_rail(layout_env).destinations) == 7, "不得新增第八导航"
        assert layout_env["page"].run_task.call_args.args[0] == _shared_vm().load_sessions

        _invoke(self._history_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)
        assert _observation_control(layout_env) is None, "再次点击应收起历史面板"

    def test_history_panel_callbacks_route_to_vm_and_run_task(self, layout_env) -> None:
        _invoke(self._history_button(layout_env).on_click, MagicMock())
        _rerender(layout_env)
        panel = _observation_control(layout_env)
        assert type(panel).__name__ == "Component"
        vm = _shared_vm()
        page = layout_env["page"]

        panel.kwargs["on_query_text"]("回撤")
        panel.kwargs["on_scope_filter"]("child")
        panel.kwargs["on_status_filter"]("archived")
        assert vm.state.history_query == "回撤"
        assert vm.state.history_scope_filter == "child"
        assert vm.state.session_status_filter == "archived"
        panel.kwargs["on_cancel_cleanup"]()
        assert vm.state.cleanup_preview is None

        panel.kwargs["on_search"]()
        assert page.run_task.call_args.args[0] == vm.run_history_search
        panel.kwargs["on_load_more"]()
        assert page.run_task.call_args.args[0] == vm.load_more_history
        panel.kwargs["on_begin_cleanup"]()
        assert page.run_task.call_args.args[0] == vm.begin_cleanup
        panel.kwargs["on_confirm_cleanup"]()
        assert list(page.run_task.call_args.args) == [vm.confirm_cleanup, True]
        panel.kwargs["on_stop_using"]()
        assert page.run_task.call_args.args[0] == vm.stop_source_use
        panel.kwargs["on_open_session"]("s-2")
        assert list(page.run_task.call_args.args) == [vm.open_session, "s-2"]

        before = page.run_task.call_count
        panel.kwargs["on_refresh_usage"]()
        assert page.run_task.call_count == before, "占用摘要为同步纯状态刷新，不应调度任务"

        panel.kwargs["on_close"]()
        _rerender(layout_env)
        assert _observation_control(layout_env) is None


def test_history_callbacks_are_no_ops_when_research_unwired(
    mock_i18n_state, mock_app_colors_state, monkeypatch
) -> None:
    """研究服务未接线时：展开历史只提示未接线，所有历史动作不得调度任务。"""

    env = _mount(
        SimpleNamespace(ready=True),
        service=None,
        mock_i18n_state=mock_i18n_state,
        mock_app_colors_state=mock_app_colors_state,
        monkeypatch=monkeypatch,
    )
    column = env["result"].content
    status_bar = column.controls[1]
    button = next(
        control
        for control in _iter_controls(status_bar)
        if isinstance(control, ft.IconButton) and control.tooltip == "agent_research_history_toggle"
    )

    _invoke(button.on_click, MagicMock())
    _rerender(env)
    panel = _observation_control(env)
    assert type(panel).__name__ == "Component"
    assert _shared_vm().state.available is False

    env["page"].run_task.reset_mock()
    for name in ("on_search", "on_load_more", "on_begin_cleanup", "on_confirm_cleanup", "on_stop_using"):
        panel.kwargs[name]()
    assert env["page"].run_task.call_count == 0, "未接线时历史动作必须为只读空操作"
    panel.kwargs["on_refresh_usage"]()
