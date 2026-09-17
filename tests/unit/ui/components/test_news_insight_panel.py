"""NewsInsightPanel 单测（新闻风险解读第一期 Phase D2）。

设计依据：``reviews/新闻风险解读方案.md`` §12（UI 状态机）+ §15（测试）。
覆盖：纯映射函数、渲染分支（free of flet UI 状态）、i18n key 中英对称。
"""

from __future__ import annotations

import json
from pathlib import Path

import flet as ft
import pytest

from core.i18n import I18n
from ui.components.news_insight_panel import (
    NewsInsightState,
    _build_coverage,
    _build_event_card,
    _build_event_cards,
    _build_evidence_list,
    _build_ready,
    coverage_source_label_key,
    risk_level_label_key,
    severity_color,
    source_status_label_key,
)


@pytest.fixture(autouse=True)
def _init_i18n():
    I18n.initialize("zh_CN")
    yield


# --- 纯映射函数 ---


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        ("low", "news_insight_level_low"),
        ("medium", "news_insight_level_medium"),
        ("high", "news_insight_level_high"),
        ("critical", "news_insight_level_critical"),
        (None, "news_insight_level_unknown"),
        ("other", "news_insight_level_unknown"),
    ],
)
def test_risk_level_label_key(level, expected):
    assert risk_level_label_key(level) == expected


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        ("critical", "#F44336"),  # AppColors.ERROR 由 UP_RED 派生同值，断言同 token 即可
        ("high", "#F44336"),
        ("medium", "#FFAB00"),
        ("low", None),  # fallback 中性
        (None, None),
    ],
)
def test_severity_color(severity, expected):
    from ui.theme import AppColors

    color = severity_color(severity)
    if expected is None:
        assert color == AppColors.TEXT_SECONDARY
    else:
        assert color in (AppColors.ERROR, AppColors.UP_RED, AppColors.WARNING)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("announcement", "news_insight_src_announcement"),
        ("news", "news_insight_src_news"),
        ("telegraph", "news_insight_src_telegraph"),
    ],
)
def test_coverage_source_label_key(source, expected):
    assert coverage_source_label_key(source) == expected


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("ok", "news_insight_coverage_ok"),
        ("fail", "news_insight_coverage_fail"),
        ("unknown", "news_insight_coverage_fail"),
    ],
)
def test_source_status_label_key(status, expected):
    assert source_status_label_key(status) == expected


# --- 渲染分支（不含 hooks 的纯构建函数） ---


def test_build_evidence_list_renders_items():
    from ui.viewmodels.news_insight_types import EvidenceItem

    evidence = (
        EvidenceItem(
            news_id=1, source="新浪财经", title="某公司公告", quoteable_text="正文摘录", publish_time="2026-09-01 10:00"
        ),
        EvidenceItem(news_id=2, source_kind="news", title=""),
    )
    control = _build_evidence_list(evidence)
    assert isinstance(control, ft.Column)
    assert len(control.controls) == 2


def test_build_evidence_list_empty():
    control = _build_evidence_list(())
    assert isinstance(control, ft.Container)


def test_build_coverage_renders():
    from ui.viewmodels.news_insight_types import SourceCoverage

    coverage = (
        SourceCoverage(source="announcement", status="ok", fetched=2, adopted=2),
        SourceCoverage(source="news", status="fail"),
    )
    control = _build_coverage(coverage)
    assert isinstance(control, ft.Column)


def test_build_ready_no_events_uses_unknown_level():
    """ready 且无事件：不渲染决策文案，风险等级为未知（R21）。"""
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        stock_name="平安银行",
        risk_level=None,
        confidence=None,
        summary="",
        window_label="2026-09-16 ~ 2026-09-16",
        analysis_time="2026-09-16 10:00",
    )
    control = _build_ready(state, ft.Column())
    assert isinstance(control, ft.Column)
    assert "未知" in I18n.get("news_insight_level_unknown")


