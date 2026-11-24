"""研究观察面板·首问输入区（composer / 计划预览 / 主回合直答）。

本模块只渲染首问相关状态并转发显式回调：不 import VM、不拼装服务请求。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    COMPOSER_MODES,
    DispatchReasonView,
    PlanStepView,
    ResearchObserverState,
)
from ui.views.research_observer_widgets import hint_text, section_card, section_title

#: 零派发原因码 → i18n key（未知码回落通用「未派发」文案，不丢原因）。
_DISPATCH_REASON_KEYS: dict[str, str] = {
    "not_wired": "agent_research_dispatch_reason_not_wired",
    "dispatch_closed": "agent_research_dispatch_reason_dispatch_closed",
    "plan_unavailable": "agent_research_dispatch_reason_plan_unavailable",
    "plan_invalid": "agent_research_dispatch_reason_plan_invalid",
    "plan_admission_unconfigured": "agent_research_dispatch_reason_plan_admission_unconfigured",
    "capability_policy_unavailable": "agent_research_dispatch_reason_capability_policy_unavailable",
    "binding_identity_unavailable": "agent_research_dispatch_reason_binding_identity_unavailable",
    "binding_identity_mismatch": "agent_research_dispatch_reason_binding_identity_mismatch",
    "mode_research_disabled": "agent_research_dispatch_reason_mode_research_disabled",
    "not_committed": "agent_research_dispatch_reason_not_committed",
    "submit_failed": "agent_research_dispatch_reason_submit_failed",
}

#: 首问模式文案（三值；直答不是第四模式）。
_MODE_LABEL_KEYS = {
    "discuss": "agent_research_mode_discuss",
    "plan": "agent_research_mode_plan",
    "execute": "agent_research_mode_execute",
}


def _ignore_text(_value: str) -> None:
    """缺省回调（未传入 MCP 范围回调时不绑定动作）。"""


def dispatch_reason_text(reason: DispatchReasonView) -> str:
    """按结构化原因码渲染文案；参数以 ``name=value`` 附注（不做自由文本拼接）。"""

    key = _DISPATCH_REASON_KEYS.get(reason.code, "agent_research_dispatch_zero")
    text = I18n.get(key)
    if reason.params:
        detail = ", ".join(f"{name}={value}" for name, value in reason.params)
        text = I18n.get("agent_research_dispatch_reason_detail_fmt").format(reason=text, detail=detail)
    return text


def build_mcp_scope(state: ResearchObserverState, on_toggle: Callable[[str], None]) -> list[ft.Control]:
    """研究侧 MCP 范围：本次允许的服务子集（空选择 = 不使用 MCP，不解释为全部）。"""

    rows: list[ft.Control] = [
        section_title("agent_research_mcp_scope"),
        hint_text("agent_research_mcp_scope_hint"),
    ]
    if state.mcp_scope_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.mcp_scope_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
        return rows
    if not state.mcp_scope_options:
        rows.append(hint_text("agent_research_mcp_scope_empty"))
        return rows
    for identity_key, label in state.mcp_scope_options:
        rows.append(
            ft.Checkbox(
                label=label,
                value=identity_key in state.mcp_scope_selected,
                disabled=state.composer_pending,
                on_change=lambda _e, key=identity_key: on_toggle(key),
            )
        )
    return rows


def build_composer(
    state: ResearchObserverState,
    *,
    on_composer_text: Callable[[str], None],
    on_composer_mode: Callable[[str], None],
    on_send: Callable[[], None],
    on_toggle_mcp_scope: Callable[[str], None] = _ignore_text,
) -> ft.Control:
    """首问输入区：正文 + 模式 + 发送（发送只经注入回调，组件不拼装服务请求）。

    讨论/计划同样经受理落库；执行零派发时由 ``last_dispatch_reason`` 如实展示
    原因（不把「未派发」显示成「已启动」）。
    """

    rows: list[ft.Control] = [section_title("agent_research_composer")]
    if not state.composer_available:
        rows.append(hint_text("agent_research_composer_unavailable"))
    rows.append(
        ft.TextField(
            # 输入态跟随首问草稿本身，而不是面板位置。
            key="agent_research_composer_input",
            value=state.composer_text,
            label=I18n.get("agent_research_composer_label"),
            hint_text=I18n.get("agent_research_composer_hint"),
            multiline=True,
            min_lines=1,
            max_lines=4,
            text_size=AppStyles.FONT_SIZE_BODY_SM,
            disabled=not state.composer_available or state.composer_pending,
            on_change=lambda e: on_composer_text(e.control.value or ""),
        )
    )
    mode_row: list[ft.Control] = []
    for mode in COMPOSER_MODES:
        selected = state.composer_mode == mode
        button_cls = ft.FilledButton if selected else ft.OutlinedButton
        mode_row.append(
            button_cls(
                content=ft.Text(
                    I18n.get(_MODE_LABEL_KEYS[mode]),
                    size=AppStyles.FONT_SIZE_CAPTION,
                ),
                disabled=not state.composer_available or state.composer_pending,
                on_click=lambda _e, value=mode: on_composer_mode(value),
            )
        )
    rows.append(ft.Row(mode_row, spacing=AppStyles.SPACING_XS, wrap=True))
    if state.mcp_scope_available:
        rows.extend(build_mcp_scope(state, on_toggle_mcp_scope))
    send_label = "agent_research_sending" if state.composer_pending else "agent_research_send"
    rows.append(
        ft.Row(
            [
                ft.FilledButton(
                    content=ft.Text(I18n.get(send_label), size=AppStyles.FONT_SIZE_BODY_SM),
                    disabled=not state.composer_available or state.composer_pending,
                    on_click=lambda _e: on_send(),
                )
            ],
            alignment=ft.MainAxisAlignment.END,
        )
    )
    if state.composer_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.composer_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.last_dispatch_reason:
        rows.append(
            ft.Text(
                dispatch_reason_text(state.last_dispatch_reason),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return section_card(rows)


def build_answer_preview(state: ResearchObserverState) -> ft.Control:
    """主回合直答展示：只显示真实产出，不占位、不伪装已取数。"""

    return section_card(
        [
            section_title("agent_research_answer_preview"),
            ft.Text(
                state.last_answer_text,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
                selectable=True,
            ),
        ]
    )


def build_plan_preview(state: ResearchObserverState, *, on_start_plan: Callable[[], None]) -> ft.Control | None:
    """计划预览与「开始这份计划」：无计划引用时不显示启动入口（不猜最新计划）。"""

    if not (state.plan_steps or state.plan_pending or state.plan_error_key or state.plan_reference):
        return None
    rows: list[ft.Control] = [section_title("agent_research_plan_preview")]
    if state.plan_pending:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))
    if state.plan_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.plan_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if not state.plan_steps and not state.plan_pending:
        rows.append(hint_text("agent_research_plan_empty"))
    for index, step in enumerate(state.plan_steps, start=1):
        rows.append(_build_plan_step_row(index, step))
    if state.plan_reference is not None:
        rows.append(
            ft.Row(
                [
                    ft.FilledButton(
                        content=ft.Text(I18n.get("agent_research_start_plan"), size=AppStyles.FONT_SIZE_BODY_SM),
                        disabled=state.composer_pending,
                        on_click=lambda _e: on_start_plan(),
                    )
                ],
                alignment=ft.MainAxisAlignment.END,
            )
        )
    return section_card(rows)


def _build_plan_step_row(index: int, step: PlanStepView) -> ft.Control:
    """单个计划步骤：序号 + 正文 + 显式依赖（无依赖不补默认）。"""

    rows: list[ft.Control] = [
        ft.Text(
            f"{index}. {step.content}",
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_PRIMARY,
        )
    ]
    if step.depends_on:
        rows.append(
            ft.Text(
                I18n.get("agent_research_plan_depends_fmt").format(depends=", ".join(step.depends_on)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return ft.Column(rows, spacing=AppStyles.SPACING_XS)
