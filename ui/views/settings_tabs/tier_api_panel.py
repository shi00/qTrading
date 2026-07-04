"""TierApiPanel — Phase 2A.1 §3.2.10 高级配置面板。

展示各档位 API 列表 + probe 三态状态 + 独立付费标记 + 触发探测按钮 +
上次 probe 时间 + stale 提示区。MVVM：UI 交互和状态展示由 Panel 负责，
probe 执行逻辑由 ``SystemViewModel.run_probe()`` / ``on_tier_changed()`` 承担。

响应式布局（§5.9 规范）:
- 宽度 ≥ 1200px (lg): 3 列展示（API 名称 | probe 状态 | 说明）
- 宽度 800-1199px (md): 2 列展示
- 宽度 < 800px (sm): 1 列垂直堆叠

i18n 9 条规范:
- 订阅 locale 变更（``I18n.subscribe``），``_on_locale_change`` 重建 options + API 列表文本
- ``_on_locale_change`` 包裹 try/except 异常降级
- ``dispose`` 取消订阅（生命周期兜底）
- 实例属性 ``_current_tier`` 提取（避免 ``_build_ui`` 依赖 ConfigHandler 全局状态）
"""

import logging
from datetime import datetime

import flet as ft

from core.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.system_viewmodel import SystemViewModel

logger = logging.getLogger(__name__)


# 响应式断点阈值（与设计文档 §3.2.10 一致；与 ui/theme.py AppStyles 配合使用）
_TIER_PANEL_LG_BREAKPOINT = 1200
_TIER_PANEL_MD_BREAKPOINT = 800