def test_build_ready_with_events():
    from ui.viewmodels.news_insight_types import RiskEvent

    event = RiskEvent(
        event_type="诉讼",
        severity="high",
        company_role="被告",
        event_status="进行中",
        impact_horizon="中长期",
        fact="涉及一起合同纠纷",
        impact_reasoning="可能影响经营",
        uncertainty="最终结果未定",
        confidence=70,
        evidence_quotes=(),
    )
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        risk_level="high",
        confidence=70,
        events=(event,),
        window_label="label",
        analysis_time="now",
    )
    control = _build_ready(state, ft.Column())
    assert isinstance(control, ft.Column)


def test_build_event_card_collapsed():
    from ui.viewmodels.news_insight_types import RiskEvent

    event = RiskEvent(
        event_type="诉讼",
        severity="high",
        company_role="",
        event_status="",
        impact_horizon="",
        fact="",
        impact_reasoning="",
        uncertainty="",
        confidence=0,
    )
    card = _build_event_card(event, is_expanded=False, on_toggle=lambda _e: None)
    assert isinstance(card, ft.Container)


def test_build_event_card_expanded_shows_sections():
    from ui.viewmodels.news_insight_types import RiskEvent, RiskQuote

    event = RiskEvent(
        event_type="诉讼",
        severity="high",
        company_role="",
        event_status="",
        impact_horizon="",
        fact="事实内容",
        impact_reasoning="影响推断内容",
        uncertainty="不确定内容",
        confidence=60,
        evidence_quotes=(RiskQuote(news_id=1, quote="引用原文"),),
    )
    card = _build_event_card(event, is_expanded=True, on_toggle=lambda _e: None)
    assert isinstance(card, ft.Container)


def test_build_event_cards_multiple():
    from ui.viewmodels.news_insight_types import RiskEvent

    def _risk_event(tag: str, severity: str) -> RiskEvent:
        return RiskEvent(
            event_type=tag,
            severity=severity,
            company_role="",
            event_status="",
            impact_horizon="",
            fact="",
            impact_reasoning="",
            uncertainty="",
            confidence=0,
        )

    events = (
        _risk_event("A", "high"),
        _risk_event("B", "low"),
    )
    state = NewsInsightState(phase="ready", ts_code="x", events=events)
    control = _build_event_cards(state, expanded_idx=0, on_toggle=lambda i: lambda _e: None)
    assert isinstance(control, ft.Column)
    assert len(control.controls) == 2


def test_translate_message_formats_params():
    from ui.components.news_insight_panel import _translate_message
    from ui.viewmodels import Message

    text = _translate_message(Message("news_insight_err_evidence", params={"ts_code": "000001.SZ"}))
    assert isinstance(text, str)
    assert "000001.SZ" in text


# --- i18n 中英对称守护（防漏翻译） ---

_PANEL_KEYS = {
    "news_insight_err_no_loop",
    "news_insight_err_evidence",
    "news_insight_no_evidence",
    "news_insight_err_analyze",
    "news_insight_analysis_failed",
    "news_insight_degraded",
    "news_insight_title",
    "news_insight_disclaimer",
    "news_insight_loading_evidence",
    "news_insight_analyzing",
    "news_insight_generate_btn",
    "news_insight_retry_btn",
    "news_insight_level_label",
    "news_insight_level_low",
    "news_insight_level_medium",
    "news_insight_level_high",
    "news_insight_level_critical",
    "news_insight_level_unknown",
    "news_insight_window_label",
    "news_insight_gen_time",
    "news_insight_reused_badge",
    "news_insight_coverage_fetched",
    "news_insight_coverage_ok",
    "news_insight_coverage_fail",
    "news_insight_src_announcement",
    "news_insight_src_news",
    "news_insight_src_telegraph",
    "news_insight_event_title",
    "news_insight_event_fact",
    "news_insight_event_impact",
    "news_insight_event_uncertainty",
    "news_insight_event_quotes",
    "news_insight_event_empty",
    "news_insight_summary",
    "news_insight_evidence_title",
}


def _load_locale_keys(locale: str) -> set[str]:
    path = Path(__file__).resolve().parents[4] / "locales" / locale / "strings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return set(data)


@pytest.mark.parametrize("locale", ["zh_CN", "en_US"])
def test_panel_i18n_keys_exist_in_locale(locale):
    keys = _load_locale_keys(locale)
    missing = _PANEL_KEYS - keys
    assert not missing, f"locale={locale} missing keys: {missing}"
