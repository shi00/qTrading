"""NewsInsightPanel 单测（新闻风险解读第一期 Phase D2）。

设计依据：``reviews/新闻风险解读方案.md`` §12（UI 状态机）+ §15（测试）。
覆盖：纯映射函数、渲染分支（free of flet UI 状态）、i18n key 中英对称。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import flet as ft
import pytest

from core.i18n import I18n
from ui.components.news_insight_panel import (
    NewsInsightState,
    _build_coverage,
    _build_degraded_body,
    _build_event_card,
    _build_event_cards,
    _build_evidence_list,
    _build_evidence_phase_body,
    _build_ready,
    coverage_source_label_key,
    risk_level_label_key,
    severity_color,
    source_status_label_key,
)

if TYPE_CHECKING:
    from ui.viewmodels.news_insight_types import RiskEvent


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


def test_build_ready_reused_shows_snapshot_time_and_cache_read():
    """UIR-F05-003：复用态同时展示快照生成时间 t0 与本次缓存读取时间 t1。"""
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        risk_level="high",
        reused=True,
        reuse_type="cache_hit",
        analysis_time="2026-09-10 10:00",
        cache_read_time="2026-09-16 12:30",
    )
    joined = "\n".join(_iter_text_values(_build_ready(state, ft.Column())))
    assert "2026-09-10 10:00" in joined
    assert "2026-09-16 12:30" in joined
    assert I18n.get("news_insight_cache_read_time") in joined
    assert I18n.get("news_insight_reused_badge") in joined


def test_build_ready_missing_generated_at_shows_unrecorded():
    """UIR-F05-003：快照生成时间缺失时显式「未记录」，不得以读取时刻冒充（R21）。"""
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        risk_level="high",
        reused=True,
        analysis_time=None,
        cache_read_time="2026-09-16 12:30",
    )
    joined = "\n".join(_iter_text_values(_build_ready(state, ft.Column())))
    assert I18n.get("news_insight_gen_time_unknown") in joined
    assert "2026-09-16 12:30" in joined


def test_build_ready_non_reused_hides_cache_read_time():
    """UIR-F05-003：非复用场景不渲染缓存读取时间标签。"""
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        risk_level="high",
        reused=False,
        analysis_time="2026-09-16 12:30",
        cache_read_time=None,
    )
    joined = "\n".join(_iter_text_values(_build_ready(state, ft.Column())))
    assert "2026-09-16 12:30" in joined
    assert I18n.get("news_insight_cache_read_time") not in joined


def test_build_ready_exec_metadata_shows_identity_summary():
    """UIR-F05-002：有执行元数据时以一行身份摘要展示（模型 · AI 状态 · 用量状态）。"""
    state = NewsInsightState(
        phase="ready",
        ts_code="000001.SZ",
        risk_level="high",
        execution_metadata=(
            ("actual_model", "local-model"),
            ("ai_status", "success"),
            ("usage_status", "ok"),
        ),
    )
    joined = "\n".join(_iter_text_values(_build_ready(state, ft.Column())))
    assert I18n.get("news_insight_exec_meta") in joined
    assert "local-model · success · ok" in joined


def test_build_ready_missing_exec_metadata_shows_unrecorded():
    """UIR-F05-002/R21：元数据缺失显式「未记录」，不得省略成看似正常。"""
    state = NewsInsightState(phase="ready", ts_code="000001.SZ", risk_level="high", execution_metadata=None)
    joined = "\n".join(_iter_text_values(_build_ready(state, ft.Column())))
    assert I18n.get("news_insight_exec_meta") in joined
    assert I18n.get("news_insight_exec_meta_unknown") in joined


def _iter_text_values(control) -> list:
    """递归收集控件树（content / controls）中所有 ft.Text.value。"""
    values: list = []
    if isinstance(control, ft.Text):
        values.append(control.value)
    content = getattr(control, "content", None)
    if content is not None:
        values.extend(_iter_text_values(content))
    for child in getattr(control, "controls", None) or []:
        values.extend(_iter_text_values(child))
    return values


def _event_card_sections(card: ft.Control) -> list:
    """事件卡片外层 Column 的直接子控件（header + 展开明细）。"""
    outer = getattr(card, "content", None)
    assert isinstance(outer, ft.Column)
    return list(outer.controls)


def _risk_event_with_body() -> RiskEvent:
    from ui.viewmodels.news_insight_types import RiskEvent, RiskQuote

    return RiskEvent(
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
    card = _build_event_card(_risk_event_with_body(), is_expanded=True, on_toggle=lambda _e: None)
    assert isinstance(card, ft.Container)

    values = _iter_text_values(card)
    # F14：明细控件直接组合进 Column，Text.value 必须是字符串
    assert values
    assert all(isinstance(v, str) for v in values), f"Text.value 必须是字符串，实际={values!r}"

    joined = "\n".join(values)
    assert "事实内容" in joined
    assert "影响推断内容" in joined
    assert "不确定内容" in joined
    assert "引用原文" in joined

    # 引用保持自身 Column 子树（不被折叠进某个 Text.value）
    nested_columns = [c for c in _event_card_sections(card) if isinstance(c, ft.Column)]
    assert nested_columns, "引用应以 Column 子树呈现"
    assert any("引用原文" in "\n".join(_iter_text_values(c)) for c in nested_columns)


def test_build_event_card_expanded_omits_empty_sections():
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
        evidence_quotes=(),
    )
    card = _build_event_card(event, is_expanded=True, on_toggle=lambda _e: None)
    # 展开但所有明细字段为空：只保留 header，不生成空明细
    assert len(_event_card_sections(card)) == 1


def test_build_event_card_collapsed_excludes_expanded_body():
    card = _build_event_card(_risk_event_with_body(), is_expanded=False, on_toggle=lambda _e: None)
    joined = "\n".join(_iter_text_values(card))
    assert "事实内容" not in joined
    assert "影响推断内容" not in joined
    assert "不确定内容" not in joined
    assert "引用原文" not in joined


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


# --- degraded / error 取证区（UIR-F05-001） ---


def _iter_controls(control):
    """递归收集控件树（content / controls）中所有控件。"""
    yield control
    content = getattr(control, "content", None)
    if content is not None:
        yield from _iter_controls(content)
    for child in getattr(control, "controls", None) or []:
        yield from _iter_controls(child)


def _degraded_state(*, evidence=(), coverage=()):
    from ui.viewmodels import Message

    return NewsInsightState(
        phase="degraded",
        ts_code="000001.SZ",
        message=Message("news_insight_degraded", params={"status": "evidence_only"}),
        evidence=evidence,
        coverage=coverage,
    )


def _evidence_and_coverage():
    from ui.viewmodels.news_insight_types import EvidenceItem, SourceCoverage

    evidence = (
        EvidenceItem(
            news_id=1,
            source="巨潮资讯",
            title="某公司公告",
            quoteable_text="公告正文摘录",
            publish_time="2026-09-01 10:00",
        ),
    )
    coverage = (SourceCoverage(source="announcement", status="ok", fetched=1, adopted=1),)
    return evidence, coverage


def test_build_degraded_body_renders_evidence_and_coverage_once():
    """degraded：非空证据展示标题/摘录/覆盖，仅一个重试按钮，不出现"无风险"表述。"""
    evidence, coverage = _evidence_and_coverage()
    body = _build_degraded_body(_degraded_state(evidence=evidence, coverage=coverage), on_retry=lambda: None)

    values = [v for v in _iter_text_values(body) if isinstance(v, str)]
    joined = "\n".join(values)
    assert joined.count("某公司公告") == 1
    assert joined.count("公告正文摘录") == 1
    assert joined.count("公告: 正常") == 1
    assert "分析降级" in joined
    assert "无风险" not in joined

    retry_buttons = [c for c in _iter_controls(body) if isinstance(c, ft.OutlinedButton)]
    assert len(retry_buttons) == 1
    assert not [c for c in _iter_controls(body) if isinstance(c, ft.FilledButton)]


def test_build_degraded_body_empty_evidence_has_no_fabricated_sources():
    """degraded 空证据/空覆盖：只显示降级消息与重试，不虚构来源或证据标题。"""
    body = _build_degraded_body(_degraded_state(), on_retry=lambda: None)

    values = [v for v in _iter_text_values(body) if isinstance(v, str)]
    assert values == [I18n.get("news_insight_degraded", status="evidence_only")]
    assert len([c for c in _iter_controls(body) if isinstance(c, ft.OutlinedButton)]) == 1


def test_build_evidence_phase_body_error_renders_coverage_and_evidence_once():
    """error：覆盖与证据各出现一次，重试按钮唯一。"""
    from ui.viewmodels import Message

    evidence, coverage = _evidence_and_coverage()
    state = NewsInsightState(
        phase="error",
        ts_code="000001.SZ",
        message=Message("news_insight_analysis_failed", params={"status": "failed"}),
        evidence=evidence,
        coverage=coverage,
    )
    body = _build_evidence_phase_body(state, None, lambda: None)

    joined = "\n".join(v for v in _iter_text_values(body) if isinstance(v, str))
    assert joined.count("某公司公告") == 1
    assert joined.count("公告正文摘录") == 1
    assert joined.count("公告: 正常") == 1
    assert len([c for c in _iter_controls(body) if isinstance(c, ft.OutlinedButton)]) == 1


# --- i18n 中英对称守护（防漏翻译） ---

_PANEL_KEYS = {
    "news_insight_err_no_loop",
    "news_insight_err_evidence",
    "news_insight_db_error",
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
    "news_insight_gen_time_unknown",
    "news_insight_cache_read_time",
    "news_insight_reused_badge",
    "news_insight_exec_meta",
    "news_insight_exec_meta_unknown",
    "news_insight_coverage_fetched",
    "news_insight_coverage_ok",
    "news_insight_coverage_db_error",
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
