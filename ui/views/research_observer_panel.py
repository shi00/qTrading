"""研究观察面板（非模态）组合根：会话列表 / 首问 / 主会话 / 成果 / 待处理决定。

分区已按职责拆到同级子视图模块，本文件只做组合与面板框架（标题区/通知/关闭），
便于后续工作流并行编辑不同分区而不互踩：

- ``research_observer_session_list``：会话列表与元数据动作入口（W-14）
- ``research_observer_composer``：首问输入、MCP 范围、计划预览、主回合直答
- ``research_observer_conversation``：主会话消息、执行树、会话草稿卡
- ``research_observer_artifacts``：成果卡/正文按需读取/比较入口占位
- ``research_observer_decisions``：必要决定卡、跨会话待处理概览
- ``research_observer_controls``：执行控制表（暂停/继续/终止/未知结案/重试/撤回）
- ``research_observer_widgets``：分区标题与卡片容器等共享小构件
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import ControlActionView, ResearchObserverState
from ui.views.research_observer_artifacts import build_artifacts_section
from ui.views.research_observer_composer import build_answer_preview, build_composer, build_plan_preview
from ui.views.research_observer_controls import build_control_section
from ui.views.research_observer_conversation import (
    MessageActionHandlers,
    build_execution_section,
    build_messages_section,
    build_session_draft_card,
    build_session_model_bar,
)
from ui.views.research_observer_decisions import build_decisions_section, build_pending_overview
from ui.views.research_observer_materials import build_materials_section
from ui.views.research_observer_session_list import build_session_list

PANEL_WIDTH = 340


def _ignore_text(_value: str) -> None:
    pass


def _ignore_optional_text(_value: str | None) -> None:
    pass


def _ignore_mode(_value: str) -> None:
    pass


def _ignore_click() -> None:
    pass


def _ignore_decision(_decision_id: str) -> None:
    pass


def _ignore_actions(_execution_id: str) -> tuple[ControlActionView, ...]:
    return ()


def _ignore_two_texts(_first: str, _second: str) -> None:
    pass


def _build_body(
    state: ResearchObserverState,
    *,
    on_draft_change: Callable[[str, str], None],
    on_submit: Callable[[str, str, str | None], None],
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
    on_load_artifact: Callable[[str, int, str, str], None],
    on_load_draft: Callable[[str, int], None],
    on_composer_text: Callable[[str], None],
    on_composer_mode: Callable[[str], None],
    on_send: Callable[[], None],
    on_start_plan: Callable[[], None],
    on_retry_delivery: Callable[[str], None] = _ignore_decision,
    on_toggle_mcp_scope: Callable[[str], None] = _ignore_text,
    on_select_model: Callable[[str], None] = _ignore_text,
    on_select_role: Callable[[str], None] = _ignore_text,
    on_select_session_model: Callable[[str], None] = _ignore_text,
    on_clear_session_model: Callable[[], None] = _ignore_click,
    on_add_materials: Callable[[], None] = _ignore_click,
    on_remove_material: Callable[[str], None] = _ignore_text,
    on_select_material_target: Callable[[str | None], None] = _ignore_optional_text,
    on_add_materials_to_session: Callable[[], None] = _ignore_click,
    on_transfer_draft: Callable[[], None] = _ignore_click,
    on_open_session: Callable[[str], None] = _ignore_text,
    on_new_session_title: Callable[[str], None] = _ignore_text,
    on_create_session: Callable[[], None] = _ignore_click,
    on_begin_rename: Callable[[str], None] = _ignore_text,
    on_rename_text: Callable[[str], None] = _ignore_text,
    on_commit_rename: Callable[[], None] = _ignore_click,
    on_cancel_rename: Callable[[], None] = _ignore_click,
    on_pin_session: Callable[[str], None] = _ignore_text,
    on_archive_session: Callable[[str], None] = _ignore_text,
    control_actions: Callable[[str], tuple[ControlActionView, ...]] = _ignore_actions,
    on_control_execution: Callable[[str, str], None] = _ignore_two_texts,
    on_retry_execution: Callable[[str, str], None] = _ignore_two_texts,
    on_prompt_control_reason: Callable[[str, str], None] = _ignore_two_texts,
    on_control_reason_change: Callable[[str], None] = _ignore_text,
    on_control_reason_submit: Callable[[], None] = _ignore_click,
    on_control_reason_cancel: Callable[[], None] = _ignore_click,
    on_toggle_artifact_selection: Callable[[str], None] | None = None,
    on_select_all_artifacts: Callable[[], None] | None = None,
    on_clear_artifact_selection: Callable[[], None] | None = None,
    on_artifact_format_change: Callable[[str], None] | None = None,
    on_artifact_target_path_change: Callable[[str], None] | None = None,
    on_preview_artifact: Callable[[], Awaitable[None]] | None = None,
    on_copy_artifacts: Callable[[], str | None] | None = None,
    on_artifact_copy_done: Callable[[], None] | None = None,
    on_artifact_copy_failed: Callable[[], None] | None = None,
    on_request_export: Callable[[], Awaitable[None]] | None = None,
    on_confirm_export: Callable[[], Awaitable[None]] | None = None,
    on_cancel_export: Callable[[], None] | None = None,
    message_handlers: MessageActionHandlers | None = None,
) -> ft.Control:
    """面板主体：会话列表 / 首问 / 计划预览 / 主会话 / 子任务树 / 成果 / 待处理决定。"""

    if not state.available:
        return ft.Text(
            I18n.get(state.unavailable_reason_key),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        )
    rows: list[ft.Control] = [
        build_session_list(
            state,
            on_open_session=on_open_session,
            on_new_session_title=on_new_session_title,
            on_create_session=on_create_session,
            on_begin_rename=on_begin_rename,
            on_rename_text=on_rename_text,
            on_commit_rename=on_commit_rename,
            on_cancel_rename=on_cancel_rename,
            on_pin_session=on_pin_session,
            on_archive_session=on_archive_session,
        ),
        build_composer(
            state,
            on_composer_text=on_composer_text,
            on_composer_mode=on_composer_mode,
            on_send=on_send,
            on_toggle_mcp_scope=on_toggle_mcp_scope,
            on_select_model=on_select_model,
            on_select_role=on_select_role,
        ),
        build_materials_section(
            state,
            on_add_click=on_add_materials,
            on_remove=on_remove_material,
            on_select_target=on_select_material_target,
            on_add_to_session=on_add_materials_to_session,
            on_transfer=on_transfer_draft,
        ),
    ]
    preview = build_plan_preview(state, on_start_plan=on_start_plan)
    if preview is not None:
        rows.append(preview)
    if state.last_answer_text:
        rows.append(build_answer_preview(state))
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
    session_id = state.session_id
    if session_id is None:
        if not state.loading:
            rows.append(
                ft.Text(
                    I18n.get("agent_research_no_session"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        return ft.Column(rows, spacing=AppStyles.SPACING_SM)
    if state.session_draft_revision > 0:
        rows.append(build_session_draft_card(state, session_id=session_id, on_load_draft=on_load_draft))
    # 会话头的会话级模型选择（切换只对新回合生效；未接线如实显示）。
    rows.append(
        build_session_model_bar(
            state,
            on_select_model=on_select_session_model,
            on_clear_model=on_clear_session_model,
        )
    )
    rows.extend(build_messages_section(state, handlers=message_handlers))
    rows.extend(
        build_execution_section(
            state,
            on_toggle_execution=on_toggle_execution,
            on_select_execution=on_select_execution,
        )
    )
    rows.extend(
        build_control_section(
            state,
            control_actions=control_actions,
            on_control=on_control_execution,
            on_retry=on_retry_execution,
            on_prompt_reason=on_prompt_control_reason,
            on_reason_change=on_control_reason_change,
            on_reason_submit=on_control_reason_submit,
            on_reason_cancel=on_control_reason_cancel,
        )
    )
    rows.extend(
        build_artifacts_section(
            state,
            on_load_artifact=on_load_artifact,
            on_toggle_artifact_selection=on_toggle_artifact_selection,
            on_select_all_artifacts=on_select_all_artifacts,
            on_clear_artifact_selection=on_clear_artifact_selection,
            on_artifact_format_change=on_artifact_format_change,
            on_artifact_target_path_change=on_artifact_target_path_change,
            on_preview_artifact=on_preview_artifact,
            on_copy_artifacts=on_copy_artifacts,
            on_artifact_copy_done=on_artifact_copy_done,
            on_artifact_copy_failed=on_artifact_copy_failed,
            on_request_export=on_request_export,
            on_confirm_export=on_confirm_export,
            on_cancel_export=on_cancel_export,
        )
    )
    rows.extend(build_decisions_section(state, on_draft_change=on_draft_change, on_submit=on_submit))
    # --- 跨会话待处理（P1-05）：记录/交付/消费分列，不只取首条会话 ---
    rows.append(build_pending_overview(state, on_retry_delivery=on_retry_delivery))
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
    on_submit: Callable[[str, str, str | None], None],
    on_toggle_execution: Callable[[str], None],
    on_select_execution: Callable[[str | None], None],
    on_load_artifact: Callable[[str, int, str, str], None],
    on_load_draft: Callable[[str, int], None],
    on_composer_text: Callable[[str], None] = _ignore_text,
    on_composer_mode: Callable[[str], None] = _ignore_mode,
    on_send: Callable[[], None] = _ignore_click,
    on_start_plan: Callable[[], None] = _ignore_click,
    on_retry_delivery: Callable[[str], None] = _ignore_decision,
    on_toggle_mcp_scope: Callable[[str], None] = _ignore_text,
    on_load_mcp_scope: Callable[[], None] = _ignore_click,
    on_select_model: Callable[[str], None] = _ignore_text,
    on_select_role: Callable[[str], None] = _ignore_text,
    on_select_session_model: Callable[[str], None] = _ignore_text,
    on_clear_session_model: Callable[[], None] = _ignore_click,
    on_add_materials: Callable[[], None] = _ignore_click,
    on_remove_material: Callable[[str], None] = _ignore_text,
    on_select_material_target: Callable[[str | None], None] = _ignore_optional_text,
    on_add_materials_to_session: Callable[[], None] = _ignore_click,
    on_transfer_draft: Callable[[], None] = _ignore_click,
    on_load_model_selection: Callable[[], None] = _ignore_click,
    on_refresh_usage: Callable[[], None] = _ignore_click,
    on_refresh_budget: Callable[[], None] = _ignore_click,
    on_load_sessions: Callable[[], None] = _ignore_click,
    on_open_session: Callable[[str], None] = _ignore_text,
    on_new_session_title: Callable[[str], None] = _ignore_text,
    on_create_session: Callable[[], None] = _ignore_click,
    on_begin_rename: Callable[[str], None] = _ignore_text,
    on_rename_text: Callable[[str], None] = _ignore_text,
    on_commit_rename: Callable[[], None] = _ignore_click,
    on_cancel_rename: Callable[[], None] = _ignore_click,
    on_pin_session: Callable[[str], None] = _ignore_text,
    on_archive_session: Callable[[str], None] = _ignore_text,
    control_actions: Callable[[str], tuple[ControlActionView, ...]] = _ignore_actions,
    on_control_execution: Callable[[str, str], None] = _ignore_two_texts,
    on_retry_execution: Callable[[str, str], None] = _ignore_two_texts,
    on_prompt_control_reason: Callable[[str, str], None] = _ignore_two_texts,
    on_control_reason_change: Callable[[str], None] = _ignore_text,
    on_control_reason_submit: Callable[[], None] = _ignore_click,
    on_control_reason_cancel: Callable[[], None] = _ignore_click,
    on_toggle_artifact_selection: Callable[[str], None] | None = None,
    on_select_all_artifacts: Callable[[], None] | None = None,
    on_clear_artifact_selection: Callable[[], None] | None = None,
    on_artifact_format_change: Callable[[str], None] | None = None,
    on_artifact_target_path_change: Callable[[str], None] | None = None,
    on_preview_artifact: Callable[[], Awaitable[None]] | None = None,
    on_copy_artifacts: Callable[[], str | None] | None = None,
    on_artifact_copy_done: Callable[[], None] | None = None,
    on_artifact_copy_failed: Callable[[], None] | None = None,
    on_request_export: Callable[[], Awaitable[None]] | None = None,
    on_confirm_export: Callable[[], Awaitable[None]] | None = None,
    on_cancel_export: Callable[[], None] | None = None,
    message_handlers: MessageActionHandlers | None = None,
) -> ft.Container:
    """非模态研究观察层（右侧公共区；收起只隐藏，不卸载业务页、不清草稿）。"""

    ft.use_effect(lambda: on_load_mcp_scope(), dependencies=[])
    ft.use_effect(lambda: on_load_sessions(), dependencies=[])
    # W-12: 模型/角色目录只读加载；会话或选中执行变化时刷新费用与耗时；正文或
    # 模型选择变化时重算发送前预算（全部经 VM 异步命令，事件处理器内不阻塞）。
    ft.use_effect(lambda: on_load_model_selection(), dependencies=[])
    ft.use_effect(
        lambda: on_refresh_usage(),
        dependencies=[state.session_id, state.selected_execution_id],
    )
    ft.use_effect(
        lambda: on_refresh_budget(),
        dependencies=[state.composer_text, state.model_selection],
    )

    title_rows: list[ft.Control] = [
        ft.Text(
            I18n.get("agent_research_title"),
            size=AppStyles.FONT_SIZE_TITLE,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
    ]
    if state.session_id is not None:
        # 当前会话标题在面板标题区常驻展示（不依赖主体滚动位置）。
        title_rows.append(
            ft.Text(
                I18n.get("agent_research_session_fmt").format(title=state.session_title or state.session_id),
                key="agent_research_session_title",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    header = ft.Row(
        [
            ft.Column(title_rows, spacing=0, expand=True),
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
            on_composer_text=on_composer_text,
            on_composer_mode=on_composer_mode,
            on_send=on_send,
            on_start_plan=on_start_plan,
            on_retry_delivery=on_retry_delivery,
            on_toggle_mcp_scope=on_toggle_mcp_scope,
            on_select_model=on_select_model,
            on_select_role=on_select_role,
            on_select_session_model=on_select_session_model,
            on_clear_session_model=on_clear_session_model,
            on_add_materials=on_add_materials,
            on_remove_material=on_remove_material,
            on_select_material_target=on_select_material_target,
            on_add_materials_to_session=on_add_materials_to_session,
            on_transfer_draft=on_transfer_draft,
            on_open_session=on_open_session,
            on_new_session_title=on_new_session_title,
            on_create_session=on_create_session,
            on_begin_rename=on_begin_rename,
            on_rename_text=on_rename_text,
            on_commit_rename=on_commit_rename,
            on_cancel_rename=on_cancel_rename,
            on_pin_session=on_pin_session,
            on_archive_session=on_archive_session,
            control_actions=control_actions,
            on_control_execution=on_control_execution,
            on_retry_execution=on_retry_execution,
            on_prompt_control_reason=on_prompt_control_reason,
            on_control_reason_change=on_control_reason_change,
            on_control_reason_submit=on_control_reason_submit,
            on_control_reason_cancel=on_control_reason_cancel,
            on_toggle_artifact_selection=on_toggle_artifact_selection,
            on_select_all_artifacts=on_select_all_artifacts,
            on_clear_artifact_selection=on_clear_artifact_selection,
            on_artifact_format_change=on_artifact_format_change,
            on_artifact_target_path_change=on_artifact_target_path_change,
            on_preview_artifact=on_preview_artifact,
            on_copy_artifacts=on_copy_artifacts,
            on_artifact_copy_done=on_artifact_copy_done,
            on_artifact_copy_failed=on_artifact_copy_failed,
            on_request_export=on_request_export,
            on_confirm_export=on_confirm_export,
            on_cancel_export=on_cancel_export,
            message_handlers=message_handlers,
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
