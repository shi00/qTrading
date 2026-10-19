"""ScenarioRouteEditor — 共用场景路由编辑器（S5 §11.2 / §11.4）。

声明式组件（CLAUDE.md §3.2 MVVM / docs/flet/v1-api-constraints.md §2）：组件接收
``ModelRoutesViewModel``，内部订阅 ``vm.state`` 并调用其命令，自身不持有业务状态，
也不直接读写 ConfigHandler / 凭据 / 推进宿主（§11.2「组件不得直接读写…」）。

语义（reviews/模型统一.md §11.4）：
- 三场景导航（``news_annotation`` / ``stock_analysis`` / ``news_risk``）+ 有序候选列表；
  切场景保留各自草稿并显示未保存数（``state.dirty_counts``）。
- 每行显示序号、配置首选 / 备用、连接、计量、资格（该任务能力），以及上移 / 下移 /
  移除按钮；首末对应箭头用 ``vm.can_move_up`` / ``vm.can_move_down`` 禁用。
- 第一行标「配置首选」；资格不满足（能力非 supported）时另标「预计首先尝试」，
  该标记不改序号、不承诺网络可用。
- 添加候选经可搜索选择器（``state.candidate_picker_open`` + ``available_connections``）：
  显示所有连接、计量与该任务能力；已选项禁用重复添加，不兼容项不悄悄隐藏；
  确认追加末尾（``vm.add_candidate``），关闭返回发起按钮焦点、不造连接副本。
- 新闻场景下有独立情绪分区（引用候选，模式 bundled / dedicated）。
- 最后一次移动 / 移除可撤销（``vm.undo_last_move``，只改草稿）。
- 未分配 / 未评估 / 未验证 / 不兼容 / 无合格候选以行内弱提示或可展开证据呈现，
  不自动跳过未知能力。

约束：R23 颜色经 ``AppColors``、字号经 ``AppStyles``；R16 事件处理器只调同步 VM 命令
（无同步 IO）；不得新增命令式生命周期 / 手动 update。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels.model_connections_view_model import ModelConnectionsViewModel
from ui.viewmodels.model_routes_view_model import (
    ConnectionOption,
    ModelRoutesState,
    ModelRoutesViewModel,
    ScenarioRouteDraft,
)
from utils.config_models import SCENARIO_IDS

logger = logging.getLogger(__name__)

__all__ = ["ScenarioRouteEditor"]

#: 三场景导航顺序（与 VM / utils.config_models.SCENARIO_IDS 对齐）。
_SCENARIO_ORDER: tuple[str, ...] = tuple(SCENARIO_IDS)

#: 场景导航 i18n 标签键。
_SCENARIO_LABEL_KEYS: dict[str, str] = {
    "news_annotation": "model_routes_scenario_news_annotation",
    "stock_analysis": "model_routes_scenario_stock_analysis",
    "news_risk": "model_routes_scenario_news_risk",
}

#: 场景主任务 id（能力查询口径，与 ai_service 既有 task_id 一致）。
_SCENARIO_TASK_IDS: dict[str, str] = {
    "news_annotation": "sentiment",
    "stock_analysis": "analysis",
    "news_risk": "risk",
}

_CAPABILITY_KEYS: dict[str, str] = {
    "supported": "model_routes_cap_supported",
    "unsupported": "model_routes_cap_unsupported",
    "unknown": "model_routes_cap_unknown",
}

_CAPABILITY_COLOR_ATTRS: dict[str, str] = {
    "supported": "SUCCESS",
    "unsupported": "WARNING",
    "unknown": "TEXT_HINT",
}

_BILLING_MODE_KEYS: dict[str, str] = {
    "usage_only": "model_routes_billing_usage_only",
    "estimate_cost": "model_routes_billing_estimate_cost",
}

_PRICING_UNIT_KEYS: dict[str, str] = {
    "llm_tokens": "model_routes_unit_llm_tokens",
    "encoder_input_tokens": "model_routes_unit_encoder_tokens",
}

#: 场景 → 任务名 i18n 键（授权摘要区「任务」列，§4.3）。
_EGRESS_TASK_KEYS: dict[str, str] = {
    "news_annotation": "model_routes_egress_task_news_annotation",
    "stock_analysis": "model_routes_egress_task_stock_analysis",
    "news_risk": "model_routes_egress_task_news_risk",
}

#: 场景 → 将发送的数据类别 i18n 键（授权摘要区「数据类别」列，§4.3）。
_EGRESS_DATA_KEYS: dict[str, str] = {
    "news_annotation": "model_routes_egress_data_news_annotation",
    "stock_analysis": "model_routes_egress_data_stock_analysis",
    "news_risk": "model_routes_egress_data_news_risk",
}


# ---------------------------------------------------------------------------
# 纯函数辅助
# ---------------------------------------------------------------------------


def _click(call: Callable[[], None]) -> Callable[[ft.ControlEvent], None]:
    """把无参命令包装为 Flet on_click 处理器（R16：只调同步 VM 命令）。"""

    def _on_click(_e: ft.ControlEvent) -> None:
        call()

    return _on_click


def _run_task_handler(coro_func: Callable, *args: Any) -> Callable[[ft.ControlEvent], None]:
    """把异步命令包装为 on_click 处理器，经 ``page.run_task`` 提交（R16）。

    授权 / 撤销授权为异步命令（写配置文件），不得在事件处理器内同步等待。
    """

    def _on_click(_e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(coro_func, *args)
        except RuntimeError:
            logger.debug("[ScenarioRouteEditor] page unavailable for %s", getattr(coro_func, "__name__", coro_func))

    return _on_click


def _try_focus(control: Any) -> None:
    """best-effort 焦点恢复（关闭选择器后返回发起按钮）。

    仅当控件暴露可调用 ``focus`` 时才调用，且吞掉一切异常——无 renderer 上下文
    （单元测试）或控件未挂载时静默跳过，绝不阻断渲染。
    """
    focus = getattr(control, "focus", None)
    if not callable(focus):
        return
    try:
        focus()
    except Exception:
        logger.debug("[ScenarioRouteEditor] focus return skipped", exc_info=True)


def _confirm_add(vm: ModelRoutesViewModel, connection_id: str) -> None:
    """确认候选：追加末尾并关闭选择器（不造连接副本，仅引用 id）。"""
    vm.add_candidate(connection_id)
    vm.close_candidate_picker()


def _current_draft(state: ModelRoutesState) -> ScenarioRouteDraft | None:
    return next((r for r in state.routes if r.scenario_id == state.selected_scenario), None)


def _option_by_id(state: ModelRoutesState, connection_id: str) -> ConnectionOption | None:
    return next((o for o in state.available_connections if o.connection_id == connection_id), None)


def _task_id_for_scenario(scenario_id: str) -> str:
    return _SCENARIO_TASK_IDS.get(scenario_id, scenario_id)


def _capability_label(capability: str) -> str:
    return I18n.get(_CAPABILITY_KEYS.get(capability, "model_routes_cap_unknown"))


def _capability_color(capability: str) -> Any:
    return getattr(AppColors, _CAPABILITY_COLOR_ATTRS.get(capability, "TEXT_HINT"))


def _billing_label(option: ConnectionOption | None) -> str:
    if option is None:
        return I18n.get("model_routes_billing_unknown")
    mode = I18n.get(_BILLING_MODE_KEYS.get(option.billing_mode, "model_routes_billing_unknown"))
    unit = I18n.get(_PRICING_UNIT_KEYS.get(option.billing_unit, "model_routes_unit_unknown"))
    return f"{mode} / {unit}"


# ---------------------------------------------------------------------------
# 控件构建
# ---------------------------------------------------------------------------


def _build_scenario_tab(scenario_id: str, state: ModelRoutesState, vm: ModelRoutesViewModel) -> ft.Control:
    """场景导航按钮（选中态高亮；未保存数作徽标）。"""
    selected = scenario_id == state.selected_scenario
    label = I18n.get(_SCENARIO_LABEL_KEYS.get(scenario_id, scenario_id))
    label_controls: list[ft.Control] = [
        ft.Text(
            label,
            size=AppStyles.FONT_SIZE_BODY_SM,
            color=AppColors.TEXT_ON_PRIMARY if selected else AppColors.TEXT_PRIMARY,
        )
    ]
    dirty = dict(state.dirty_counts).get(scenario_id, 0)
    if dirty > 0:
        label_controls.append(
            ft.Text(
                I18n.get("model_routes_dirty_badge", count=dirty),
                key=f"scenario_dirty_{scenario_id}",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_ON_PRIMARY if selected else AppColors.WARNING,
            )
        )
    return anchored(
        EIDS.MODEL.scenario_tab(scenario_id),
        ft.Button(
            content=ft.Row(label_controls, spacing=AppStyles.SPACING_XS),
            key=f"scenario_tab_{scenario_id}",
            icon=ft.Icons.CHECK if selected else None,
            style=AppStyles.primary_button() if selected else AppStyles.secondary_button(),
            on_click=safe_on_click(_click(lambda: vm.select_scenario(scenario_id))),
        ),
    )


def _build_candidate_row(
    index: int,
    connection_id: str,
    draft: ScenarioRouteDraft,
    state: ModelRoutesState,
    vm: ModelRoutesViewModel,
    task_id: str,
    *,
    compact: bool,
) -> ft.Container:
    """单个候选行：序号 / 首选备用 / 连接 / 计量 / 资格 / 操作性按钮 / 行内提示。"""
    option = _option_by_id(state, connection_id)
    capability = option.capability_for(task_id) if option is not None else "unknown"
    is_first = index == 0
    display_name = (option.name or option.connection_id) if option is not None else connection_id

    index_text = ft.Text(
        f"{index + 1}.",
        key=f"index_{connection_id}",
        size=AppStyles.FONT_SIZE_BODY,
        weight=ft.FontWeight.BOLD,
        color=AppColors.TEXT_PRIMARY,
        width=24,
    )
    role_text = ft.Text(
        I18n.get("model_routes_preferred") if is_first else I18n.get("model_routes_backup"),
        key=f"role_{connection_id}",
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.PRIMARY if is_first else AppColors.TEXT_HINT,
    )
    name_text = ft.Text(
        display_name,
        key=f"name_{connection_id}",
        size=AppStyles.FONT_SIZE_BODY_SM,
        weight=ft.FontWeight.W_500,
        color=AppColors.TEXT_PRIMARY,
    )
    billing_text = ft.Text(
        _billing_label(option),
        key=f"billing_{connection_id}",
        size=AppStyles.FONT_SIZE_CAPTION,
        color=AppColors.TEXT_SECONDARY,
    )
    capability_text = ft.Text(
        _capability_label(capability),
        key=f"capability_{connection_id}",
        size=AppStyles.FONT_SIZE_CAPTION,
        color=_capability_color(capability),
    )

    top_row = ft.Row(
        [
            index_text,
            role_text,
            name_text,
            billing_text,
            capability_text,
            ft.Container(expand=True),
            anchored(
                EIDS.MODEL.move_up(connection_id),
                ft.IconButton(
                    ft.Icons.ARROW_UPWARD,
                    key=f"move_up_{connection_id}",
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("model_routes_move_up"),
                    disabled=not vm.can_move_up(connection_id),
                    on_click=safe_on_click(_click(lambda: vm.move_up(connection_id))),
                ),
            ),
            anchored(
                EIDS.MODEL.move_down(connection_id),
                ft.IconButton(
                    ft.Icons.ARROW_DOWNWARD,
                    key=f"move_down_{connection_id}",
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("model_routes_move_down"),
                    disabled=not vm.can_move_down(connection_id),
                    on_click=safe_on_click(_click(lambda: vm.move_down(connection_id))),
                ),
            ),
            anchored(
                EIDS.MODEL.remove(connection_id),
                ft.IconButton(
                    ft.Icons.DELETE_OUTLINE,
                    key=f"remove_{connection_id}",
                    icon_size=AppStyles.FONT_SIZE_TITLE,
                    tooltip=I18n.get("model_routes_remove"),
                    on_click=safe_on_click(_click(lambda: vm.remove_candidate(connection_id))),
                ),
            ),
        ],
        spacing=AppStyles.SPACING_SM,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    notes: list[ft.Control] = []
    if is_first and capability != "supported":
        # 资格不满足时的「预计首先尝试」：不改序号、不承诺网络可用。
        notes.append(
            ft.Text(
                I18n.get("model_routes_expected_first_try"),
                key=f"expected_first_try_{connection_id}",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.WARNING,
            )
        )
    hint_key: str | None = None
    if option is None:
        hint_key = "model_routes_hint_unassigned"
    elif capability == "unsupported":
        hint_key = "model_routes_hint_incompatible"
    elif capability == "unknown":
        hint_key = "model_routes_hint_unverified"
    if hint_key is not None:
        notes.append(
            ft.Text(
                I18n.get(hint_key),
                key=f"hint_{connection_id}",
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        )

    column_controls: list[ft.Control] = [top_row]
    if notes:
        column_controls.append(ft.Row(notes, spacing=AppStyles.SPACING_SM))

    return ft.Container(
        key=f"route_row_{connection_id}",
        content=ft.Column(column_controls, spacing=AppStyles.SPACING_XS),
        padding=ft.Padding.symmetric(horizontal=12, vertical=6 if compact else 8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
        bgcolor=AppColors.SURFACE_VARIANT,
    )


def _build_advanced_section(draft: ScenarioRouteDraft, expanded: bool, *, compact: bool) -> ft.Column:
    """高级参数折叠区（只读展示草稿中的时限 / 重试 / 降级开关）。"""
    fallback_text = (
        I18n.get("model_routes_advanced_fallback_on")
        if draft.fallback_enabled
        else I18n.get("model_routes_advanced_fallback_off")
    )
    return ft.Column(
        [
            ft.Text(
                I18n.get("model_routes_advanced_timeout", seconds=draft.total_timeout_seconds),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
            ft.Text(
                I18n.get("model_routes_advanced_retries", retries=draft.transient_retries),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
            ft.Text(
                fallback_text,
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
        ],
        key="advanced_section",
        visible=expanded,
        spacing=AppStyles.SPACING_XS,
    )


def _build_candidate_picker(
    state: ModelRoutesState,
    task_id: str,
    search: str,
    set_search: Callable[[Any], None],
    vm: ModelRoutesViewModel,
) -> ft.Container:
    """可搜索候选选择器（显示所有连接 / 计量 / 该任务能力；已选项禁用，不兼容项保留）。"""
    draft = _current_draft(state)
    selected: set[str] = set(draft.candidates) if draft is not None else set()
    query = search.strip().lower()

    rows: list[ft.Control] = []
    for option in state.available_connections:
        display = option.name or option.connection_id
        if query and query not in f"{display} {option.connection_id}".lower():
            continue
        already = option.connection_id in selected
        capability = option.capability_for(task_id)
        marks: list[ft.Control] = [
            ft.Text(
                display,
                key=f"picker_name_{option.connection_id}",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_DISABLED if already else AppColors.TEXT_PRIMARY,
            ),
            ft.Text(
                _billing_label(option),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
            ),
            ft.Text(
                _capability_label(capability),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=_capability_color(capability),
            ),
        ]
        if already:
            marks.append(
                ft.Text(
                    I18n.get("model_routes_picker_already_added"),
                    key=f"picker_added_{option.connection_id}",
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_DISABLED,
                )
            )
        elif capability == "unsupported":
            # 不兼容项不悄悄隐藏：保留条目并明确标注（§11.4）。
            marks.append(
                ft.Text(
                    I18n.get("model_routes_picker_incompatible"),
                    key=f"picker_incompatible_{option.connection_id}",
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.WARNING,
                )
            )
        rows.append(
            ft.Container(
                key=f"picker_option_{option.connection_id}",
                content=ft.Row(marks, spacing=AppStyles.SPACING_SM),
                padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                border=ft.Border.all(1, AppColors.BORDER),
                border_radius=6,
                bgcolor=AppColors.SURFACE_VARIANT,
                # 已选项：禁用重复添加（on_click 置空，不复制连接）；不兼容项仍可选（需显式确认）。
                on_click=None
                if already
                else safe_on_click(_click(lambda cid=option.connection_id: _confirm_add(vm, cid))),
            )
        )

    body: list[ft.Control] = [
        ft.Text(I18n.get("model_routes_picker_title"), size=AppStyles.FONT_SIZE_TITLE, color=AppColors.TEXT_PRIMARY),
        anchored(
            EIDS.MODEL.PICKER_SEARCH,
            ft.TextField(
                key="picker_search",
                value=search,
                hint_text=I18n.get("model_routes_picker_search"),
                tooltip=I18n.get("model_routes_picker_search"),
                width=AppStyles.CONTROL_WIDTH_LG,
                bgcolor=AppColors.INPUT_BG,
                color=AppColors.INPUT_TEXT,
                border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                on_change=safe_on_change(lambda e: set_search(get_control_value(e.control, ft.TextField))),
            ),
        ),
    ]
    if rows:
        body.append(ft.Column(rows, spacing=AppStyles.SPACING_XS))
    else:
        body.append(
            ft.Text(
                I18n.get("model_routes_picker_empty"),
                key="picker_empty",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        )
    body.append(
        ft.Row(
            [
                anchored(
                    EIDS.MODEL.PICKER_CLOSE,
                    ft.TextButton(
                        content=I18n.get("common_cancel"),
                        key="picker_close",
                        on_click=safe_on_click(_click(vm.close_candidate_picker)),
                    ),
                )
            ],
            alignment=ft.MainAxisAlignment.END,
        )
    )
    return ft.Container(
        key="candidate_picker",
        content=ft.Column(body, spacing=AppStyles.SPACING_SM),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
        bgcolor=AppColors.SURFACE_VARIANT,
    )


def _build_sentiment_section(draft: ScenarioRouteDraft, *, compact: bool) -> ft.Container:
    """新闻场景独立情绪分区（引用候选；模式 bundled / dedicated）。"""
    is_bundled = draft.sentiment_mode == "bundled"
    mode_text = I18n.get("model_routes_sentiment_bundled" if is_bundled else "model_routes_sentiment_dedicated")
    refs: list[ft.Control] = [
        ft.Text(
            I18n.get("model_routes_sentiment_ref", index=i + 1),
            key=f"sentiment_ref_{connection_id}",
            size=AppStyles.FONT_SIZE_CAPTION,
            color=AppColors.TEXT_SECONDARY,
        )
        for i, connection_id in enumerate(draft.candidates)
    ]
    if not refs:
        refs.append(
            ft.Text(
                I18n.get("model_routes_empty_candidates"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        )
    reuse_text = (
        I18n.get("model_routes_sentiment_reuse_on")
        if draft.reuse_bundled_sentiment
        else I18n.get("model_routes_sentiment_reuse_off")
    )
    return ft.Container(
        key="sentiment_section",
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("model_routes_sentiment_section"),
                    size=AppStyles.FONT_SIZE_TITLE,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Text(mode_text, key="sentiment_mode", size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.PRIMARY),
                *refs,
                ft.Text(reuse_text, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
            ],
            spacing=AppStyles.SPACING_XS,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
    )


def _build_egress_summary(
    draft: ScenarioRouteDraft | None,
    purpose: str,
    egress_vm: ModelConnectionsViewModel,
    *,
    compact: bool,
) -> ft.Container:
    """场景检查区授权摘要（§4.3）：列出准确目标 / 任务 / 将发送的数据类别 + 授权 / 撤销。

    按当前场景草稿候选逐条渲染：本地模型（无外发目标）显式提示「不外发」且不渲染授权
    操作（R21 不臆造目标）；HTTP 目标展示规范化目标与当前授权状态，确认后提交 grant。
    数据类别与任务为场景级信息，只需列一次；目标与授权状态随候选变化。
    """
    task_label = I18n.get(_EGRESS_TASK_KEYS.get(purpose, purpose))
    data_label = I18n.get(_EGRESS_DATA_KEYS.get(purpose, purpose))
    rows: list[ft.Control] = [
        ft.Row(
            [
                ft.Text(
                    I18n.get("model_routes_egress_task", task=task_label),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Text(
                    I18n.get("model_routes_egress_data", data=data_label),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
            spacing=AppStyles.SPACING_SM,
            wrap=True,
        )
    ]

    candidates = draft.candidates if draft is not None else []
    if not candidates:
        rows.append(
            ft.Text(
                I18n.get("model_routes_empty_candidates"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        )

    for connection_id in candidates:
        target = egress_vm.connection_target(connection_id)
        if target is None:
            rows.append(
                ft.Text(
                    I18n.get("model_routes_egress_local"),
                    key=f"egress_local_{connection_id}",
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                )
            )
            continue
        grants = egress_vm.grants_for(connection_id, purpose)
        status_controls: list[ft.Control] = []
        if grants:
            rows_status: list[ft.Control] = [
                ft.Text(
                    I18n.get("model_routes_egress_granted"),
                    key=f"egress_granted_{connection_id}",
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.SUCCESS,
                )
            ]
            grant_id = str(grants[0].get("grant_id") or "")
            if grant_id:
                rows_status.append(
                    anchored(
                        EIDS.MODEL.egress_revoke(connection_id),
                        ft.TextButton(
                            content=I18n.get("model_routes_egress_revoke"),
                            key=f"egress_revoke_{connection_id}",
                            on_click=safe_on_click(_run_task_handler(egress_vm.revoke_egress, grant_id)),
                        ),
                    )
                )
            status_controls = rows_status
        else:
            status_controls = [
                anchored(
                    EIDS.MODEL.egress_grant(connection_id),
                    ft.Button(
                        content=I18n.get("model_routes_egress_grant"),
                        key=f"egress_grant_{connection_id}",
                        style=AppStyles.primary_button(),
                        on_click=safe_on_click(
                            _run_task_handler(egress_vm.grant_egress, connection_id, target, purpose)
                        ),
                    ),
                )
            ]
        rows.append(
            ft.Row(
                [
                    ft.Text(
                        I18n.get("model_routes_egress_target", target=target),
                        key=f"egress_target_{connection_id}",
                        size=AppStyles.FONT_SIZE_CAPTION,
                        color=AppColors.TEXT_PRIMARY,
                    ),
                    *status_controls,
                ],
                spacing=AppStyles.SPACING_SM,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            )
        )

    return ft.Container(
        key="egress_summary",
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("model_routes_egress_section"),
                    size=AppStyles.FONT_SIZE_TITLE,
                    color=AppColors.TEXT_PRIMARY,
                ),
                *rows,
            ],
            spacing=AppStyles.SPACING_XS,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=6 if compact else 8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
    )


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------


@ft.component
def ScenarioRouteEditor(
    vm: ModelRoutesViewModel, egress_vm: ModelConnectionsViewModel, *, compact: bool = False
) -> ft.Control:
    """场景路由编辑器（声明式，消费外部 VM）。

    Args:
        vm: 消费方实例化的 ``ModelRoutesViewModel``（外部 VM 模式，生命周期由消费方管理）。
        egress_vm: 「模型连接」页 ViewModel（外部 VM 模式）——授权摘要区经其公开命令
            读写外发授权（§4.3 场景检查区；组件不触私有成员、不直接读写 ConfigHandler）。
        compact: 紧凑布局（缩小间距 / 内边距），用于窄宿主。
    """
    # --- 订阅 VM state 变化（外部 VM 模式，本组件不 dispose） ---
    state, _ = use_viewmodel(vm=vm)
    # 订阅 egress VM：授权 / 撤销后其快照刷新触发重渲染（授权摘要区随之更新）。
    _egress_state, _ = use_viewmodel(vm=egress_vm)

    # --- 订阅 i18n / theme（热切换自动重渲染） ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 选择器本地搜索态 + 发起按钮 ref + 开合前值（焦点恢复用） ---
    search, set_search = ft.use_state("")
    add_ref = ft.use_ref(lambda: None)
    was_open_ref = ft.use_ref(lambda: False)

    def _on_picker_state_change() -> None:
        if state.candidate_picker_open and not was_open_ref.current:
            set_search("")  # 每次打开重置搜索
        elif was_open_ref.current and not state.candidate_picker_open:
            _try_focus(add_ref.current)  # 关闭返回发起按钮焦点（best-effort）
        was_open_ref.current = state.candidate_picker_open

    ft.use_effect(_on_picker_state_change, dependencies=[state.candidate_picker_open])

    draft = _current_draft(state)
    task_id = _task_id_for_scenario(state.selected_scenario)
    spacing = AppStyles.SPACING_XS if compact else AppStyles.SPACING_SM

    nav_row = ft.Row(
        [_build_scenario_tab(sid, state, vm) for sid in _SCENARIO_ORDER],
        spacing=spacing,
        wrap=True,
    )

    if draft is not None and draft.candidates:
        candidate_rows: list[ft.Control] = [
            _build_candidate_row(i, connection_id, draft, state, vm, task_id, compact=compact)
            for i, connection_id in enumerate(draft.candidates)
        ]
    else:
        candidate_rows = [
            ft.Text(
                I18n.get("model_routes_empty_candidates"),
                key="empty_candidates",
                size=AppStyles.FONT_SIZE_BODY_SM,
                color=AppColors.TEXT_HINT,
                italic=True,
            )
        ]

    add_button = ft.OutlinedButton(
        content=I18n.get("model_routes_add_candidate"),
        icon=ft.Icons.ADD,
        key="add_candidate",
        style=AppStyles.secondary_button(),
        on_click=safe_on_click(_click(vm.open_candidate_picker)),
    )
    # 焦点恢复引用指向底层按钮（anchored 包裹的 Semantics 无 focus 方法）。
    add_ref.current = add_button  # type: ignore[reportAttributeAccessIssue]  # use_ref(None) 推断 MutableRef[None]，实际承载按钮以供焦点恢复（沿 toast_manager 既有模式）

    undo_button = ft.TextButton(
        content=I18n.get("model_routes_undo"),
        icon=ft.Icons.UNDO,
        key="undo_button",
        disabled=not state.undo_available,
        on_click=safe_on_click(_click(vm.undo_last_move)),
    )
    advanced_button = ft.TextButton(
        content=I18n.get("model_routes_advanced"),
        icon=ft.Icons.TUNE,
        key="advanced_toggle",
        on_click=safe_on_click(_click(vm.toggle_advanced)),
    )

    children: list[ft.Control] = [
        ft.Text(I18n.get("model_routes_title"), size=AppStyles.FONT_SIZE_TITLE, color=AppColors.TEXT_PRIMARY),
        nav_row,
        ft.Column(candidate_rows, spacing=AppStyles.SPACING_SM),
        ft.Row(
            [
                anchored(EIDS.MODEL.ADD_CANDIDATE, add_button),
                anchored(EIDS.MODEL.UNDO, undo_button),
                anchored(EIDS.MODEL.ADVANCED_TOGGLE, advanced_button),
            ],
            spacing=spacing,
            wrap=True,
        ),
        _build_advanced_section(draft, state.advanced_expanded, compact=compact)
        if draft is not None
        else ft.Container(key="advanced_section", visible=False),
    ]
    if state.candidate_picker_open:
        children.append(_build_candidate_picker(state, task_id, search, set_search, vm))
    if state.selected_scenario == "news_annotation" and draft is not None:
        children.append(_build_sentiment_section(draft, compact=compact))
    # 场景检查区（§4.3）：授权摘要——目标 / 任务 / 数据类别 + 授权 / 撤销。
    children.append(_build_egress_summary(draft, state.selected_scenario, egress_vm, compact=compact))

    return ft.Column(children, spacing=spacing)