class TierApiPanel(ft.Column):
    """高级配置面板——展示各档位 API 列表 + probe 状态 + 独立付费标记 + 触发探测按钮。

    通过构造注入 ``SystemViewModel``（v1.6.0 P1-10：单一实例由 system_tab 创建并注入，
    Panel 不自行实例化 VM）。VM 通过 ``on_probe_completed`` 单回调字段通知 Panel
    probe 结果（同 ``DataSourceViewModel.on_show_snack`` 模式）。
    """

    def __init__(self, viewmodel: SystemViewModel):  # pragma: no cover - UI 渲染入口
        super().__init__()  # pragma: no cover
        # 实例属性提取（i18n 规范 5：避免 _build_ui 依赖全局状态）
        self._viewmodel = viewmodel
        self._current_tier: str = viewmodel.get_current_tier()
        self._last_probe_time: datetime | None = None
        self._probe_status: dict[str, bool | None] = {}
        # 档位降级后被排除的 API（用于 stale 提示）
        self._stale_apis: list[str] = []
        self._probe_in_progress = False
        self._locale_sub_id: object | None = None
        self._build_ui()
        # 订阅 locale 变更（subscribe 返回 callback 本身；__init__ 已构建 UI，
        # 用 sync_immediately=False 避免重复重建）
        self._locale_sub_id = I18n.subscribe(self._on_locale_change, sync_immediately=False)

    # ------------------------------------------------------------------
    # UI 构建
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:  # pragma: no cover - UI 渲染
        """构建 UI: 档位选择 + 提示文案 + API 列表 + probe 状态 + 触发按钮 + stale 提示。"""
        # 1. 档位下拉框（与 system_tab 5 档保持一致；on_change 触发档位变更全链路）
        self.tier_dropdown = ft.Dropdown(
            label=I18n.get("sys_label_point_tier"),
            value=self._current_tier,
            width=AppStyles.CONTROL_WIDTH_MD,
            text_size=14,
            border_radius=8,
            content_padding=10,
            options=self._build_tier_options(),
            on_change=self._on_tier_dropdown_change,
        )

        # 2. 提示文案
        self.points_hint_text = ft.Text(
            I18n.get("sys_tier_points_hint"),
            size=11,
            color=ft.Colors.ON_SURFACE_VARIANT,
            italic=True,
        )

        # 3. 触发探测按钮
        self.probe_button = ft.ElevatedButton(
            text=I18n.get("sys_tier_probe_button"),
            icon=ft.Icons.SYNC_ROUNDED,
            on_click=self.on_probe_button_clicked,
            style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=8)),
        )

        # 4. 上次 probe 时间 + 进度文本（初始为占位）
        self.last_probe_text = ft.Text(
            self._format_last_probe_text(),
            size=11,
            color=ft.Colors.ON_SURFACE_VARIANT,
        )
        self.progress_text = ft.Text("", size=11, color=ft.Colors.PRIMARY)

        # 5. API 列表 + probe 状态（用 ListView 虚拟滚动，v1.9.0 P2-3）
        self.api_list_view = ft.ListView(
            controls=self._build_api_list_controls(),
            spacing=4,
            padding=8,
            height=300,
            auto_scroll=False,
        )

        # 6. API 列表表头
        self.api_list_header = ft.Text(
            I18n.get("sys_tier_api_list_header"),
            size=13,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )
        self.probe_status_header = ft.Text(
            I18n.get("sys_tier_probe_status_header"),
            size=13,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )

        # 7. stale 提示区（_stale_apis 非空才显示）
        self.stale_hint_text = ft.Text(
            self._format_stale_hint_text(),
            size=11,
            color=AppColors.WARNING,
            visible=bool(self._stale_apis),
        )

        # 8. 面板标题
        self.panel_title = ft.Text(
            I18n.get("sys_tier_panel_title"),
            size=16,
            weight=ft.FontWeight.BOLD,
            color=ft.Colors.ON_SURFACE,
        )

        # 组装（ResponsiveRow 用于响应式列数切换）
        self.controls = [
            self.panel_title,
            ft.Container(height=4),
            ft.Row(
                [self.tier_dropdown, self.probe_button],
                spacing=10,
                wrap=True,
            ),
            self.points_hint_text,
            ft.Container(height=8),
            ft.Row(
                [self.last_probe_text, self.progress_text],
                spacing=10,
                wrap=True,
            ),
            ft.Container(height=8),
            ft.Row(
                [self.api_list_header, self.probe_status_header],
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            ),
            self.api_list_view,
            self.stale_hint_text,
        ]

    def _build_tier_options(self) -> list[ft.dropdown.Option]:
        """构建档位下拉框选项（locale 变更时重建）。"""
        return [
            ft.dropdown.Option("points_120", I18n.get("sys_tier_points_120_label")),
            ft.dropdown.Option("points_2000", I18n.get("sys_tier_points_2000_label")),
            ft.dropdown.Option("points_5000", I18n.get("sys_tier_points_5000_label")),
            ft.dropdown.Option("points_10000", I18n.get("sys_tier_points_10000_label")),
            ft.dropdown.Option("points_15000", I18n.get("sys_tier_points_15000_label")),
        ]

    def _build_api_list_controls(self) -> list[ft.Control]:
        """构建 API 列表控件（按当前档位过滤 _TIER_API_COVERAGE）。

        每项含：API 名称 + 独立付费标记（💰）+ probe 三态状态图标 + 状态文本。
        """
        from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

        client = TushareClient()
        # 当前档位覆盖的 API 集合（含低档位累积）
        tier_apis = client.get_tier_apis(self._current_tier)
        # 排序：按字母序便于查找
        sorted_apis = sorted(tier_apis)

        controls: list[ft.Control] = []
        for api_name in sorted_apis:
            available = self._probe_status.get(api_name)
            status_icon, status_text, status_color = self._render_probe_status(api_name, available)

            # 独立付费标记（💰 图标 + tooltip）
            independent_badge: list[ft.Control] = []
            if client.is_independent_purchase(api_name):
                independent_badge.append(
                    ft.Icon(
                        ft.Icons.ATTACH_MONEY_ROUNDED,
                        size=12,
                        color=AppColors.WARNING,
                        tooltip=I18n.get("sys_tier_independent_purchase"),
                    )
                )

            # 每行使用 ResponsiveRow：API 名称（col 6）+ 状态（col 6），
            # 窄屏自动堆叠
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
                        content=self._build_api_description(api_name, available),
                        col={"xs": 12, "sm": 12, "md": 12, "lg": 4},
                    ),
                ],
                spacing=4,
            )
            controls.append(row)
        return controls

    def _render_probe_status(self, api_name: str, available: bool | None) -> tuple[ft.Icon, ft.Text, str]:
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
            # 区分"积分不足"vs"需独立购买"
            from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

            client = TushareClient()
            if client.is_independent_purchase(api_name):
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
        # None：未探测
        return (
            ft.Icon(ft.Icons.HELP_OUTLINE, size=14, color=ft.Colors.ON_SURFACE_VARIANT),
            ft.Text(I18n.get("sys_tier_not_probed"), size=11, color=ft.Colors.ON_SURFACE_VARIANT),
            str(ft.Colors.ON_SURFACE_VARIANT),
        )

    def _build_api_description(self, api_name: str, available: bool | None) -> ft.Control:
        """构建 API 说明列（独立付费/积分不足/未探测说明）。"""
        from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

        client = TushareClient()
        if client.is_independent_purchase(api_name):
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

    def _format_last_probe_text(self) -> str:
        """格式化上次 probe 时间文本。"""
        if self._last_probe_time is None:
            return I18n.get("sys_tier_last_probe_time") + ": -"
        return I18n.get("sys_tier_last_probe_time") + ": " + self._last_probe_time.strftime("%Y-%m-%d %H:%M")

    def _format_stale_hint_text(self) -> str:
        """格式化 stale API 提示文本。"""
        if not self._stale_apis:
            return ""
        apis_str = ", ".join(self._stale_apis)
        return I18n.get("sys_tier_stale_apis_hint") + ": " + apis_str

    # ------------------------------------------------------------------
    # 响应式布局
    # ------------------------------------------------------------------

    def handle_resize(self, width: float, height: float) -> None:  # pragma: no cover - UI 事件
        """响应式布局：按断点调整 API 列表高度与列数。

        父组件 ``system_tab`` 在 ``handle_resize`` 中级联调用本方法（触发时机完整性）。
        断点（§3.2.10）:
        - lg ≥ 1200px: 3 列布局，列表高度 300
        - md 800-1199px: 2 列布局，列表高度 280
        - sm < 800px: 1 列堆叠，列表高度 240
        """
        try:
            if width >= _TIER_PANEL_LG_BREAKPOINT:
                self.api_list_view.height = 300
            elif width >= _TIER_PANEL_MD_BREAKPOINT:
                self.api_list_view.height = 280
            else:
                self.api_list_view.height = 240
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug("[TierApiPanel] handle_resize skipped: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # i18n 响应
    # ------------------------------------------------------------------

    def _on_locale_change(self) -> None:
        """i18n 响应：重建档位下拉框 options + API 列表文本（纯 UI 操作）。

        异常降级（§5.8 规范 9）：locale 变更失败不影响主流程。
        """
        try:
            # 重建档位下拉框 options（保留当前选中值）
            from ui.i18n import refresh_dropdown_options

            refresh_dropdown_options(self.tier_dropdown, self._build_tier_options())
            self.tier_dropdown.label = I18n.get("sys_label_point_tier")
            self.points_hint_text.value = I18n.get("sys_tier_points_hint")
            self.probe_button.text = I18n.get("sys_tier_probe_button")
            self.last_probe_text.value = self._format_last_probe_text()
            self.stale_hint_text.value = self._format_stale_hint_text()
            self.panel_title.value = I18n.get("sys_tier_panel_title")
            self.api_list_header.value = I18n.get("sys_tier_api_list_header")
            self.probe_status_header.value = I18n.get("sys_tier_probe_status_header")
            # 重建 API 列表（状态文本 + tooltip 需要 locale 刷新）
            self.api_list_view.controls = self._build_api_list_controls()
            if self.page:
                self.update()
        except Exception as exc:
            logger.warning("[TierApiPanel] _on_locale_change failed: %s", exc, exc_info=True)

    def dispose(self) -> None:
        """生命周期兜底，取消 locale 变更订阅。

        v1.9.0 P1-5：裸 ``except: pass`` 违反 §5.7，改 logger.warning；
        重复 dispose 容错：subscription_id 已失效时 unsubscribe 可能抛异常。
        """
        try:
            if self._locale_sub_id is not None:
                I18n.unsubscribe(self._locale_sub_id)
                self._locale_sub_id = None
        except Exception as exc:
            logger.warning("[TierApiPanel] dispose: I18n.unsubscribe failed: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def did_mount(self) -> None:  # pragma: no cover - UI 生命周期
        """挂载时注册 ViewModel 的 on_probe_completed 回调（单回调字段模式）。

        v1.9.0 M-5 修订：自动 probe（bootstrap 启动）可能在 TierApiPanel 挂载之前完成，
        此时 on_probe_completed 为 None，_emit_probe_result 静默丢失（已改 logger.warning）。
        did_mount 主动调用 ``client.get_capability_cache()`` 拉取最新缓存刷新 API 列表，
        覆盖自动 probe 早完成的情况。
        """
        try:
            self._viewmodel.on_probe_completed = self._on_probe_result
            # M-5：主动拉取最新缓存，覆盖自动 probe 在 Panel 挂载前完成的情况
            from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

            client = TushareClient()
            cache = client.get_capability_cache()
            if cache:
                self._refresh_api_list(cache)
            # 同步 last_probe_time（自动 probe 可能已更新）
            self._last_probe_time = client._last_probe_time  # noqa: SLF001  # 访问私有属性以同步自动 probe 时间
            self.last_probe_text.value = self._format_last_probe_text()
            if self.page:
                self.update()
        except Exception as exc:
            logger.warning("[TierApiPanel] did_mount refresh failed: %s", exc, exc_info=True)

    def will_unmount(self) -> None:  # pragma: no cover - UI 生命周期
        """卸载时清空回调，避免悬挂引用/内存泄漏。"""
        if self._viewmodel.on_probe_completed is self._on_probe_result:
            self._viewmodel.on_probe_completed = None
        self.dispose()

    # ------------------------------------------------------------------
    # 档位下拉框回调
    # ------------------------------------------------------------------

    def _on_tier_dropdown_change(self, e) -> None:  # pragma: no cover - UI 事件
        """档位下拉框变更：启动 on_tier_changed 全链路任务。

        由 ``system_tab`` 注入的 progress_callback 用于实时推送进度到 Panel。
        使用 ``page.run_task`` 避免阻塞 UI 主循环（R16）。
        """
        new_tier = self.tier_dropdown.value
        if not new_tier or new_tier == self._current_tier:
            return
        if self.page:
            self.page.run_task(self._run_tier_change, new_tier)

    async def _run_tier_change(self, new_tier: str) -> None:  # pragma: no cover - UI 事件
        """执行档位变更全链路（异步）。

        通过 ``SystemViewModel.on_tier_changed`` 完成 set_tier → reload_limiters →
        clear_cache → probe → _emit_probe_result 链路。结果通过 on_probe_completed
        回调刷新 Panel。
        """
        self._set_probe_in_progress()
        try:
            await self._viewmodel.on_tier_changed(new_tier, progress_callback=self._on_probe_progress)
        except Exception as exc:
            logger.error("[TierApiPanel] on_tier_changed failed: %s", exc, exc_info=True)
            self._notify_probe_failed(str(exc))

    # ------------------------------------------------------------------
    # probe 按钮回调
    # ------------------------------------------------------------------

    async def on_probe_button_clicked(self, e) -> None:  # pragma: no cover - UI 事件
        """触发探测按钮回调（直接 await ViewModel.run_probe）。

        R16：``run_probe`` 内部 IO 已通过 ``_handle_probe_call`` 投递到 io_pool，
        无需 ThreadPoolManager.run_async 二次包装。
        """
        self._set_probe_in_progress()
        try:
            await self._viewmodel.run_probe(progress_callback=self._on_probe_progress)
        except Exception as exc:
            logger.error("[TierApiPanel] run_probe failed: %s", exc, exc_info=True)
            self._notify_probe_failed(str(exc))

    # ------------------------------------------------------------------
    # View 回调方法（由 ViewModel 通过 on_probe_completed 触发）
    # ------------------------------------------------------------------

    def _on_probe_result(self, result: dict) -> None:
        """on_probe_completed 回调：根据 result.type 分派到对应 _notify_* 方法。

        result.type 由 SystemViewModel._emit_probe_result 定义:
        - "completed" → _notify_probe_completed(tier, available, unavailable, unknown)
        - "tier_too_high" → _notify_tier_too_high(tier, false_count, total)
        - "all_failed" → _notify_probe_all_failed(tier)
        - "set_tier_failed" → _notify_probe_failed(message)
        """
        try:
            rtype = result.get("type")
            if rtype == "completed":
                self._notify_probe_completed(
                    result.get("tier", ""),
                    result.get("available", 0),
                    result.get("unavailable", 0),
                    result.get("unknown", 0),
                )
            elif rtype == "tier_too_high":
                self._notify_tier_too_high(
                    result.get("tier", ""),
                    result.get("false_count", 0),
                    result.get("total", 0),
                )
            elif rtype == "all_failed":
                self._notify_probe_all_failed(result.get("tier", ""))
            elif rtype == "set_tier_failed":
                self._notify_probe_failed(result.get("message", "档位保存失败"))
            else:
                logger.warning("[TierApiPanel] Unknown probe result type: %s", rtype)
        except Exception as exc:
            logger.warning("[TierApiPanel] _on_probe_result dispatch failed: %s", exc, exc_info=True)

    def _set_probe_in_progress(self) -> None:  # pragma: no cover - UI 状态
        """禁用触发探测按钮和档位下拉框（避免 probe 期间切换档位导致时序混乱）+ 显示探测中状态。"""
        self._probe_in_progress = True
        self.probe_button.disabled = True
        self.tier_dropdown.disabled = True
        self.progress_text.value = I18n.get("sys_tier_probe_in_progress")
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] update skipped: %s", exc, exc_info=True)

    def _on_probe_progress(self, completed: int, total: int) -> None:  # pragma: no cover - UI 进度
        """probe 进度回调：更新进度文本（v1.10.0 P2-4 进度文本动态化）。"""
        self.progress_text.value = I18n.get("sys_tier_probe_in_progress_with_count", completed=completed, total=total)
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] progress update skipped: %s", exc, exc_info=True)

    def _notify_probe_completed(
        self, tier: str, available: int, unavailable: int, unknown: int
    ) -> None:  # pragma: no cover - UI 状态
        """probe 完成：恢复按钮 + 显示完成提示 + 刷新 API 列表。"""
        self._probe_in_progress = False
        self.probe_button.disabled = False
        self.tier_dropdown.disabled = False
        self._current_tier = tier
        self.tier_dropdown.value = tier
        self.progress_text.value = I18n.get(
            "sys_tier_probe_completed", available=available, unavailable=unavailable, unknown=unknown
        )
        # 刷新 API 列表（用最新 probe 结果）
        from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

        self._probe_status = TushareClient().get_capability_cache()
        self._last_probe_time = TushareClient()._last_probe_time  # noqa: SLF001  # 同步最新 probe 时间
        self.last_probe_text.value = self._format_last_probe_text()
        self.api_list_view.controls = self._build_api_list_controls()
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] update skipped: %s", exc, exc_info=True)

    def _notify_tier_too_high(self, tier: str, false_count: int, total: int) -> None:  # pragma: no cover - UI 状态
        """档位声明过高：恢复按钮 + 显示警告提示 + 刷新 API 列表。"""
        self._probe_in_progress = False
        self.probe_button.disabled = False
        self.tier_dropdown.disabled = False
        self._current_tier = tier
        self.tier_dropdown.value = tier
        self.progress_text.value = I18n.get("sys_tier_tier_too_high", false_count=false_count, total=total)
        from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

        self._probe_status = TushareClient().get_capability_cache()
        self._last_probe_time = TushareClient()._last_probe_time  # noqa: SLF001
        self.last_probe_text.value = self._format_last_probe_text()
        self.api_list_view.controls = self._build_api_list_controls()
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] update skipped: %s", exc, exc_info=True)

    def _notify_probe_all_failed(self, tier: str) -> None:  # pragma: no cover - UI 状态
        """probe 全部失败：恢复按钮 + 显示失败提示 + 刷新 API 列表。"""
        self._probe_in_progress = False
        self.probe_button.disabled = False
        self.tier_dropdown.disabled = False
        self._current_tier = tier
        self.tier_dropdown.value = tier
        self.progress_text.value = I18n.get("sys_tier_probe_all_failed")
        from data.external.tushare_client import TushareClient  # lazy import to avoid circular dependency

        self._probe_status = TushareClient().get_capability_cache()
        self._last_probe_time = TushareClient()._last_probe_time  # noqa: SLF001
        self.last_probe_text.value = self._format_last_probe_text()
        self.api_list_view.controls = self._build_api_list_controls()
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] update skipped: %s", exc, exc_info=True)

    def _notify_probe_failed(self, message: str) -> None:  # pragma: no cover - UI 状态
        """probe 失败（异常路径）：恢复按钮 + 显示失败消息。"""
        self._probe_in_progress = False
        self.probe_button.disabled = False
        self.tier_dropdown.disabled = False
        # 档位保存失败时回滚下拉框到当前实际档位
        self._current_tier = self._viewmodel.get_current_tier()
        self.tier_dropdown.value = self._current_tier
        self.progress_text.value = I18n.get("sys_tier_probe_failed") + " (" + message + ")"
        if self.page:
            try:
                self.update()
            except Exception as exc:
                logger.debug("[TierApiPanel] update skipped: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # 缓存刷新
    # ------------------------------------------------------------------

    def _refresh_api_list(self, cache: dict[str, bool | None]) -> None:
        """用最新 capability_cache 刷新 API 列表显示。

        did_mount 时主动调用，覆盖自动 probe 在 Panel 挂载前完成的情况（M-5）。
        """
        try:
            self._probe_status = cache
            self.api_list_view.controls = self._build_api_list_controls()
            if self.page:
                self.update()
        except Exception as exc:
            logger.warning("[TierApiPanel] _refresh_api_list failed: %s", exc, exc_info=True)
