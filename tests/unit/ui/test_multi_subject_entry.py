"""多选研究入口单测（选股结果 / 资讯两个入口，去重、上限、缺失与发起）。"""

from __future__ import annotations

import inspect
from pathlib import Path

import flet as ft
import pytest

import ui.components.news_feed as news_feed_module
import ui.views.screener_view as screener_view_module
from ui.components.news_feed import (
    _build_multi_subject_candidates,
    _build_news_multi_research_controls,
)
from ui.viewmodels.home_view_model import NewsRow
from ui.viewmodels.multi_subject_research_view_model import (
    MultiSubjectResearchState,
    MultiSubjectResearchViewModel,
)
from ui.views.screener_view import (
    _build_multi_research_candidates,
    _build_multi_research_controls,
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


def test_screener_controls_render_open_button_and_panel() -> None:
    panel = ft.Container(key="panel")
    controls = _build_multi_research_controls(on_open=lambda _e: None, panel=panel)

    assert len(controls) == 2
    assert controls[1] is panel
    assert isinstance(controls[0], ft.Row)
    assert isinstance(controls[0].controls[0], ft.OutlinedButton)
    assert _build_multi_research_controls(on_open=None, panel=None) == []


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
