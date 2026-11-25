"""研究观察面板·主会话与执行树子视图（只读：消息、子任务、会话草稿）。

本模块不含输入组件（首问/答复分别归 composer / decisions），也不 import VM。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    MESSAGE_COPY_EMPTY_KEY,
    MESSAGE_TOOLS_UNAVAILABLE_KEY,
    ExecutionNodeView,
    ResearchObserverState,
    SessionDraftView,
    SessionMessageView,
)
from ui.views.research_observer_widgets import error_text, hint_text, section_title

_ROLE_KEYS = {
    "user": "agent_research_role_user",
    "assistant": "agent_research_role_assistant",
    "tool": "agent_research_role_tool",
    "public_status": "agent_research_role_public_status",
}


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``；未在渲染上下文（单元测试）时返回 None。"""

    try:
        return ft.context.page
    except RuntimeError:
        return None


def _selection_text(event: ft.Event[ft.SelectionArea]) -> str:
    """``SelectionArea.on_change`` 载荷为选中文本（null/非字符串视为空选择）。"""

    data = getattr(event, "data", None)
    return data if isinstance(data, str) else ""


@dataclass(frozen=True)
class MessageActionHandlers:
    """消息修订/分支/局部复制的视图回调（VM 命令经组合根注入，视图不 import VM 命令）。"""

    on_begin_edit: Callable[[str], None]
    on_edit_text: Callable[[str], None]
    on_submit_edit: Callable[[], None]
    on_cancel_edit: Callable[[], None]
    on_branch: Callable[[str], None]
    on_selection: Callable[[str, str], None]
    on_copy: Callable[[], str | None]
    on_copy_summary: Callable[[], str | None]
    on_copy_done: Callable[[], None]
    on_copy_failed: Callable[[], None]


def _role_label(role: str | None) -> str:
    """消息角色文案（未知角色显示原值，不伪造成用户/助手）。"""

    if role is None:
        return I18n.get("agent_research_role_unknown")
    key = _ROLE_KEYS.get(role)
    return I18n.get(key) if key is not None else role


