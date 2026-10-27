"""ai_brain_tab — 声明式组件 (Phase E.1 + Task 5.2 + Phase 3.2 P1-1 + S5 §11.1).

从命令式容器子类重写为 ``@ft.component`` 函数组件范式
(CLAUDE.md §3.2 MVVM, §3.3 声明式 UI).

变更要点:
- 旧命令式 ``class AIBrainTab(ft.Container)`` → ``@ft.component def AIBrainTab(show_snack_callback)``
- S5 §11.1: 模型连接 / 场景配置 / 使用记录三类配置迁入 ``ui/components/model_management_panel.py``
  的 ``ModelManagementPanel``（三页签），由三个专用 VM
  （ModelConnectionsViewModel / ModelRoutesViewModel / ModelUsageViewModel）承载；
  本页不再构造 LLM/failover/local 子 VM，不再消费 LLMConfigPanel / FailoverConfigPanel /
  LocalModelConfigPanel / NewsSentimentConfigPanel
- AIBrainSettingsViewModel 通过 ``use_viewmodel(factory=)`` 内部模式实例化 (S5 后无参构造),
  仅保留本页仍负责的调优参数 (max_candidates / min_turnover)、AI / 新闻 Prompt 与
  「仅本地模式」开关, 以及三阶段保存状态机
- i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 订阅自动重渲染
- save_state 从 ai_settings_state.save_state 派生 (声明式自动重渲染)
- page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
- 异步保存: ``page.run_task`` 调度; R2 CancelledError 显式 raise
"""

import asyncio
import logging
import uuid
from collections.abc import Callable
from decimal import Decimal

import flet as ft

from ui.components.confirm_dialog import ConfirmDialog
from ui.components.flet_type_helpers import (
    get_control_value,
    safe_icon_str,
    safe_on_change,
    safe_on_click,
)
from ui.components.model_management_panel import ModelManagementPanel
from ui.components.settings_widgets import DashboardCard, SectionHeader, SettingRow
from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.ai_brain_settings_view_model import (
    SAVE_ERROR,
    SAVE_SAVING,
    SAVE_SUCCESS,
    AIBrainSettingsViewModel,
)
from ui.viewmodels.egress_audit_view_model import EgressAuditViewModel
from ui.viewmodels.model_connections_view_model import (
    ModelConnectionsViewModel,
    UsageCostSummary,
)
from ui.viewmodels.model_routes_view_model import ModelRoutesViewModel
from ui.viewmodels.model_usage_view_model import ModelUsageViewModel
from utils.config_models import DEFAULT_AI_PROMPT, DEFAULT_NEWS_PROMPT
from utils.log_decorators import UILogger
from utils.prompt_guard import MAX_PROMPT_LENGTH, validate_prompt
from utils.sanitizers import DataSanitizer

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_INPUT_WIDTH_SMALL = 190


# ============================================================================
# Module-level pure helpers
# ============================================================================


def _get_page() -> ft.Page | None:
    """安全获取 ``ft.context.page``, 未在渲染上下文时返回 None。"""
    try:
        return ft.context.page
    except RuntimeError:
        return None


def _validate_prompt_or_warn(prompt: str, show_snack: Callable) -> bool:
    """验证 Prompt 安全性，不合法时显示警告并返回 False。"""
    is_valid, warning = validate_prompt(prompt)
    if not is_valid:
        msg = I18n.get(warning, warning)
        if warning == "prompt_err_length":
            msg = I18n.get("prompt_err_length").format(max=MAX_PROMPT_LENGTH)
        show_snack(msg, color=AppColors.WARNING)
        return False
    return True


def _format_bytes(size_bytes: int) -> str:
    """人类可读字节格式化（SEC-03 数据出口面板数据量列展示）。"""
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def _ai_usage_dao_provider():
    """账本 DAO 惰性解析（D6）：engine 未就绪 / 未组装时返回 None（降级）。

    R1: ui 不可 import app 层; 本 provider 沿 data 层 CacheManager 直接解析, 与
    app.bootstrap._ai_usage_dao_provider 同口径但避开反向依赖。
    """
    from data.cache.cache_manager import CacheManager

    return getattr(CacheManager(), "ai_usage_dao", None)


