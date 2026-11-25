"""研究观察面板·待处理事项子视图（必要决定卡 / 跨会话待处理概览）。

只读事实 + 显式回调（草稿变更/提交/重试投递）；不 import VM、不拼装服务请求。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    PENDING_SCOPE_ALL,
    PENDING_STAGES,
    PendingAnswerView,
    PendingDecision,
    ResearchObserverState,
)
from ui.views.research_observer_widgets import hint_text, section_title

#: 跨会话待处理阶段分组标题（记录/交付/消费）。
_PENDING_STAGE_KEYS = {
    "answer": "agent_research_pending_answer",
    "delivery": "agent_research_pending_delivery",
    "consumption": "agent_research_pending_consumption",
}


def build_decision_card(
    decision: PendingDecision,
    *,
    draft: str,
    submitting: bool,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str | None], None],
) -> ft.Control:
    """单个必要决定的阅读卡（授权类只读，不给提交入口）。"""

    rows: list[ft.Control] = [
        ft.Text(
            I18n.get("agent_research_decision_fmt").format(
                kind=decision.decision_kind,
                revision=decision.revision,
            ),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
    ]
    if decision.question_text:
        rows.append(
            ft.Text(
                decision.question_text,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    if decision.expires_at is not None:
        rows.append(
            ft.Text(
                I18n.get("agent_research_decision_expires_fmt").format(expires_at=decision.expires_at),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if decision.answerable:
        rows.append(
            ft.TextField(
                # 输入态（含未上屏的输入法组合串与光标/选区）跟随决定归属，而不是
                # 列表位置：决定列表增删后同位置控件不再是同一张卡。
                key=f"agent_research_answer_{decision.decision_id}",
                value=draft,
                label=I18n.get("agent_research_answer_label"),
                hint_text=I18n.get("agent_research_answer_hint"),
                multiline=True,
                min_lines=2,
                max_lines=4,
                text_size=AppStyles.FONT_SIZE_BODY_SM,
                on_change=lambda e, decision_id=decision.decision_id: on_draft_change(
                    decision_id,
                    e.control.value or "",
                ),
            )
        )
        rows.append(
            ft.TextButton(
                content=ft.Text(
                    I18n.get("agent_research_submitting") if submitting else I18n.get("agent_research_submit"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                ),
                disabled=submitting,
                on_click=lambda _e, d=decision: on_submit(d.decision_id, d.execution_id, d.round_id),
            )
        )
    else:
        rows.append(hint_text("agent_research_decision_dedicated"))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def build_pending_row(item: PendingAnswerView, *, on_retry_delivery: Callable[[str], None]) -> ft.Control:
    """跨会话待处理项（记录/交付/消费）：只读事实 + 未投递项的可重试入口。"""

    rows: list[ft.Control] = [
        ft.Text(
            I18n.get("agent_research_pending_item_fmt").format(
                session=item.session_title or I18n.get("agent_research_pending_unknown_session"),
                kind=item.decision_kind,
                revision=item.revision,
            ),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_PRIMARY,
        )
    ]
    if item.question_text:
        rows.append(
            ft.Text(
                item.question_text,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    states: list[str] = []
    if item.delivery_state is not None:
        states.append(I18n.get("agent_research_delivery_state_fmt").format(state=item.delivery_state))
    if item.consumption_state is not None:
        states.append(I18n.get("agent_research_consumption_state_fmt").format(state=item.consumption_state))
    if states:
        rows.append(ft.Text(" · ".join(states), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT))
    if item.retryable_delivery:
        rows.append(
            ft.TextButton(
                content=ft.Text(I18n.get("agent_research_delivery_retry"), size=AppStyles.FONT_SIZE_BODY_SM),
                on_click=lambda _e, decision_id=item.decision_id: on_retry_delivery(decision_id),
            )
        )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def build_pending_overview(
    state: ResearchObserverState,
    *,
    on_retry_delivery: Callable[[str], None],
) -> ft.Control:
    """跨会话待处理概览（P1-05）：记录/交付/消费三段分列，徽标范围如实标注。

    当前会话的待答复决定已逐卡渲染，本段只补其他会话的待答复项；
    交付/消费阶段三项均列出（含当前会话），保证「已保存未投递」不提前消失。
    """

    scope_key = (
        "agent_research_pending_global"
        if state.pending_scope == PENDING_SCOPE_ALL
        else "agent_research_pending_session_only"
    )
    rows: list[ft.Control] = [
        ft.Row(
            [
                section_title(scope_key),
                ft.Text(
                    I18n.get("agent_research_pending_fmt").format(count=state.pending_count),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    ]
    for stage in PENDING_STAGES:
        items = state.pending_for(stage)
        if stage == "answer":
            items = tuple(item for item in items if not item.belongs_to(state.session_id))
        if not items:
            continue
        rows.append(
            ft.Text(
                I18n.get(_PENDING_STAGE_KEYS[stage]),
                size=AppStyles.FONT_SIZE_CAPTION,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_HINT,
            )
        )
        for item in items:
            rows.append(build_pending_row(item, on_retry_delivery=on_retry_delivery))
    if state.pending_items_truncated:
        rows.append(hint_text("agent_research_pending_truncated"))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def build_decisions_section(
    state: ResearchObserverState,
    *,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str | None], None],
) -> list[ft.Control]:
    """待处理事项（必要决定逐卡）+ 最近一次答复交付状态。"""

    rows: list[ft.Control] = [section_title("agent_research_decisions")]
    if not state.decisions:
        rows.append(hint_text("agent_research_no_decisions"))
    for decision in state.decisions:
        rows.append(
            build_decision_card(
                decision,
                draft=state.draft_for(decision.decision_id),
                submitting=state.submitting,
                on_draft_change=on_draft_change,
                on_submit=on_submit,
            )
        )
    if state.answer_notice_key:
        rows.append(
            ft.Text(
                I18n.get(state.answer_notice_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if state.last_answer_delivery_state:
        rows.append(
            ft.Text(
                I18n.get("agent_research_delivery_state_fmt").format(state=state.last_answer_delivery_state),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if state.last_answer_delivery_reason:
        rows.append(
            ft.Text(
                I18n.get("agent_research_delivery_reason_fmt").format(reason=state.last_answer_delivery_reason),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    return rows
