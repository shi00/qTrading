"""ResearchObserverPanel — 壳层公共研究观察层（非模态，检视 P02）。

UX §3.1/P05/P10：研究观察是壳层内的非模态工作区，原业务页常驻不销毁。
本组件只渲染 ``ResearchObserverViewModel.state`` 并转发显式点击：

- 收起/刷新/答复草稿/提交全部由调用方（AppLayout）注入的显式回调处理；
  组件不 import VM、不拼装服务请求、不感知页面。
- 组件不绑定任何键盘批准路径：答复提交只能由用户对具体决定显式点击按钮，
  旧 Dialog / IME 的 Enter 不会落到新决定（仲裁见 ``ui.agent_decision_arbiter``）。
- 授权类决定（data_consent / tool_grant）只展示，提示走专用授权界面。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import PendingDecision, ResearchObserverState

PANEL_WIDTH = 340


def _build_decision_card(
    decision: PendingDecision,
    *,
    draft: str,
    submitting: bool,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str], None],
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
                value=draft,
                hint_text=I18n.get("agent_research_answer_hint"),
                tooltip=I18n.get("agent_research_answer_label"),
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
                on_click=lambda _e, decision_id=decision.decision_id: on_submit(decision_id),
            )
        )
    else:
        rows.append(
            ft.Text(
                I18n.get("agent_research_decision_dedicated"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_body(
    state: ResearchObserverState,
    *,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str], None],
) -> ft.Control:
    """面板主体：不可用/加载/空态/会话与决定（未知如实显示，不伪造数据）。"""

    if not state.available:
        return ft.Text(
            I18n.get(state.unavailable_reason_key),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        )
    rows: list[ft.Control] = []
    if state.loading:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    if state.error_key:
        rows.append(
            ft.Text(
                I18n.get(state.error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.last_error_code:
        rows.append(
            ft.Text(
                I18n.get("agent_research_error_fmt").format(code=state.last_error_code),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.session_id is None:
        if not state.loading:
            rows.append(
                ft.Text(
                    I18n.get("agent_research_no_session"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        return ft.Column(rows, spacing=AppStyles.SPACING_SM)

    rows.append(
        ft.Text(
            I18n.get("agent_research_session_fmt").format(title=state.session_title or state.session_id),
            size=AppStyles.FONT_SIZE_BODY,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
    )
    for execution_id, display_state in state.executions:
        rows.append(
            ft.Text(
                f"{display_state} · {execution_id}",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    rows.append(
        ft.Text(
            I18n.get("agent_research_decisions"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
    )
    if not state.decisions:
        rows.append(
            ft.Text(
                I18n.get("agent_research_no_decisions"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    for decision in state.decisions:
        rows.append(
            _build_decision_card(
                decision,
                draft=state.draft_for(decision.decision_id),
                submitting=state.submitting,
                on_draft_change=on_draft_change,
                on_submit=on_submit,
            )
        )
    if state.last_updated_at:
        rows.append(
            ft.Text(
                I18n.get("agent_research_last_update_fmt").format(time=state.last_updated_at),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return ft.Column(rows, spacing=AppStyles.SPACING_SM, scroll=ft.ScrollMode.AUTO, expand=True)


@ft.component
def ResearchObserverPanel(
    state: ResearchObserverState,
    notice: str | None,
    on_close: Callable[[], None],
    on_refresh: Callable[[], None],
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str], None],
) -> ft.Container:
    """非模态研究观察层（右侧公共区；收起只隐藏，不卸载业务页、不清草稿）。"""

    header = ft.Row(
        [
            ft.Text(
                I18n.get("agent_research_title"),
                size=AppStyles.FONT_SIZE_TITLE,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.IconButton(
                icon=ft.Icons.REFRESH,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_refresh"),
                on_click=lambda _e: on_refresh(),
            ),
            ft.IconButton(
                icon=ft.Icons.CLOSE,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_close"),
                on_click=lambda _e: on_close(),
            ),
        ],
        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        spacing=AppStyles.SPACING_SM,
    )
    body: list[ft.Control] = [header]
    if notice:
        body.append(
            ft.Text(
                notice,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    body.append(_build_body(state, on_draft_change=on_draft_change, on_submit=on_submit))
    return ft.Container(
        content=ft.Column(body, spacing=AppStyles.SPACING_SM, expand=True),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_MD,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
        expand=False,
    )
