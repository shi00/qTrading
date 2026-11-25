"""研究历史面板（W-04）：历史目录 / 全局检索 / 空间占用 / 清理确认 / 停止使用来源。

View = f(ViewModel.state)：本模块只渲染 ``ResearchObserverState`` 并转发显式点击
回调，不 import ViewModel/服务层、不拼装请求、不做业务判断。

- 历史命中带摘要与准确地址（正文不在此加载）；读取失败保留既有命中并显式报码。
- 清理必须先预览（冻结集合计数）再显式确认；作业未准备/未接线如实阻断，不提供
  「强行清理」路径；回执 ``retry_pending`` 不渲染为已清除（R21）。
- 停止使用来源只提交用户输入的准确 source/version，回执含落定修订与是否重放。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    HISTORY_SCOPE_FILTERS,
    SESSION_STATUS_FILTERS,
    HistoryHitView,
    ResearchObserverState,
)
from ui.views.research_observer_widgets import error_text, hint_text, section_card, section_title

PANEL_WIDTH = 400

_SCOPE_LABEL_KEYS = {
    "all": "agent_research_history_scope_all",
    "main": "agent_research_history_scope_main",
    "child": "agent_research_history_scope_child",
}

_STATUS_LABEL_KEYS = {
    "all": "agent_research_history_status_all",
    "archived": "agent_research_history_status_archived",
    "cleaned": "agent_research_history_status_cleaned",
}


def _caption(text: str, *, color: str = AppColors.TEXT_HINT) -> ft.Text:
    return ft.Text(text, size=AppStyles.FONT_SIZE_CAPTION, color=color)


def _filter_row(
    values: tuple[str, ...],
    labels: dict[str, str],
    selected: str,
    on_select: Callable[[str], None],
) -> ft.Row:
    """筛选按钮组：当前值高亮（显式点击，无隐式键盘路径）。"""

    buttons: list[ft.Control] = []
    for value in values:
        button_cls = ft.FilledButton if value == selected else ft.OutlinedButton
        buttons.append(
            button_cls(
                content=ft.Text(I18n.get(labels[value]), size=AppStyles.FONT_SIZE_CAPTION),
                on_click=lambda _e, selected_value=value: on_select(selected_value),
            )
        )
    return ft.Row(buttons, spacing=AppStyles.SPACING_XS, wrap=True)


def _build_hit_row(hit: HistoryHitView, *, on_open_session: Callable[[str], None]) -> ft.Control:
    """单条历史命中：摘要/类型/时间 + 准确定位 + 打开原会话入口。"""

    summary = hit.summary_text or I18n.get("agent_research_history_hit_no_summary")
    return ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    summary,
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_PRIMARY,
                ),
                _caption(
                    I18n.get("agent_research_history_hit_meta_fmt").format(
                        kind=hit.hit_kind,
                        time=hit.created_at,
                        role=hit.role or "-",
                    )
                ),
                _caption(hit.location),
                ft.TextButton(
                    content=ft.Text(I18n.get("agent_research_history_open"), size=AppStyles.FONT_SIZE_CAPTION),
                    on_click=lambda _e, session_id=hit.session_id: on_open_session(session_id),
                ),
            ],
            spacing=0,
        ),
        padding=AppStyles.SPACING_XS,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.BORDER),
    )


def _build_usage_card(state: ResearchObserverState, *, on_refresh_usage: Callable[[], None]) -> ft.Control:
    """空间占用摘要：计数口径；字节用量未知显式标注，不填 0（R21）。"""

    usage = state.usage
    rows: list[ft.Control] = [
        ft.Row(
            [
                section_title("agent_research_history_usage_title"),
                ft.IconButton(
                    icon=ft.Icons.REFRESH,
                    icon_size=AppStyles.FONT_SIZE_BODY_SM,
                    tooltip=I18n.get("agent_research_usage_refresh"),
                    on_click=lambda _e: on_refresh_usage(),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    ]
    if usage is None:
        rows.append(hint_text("agent_research_usage_not_loaded"))
        return section_card(rows)
    truncated = I18n.get("agent_research_usage_truncated_suffix")
    rows.extend(
        [
            _caption(
                I18n.get("agent_research_usage_sessions_fmt").format(
                    count=usage.sessions_listed,
                    suffix=truncated if usage.sessions_truncated else "",
                )
            ),
            _caption(I18n.get("agent_research_usage_executions_fmt").format(count=usage.executions)),
            _caption(
                I18n.get("agent_research_usage_messages_fmt").format(
                    count=usage.messages,
                    suffix=truncated if usage.messages_truncated else "",
                )
            ),
            _caption(
                I18n.get("agent_research_usage_artifacts_fmt").format(
                    count=usage.artifacts,
                    suffix=truncated if usage.artifacts_truncated else "",
                )
            ),
            _caption(
                I18n.get("agent_research_usage_lifecycle_fmt").format(
                    archived=usage.archived_sessions,
                    cleaned=usage.cleaned_sessions,
                )
            ),
            _caption(
                I18n.get("agent_research_usage_bytes_unknown")
                if usage.exact_bytes is None
                else I18n.get("agent_research_usage_bytes_fmt").format(bytes=usage.exact_bytes)
            ),
        ]
    )
    return section_card(rows)


def _build_cleanup_card(
    state: ResearchObserverState,
    *,
    on_begin_cleanup: Callable[[], None],
    on_confirm_cleanup: Callable[[], None],
    on_cancel_cleanup: Callable[[], None],
) -> ft.Control:
    """清理确认：预览 → 显式确认 → 回执；未准备/未接线如实阻断。"""

    rows: list[ft.Control] = [section_title("agent_research_cleanup_title")]
    if not state.cleanup_available:
        rows.append(hint_text("agent_research_cleanup_unavailable"))
        if state.cleanup_unavailable_reason:
            rows.append(
                _caption(I18n.get("agent_research_cleanup_reason_fmt").format(code=state.cleanup_unavailable_reason))
            )
    if state.cleanup_error_key:
        rows.append(error_text(I18n.get(state.cleanup_error_key)))
    if state.cleanup_error_code and state.cleanup_error_key is None:
        rows.append(error_text(I18n.get("agent_research_cleanup_error_fmt").format(code=state.cleanup_error_code)))
    preview = state.cleanup_preview
    if preview is not None:
        rows.append(
            _caption(
                I18n.get("agent_research_cleanup_preview_fmt").format(
                    phase=preview.phase,
                    executions=preview.exact_count,
                    sessions=preview.session_count,
                    assets=preview.asset_count,
                    revision=preview.expected_revision,
                )
            )
        )
        rows.append(
            ft.Row(
                [
                    ft.TextButton(
                        content=ft.Text(I18n.get("agent_research_cleanup_confirm"), size=AppStyles.FONT_SIZE_CAPTION),
                        disabled=state.cleanup_pending or preview.verified,
                        on_click=lambda _e: on_confirm_cleanup(),
                    ),
                    ft.TextButton(
                        content=ft.Text(I18n.get("agent_research_cleanup_cancel"), size=AppStyles.FONT_SIZE_CAPTION),
                        disabled=state.cleanup_pending,
                        on_click=lambda _e: on_cancel_cleanup(),
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
    else:
        rows.append(
            ft.TextButton(
                content=ft.Text(I18n.get("agent_research_cleanup_preview"), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=state.cleanup_pending or state.session_id is None,
                on_click=lambda _e: on_begin_cleanup(),
            )
        )
    receipt = state.cleanup_receipt
    if receipt is not None:
        rows.append(
            _caption(
                I18n.get(
                    "agent_research_cleanup_receipt_fmt" if receipt.verified else "agent_research_cleanup_retry_fmt"
                ).format(job=receipt.cleanup_job_id, phase=receipt.phase, replayed=receipt.replayed)
            )
        )
    if state.cleanup_pending:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    return section_card(rows)


def _build_stop_source_card(
    state: ResearchObserverState,
    *,
    on_stop_source_id: Callable[[str], None],
    on_stop_source_version: Callable[[str], None],
    on_stop_using: Callable[[], None],
) -> ft.Control:
    """停止使用来源：准确 source/version 输入 + 回执（禁用先胜、幂等重放）。"""

    rows: list[ft.Control] = [
        section_title("agent_research_stop_source_title"),
        ft.TextField(
            key="agent_research_stop_source_id",
            value=state.stop_source_id,
            label=I18n.get("agent_research_stop_source_id_label"),
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            dense=True,
            disabled=state.stop_source_pending,
            on_change=lambda e: on_stop_source_id(e.control.value or ""),
        ),
        ft.TextField(
            key="agent_research_stop_source_version",
            value=state.stop_source_version,
            label=I18n.get("agent_research_stop_source_version_label"),
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            dense=True,
            disabled=state.stop_source_pending,
            on_change=lambda e: on_stop_source_version(e.control.value or ""),
        ),
        ft.TextButton(
            content=ft.Text(I18n.get("agent_research_stop_source_submit"), size=AppStyles.FONT_SIZE_CAPTION),
            disabled=state.stop_source_pending,
            on_click=lambda _e: on_stop_using(),
        ),
    ]
    if state.stop_source_error_key:
        rows.append(error_text(I18n.get(state.stop_source_error_key)))
    if state.stop_source_error_code:
        rows.append(
            error_text(I18n.get("agent_research_stop_source_error_fmt").format(code=state.stop_source_error_code))
        )
    receipt = state.stop_source_receipt
    if receipt is not None:
        rows.append(
            _caption(
                I18n.get("agent_research_stop_source_receipt_fmt").format(
                    source=receipt.source_id,
                    version=receipt.source_version,
                    revision=receipt.disabled_revision,
                    at=receipt.disabled_at,
                )
            )
        )
        if receipt.already_disabled:
            rows.append(_caption(I18n.get("agent_research_stop_source_replayed")))
    if state.stop_source_pending:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    return section_card(rows)


def build_history_panel(
    state: ResearchObserverState,
    *,
    on_close: Callable[[], None],
    on_query_text: Callable[[str], None],
    on_scope_filter: Callable[[str], None],
    on_status_filter: Callable[[str], None],
    on_search: Callable[[], None],
    on_load_more: Callable[[], None],
    on_refresh_usage: Callable[[], None],
    on_open_session: Callable[[str], None],
    on_begin_cleanup: Callable[[], None],
    on_confirm_cleanup: Callable[[], None],
    on_cancel_cleanup: Callable[[], None],
    on_stop_source_id: Callable[[str], None],
    on_stop_source_version: Callable[[str], None],
    on_stop_using: Callable[[], None],
) -> ft.Container:
    """历史面板（右侧独立区；动作全部显式点击，不隐式派发研究）。"""

    rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(
                    I18n.get("agent_research_history_title"),
                    size=AppStyles.FONT_SIZE_XL,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    icon_size=AppStyles.FONT_SIZE_BODY_SM,
                    tooltip=I18n.get("common_close"),
                    on_click=lambda _e: on_close(),
                ),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    ]
    if not state.available:
        rows.append(hint_text(state.unavailable_reason_key))
        rows.append(_build_usage_card(state, on_refresh_usage=on_refresh_usage))
        return ft.Container(
            content=ft.Column(rows, spacing=AppStyles.SPACING_SM, scroll=ft.ScrollMode.AUTO),
            width=PANEL_WIDTH,
            padding=AppStyles.SPACING_SM,
            bgcolor=AppColors.SURFACE,
            border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
        )

    rows.append(
        ft.Row(
            [
                ft.TextField(
                    key="agent_research_history_query",
                    value=state.history_query,
                    label=I18n.get("agent_research_history_query_label"),
                    hint_text=I18n.get("agent_research_history_query_hint"),
                    text_size=AppStyles.FONT_SIZE_BODY_SM,
                    dense=True,
                    on_change=lambda e: on_query_text(e.control.value or ""),
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.SEARCH,
                    icon_size=AppStyles.FONT_SIZE_BODY_SM,
                    tooltip=I18n.get("agent_research_history_search"),
                    disabled=state.history_loading,
                    on_click=lambda _e: on_search(),
                ),
            ],
            spacing=AppStyles.SPACING_XS,
        )
    )
    rows.append(_filter_row(HISTORY_SCOPE_FILTERS, _SCOPE_LABEL_KEYS, state.history_scope_filter, on_scope_filter))
    rows.append(_filter_row(SESSION_STATUS_FILTERS, _STATUS_LABEL_KEYS, state.session_status_filter, on_status_filter))

    sessions = state.filtered_sessions()
    rows.append(section_title("agent_research_sessions"))
    if not sessions:
        rows.append(hint_text("agent_research_session_list_empty"))
    for item in sessions:
        title = item.title or I18n.get("agent_research_session_untitled")
        rows.append(
            ft.Row(
                [
                    ft.Text(
                        title,
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_PRIMARY,
                        expand=True,
                    ),
                    _caption(I18n.get(item.status_key)),
                    ft.TextButton(
                        content=ft.Text(I18n.get("agent_research_session_switch"), size=AppStyles.FONT_SIZE_CAPTION),
                        on_click=lambda _e, session_id=item.session_id: on_open_session(session_id),
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )

    if state.history_loading:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    if state.history_error_code:
        rows.append(error_text(I18n.get("agent_research_history_error_fmt").format(code=state.history_error_code)))
    for hit in state.history_hits:
        rows.append(_build_hit_row(hit, on_open_session=on_open_session))
    if not state.history_hits and not state.history_loading and not state.history_error_code:
        rows.append(hint_text("agent_research_history_empty"))
    if state.history_has_more:
        rows.append(
            ft.TextButton(
                content=ft.Text(I18n.get("agent_research_history_load_more"), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=state.history_loading,
                on_click=lambda _e: on_load_more(),
            )
        )

    rows.append(_build_usage_card(state, on_refresh_usage=on_refresh_usage))
    rows.append(
        _build_cleanup_card(
            state,
            on_begin_cleanup=on_begin_cleanup,
            on_confirm_cleanup=on_confirm_cleanup,
            on_cancel_cleanup=on_cancel_cleanup,
        )
    )
    rows.append(
        _build_stop_source_card(
            state,
            on_stop_source_id=on_stop_source_id,
            on_stop_source_version=on_stop_source_version,
            on_stop_using=on_stop_using,
        )
    )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_SM, scroll=ft.ScrollMode.AUTO),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
    )


@ft.component
def ResearchHistoryPanel(
    state: ResearchObserverState,
    *,
    on_close: Callable[[], None],
    on_query_text: Callable[[str], None],
    on_scope_filter: Callable[[str], None],
    on_status_filter: Callable[[str], None],
    on_search: Callable[[], None],
    on_load_more: Callable[[], None],
    on_refresh_usage: Callable[[], None],
    on_open_session: Callable[[str], None],
    on_begin_cleanup: Callable[[], None],
    on_confirm_cleanup: Callable[[], None],
    on_cancel_cleanup: Callable[[], None],
    on_stop_source_id: Callable[[str], None],
    on_stop_source_version: Callable[[str], None],
    on_stop_using: Callable[[], None],
) -> ft.Container:
    """声明式入口：纯渲染 + 回调转发（状态由共享 VM 持有）。"""

    return build_history_panel(
        state,
        on_close=on_close,
        on_query_text=on_query_text,
        on_scope_filter=on_scope_filter,
        on_status_filter=on_status_filter,
        on_search=on_search,
        on_load_more=on_load_more,
        on_refresh_usage=on_refresh_usage,
        on_open_session=on_open_session,
        on_begin_cleanup=on_begin_cleanup,
        on_confirm_cleanup=on_confirm_cleanup,
        on_cancel_cleanup=on_cancel_cleanup,
        on_stop_source_id=on_stop_source_id,
        on_stop_source_version=on_stop_source_version,
        on_stop_using=on_stop_using,
    )


__all__ = ["PANEL_WIDTH", "ResearchHistoryPanel", "build_history_panel"]
