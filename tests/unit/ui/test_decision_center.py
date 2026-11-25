"""W-07 统一决定/授权界面与跨页待处理提醒测试。

覆盖：
1. 三动作（允许/拒绝/稍后）与幂等提交（同决定重复提交只写一次，AC28/AC35）；
2. 期限：过期不放行、不提交；「稍后」不延长期限、不移出待处理；
3. 授权范围按需加载与撤销（未接线如实拒绝，CAS 版本来自会话首读）；
4. 跨页提醒/公告：新增与解除阻塞、交付异常、定位源会话（非抢焦点）；
5. 视图：三动作、读屏 live_region 语义、定位回调、双语 key 完整性。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
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
    run_render_effects,
    run_unmount_effects,
)
from ui import agent_decision_arbiter
from ui.viewmodels import decision_center_view_model as vm_mod
from ui.viewmodels.decision_center_view_model import (
    ANNOUNCE_NEW,
    ANNOUNCE_RESOLVED,
    NOTICE_EMPTY_ANSWER,
    NOTICE_EXPIRED,
    NOTICE_REVOKE_DONE,
    NOTICE_REVOKE_UNAVAILABLE,
    NOTICE_SCOPE_FIRST,
    REMINDER_CONSUMPTION,
    REMINDER_DELIVERY,
    REMINDER_RESOLVED,
    AuthorizationDetail,
    DecisionCenterState,
    DecisionCenterViewModel,
    DecisionItemView,
    ReminderView,
    shared_decision_center,
)
from ui.views.decision_center_panel import build_decision_center_panel

pytestmark = pytest.mark.unit


def _fact(
    decision_id: str = "d-1",
    *,
    session_id: str = "s-1",
    session_title: str | None = "研究 A",
    decision_kind: str = "user_choice",
    stage: str = "answer",
    expires_at: str | None = None,
    command_record_id: str | None = None,
    delivery_state: Any = None,
    consumption_state: Any = None,
    question_payload: str | None = '{"question": "选哪个？"}',
) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        session_title=session_title,
        decision_id=decision_id,
        execution_id="e-1",
        round_id="r-1",
        decision_kind=decision_kind,
        revision=2,
        created_at="2026-11-24T01:00:00.000000Z",
        expires_at=expires_at,
        question_payload=question_payload,
        stage=stage,
        command_record_id=command_record_id,
        delivery_state=delivery_state,
        consumption_state=consumption_state,
    )


def _auth_payload() -> str:
    return json.dumps(
        {
            "question": "允许本次工具调用？",
            "tool_name": "search_docs",
            "service_id": "svc-1",
            "config_version": "3",
            "parameter_digest": "abcdef0123456789",
        },
        ensure_ascii=False,
    )


class _FakeService:
    """最小 facade 替身：记录答复/交付调用，可配置跨会话聚合与会话首读。"""

    def __init__(self, facts: tuple[Any, ...] = ()) -> None:
        self.facts = facts
        self.answer_calls: list[Any] = []
        self.delivery_calls: list[Any] = []
        self.read_calls: list[Any] = []
        self.summaries: tuple[Any, ...] = ()

    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> Any:
        assert user_scope == "local-desktop"
        return SimpleNamespace(items=self.facts, truncated=False)

    async def read_session(self, query: Any, *, user_scope: str) -> Any:
        self.read_calls.append(query)
        return SimpleNamespace(decisions=self.summaries)

    async def answer(self, request: Any) -> Any:
        self.answer_calls.append(request)
        await asyncio.sleep(0)
        return SimpleNamespace(command_record_id="cmd-1", delivery_state="not_delivered")

    async def deliver_answer(self, request: Any) -> Any:
        self.delivery_calls.append(request)
        return SimpleNamespace(delivery_state="delivered")


class _FakeRevoker:
    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def revoke_tool_grant(self, request: Any) -> Any:
        self.calls.append(request)
        return SimpleNamespace(grant_state="revoked", grant_revision=9)


def _vm(
    *,
    facts: tuple[Any, ...] = (),
    revoker: Any = None,
    now: datetime | None = None,
) -> tuple[DecisionCenterViewModel, _FakeService]:
    service = _FakeService(facts)
    current = now or datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
    return DecisionCenterViewModel(service, revoker=revoker, now=lambda: current), service


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset_decision_shared() -> Iterator[None]:
    vm_mod.reset_shared_decision_center()
    agent_decision_arbiter.get_decision_arbiter().reset()
    yield
    vm_mod.reset_shared_decision_center()
    agent_decision_arbiter.get_decision_arbiter().reset()


# ---------------------------------------------------------------------------
# VM：三动作 + 幂等
# ---------------------------------------------------------------------------


class TestDecisionActions:
    def test_allow_writes_approve_payload_and_delivers_once(self) -> None:
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),))

        _run(vm.refresh())
        _run(vm.allow("d-auth"))

        assert len(service.answer_calls) == 1, "允许应只写一次答复"
        request = service.answer_calls[0]
        assert json.loads(request.answer_payload) == {"approve": True}
        assert request.client_command_id == "ui-answer:d-auth"
        assert (request.expected_execution_id, request.expected_round_id) == ("e-1", "r-1")
        assert request.expected_decision_revision == 2
        assert len(service.delivery_calls) == 1, "保存成功后应尝试交付原命令一次"
        assert service.delivery_calls[0].command_record_id == "cmd-1"

    def test_deny_writes_approve_false(self) -> None:
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),))
        _run(vm.refresh())
        _run(vm.deny("d-auth"))
        assert json.loads(service.answer_calls[0].answer_payload) == {"approve": False}

    def test_concurrent_allow_is_at_most_once(self) -> None:
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),))
        _run(vm.refresh())

        async def _both() -> None:
            await asyncio.gather(vm.allow("d-auth"), vm.allow("d-auth"))

        _run(_both())
        assert len(service.answer_calls) == 1, "同一决定的并发提交只能落一次（AC28）"

    def test_second_decision_during_submit_is_ignored(self) -> None:
        facts = (
            _fact("d-a", decision_kind="tool_permission", question_payload=_auth_payload()),
            _fact("d-b", decision_kind="tool_permission", question_payload=_auth_payload()),
        )
        vm, service = _vm(facts=facts)
        _run(vm.refresh())

        async def _both() -> None:
            await asyncio.gather(vm.allow("d-a"), vm.allow("d-b"))

        _run(_both())
        assert [call.decision_id for call in service.answer_calls] == ["d-a"], "单槽只允许一个在途决定"

    def test_clarification_answer_uses_text_payload_and_clears_draft(self) -> None:
        vm, service = _vm(facts=(_fact("d-text"),))
        _run(vm.refresh())
        vm.set_answer_draft("d-text", "选 A")
        _run(vm.submit_answer("d-text"))
        assert json.loads(service.answer_calls[0].answer_payload) == {"text": "选 A"}
        assert vm.state.draft_for("d-text") == ""

    def test_empty_clarification_is_not_submitted(self) -> None:
        vm, service = _vm(facts=(_fact("d-text"),))
        _run(vm.refresh())
        _run(vm.submit_answer("d-text"))
        assert service.answer_calls == []
        assert vm.state.notice_key == NOTICE_EMPTY_ANSWER

    def test_defer_keeps_item_and_expiry_without_write(self) -> None:
        expires = "2026-11-25T00:00:00.000000Z"
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission", expires_at=expires),))
        _run(vm.refresh())
        before = vm.state.item_by_id("d-auth")
        assert before is not None

        vm.defer("d-auth")

        assert service.answer_calls == [], "「稍后」不得写服务"
        after = vm.state.item_by_id("d-auth")
        assert after is not None and after.expires_at == expires, "「稍后」不延长期限、不移出待处理"
        assert vm.state.is_deferred("d-auth")
        assert vm.state.ordered_items()[-1].decision_id == "d-auth", "稍后项排在后面但不隐藏"

    def test_expired_authorization_is_refused_without_write(self) -> None:
        vm, service = _vm(
            facts=(
                _fact(
                    "d-auth",
                    decision_kind="tool_permission",
                    expires_at="2026-11-24T11:00:00.000000Z",
                    question_payload=_auth_payload(),
                ),
            ),
        )
        _run(vm.refresh())
        item = vm.state.item_by_id("d-auth")
        assert item is not None and item.expired is True

        _run(vm.allow("d-auth"))

        assert service.answer_calls == []
        assert vm.state.notice_key == NOTICE_EXPIRED


# ---------------------------------------------------------------------------
# VM：授权范围 / 撤销
# ---------------------------------------------------------------------------


class TestAuthorizationDetail:
    def _vm_with_detail(self, *, revoker: Any = None) -> tuple[DecisionCenterViewModel, _FakeService]:
        vm, service = _vm(
            facts=(_fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),),
            revoker=revoker,
        )
        service.summaries = (
            SimpleNamespace(
                decision_id="d-auth",
                grant_state=SimpleNamespace(value="granted"),
                grant_revision=4,
            ),
        )
        _run(vm.refresh())
        return vm, service

    def test_detail_shows_scope_and_grant_state(self) -> None:
        vm, service = self._vm_with_detail()
        _run(vm.load_authorization_detail("d-auth"))

        detail = vm.state.detail_for("d-auth")
        assert detail is not None and detail.loaded
        assert (detail.grant_state, detail.grant_revision) == ("granted", 4)
        assert detail.revocable is True
        assert service.read_calls[0].session_id == "s-1"
        item = vm.state.item_by_id("d-auth")
        assert item is not None
        assert (item.tool_name, item.service_id, item.parameter_digest) == (
            "search_docs",
            "svc-1",
            "abcdef012345…",
        ), "授权范围只显示工具/服务/摘要前缀，不回显原始参数"

    def test_revoke_requires_loaded_scope(self) -> None:
        vm, service = self._vm_with_detail(revoker=_FakeRevoker())
        _run(vm.revoke("d-auth"))
        assert vm.state.notice_key == NOTICE_SCOPE_FIRST
        assert service.answer_calls == []

    def test_revoke_without_wired_port_is_honest(self) -> None:
        vm, _service = self._vm_with_detail(revoker=None)
        _run(vm.load_authorization_detail("d-auth"))
        _run(vm.revoke("d-auth"))
        assert vm.state.notice_key == NOTICE_REVOKE_UNAVAILABLE
        assert vm.state.revoke_available is False

    def test_revoke_passes_expected_revision_and_updates_state(self) -> None:
        revoker = _FakeRevoker()
        vm, _service = self._vm_with_detail(revoker=revoker)
        _run(vm.load_authorization_detail("d-auth"))
        _run(vm.revoke("d-auth"))

        assert len(revoker.calls) == 1
        assert revoker.calls[0].expected_grant_revision == 4
        assert revoker.calls[0].decision_id == "d-auth"
        detail = vm.state.detail_for("d-auth")
        assert detail is not None and detail.grant_state == "revoked"
        assert vm.state.notice_key == NOTICE_REVOKE_DONE


# ---------------------------------------------------------------------------
# VM：跨页提醒 / 公告
# ---------------------------------------------------------------------------


class TestRemindersAndAnnouncements:
    def test_first_refresh_is_baseline_without_announcement(self) -> None:
        vm, _service = _vm(facts=(_fact("d-1"),))
        _run(vm.refresh())
        assert vm.state.announcement is None, "首次读取只建基线，不打断用户"

    def test_new_pending_announces_without_focus_or_open(self) -> None:
        vm, service = _vm(facts=(_fact("d-1"),))
        _run(vm.refresh())
        service.facts = (_fact("d-1"), _fact("d-2"))
        _run(vm.refresh())

        announcement = vm.state.announcement
        assert announcement is not None
        assert announcement.key == ANNOUNCE_NEW
        assert dict(announcement.params) == {"count": "1"}
        assert vm.state.pending_count == 2

    def test_resolved_item_creates_locatable_reminder_and_announcement(self) -> None:
        vm, service = _vm(facts=(_fact("d-1"),))
        _run(vm.refresh())
        service.facts = ()
        _run(vm.refresh())

        announcement = vm.state.announcement
        assert announcement is not None and announcement.key == ANNOUNCE_RESOLVED
        resolved = [item for item in vm.state.reminders if item.kind == "resolved"]
        assert len(resolved) == 1
        assert resolved[0].key == REMINDER_RESOLVED
        assert resolved[0].session_id == "s-1", "解除阻塞提醒应保留源会话以便定位"
        assert resolved[0].session_title == "研究 A"

    def test_delivery_failure_and_consumption_reminders(self) -> None:
        facts = (
            _fact(
                "d-del",
                stage="delivery",
                command_record_id="cmd-1",
                delivery_state=SimpleNamespace(value="not_delivered"),
            ),
            _fact(
                "d-con",
                stage="consumption",
                command_record_id="cmd-2",
                delivery_state=SimpleNamespace(value="delivered"),
                consumption_state=SimpleNamespace(value="not_consumed"),
            ),
        )
        vm, _service = _vm(facts=facts)
        _run(vm.refresh())

        keys = {item.key for item in vm.state.reminders}
        assert REMINDER_DELIVERY in keys
        assert REMINDER_CONSUMPTION in keys
        delivery = next(item for item in vm.state.reminders if item.key == REMINDER_DELIVERY)
        assert delivery.session_id == "s-1"

    def test_dismiss_reminder_only_clears_local_reminder(self) -> None:
        vm, _service = _vm(
            facts=(_fact("d-del", stage="delivery", delivery_state=SimpleNamespace(value="not_delivered")),)
        )
        _run(vm.refresh())
        seq = vm.state.reminders[0].seq

        vm.dismiss_reminder(seq)

        assert vm.state.reminders == ()


# ---------------------------------------------------------------------------
# VM：不可用 / 读取失败 / 重试投递 / 组合根解析
# ---------------------------------------------------------------------------


class _BrokenService(_FakeService):
    async def read_pending_answers(self, *, user_scope: str, limit: int = 200) -> Any:
        raise RuntimeError("db down")


class TestFailurePaths:
    def test_unavailable_service_reports_reason_without_writes(self) -> None:
        vm = DecisionCenterViewModel(None, unavailable_reason_key="agent_research_not_wired")
        assert vm.state.available is False
        _run(vm.allow("d-1"))
        _run(vm.revoke("d-1"))
        _run(vm.retry_delivery("d-1"))
        assert vm.state.notice_key == "agent_research_not_wired"

    def test_pending_read_failure_is_reported_not_zero(self) -> None:
        vm = DecisionCenterViewModel(_BrokenService())
        _run(vm.refresh())
        assert vm.state.items == ()
        assert vm.state.error_code, "读取失败必须如实给出错误码（不填 0 待办）"

    def test_retry_delivery_uses_original_command_and_refresh(self) -> None:
        vm, service = _vm(
            facts=(
                _fact(
                    "d-del",
                    stage="delivery",
                    command_record_id="cmd-9",
                    delivery_state=SimpleNamespace(value="not_delivered"),
                ),
            ),
        )
        _run(vm.refresh())
        _run(vm.retry_delivery("d-del"))
        assert [call.command_record_id for call in service.delivery_calls] == ["cmd-9"]
        assert service.answer_calls == [], "重试投递不得新建答复"

    def test_retry_delivery_unknown_is_not_resent(self) -> None:
        vm, service = _vm(
            facts=(
                _fact(
                    "d-del",
                    stage="delivery",
                    command_record_id="cmd-9",
                    delivery_state=SimpleNamespace(value="delivery_unknown"),
                ),
            ),
        )
        _run(vm.refresh())
        _run(vm.retry_delivery("d-del"))
        assert service.delivery_calls == []
        assert vm.state.notice_key == "agent_research_delivery_unknown"

    def test_retry_delivery_stale_item_is_refused(self) -> None:
        vm, service = _vm(facts=())
        _run(vm.refresh())
        _run(vm.retry_delivery("missing"))
        assert service.delivery_calls == []
        assert vm.state.notice_key == vm_mod.NOTICE_STALE

    def test_detail_read_failure_keeps_loaded_false(self) -> None:
        class _ReaderFail(_FakeService):
            async def read_session(self, query: Any, *, user_scope: str) -> Any:
                raise RuntimeError("read failed")

        service = _ReaderFail(facts=(_fact("d-auth", decision_kind="tool_permission"),))
        vm = DecisionCenterViewModel(service)
        _run(vm.refresh())
        _run(vm.load_authorization_detail("d-auth"))
        detail = vm.state.detail_for("d-auth")
        assert detail is not None and detail.loaded is False and detail.error_code

    def test_detail_missing_decision_is_flagged(self) -> None:
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission"),))
        service.summaries = ()
        _run(vm.refresh())
        _run(vm.load_authorization_detail("d-auth"))
        detail = vm.state.detail_for("d-auth")
        assert detail is not None
        assert detail.error_code == "decision_not_in_session_read"
        assert detail.grant_revision is None

    def test_revoke_on_revoked_grant_is_not_repeated(self) -> None:
        revoker = _FakeRevoker()
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission"),), revoker=revoker)
        service.summaries = (
            SimpleNamespace(decision_id="d-auth", grant_state=SimpleNamespace(value="revoked"), grant_revision=5),
        )
        _run(vm.refresh())
        _run(vm.load_authorization_detail("d-auth"))
        _run(vm.revoke("d-auth"))
        assert revoker.calls == [], "已撤销的许可不得重复撤销"
        assert vm.state.notice_key == "agent_decision_grant_state_fmt"

    def test_grant_revision_missing_blocks_revoke(self) -> None:
        vm, service = _vm(facts=(_fact("d-auth", decision_kind="tool_permission"),), revoker=_FakeRevoker())
        service.summaries = (SimpleNamespace(decision_id="d-auth", grant_state="pending", grant_revision=None),)
        _run(vm.refresh())
        _run(vm.load_authorization_detail("d-auth"))
        _run(vm.revoke("d-auth"))
        assert vm.state.notice_key == "agent_decision_grant_unknown"

    def test_allow_on_clarification_is_read_only(self) -> None:
        vm, service = _vm(facts=(_fact("d-text"),))
        _run(vm.refresh())
        _run(vm.allow("d-text"))
        assert service.answer_calls == []
        assert vm.state.notice_key == vm_mod.NOTICE_READ_ONLY_KIND

    def test_draft_replace_keeps_latest_only(self) -> None:
        vm, _service = _vm(facts=(_fact("d-text"),))
        vm.set_answer_draft("d-text", "a")
        vm.set_answer_draft("d-text", "ab")
        assert vm.state.answer_drafts == (("d-text", "ab"),)

    def test_report_refusal_sets_notice(self) -> None:
        vm, _service = _vm()
        vm.report_refusal("agent_decision_busy")
        assert vm.state.notice_key == "agent_decision_busy"


class TestPayloadHelpers:
    def test_expiry_and_payload_helpers_are_conservative(self) -> None:
        assert vm_mod._parse_moment(None) is None
        assert vm_mod._parse_moment("not-a-date") is None
        assert vm_mod._parse_moment("2026-11-24T00:00:00Z") is not None
        naive = vm_mod._parse_moment("2026-11-24T00:00:00")
        assert naive is not None and naive.tzinfo is not None
        assert vm_mod._parse_payload(None) is None
        assert vm_mod._parse_payload("{bad") is None
        assert vm_mod._parse_payload("[1]") is None
        assert vm_mod._payload_text(None, "question") is None
        assert vm_mod._payload_text({"question": "  "}, "question") is None
        assert vm_mod._digest_preview(None) is None
        assert vm_mod._digest_preview("short") == "short"


class TestSharedWiring:
    def test_shared_vm_reused_and_reset(self) -> None:
        service = _FakeService()
        first = shared_decision_center(service)
        assert shared_decision_center(service) is first
        vm_mod.reset_shared_decision_center()
        assert vm_mod._shared_vm is None

    def test_resolver_accepts_service_with_revoke(self) -> None:
        revoker = _FakeRevoker()
        wiring = SimpleNamespace(service=revoker)
        assert vm_mod.resolve_tool_grant_revoker(wiring) is revoker
        assert vm_mod.resolve_tool_grant_revoker(None) is None
        assert vm_mod.resolve_tool_grant_revoker(SimpleNamespace(service=object())) is None


# ---------------------------------------------------------------------------
# 视图：三动作 / 授权范围 / 读屏公告 / 定位
# ---------------------------------------------------------------------------


def _auth_item(**overrides: Any) -> DecisionItemView:
    base = DecisionItemView(
        decision_id="d-auth",
        session_id="s-1",
        session_title="研究 A",
        execution_id="e-1",
        round_id="r-1",
        decision_kind="tool_permission",
        revision=2,
        stage="answer",
        created_at="2026-11-24T01:00:00.000000Z",
        expires_at="2026-11-24T18:00:00.000000Z",
        expiry_unknown=False,
        expired=False,
        question_text="允许本次工具调用？",
        tool_name="search_docs",
        service_id="svc-1",
        config_version="3",
        parameter_digest="abcdef012345…",
        command_record_id=None,
        delivery_state=None,
        consumption_state=None,
    )
    return replace(base, **overrides)


def _panel(state: DecisionCenterState, *, callbacks: dict[str, Any] | None = None) -> ft.Container:
    cbs = callbacks or {}
    noop = lambda *_a: None  # noqa: E731
    return build_decision_center_panel(
        state,
        on_close=cbs.get("on_close", noop),
        on_refresh=cbs.get("on_refresh", noop),
        on_allow=cbs.get("on_allow", noop),
        on_deny=cbs.get("on_deny", noop),
        on_defer=cbs.get("on_defer", noop),
        on_submit_answer=cbs.get("on_submit_answer", noop),
        on_draft_change=cbs.get("on_draft_change", noop),
        on_retry_delivery=cbs.get("on_retry_delivery", noop),
        on_load_detail=cbs.get("on_load_detail", noop),
        on_revoke=cbs.get("on_revoke", noop),
        on_locate=cbs.get("on_locate", noop),
        on_dismiss_reminder=cbs.get("on_dismiss_reminder", noop),
    )


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


def _buttons(panel: ft.Container) -> dict[str, ft.TextButton]:
    found: dict[str, ft.TextButton] = {}
    for control in _iter_controls(panel):
        if isinstance(control, ft.TextButton) and isinstance(control.content, ft.Text):
            found[str(control.content.value)] = control
    return found


def _texts(panel: ft.Container) -> list[str]:
    return [str(control.value) for control in _iter_controls(panel) if isinstance(control, ft.Text)]


def _click(control: Any) -> None:
    """按 Flet 事件签名点击控件（on_click 在类型上可空，测试断言其已绑定）。"""

    handler = getattr(control, "on_click", None)
    assert handler is not None, "控件应绑定点击处理器"
    handler(MagicMock())


@pytest.fixture
def key_i18n(monkeypatch: Any) -> None:
    mock_i18n = MagicMock()
    mock_i18n.get.side_effect = lambda key, *a, **kw: key
    from ui.views import decision_center_panel as panel_mod, research_observer_widgets as widgets_mod

    monkeypatch.setattr(panel_mod, "I18n", mock_i18n)
    monkeypatch.setattr(widgets_mod, "I18n", mock_i18n)


class TestDecisionCenterPanel:
    def test_authorization_renders_three_actions_and_scope(self, key_i18n: None) -> None:
        panel = _panel(DecisionCenterState(available=True, items=(_auth_item(),)))
        buttons = _buttons(panel)

        assert "agent_decision_allow" in buttons
        assert "agent_decision_deny" in buttons
        assert "agent_decision_later" in buttons
        texts = _texts(panel)
        assert "agent_decision_source_fmt" in texts
        assert "agent_decision_scope_fmt" in texts
        assert "agent_decision_tool_fmt" in texts
        assert "agent_decision_service_fmt" in texts

    def test_expired_item_disables_allow_and_deny(self, key_i18n: None) -> None:
        panel = _panel(DecisionCenterState(available=True, items=(_auth_item(expired=True),)))
        buttons = _buttons(panel)
        assert buttons["agent_decision_allow"].disabled is True
        assert buttons["agent_decision_deny"].disabled is True
        assert buttons["agent_decision_later"].disabled is False, "稍后仍可用（不写服务）"
        assert "agent_decision_expired_hint" in _texts(panel)

    def test_revoke_entry_hidden_until_scope_loaded_then_honest(self, key_i18n: None) -> None:
        panel = _panel(DecisionCenterState(available=True, items=(_auth_item(),)))
        assert "agent_decision_revoke" not in _buttons(panel)
        assert "agent_decision_load_scope" in _buttons(panel)

        state = DecisionCenterState(
            available=True,
            items=(_auth_item(),),
            details=(AuthorizationDetail(decision_id="d-auth", loaded=True, grant_state="granted", grant_revision=3),),
        )
        panel = _panel(state)
        assert _buttons(panel)["agent_decision_revoke"].disabled is True
        assert "agent_decision_revoke_unavailable" in _texts(panel)

        wired = replace(state, revoke_available=True)
        panel = _panel(wired)
        assert _buttons(panel)["agent_decision_revoke"].disabled is False

    def test_reminder_announcement_is_live_region_and_locatable(self, key_i18n: None) -> None:
        located: list[str] = []
        state = DecisionCenterState(
            available=True,
            announcement=vm_mod.AnnouncementView(seq=1, key=ANNOUNCE_NEW, params=(("count", "2"),)),
            announcement_seq=1,
            reminders=(
                ReminderView(
                    seq=1,
                    kind="resolved",
                    key=REMINDER_RESOLVED,
                    params=(("session", "研究 A"),),
                    session_id="s-1",
                    session_title="研究 A",
                    decision_id="d-1",
                ),
            ),
        )
        panel = _panel(state, callbacks={"on_locate": located.append})

        live_regions = [
            control
            for control in _iter_controls(panel)
            if isinstance(control, ft.Semantics) and control.live_region is True
        ]
        assert len(live_regions) == 1, "公告应挂在 live_region（读屏友好、非抢焦点）"
        assert live_regions[0].label == "agent_decision_announce_new_fmt"

        _click(_buttons(panel)["agent_decision_locate"])
        assert located == ["s-1"], "提醒应能定位到源会话"

    def test_clarification_renders_text_field_and_defer(self, key_i18n: None) -> None:
        item = replace(_auth_item(), decision_kind="user_choice", tool_name=None, service_id=None)
        submitted: list[str] = []
        panel = _panel(
            DecisionCenterState(available=True, items=(item,), answer_drafts=(("d-auth", "草稿"),)),
            callbacks={"on_submit_answer": submitted.append},
        )

        field = next(control for control in _iter_controls(panel) if isinstance(control, ft.TextField))
        assert field.value == "草稿"
        assert str(field.key) == "agent_decision_answer_d-auth"
        _click(_buttons(panel)["agent_decision_submit_answer"])
        assert submitted == ["d-auth"]

    def test_deferred_hint_is_shown_and_item_not_hidden(self, key_i18n: None) -> None:
        panel = _panel(DecisionCenterState(available=True, items=(_auth_item(),), deferred_ids=("d-auth",)))
        assert "agent_decision_deferred_hint" in _texts(panel)
        assert "agent_decision_source_fmt" in _texts(panel)

    def test_unavailable_error_notice_loading_and_truncated_are_rendered(self, key_i18n: None) -> None:
        state = DecisionCenterState(
            available=False,
            unavailable_reason_key="agent_research_not_wired",
            loading=True,
            truncated=True,
            error_code="db down",
            notice_key="agent_decision_action_saved",
        )
        panel = _panel(state)
        texts = _texts(panel)
        assert "agent_research_not_wired" in texts
        assert "agent_decision_error_fmt" in texts
        assert "agent_decision_action_saved" in texts
        assert "agent_research_pending_truncated" in texts
        assert any(isinstance(control, ft.ProgressRing) for control in _iter_controls(panel))

    def test_delivery_retry_button_only_for_retryable_items(self, key_i18n: None) -> None:
        item = replace(
            _auth_item(),
            decision_kind="user_choice",
            stage="delivery",
            command_record_id="cmd-1",
            delivery_state="not_delivered",
        )
        retried: list[str] = []
        panel = _panel(
            DecisionCenterState(available=True, items=(item,)),
            callbacks={"on_retry_delivery": retried.append},
        )
        _click(_buttons(panel)["agent_decision_retry_delivery"])
        assert retried == ["d-auth"]

        unknown = replace(item, delivery_state="delivery_unknown")
        panel = _panel(DecisionCenterState(available=True, items=(unknown,)))
        assert "agent_decision_retry_delivery" not in _buttons(panel)

    def test_reminder_without_session_cannot_locate(self, key_i18n: None) -> None:
        state = DecisionCenterState(
            available=True,
            reminders=(
                ReminderView(
                    seq=3,
                    kind="resolved",
                    key=REMINDER_RESOLVED,
                    params=(("session", ""),),
                    session_id="",
                    session_title=None,
                    decision_id="d-1",
                ),
            ),
        )
        panel = _panel(state)
        assert _buttons(panel)["agent_decision_locate"].disabled is True

    def test_detail_error_and_not_revocable_hints(self, key_i18n: None) -> None:
        state = DecisionCenterState(
            available=True,
            items=(_auth_item(),),
            details=(AuthorizationDetail(decision_id="d-auth", loaded=True, error_code="read failed"),),
            revoke_available=True,
        )
        panel = _panel(state)
        assert "agent_decision_scope_error_fmt" in _texts(panel)
        assert _buttons(panel)["agent_decision_revoke"].disabled is True, "许可版本缺失时不得撤销"

        not_revocable = replace(
            state,
            details=(AuthorizationDetail(decision_id="d-auth", loaded=True, grant_state="revoked", grant_revision=6),),
        )
        assert "agent_decision_revoke_not_applicable" in _texts(_panel(not_revocable))

    def test_submitting_labels_and_disabled_actions(self, key_i18n: None) -> None:
        state = DecisionCenterState(
            available=True,
            items=(_auth_item(),),
            submitting_decision_id="d-auth",
            submitting_action="allow",
        )
        buttons = _buttons(_panel(state))
        assert buttons["agent_decision_submitting"].disabled is True
        assert buttons["agent_decision_deny"].disabled is True

        clarification = replace(_auth_item(), decision_kind="user_choice")
        state = DecisionCenterState(
            available=True,
            items=(clarification,),
            submitting_decision_id="d-auth",
            submitting_action="answer",
        )
        assert "agent_decision_submitting" in _buttons(_panel(state))


# ---------------------------------------------------------------------------
# 双语 i18n
# ---------------------------------------------------------------------------


def test_new_i18n_keys_exist_in_both_locales() -> None:
    keys = [
        key
        for key in dir(vm_mod)
        if key.isupper() and isinstance(getattr(vm_mod, key), str) and getattr(vm_mod, key).startswith("agent_")
    ]
    values = {getattr(vm_mod, key) for key in keys}
    values.update(
        {
            "agent_decision_center_title",
            "agent_decision_allow",
            "agent_decision_deny",
            "agent_decision_later",
            "agent_decision_revoke",
            "agent_decision_locate",
            "agent_decision_stage_answer",
            "agent_decision_stage_unknown",
            "agent_decision_kind_authorization",
            "agent_decision_expired_hint",
            "agent_decision_center_scope_note",
            "agent_decision_grant_state_fmt",
            "agent_decision_reminders",
        }
    )
    root = Path(__file__).resolve().parents[3]
    for locale in ("zh_CN", "en_US"):
        data = json.loads((root / "locales" / locale / "strings.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in values if key not in data)
        assert missing == [], f"{locale} 缺少 {missing}"
        if locale == "en_US":
            assert not any("\u4e00" <= char <= "\u9fff" for key in values for char in data[key]), "英文文案不应包含中文"


# ---------------------------------------------------------------------------
# 壳层：任意页可达 / 不自动打开 / 非抢焦点播报
# ---------------------------------------------------------------------------


def _make_fake_page() -> Any:
    page = FakePage()
    page.run_task = MagicMock(return_value=MagicMock())  # type: ignore[method-assign]
    page.schedule_update = MagicMock()  # type: ignore[method-assign]
    page.pubsub = MagicMock()  # type: ignore[method-assign]
    page.pubsub.subscribe_topic = MagicMock()  # type: ignore[method-assign]
    page.pubsub.unsubscribe_topic = MagicMock()  # type: ignore[method-assign]
    return page


def _mount_layout(monkeypatch: Any, service: _FakeService) -> dict[str, Any]:
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
    from ui.views import decision_center_panel as panel_mod

    monkeypatch.setattr(panel_mod, "I18n", mock_i18n)
    monkeypatch.setattr(mod, "UILogger", MagicMock())

    component = make_component(mod.AppLayout, agent_wiring=SimpleNamespace(service=service, ready=True))
    page = _make_fake_page()
    run_mount_effects(component, page=page)
    result = render_once(component)
    return {"mod": mod, "component": component, "page": page, "result": result, "service": service}


def _render(env: dict[str, Any]) -> ft.Container:
    """重渲染并执行 deps 变化的 effect（读屏公告/角标同步在此触发）。"""

    run_render_effects(env["component"])
    env["result"] = render_once(env["component"])
    return env["result"]


def _leading_buttons(env: dict[str, Any]) -> list[Any]:
    body_region = env["result"].content.controls[0]
    rail = body_region.content.controls[0]
    return rail.leading.content.controls


def _tail_control(env: dict[str, Any]) -> Any:
    body_region = env["result"].content.controls[0]
    return body_region.content.controls[-1]


def test_decision_center_reachable_from_any_page(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    env = _mount_layout(monkeypatch, _FakeService())
    buttons = _leading_buttons(env)
    decision_toggle = buttons[5]
    assert decision_toggle.tooltip == "agent_decision_center_open"

    body_region = env["result"].content.controls[0]
    rail = body_region.content.controls[0]
    assert len(rail.destinations) == 7, "不得新增第八业务导航"

    decision_toggle.on_click(MagicMock())
    _render(env)
    assert isinstance(_tail_control(env), ft.Container), "展开后应挂非模态统一决定面板"
    assert _tail_control(env).width == 340

    # 切到另一业务页后入口仍在（跨页可达）
    rail.on_change(SimpleNamespace(control=SimpleNamespace(selected_index=2)))
    _render(env)
    assert _leading_buttons(env)[5].tooltip == "agent_decision_center_open"


def test_new_pending_does_not_auto_open_and_announces_politely(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    service = _FakeService(facts=(_fact("d-1"),))
    env = _mount_layout(monkeypatch, service)
    vm = vm_mod._shared_vm
    assert isinstance(vm, DecisionCenterViewModel)
    _run(vm.refresh())
    env["page"].run_task.reset_mock()

    service.facts = (_fact("d-1"), _fact("d-2"))
    _run(vm.refresh())
    _render(env)

    assert getattr(_tail_control(env), "width", None) != 340, "新事项不得自动打开面板"
    announced = [
        call.args
        for call in env["page"].run_task.call_args_list
        if getattr(call.args[0], "__name__", "") == "announce_message"
    ]
    assert announced, "新增待处理应以读屏公告提醒（不抢焦点）"
    assert announced[-1][1] == "agent_decision_announce_new_fmt"
    assert env["page"]._dialogs.controls == [], "不得弹模态框抢焦点"


def test_allow_double_click_only_schedules_once(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    service = _FakeService(facts=(_fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),))
    env = _mount_layout(monkeypatch, service)
    vm = vm_mod._shared_vm
    assert isinstance(vm, DecisionCenterViewModel)
    _run(vm.refresh())
    buttons = _leading_buttons(env)
    buttons[5].on_click(MagicMock())
    _render(env)

    panel = _tail_control(env)
    allow = _buttons(panel)["agent_decision_allow"]
    env["page"].run_task.reset_mock()
    _click(allow)
    _click(allow)

    scheduled = [call.args[0] for call in env["page"].run_task.call_args_list if call.args]
    runs = [item for item in scheduled if getattr(item, "__name__", "") == "_run_action"]
    assert len(runs) == 1, "仲裁单槽：双击只调度一次提交（至多一次）"


def _open_center(env: dict[str, Any]) -> ft.Container:
    """打开统一决定面板并重渲染（入口与观察层相互独立）。"""

    _click(_leading_buttons(env)[5])
    _render(env)
    panel = _tail_control(env)
    assert isinstance(panel, ft.Container) and panel.width == 340
    return panel


def test_center_shell_callbacks_cover_all_actions(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """壳层回调全覆盖：拒绝/答复/稍后/查看范围/撤销/重试投递都经统一入口。"""

    facts = (
        _fact("d-auth", decision_kind="tool_permission", question_payload=_auth_payload()),
        _fact("d-text", question_payload='{"question":"选哪个？"}'),
        _fact(
            "d-del",
            stage="delivery",
            command_record_id="cmd-1",
            delivery_state=SimpleNamespace(value="not_delivered"),
        ),
    )
    env = _mount_layout(monkeypatch, _FakeService(facts=facts))
    vm = vm_mod._shared_vm
    assert isinstance(vm, DecisionCenterViewModel)
    _run(vm.refresh())
    vm.set_answer_draft("d-text", "选 A")

    panel = _open_center(env)
    buttons = _buttons(panel)
    _click(buttons["agent_decision_deny"])
    _click(buttons["agent_decision_submit_answer"])
    _click(buttons["agent_decision_later"])
    _click(buttons["agent_decision_load_scope"])
    _click(buttons["agent_decision_retry_delivery"])

    # 详情加载后撤销入口才出现（许可版本未知时仍可点击，但服务端拒绝）
    _run(vm.load_authorization_detail("d-auth"))
    _render(env)
    _click(_buttons(_tail_control(env))["agent_decision_revoke"])

    names = [getattr(call.args[0], "__name__", "") for call in env["page"].run_task.call_args_list if call.args]
    assert "_run_action" in names, "写动作应经仲裁调度"
    assert "load_authorization_detail" in names, "查看范围应只读调度"
    assert vm.state.submitting_decision_id is None or vm.state.submitting_decision_id == "d-auth"


def test_pending_reminder_locate_returns_to_source_session(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """提醒「定位」切回源会话（用户显式点击，不自动跳转）。"""

    from ui.viewmodels import research_observer_view_model as research_mod

    service = _FakeService(
        facts=(
            _fact(
                "d-del",
                stage="delivery",
                command_record_id="cmd-1",
                delivery_state=SimpleNamespace(value="not_delivered"),
            ),
        ),
    )
    env = _mount_layout(monkeypatch, service)
    vm = vm_mod._shared_vm
    assert isinstance(vm, DecisionCenterViewModel)
    _run(vm.refresh())
    assert vm.state.reminders, "交付异常应生成跨页提醒"

    panel = _open_center(env)
    env["page"].run_task.reset_mock()
    _click(_buttons(panel)["agent_decision_locate"])

    research_vm = research_mod._shared_vm
    assert research_vm is not None
    result = [
        call.args
        for call in env["page"].run_task.call_args_list
        if call.args and call.args[0] == research_vm.open_session
    ]
    assert result and result[0][1] == "s-1", "定位应打开源会话 s-1"


def test_empty_pending_hides_status_indicator_and_read_failure_is_quiet(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """无待处理不显示状态栏指示；读取失败不伪造 0 待办也不阻塞壳层。"""

    env = _mount_layout(monkeypatch, _FakeService(facts=()))
    pending_labels = [
        control
        for control in _iter_controls(env["result"])
        if isinstance(control, ft.Text) and control.value == "agent_research_pending_fmt"
    ]
    assert pending_labels == [], "无待处理时不应出现待处理指示"

    broken = _mount_layout(monkeypatch, _BrokenService())
    vm = vm_mod._shared_vm
    assert isinstance(vm, DecisionCenterViewModel)
    _run(vm.refresh())
    assert vm.state.error_code, "读取失败应给出错误码"
    panel = _open_center(broken)
    assert "agent_decision_error_fmt" in _texts(panel)


def test_poll_tick_stops_on_cancellation_and_unmount_cancels_task(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """轮询协程取消必须传播（R2）；卸载时取消挂起的轮询任务。"""

    env = _mount_layout(monkeypatch, _FakeService())
    ticks = [
        call.args[0]
        for call in env["page"].run_task.call_args_list
        if call.args and getattr(call.args[0], "__name__", "") == "_tick"
    ]
    assert ticks, "挂载后应调度低频轮询"

    sleeps = {"count": 0}

    async def _sleep_once_then_cancel(*_a: Any, **_kw: Any) -> None:
        sleeps["count"] += 1
        if sleeps["count"] > 1:
            raise asyncio.CancelledError

    original_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", _sleep_once_then_cancel)
    try:
        with pytest.raises(asyncio.CancelledError) as exc_info:
            _run(ticks[0]())
    finally:
        monkeypatch.setattr(asyncio, "sleep", original_sleep)
    assert isinstance(exc_info.value, asyncio.CancelledError), "取消必须原样传播（R2）"
    assert sleeps["count"] >= 2, "首轮轮询应真正执行一次刷新后再进入下一轮"

    mount_task = env["page"].run_task.return_value
    run_unmount_effects(env["component"])
    assert mount_task.cancel.call_count >= 1, "卸载应取消轮询任务（不泄漏后台任务）"


def test_shell_sidebar_toggles_keep_panels_mutually_usable(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """公共区入口互不抢占：对比面板可开可关，决定中心入口仍在（跨页返回）。"""

    env = _mount_layout(monkeypatch, _FakeService())
    comparison_toggle = _leading_buttons(env)[4]
    _click(comparison_toggle)
    _render(env)
    assert getattr(_tail_control(env), "width", None) != 340, "对比面板与决定面板互不串位"

    _click(_leading_buttons(env)[4])
    _render(env)
    assert getattr(_tail_control(env), "width", None) != 340, "关闭对比后不得残留决定面板"
    assert _leading_buttons(env)[5].tooltip == "agent_decision_center_open"


def test_observer_panel_and_business_callbacks_schedule_async_commands(
    monkeypatch: Any, mock_i18n_state: Any, mock_app_colors_state: Any
) -> None:
    """观察层与业务入口回调都只调度异步命令（事件处理器内不做同步 IO，R16）。"""

    from ui import app_layout as layout_mod
    from ui.viewmodels import research_observer_view_model as research_mod

    panel_mock = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(layout_mod, "ResearchObserverPanel", panel_mock)
    env = _mount_layout(monkeypatch, _FakeService())
    _click(_leading_buttons(env)[3])
    _render(env)
    calls = panel_mock.call_args_list or panel_mock.mock_calls
    assert calls, "展开观察层后应构建观察面板"
    callbacks = calls[-1].kwargs

    research_vm = research_mod._shared_vm
    assert research_vm is not None
    research_vm._set_state(session_id="s-1", session_draft_revision=2)
    env["page"].run_task.reset_mock()
    callbacks["on_load_mcp_scope"]()
    callbacks["on_load_sessions"]()
    callbacks["on_load_draft"]("s-1", 2)
    callbacks["on_send"]()
    callbacks["on_start_plan"]()
    callbacks["on_submit"]("d-1", "e-1", "r-1")

    names = [getattr(call.args[0], "__name__", "") for call in env["page"].run_task.call_args_list if call.args]
    assert "load_mcp_scope" in names
    assert "load_sessions" in names
    assert "load_draft" in names
    assert "_run_action" in names, "观察层决定提交应经仲裁调度（至多一次）"

    assert "send_first_question" not in names, "未接线 composer 时不得伪造发送"
    assert "start_confirmed_plan" not in names, "未接线 composer 时不得伪造派发"
