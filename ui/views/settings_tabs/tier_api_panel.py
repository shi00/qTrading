"""TierApiPanel — 声明式组件 (Phase D.2).

从命令式容器子类重写为 @ft.component + use_viewmodel 范式
(CLAUDE.md §3.2 MVVM, §3.3 use_viewmodel hook 已实现).

变更要点:
- 旧命令式 class → ``@ft.component def TierApiPanel(system_vm)``
- VM 由消费方实例化（system_tab 创建 SystemViewModel 并注入）
- View 通过 ``use_viewmodel(vm=system_vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
- i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
- probe 三态（idle/running/result）由 VM state.probe_result 驱动渲染 (L771 合规, 无 dual-track)
- 响应式断点用 ``use_state`` + ``page.on_resize``（use_effect 挂载，链式保留 prev）
- 移除命令式生命周期回调、手动 update、手动 locale 刷新等命令式模式
- 实例方法 → 模块级纯函数；stale_hint_text 移除（原 _stale_apis 从未赋值，死代码，YAGNI）
"""

import asyncio
import logging
from datetime import datetime

import flet as ft

from ui.hooks import use_viewmodel
from ui.i18n import I18n, get_observable_state
from ui.theme import AppColors, AppStyles
from ui.viewmodels.system_viewmodel import ProbeResultRow, SystemViewModel

logger = logging.getLogger(__name__)

# 响应式断点阈值（与设计文档 §3.2.10 一致；与 ui/theme.py AppStyles 配合使用）
_TIER_PANEL_LG_BREAKPOINT = 1200
_TIER_PANEL_MD_BREAKPOINT = 800


# ------------------------------------------------------------------
# 模块级纯函数（无 self 依赖，便于单测）
# ------------------------------------------------------------------


def _build_tier_options(vm: SystemViewModel) -> list[ft.dropdown.Option]:
    """构建档位下拉框选项（locale 变更时由组件重渲染自动刷新）。"""
    return [ft.dropdown.Option(key=tier, text=I18n.get(f"sys_tier_{tier}_label")) for tier in vm.get_tier_options()]


