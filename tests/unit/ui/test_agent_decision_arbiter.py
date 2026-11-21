"""ui/agent_decision_arbiter.py 单测（检视 P02：统一决定仲裁）。"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ui.agent_decision_arbiter import (
    REFUSAL_BUSY,
    REFUSAL_DIALOG_OPEN,
    REFUSAL_OWNER_CONFLICT,
    DecisionArbiter,
    get_decision_arbiter,
    has_open_modal_dialog,
)

pytestmark = pytest.mark.unit


class TestDecisionArbiter:
    def test_begin_submit_succeeds_when_idle(self) -> None:
        arbiter = DecisionArbiter()
        assert (
            arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False)
            is None
        )
        assert arbiter.owner == "owner-a"
        assert arbiter.inflight_decision_id == "d-1"
        assert arbiter.inflight_scope == ("e-1", "r-1")

    def test_open_modal_dialog_blocks_even_when_idle(self) -> None:
        """旧 Dialog 不穿透批准：已有对话打开时任何决定都不接走输入。"""
        arbiter = DecisionArbiter()
        assert (
            arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=True)
            == REFUSAL_DIALOG_OPEN
        )
        assert arbiter.owner is None

    def test_same_owner_second_submit_is_busy(self) -> None:
        arbiter = DecisionArbiter()
        assert (
            arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False)
            is None
        )
        assert (
            arbiter.begin_submit("owner-a", "d-2", execution_id="e-2", round_id="r-2", has_open_modal_dialog=False)
            == REFUSAL_BUSY
        )
        assert arbiter.inflight_decision_id == "d-1"

    def test_other_owner_is_conflict(self) -> None:
        arbiter = DecisionArbiter()
        assert (
            arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False)
            is None
        )
        assert (
            arbiter.begin_submit("owner-b", "d-2", execution_id="e-2", round_id="r-2", has_open_modal_dialog=False)
            == REFUSAL_OWNER_CONFLICT
        )

    def test_end_submit_releases_only_for_owner(self) -> None:
        arbiter = DecisionArbiter()
        assert (
            arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False)
            is None
        )
        arbiter.end_submit("owner-b")
        assert arbiter.owner == "owner-a"
        arbiter.end_submit("owner-a")
        assert arbiter.owner is None
        assert arbiter.inflight_decision_id is None
        assert arbiter.inflight_scope == (None, None)
        # 释放后可再次提交
        assert (
            arbiter.begin_submit("owner-a", "d-2", execution_id="e-2", round_id="r-2", has_open_modal_dialog=False)
            is None
        )

    def test_runtime_ack_shares_the_single_decision_slot(self) -> None:
        """旧外发确认与统一决定不得并存（否则出现双份人工许可）。"""
        arbiter = DecisionArbiter()
        assert arbiter.begin_runtime_ack("screener-egress:1", "runtime-egress-ack") is None
        assert arbiter.inflight_scope == (None, None)
        assert (
            arbiter.begin_submit(
                "research_observer", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False
            )
            == REFUSAL_OWNER_CONFLICT
        )
        arbiter.end_submit("screener-egress:1")
        assert (
            arbiter.begin_submit(
                "research_observer", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False
            )
            is None
        )
        assert arbiter.begin_runtime_ack("screener-unpriced:1", "runtime-unpriced-ack") == REFUSAL_OWNER_CONFLICT

    def test_reset_clears_state(self) -> None:
        arbiter = DecisionArbiter()
        arbiter.begin_submit("owner-a", "d-1", execution_id="e-1", round_id="r-1", has_open_modal_dialog=False)
        arbiter.reset()
        assert arbiter.owner is None
        assert arbiter.inflight_decision_id is None
        assert arbiter.inflight_scope == (None, None)

    def test_module_singleton_is_stable(self) -> None:
        assert get_decision_arbiter() is get_decision_arbiter()


class TestHasOpenModalDialog:
    def test_page_without_dialogs_is_closed(self) -> None:
        assert has_open_modal_dialog(object()) is False

    def test_open_dialog_detected(self) -> None:
        page = SimpleNamespace(_dialogs=SimpleNamespace(controls=[SimpleNamespace(open=True)]))
        assert has_open_modal_dialog(page) is True

    def test_closed_dialog_not_treated_as_open(self) -> None:
        page = SimpleNamespace(_dialogs=SimpleNamespace(controls=[SimpleNamespace(open=False)]))
        assert has_open_modal_dialog(page) is False

    def test_magicmock_dialogs_supported(self) -> None:
        page = MagicMock()
        page._dialogs.controls = []
        assert has_open_modal_dialog(page) is False
