"""多选研究入口单测（选股结果 / 资讯两个入口，去重、上限、缺失与发起）。"""

from __future__ import annotations

import inspect
import typing
from pathlib import Path

import flet as ft
import pytest
from flet.components.component import Component

import ui.components.news_feed as news_feed_module
import ui.views.screener_view as screener_view_module
from tests.unit.ui.component_renderer import make_component, render_once
from ui.components.news_feed import (
    _build_multi_subject_candidates,
    _build_news_multi_research_controls,
)
from ui.i18n import I18n
from ui.viewmodels.home_view_model import NewsRow
from ui.viewmodels.multi_subject_research_view_model import (
    MultiSubjectResearchState,
    MultiSubjectResearchViewModel,
)
from ui.viewmodels.screener_view_model import ScreenerState
from ui.views.screener_view import (
    _build_multi_research_candidates,
    _build_multi_research_open_button,
    _build_screener_table_card,
    _collect_multi_research_rows,
    _find_research_raw_row,
)

pytestmark = pytest.mark.unit


def _screener_row(ts_code: str, name: str) -> dict:
    return {"_raw": {"ts_code": ts_code, "name": name}, "ts_code": ts_code, "name": name}


def test_screener_candidates_dedupe_and_missing_identity() -> None:
    rows = [
        _screener_row("600519.SH", "贵州茅台"),
        _screener_row("600519.SH", "贵州茅台"),
        {"_raw": {"name": "无代码标的"}, "name": "无代码标的"},
    ]

    candidates = _build_multi_research_candidates(rows)

    assert [item.object_id for item in candidates] == ["600519.SH", ""]
    assert candidates[0].display_name == "贵州茅台 (600519.SH)"
    assert candidates[1].selectable is False
    assert candidates[1].missing_reason == "missing_identity"


def test_news_candidates_dedupe_and_missing_identity() -> None:
    rows = (
        NewsRow(content="同一资讯", news_id=11),
        NewsRow(content="同一资讯", news_id=11),
        NewsRow(content="无 id 资讯", news_id=None),
    )

    candidates = _build_multi_subject_candidates(rows)

    assert [item.object_id for item in candidates] == ["11", ""]
    assert candidates[1].selectable is False
    assert candidates[1].missing_reason == "missing_identity"


def test_screener_collects_rows_across_sections_and_finds_raw() -> None:
    main_row = _screener_row("600519.SH", "贵州茅台")
    section_row = _screener_row("000001.SZ", "平安银行")
    rows = _collect_multi_research_rows([main_row], {"recommended": [section_row]})

    assert [row["ts_code"] for row in rows] == ["600519.SH", "000001.SZ"]
    assert _find_research_raw_row(rows, "000001.SZ") == {"ts_code": "000001.SZ", "name": "平安银行"}
    assert _find_research_raw_row(rows, "999999.SH") is None


def test_screener_open_button_only_when_wired() -> None:
    button = _build_multi_research_open_button(on_open=lambda _e: None)

    assert isinstance(button, ft.OutlinedButton)
    assert button.on_click is not None
    assert button.content.value == I18n.get("agent_multi_subject_open")
    assert _build_multi_research_open_button(on_open=None) is None


@ft.component
def _TableCardProbe(*, rows: list[dict], panel: ft.Control | None) -> ft.Container:
    """测试探针：在渲染上下文中构建表格卡片（PaginatedTable 为 @ft.component）。"""
    return _build_screener_table_card(
        state=ScreenerState(page_no=1, total_pages=1, total_items=len(rows)),
        formatted_rows=rows,
        vt_columns=[{"id": "ts_code", "label": "代码", "width": 120}],
        section_formatted={},
        on_prev_page=lambda _e: None,
        on_next_page=lambda _e: None,
        on_page_size_change=lambda _e: None,
        on_section_change=lambda _e: None,
        on_virtual_sort=lambda *_args: None,
        on_row_click=lambda _row: None,
        on_load_col_widths=lambda: None,
        on_persist_col_widths=lambda _widths: None,
        is_realtime=False,
        on_open_multi_research=lambda _e: None,
        multi_research_panel=panel,
    )


def _build_result_table_card(
    *,
    panel: ft.Control | None = None,
    rows: list[dict] | None = None,
) -> ft.Container:
    """构造「有结果」态的表格卡片（默认 1 行命中，复刻 1024x640 的布局前提）。"""

    if rows is None:
        rows = [_screener_row("000001.SZ", "平安银行")]
    return typing.cast(ft.Container, render_once(make_component(_TableCardProbe, rows=rows, panel=panel)))