def _render_probe_status(api_name: str, available: bool | None, vm: SystemViewModel) -> tuple[ft.Icon, ft.Text, str]:
    """渲染 probe 三态状态图标 + 文本。

    Returns:
        (status_icon, status_text, color) 三元组
    """
    if available is True:
        return (
            ft.Icon(ft.Icons.CHECK_CIRCLE, size=14, color=AppColors.SUCCESS),
            ft.Text(I18n.get("sys_tier_available"), size=11, color=AppColors.SUCCESS),
            AppColors.SUCCESS,
        )
    if available is False:
        if vm.is_independent_purchase(api_name):
            return (
                ft.Icon(ft.Icons.CANCEL, size=14, color=AppColors.WARNING),
                ft.Text(I18n.get("sys_tier_independent_purchase"), size=11, color=AppColors.WARNING),
                AppColors.WARNING,
            )
        return (
            ft.Icon(ft.Icons.CANCEL, size=14, color=AppColors.ERROR),
            ft.Text(I18n.get("sys_tier_unavailable"), size=11, color=AppColors.ERROR),
            AppColors.ERROR,
        )
    return (
        ft.Icon(ft.Icons.HELP_OUTLINE, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
        ft.Text(I18n.get("sys_tier_not_probed"), size=11, color=ft.Colors.ON_SURFACE_VARIANT),
        str(ft.Colors.ON_SURFACE_VARIANT),
    )


def _build_api_description(api_name: str, available: bool | None, vm: SystemViewModel) -> ft.Control:
    """构建 API 说明列（独立付费/积分不足/默认空）。"""
    if vm.is_independent_purchase(api_name):
        return ft.Text(
            I18n.get("sys_tier_independent_purchase"),
            size=10,
            color=AppColors.WARNING,
            italic=True,
        )
    if available is False:
        return ft.Text(
            I18n.get("sys_tier_insufficient_points"),
            size=10,
            color=AppColors.ERROR,
            italic=True,
        )
    return ft.Text("", size=10)


def _build_api_list_controls(
    current_tier: str, probe_status: dict[str, bool | None], vm: SystemViewModel
) -> list[ft.Control]:
    """构建 API 列表控件（按当前档位过滤 _TIER_API_COVERAGE）。

    每项含：API 名称 + 独立付费标记（💰）+ probe 三态状态图标 + 状态文本。
    """
    tier_apis = vm.get_tier_apis(current_tier)
    sorted_apis = sorted(tier_apis)

    controls: list[ft.Control] = []
    for api_name in sorted_apis:
        available = probe_status.get(api_name)
        status_icon, status_text, _ = _render_probe_status(api_name, available, vm)

        independent_badge: list[ft.Control] = []
        if vm.is_independent_purchase(api_name):
            independent_badge.append(
                ft.Icon(
                    ft.Icons.ATTACH_MONEY_ROUNDED,
                    size=12,
                    color=AppColors.WARNING,
                    tooltip=I18n.get("sys_tier_independent_purchase"),
                )
            )

        row = ft.ResponsiveRow(
            [
                ft.Container(
                    content=ft.Row(
                        [ft.Text(api_name, size=12, color=ft.Colors.ON_SURFACE), *independent_badge],
                        spacing=4,
                    ),
                    col={"xs": 12, "sm": 6, "md": 6, "lg": 4},
                ),
                ft.Container(
                    content=ft.Row(
                        [status_icon, status_text],
                        spacing=4,
                    ),
                    col={"xs": 12, "sm": 6, "md": 6, "lg": 4},
                ),
                ft.Container(
                    content=_build_api_description(api_name, available, vm),
                    col={"xs": 12, "sm": 12, "md": 12, "lg": 4},
                ),
            ],
            spacing=4,
        )
        controls.append(row)
    return controls


def _format_last_probe_text(last_probe_time: datetime | None) -> str:
    """格式化上次 probe 时间文本。"""
    if last_probe_time is None:
        return I18n.get("sys_tier_last_probe_time") + ": -"
    return I18n.get("sys_tier_last_probe_time") + ": " + last_probe_time.strftime("%Y-%m-%d %H:%M")


def _compute_progress_text(
    probe_in_progress: bool,
    progress: tuple[int, int],
    result: ProbeResultRow | None,
) -> str:
    """根据 probe 三态计算进度文本。

    - running 且有进度（total>0）：sys_tier_probe_in_progress_with_count
    - running 无进度：sys_tier_probe_in_progress
    - result.type=completed：sys_tier_probe_completed
    - result.type=tier_too_high：sys_tier_tier_too_high
    - result.type=all_failed：sys_tier_probe_all_failed
    - result.type=set_tier_failed：sys_tier_probe_failed + (message)
    - 其他（idle 无 result / 未知 type）：空
    """
    if probe_in_progress:
        if progress[1] > 0:
            return I18n.get(
                "sys_tier_probe_in_progress_with_count",
                completed=progress[0],
                total=progress[1],
            )
        return I18n.get("sys_tier_probe_in_progress")
    if result is not None:
        rtype = result.type
        if rtype == "completed":
            return I18n.get(
                "sys_tier_probe_completed",
                available=result.available,
                unavailable=result.unavailable,
                unknown=result.unknown,
            )
        if rtype == "tier_too_high":
            return I18n.get(
                "sys_tier_tier_too_high",
                false_count=result.false_count,
                total=result.total,
            )
        if rtype == "all_failed":
            return I18n.get("sys_tier_probe_all_failed")
        if rtype == "set_tier_failed":
            return I18n.get("sys_tier_probe_failed") + " (" + result.message + ")"
    return ""


def _compute_list_height(width: float) -> int:
    """按响应式断点计算 API 列表高度。

    - width=0（初始未知）或 ≥ lg(1200)：300
    - md(800-1199)：280
    - sm(<800)：240
    """
    if width == 0 or width >= _TIER_PANEL_LG_BREAKPOINT:
        return 300
    if width >= _TIER_PANEL_MD_BREAKPOINT:
        return 280
    return 240


# ------------------------------------------------------------------
# 声明式组件
# ------------------------------------------------------------------


@ft.component
def TierApiPanel(system_vm: SystemViewModel) -> ft.Column:
    """高级配置面板——展示各档位 API 列表 + probe 状态 + 独立付费标记 + 触发探测按钮。

    CLAUDE.md §3.2 MVVM + §3.3 use_viewmodel hook:
    - VM 由消费方实例化（system_tab 创建 SystemViewModel 并注入）
    - View 通过 ``use_viewmodel(vm=system_vm)`` hook 订阅 ``vm.state`` 变化触发重渲染（外部 VM 模式）
    - i18n 通过 ``ft.use_state(get_observable_state)`` 自动重渲染
    - probe 三态（idle/running/result）由 VM state.probe_result 驱动渲染 (L771 合规, 无 dual-track)
    - 响应式断点用 ``use_state`` + ``page.on_resize``（use_effect 挂载，链式保留 prev）
    - 无 page ref / 生命周期回调 / 手动刷新

    Args:
        system_vm: 由消费方实例化的 SystemViewModel（生命周期由消费方管理）
    """
    # --- 订阅 VM state（外部 VM 模式，VM 生命周期由消费方管理）---
    state, vm = use_viewmodel(vm=system_vm)

    # --- 订阅 i18n 变更（自动重渲染）---
    ft.use_state(get_observable_state)

    # --- 局部状态 ---
    current_tier = vm.get_current_tier()
    # 下拉框乐观值：用户选择后立即更新，probe 完成后同步 VM 当前档位
    selected_tier, set_selected_tier = ft.use_state(current_tier)
    # probe 进度（progress_callback 驱动，running 态动态文本）
    progress, set_progress = ft.use_state((0, 0))
    # 响应式宽度（page.on_resize 驱动）
    width, set_width = ft.use_state(0.0)
    # prev on_resize 引用（链式保留，cleanup 恢复）
    _prev_resize_ref = ft.use_ref(lambda: None)

    probe_in_progress = state.probe_in_progress

    # --- probe 完成后同步 selected_tier 到 VM 当前档位（处理 set_tier_failed 回滚）---
    def _sync_selected() -> None:
        if not probe_in_progress:
            set_selected_tier(current_tier)

    ft.use_effect(_sync_selected, dependencies=[probe_in_progress])

    # --- 响应式 on_resize 挂载（保存 prev 并链式调用，cleanup 恢复）---
    def _setup_resize() -> None:
        try:
            page = ft.context.page
        except RuntimeError:
            return
        if page is None:
            return
        _prev_resize_ref.current = page.on_resize

        def _on_resize(e) -> None:
            if _prev_resize_ref.current:
                _prev_resize_ref.current(e)
            try:
                w = getattr(e, "width", 0.0)
                if w:
                    set_width(float(w))
            except Exception:
                logger.debug("[TierApiPanel] on_resize width parse skipped", exc_info=True)

        page.on_resize = _on_resize

    def _cleanup_resize() -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.on_resize = _prev_resize_ref.current
        except RuntimeError:
            pass

    ft.use_effect(_setup_resize, dependencies=[], cleanup=_cleanup_resize)

    # --- 从 state 读取业务数据 (L771 合规, 无 dual-track property 拉取) ---
    probe_status = vm.get_capability_cache()
    result = state.probe_result
    last_probe_time = vm.get_last_probe_time()

    # --- 计算 UI 状态 ---
    progress_text = _compute_progress_text(probe_in_progress, progress, result)
    last_probe_text = _format_last_probe_text(last_probe_time)
    list_height = _compute_list_height(width)

    # --- handlers ---
    def _on_progress(completed: int, total: int) -> None:
        set_progress((completed, total))

    async def _run_tier_change(new_tier: str) -> None:
        set_progress((0, 0))
        try:
            await vm.on_tier_changed(new_tier, progress_callback=_on_progress)
        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except Exception as exc:
            logger.error("[TierApiPanel] on_tier_changed failed: %s", exc, exc_info=True)

    def _on_tier_change(e: ft.ControlEvent) -> None:
        new_tier = e.control.value if e and e.control else None
        if not new_tier or new_tier == current_tier:
            return
        set_selected_tier(new_tier)
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_run_tier_change, new_tier)
        except RuntimeError:
            logger.debug("[TierApiPanel] page not available for on_tier_changed")

    async def _run_probe() -> None:
        set_progress((0, 0))
        try:
            await vm.run_probe(progress_callback=_on_progress)
        except asyncio.CancelledError:  # R2: CancelledError 必须传播, 不被 except Exception 吞没
            raise
        except Exception as exc:
            logger.error("[TierApiPanel] run_probe failed: %s", exc, exc_info=True)

    def _on_probe_click(e: ft.ControlEvent) -> None:
        try:
            page = ft.context.page
            if page is not None:
                page.run_task(_run_probe)
        except RuntimeError:
            logger.debug("[TierApiPanel] page not available for run_probe")

    # --- 构建控件（状态驱动）---
    tier_dropdown = ft.Dropdown(
        label=I18n.get("sys_label_point_tier"),
        value=selected_tier,
        width=AppStyles.CONTROL_WIDTH_MD,
        text_size=14,
        border_radius=8,
        content_padding=10,
        options=_build_tier_options(vm),
        on_select=_on_tier_change,
        disabled=probe_in_progress,
    )

    points_hint_text = ft.Text(
        I18n.get("sys_tier_points_hint"),
        size=11,
        color=ft.Colors.ON_SURFACE_VARIANT,
        italic=True,
    )

    probe_button = ft.Button(
        content=I18n.get("sys_tier_probe_button"),
        icon=ft.Icons.SYNC_ROUNDED,
        on_click=_on_probe_click,
        style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        disabled=probe_in_progress,
    )

    last_probe_text_ctrl = ft.Text(
        last_probe_text,
        size=11,
        color=ft.Colors.ON_SURFACE_VARIANT,
    )
    progress_text_ctrl = ft.Text(progress_text, size=11, color=ft.Colors.PRIMARY)

    api_list_view = ft.ListView(
        controls=_build_api_list_controls(current_tier, probe_status, vm),
        spacing=4,
        padding=8,
        height=list_height,
        auto_scroll=False,
    )

    api_list_header = ft.Text(
        I18n.get("sys_tier_api_list_header"),
        size=13,
        weight=ft.FontWeight.BOLD,
        color=ft.Colors.ON_SURFACE,
    )
    probe_status_header = ft.Text(
        I18n.get("sys_tier_probe_status_header"),
        size=13,
        weight=ft.FontWeight.BOLD,
        color=ft.Colors.ON_SURFACE,
    )

    panel_title = ft.Text(
        I18n.get("sys_tier_panel_title"),
        size=16,
        weight=ft.FontWeight.BOLD,
        color=ft.Colors.ON_SURFACE,
    )

    return ft.Column(
        [
            panel_title,
            ft.Container(height=4),
            ft.Row(
                [tier_dropdown, probe_button],
                spacing=10,
                wrap=True,
            ),
            points_hint_text,
            ft.Container(height=8),
            ft.Row(
                [last_probe_text_ctrl, progress_text_ctrl],
                spacing=10,
                wrap=True,
            ),
            ft.Container(height=8),
            ft.Row(
                [api_list_header, probe_status_header],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            api_list_view,
        ]
    )
