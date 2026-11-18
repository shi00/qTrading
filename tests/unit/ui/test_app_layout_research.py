"""AppLayout 研究观察层运行时测试（检视 P02）。

覆盖验收点：
1. 壳层公共区有研究入口，展开在不新增第八导航的前提下挂非模态观察组件；
2. 导航点击（含当前业务项）/ Ctrl+1—7 只收起观察层：不清草稿、不停止/取消研究；
3. 统一决定经仲裁管线（旧 Dialog 打开/他人占用时拒绝，不触发 facade 写调用）；
4. 观察面板只渲染状态与显式回调（不绑定键盘批准）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import flet as ft
import pytest

from tests.unit.ui.component_renderer import (
    FakePage,
    make_component,
    render_once,
    run_mount_effects,
)
from ui import agent_decision_arbiter
from ui.viewmodels import research_observer_view_model as vm_mod
from ui.viewmodels.research_observer_view_model import PendingDecision, ResearchObserverState

pytestmark = pytest.mark.unit


def _decision(decision_id: str = "d-1", *, kind: str = "user_choice") -> Any:
    return SimpleNamespace(
        decision_id=decision_id,
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

    async def search(self, query: Any, *, user_scope: str) -> Any:
        self.search_calls.append((query, user_scope))
        return SimpleNamespace(
            hits=(SimpleNamespace(session_id="s-1"),),
            read_moment="2026-11-17T03:00:00.000000Z",
        )

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_calls.append((query, user_scope))
        return SimpleNamespace(
            summary=SimpleNamespace(title="研究 A", updated_at="2026-11-17T02:00:00.000000Z"),
            executions=(SimpleNamespace(execution_id="e-1", display_state="running"),),
            decisions=(_decision(),),
        )

    async def answer(self, request: Any) -> Any:
        self.answer_calls.append(request)
        return SimpleNamespace(delivery_state=SimpleNamespace(value="not_delivered"))


class _FakeDecisionVm:
    """决定管线单测用的最小 VM：只记录 submit_answer 调用。"""

    def __init__(self) -> None:
        self.submitted: list[str] = []

    async def submit_answer(self, decision_id: str) -> None:
        self.submitted.append(decision_id)


@pytest.fixture(autouse=True)
def _reset_research_shared() -> Iterator[None]:
    vm_mod.reset_shared_research_observer()
    agent_decision_arbiter.get_decision_arbiter().reset()
    yield
    vm_mod.reset_shared_research_observer()
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


def _research_button(env: dict) -> ft.IconButton:
    leading = _nav_rail(env).leading
    assert isinstance(leading, ft.Container)
    column = leading.content
    assert isinstance(column, ft.Column)
    button = column.controls[3]
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
    from ui.views import research_observer_panel as panel_mod

    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    monkeypatch.setattr(panel_mod, "I18n", mock_i18n)
    cbs = callbacks or {}
    component = make_component(
        panel_mod.ResearchObserverPanel,
        state=state,
        notice=notice,
        on_close=cbs.get("on_close", lambda: None),
        on_refresh=cbs.get("on_refresh", lambda: None),
        on_draft_change=cbs.get("on_draft_change", lambda *_a: None),
        on_submit=cbs.get("on_submit", lambda *_a: None),
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
        if isinstance(control, ft.TextField):
            return control
    raise AssertionError("未找到答复输入框")


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

    def test_unavailable_state_is_honest(self, monkeypatch) -> None:
        panel = _render_panel(
            ResearchObserverState(available=False, unavailable_reason_key="agent_research_not_wired"),
            monkeypatch=monkeypatch,
        )
        _assert_text_present(panel, "agent_research_not_wired")

    def test_answerable_decision_wires_explicit_callbacks(self, monkeypatch) -> None:
        drafted: list[tuple[str, str]] = []
        submitted: list[str] = []
        decision = PendingDecision(
            decision_id="d-1",
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
                "on_submit": lambda decision_id: submitted.append(decision_id),
            },
        )

        field = _find_field(panel)
        _invoke(field.on_change, SimpleNamespace(control=SimpleNamespace(value="选择第一家")))
        assert drafted == [("d-1", "选择第一家")]

        button = _find_button(panel, "agent_research_submit")
        _invoke(button.on_click, MagicMock())
        assert submitted == ["d-1"]

    def test_authorization_decision_has_no_submit_entry(self, monkeypatch) -> None:
        decision = PendingDecision(
            decision_id="d-1",
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


class TestDecisionSubmissionPipeline:
    def test_happy_path_runs_vm_then_releases_arbiter(self) -> None:
        page = _make_fake_page()
        vm = _FakeDecisionVm()

        refusal = agent_decision_arbiter.submit_decision(
            page=page,
            vm=vm,
            owner="research_observer",
            decision_id="d-1",
        )

        assert refusal is None
        assert page.run_task.call_count == 1
        asyncio.run(page.run_task.call_args.args[0]())
        assert vm.submitted == ["d-1"]
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
        )

        assert refusal == agent_decision_arbiter.REFUSAL_DIALOG_OPEN
        assert page.run_task.call_args_list == []
        assert vm.submitted == []

    def test_second_surface_is_rejected(self) -> None:
        page = _make_fake_page()
        vm = _FakeDecisionVm()
        arbiter = agent_decision_arbiter.get_decision_arbiter()
        assert arbiter.begin_submit("other_surface", "d-9", has_open_modal_dialog=False) is None

        refusal = agent_decision_arbiter.submit_decision(
            page=page,
            vm=vm,
            owner="research_observer",
            decision_id="d-1",
        )

        assert refusal == agent_decision_arbiter.REFUSAL_OWNER_CONFLICT
        assert page.run_task.call_args_list == []
        arbiter.end_submit("other_surface")