def test_screener_table_card_entry_shares_toolbar_not_table_height() -> None:
    """多选入口不得占用结果表体的弹性高度（1024x640 表体归零回归）。

    精确断言：结果态卡片内**没有**独立的入口横条——多选按钮位于既有分页工具栏内，
    表体仍以 ``expand=True`` 独占剩余高度；否则低高度视口下 PaginatedTable 只剩表头，
    行文本（含「平安银行」）不进入语义树。
    """

    card = _build_result_table_card()
    children = card.content.controls

    assert len(children) == 4, f"结果态固定横条数变化（会挤压表体）: {[type(c).__name__ for c in children]}"
    disclaimer, body, divider, pagination = children
    assert isinstance(disclaimer, ft.Container)
    assert isinstance(body, ft.Container)
    assert body.expand is True
    assert isinstance(body.content, Component)
    assert isinstance(divider, ft.Divider)
    assert isinstance(pagination, ft.Row)

    buttons = [c for c in pagination.controls if isinstance(c, ft.OutlinedButton)]
    assert len(buttons) == 1, "多选入口按钮必须恰好一个且位于分页工具栏内"
    assert buttons[0].on_click is not None


def test_screener_table_card_empty_state_keeps_entry_button() -> None:
    """空态无分页工具栏可并入：入口按钮仍渲染（功能不降级），空态区保持弹性。"""

    card = _build_result_table_card(rows=[])
    children = card.content.controls

    assert len(children) == 3, f"空态结构变化: {[type(c).__name__ for c in children]}"
    entry_row, empty_zone = children[1], children[2]
    assert isinstance(entry_row, ft.Row)
    assert [type(c).__name__ for c in entry_row.controls] == ["OutlinedButton"]
    assert isinstance(empty_zone, ft.Container)
    assert empty_zone.expand is True


def test_screener_table_card_panel_shares_flex_height_with_body() -> None:
    """面板打开时与表体并列分享弹性高度（表体不归零、面板自带滚动）。"""

    panel = ft.Container(key="multi-subject-research-panel")
    card = _build_result_table_card(panel=panel)
    children = card.content.controls

    assert len(children) == 5, f"面板应作为独立弹性区加入: {[type(c).__name__ for c in children]}"
    body = children[1]
    panel_band = children[2]
    assert isinstance(panel_band, ft.Container)
    assert panel_band.content is panel
    assert panel_band.expand is True
    assert body.expand is True, "面板打开时表体仍须保留弹性高度，不得被固定横条挤为零"


def test_news_controls_render_open_button_and_panel_when_open(monkeypatch: pytest.MonkeyPatch) -> None:
    # 面板是 @ft.component，结构渲染在 test_multi_subject_research_panel.py 覆盖；
    # 此处只验证「打开时插入面板」，用占位控件替换避免依赖 Renderer 上下文。
    monkeypatch.setattr(
        news_feed_module,
        "MultiSubjectResearchPanel",
        lambda **_kwargs: ft.Container(key="multi-subject-research-panel"),
    )
    state = MultiSubjectResearchState(open=True, candidates=(), selected_keys=())
    callbacks = {
        "on_open": lambda _e: None,
        "on_toggle": lambda _key: None,
        "on_select_all": lambda: None,
        "on_clear": lambda: None,
        "on_submit": lambda: None,
        "on_close": lambda: None,
    }

    controls = _build_news_multi_research_controls(state, **callbacks)

    assert len(controls) == 2
    assert isinstance(controls[1], ft.Row)
    assert isinstance(controls[1].controls[0], ft.OutlinedButton)
    assert controls[0].key == "multi-subject-research-panel"


def test_screener_entry_submits_every_selected_subject() -> None:
    candidates = _build_multi_research_candidates(
        [_screener_row("600519.SH", "贵州茅台"), _screener_row("000001.SZ", "平安银行")]
    )
    vm = MultiSubjectResearchViewModel()
    vm.open_candidates(candidates)
    vm.select_all()
    prepared: list[str] = []

    vm.submit(lambda key: prepared.append(key) or True)

    assert prepared == ["screener_candidate:600519.SH", "screener_candidate:000001.SZ"]
    assert vm.state.comparison is not None
    assert vm.state.comparison.missing_objects == ()
    assert len(vm.state.comparison.rows) == 2


def test_news_entry_lists_missing_subject_instead_of_single_object() -> None:
    candidates = _build_multi_subject_candidates(
        (NewsRow(content="有 id 资讯", news_id=7), NewsRow(content="无 id 资讯", news_id=None))
    )
    vm = MultiSubjectResearchViewModel()
    vm.open_candidates(candidates)
    vm.toggle("news_item:7")
    prepared: list[str] = []

    vm.submit(lambda key: prepared.append(key) or True)

    assert prepared == ["news_item:7"]
    comparison = vm.state.comparison
    assert comparison is not None
    assert "无 id 资讯" in comparison.missing_objects
    assert len(comparison.rows) == 2  # 不静默降级为单对象
    assert comparison.status == "partial"


def test_both_entries_wire_multi_select_panel() -> None:
    screener_source = inspect.getsource(screener_view_module)
    news_source = Path(news_feed_module.__file__).read_text(encoding="utf-8")

    assert "on_open_multi_research" in screener_source
    assert "MultiSubjectResearchPanel(" in screener_source
    assert "_build_multi_research_candidates(" in screener_source

    assert "agent_multi_subject_open" in news_source
    assert "MultiSubjectResearchPanel(" in news_source
    assert "_build_multi_subject_candidates(" in news_source