async def _release_unconfirmed_reservation(reservation_id: str) -> bool:
    """解除未确认预留宿主副作用（§11.5「未确认详情」）：惰性解析账本 DAO 后调用写方法。

    与 ``_ai_usage_dao_provider`` 同口径惰性解析（engine 未就绪 / DAO 未组装返回 None）;
    DAO 不可用时以异常降级（VM 据异常记为释放失败, 不伪装成功, R21/R5）, 不崩溃;
    返回 ``True`` 表示确有预留由 ``held_unconfirmed`` 转为已解除，``False`` 表示该预留
    已不处于未确认状态（no-op）——VM 据此如实区分「已解除」与「无变更」（R21）;
    R2: 本方法不捕获 CancelledError, 由 VM 侧显式传播。
    """
    dao = _ai_usage_dao_provider()
    if dao is None:
        raise RuntimeError("ai_usage dao unavailable")
    return await dao.release_reservation(reservation_id=reservation_id)


async def _load_connections_usage_summary() -> UsageCostSummary:
    """「模型连接」页头本月已知费用（其余未知 / 未统计字段保持 None，R21 不伪零）。"""
    dao = _ai_usage_dao_provider()
    if dao is None:
        return UsageCostSummary()
    try:
        from utils.time_utils import get_now

        now = get_now()
        month = f"{now.year:04d}{now.month:02d}"
        summary = await dao.read_budget_summary(month=month)
    except asyncio.CancelledError:
        raise  # R2
    except Exception:
        return UsageCostSummary()
    known = summary.get("known_cny")
    return UsageCostSummary(known_cost_cny=str(known) if known is not None else None)


def _synthetic_adapters_provider():
    """单次测试适配器表（F15，S3 调度服务组装结果）；未组装时返回 None（测试降级）。

    与业务 Executor 同一组合根组装，但测试路径直连适配器、不经 Executor 业务授权
    通道（测试 consent 与业务 grant 两通道隔离）。
    """
    from services.ai_service.scenario_dispatch import get_scheduling_services

    services = get_scheduling_services()
    return services.get("adapters") if services else None


def _model_configuration_service():
    """模型配置服务（非单例，惰性导入以保持 ui 模块加载轻量）。"""
    from services.model_configuration_service import ModelConfigurationService

    return ModelConfigurationService()


async def _read_monthly_budget_limit() -> Decimal | None:
    """读取月软预算限额（``usage_policy.monthly_cny_limit``，F36 宿主副作用）。

    经 ModelConfigurationService 新鲜快照（CAS 同源，§5.1）读取；未设返回 None
    （合法语义）；读取失败 raise（VM 记 unknown 显式「未知」，不以 None 伪装
    「未设置」，R21）。快照读取已在服务内经线程池提交（R16）。
    """
    service = _model_configuration_service()
    snapshot = await service.get_snapshot()
    raw = snapshot.usage_policy.get("monthly_cny_limit")
    if raw is None or str(raw).strip() == "":
        return None
    return Decimal(str(raw))


