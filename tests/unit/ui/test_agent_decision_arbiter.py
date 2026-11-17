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
        assert arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=False) is None
        assert arbiter.owner == "owner-a"
        assert arbiter.inflight_decision_id == "d-1"

    def test_open_modal_dialog_blocks_even_when_idle(self) -> None:
        """旧 Dialog 不穿透批准：已有对话打开时任何决定都不接走输入。"""
        arbiter = DecisionArbiter()
        assert arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=True) == REFUSAL_DIALOG_OPEN
        assert arbiter.owner is None

    def test_same_owner_second_submit_is_busy(self) -> None:
        arbiter = DecisionArbiter()
        assert arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=False) is None
        assert arbiter.begin_submit("owner-a", "d-2", has_open_modal_dialog=False) == REFUSAL_BUSY
        assert arbiter.inflight_decision_id == "d-1"

    def test_other_owner_is_conflict(self) -> None:
        arbiter = DecisionArbiter()
        assert arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=False) is None
        assert arbiter.begin_submit("owner-b", "d-2", has_open_modal_dialog=False) == REFUSAL_OWNER_CONFLICT

    def test_end_submit_releases_only_for_owner(self) -> None:
        arbiter = DecisionArbiter()
        assert arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=False) is None
        arbiter.end_submit("owner-b")
        assert arbiter.owner == "owner-a"
        arbiter.end_submit("owner-a")
        assert arbiter.owner is None
        assert arbiter.inflight_decision_id is None
        # 释放后可再次提交
        assert arbiter.begin_submit("owner-a", "d-2", has_open_modal_dialog=False) is None

    def test_reset_clears_state(self) -> None:
        arbiter = DecisionArbiter()
        arbiter.begin_submit("owner-a", "d-1", has_open_modal_dialog=False)
        arbiter.reset()
        assert arbiter.owner is None
        assert arbiter.inflight_decision_id is None

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
