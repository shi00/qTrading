"""研究工作台渲染与「＋指令与引用」菜单接线单测（W-18）。

覆盖：
- 四视角切换按钮渲染顺序与当前视角高亮；
- 概览查询草稿渲染 + 菜单入口存在；
- 未接线 / 空态显式呈现；
- 菜单四项分别只触发对应回调（不重复实现选择器）。
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import MagicMock

import flet as ft
import pytest

from ui.i18n import I18n
from ui.viewmodels.research_observer_view_model import ResearchObserverState
from ui.viewmodels.research_workbench_view_model import (
    PERSPECTIVE_EVIDENCE,
    PERSPECTIVE_OVERVIEW,
    ResearchWorkbenchState,
    build_perspective_view,
)
from ui.views.workbench_command_menu import build_command_reference_menu
from ui.views.workbench_panel import (
    WorkbenchPanel,
    build_facts_section,
    build_perspective_content,
    build_perspective_tabs,
    build_rows_section,
    build_workbench_body,
)
from ui.viewmodels.research_workbench_view_model import WorkbenchFact, WorkbenchRow
from tests.unit.ui.render_helper import render_component

pytestmark = pytest.mark.unit


def _noop_text(_value: str) -> None:
    return None


def _noop_id(_value: str | None) -> None:
    return None


def _noop() -> None:
    return None


def test_tabs_render_in_order_and_highlight_current() -> None:
    clicked: list[str] = []
    row = build_perspective_tabs(PERSPECTIVE_EVIDENCE, on_perspective=clicked.append)

    assert [button.data for button in row.controls] == ["overview", "evidence", "conclusion", "config"]
    labels = [button.content for button in row.controls]
    assert labels == [
        I18n.get("agent_workbench_tab_overview"),
        I18n.get("agent_workbench_tab_evidence"),
        I18n.get("agent_workbench_tab_conclusion"),
        I18n.get("agent_workbench_tab_config"),
    ]

    for control in row.controls:
        control.on_click(MagicMock())
    assert clicked == ["overview", "evidence", "conclusion", "config"]


def test_body_renders_query_field_and_command_menu() -> None:
    state = ResearchObserverState(available=True, session_title="银行研究")
    workbench_state = ResearchWorkbenchState(overview_query="息差")

    control = build_workbench_body(
        state,
        workbench_state,
        on_perspective=_noop_text,
        on_overview_query=_noop_text,
        on_select_evidence=_noop_id,
    )

    assert isinstance(control, ft.Column)
    field = next(item for item in control.controls if isinstance(item, ft.Row) and len(item.controls) == 2)
    text_field = next(item for item in field.controls if isinstance(item, ft.TextField))
    assert text_field.value == "息差"
    assert isinstance(field.controls[1], ft.PopupMenuButton)

    labels = [item.value for item in control.controls if isinstance(item, ft.Text)]
    assert I18n.get("agent_workbench_query_label") in labels


def test_unavailable_state_is_explicit() -> None:
    state = ResearchObserverState(
        available=False,
        unavailable_reason_key="agent_research_source_unavailable",
    )
    view = build_perspective_view(state, PERSPECTIVE_OVERVIEW)

    control = build_perspective_content(view, on_select_evidence=_noop_id)

    assert isinstance(control, ft.Column)
    texts = [item.value for item in control.controls if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_source_unavailable") in texts


def test_empty_state_is_explicit() -> None:
    state = ResearchObserverState(available=True)
    view = build_perspective_view(state, PERSPECTIVE_EVIDENCE)

    control = build_perspective_content(view, on_select_evidence=_noop_id)

    texts = [item.value for item in control.controls if isinstance(item, ft.Text)]
    assert I18n.get("agent_workbench_empty_evidence") in texts


def test_error_state_is_shown_alongside_content() -> None:
    state = ResearchObserverState(available=True, session_title="会话", error_key="agent_research_refresh_failed")
    view = build_perspective_view(state, PERSPECTIVE_OVERVIEW)

    control = build_perspective_content(view, on_select_evidence=_noop_id)

    texts = [item.value for item in control.controls if isinstance(item, ft.Text)]
    assert I18n.get("agent_research_refresh_failed") in texts


def test_command_menu_each_item_triggers_its_own_callback() -> None:
    calls: list[str] = []
    menu = build_command_reference_menu(
        on_command=lambda: calls.append("command"),
        on_reference=lambda: calls.append("reference"),
        on_skill=lambda: calls.append("skill"),
        on_mcp=lambda: calls.append("mcp"),
    )

    assert isinstance(menu, ft.PopupMenuButton)
    assert len(menu.items) == 4
    assert menu.tooltip == I18n.get("agent_composer_menu_open")

    for item in menu.items:
        cast(Any, item.on_click)(MagicMock())

    assert calls == ["command", "reference", "skill", "mcp"]


def test_facts_section_shows_missing_explicitly() -> None:
    column = build_facts_section((WorkbenchFact("agent_workbench_fact_session_id"),))

    values = [row.controls[1].value for row in column.controls]
    assert values == [I18n.get("agent_workbench_value_missing")]


def test_rows_section_click_selects_row() -> None:
    selected: list[str] = []
    column = build_rows_section(
        (WorkbenchRow(row_id="e1", title="节点", subtitle="state=done", selected=True),),
        on_select=selected.append,
    )

    column.controls[0].on_click(MagicMock())
    assert selected == ["e1"]


def test_workbench_panel_shell_renders_title_and_close() -> None:
    closed: list[bool] = []
    container = render_component(
        WorkbenchPanel,
        state=ResearchObserverState(available=True, session_title="银行研究"),
        workbench_state=ResearchWorkbenchState(),
        on_close=lambda: closed.append(True),
    )

    assert isinstance(container, ft.Container)
    header = container.content.controls[0]
    assert header.controls[0].value == I18n.get("agent_workbench_title")
    header.controls[1].on_click(MagicMock())
    assert closed == [True]
