"""ResearchComparisonPanel 渲染与 i18n 覆盖单测（F37）。

覆盖：组件为声明式 ``@ft.component``、并列/表格两种视图可渲染、未计价显式显示
「未知」而非 0（R21）、单路重试按钮可用性、VM 产出的 i18n key 在双语表中齐全。
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import flet as ft
import pytest

import ui.viewmodels.research_comparison_view_model as comparison_vm_module
import ui.views.research_comparison_panel as comparison_view_module
from tests.unit.ui.render_helper import render_component
from ui.viewmodels.research_comparison_view_model import (
    VIEW_SIDE_BY_SIDE,
    VIEW_TABLE,
    ComparisonConnectionOption,
    ComparisonGroupSummary,
    ComparisonLaneRow,
    ResearchComparisonState,
)
from ui.views.research_comparison_panel import ResearchComparisonPanel

pytestmark = pytest.mark.unit


class _FakeI18n:
    @staticmethod
    def get(key: str, *args: Any, **kwargs: Any) -> str:
        return key


@pytest.fixture(autouse=True)
def _patch_i18n(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(comparison_view_module, "I18n", _FakeI18n)


def _callbacks() -> dict[str, Any]:
    """组件回调桩（渲染测试不模拟点击，只校验接线与控件状态）。"""

    return {
        "on_close": lambda: None,
        "on_lane": lambda slot, connection_id: None,
        "on_text": lambda text: None,
        "on_mode": lambda mode: None,
        "on_view_mode": lambda view_mode: None,
        "on_submit": lambda: None,
        "on_refresh": lambda: None,
        "on_retry_lane": lambda lane_ref: None,
        "on_stop": lambda: None,
        "on_mcp_disconnect": lambda: None,
    }


def _option(connection_id: str, name: str) -> ComparisonConnectionOption:
    return ComparisonConnectionOption(
        connection_id=connection_id,
        display_name=name,
        model="model-x",
        revision=1,
        eligible=True,
        reject_key=None,
        target_fingerprint=f"fp-{connection_id}",
    )


def _lane_row(
    lane_ref: str,
    name: str,
    *,
    known: Decimal | None = None,
    retryable: bool = False,
    impact: str | None = None,
    reserved: Decimal | None = None,
    unpriced: int | None = None,
    active_ms: int | None = None,
) -> ComparisonLaneRow:
    impact_keys = {
        "preserve_result": "agent_comparison_impact_unaffected",
        "stop_new_dispatch": "agent_comparison_impact_stop_new_dispatch",
        "verify_unknown": "agent_comparison_impact_verify_unknown",
        "withdraw": "agent_comparison_impact_withdraw",
    }
    return ComparisonLaneRow(
        lane_ref=lane_ref,
        connection_id=f"c-{lane_ref}",
        display_name=name,
        display_state="failed" if retryable else "complete",
        reliable_result=not retryable,
        state_key="agent_comparison_lane_state_failed" if retryable else "agent_comparison_lane_state_complete",
        reason="failed_no_result" if retryable else "complete",
        reason_key="agent_comparison_reason_failed_no_result" if retryable else "agent_comparison_reason_complete",
        gap_key="agent_comparison_gap_no_result" if retryable else None,
        cost_state="known" if known is not None else "unread",
        cost_key="agent_comparison_cost_state_known" if known is not None else "agent_comparison_cost_state_unread",
        known_cny=known,
        reserved_cny=reserved,
        unpriced_attempts=unpriced,
        active_ms=active_ms,
        retryable=retryable,
        mcp_impact=impact,
        mcp_impact_key=impact_keys.get(impact) if impact is not None else None,
    )


def _group() -> ComparisonGroupSummary:
    return ComparisonGroupSummary(
        group_ref="group-1",
        group_state="partial",
        state_key="agent_comparison_group_partial",
        coverage_key="agent_comparison_coverage_partial",
        ended=False,
        selected_count=2,
        complete_count=1,
        partial_count=0,
        failed_count=1,
        pending_count=0,
        not_started_count=0,
        unconfirmed_count=0,
        cost_state="partial",
        cost_key="agent_comparison_cost_state_partial",
        known_cny=None,
        reserved_cny=None,
        unpriced_attempts=1,
    )


def _state(**changes: Any) -> ResearchComparisonState:
    state = ResearchComparisonState(
        available=True,
        connections=(_option("c1", "模型一"), _option("c2", "模型二")),
        group=_group(),
        group_ref="group-1",
        session_id="session-1",
        lanes=(_lane_row("lane-1", "模型一", known=Decimal("0.10")), _lane_row("lane-2", "模型二", retryable=True)),
    )
    return replace(state, **changes)


def _walk(control: Any) -> Iterator[Any]:
    yield control
    if isinstance(control, ft.Container):
        inner = control.content
        if inner is not None:
            yield from _walk(inner)
    elif isinstance(control, (ft.Row, ft.Column)):
        for child in control.controls:
            yield from _walk(child)
    elif isinstance(control, ft.DataTable):
        for column in control.columns:
            yield from _walk(column)
        for row in control.rows:
            yield from _walk(row)
    elif isinstance(control, ft.DataColumn):
        label = control.label
        if label is not None:
            yield from _walk(label)
    elif isinstance(control, ft.DataRow):
        for cell in control.cells:
            yield from _walk(cell)
    elif isinstance(control, ft.DataCell):
        inner = control.content
        if inner is not None:
            yield from _walk(inner)


def _texts(root: Any) -> list[str]:
    return [node.value for node in _walk(root) if isinstance(node, ft.Text)]


def test_component_is_declarative() -> None:
    assert hasattr(ResearchComparisonPanel, "__wrapped__")


def test_unavailable_state_renders_notice_without_composer() -> None:
    control = render_component(
        ResearchComparisonPanel,
        state=ResearchComparisonState(available=False, unavailable_reason_key="agent_comparison_not_wired"),
        **_callbacks(),
    )

    texts = _texts(control)
    assert "agent_comparison_title" in texts
    assert "agent_comparison_not_wired" in texts
    assert "agent_comparison_submit" not in texts


def test_side_by_side_shows_unknown_cost_not_zero() -> None:
    control = render_component(
        ResearchComparisonPanel,
        state=_state(view_mode=VIEW_SIDE_BY_SIDE),
        **_callbacks(),
    )

    texts = _texts(control)
    joined = "\n".join(texts)
    assert "模型一" in texts
    assert "模型二" in texts
    # 未知费用显式「未知」，不得渲染成 0。
    assert "agent_comparison_value_unknown" in joined
    assert "0" not in texts


def test_table_mode_renders_data_table() -> None:
    control = render_component(
        ResearchComparisonPanel,
        state=_state(view_mode=VIEW_TABLE),
        **_callbacks(),
    )

    tables = [node for node in _walk(control) if isinstance(node, ft.DataTable)]
    assert len(tables) == 1
    assert len(tables[0].rows) == 2
    assert len(tables[0].columns) == 6


def test_retry_button_disabled_for_reliable_lane() -> None:
    control = render_component(
        ResearchComparisonPanel,
        state=_state(view_mode=VIEW_SIDE_BY_SIDE),
        **_callbacks(),
    )

    buttons = [node for node in _walk(control) if isinstance(node, ft.TextButton)]
    retry_buttons = [
        node
        for node in buttons
        if isinstance(node.content, ft.Text) and node.content.value == "agent_comparison_retry_lane"
    ]
    assert len(retry_buttons) == 2
    assert sorted(button.disabled for button in retry_buttons) == [False, True]


def test_mcp_impact_text_rendered_for_lane() -> None:
    control = render_component(
        ResearchComparisonPanel,
        state=_state(
            lanes=(
                _lane_row("lane-1", "模型一", impact="preserve_result"),
                _lane_row("lane-2", "模型二", retryable=True, impact="stop_new_dispatch"),
            )
        ),
        **_callbacks(),
    )

    texts = _texts(control)
    joined = "\n".join(texts)
    assert "agent_comparison_impact_unaffected" in joined
    assert "agent_comparison_impact_stop_new_dispatch" in joined


def _comparison_keys_in_vm() -> set[str]:
    keys: set[str] = set()
    for value in vars(comparison_vm_module).values():
        if isinstance(value, Mapping):
            keys.update(item for item in value.values() if isinstance(item, str))
    keys.update(
        {
            "agent_comparison_not_wired",
            "agent_comparison_not_ready",
            "agent_comparison_lane_state_unstarted",
            "agent_comparison_connections_load_failed",
            "agent_comparison_error_generic",
            "agent_comparison_no_session",
            "agent_comparison_text_required",
            "agent_comparison_invalid_selection",
            "agent_comparison_need_two_lanes",
            "agent_comparison_lane_duplicate",
            "agent_comparison_connection_unknown",
            "agent_comparison_submitted",
            "agent_comparison_submit_partial",
            "agent_comparison_retry_rejected",
            "agent_comparison_retry_submitted",
            "agent_comparison_stopped",
            "agent_comparison_stopped_unverified",
            "agent_comparison_mcp_unavailable",
            "agent_comparison_mcp_reported",
            "agent_comparison_connection_disabled",
            "agent_comparison_connection_local_unsupported",
            "agent_comparison_connection_no_target",
        }
    )
    return {key for key in keys if isinstance(key, str) and key.startswith("agent_comparison_")}


def _locale_keys(locale: str) -> dict[str, str]:
    path = Path("locales") / locale / "strings.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_vm_i18n_keys_present_in_both_locales() -> None:
    zh = _locale_keys("zh_CN")
    en = _locale_keys("en_US")

    missing_zh = sorted(_comparison_keys_in_vm() - zh.keys())
    missing_en = sorted(_comparison_keys_in_vm() - en.keys())

    assert missing_zh == []
    assert missing_en == []


def test_comparison_locale_keys_are_parity() -> None:
    zh = {key for key in _locale_keys("zh_CN") if key.startswith("agent_comparison_")}
    en = {key for key in _locale_keys("en_US") if key.startswith("agent_comparison_")}

    assert zh == en


def test_error_message_and_no_group_notice_render() -> None:
    state = replace(
        _state(),
        group=None,
        group_ref=None,
        lanes=(),
        error_key="agent_comparison_error_generic",
        error_code="E_TEST",
        message_key="agent_comparison_submitted",
        selection_error_key="agent_comparison_no_session",
    )

    control = render_component(ResearchComparisonPanel, state=state, **_callbacks())

    joined = "\n".join(_texts(control))
    assert "agent_comparison_error_generic (E_TEST)" in joined
    assert "agent_comparison_submitted" in joined
    assert "agent_comparison_no_session" in joined
    assert "agent_comparison_no_group" in joined


def test_ineligible_option_and_duration_and_cost_extras_render() -> None:
    ineligible = ComparisonConnectionOption(
        connection_id="off",
        display_name="停用连接",
        model="model-y",
        revision=2,
        eligible=False,
        reject_key="agent_comparison_connection_disabled",
        target_fingerprint=None,
    )
    state = replace(
        _state(
            lanes=(
                _lane_row("lane-1", "模型一", known=Decimal("0.10"), reserved=Decimal("2"), unpriced=1, active_ms=120),
                _lane_row("lane-2", "模型二", retryable=True, active_ms=1500),
            )
        ),
        connections=(_option("c1", "模型一"), _option("c2", "模型二"), ineligible),
    )

    control = render_component(ResearchComparisonPanel, state=state, **_callbacks())

    joined = "\n".join(_texts(control))
    dropdown_labels = [
        option.text or "" for node in _walk(control) if isinstance(node, ft.Dropdown) for option in node.options
    ]
    assert any("agent_comparison_option_unavailable_fmt" in label for label in dropdown_labels)
    assert "agent_comparison_duration_ms_fmt" in joined
    assert "agent_comparison_duration_s_fmt" in joined
    assert "agent_comparison_cost_reserved_fmt" in joined
    assert "agent_comparison_cost_unpriced_fmt" in joined