def build_message_row(
    message: SessionMessageView,
    *,
    anchor: bool = False,
    state: ResearchObserverState | None = None,
    handlers: MessageActionHandlers | None = None,
) -> ft.Control:
    """单条主会话消息（正文未随首屏返回时如实标注，不显示空正文）。

    ``anchor`` 为返回现场时的滚动锚点目标（以控件 key 标识该消息，供视图恢复定位）。
    ``state`` / ``handlers`` 提供消息修订、前缀分支与局部复制工具栏；二者缺省时
    只渲染只读正文（兼容既有调用方）。空选择复制按钮禁用并显示原因，剪贴板失败
    如实报错，不伪装成功。
    """

    header_children: list[ft.Control] = [
        ft.Text(_role_label(message.role), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
        ft.Text(message.occurred_at, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
    ]
    rows: list[ft.Control] = []
    if state is not None and handlers is not None:
        unavailable_key = state.message_tools_unavailable_reason_key or MESSAGE_TOOLS_UNAVAILABLE_KEY
        if message.role == "user":
            header_children.append(
                ft.IconButton(
                    icon=ft.Icons.EDIT,
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get(
                        "agent_research_message_edit" if state.message_edit_available else unavailable_key
                    ),
                    disabled=not state.message_edit_available,
                    on_click=lambda _e, mid=message.message_id: handlers.on_begin_edit(mid),
                )
            )
        header_children.append(
            ft.IconButton(
                icon=ft.Icons.CALL_SPLIT,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get(
                    "agent_research_message_branch" if state.message_branch_available else unavailable_key
                ),
                disabled=not state.message_branch_available,
                on_click=lambda _e, mid=message.message_id: handlers.on_branch(mid),
            )
        )
    rows.append(ft.Row(header_children, spacing=AppStyles.SPACING_SM))
    if state is not None and state.branch_pending_message_id == message.message_id:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))

    if state is not None and handlers is not None and state.edit_message_id == message.message_id:
        edit_error_rows: list[ft.Control] = []
        if state.edit_error_key:
            edit_error_rows.append(error_text(I18n.get(state.edit_error_key)))
        if state.edit_error_code:
            edit_error_rows.append(error_text(I18n.get("agent_research_error_fmt").format(code=state.edit_error_code)))
        rows.append(
            ft.Column(
                [
                    ft.TextField(
                        value=state.edit_text,
                        tooltip=I18n.get("agent_research_message_edit"),
                        multiline=True,
                        min_lines=2,
                        max_lines=6,
                        text_size=AppStyles.FONT_SIZE_BODY_SM,
                        disabled=state.edit_pending,
                        on_change=lambda e: handlers.on_edit_text(e.control.value or ""),
                    ),
                    ft.Row(
                        [
                            ft.TextButton(
                                content=ft.Text(
                                    I18n.get("agent_research_message_edit_submit"),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                ),
                                icon=ft.Icons.SEND,
                                disabled=state.edit_pending,
                                on_click=lambda _e: handlers.on_submit_edit(),
                            ),
                            ft.TextButton(
                                content=ft.Text(
                                    I18n.get("agent_research_message_edit_cancel"),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                ),
                                disabled=state.edit_pending,
                                on_click=lambda _e: handlers.on_cancel_edit(),
                            ),
                        ],
                        spacing=AppStyles.SPACING_XS,
                    ),
                    *edit_error_rows,
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
    elif message.body_available:
        body: ft.Control = ft.Text(
            message.body_text or "",
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_PRIMARY,
        )
        if state is not None and handlers is not None:
            bound_handlers = handlers

            def _on_selection(event: ft.Event[ft.SelectionArea], *, mid: str = message.message_id) -> None:
                bound_handlers.on_selection(mid, _selection_text(event))

            body = ft.SelectionArea(content=body, on_change=_on_selection)
        rows.append(body)
    else:
        rows.append(
            ft.Text(
                I18n.get("agent_research_message_body_unloaded"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )

    if state is not None and handlers is not None and state.copy_toolbar_message_id == message.message_id:
        rows.extend(_copy_toolbar_rows(state, handlers))

    return ft.Container(
        key=f"agent_research_anchor_{message.message_id}" if anchor else None,
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS),
        padding=ft.Padding.symmetric(vertical=AppStyles.SPACING_XS),
    )


async def _write_clipboard(text: str) -> bool:
    """写入系统剪贴板；无渲染上下文或写入异常返回 ``False``（不伪装成功）。"""

    page = _get_page()
    if page is None:
        return False
    clipboard = ft.Clipboard()
    page.services.append(clipboard)
    try:
        await clipboard.set(text)
    except asyncio.CancelledError:
        raise
    except Exception:
        return False
    finally:
        try:
            page.services.remove(clipboard)
        except ValueError:
            pass
    return True


def _copy_toolbar_rows(state: ResearchObserverState, handlers: MessageActionHandlers) -> list[ft.Control]:
    """局部复制工具栏：复制/复制摘要；空选择禁用并显示原因，结果如实分列。"""

    selected = state.copy_selection_available()

    async def _copy(_e: ft.ControlEvent | None = None) -> None:
        text = handlers.on_copy()
        if text is None:
            return  # VM 已给出拒绝原因（空选择）
        if await _write_clipboard(text):
            handlers.on_copy_done()
        else:
            handlers.on_copy_failed()

    async def _copy_summary(_e: ft.ControlEvent | None = None) -> None:
        text = handlers.on_copy_summary()
        if text is None:
            return
        if await _write_clipboard(text):
            handlers.on_copy_done()
        else:
            handlers.on_copy_failed()

    toolbar: list[ft.Control] = [
        ft.TextButton(
            content=ft.Text(I18n.get("agent_research_message_copy"), size=AppStyles.FONT_SIZE_CAPTION),
            icon=ft.Icons.CONTENT_COPY,
            disabled=not selected,
            on_click=_copy,
        ),
        ft.TextButton(
            content=ft.Text(I18n.get("agent_research_message_copy_summary"), size=AppStyles.FONT_SIZE_CAPTION),
            icon=ft.Icons.SHORT_TEXT,
            disabled=not selected,
            on_click=_copy_summary,
        ),
    ]
    rows: list[ft.Control] = [ft.Row(toolbar, spacing=AppStyles.SPACING_XS)]
    if not selected:
        rows.append(hint_text(MESSAGE_COPY_EMPTY_KEY))
    if state.copy_notice_key:
        rows.append(
            ft.Text(
                I18n.get(state.copy_notice_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.PRIMARY,
            )
        )
    if state.copy_error_key:
        rows.append(error_text(I18n.get(state.copy_error_key)))
        if state.copy_error_code:
            rows.append(error_text(I18n.get("agent_research_error_fmt").format(code=state.copy_error_code)))
    return rows


def build_progress_section(state: ResearchObserverState) -> list[ft.Control]:
    """首层进度四问（在做什么 / 等什么 / 已保存 / 下一步）。

    取值均为 i18n key；数据未读到时会话层给出显式「未知」，不显示为已完成（R21）。
    """

    pairs = (
        ("agent_research_progress_doing", state.progress.doing_key),
        ("agent_research_progress_waiting", state.progress.waiting_key),
        ("agent_research_progress_saved", state.progress.saved_key),
        ("agent_research_progress_next", state.progress.next_key),
    )
    rows: list[ft.Control] = [section_title("agent_research_progress")]
    for label_key, value_key in pairs:
        rows.append(
            ft.Row(
                [
                    ft.Text(
                        I18n.get(label_key),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_HINT,
                    ),
                    ft.Text(
                        I18n.get(value_key),
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                ],
                spacing=AppStyles.SPACING_SM,
            )
        )
    return rows


def build_stream_section(state: ResearchObserverState) -> list[ft.Control]:
    """增量流式呈现（真实模型片段与合成/占位片段分列，不混排为结论正文）。"""

    if not state.stream_fragments:
        return []
    rows: list[ft.Control] = [section_title("agent_research_stream")]
    if state.stream_text:
        rows.append(
            ft.Text(
                state.stream_text,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    else:
        rows.append(hint_text("agent_research_stream_no_model_text"))
    if state.stream_synthetic_visible:
        rows.append(
            ft.Text(
                I18n.get("agent_research_stream_synthetic_badge"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.WARNING,
            )
        )
    return rows


def build_messages_section(
    state: ResearchObserverState,
    *,
    handlers: MessageActionHandlers | None = None,
) -> list[ft.Control]:
    """主会话消息区（只读；正文未载入如实标注）。

    进度四问与增量流式块随消息区一并渲染（同一分区，避免调用方多处分发）。
    ``handlers`` 提供消息修订/分支/局部复制回调；缺省时保持纯只读。
    """

    rows: list[ft.Control] = [*build_progress_section(state), *build_stream_section(state)]
    rows.append(section_title("agent_research_messages"))
    if state.session_parent_id is not None:
        rows.append(
            ft.Text(
                I18n.get("agent_research_branch_origin_fmt").format(
                    parent=state.session_parent_id,
                    message=state.session_branch_source_message_id or "-",
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if state.branch_error_key:
        rows.append(error_text(I18n.get(state.branch_error_key)))
    if state.branch_error_code:
        rows.append(error_text(I18n.get("agent_research_error_fmt").format(code=state.branch_error_code)))
    if not state.messages:
        rows.append(hint_text("agent_research_no_messages"))
    anchor = state.restored_scroll_anchor
    for message in state.messages:
        rows.append(
            build_message_row(
                message,
                anchor=anchor is not None and message.message_id == anchor,
                state=state,
                handlers=handlers,
            )
        )
    if state.messages_has_more:
        rows.append(hint_text("agent_research_more_items"))
    return rows


def build_execution_row(
    node: ExecutionNodeView,
    *,
    collapsed: bool,
    selected: bool,
    restored_selected: bool,
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
) -> ft.Control:
    """子任务树节点：只读进度 + 展开/收起 + 切换观察位置（均不控制执行）。

    ``restored_selected`` 标记返回父级时恢复的「上次选中的子执行」（现场提示，
    不代表正在执行或正在跟随）。
    """

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
    if restored_selected:
        leading.append(
            ft.Icon(
                ft.Icons.HISTORY,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_HINT,
                tooltip=I18n.get("agent_research_child_last_selected"),
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


def build_execution_section(
    state: ResearchObserverState,
    *,
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
) -> list[ft.Control]:
    """子任务树区（父执行 → 子执行；观察位置可切换，均不控制执行）。"""

    execution_header: list[ft.Control] = [section_title("agent_research_executions")]
    if state.selected_execution_id is not None:
        execution_header.append(
            ft.IconButton(
                icon=ft.Icons.ACCOUNT_TREE,
                icon_size=AppStyles.FONT_SIZE_TITLE,
                tooltip=I18n.get("agent_research_observe_all"),
                on_click=lambda _e: on_select_execution(None),
            )
        )
    rows: list[ft.Control] = [ft.Row(execution_header, alignment=ft.MainAxisAlignment.SPACE_BETWEEN)]
    if state.observation_path:
        rows.append(
            ft.Text(
                I18n.get("agent_research_observation_path_fmt").format(
                    path=" › ".join(state.observation_path),
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
        # 子会话是只读视图：观察位置切换不改执行；返回父级用同一导航入口。
        rows.append(hint_text("agent_research_child_readonly"))
    if state.return_to_execution_id is not None:
        rows.append(
            ft.TextButton(
                content=ft.Text(
                    I18n.get("agent_research_observation_back"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                ),
                on_click=lambda _e: on_select_execution(state.return_to_execution_id),
            )
        )
    if state.selected_execution_id is not None:
        rows.append(
            ft.Text(
                I18n.get("agent_research_observing_fmt").format(execution_id=state.selected_execution_id),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if not state.execution_nodes:
        rows.append(hint_text("agent_research_no_executions"))
    for node in state.execution_nodes:
        rows.append(
            build_execution_row(
                node,
                collapsed=state.is_collapsed(node.execution_id),
                selected=state.selected_execution_id == node.execution_id,
                restored_selected=state.restored_child_selection == node.execution_id,
                on_toggle_execution=on_toggle_execution,
                on_select_execution=on_select_execution,
            )
        )
    return rows


def build_session_draft_card(
    state: ResearchObserverState,
    *,
    session_id: str,
    on_load_draft: Callable[[str, int], None],
) -> ft.Control:
    """会话草稿只读卡（R2-I23）：读取入口绑定本卡会话/稿版；不发送、不建研究。"""

    revision = state.session_draft_revision
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
                on_click=lambda _e, sid=session_id, rev=revision: on_load_draft(sid, rev),
            )
        )
    rows: list[ft.Control] = [ft.Row(header, spacing=AppStyles.SPACING_SM)]
    if state.draft_pending:
        rows.append(hint_text("agent_research_draft_loading"))
    if state.draft_error_code:
        rows.append(error_text(I18n.get("agent_research_draft_error_fmt").format(code=state.draft_error_code)))
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