async def _save_monthly_budget_limit(amount: Decimal | None) -> bool:
    """保存月软预算限额（CAS 三态，F36 宿主副作用）：True=已提交，False=未提交。

    payload 期望完整 model_management 域（connections / scenario_routes / usage_policy /
    schema_version），其余域沿已提交快照值；安全子域与迁移子域由 CAS 以磁盘现值
    保留（§5.3 / F07）。清除限额（``amount=None``）移除该键（未设语义）。
    revision 冲突 / not_committed 返回 False（VM 提示重试，不自动重试）；
    commit_unknown 凭回执确认一次（§5.1）后判定。R2: CancelledError 显式传播
    （不捕获，由 VM 侧传播）。
    """
    service = _model_configuration_service()
    snapshot = await service.get_snapshot()
    usage_policy = dict(snapshot.usage_policy)
    if amount is None:
        usage_policy.pop("monthly_cny_limit", None)
    else:
        usage_policy["monthly_cny_limit"] = str(amount)
    payload = {
        "schema_version": 1,
        "config_revision": snapshot.config_revision,
        "connections": [dict(c) for c in snapshot.connections],
        "scenario_routes": [dict(r) for r in snapshot.scenario_routes],
        "usage_policy": usage_policy,
    }
    operation_id = str(uuid.uuid4())
    outcome = await service.save_draft(payload, snapshot.config_revision, operation_id)
    if outcome.status == "commit_unknown":
        outcome = await service.resolve_save_outcome(operation_id)
    return outcome.status == "committed"


# ============================================================================
# AIBrainTab
# ============================================================================


