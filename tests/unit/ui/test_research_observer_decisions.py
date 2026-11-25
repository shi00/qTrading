"""研究观察面板·决定卡渲染单测（澄清候选/影响按缺失如实渲染）。"""

from __future__ import annotations

import flet as ft
import pytest

from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import PendingDecision
from ui.views.research_observer_decisions import build_decision_card

pytestmark = pytest.mark.unit


def _noop_draft(_decision_id: str, _value: str) -> None:
    return None


def _noop_submit(_decision_id: str, _execution_id: str, _round_id: str | None) -> None:
    return None


def _decision(
    *,
    decision_kind: str = "user_choice",
    question_text: str | None = "你关注行业景气还是个股估值？",
    options: tuple[str, ...] = ("行业景气", "个股估值"),
    impact: str | None = "决定后续分析口径",
) -> PendingDecision:
    return PendingDecision(
        decision_id="decision-1",
        execution_id="execution-1",
        round_id=None,
        decision_kind=decision_kind,
        revision=1,
        created_at="2026-01-01T00:00:00.000000Z",
        expires_at=None,
        question_text=question_text,
        options=options,
        impact=impact,
    )


def _texts(decision: PendingDecision) -> list[str]:
    control = build_decision_card(
        decision,
        draft="",
        submitting=False,
        on_draft_change=_noop_draft,
        on_submit=_noop_submit,
    )
    assert isinstance(control, ft.Container)
    column = control.content
    assert isinstance(column, ft.Column)
    return [item.value for item in column.controls if isinstance(item, ft.Text)]


def test_user_choice_card_renders_title_options_and_impact() -> None:
    texts = _texts(_decision())

    assert texts == [
        I18n.get("agent_research_clarification_title"),
        "你关注行业景气还是个股估值？",
        I18n.get("agent_research_clarification_option_fmt").format(option="行业景气"),
        I18n.get("agent_research_clarification_option_fmt").format(option="个股估值"),
        I18n.get("agent_research_clarification_impact").format(impact="决定后续分析口径"),
    ]


def test_user_choice_card_omits_empty_options_and_impact() -> None:
    texts = _texts(_decision(options=(), impact=None))

    assert texts == [
        I18n.get("agent_research_clarification_title"),
        "你关注行业景气还是个股估值？",
    ]


def test_non_clarification_card_keeps_kind_title_and_dedicated_hint() -> None:
    texts = _texts(_decision(decision_kind="tool_permission", options=(), impact=None))

    assert texts == [
        I18n.get("agent_research_decision_fmt").format(kind="tool_permission", revision=1),
        "你关注行业景气还是个股估值？",
        I18n.get("agent_research_decision_dedicated"),
    ]
