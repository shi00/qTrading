"""应用级统一决定/授权面板（W-07）：三动作、授权范围与撤销、跨页提醒。

纯展示 + 显式回调；不 import VM 的服务实现、不拼装请求、不感知执行状态。
读屏公告用 ``ft.Semantics(live_region=True)`` 承载（非模态、不抢焦点）。
"""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.decision_center_view_model import (
    ACTION_ALLOW,
    ACTION_ANSWER,
    DecisionCenterState,
    DecisionItemView,
    ReminderView,
)
from ui.views.research_observer_panel import PANEL_WIDTH
from ui.views.research_observer_widgets import hint_text, section_card, section_title

#: 待处理阶段标题（记录/交付/消费）。
_STAGE_KEYS = {
    "answer": "agent_decision_stage_answer",
    "delivery": "agent_decision_stage_delivery",
    "consumption": "agent_decision_stage_consumption",
}


def _format(key: str, params: tuple[tuple[str, str], ...] = ()) -> str:
    """按 key + 参数渲染本地化文案（参数缺失保持原占位，不猜）。"""

    text = I18n.get(key)
    return text.format(**dict(params)) if params else text


def _session_label(title: str | None) -> str:
    """会话标题缺失时用本地化占位（不把未知读成空字符串）。"""

    return title or I18n.get("agent_research_pending_unknown_session")


def _action_button(
    label_key: str,
    on_click: Callable[[], None],
    *,
    disabled: bool = False,
    tooltip: str | None = None,
) -> ft.TextButton:
    """统一动作按钮（显式点击；禁用即不可提交，不排队）。"""

    return ft.TextButton(
        content=ft.Text(I18n.get(label_key), size=AppStyles.FONT_SIZE_BODY_SM),
        disabled=disabled,
        tooltip=tooltip,
        on_click=lambda _e: on_click(),
    )