@ft.component
def AIBrainTab(show_snack_callback: Callable) -> ft.Container:
    """AI Brain 配置标签页 (声明式).

    CLAUDE.md §3.2 MVVM + §3.3 声明式 UI + S5 §11.1:
    - 模型管理的三个 VM（连接 / 场景 / 使用）由 ``use_viewmodel(factory=)`` 内部模式
      实例化, 组合进 ``ModelManagementPanel`` 三页签; 本页不再持有子 VM
    - AIBrainSettingsViewModel 经 ``use_viewmodel(factory=)`` 实例化 (无参构造)
    - i18n/theme 通过 ``ft.use_state(*.get_observable_state)`` 自动重渲染
    - page 访问: ``ft.context.page`` (try/except 守卫), 不持有 page 引用
    - 异步保存: ``page.run_task`` 调度, R2 CancelledError 显式 raise
    - View 仅调 ai_settings_vm.save_ai_settings() 并据 state.save_state 展示反馈

    Args:
        show_snack_callback: 消费方(SettingsView)传入的 snackbar 触发函数
    """
    # --- Subscribe to i18n + theme changes (auto-rerender) ---
    ft.use_state(get_observable_state)
    ft.use_state(AppColors.get_observable_state)

    # --- 模型管理三页签 VM (内部模式: factory 首次渲染调用一次, 卸载 dispose) ---
    # F30: conn_state.config_revision / routes_state.commit_seq 供宿主协调 effect 消费
    conn_state, connections_vm = use_viewmodel(
        factory=lambda: ModelConnectionsViewModel(
            usage_summary_loader=_load_connections_usage_summary,
            synthetic_adapters=_synthetic_adapters_provider(),
        )
    )
    routes_state, routes_vm = use_viewmodel(factory=lambda: ModelRoutesViewModel(_model_configuration_service()))
    _usage_state, usage_vm = use_viewmodel(
        factory=lambda: ModelUsageViewModel(
            dao_provider=_ai_usage_dao_provider,
            reservation_releaser=_release_unconfirmed_reservation,
            budget_limit_reader=_read_monthly_budget_limit,
            budget_limit_saver=_save_monthly_budget_limit,
        )
    )

    # --- AIBrainSettingsViewModel: 仅本页调优参数 / Prompt / 仅本地模式 (S5 无参构造) ---
    ai_settings_state, ai_settings_vm = use_viewmodel(factory=AIBrainSettingsViewModel)

    # --- SEC-03 第二步: 数据出口聚合 ViewModel (今日/本月聚合 + 会话计数) ---
    egress_state, egress_vm = use_viewmodel(factory=EgressAuditViewModel)

    # --- 首次渲染挂载时各调度一次加载 (声明式 hook, 经 page.run_task, R16) ---
    def _trigger_load_model_management() -> None:
        page = _get_page()
        if page is None:
            return
        page.run_task(connections_vm.load)
        page.run_task(routes_vm.load)
        page.run_task(usage_vm.load)

    ft.use_effect(_trigger_load_model_management, dependencies=[])

    def _trigger_load_egress() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(egress_vm.load_aggregates)

    ft.use_effect(_trigger_load_egress, dependencies=[])

    # --- F30 宿主协调：双 VM 快照重基（同屏交替保存不产生陈旧冲突） ---
    # 连接域提交（保存 / 授权 / 撤销 / 回执恢复均递增 config_revision）→ 场景 VM 重基：
    # 刷新 CAS 基线与候选选择器，保留未提交草稿。首渲染（revision=0）跑一次为幂等预载。
    def _trigger_rebase_routes() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(routes_vm.rebase_snapshot)

    ft.use_effect(_trigger_rebase_routes, dependencies=[conn_state.config_revision])

    # 场景域提交（仅 save committed 递增 commit_seq）→ 连接 VM 重基：刷新其 _raw_routes，
    # 防止下次连接保存以陈旧路由回退路由修订（F30 核心危害）。
    def _trigger_rebase_connections() -> None:
        page = _get_page()
        if page is not None:
            page.run_task(connections_vm.rebase_snapshot)

    ft.use_effect(_trigger_rebase_connections, dependencies=[routes_state.commit_seq])

    # --- Pure UI state (VM state 是唯一真值源, 无 use_state 本地副本) ---
    # P1-4 批次 2: ConfirmDialog open_state (消费方驱动, 无 dirty state 检测 §0.5.11.1 #78)
    reset_ai_dialog_open, set_reset_ai_dialog_open = ft.use_state(False)
    reset_news_dialog_open, set_reset_news_dialog_open = ft.use_state(False)

    # --- 异步保存 (R2: CancelledError 显式 raise; 调用 VM commands) ---
    async def _do_save_ai_settings() -> None:
        """保存本页配置 (调优参数 + prompts + 仅本地模式)。

        View 仅调 ai_settings_vm.save_ai_settings() 并据 state.save_state 展示反馈;
        业务编排全部下沉到 VM。R2: CancelledError 显式 raise。
        """
        UILogger.log_action("AIBrainTab", "Click", "btn_save_ai")

        # Prompt 验证 (UI 反馈逻辑, 保留在 View)
        if not _validate_prompt_or_warn(ai_settings_state.ai_prompt_value, show_snack_callback):
            return
        if not _validate_prompt_or_warn(ai_settings_state.news_prompt_value, show_snack_callback):
            return

        try:
            await ai_settings_vm.save_ai_settings()
            current_state = ai_settings_vm.state
            if current_state.save_state == SAVE_ERROR:
                show_snack_callback(I18n.get("settings_save_failed"), color=AppColors.ERROR)
            elif current_state.save_state == SAVE_SUCCESS:
                show_snack_callback(I18n.get("settings_snack_ai_saved"))
        except asyncio.CancelledError:
            raise  # R2: 必须传播
        except Exception as e:
            from utils.error_classifier import classify_error, classify_severity

            error_info = classify_error(e, context="general")
            severity = classify_severity(e, context="general")
            sanitized = DataSanitizer.sanitize_error(e)  # F4-U-2: R9 异常消息脱敏
            if severity == "system":
                logger.critical("[AIBrainTab] SYSTEM-LEVEL failure saving config: %s", sanitized, exc_info=True)
            else:
                logger.error(
                    "[AIBrainTab] Error saving config (%s): %s",
                    error_info["code"],
                    sanitized,
                    exc_info=True,
                )
            show_snack_callback(
                I18n.get("settings_snack_ai_error").format(
                    error=I18n.get("settings_save_failed"),
                ),
                color=AppColors.ERROR,
            )

    def _on_save_ai(_e: ft.ControlEvent) -> None:
        page = _get_page()
        if page is not None:
            page.run_task(_do_save_ai_settings)

    def _on_reset_ai_prompt(_e: ft.ControlEvent) -> None:
        # P1-4 批次 2: 打开 ConfirmDialog 而非直接重置 (统一提示, 无 dirty state)
        set_reset_ai_dialog_open(True)

    def _on_reset_news_prompt(_e: ft.ControlEvent) -> None:
        set_reset_news_dialog_open(True)

    def _do_confirm_reset_ai_prompt() -> None:
        """ConfirmDialog on_confirm: 实际执行 AI prompt 重置 (P1-4 批次 2)."""
        ai_settings_vm.set_ai_prompt_value(DEFAULT_AI_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))
        set_reset_ai_dialog_open(False)

    def _do_cancel_reset_ai_prompt() -> None:
        """ConfirmDialog on_cancel: 关闭对话框, 不执行重置。"""
        set_reset_ai_dialog_open(False)

    def _do_confirm_reset_news_prompt() -> None:
        """ConfirmDialog on_confirm: 实际执行 news prompt 重置 (P1-4 批次 2)."""
        ai_settings_vm.set_news_prompt_value(DEFAULT_NEWS_PROMPT)
        show_snack_callback(I18n.get("settings_snack_prompt_reset"))
        set_reset_news_dialog_open(False)

    def _do_cancel_reset_news_prompt() -> None:
        """ConfirmDialog on_cancel: 关闭对话框, 不执行重置。"""
        set_reset_news_dialog_open(False)

    # --- Build controls (状态驱动: value/disabled/color 从 state 派生) ---
    ai_max_candidates_input = ft.TextField(
        label=I18n.get("settings_max_candidates"),
        value=ai_settings_state.max_candidates_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=30),
        tooltip=I18n.get("settings_hint_ai_cost"),
        on_change=lambda e: ai_settings_vm.set_max_candidates_value(e.control.value),
        on_submit=safe_on_change(_on_save_ai),  # UX-09: Enter = 保存 AI 设置主动作
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )
    strategy_min_turnover_input = ft.TextField(
        label=I18n.get("settings_min_turnover"),
        value=ai_settings_state.min_turnover_value,
        width=_INPUT_WIDTH_SMALL,
        keyboard_type=ft.KeyboardType.NUMBER,
        hint_text=I18n.get("ai_hint_default").format(val=2.0),
        tooltip=I18n.get("settings_hint_turnover"),
        on_change=lambda e: ai_settings_vm.set_min_turnover_value(e.control.value),
        on_submit=safe_on_change(_on_save_ai),  # UX-09: Enter = 保存 AI 设置主动作
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )
    ai_prompt_input = ft.TextField(
        label=I18n.get("settings_ai_prompt"),
        value=ai_settings_state.ai_prompt_value,
        multiline=True,
        min_lines=5,
        max_lines=15,
        text_size=AppStyles.FONT_SIZE_BODY_SM,
        hint_text=I18n.get("settings_ai_prompt_hint"),
        on_change=lambda e: ai_settings_vm.set_ai_prompt_value(e.control.value),
        # NOTE(lazy): 多行 Prompt 无 Ctrl+Enter 提交 (UX-09 P2-04) — Flet on_submit 无修饰键信息,
        # KeyboardListener 组合 hack 与 IME 冲突风险高 YAGNI. ceiling: 保存走显式"保存 AI 设置"按钮.
        # upgrade: Flet 提供带修饰键的提交事件 (如 on_submit 携带 modifierFlags) 或 KeyboardListener 方案
        # 经 IME 场景验证可行时恢复.
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )
    ai_news_prompt_input = ft.TextField(
        label=I18n.get("settings_news_prompt"),
        value=ai_settings_state.news_prompt_value,
        multiline=True,
        min_lines=3,
        max_lines=10,
        text_size=AppStyles.FONT_SIZE_BODY_SM,
        hint_text=I18n.get("settings_news_prompt_hint"),
        on_change=lambda e: ai_settings_vm.set_news_prompt_value(e.control.value),
        # NOTE(lazy): 同 ai_prompt_input — 无 Ctrl+Enter 提交 (UX-09 P2-04, 与上行三要素一致).
        # ceiling: 保存走显式"保存 AI 设置"按钮.
        # upgrade: Flet 提供带修饰键的提交事件 (如 on_submit 携带 modifierFlags) 时恢复.
        bgcolor=AppColors.INPUT_BG,
        color=AppColors.INPUT_TEXT,
        border=ft.OutlineInputBorder(side=ft.BorderSide(color=AppColors.INPUT_BORDER)),
    )

    btn_reset_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=safe_on_click(_on_reset_ai_prompt),
    )
    btn_reset_news_prompt = ft.TextButton(
        content=I18n.get("settings_reset_prompt"),
        icon=ft.Icons.RESTORE,
        on_click=safe_on_click(_on_reset_news_prompt),
    )

    # save_state 从 VM state 派生 (声明式自动重渲染)
    is_saving = ai_settings_state.save_state == SAVE_SAVING
    is_error = ai_settings_state.save_state == SAVE_ERROR
    is_success = ai_settings_state.save_state == SAVE_SUCCESS
    btn_save_ai = ft.Button(
        content=I18n.get("settings_save_ai"),
        icon=ft.Icons.SAVE,
        on_click=safe_on_click(_on_save_ai),
        style=AppStyles.primary_button(),
        height=40,
        disabled=is_saving,
    )
    save_progress = ft.ProgressRing(
        visible=is_saving,
        width=20,
        height=20,
        stroke_width=2,
    )

    # --- S5 §11.1: 模型管理三页签 (连接 / 场景 / 使用), 由三个专用 VM 承载 ---
    model_management_panel = ModelManagementPanel(
        connections_vm,
        routes_vm,
        usage_vm,
        show_snack_callback,
        compact=False,
    )

    # SEC-02: 向用户披露提示注入防护边界（UN-07/UN-08 AI 风险知情）。
    # 承认正则黑名单净化无法穷尽注入（prompt_guard SEC-003），并如实告知用户：
    # AI 结果仅供参考、勿作唯一决策依据。放置于模型管理正下方，用户在
    # 启用云端外发时即可见。
    card_data_boundary = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(
                            I18n.get("settings_ai_data_boundary_title"),
                            title_key="settings_ai_data_boundary_title",
                        ),
                        ft.Icon(ft.Icons.INFO_OUTLINE, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("settings_ai_data_boundary_body"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
            ],
        ),
    )

    # --- Tuning card (S5: 仅保留 max_candidates / min_turnover) ---
    icon_help_max = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=AppStyles.FONT_SIZE_TITLE,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_cap"),
    )
    icon_help_min = ft.Icon(
        ft.Icons.HELP_OUTLINE,
        size=AppStyles.FONT_SIZE_TITLE,
        color=AppColors.TEXT_HINT,
        tooltip=I18n.get("ai_hint_turnover_min"),
    )

    section_header_tuning = SectionHeader(
        I18n.get("settings_sec_tuning"),
        title_key="settings_sec_tuning",
    )
    card_tuning = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        section_header_tuning,
                        ft.Icon(ft.Icons.TUNE, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("ai_tuning_desc"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Container(height=10),
                ft.ResponsiveRow(
                    [
                        ft.Column(
                            [
                                ft.Row(
                                    [ai_max_candidates_input, icon_help_max],
                                    spacing=5,
                                ),
                            ],
                            col={"sm": 12, "md": 4},
                        ),
                        ft.Column(
                            [
                                ft.Row(
                                    [strategy_min_turnover_input, icon_help_min],
                                    spacing=5,
                                ),
                            ],
                            col={"sm": 12, "md": 4},
                        ),
                    ],
                    run_spacing=10,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
            ],
        ),
    )

    # --- Prompt card ---
    section_header_persona = SectionHeader(
        I18n.get("ai_sec_persona"),
        title_key="ai_sec_persona",
    )
    section_header_news_prompt = SectionHeader(
        I18n.get("settings_news_prompt"),
        title_key="settings_news_prompt",
    )
    card_prompt = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [section_header_persona, btn_reset_prompt],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ai_prompt_input,
                    border=ft.Border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                ),
                ft.Text(
                    I18n.get("settings_ai_prompt_hint"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
                ft.Divider(height=20, color=AppColors.BORDER),
                ft.Row(
                    [section_header_news_prompt, btn_reset_news_prompt],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Container(
                    content=ai_news_prompt_input,
                    border=ft.Border.all(1, AppColors.BORDER),
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.02, AppColors.BORDER),
                ),
                ft.Text(
                    I18n.get("settings_news_prompt_hint"),
                    size=AppStyles.FONT_SIZE_CAPTION,
                    color=AppColors.TEXT_HINT,
                ),
            ],
        ),
    )

    # --- SEC-03 第二步: 「仅本地模式」开关卡片 (禁用全部云端 LLM, 数据不出本机) ---
    # 开关仅更新 VM state (ai_local_only_mode), 点「保存 AI 设置」时随 save_ai_settings 持久化。
    def _on_local_only_toggle(e: ft.ControlEvent) -> None:
        ai_settings_vm.set_ai_local_only_mode(get_control_value(e.control, ft.Switch))

    local_only_switch = ft.Switch(
        label="",
        value=ai_settings_state.ai_local_only_mode,
        on_change=safe_on_change(_on_local_only_toggle),
    )
    row_local_only = SettingRow(
        icon=safe_icon_str(ft.Icons.PHONE_LOCKED),
        title=I18n.get("settings_ai_local_only_title"),
        subtitle=I18n.get("settings_ai_local_only_body"),
        control=local_only_switch,
        icon_color=AppColors.PRIMARY,
        title_key="settings_ai_local_only_title",
        subtitle_key="settings_ai_local_only_body",
    )
    card_local_only = DashboardCard(
        content=ft.Column(
            [
                row_local_only,
                ft.Divider(height=10, color=AppColors.TRANSPARENT),
                ft.Row(
                    [
                        ft.Icon(
                            ft.Icons.INFO_OUTLINE,
                            size=AppStyles.FONT_SIZE_TITLE,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                        ft.Text(
                            I18n.get("settings_ai_local_only_boundary"),
                            size=AppStyles.FONT_SIZE_CAPTION,
                            color=AppColors.TEXT_SECONDARY,
                        ),
                    ],
                    spacing=6,
                ),
            ],
        ),
    )

    # --- SEC-03 第二步: 「数据出口」审计面板 (今日/本月按目的地聚合 + 会话指示) ---
    def _build_egress_table(dest_rows: list) -> ft.DataTable | ft.Column:
        """构造今日/本月聚合表; 空 rows 时以空态文案替代表格。"""
        if not dest_rows:
            return ft.Column(
                [
                    ft.Text(
                        I18n.get("egress_empty"),
                        size=AppStyles.FONT_SIZE_BODY_SM,
                        color=AppColors.TEXT_SECONDARY,
                    ),
                ],
                spacing=4,
            )
        columns = [
            ft.DataColumn(label=ft.Text(I18n.get("egress_dest_col"))),
            ft.DataColumn(label=ft.Text(I18n.get("egress_count_col")), numeric=True),
            ft.DataColumn(label=ft.Text(I18n.get("egress_bytes_col")), numeric=True),
        ]
        rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(r.destination, size=AppStyles.FONT_SIZE_BODY_SM)),
                    ft.DataCell(ft.Text(str(r.count), size=AppStyles.FONT_SIZE_BODY_SM)),
                    ft.DataCell(ft.Text(_format_bytes(r.payload_size_bytes), size=AppStyles.FONT_SIZE_BODY_SM)),
                ],
            )
            for r in dest_rows
        ]
        return ft.DataTable(
            columns=columns,
            rows=rows,
            heading_row_color=AppColors.TABLE_HEADER_BG,
            data_row_color={ft.ControlState.HOVERED: AppColors.TABLE_ROW_HOVER},
            border=ft.Border.all(1, AppColors.DIVIDER),
            vertical_lines=ft.BorderSide(1, AppColors.DIVIDER),
        )

    egress_session_text = I18n.get("egress_session_indicator").format(count=egress_state.session_count)
    card_egress = DashboardCard(
        content=ft.Column(
            [
                ft.Row(
                    [
                        SectionHeader(I18n.get("egress_panel_title"), title_key="egress_panel_title"),
                        ft.Icon(ft.Icons.LOGOUT, size=AppStyles.FONT_SIZE_HEADLINE, color=AppColors.PRIMARY),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                ft.Text(
                    I18n.get("egress_panel_desc"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_SECONDARY,
                ),
                ft.Text(
                    egress_session_text,
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    color=AppColors.TEXT_PRIMARY,
                    weight=ft.FontWeight.BOLD,
                ),
                ft.Divider(height=20, color=AppColors.BORDER),
                ft.Text(
                    I18n.get("egress_today_section"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                _build_egress_table(egress_state.today_rows),
                ft.Divider(height=20, color=AppColors.BORDER),
                ft.Text(
                    I18n.get("egress_month_section"),
                    size=AppStyles.FONT_SIZE_BODY_SM,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                _build_egress_table(egress_state.month_rows),
            ],
            spacing=8,
        ),
    )

    # 状态指示器 (从 VM state 派生)
    status_color = AppColors.SUCCESS if is_success else AppColors.ERROR if is_error else AppColors.TEXT_HINT
    status_text = I18n.get("common_saved") if is_success else I18n.get("sys_snack_save_err") if is_error else ""
    status_row = (
        ft.Row(
            [
                ft.Icon(
                    ft.Icons.INFO_OUTLINE,
                    size=AppStyles.FONT_SIZE_TITLE,
                    color=status_color,
                ),
                ft.Text(status_text, size=AppStyles.FONT_SIZE_CAPTION, color=status_color),
            ],
            spacing=5,
        )
        if status_text
        else ft.Container()
    )

    return ft.Container(
        content=ft.Column(
            controls=[
                model_management_panel,
                card_data_boundary,
                card_tuning,
                card_prompt,
                card_local_only,
                card_egress,
                ft.Container(
                    content=ft.Row(
                        [btn_save_ai, save_progress, status_row],
                        alignment=ft.MainAxisAlignment.END,
                        spacing=10,
                    ),
                    padding=ft.Padding.only(top=10, bottom=30, right=20),
                ),
                # P1-4 批次 2: 追加 2 个 ConfirmDialog (width=0/height=0 不影响布局,
                # use_dialog 挂载到 page overlay)
                ConfirmDialog(
                    open_state=reset_ai_dialog_open,
                    title=I18n.get("confirm_reset_title"),
                    body=I18n.get("confirm_reset_body"),
                    on_confirm=_do_confirm_reset_ai_prompt,
                    on_cancel=_do_cancel_reset_ai_prompt,
                    confirm_text=I18n.get("common_confirm"),
                    cancel_text=I18n.get("common_cancel"),
                    # 重置 AI 提示词会覆盖用户自定义内容 → 危险色 (MINOR-05)
                    destructive=True,
                ),
                ConfirmDialog(
                    open_state=reset_news_dialog_open,
                    title=I18n.get("confirm_reset_title"),
                    body=I18n.get("confirm_reset_body"),
                    on_confirm=_do_confirm_reset_news_prompt,
                    on_cancel=_do_cancel_reset_news_prompt,
                    confirm_text=I18n.get("common_confirm"),
                    cancel_text=I18n.get("common_cancel"),
                    # 重置新闻提示词会覆盖用户自定义内容 → 危险色 (MINOR-05)
                    destructive=True,
                ),
            ],
            spacing=15,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
    )
