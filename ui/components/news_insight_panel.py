"""新闻风险解读面板（声明式 V1）。

设计依据：``reviews/新闻风险解读方案.md`` §12（UI 状态机 + 默认交互）。

MVVM（CLAUDE.md §3.2 / docs/patterns/mvvm.md）：
- View = 声明式组件，只读不可变 ``NewsInsightState``（不可变快照，由父组件持有
  NewsInsightViewModel 并经 props 推送）+ command 回调；本组件不创建业务 VM，
  不持业务状态（事件展开仅用 ``use_state`` 临时 UI 态）。
- 只产出可读 label，禁止决策文案（"安全 / 可以买入 / 应卖出"）；无事件用
  ``None`` 表示未知（R21）；颜色不作为风险等级唯一表达。
- 状态提示 ``message``（``Message(key, params)``）渲染期 ``I18n.get(msg.key, **msg.params)``
  按当前 locale 翻译；``risk_level`` → 文本由本组件映射（非 i18n key）。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.news_insight_types import (
    EvidenceItem,
    NewsInsightState,
    PHASE_ANALYZING,
    PHASE_DEGRADED,
    PHASE_EVIDENCE_READY,
    PHASE_ERROR,
    PHASE_LOADING_EVIDENCE,
    PHASE_READY,
    RiskEvent,
)

# --- 纯映射函数（可独立单测；不依赖具体控件） ---


def risk_level_label_key(level: str | None) -> str:
    """风险等级 → i18n key（None=未知，R21：不填充低风险）。"""
    if level in ("low", "medium", "high", "critical"):
        return f"news_insight_level_{level}"
    return "news_insight_level_unknown"


def severity_color(severity: str) -> str:
    """风险等级/事件严重度 → 颜色 token（颜色仅辅助，非唯一表达）。"""
    return {
        "critical": AppColors.ERROR,
        "high": AppColors.UP_RED,
        "medium": AppColors.WARNING,
    }.get(severity, AppColors.TEXT_SECONDARY)


def coverage_source_label_key(source: str) -> str:
    """来源类型 → i18n key（announcement / news / telegraph）。"""
    return f"news_insight_src_{source}"


def source_status_label_key(status: str) -> str:
    """来源状态 → i18n key（ok / fail）。"""
    key = "news_insight_coverage_ok" if status == "ok" else "news_insight_coverage_fail"
    return key


# --- 声明式组件 ---


def _build_coverage(coverage) -> ft.Control:
    """来源覆盖区块（§12 必须显示）。"""
    if not coverage:
        return ft.Container()
    rows = []
    for c in coverage:
        status_txt = I18n.get(source_status_label_key(c.status))
        meta = ""
        if c.fetched or c.adopted:
            meta = f" · {I18n.get('news_insight_coverage_fetched', count=c.fetched)}"
        rows.append(
            ft.Text(
                f"{I18n.get(coverage_source_label_key(c.source))}: {status_txt}{meta}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    return ft.Column(rows, spacing=2)


def _build_evidence_list(evidence: tuple[EvidenceItem, ...]) -> ft.Control:
    """原始来源列表（§12 必显示：来源名/时间/正文摘录）。"""
    if not evidence:
        return ft.Container()
    items = []
    for item in evidence:
        title = item.title or item.quoteable_text
        source_part = item.source or item.source_kind or ""
        time_part = item.publish_time or ""
        head = " · ".join(p for p in (source_part, time_part) if p)
        quote = item.quoteable_text or ""
        items.append(
            ft.Container(
                content=ft.Column(
                    [
                        ft.Text(title or head, size=AppStyles.FONT_SIZE_BODY_SM, weight=ft.FontWeight.BOLD),
                        ft.Text(head, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                        ft.Text(
                            quote,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.TEXT_SECONDARY,
                            selectable=True,
                        )
                        if quote
                        else ft.Container(),
                    ],
                    spacing=2,
                ),
                padding=8,
                bgcolor=AppColors.SURFACE_VARIANT,
                border_radius=6,
            )
        )
    return ft.Column(items, spacing=6)


def _build_event_card(event: RiskEvent, is_expanded: bool, on_toggle) -> ft.Control:
    """单条风险事件卡片（点击展开事实/影响推断/不确定性/来源引用）。"""
    color = severity_color(event.severity)
    header = ft.Row(
        [
            ft.Text("▲" if is_expanded else "▼", size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
            ft.Text(
                event.event_type or I18n.get("news_insight_event_title"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
                color=color,
            ),
        ],
        spacing=6,
    )
    sections = []
    if is_expanded:
        if event.fact:
            sections.append(ft.Text(f"{I18n.get('news_insight_event_fact')}{event.fact}"))
        if event.impact_reasoning:
            sections.append(ft.Text(f"{I18n.get('news_insight_event_impact')}{event.impact_reasoning}"))
        if event.uncertainty:
            sections.append(ft.Text(f"{I18n.get('news_insight_event_uncertainty')}{event.uncertainty}"))
        if event.evidence_quotes:
            quote_rows = [
                ft.Text(
                    f"· {q.quote}",
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                    selectable=True,
                )
                for q in event.evidence_quotes
            ]
            sections.append(
                ft.Column(
                    [ft.Text(I18n.get("news_insight_event_quotes"), size=AppStyles.FONT_SIZE_BODY_SM)] + quote_rows,
                    spacing=2,
                )
            )
    return ft.Container(
        content=ft.Column(
            [header] + [ft.Text(s, size=AppStyles.FONT_SIZE_BODY_SM, selectable=True) for s in sections],
            spacing=4,
        ),
        padding=8,
        bgcolor=AppColors.SURFACE_VARIANT,
        border_radius=6,
        on_click=on_toggle,
    )


def _build_ready(state: NewsInsightState, event_cards: ft.Control) -> ft.Control:
    """ready：风险等级 + 窗口/时间/复用 + 覆盖 + 事件卡片 + 证据列表。"""
    level_txt = I18n.get(risk_level_label_key(state.risk_level))
    level_color = severity_color(state.risk_level)
    info_rows = [
        ft.Row(
            [
                ft.Text(
                    f"{I18n.get('news_insight_level_label')}: {level_txt}",
                    size=AppStyles.FONT_SIZE_LG,
                    weight=ft.FontWeight.BOLD,
                    color=level_color,
                ),
            ]
        )
    ]
    if state.window_label and state.window_label != "unknown":
        info_rows.append(
            ft.Text(
                f"{I18n.get('news_insight_window_label')}: {state.window_label}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    if state.analysis_time:
        info_rows.append(
            ft.Text(
                f"{I18n.get('news_insight_gen_time')}: {state.analysis_time}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    if state.reused:
        info_rows.append(
            ft.Text(
                I18n.get("news_insight_reused_badge"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.PRIMARY,
            )
        )

    children: list[ft.Control] = [
        ft.Column(info_rows, spacing=4),
        _build_coverage(state.coverage),
    ]
    # 无事件：用 None 表达"未知"（R21），不渲染绿色低风险
    if not state.events:
        children.append(
            ft.Text(
                I18n.get("news_insight_event_empty"),
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    children.append(
        ft.Text(I18n.get("news_insight_event_title"), size=AppStyles.FONT_SIZE_TITLE, weight=ft.FontWeight.BOLD)
    )
    children.append(event_cards)
    children.append(
        ft.Text(I18n.get("news_insight_evidence_title"), size=AppStyles.FONT_SIZE_TITLE, weight=ft.FontWeight.BOLD)
    )
    children.append(_build_evidence_list(state.evidence))
    return ft.Column(children, spacing=8)


def _build_event_cards(state: NewsInsightState, expanded_idx: int, on_toggle) -> ft.Control:
    """风险事件卡片列（展开态由上层组件持有）。"""
    rows = [_build_event_card(ev, expanded_idx == i, on_toggle(i)) for i, ev in enumerate(state.events)]
    return ft.Column(rows, spacing=6)


@ft.component
def NewsInsightPanel(
    news_state: NewsInsightState,
    news_on_generate: Callable[[], None] | None = None,
    news_on_retry: Callable[[], None] | None = None,
) -> ft.Container:  # pragma: no cover  # UI 组件闭包（hooks/事件处理器）不可无头单测
    """新闻风险解读面板（按 phase 渲染；不创建业务 VM）。"""
    ft.use_state(get_observable_state)

    expanded_idx, set_expanded_idx = ft.use_state(-1)

    def _reset_expanded() -> None:
        set_expanded_idx(-1)

    ft.use_effect(_reset_expanded, dependencies=[news_state.ts_code])

    def _toggle_event(idx: int) -> Callable[[ft.ControlEvent], None]:
        def _handler(_e: ft.ControlEvent) -> None:
            set_expanded_idx(-1 if expanded_idx == idx else idx)

        return _handler

    # --- 按 phase 组装 body ---
    body: ft.Control
    phase = news_state.phase
    if phase == PHASE_LOADING_EVIDENCE:
        body = ft.Column(
            [ft.ProgressRing(width=22, height=22), ft.Text(I18n.get("news_insight_loading_evidence"))],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
    elif phase == PHASE_ANALYZING:
        body = ft.Column(
            [ft.ProgressRing(width=22, height=22), ft.Text(I18n.get("news_insight_analyzing"))],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=8,
        )
    elif phase == PHASE_READY:
        body = _build_ready(news_state, _build_event_cards(news_state, expanded_idx, _toggle_event))
    elif phase == PHASE_DEGRADED:
        rows = [ft.Text(_translate_message(news_state.message))]
        if news_on_retry is not None:
            rows.append(ft.OutlinedButton(I18n.get("news_insight_retry_btn"), on_click=lambda _e: news_on_retry()))
        body = ft.Column(rows, spacing=8)
    else:  # evidence_ready / error / idle
        rows: list[ft.Control] = []
        if news_state.message is not None:
            rows.append(ft.Text(_translate_message(news_state.message), color=AppColors.TEXT_SECONDARY))
        if phase == PHASE_ERROR and news_on_retry is not None:
            rows.append(ft.OutlinedButton(I18n.get("news_insight_retry_btn"), on_click=lambda _e: news_on_retry()))
        rows.append(_build_evidence_list(news_state.evidence))
        if phase == PHASE_EVIDENCE_READY and news_on_generate is not None:
            rows.append(
                ft.ElevatedButton(I18n.get("news_insight_generate_btn"), on_click=lambda _e: news_on_generate())
            )
        body = ft.Column(rows, spacing=8)

    return ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            I18n.get("news_insight_title"),
                            size=AppStyles.FONT_SIZE_HEADLINE,
                            weight=ft.FontWeight.BOLD,
                        )
                    ]
                ),
                ft.Text(
                    I18n.get("news_insight_disclaimer"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                body,
            ],
            spacing=6,
        ),
        margin=ft.Margin.symmetric(vertical=8),
    )


def _translate_message(message) -> str | None:
    """把 Message(key, params) 翻译为当前 locale 字符串（locale 驱动渲染）。"""
    if message is None:
        return None
    return I18n.get(message.key, **message.params)
