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
from ui.viewmodels.research_observer_view_model import (
    ExecutionNodeView,
    LoadedArtifactBody,
    PendingDecision,
    ResearchObserverState,
    SessionArtifactView,
    SessionDraftView,
    SessionMessageView,
)

PANEL_WIDTH = 340

_ROLE_KEYS = {
    "user": "agent_research_role_user",
    "assistant": "agent_research_role_assistant",
    "tool": "agent_research_role_tool",
    "public_status": "agent_research_role_public_status",
}

#: 服务端正文不可用原因的已知映射；未知原因按原始码展示（未知即未知，不猜语义）。
_ARTIFACT_UNAVAILABLE_KEYS = {
    "body_not_saved": "agent_research_artifact_body_not_saved",
    "body_cleaned": "agent_research_artifact_body_cleaned",
}


def _role_label(role: str | None) -> str:
    """消息角色文案（未知角色显示原值，不伪造成用户/助手）。"""

    if role is None:
        return I18n.get("agent_research_role_unknown")
    key = _ROLE_KEYS.get(role)
    return I18n.get(key) if key is not None else role


def _section_title(key: str) -> ft.Text:
    return ft.Text(
        I18n.get(key),
        size=AppStyles.FONT_SIZE_BODY_SM,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
    )


def _build_message_row(message: SessionMessageView) -> ft.Control:
    """单条主会话消息（正文未随首屏返回时如实标注，不显示空正文）。"""

    rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(_role_label(message.role), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                ft.Text(message.occurred_at, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
            ],
            spacing=AppStyles.SPACING_SM,
        )
    ]
    if message.body_available:
        rows.append(
            ft.Text(
                message.body_text or "",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    else:
        rows.append(
            ft.Text(
                I18n.get("agent_research_message_body_unloaded"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=ft.Padding.symmetric(vertical=AppStyles.SPACING_XS),
    )


def _build_execution_row(
    node: ExecutionNodeView,
    *,
    collapsed: bool,
    selected: bool,
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
) -> ft.Control:
    """子任务树节点：只读进度 + 展开/收起 + 切换观察位置（均不控制执行）。"""

    leading: list[ft.Control] = []
    if node.has_children:
        leading.append(
            ft.IconButton(
                icon=ft.Icons.EXPAND_MORE if collapsed else ft.Icons.EXPAND_LESS,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_expand" if collapsed else "agent_research_collapse"),
                on_click=lambda _e, execution_id=node.execution_id: on_toggle_execution(execution_id),
            )
        )
    else:
        leading.append(
            ft.Icon(ft.Icons.SUBDIRECTORY_ARROW_RIGHT, size=AppStyles.FONT_SIZE_TITLE, color=AppColors.TEXT_HINT)
        )
    if selected:
        leading.append(
            ft.Icon(
                ft.Icons.VISIBILITY,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.PRIMARY,
                tooltip=I18n.get("agent_research_selected"),
            )
        )
    leading.append(
        ft.IconButton(
            icon=ft.Icons.CENTER_FOCUS_WEAK,
            icon_size=AppStyles.FONT_SIZE_TITLE,
            tooltip=I18n.get("agent_research_observe"),
            on_click=lambda _e, execution_id=node.execution_id: on_select_execution(execution_id),
        )
    )
    details = ft.Column(
        [
            ft.Text(
                node.execution_id,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Text(
                I18n.get("agent_research_execution_progress_fmt").format(
                    state=node.display_state,
                    models=node.model_attempt_count,
                    tools=node.tool_call_count,
                    active_ms=node.active_ms,
                    coverage=node.goal_coverage,
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            ),
        ],
        spacing=AppStyles.SPACING_XS,
        expand=True,
    )
    return ft.Container(
        content=ft.Row(
            [*leading, details], spacing=AppStyles.SPACING_XS, vertical_alignment=ft.CrossAxisAlignment.START
        ),
        padding=ft.Padding.only(left=AppStyles.SPACING_LG * node.depth, top=AppStyles.SPACING_XS),
    )


def _artifact_unavailable_text(reason: str | None) -> str:
    """成果正文不可用文案（已知原因映射 i18n；未知原因原样呈现错误码）。"""

    if reason is None:
        return I18n.get("agent_research_artifact_body_unloaded")
    key = _ARTIFACT_UNAVAILABLE_KEYS.get(reason)
    if key is not None:
        return I18n.get(key)
    return I18n.get("agent_research_artifact_unavailable_fmt").format(reason=reason)


def _build_artifact_card(
    artifact: SessionArtifactView,
    *,
    loaded: LoadedArtifactBody | None,
    loading: bool,
    on_load_artifact: Callable[[str, int, str, str], None],
) -> ft.Control:
    """成果卡：摘要常驻；正文按需只读读取，未载入/不可用如实标注。"""

    header = ft.Row(
        [
            ft.Text(
                f"{artifact.artifact_kind} · v{artifact.artifact_version}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
            ),
            ft.Text(
                artifact.execution_id,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            ),
        ],
        spacing=AppStyles.SPACING_SM,
    )
    rows: list[ft.Control] = [header]
    if artifact.summary:
        rows.append(
            ft.Text(
                artifact.summary,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    if loaded is not None:
        if loaded.body_available:
            if loaded.body_text:
                rows.append(
                    ft.Text(
                        loaded.body_text,
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_PRIMARY,
                    )
                )
            if loaded.truncated:
                rows.append(
                    ft.Text(
                        I18n.get("agent_research_artifact_truncated"),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_HINT,
                    )
                )
        else:
            rows.append(
                ft.Text(
                    _artifact_unavailable_text(loaded.unavailable_reason),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
    elif artifact.body_available:
        # 首屏已携正文：直接展示，不再给「读取正文」入口（避免把已有正文标成未载入）
        rows.append(
            ft.Text(
                artifact.body_text or "",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    else:
        rows.append(
            ft.Text(
                I18n.get("agent_research_artifact_body_unloaded"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
        if loading:
            rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
        else:
            rows.append(
                ft.IconButton(
                    icon=ft.Icons.ARTICLE_OUTLINED,
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("agent_research_artifact_load"),
                    on_click=lambda _e, item=artifact: on_load_artifact(
                        item.artifact_id,
                        item.artifact_version,
                        item.execution_id,
                        item.round_id,
                    ),
                )
            )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_session_draft_card(
    state: ResearchObserverState,
    *,
    on_load_draft: Callable[[], None],
) -> ft.Control:
    """会话草稿只读卡（R2-I23）：读取入口 + 正文/选择/稿版；不发送、不建研究。"""

    header: list[ft.Control] = [
        ft.Text(
            I18n.get("agent_research_draft"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.Text(
            I18n.get("agent_research_draft_revision_fmt").format(revision=state.session_draft_revision),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        ),
    ]
    if state.draft_pending:
        header.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    else:
        header.append(
            ft.IconButton(
                icon=ft.Icons.RESTORE_PAGE,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_draft_load"),
                on_click=lambda _e: on_load_draft(),
            )
        )
    rows: list[ft.Control] = [ft.Row(header, spacing=AppStyles.SPACING_SM)]
    if state.draft_pending:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_loading"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if state.draft_error_code:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_error_fmt").format(code=state.draft_error_code),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    draft = state.session_draft
    if draft is not None:
        rows.extend(_draft_detail_rows(draft))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _draft_detail_rows(draft: SessionDraftView) -> list[ft.Control]:
    """草稿详情（清理/移出/空稿/无稿分列如实标注；正文与选择只读展示）。"""

    def hint(key: str) -> ft.Text:
        return ft.Text(
            I18n.get(key),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        )

    if draft.cleaned:
        return [hint("agent_research_draft_cleaned")]
    if draft.transferred:
        return [hint("agent_research_draft_transferred")]
    if draft.text is None:
        return [hint("agent_research_draft_none")]

    rows: list[ft.Control] = []
    if draft.archived:
        rows.append(hint("agent_research_draft_archived"))
    if draft.is_empty:
        rows.append(hint("agent_research_draft_empty"))
    rows.append(
        ft.Text(
            draft.text if draft.text else I18n.get("agent_research_draft_body_empty"),
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_PRIMARY,
        )
    )
    meta = I18n.get("agent_research_draft_meta_fmt").format(
        mode=draft.mode or "-",
        purpose=draft.purpose or "-",
        research_date=draft.research_date or "-",
        cutoff=draft.cutoff or "-",
    )
    rows.append(ft.Text(meta, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT))
    if draft.sources:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_sources_fmt").format(sources=", ".join(draft.sources)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if draft.skills:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_skills_fmt").format(skills=", ".join(draft.skills)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if draft.mcp_services:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_mcp_fmt").format(services=", ".join(draft.mcp_services)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if draft.customized_fields:
        rows.append(
            ft.Text(
                I18n.get("agent_research_draft_customized_fmt").format(fields=", ".join(draft.customized_fields)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    fragments = (
        ("agent_research_draft_model_selection_fmt", draft.model_selection),
        ("agent_research_draft_send_target_fmt", draft.send_target),
        ("agent_research_draft_object_context_fmt", draft.object_context),
        ("agent_research_draft_role_prompt_fmt", draft.role_prompt_selection),
    )
    for key, value in fragments:
        if value is not None:
            rows.append(
                ft.Text(
                    I18n.get(key).format(value=value),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
    return rows


def _build_body(
    state: ResearchObserverState,
    *,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str], None],
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
    on_load_artifact: Callable[[str, int, str, str], None],
    on_load_draft: Callable[[], None],
) -> ft.Control:
    """面板主体：主会话 / 子任务树 / 成果 / 待处理决定（未知如实显示，不伪造数据）。"""

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
    if state.session_draft_revision > 0:
        rows.append(_build_session_draft_card(state, on_load_draft=on_load_draft))

    # --- 主会话消息（只读；正文未载入如实标注） ---
    rows.append(_section_title("agent_research_messages"))
    if not state.messages:
        rows.append(
            ft.Text(
                I18n.get("agent_research_no_messages"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    for message in state.messages:
        rows.append(_build_message_row(message))
    if state.messages_has_more:
        rows.append(
            ft.Text(
                I18n.get("agent_research_more_items"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )

    # --- 子任务树（父执行 → 子执行；观察位置可切换，均不控制执行） ---
    execution_header: list[ft.Control] = [_section_title("agent_research_executions")]
    if state.selected_execution_id is not None:
        execution_header.append(
            ft.IconButton(
                icon=ft.Icons.ACCOUNT_TREE,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_observe_all"),
                on_click=lambda _e: on_select_execution(None),
            )
        )
    rows.append(ft.Row(execution_header, alignment=ft.MainAxisAlignment.SPACE_BETWEEN))
    if state.selected_execution_id is not None:
        rows.append(
            ft.Text(
                I18n.get("agent_research_observing_fmt").format(execution_id=state.selected_execution_id),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if not state.execution_nodes:
        rows.append(
            ft.Text(
                I18n.get("agent_research_no_executions"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    for node in state.execution_nodes:
        rows.append(
            _build_execution_row(
                node,
                collapsed=state.is_collapsed(node.execution_id),
                selected=state.selected_execution_id == node.execution_id,
                on_toggle_execution=on_toggle_execution,
                on_select_execution=on_select_execution,
            )
        )

    # --- 成果（摘要常驻；正文按需只读读取） ---
    rows.append(_section_title("agent_research_artifacts"))
    if not state.artifacts:
        rows.append(
            ft.Text(
                I18n.get("agent_research_no_artifacts"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    for artifact in state.artifacts:
        key = f"{artifact.artifact_id}:{artifact.artifact_version}"
        rows.append(
            _build_artifact_card(
                artifact,
                loaded=state.artifact_body_for(artifact.artifact_id, artifact.artifact_version),
                loading=key in state.artifact_body_pending_keys,
                on_load_artifact=on_load_artifact,
            )
        )
    if state.artifacts_has_more:
        rows.append(
            ft.Text(
                I18n.get("agent_research_more_items"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )

    # --- 待处理事项（必要决定） ---
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


def _build_decision_card(
    decision: PendingDecision,
    *,
    draft: str,
    submitting: bool,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str], None],
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


@ft.component
def ResearchObserverPanel(
    state: ResearchObserverState,
    notice: str | None,
    on_close: Callable[[], None],
    on_refresh: Callable[[], None],
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str], None],
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
    on_load_artifact: Callable[[str, int, str, str], None],
    on_load_draft: Callable[[], None],
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
    body.append(
        _build_body(
            state,
            on_draft_change=on_draft_change,
            on_submit=on_submit,
            on_toggle_execution=on_toggle_execution,
            on_select_execution=on_select_execution,
            on_load_artifact=on_load_artifact,
            on_load_draft=on_load_draft,
        )
    )
    return ft.Container(
        content=ft.Column(body, spacing=AppStyles.SPACING_SM, expand=True),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_MD,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
        expand=False,
    )