def _build_authorization_detail(
    state: DecisionCenterState,
    item: DecisionItemView,
    *,
    busy: bool,
    on_load_detail: Callable[[str], None],
    on_revoke: Callable[[str], None],
) -> list[ft.Control]:
    """授权范围/期限/撤销入口（详情按需加载，未接线如实提示）。"""

    rows: list[ft.Control] = []
    detail = state.detail_for(item.decision_id)
    if detail is None:
        rows.append(
            _action_button(
                "agent_decision_load_scope",
                lambda: on_load_detail(item.decision_id),
                disabled=busy,
            )
        )
        return rows
    if detail.error_code is not None:
        rows.append(
            ft.Text(
                _format("agent_decision_scope_error_fmt", (("code", detail.error_code),)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if detail.grant_state is not None:
        rows.append(
            ft.Text(
                _format(
                    "agent_decision_grant_state_fmt",
                    (("state", detail.grant_state),),
                ),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if detail.grant_revision is not None:
        rows.append(
            ft.Text(
                _format("agent_decision_grant_revision_fmt", (("revision", str(detail.grant_revision)),)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    revoke_disabled = busy or not state.revoke_available or not detail.revocable
    rows.append(
        _action_button(
            "agent_decision_revoke",
            lambda: on_revoke(item.decision_id),
            disabled=revoke_disabled,
            tooltip=None if state.revoke_available else I18n.get("agent_decision_revoke_unavailable"),
        )
    )
    if not state.revoke_available:
        rows.append(hint_text("agent_decision_revoke_unavailable"))
    if not detail.revocable and detail.grant_state is not None:
        rows.append(hint_text("agent_decision_revoke_not_applicable"))
    return rows


def _build_item(
    state: DecisionCenterState,
    item: DecisionItemView,
    *,
    on_allow: Callable[[str], None],
    on_deny: Callable[[str], None],
    on_defer: Callable[[str], None],
    on_submit_answer: Callable[[str], None],
    on_draft_change: Callable[[str, str], None],
    on_retry_delivery: Callable[[str], None],
    on_load_detail: Callable[[str], None],
    on_revoke: Callable[[str], None],
) -> ft.Control:
    """单个待处理决定（源会话/影响范围/期限 + 三动作或文本答复）。"""

    busy = state.submitting_decision_id is not None
    submitting_here = state.submitting_decision_id == item.decision_id
    rows: list[ft.Control] = [
        ft.Text(
            _format("agent_decision_source_fmt", (("session", _session_label(item.session_title)),)),
            size=AppStyles.FONT_SIZE_BODY_SM,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.Text(
            _format(
                "agent_decision_scope_fmt",
                (
                    ("execution", item.execution_id),
                    ("round", item.round_id),
                    ("stage", I18n.get(_STAGE_KEYS.get(item.stage, "agent_decision_stage_unknown"))),
                ),
            ),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        ),
        ft.Text(
            I18n.get("agent_decision_kind_authorization")
            if item.is_authorization
            else _format("agent_decision_kind_fmt", (("kind", item.decision_kind),)),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_HINT,
        ),
    ]
    if item.question_text:
        rows.append(
            ft.Text(
                item.question_text,
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    if item.is_authorization:
        if item.tool_name:
            rows.append(
                ft.Text(
                    _format("agent_decision_tool_fmt", (("tool", item.tool_name),)),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        if item.service_id:
            rows.append(
                ft.Text(
                    _format(
                        "agent_decision_service_fmt",
                        (("service", item.service_id), ("version", item.config_version or "—")),
                    ),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
        if item.parameter_digest:
            rows.append(
                ft.Text(
                    _format("agent_decision_parameter_digest_fmt", (("digest", item.parameter_digest),)),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
    if item.expires_at is not None:
        rows.append(
            ft.Text(
                _format("agent_decision_expires_fmt", (("expires_at", item.expires_at),)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            )
        )
    if item.expired:
        rows.append(hint_text("agent_decision_expired_hint"))
    elif item.expiry_unknown:
        rows.append(hint_text("agent_decision_expiry_unknown"))
    states: list[str] = []
    if item.delivery_state is not None:
        states.append(I18n.get("agent_research_delivery_state_fmt").format(state=item.delivery_state))
    if item.consumption_state is not None:
        states.append(I18n.get("agent_research_consumption_state_fmt").format(state=item.consumption_state))
    if states:
        rows.append(ft.Text(" · ".join(states), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT))
    if state.is_deferred(item.decision_id):
        rows.append(hint_text("agent_decision_deferred_hint"))

    if item.authorization_actionable:
        rows.append(
            ft.Row(
                [
                    _action_button(
                        "agent_decision_submitting"
                        if submitting_here and state.submitting_action == ACTION_ALLOW
                        else "agent_decision_allow",
                        lambda: on_allow(item.decision_id),
                        disabled=busy or item.expired,
                    ),
                    _action_button(
                        "agent_decision_deny",
                        lambda: on_deny(item.decision_id),
                        disabled=busy or item.expired,
                    ),
                    _action_button(
                        "agent_decision_later",
                        lambda: on_defer(item.decision_id),
                        disabled=busy,
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
    elif item.clarification_actionable:
        rows.append(
            ft.TextField(
                # 输入态跟随决定归属而非列表位置（同观察层契约）。
                key=f"agent_decision_answer_{item.decision_id}",
                value=state.draft_for(item.decision_id),
                label=I18n.get("agent_research_answer_label"),
                hint_text=I18n.get("agent_research_answer_hint"),
                multiline=True,
                min_lines=2,
                max_lines=4,
                text_size=AppStyles.FONT_SIZE_BODY_SM,
                on_change=lambda e, decision_id=item.decision_id: on_draft_change(
                    decision_id,
                    e.control.value or "",
                ),
            )
        )
        rows.append(
            ft.Row(
                [
                    _action_button(
                        "agent_decision_submitting"
                        if submitting_here and state.submitting_action == ACTION_ANSWER
                        else "agent_decision_submit_answer",
                        lambda: on_submit_answer(item.decision_id),
                        disabled=busy or item.expired,
                    ),
                    _action_button(
                        "agent_decision_later",
                        lambda: on_defer(item.decision_id),
                        disabled=busy,
                    ),
                ],
                spacing=AppStyles.SPACING_XS,
            )
        )
    elif item.stage == "delivery" and item.retryable_delivery:
        rows.append(
            _action_button(
                "agent_decision_retry_delivery",
                lambda: on_retry_delivery(item.decision_id),
                disabled=busy,
            )
        )
    if item.is_authorization:
        rows.extend(
            _build_authorization_detail(
                state,
                item,
                busy=busy,
                on_load_detail=on_load_detail,
                on_revoke=on_revoke,
            )
        )
    return section_card(rows)


def _build_reminder(
    reminder: ReminderView,
    *,
    on_locate: Callable[[str], None],
    on_dismiss: Callable[[int], None],
) -> ft.Control:
    """跨页提醒：文案 + 定位源会话 + 忽略（不抢焦点、不自动跳转）。"""

    rows: list[ft.Control] = [
        ft.Text(
            _format(reminder.key, reminder.params),
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_PRIMARY,
        ),
        ft.Row(
            [
                _action_button(
                    "agent_decision_locate",
                    lambda: on_locate(reminder.session_id),
                    disabled=not reminder.session_id,
                ),
                _action_button(
                    "agent_decision_dismiss_reminder",
                    lambda: on_dismiss(reminder.seq),
                ),
            ],
            spacing=AppStyles.SPACING_XS,
        ),
    ]
    return section_card(rows)


def build_decision_center_panel(
    state: DecisionCenterState,
    *,
    on_close: Callable[[], None],
    on_refresh: Callable[[], None],
    on_allow: Callable[[str], None],
    on_deny: Callable[[str], None],
    on_defer: Callable[[str], None],
    on_submit_answer: Callable[[str], None],
    on_draft_change: Callable[[str, str], None],
    on_retry_delivery: Callable[[str], None],
    on_load_detail: Callable[[str], None],
    on_revoke: Callable[[str], None],
    on_locate: Callable[[str], None],
    on_dismiss_reminder: Callable[[int], None],
) -> ft.Container:
    """统一决定面板（右侧非模态栏；任意业务页均从壳层入口打开）。"""

    rows: list[ft.Control] = [
        ft.Row(
            [
                section_title("agent_decision_center_title"),
                ft.Row(
                    [
                        ft.IconButton(
                            icon=ft.Icons.REFRESH,
                            icon_size=AppStyles.FONT_SIZE_BODY_SM,
                            tooltip=I18n.get("agent_decision_center_refresh"),
                            on_click=lambda _e: on_refresh(),
                        ),
                        ft.IconButton(
                            icon=ft.Icons.CLOSE,
                            icon_size=AppStyles.FONT_SIZE_BODY_SM,
                            tooltip=I18n.get("agent_decision_center_close"),
                            on_click=lambda _e: on_close(),
                        ),
                    ],
                    spacing=0,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
    ]
    if state.announcement is not None:
        # 读屏公告：live_region 非抢焦点；文案同时可见，不依赖焦点/弹窗。
        announcement_text = _format(state.announcement.key, state.announcement.params)
        rows.append(
            ft.Semantics(
                content=ft.Text(
                    announcement_text,
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_PRIMARY,
                ),
                label=announcement_text,
                live_region=True,
            )
        )
    if not state.available:
        rows.append(hint_text(state.unavailable_reason_key))
    if state.error_code is not None:
        rows.append(
            ft.Text(
                _format("agent_decision_error_fmt", (("code", state.error_code),)),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.ERROR,
            )
        )
    if state.notice_key is not None:
        rows.append(
            ft.Text(
                _format(state.notice_key, state.notice_params),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_PRIMARY,
            )
        )
    if state.loading:
        rows.append(ft.ProgressRing(width=16, height=16, stroke_width=2))

    items = state.ordered_items()
    rows.append(section_title("agent_decision_center_pending"))
    if not items:
        rows.append(hint_text("agent_decision_center_empty"))
    for item in items:
        rows.append(
            _build_item(
                state,
                item,
                on_allow=on_allow,
                on_deny=on_deny,
                on_defer=on_defer,
                on_submit_answer=on_submit_answer,
                on_draft_change=on_draft_change,
                on_retry_delivery=on_retry_delivery,
                on_load_detail=on_load_detail,
                on_revoke=on_revoke,
            )
        )
    if state.truncated:
        rows.append(hint_text("agent_research_pending_truncated"))
    if state.reminders:
        rows.append(section_title("agent_decision_reminders"))
        for reminder in state.reminders:
            rows.append(
                _build_reminder(
                    reminder,
                    on_locate=on_locate,
                    on_dismiss=on_dismiss_reminder,
                )
            )
    rows.append(hint_text("agent_decision_center_scope_note"))
    return ft.Container(
        content=ft.Column(rows, spacing=AppStyles.SPACING_XS, scroll=ft.ScrollMode.AUTO, expand=True),
        width=PANEL_WIDTH,
        padding=AppStyles.SPACING_MD,
        bgcolor=AppColors.SURFACE,
        border=ft.Border(left=ft.BorderSide(1, AppColors.BORDER)),
        expand=False,
    )
