"""model_management_panel — 设置「模型管理」三页签声明式组合（S5 §11.1/§11.2）。

职责（reviews/模型统一.md §11.1）：
- 设置工作台中 AI 大脑的模型区域改为「模型管理」，三页签固定为
  「模型连接 / 场景配置 / 使用记录」；第一次打开即连接列表；
- 标题下只放一行紧凑摘要（可执行场景数 / 需处理配置数 / 本月已知费用），
  未知费用与未统计数独立字段，缺失（None）时不渲染（R21 不伪装为 0）；
- 各页签只做组合与事件转发，不承担业务状态（§11.2）。

设计约束（CLAUDE.md §3.2 MVVM / §3.3 声明式）：
- ``@ft.component`` 函数组件，无 class 子类 / 生命周期回调 / 手动 update；
- 三个 ViewModel 由宿主以 ``use_viewmodel`` factory 创建后传入（外部 VM 模式，
  本组件用 ``use_viewmodel(vm=...)`` 订阅，不 dispose，生命周期归宿主）；
- 组件只读 state、调 vm 命令，不直接读写 ConfigHandler / 凭据 / 推进宿主；
- R16：事件处理器经 ``page.run_task`` 提交异步命令，不阻塞渲染；
- R2：async 回调显式 ``except asyncio.CancelledError: raise``；
- R23：颜色经 ``AppColors``、字号 / 宽度 / 间距经 ``AppStyles``。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from collections.abc import Callable, Sequence
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import flet as ft

from ui.components.config_panels.model_connection_form import ModelConnectionForm
from ui.components.config_panels.scenario_route_editor import ScenarioRouteEditor
from ui.components.flet_type_helpers import get_control_value, safe_on_change, safe_on_click
from ui.components.settings_widgets import DashboardCard, SectionHeader
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.testing.anchor import anchored
from ui.testing.e2e_ids import EIDS
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.model_connections_view_model import (
    ConnectionRow,
    ModelConnectionsState,
    ModelConnectionsViewModel,
)
from ui.viewmodels.model_routes_view_model import ModelRoutesState, ModelRoutesViewModel
from ui.viewmodels.model_usage_view_model import (
    PAGE_SIZE,
    ModelUsageState,
    ModelUsageViewModel,
)
from utils.sanitizers import DataSanitizer
from utils.time_utils import get_now

logger = logging.getLogger(__name__)

__all__ = ["ModelManagementPanel"]

#: 页签索引。
_TAB_CONNECTIONS = 0
_TAB_ROUTES = 1
_TAB_USAGE = 2

#: 状态过滤选项（all / enabled / disabled）。
_STATUS_FILTER_KEYS: dict[str, str] = {
    "all": "model_management_filter_all",
    "enabled": "model_management_filter_enabled",
    "disabled": "model_management_filter_disabled",
}

#: 业务 / 测试来源过滤选项。
_ORIGIN_FILTER_KEYS: dict[str, str] = {
    "all": "model_management_filter_all",
    "business": "model_usage_origin_business",
    "test": "model_usage_origin_test",
}

#: 场景过滤选项 label i18n 键（值为 SCENARIO_IDS 子集）。
_SCENARIO_FILTER_KEYS: dict[str, str] = {
    "news_annotation": "model_routes_scenario_news_annotation",
    "stock_analysis": "model_routes_scenario_stock_analysis",
    "news_risk": "model_routes_scenario_news_risk",
}

#: 子任务过滤选项（与 ai_service task_id 口径一致）。
_TASK_FILTER_KEYS: dict[str, str] = {
    "sentiment": "model_usage_task_sentiment",
    "analysis": "model_usage_task_analysis",
    "risk": "model_usage_task_risk",
}

#: 终态过滤选项（DAO 派生列枚举；见 ai_usage_dao 的 ``_REQUEST_PAGE_SELECT_SQL``）。
_TERMINAL_FILTER_KEYS: dict[str, str] = {
    "succeeded": "model_usage_terminal_succeeded",
    "cancelled": "model_usage_terminal_cancelled",
    "running": "model_usage_terminal_running",
    "failed": "model_usage_terminal_failed",
    "skipped": "model_usage_terminal_skipped",
    "unknown": "model_usage_terminal_unknown",
}

#: 费用状态过滤选项（DAO 派生列枚举）。
_COST_FILTER_KEYS: dict[str, str] = {
    "known": "model_usage_cost_known",
    "partial": "model_usage_cost_partial",
    "unpriced": "model_usage_cost_unpriced",
    "unknown": "model_usage_cost_unknown",
}

#: 尝试行状态 → i18n key（``ai_attempt.status``：dispatched / succeeded / failed / cancelled）。
#: 跳过行（``executed=False``）不按此映射，显示「未执行」（§11.5）。
_ATTEMPT_STATUS_KEYS: dict[str, str] = {
    "dispatched": "model_usage_terminal_running",
    "succeeded": "model_usage_terminal_succeeded",
    "failed": "model_usage_terminal_failed",
    "cancelled": "model_usage_terminal_cancelled",
}

#: 未选择过滤值（下拉「全部」）语义。
_FILTER_ALL = "all"


# ---------------------------------------------------------------------------
# 模块级辅助（纯函数 / 事件包装，便于单测与复用）
# ---------------------------------------------------------------------------


def _render_message(msg: Message | None) -> str:
    """渲染 Message 到本地化文本（嵌套 ``_key`` 参数先翻译后填入主模板）。"""
    if msg is None:
        return ""
    params = dict(msg.params)
    for k in list(params):
        if k.endswith("_key") and isinstance(params[k], str):
            params[k[:-4]] = I18n.get(params[k])
            del params[k]
    return I18n.get(msg.key, **params)


def _click(call: Callable[[], None]) -> Callable[[ft.ControlEvent], None]:
    """把无参同步命令包装为 Flet on_click 处理器（R16：只调同步 VM 命令）。"""

    def _on_click(_e: ft.ControlEvent) -> None:
        call()

    return _on_click


def _enum_text(value: Any, mapping: dict[str, str], fallback_key: str) -> str:
    """已知枚举值经映射本地化；未知值回退到明确的「未知」文案（不展示原始枚举）。"""
    key = mapping.get(str(value or ""))
    return I18n.get(key if key is not None else fallback_key)


def _run_task_handler(coro_func: Callable, *args: Any, **kwargs: Any) -> Callable[[ft.ControlEvent], None]:
    """把异步命令包装为 on_click 处理器，经 ``page.run_task`` 提交（R16）。"""

    def _on_click(_e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(coro_func, *args, **kwargs)
        except RuntimeError:
            logger.debug("[ModelManagementPanel] page unavailable for %s", getattr(coro_func, "__name__", coro_func))

    return _on_click


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``；未在渲染上下文（单元测试）时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _sync_file_picker_service(page: ft.Page | None, picker: ft.FilePicker | None, attach: bool) -> None:
    """注册 / 移除 FilePicker 服务（沿 screener_view 既有模式）。"""
    if page is not None and picker is not None:
        if attach and picker not in page.services:
            page.services.append(picker)
        elif not attach and picker in page.services:
            page.services.remove(picker)


def _parse_date_filter(value: str, *, end_of_day: bool) -> dt.datetime | None:
    """用户输入的本地日期（``YYYY-MM-DD``, Asia/Shanghai）→ ``created_at`` 的 naive UTC 墙钟边界。

    ``end_of_day=True`` 取次日零点（DAO 以 ``created_at < end`` 为排他上界）。
    非法 / 空值返回 ``None``：空值清除该过滤；非法值不静默放宽（交由 VM 侧原样呈现）。
    """
    text = value.strip()
    if not text:
        return None
    try:
        day = dt.date.fromisoformat(text)
    except ValueError:
        return None
    tz = ZoneInfo("Asia/Shanghai")
    base = day + dt.timedelta(days=1) if end_of_day else day
    local = dt.datetime.combine(base, dt.time.min, tzinfo=tz)
    return local.astimezone(dt.UTC).replace(tzinfo=None)


async def _export_csv_and_notify(
    vm: ModelUsageViewModel, file_picker: ft.FilePicker | None, show_snack: Callable
) -> None:
    """导出脱敏 CSV 并真实落盘（沿 screener_view 范式：web 走 ``src_bytes``，桌面写路径）。

    VM 的 ``export_csv`` 构建文本、``write_csv`` 负责桌面落盘（§11.2：写盘副作用归 VM，
    View 只做 FilePicker）；web 路径经 ``save_file(src_bytes=...)`` 不落盘。
    """
    try:
        csv_text = await vm.export_csv()
    except asyncio.CancelledError:
        raise  # R2
    except Exception:
        show_snack(I18n.get("model_usage_export_failed"), color=AppColors.ERROR)
        return

    page = _get_page()
    if file_picker is None or page is None:
        return
    _sync_file_picker_service(page, file_picker, True)
    filename = f"ai_usage_{get_now().strftime('%Y%m%d_%H%M%S')}.csv"
    try:
        if page.web:
            await file_picker.save_file(
                dialog_title=I18n.get("model_usage_export"),
                file_name=filename,
                allowed_extensions=["csv"],
                src_bytes=csv_text.encode("utf-8-sig"),
            )
        else:
            filepath = await file_picker.save_file(
                dialog_title=I18n.get("model_usage_export"),
                file_name=filename,
                allowed_extensions=["csv"],
            )
            if not filepath:
                return
            # R16: 同步文件 IO 经 VM 命令走 IO 线程池，不阻塞事件循环（落盘属 VM 副作用）。
            await vm.write_csv(filepath, csv_text)
        show_snack(I18n.get("model_usage_export_ready"), color=AppColors.SUCCESS)
    except asyncio.CancelledError:
        raise  # R2
    except Exception as ex:
        logger.error("[ModelManagementPanel] CSV export failed: %s", DataSanitizer.sanitize_error(ex))
        show_snack(I18n.get("model_usage_export_failed"), color=AppColors.ERROR)


async def _rebase_all_and_notify(
    connections_vm: ModelConnectionsViewModel,
    routes_vm: ModelRoutesViewModel,
    show_snack: Callable,
) -> None:
    """手动重基两 VM 快照（F30 显式刷新入口）：只前移基线，保留未提交草稿。

    并发重基（两 VM 独立状态域，无共享可变状态）；任一失败即按失败反馈，
    不伪装成功（R21）。R2: CancelledError 显式传播。
    """
    try:
        ok_conn, ok_routes = await asyncio.gather(connections_vm.rebase_snapshot(), routes_vm.rebase_snapshot())
    except asyncio.CancelledError:
        raise  # R2
    if ok_conn and ok_routes:
        show_snack(I18n.get("model_management_refresh_done"), color=AppColors.SUCCESS)
    else:
        show_snack(I18n.get("model_management_refresh_failed"), color=AppColors.ERROR)


def _fmt_decimal(value: Decimal | None) -> str:
    """Decimal 展示：缺失用「未知」而非 0（R21）。"""
    return f"{value:.2f}" if value is not None else I18n.get("model_usage_unknown")


def _summary_chip(label: str, value: str) -> ft.Control:
    return ft.Row(
        [
            ft.Text(f"{label}:", size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
            ft.Text(value, size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_PRIMARY, weight=ft.FontWeight.BOLD),
        ],
        spacing=AppStyles.SPACING_XS,
        tight=True,
    )


def _hint_text(key: str) -> ft.Control:
    return ft.Text(I18n.get(key), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT, key=key)


def _empty_text(key: str) -> ft.Control:
    return ft.Text(I18n.get(key), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_HINT, italic=True, key=key)


def _message_text(msg: Message) -> ft.Control:
    return ft.Text(_render_message(msg), size=AppStyles.FONT_SIZE_BODY_SM, color=AppColors.TEXT_SECONDARY)


# ---------------------------------------------------------------------------
# 页头紧凑摘要（§11.1）
# ---------------------------------------------------------------------------


def _build_summary_row(state: ModelConnectionsState) -> ft.Control:
    """标题下一行紧凑摘要；未知费用 / 未统计数缺失时不渲染（R21）。"""
    summary = state.summary
    cost_text = summary.known_cost_cny if summary.known_cost_cny is not None else I18n.get("model_usage_unknown")
    items: list[ft.Control] = [
        _summary_chip(I18n.get("model_management_summary_scenarios"), str(summary.executable_scenarios)),
        _summary_chip(I18n.get("model_management_summary_attention"), str(summary.configs_needing_attention)),
        _summary_chip(I18n.get("model_management_summary_cost"), cost_text),
    ]
    if summary.unknown_cost_count is not None:
        items.append(_summary_chip(I18n.get("model_management_summary_unknown_cost"), str(summary.unknown_cost_count)))
    if summary.unpriced_count is not None:
        items.append(_summary_chip(I18n.get("model_management_summary_unpriced"), str(summary.unpriced_count)))
    return ft.Row(items, spacing=AppStyles.SPACING_MD, wrap=True)


# ---------------------------------------------------------------------------
# 「模型连接」页
# ---------------------------------------------------------------------------


def _build_connection_row(row: ConnectionRow, vm: ModelConnectionsViewModel) -> ft.Control:
    status_text = I18n.get("model_management_status_enabled" if row.enabled else "model_management_status_disabled")
    status_color = AppColors.SUCCESS if row.enabled else AppColors.TEXT_DISABLED
    billing_mode_key = (
        "model_connection_billing_estimate_cost"
        if row.billing.mode == "estimate_cost"
        else "model_connection_billing_usage_only"
    )
    billing_text = I18n.get(billing_mode_key) + (f" / {row.billing.price_display}" if row.billing.price_display else "")
    ref_text = I18n.get("model_management_ref_count", count=row.scenario_ref_count)
    return ft.Container(
        key=f"connection_row_{row.connection_id}",
        content=ft.Row(
            [
                ft.Column(
                    [
                        ft.Text(
                            row.name or row.connection_id,
                            size=AppStyles.FONT_SIZE_BODY_SM,
                            color=AppColors.TEXT_PRIMARY,
                            weight=ft.FontWeight.W_500,
                        ),
                        ft.Text(row.model, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                    ],
                    spacing=AppStyles.SPACING_XS,
                    tight=True,
                    expand=True,
                ),
                ft.Text(status_text, size=AppStyles.FONT_SIZE_CAPTION, color=status_color),
                ft.Text(billing_text, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                ft.Text(ref_text, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_HINT),
                anchored(
                    EIDS.MODEL.connection_edit(row.connection_id),
                    ft.TextButton(
                        content=I18n.get("model_management_edit_connection"),
                        icon=ft.Icons.EDIT,
                        key="model_connection_edit",
                        tooltip=I18n.get("model_management_edit_connection"),
                        on_click=safe_on_click(_click(lambda: vm.edit(row.connection_id))),
                    ),
                ),
            ],
            spacing=AppStyles.SPACING_SM,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
        bgcolor=AppColors.SURFACE_VARIANT,
    )


def _build_connection_editor(
    state: ModelConnectionsState, vm: ModelConnectionsViewModel, *, compact: bool
) -> ft.Control:
    """三步创建 / 编辑：共用字段组 + 步骤导航 + 保存 / 测试 / 回执恢复。"""
    step = state.editor_step
    actions: list[ft.Control] = [
        anchored(
            EIDS.MODEL.EDITOR_PREV,
            ft.TextButton(
                content=I18n.get("model_management_prev_step"),
                key="model_editor_prev",
                disabled=step <= 1,
                on_click=safe_on_click(_click(lambda: vm.set_editor_step(step - 1))),
            ),
        ),
        anchored(
            EIDS.MODEL.EDITOR_NEXT,
            ft.TextButton(
                content=I18n.get("model_management_next_step"),
                key="model_editor_next",
                disabled=step >= 3,
                on_click=safe_on_click(_click(lambda: vm.set_editor_step(step + 1))),
            ),
        ),
        anchored(
            EIDS.MODEL.EDITOR_TEST,
            ft.TextButton(
                content=I18n.get("model_management_test_connection"),
                key="model_editor_test",
                on_click=safe_on_click(_run_task_handler(vm.test)),
            ),
        ),
        anchored(
            EIDS.MODEL.EDITOR_SAVE,
            ft.Button(
                content=I18n.get("model_connection_btn_save"),
                icon=ft.Icons.SAVE,
                key="model_editor_save",
                style=AppStyles.primary_button(),
                on_click=safe_on_click(_run_task_handler(vm.save)),
            ),
        ),
    ]
    if state.commit_unknown_pending:
        actions.insert(
            0,
            anchored(
                EIDS.MODEL.EDITOR_RESOLVE,
                ft.TextButton(
                    content=I18n.get("model_management_resolve_outcome"),
                    key="model_editor_resolve",
                    on_click=safe_on_click(_run_task_handler(vm.resolve_commit_unknown)),
                ),
            ),
        )
    return ft.Container(
        key="model_connection_editor",
        content=ft.Column(
            [
                ft.Text(
                    I18n.get("model_management_editor_title"),
                    size=AppStyles.FONT_SIZE_TITLE,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ModelConnectionForm(vm=vm, editor_step=step, compact=compact),
                ft.Row(actions, spacing=AppStyles.SPACING_SM, wrap=True),
            ],
            spacing=AppStyles.SPACING_SM,
        ),
        padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        border=ft.Border.all(1, AppColors.BORDER),
        border_radius=6,
    )


def _build_connections_view(
    state: ModelConnectionsState,
    vm: ModelConnectionsViewModel,
    *,
    compact: bool,
) -> ft.Control:
    controls: list[ft.Control] = [
        ft.Row(
            [
                anchored(
                    EIDS.MODEL.SEARCH_INPUT,
                    ft.TextField(
                        hint_text=I18n.get("model_management_search_hint"),
                        tooltip=I18n.get("model_management_search_hint"),
                        value=state.search_query,
                        width=AppStyles.CONTROL_WIDTH_MD,
                        bgcolor=AppColors.INPUT_BG,
                        color=AppColors.INPUT_TEXT,
                        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                        on_change=safe_on_change(lambda e: vm.set_search(get_control_value(e.control, ft.TextField))),
                    ),
                ),
                anchored(
                    EIDS.MODEL.STATUS_FILTER,
                    ft.Dropdown(
                        value=state.status_filter,
                        width=AppStyles.CONTROL_WIDTH_SM,
                        options=[ft.dropdown.Option(key=k, text=I18n.get(v)) for k, v in _STATUS_FILTER_KEYS.items()],
                        on_select=safe_on_change(
                            lambda e: vm.set_status_filter(get_control_value(e.control, ft.Dropdown))
                        ),
                    ),
                ),
                anchored(
                    EIDS.MODEL.ADD_CONNECTION,
                    ft.Button(
                        content=I18n.get("model_management_add_connection"),
                        icon=ft.Icons.ADD,
                        key="model_add_connection",
                        style=AppStyles.primary_button(),
                        on_click=safe_on_click(_click(vm.start_add)),
                    ),
                ),
            ],
            spacing=AppStyles.SPACING_SM,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
    ]

    rows = vm.visible_connections()
    if rows:
        controls.extend(_build_connection_row(row, vm) for row in rows)
    else:
        controls.append(_empty_text("model_management_no_connections"))

    if state.editor_open:
        controls.append(_build_connection_editor(state, vm, compact=compact))
    if state.message is not None:
        controls.append(_message_text(state.message))
    return ft.Column(controls, spacing=AppStyles.SPACING_SM)


# ---------------------------------------------------------------------------
# 「场景配置」页
# ---------------------------------------------------------------------------


def _build_routes_view(
    state: ModelRoutesState,
    vm: ModelRoutesViewModel,
    connections_vm: ModelConnectionsViewModel,
    *,
    compact: bool,
) -> ft.Control:
    controls: list[ft.Control] = [
        ScenarioRouteEditor(vm=vm, egress_vm=connections_vm, compact=compact),
        ft.Row(
            [
                anchored(
                    EIDS.MODEL.ROUTES_SAVE_CURRENT,
                    ft.Button(
                        content=I18n.get("model_routes_save_current"),
                        icon=ft.Icons.SAVE,
                        key="routes_save_current",
                        style=AppStyles.primary_button(),
                        on_click=safe_on_click(_run_task_handler(vm.save_current_scenario)),
                    ),
                ),
            ],
            alignment=ft.MainAxisAlignment.END,
        ),
    ]
    if state.message is not None:
        controls.append(_message_text(state.message))
    return ft.Column(controls, spacing=AppStyles.SPACING_SM)


# ---------------------------------------------------------------------------
# 「使用记录」页
# ---------------------------------------------------------------------------


def _reservation_amount(value: Any) -> Decimal | None:
    """预留金额（JSONB 文本）→ Decimal；缺失 / 坏值 → None（不伪零，R21）。"""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _build_release_controls(state: ModelUsageState, vm: ModelUsageViewModel) -> list[ft.Control]:
    """未确认详情逐条「解除未确认预留」（§11.5）；说明可能存在未入账费用、历史费用不变。

    无可解除对象（缺 reservation_id）时返回空列表——不渲染无目标按钮（R21 不伪装）。
    """
    releasable = [res for res in state.unconfirmed_reservations if str(res.get("reservation_id") or "")]
    if not releasable:
        return []
    controls: list[ft.Control] = [_hint_text("model_usage_release_hint")]
    for res in releasable:
        reservation_id = str(res["reservation_id"])
        target = I18n.get(
            "model_usage_unconfirmed_item",
            month=res.get("billing_month") or I18n.get("model_usage_unknown"),
            task=res.get("task_id") or I18n.get("model_usage_unknown"),
            amount=_fmt_decimal(_reservation_amount(res.get("amount_cny"))),
        )
        controls.append(
            ft.Row(
                [
                    ft.Text(target, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY),
                    ft.TextButton(
                        content=I18n.get("model_usage_release_button"),
                        icon=ft.Icons.LOCK_OPEN,
                        tooltip=I18n.get("model_usage_release_hint"),
                        on_click=safe_on_click(_run_task_handler(vm.release_unconfirmed_reservation, reservation_id)),
                    ),
                ],
                spacing=AppStyles.SPACING_SM,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                wrap=True,
            )
        )
    return controls


def _build_budget_summary(state: ModelUsageState, vm: ModelUsageViewModel) -> ft.Control:
    budget = state.budget_summary
    controls: list[ft.Control] = [
        ft.Row(
            [
                _summary_chip(I18n.get("model_usage_budget_known"), _fmt_decimal(budget.known_cny)),
                _summary_chip(I18n.get("model_usage_budget_effective"), _fmt_decimal(budget.effective_reserved_cny)),
                _summary_chip(
                    I18n.get("model_usage_budget_unconfirmed"), _fmt_decimal(budget.unconfirmed_reserved_cny)
                ),
            ],
            spacing=AppStyles.SPACING_MD,
            wrap=True,
        )
    ]
    # F36：月度软预算录入行（与历史月汇总分离）：金额输入 + 保存；当前值回显供重开核对。
    controls.append(_build_budget_edit_row(state, vm))
    # B1（§10.3「未确认预留需先明确处理」）：解除入口不依赖「未确认金额 > 0」这一数值，
    # 否则金额为 None / 0 但存在 held_unconfirmed 明细时用户无法处置（被数值门控遮蔽）。
    controls.extend(_build_release_controls(state, vm))
    return ft.Column(controls, spacing=AppStyles.SPACING_XS)


def _build_budget_edit_row(state: ModelUsageState, vm: ModelUsageViewModel) -> ft.Control:
    """月度软预算录入行（F36）：金额输入 + 保存；当前值回显供重开核对。

    未设限额显式「未设置」（空输入保存即清除限额）、读取失败显式「未知」，
    均不以 0 伪装（R21）。金额经 VM 草稿状态承载（on_change 同步 setter，沿
    ``set_search`` 先例），保存经 ``page.run_task(vm.save_monthly_budget)`` 提交（R16）。
    """
    if state.monthly_limit_unknown:
        current_text = I18n.get("model_usage_unknown")
    elif state.monthly_limit_cny is None:
        current_text = I18n.get("model_usage_budget_limit_unset")
    else:
        current_text = f"{state.monthly_limit_cny:.2f} CNY"
    return ft.Row(
        [
            ft.Text(
                I18n.get("model_usage_budget_limit_label"),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
            ft.Text(current_text, size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_PRIMARY),
            anchored(
                EIDS.MODEL.USE_BUDGET_INPUT,
                ft.TextField(
                    value=state.budget_draft,
                    hint_text=I18n.get("model_usage_budget_limit_hint"),
                    tooltip=I18n.get("model_usage_budget_limit_hint"),
                    width=AppStyles.CONTROL_WIDTH_SM,
                    bgcolor=AppColors.INPUT_BG,
                    color=AppColors.INPUT_TEXT,
                    border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                    on_change=safe_on_change(lambda e: vm.set_budget_draft(get_control_value(e.control, ft.TextField))),
                ),
            ),
            anchored(
                EIDS.MODEL.USE_BUDGET_SAVE,
                ft.TextButton(
                    content=I18n.get("model_usage_budget_save"),
                    icon=ft.Icons.SAVE,
                    key="usage_budget_save",
                    on_click=safe_on_click(_run_task_handler(vm.save_monthly_budget)),
                ),
            ),
        ],
        key="usage_budget_edit_row",
        spacing=AppStyles.SPACING_SM,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _month_cost_text(value: Any) -> str:
    """月汇总行金额展示：缺失 → 「未知」（R21 不伪零）；数值统一两位小数。"""
    if value is None:
        return I18n.get("model_usage_unknown")
    try:
        return f"{Decimal(str(value)):.2f}"
    except (ArithmeticError, ValueError):
        return str(value)


def _month_row_text(row: dict[str, Any]) -> str:
    """月汇总场景行文本（F36）：场景 / 触发 / 请求 / 尝试 / 成功 / 已知费用。"""
    return I18n.get(
        "model_usage_month_row",
        scenario=str(row.get("scenario_id") or row.get("grouping_key") or I18n.get("model_usage_unknown")),
        trigger=str(row.get("trigger") or "-"),
        invocations=str(row.get("invocation_count") if row.get("invocation_count") is not None else "-"),
        attempts=str(row.get("attempt_count") if row.get("attempt_count") is not None else "-"),
        succeeded=str(row.get("succeeded_count") if row.get("succeeded_count") is not None else "-"),
        cost=_month_cost_text(row.get("cost_cny")),
    )


def _build_month_summary_section(state: ModelUsageState, vm: ModelUsageViewModel) -> ft.Control:
    """历史月汇总视图（F36）：返回明细 + 月份选择 + 查看月摘要与场景行。

    查看月独立于当前月：摘要（``summary_budget``）与行（``monthly_rows``）均来自
    选中月份的真实读取结果，不由当前月数据替代（F36 核心语义）；空结果显式
    「该月无汇总数据」（R21 不伪零）。
    """
    summary = state.summary_budget
    chips: list[ft.Control] = []
    if summary is not None:
        chips = [
            _summary_chip(I18n.get("model_usage_budget_known"), _fmt_decimal(summary.known_cny)),
            _summary_chip(I18n.get("model_usage_budget_effective"), _fmt_decimal(summary.effective_reserved_cny)),
            _summary_chip(I18n.get("model_usage_budget_unconfirmed"), _fmt_decimal(summary.unconfirmed_reserved_cny)),
        ]
    month_rows: list[ft.Control] = []
    if state.monthly_rows:
        for row in state.monthly_rows:
            month_rows.append(
                ft.Text(_month_row_text(row), size=AppStyles.FONT_SIZE_CAPTION, color=AppColors.TEXT_SECONDARY)
            )
    else:
        month_rows.append(_empty_text("model_usage_month_no_data"))
    controls: list[ft.Control] = [
        ft.Row(
            [
                anchored(
                    EIDS.MODEL.USE_EXIT_MONTHLY,
                    ft.TextButton(
                        content=I18n.get("model_usage_exit_monthly"),
                        icon=ft.Icons.ARROW_BACK,
                        key="usage_exit_monthly",
                        on_click=safe_on_click(_click(vm.exit_monthly_summary)),
                    ),
                ),
                ft.Text(
                    I18n.get("model_usage_month_pick_label"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_SECONDARY,
                ),
                anchored(
                    EIDS.MODEL.USE_MONTH_PICK,
                    ft.TextField(
                        value=state.month_draft,
                        hint_text=I18n.get("model_usage_month_pick_hint"),
                        tooltip=I18n.get("model_usage_month_pick_hint"),
                        width=AppStyles.CONTROL_WIDTH_SM,
                        bgcolor=AppColors.INPUT_BG,
                        color=AppColors.INPUT_TEXT,
                        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
                        on_change=safe_on_change(
                            lambda e: vm.set_month_draft(get_control_value(e.control, ft.TextField))
                        ),
                        on_submit=safe_on_click(_run_task_handler(vm.select_summary_month)),
                    ),
                ),
                anchored(
                    EIDS.MODEL.USE_MONTH_QUERY,
                    ft.TextButton(
                        content=I18n.get("model_usage_month_query"),
                        icon=ft.Icons.SEARCH,
                        key="usage_month_query",
                        on_click=safe_on_click(_run_task_handler(vm.select_summary_month)),
                    ),
                ),
            ],
            spacing=AppStyles.SPACING_SM,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    ]
    if chips:
        controls.append(ft.Row(chips, spacing=AppStyles.SPACING_MD, wrap=True))
    controls.extend(month_rows)
    return ft.Column(controls, spacing=AppStyles.SPACING_SM)


def _build_attempts(attempts: Sequence[dict[str, Any]]) -> list[ft.Control]:
    """展开尝试明细（§11.5）：候选序号 / 重试 / 状态 / 模型 / 失败·跳过原因 / 费用快照。

    跳过行（``executed=False``）文字标「未执行」、不展示虚构费用（§10.1 / R21）；
    状态经映射本地化，不展示原始枚举。``reason`` 对实际尝试为 ``error_code``、
    对跳过行为候选决策理由。
    """
    controls: list[ft.Control] = []
    for a in attempts:
        executed = bool(a.get("executed", True))
        if executed:
            status_text = _enum_text(a.get("status"), _ATTEMPT_STATUS_KEYS, "model_usage_terminal_unknown")
        else:
            status_text = I18n.get("model_usage_attempt_not_executed")
        parts = [
            I18n.get(
                "model_usage_attempt_line",
                index=a.get("candidate_index", ""),
                status=status_text,
                model=a.get("actual_model") or a.get("configured_model") or "",
            )
        ]
        retry = a.get("retry_index")
        if retry:
            parts.append(I18n.get("model_usage_attempt_retry", retry=retry))
        reason = a.get("reason")
        if reason:
            parts.append(I18n.get("model_usage_attempt_reason", reason=reason))
        cost = a.get("cost_cny")
        if executed and cost is not None and str(cost) != "":
            parts.append(I18n.get("model_usage_attempt_cost", cost=cost))
        controls.append(
            ft.Text(
                " · ".join(parts),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            )
        )
    return controls


def _build_request_rows(state: ModelUsageState, vm: ModelUsageViewModel) -> list[ft.Control]:
    if not state.requests:
        return [_empty_text("model_usage_no_requests")]
    rows: list[ft.Control] = []
    for req in state.requests:
        rid = str(req.get("invocation_id") or "")
        expanded = bool(rid) and state.expanded_id == rid
        detail: list[ft.Control] = []
        if expanded:
            detail = _build_attempts(state.attempts) or [_hint_text("model_usage_no_attempts")]
        rows.append(
            ft.Container(
                key=f"request_row_{rid}",
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Text(
                                    str(req.get("created_at") or ""),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                ft.Text(
                                    str(req.get("scenario_id") or ""),
                                    size=AppStyles.FONT_SIZE_BODY_SM,
                                    color=AppColors.TEXT_PRIMARY,
                                ),
                                ft.Text(
                                    _enum_text(
                                        req.get("terminal_status"),
                                        _TERMINAL_FILTER_KEYS,
                                        "model_usage_terminal_unknown",
                                    ),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                                ft.Text(
                                    _enum_text(
                                        req.get("cost_status"),
                                        _COST_FILTER_KEYS,
                                        "model_usage_cost_unknown",
                                    ),
                                    size=AppStyles.FONT_SIZE_CAPTION,
                                    color=AppColors.TEXT_SECONDARY,
                                ),
                            ],
                            spacing=AppStyles.SPACING_SM,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        *detail,
                    ],
                    spacing=AppStyles.SPACING_XS,
                ),
                padding=ft.Padding.symmetric(horizontal=12, vertical=8),
                border=ft.Border.all(1, AppColors.BORDER),
                border_radius=6,
                bgcolor=AppColors.SURFACE_VARIANT,
                on_click=safe_on_click(_run_task_handler(vm.expand, rid)) if rid else None,
            )
        )
    return rows


def _build_pagination(state: ModelUsageState, vm: ModelUsageViewModel) -> ft.Control:
    return ft.Row(
        [
            anchored(
                EIDS.MODEL.USE_PREV_PAGE,
                ft.TextButton(
                    content=I18n.get("model_usage_prev_page"),
                    key="usage_prev_page",
                    disabled=state.page <= 0,
                    on_click=safe_on_click(_run_task_handler(vm.prev_page)),
                ),
            ),
            ft.Text(
                I18n.get("model_usage_page_indicator", page=state.page + 1, total=state.total),
                size=AppStyles.FONT_SIZE_CAPTION,
                color=AppColors.TEXT_SECONDARY,
            ),
            anchored(
                EIDS.MODEL.USE_NEXT_PAGE,
                ft.TextButton(
                    content=I18n.get("model_usage_next_page"),
                    key="usage_next_page",
                    disabled=(state.page + 1) * PAGE_SIZE >= state.total,
                    on_click=safe_on_click(_run_task_handler(vm.next_page)),
                ),
            ),
        ],
        alignment=ft.MainAxisAlignment.CENTER,
        spacing=AppStyles.SPACING_MD,
    )


def _submit_filter_task(vm: ModelUsageViewModel, **changes: Any) -> None:
    """把筛选变化提交为异步重载任务（F35：事件 → ``vm.set_filter`` → 统一重载）。

    R16：事件处理器内不 await，经 ``page.run_task`` 提交 ``set_filter`` 协程；
    无页面上下文（单元测试）时跳过。
    """
    page = _get_page()
    if page is None:
        return
    page.run_task(vm.set_filter, **changes)


def _select_filter_handler(vm: ModelUsageViewModel, field: str) -> Callable[[ft.ControlEvent], None]:
    """下拉过滤处理器工厂：``all`` 置 None，其余原样透传（非法值由 VM raise，不静默放宽）。

    F35：筛选变化经 ``_submit_filter_task`` 提交异步重载，不再只改控件状态。
    """

    def _handler(e: ft.ControlEvent) -> None:
        value = get_control_value(e.control, ft.Dropdown)
        _submit_filter_task(vm, **{field: None if value == _FILTER_ALL else value})

    return _handler


def _date_filter_handler(vm: ModelUsageViewModel, field: str, *, end_of_day: bool) -> Callable[[ft.ControlEvent], None]:
    """日期过滤处理器工厂：仅当输入为完整合法日期或空值时更新过滤（半截 / 非法输入不改动）。

    F35：合法输入经 ``_submit_filter_task`` 提交异步重载。
    """

    def _handler(e: ft.ControlEvent) -> None:
        raw = (get_control_value(e.control, ft.TextField) or "").strip()
        if raw and len(raw) != 10:
            return  # 输入未完成：不触发查询
        parsed = _parse_date_filter(raw, end_of_day=end_of_day)
        if raw and parsed is None:
            return  # 非法日期：不改变过滤
        _submit_filter_task(vm, **{field: parsed})

    return _handler


def _format_date_filter(value: dt.datetime | None, *, end_of_day: bool) -> str:
    """``created_at`` 的 naive UTC 墙钟边界 → 用户可读的本地日期（Asia/Shanghai）。"""
    if not isinstance(value, dt.datetime):
        return ""
    local = value.replace(tzinfo=dt.UTC).astimezone(ZoneInfo("Asia/Shanghai"))
    day = local.date() - dt.timedelta(days=1) if end_of_day else local.date()
    return day.isoformat()


def _option_list(options: dict[str, str]) -> list[ft.dropdown.Option]:
    return [ft.dropdown.Option(key=k, text=I18n.get(v)) for k, v in options.items()]


def _date_field(value: str, *, hint_key: str, on_change: Callable[[ft.ControlEvent], None]) -> ft.TextField:
    return ft.TextField(
        value=value,
        hint_text=I18n.get(hint_key),
        tooltip=I18n.get(hint_key),
        width=AppStyles.CONTROL_WIDTH_SM,
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
        on_change=safe_on_change(on_change),
    )


def _filter_dropdown(
    value: str | None, options: list[ft.dropdown.Option], on_select: Callable[[ft.ControlEvent], None]
) -> ft.Dropdown:
    return ft.Dropdown(
        value=value or _FILTER_ALL,
        width=AppStyles.CONTROL_WIDTH_SM,
        options=options,
        on_select=safe_on_change(on_select),
    )


def _build_usage_filters(
    state: ModelUsageState,
    vm: ModelUsageViewModel,
    connections: Sequence[ConnectionRow],
) -> ft.Control:
    """使用记录过滤区（§11.5）：日期起止 / 场景 / 子任务 / 连接 / 业务·测试 / 终态 / 费用状态。

    连接下拉取已提交连接列表（名称优先，缺失回退 id）；下拉「全部」→ 过滤置 None。
    """
    f = state.filters
    all_option = {"all": "model_management_filter_all"}
    scenario_options = _option_list({**all_option, **_SCENARIO_FILTER_KEYS})
    task_options = _option_list({**all_option, **_TASK_FILTER_KEYS})
    terminal_options = _option_list({**all_option, **_TERMINAL_FILTER_KEYS})
    cost_options = _option_list({**all_option, **_COST_FILTER_KEYS})
    connection_options = [ft.dropdown.Option(key=_FILTER_ALL, text=I18n.get("model_management_filter_all"))]
    connection_options.extend(
        ft.dropdown.Option(key=row.connection_id, text=(row.name or row.connection_id)) for row in connections
    )
    return ft.Row(
        [
            anchored(
                EIDS.MODEL.USE_START_FILTER,
                _date_field(
                    _format_date_filter(f.start, end_of_day=False),
                    hint_key="model_usage_filter_start_hint",
                    on_change=_date_filter_handler(vm, "start", end_of_day=False),
                ),
            ),
            anchored(
                EIDS.MODEL.USE_END_FILTER,
                _date_field(
                    _format_date_filter(f.end, end_of_day=True),
                    hint_key="model_usage_filter_end_hint",
                    on_change=_date_filter_handler(vm, "end", end_of_day=True),
                ),
            ),
            anchored(
                EIDS.MODEL.USE_SCENARIO_FILTER,
                _filter_dropdown(f.scenario_id, scenario_options, _select_filter_handler(vm, "scenario_id")),
            ),
            anchored(
                EIDS.MODEL.USE_TASK_FILTER,
                _filter_dropdown(f.task_id, task_options, _select_filter_handler(vm, "task_id")),
            ),
            anchored(
                EIDS.MODEL.USE_CONNECTION_FILTER,
                _filter_dropdown(f.connection_id, connection_options, _select_filter_handler(vm, "connection_id")),
            ),
            anchored(
                EIDS.MODEL.USE_ORIGIN_FILTER,
                _filter_dropdown(f.origin, _option_list(_ORIGIN_FILTER_KEYS), _select_filter_handler(vm, "origin")),
            ),
            anchored(
                EIDS.MODEL.USE_TERMINAL_FILTER,
                _filter_dropdown(f.terminal_status, terminal_options, _select_filter_handler(vm, "terminal_status")),
            ),
            anchored(
                EIDS.MODEL.USE_COST_FILTER,
                _filter_dropdown(f.cost_status, cost_options, _select_filter_handler(vm, "cost_status")),
            ),
        ],
        spacing=AppStyles.SPACING_SM,
        wrap=True,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def _build_usage_view(
    state: ModelUsageState,
    vm: ModelUsageViewModel,
    connections: Sequence[ConnectionRow],
    file_picker: ft.FilePicker | None,
    show_snack: Callable,
) -> ft.Control:
    # F36：明细视图（预算录入 + 过滤 + 请求行）与历史月汇总视图分离——
    # 月汇总模式只渲染月汇总区（返回 / 月份选择 / 查看月数据），不再渲染
    # 当前月预算摘要与请求行，避免历史月数据被当前月替代（R21）。
    controls: list[ft.Control] = []
    if state.monthly_summary_mode:
        controls.append(_build_month_summary_section(state, vm))
    else:
        controls.append(_build_budget_summary(state, vm))
        controls.append(_build_usage_filters(state, vm, connections))
        controls.append(
            ft.Row(
                [
                    anchored(
                        EIDS.MODEL.USE_ENTER_MONTHLY,
                        ft.TextButton(
                            content=I18n.get("model_usage_enter_monthly"),
                            key="usage_enter_monthly",
                            # enter_monthly_summary 为 async（F36：进入即加载查看月数据），
                            # 经 page.run_task 提交（R16）。
                            on_click=safe_on_click(_run_task_handler(vm.enter_monthly_summary)),
                        ),
                    ),
                    anchored(
                        EIDS.MODEL.USE_EXPORT,
                        ft.TextButton(
                            content=I18n.get("model_usage_export"),
                            icon=ft.Icons.DOWNLOAD,
                            key="usage_export",
                            on_click=safe_on_click(
                                _run_task_handler(_export_csv_and_notify, vm, file_picker, show_snack)
                            ),
                        ),
                    ),
                ],
                spacing=AppStyles.SPACING_SM,
                wrap=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        )
    if state.monthly_summary_mode:
        controls.append(_hint_text("model_usage_monthly_mode_note"))
    else:
        if state.retention_exceeded:
            controls.append(_hint_text("model_usage_retention_hint"))
        controls.extend(_build_request_rows(state, vm))
        controls.append(_build_pagination(state, vm))
    if state.message is not None:
        controls.append(_message_text(state.message))
    return ft.Column(controls, spacing=AppStyles.SPACING_SM)


# ---------------------------------------------------------------------------
# 组件
# ---------------------------------------------------------------------------


@ft.component
def ModelManagementPanel(
    connections_vm: ModelConnectionsViewModel,
    routes_vm: ModelRoutesViewModel,
    usage_vm: ModelUsageViewModel,
    show_snack_callback: Callable,
    *,
    compact: bool = False,
) -> ft.Control:
    """设置「模型管理」三页签组合（声明式，消费宿主创建的外部 VM）。

    Args:
        connections_vm: 「模型连接」页 ViewModel（外部 VM 模式，宿主管理生命周期）。
        routes_vm: 「场景配置」页 ViewModel。
        usage_vm: 「使用记录」页 ViewModel。
        show_snack_callback: 宿主传入的 snackbar 触发函数（用于导出 / 操作反馈）。
        compact: 紧凑布局（窄宿主）。
    """
    # --- 订阅三个 VM（外部 VM 模式，本组件不 dispose） ---
    conn_state, _ = use_viewmodel(vm=connections_vm)
    routes_state, _ = use_viewmodel(vm=routes_vm)
    usage_state, _ = use_viewmodel(vm=usage_vm)

    # --- 订阅 i18n / theme（热切换自动重渲染） ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- FilePicker（CSV 导出落盘；经 page.services 注册，沿 screener_view 模式） ---
    file_picker = ft.use_ref(lambda: ft.FilePicker()).current
    ft.use_effect(
        lambda: _sync_file_picker_service(_get_page(), file_picker, True),
        cleanup=lambda: _sync_file_picker_service(_get_page(), file_picker, False),
    )

    selected_tab, set_selected_tab = ft.use_state(_TAB_CONNECTIONS)

    # 三个子页内容全量构造、条件渲染当前页（tab_pages[selected_tab]）。
    # BUG 修复：此处原使用 ft.TabBarView(expand=True)，但其 Flutter 侧是 page-view
    # viewport，要求有界主轴约束；本 panel 处于 AIBrainTab 的
    # Column(scroll=AUTO)（子项主轴约束无界）内，page-view 布局崩溃产生 NaN 并沿
    # 父链传染，导致 AI 大脑整层不绘制、hit test 失效（语义树仍完整，E2E 无法拦截；
    # 桌面客户端 release 模式吞掉布局异常，仅 UIA 呈现 Infinity 矩形）。
    # 故改为普通 Column 条件渲染：仅渲染当前选中页，切换由 set_selected_tab 驱动
    # 重渲染完成，TabBar 视觉/交互与 E2E 锚点全部保留。
    tab_pages = [
        ft.Column(
            [_build_connections_view(conn_state, connections_vm, compact=compact)],
            scroll=ft.ScrollMode.AUTO,
        ),
        ft.Column(
            [_build_routes_view(routes_state, routes_vm, connections_vm, compact=compact)],
            scroll=ft.ScrollMode.AUTO,
        ),
        ft.Column(
            [
                _build_usage_view(
                    usage_state,
                    usage_vm,
                    conn_state.connections,
                    file_picker,
                    show_snack_callback,
                )
            ],
            scroll=ft.ScrollMode.AUTO,
        ),
    ]

    # V1 三件套：selected_index/on_change 在 ft.Tabs 上（ft.TabBar 无这两个参数）。
    tabs = ft.Tabs(
        length=3,
        selected_index=selected_tab,
        animation_duration=300,
        expand=True,
        on_change=lambda e: set_selected_tab(e.control.selected_index),
        content=ft.Column(
            expand=True,
            controls=[
                ft.TabBar(
                    tabs=[
                        ft.Tab(
                            label=anchored(
                                EIDS.MODEL.tab("connection"),
                                ft.Text(I18n.get("model_management_tab_connections")),
                            )
                        ),
                        ft.Tab(
                            label=anchored(
                                EIDS.MODEL.tab("routes"),
                                ft.Text(I18n.get("model_management_tab_routes")),
                            )
                        ),
                        ft.Tab(
                            label=anchored(
                                EIDS.MODEL.tab("usage"),
                                ft.Text(I18n.get("model_management_tab_usage")),
                            )
                        ),
                    ]
                ),
                tab_pages[selected_tab],
            ],
        ),
    )

    return DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(
                            I18n.get("model_management_title"),
                            title_key="model_management_title",
                        ),
                        ft.Row(
                            [
                                # F30：显式重基入口——手动刷新两 VM 快照（保留未提交草稿）
                                anchored(
                                    EIDS.MODEL.PANEL_REFRESH,
                                    ft.IconButton(
                                        icon=ft.Icons.REFRESH,
                                        tooltip=I18n.get("model_management_refresh"),
                                        icon_size=AppStyles.FONT_SIZE_HEADLINE,
                                        icon_color=AppColors.PRIMARY,
                                        on_click=safe_on_click(
                                            _run_task_handler(
                                                _rebase_all_and_notify, connections_vm, routes_vm, show_snack_callback
                                            )
                                        ),
                                    ),
                                ),
                                ft.Icon(
                                    ft.Icons.MODEL_TRAINING,
                                    size=AppStyles.FONT_SIZE_HEADLINE,
                                    color=AppColors.PRIMARY,
                                ),
                            ],
                            spacing=AppStyles.SPACING_SM,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                _build_summary_row(conn_state),
                tabs,
            ],
            spacing=AppStyles.SPACING_MD,
        ),
    )
