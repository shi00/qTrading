"""研究观察面板·会话列表子视图（标题/时间/状态 + 新建/切换/改名/置顶/归档入口）。

本模块只渲染列表与动作入口并转发显式回调；状态（含输入态）全部来自 VM。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import ResearchObserverState, SessionListItemView
from ui.views.research_observer_widgets import error_text, hint_text, section_card, section_title


def build_session_list(
    state: ResearchObserverState,
    *,
    on_open_session: Callable[[str], None],
    on_new_session_title: Callable[[str], None],
    on_create_session: Callable[[], None],
    on_begin_rename: Callable[[str], None],
    on_rename_text: Callable[[str], None],
    on_commit_rename: Callable[[], None],
    on_cancel_rename: Callable[[], None],
    on_pin_session: Callable[[str], None],
    on_archive_session: Callable[[str], None],
) -> ft.Control:
    """会话列表区：新建标题输入、逐会话动作入口、改名编辑行。"""

    pending = state.session_action_pending
    rows: list[ft.Control] = [
        ft.Row(
            [
                section_title("agent_research_sessions"),
                ft.ProgressRing(width=16, height=16, stroke_width=2) if pending else ft.Container(),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        ft.Row(
            [
                ft.TextField(
                    key="agent_research_session_new_input",
                    value=state.new_session_title,
                    label=I18n.get("agent_research_session_new_label"),
                    hint_text=I18n.get("agent_research_session_new_hint"),
                    text_size=AppStyles.FONT_SIZE_BODY_SM,
                    dense=True,
                    disabled=pending,
                    on_change=lambda e: on_new_session_title(e.control.value or ""),
                    expand=True,
                ),
                ft.IconButton(
                    icon=ft.Icons.ADD,
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("agent_research_session_new"),
                    disabled=pending,
                    on_click=lambda _e: on_create_session(),
                ),
            ],
            spacing=AppStyles.SPACING_XS,
        ),
    ]
    if state.session_action_error_code:
        rows.append(
            error_text(I18n.get("agent_research_session_action_error_fmt").format(code=state.session_action_error_code))
        )
    elif state.session_action_error_key:
        rows.append(error_text(I18n.get(state.session_action_error_key)))
    if state.sessions_loading:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    if state.sessions_error_code:
        rows.append(error_text(I18n.get("agent_research_sessions_error_fmt").format(code=state.sessions_error_code)))
    if not state.sessions and not state.sessions_loading and not state.sessions_error_code:
        rows.append(hint_text("agent_research_session_list_empty"))
    for item in state.sessions:
        rows.append(
            _build_session_item(
                item,
                state,
                on_open_session=on_open_session,
                on_begin_rename=on_begin_rename,
                on_rename_text=on_rename_text,
                on_commit_rename=on_commit_rename,
                on_cancel_rename=on_cancel_rename,
                on_pin_session=on_pin_session,
                on_archive_session=on_archive_session,
            )
        )
    return section_card(rows)


def _build_session_item(
    item: SessionListItemView,
    state: ResearchObserverState,
    *,
    on_open_session: Callable[[str], None],
    on_begin_rename: Callable[[str], None],
    on_rename_text: Callable[[str], None],
    on_commit_rename: Callable[[], None],
    on_cancel_rename: Callable[[], None],
    on_pin_session: Callable[[str], None],
    on_archive_session: Callable[[str], None],
) -> ft.Control:
    """单会话卡：标题/时间/状态/当前标记 + 动作入口；改名编辑行就地展开。"""

    current = item.session_id == state.session_id
    pending = state.session_action_pending
    title = item.title or I18n.get("agent_research_session_untitled")
    header = ft.Row(
        [
            ft.Text(
                title,
                size=AppStyles.FONT_SIZE_BODY_SM,
                weight=ft.FontWeight.BOLD,
                color=AppColors.TEXT_PRIMARY,
                expand=True,
            ),
            ft.Text(
                I18n.get(item.status_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            ),
        ],
        spacing=AppStyles.SPACING_XS,
    )
    meta: list[ft.Control] = [
        ft.Text(
            item.updated_at or "-",
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
            expand=True,
        )
    ]
    if item.pinned:
        meta.append(
            ft.Text(
                I18n.get("agent_research_session_pinned"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.PRIMARY,
            )
        )
    actions: list[ft.Control] = []
    if current:
        actions.append(
            ft.Icon(
                ft.Icons.VISIBILITY,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.PRIMARY,
                tooltip=I18n.get("agent_research_session_current"),
            )
        )
    else:
        actions.append(
            ft.TextButton(
                content=ft.Text(I18n.get("agent_research_session_switch"), size=AppStyles.FONT_SIZE_CAPTION),
                disabled=pending,
                on_click=lambda _e, session_id=item.session_id: on_open_session(session_id),
            )
        )
    actions.append(
        ft.IconButton(
            icon=ft.Icons.EDIT_OUTLINED,
            icon_size=AppStyles.FONT_SIZE_TITLE,
            tooltip=I18n.get("agent_research_session_rename"),
            disabled=pending,
            on_click=lambda _e, session_id=item.session_id: on_begin_rename(session_id),
        )
    )
    actions.append(
        ft.IconButton(
            icon=ft.Icons.PUSH_PIN,
            icon_size=AppStyles.FONT_SIZE_TITLE,
            tooltip=I18n.get("agent_research_session_pin"),
            disabled=pending or item.pinned or item.revision is None,
            on_click=lambda _e, session_id=item.session_id: on_pin_session(session_id),
        )
    )
    actions.append(
        ft.IconButton(
            icon=ft.Icons.ARCHIVE_OUTLINED,
            icon_size=AppStyles.FONT_SIZE_TITLE,
            tooltip=I18n.get("agent_research_session_archive"),
            disabled=pending or item.archived or item.revision is None,
            on_click=lambda _e, session_id=item.session_id: on_archive_session(session_id),
        )
    )
    rows: list[ft.Control] = [header, ft.Row(meta, spacing=AppStyles.SPACING_XS), ft.Row(actions, spacing=0)]
    if state.rename_target_session_id == item.session_id:
        rows.append(
            ft.TextField(
                key="agent_research_session_rename_input",
                value=state.rename_text,
                label=I18n.get("agent_research_session_rename_label"),
                text_size=AppStyles.FONT_SIZE_BODY_SM,
                dense=True,
                disabled=pending,
                on_change=lambda e: on_rename_text(e.control.value or ""),
            )
        )
        rows.append(
            ft.Row(
                [
                    ft.TextButton(
                        content=ft.Text(
                            I18n.get("agent_research_session_rename_confirm"), size=AppStyles.FONT_SIZE_CAPTION
                        ),
                        disabled=pending,
                        on_click=lambda _e: on_commit_rename(),
                    ),
                    ft.TextButton(
                        content=ft.Text(
                            I18n.get("agent_research_session_rename_cancel"), size=AppStyles.FONT_SIZE_CAPTION
                        ),
                        disabled=pending,
                        on_click=lambda _e: on_cancel_rename(),
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=AppStyles.SPACING_SM,
        bgcolor=AppColors.BACKGROUND,
        border_radius=4,
        border=ft.Border.all(1, AppColors.PRIMARY if current else AppColors.BORDER),
    )
