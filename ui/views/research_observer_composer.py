"""研究观察面板·首问输入区（composer / 计划预览 / 主回合直答）。

本模块只渲染首问相关状态并转发显式回调：不 import VM、不拼装服务请求。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.components.model_management_panel import format_cny_amount
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.research_observer_view_model import (
    COMPOSER_MODES,
    BUDGET_ESTIMATED,
    CostUsageView,
    DispatchReasonView,
    DurationSegment,
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


def _ignore_click() -> None:
    """缺省回调（未传入选择/刷新回调时不绑定动作）。"""


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


def _option_text(label: str, model: str, suffix_key: str | None) -> str:
    """连接选项文案：名称 + 模型 + 显式状态后缀（生效/拒绝原因，不隐藏非法项）。"""

    base = f"{label}（{model}）" if model else label
    return f"{base} · {I18n.get(suffix_key)}" if suffix_key else base


def build_model_selection(
    state: ResearchObserverState,
    *,
    on_select_model: Callable[[str], None] = _ignore_text,
    on_select_role: Callable[[str], None] = _ignore_text,
) -> ft.Control:
    """工具栏：研究场景模型连接 + 研究角色（选择随首问冻结，非法选择显式拒绝）。"""

    rows: list[ft.Control] = [
        section_title("agent_research_model_selection_title"),
        hint_text("agent_research_model_selection_hint"),
    ]
    if not state.model_selection_available:
        rows.append(hint_text("agent_research_model_selection_unavailable"))
    elif not state.model_options:
        rows.append(hint_text("agent_research_model_selection_empty"))
    else:
        model_value = state.model_selection if state.model_option(state.model_selection) is not None else None
        dropdown_options = [
            ft.dropdown.Option(
                key=option.connection_id,
                text=_option_text(
                    option.display_name,
                    option.model,
                    option.reject_key
                    or (
                        "agent_research_model_selection_effective"
                        if option.effective
                        else "agent_research_model_selection_fallback"
                    ),
                ),
                disabled=not option.eligible,
            )
            for option in state.model_options
        ]
        rows.append(
            ft.Dropdown(
                key="agent_research_model_select",
                value=model_value,
                options=dropdown_options,
                label=I18n.get("agent_research_model_label"),
                text_size=AppStyles.FONT_SIZE_CAPTION,
                disabled=state.composer_pending,
                on_select=lambda e: on_select_model(e.control.value or ""),
            )
        )
    if state.model_selection_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.model_selection_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.model_selection_frozen is not None:
        frozen = state.model_option(state.model_selection_frozen)
        effective = next((option for option in state.model_options if option.effective), None)
        if frozen is not None and not frozen.effective and effective is not None:
            frozen_text = I18n.get("agent_research_model_frozen_fallback_fmt").format(
                model=frozen.display_name, effective=effective.display_name
            )
        else:
            frozen_text = I18n.get("agent_research_model_frozen_fmt").format(
                model=frozen.display_name if frozen is not None else state.model_selection_frozen
            )
        rows.append(
            ft.Text(
                frozen_text,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if state.role_options:
        role_value = state.role_selection if state.role_option(state.role_selection) is not None else None
        rows.append(
            ft.Dropdown(
                key="agent_research_role_select",
                value=role_value,
                options=[
                    ft.dropdown.Option(
                        key=option.role_key,
                        text=_option_text(option.display_name or I18n.get(option.name_key), "", option.reject_key),
                        disabled=not option.eligible,
                    )
                    for option in state.role_options
                ],
                label=I18n.get("agent_research_role_label"),
                text_size=AppStyles.FONT_SIZE_CAPTION,
                disabled=state.composer_pending,
                on_select=lambda e: on_select_role(e.control.value or ""),
            )
        )
    if state.role_selection_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.role_selection_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.role_selection_frozen is not None:
        rows.append(
            ft.Text(
                I18n.get("agent_research_role_frozen_fmt").format(role=state.role_selection_frozen),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    return ft.Column(rows, spacing=AppStyles.SPACING_XS)


def build_budget_rows(state: ResearchObserverState) -> list[ft.Control]:
    """发送前预算：可估算给金额与单位，不可估显式未计价/未知（不填 0，R21）。"""

    budget = state.budget
    if budget is None:
        return [hint_text("agent_research_budget_unknown")]
    if budget.state == BUDGET_ESTIMATED and budget.amount_cny is not None:
        basis = ""
        if budget.basis_key:
            basis = I18n.get(budget.basis_key).format(
                input=budget.input_tokens if budget.input_tokens is not None else I18n.get("model_usage_unknown"),
                output=budget.output_reserve_tokens
                if budget.output_reserve_tokens is not None
                else I18n.get("model_usage_unknown"),
            )
        return [
            ft.Text(
                I18n.get("agent_research_budget_estimated_fmt").format(
                    amount=format_cny_amount(budget.amount_cny),
                    unit=I18n.get(budget.unit_key),
                    basis=basis,
                ),
                key="agent_research_budget_estimated",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        ]
    return [hint_text(budget.state_key)]


def _cost_rows(label_key: str, view: CostUsageView | None) -> list[ft.Control]:
    """费用行：状态文案 + 已知金额（未计价/预留/未知不显示伪零）。"""

    if view is None:
        return []
    value = format_cny_amount(view.known_cny) if view.state in ("known", "partial") else ""
    text = I18n.get(label_key).format(value=I18n.get(view.state_key))
    if value:
        text = f"{text} {value}"
    if view.truncated:
        text = f"{text} {I18n.get('agent_research_usage_truncated')}"
    return [
        ft.Text(
            text,
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_SECONDARY,
        )
    ]


def _duration_text(segment: DurationSegment) -> str:
    """单段耗时：有来源给毫秒，缺来源显式未知（不使用 0 冒充），并标注来源字段。"""

    if segment.value_ms is not None:
        value = I18n.get("agent_research_duration_value_fmt").format(ms=segment.value_ms)
    else:
        value = I18n.get(segment.unknown_key or "agent_research_duration_unknown")
    return I18n.get("agent_research_duration_row_source_fmt").format(
        label=I18n.get(segment.label_key),
        value=value,
        source=I18n.get(segment.source_key),
    )


def build_usage_rows(state: ResearchObserverState) -> list[ft.Control]:
    """本轮/累计费用 + 五段耗时（只读投影；缺段与未结算显式未知）。"""

    if state.usage_round is None and state.usage_cumulative is None and not state.durations:
        return []
    rows: list[ft.Control] = [section_title("agent_research_usage_title")]
    rows.extend(_cost_rows("agent_research_usage_round_fmt", state.usage_round))
    rows.extend(_cost_rows("agent_research_usage_cumulative_fmt", state.usage_cumulative))
    if state.usage_error_key:
        rows.append(
            ft.Text(
                I18n.get(state.usage_error_key),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.durations:
        rows.append(section_title("agent_research_duration_title"))
        for segment in state.durations:
            rows.append(
                ft.Text(
                    _duration_text(segment),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
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
    on_select_model: Callable[[str], None] = _ignore_text,
    on_select_role: Callable[[str], None] = _ignore_text,
) -> ft.Control:
    """首问输入区：正文 + 模式 + 发送（发送只经注入回调，组件不拼装服务请求）。

    讨论/计划同样经受理落库；执行零派发时由 ``last_dispatch_reason`` 如实展示
    原因（不把「未派发」显示成「已启动」）。
    """

    rows: list[ft.Control] = [section_title("agent_research_composer")]
    rows.append(build_model_selection(state, on_select_model=on_select_model, on_select_role=on_select_role))
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
    rows.extend(build_budget_rows(state))
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
    rows.extend(build_usage_rows(state))
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
