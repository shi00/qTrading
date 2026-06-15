import logging
import weakref

import flet as ft

from ui.components.settings_widgets import DashboardCard, SettingRow
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

# ============================================================================
# UI Constants
# ============================================================================
_FONT_SIZE_TITLE = 16
_FONT_SIZE_BODY = 14
_FONT_SIZE_SMALL = 12
_FONT_SIZE_HINT = 11
_ICON_SIZE_SMALL = 16
_DROPDOWN_WIDTH = AppStyles.CONTROL_WIDTH_MD
_CARD_PADDING = 15
_CARD_BORDER_RADIUS = 8
_SPACING_DEFAULT = 20
_SPACING_SMALL = 10


class AutomationTab(ft.Container):
    """自动化任务设置标签页"""

    def __init__(self, show_snack_callback):
        super().__init__()
        self.show_snack = show_snack_callback
        self._locale_subscription_id = None

        auto_update_enabled = ConfigHandler.is_auto_update_enabled()
        auto_update_time = ConfigHandler.get_auto_update_time()

        self.schedule_enabled = ft.Switch(
            label=I18n.get("settings_auto_update"),
            value=auto_update_enabled,
            on_change=self.on_schedule_toggle,
        )
        self.schedule_time = ft.Dropdown(
            label=I18n.get("settings_update_time"),
            width=_DROPDOWN_WIDTH,
            value=auto_update_time,
            options=self._build_time_options(),
            on_change=self.on_schedule_time_change,
            disabled=not auto_update_enabled,
        )

        self.schedule_status = ft.Text(
            self._get_schedule_status_text(auto_update_enabled),
            size=_FONT_SIZE_SMALL,
            color=AppColors.SUCCESS if auto_update_enabled else AppColors.TEXT_HINT,
        )

        doubao_enabled = ConfigHandler.is_doubao_schedule_enabled()
        doubao_time = ConfigHandler.get_doubao_schedule_time()

        self.doubao_enabled = ft.Switch(
            label=I18n.get("settings_doubao_update"),
            value=doubao_enabled,
            on_change=self.on_doubao_toggle,
        )
        self.doubao_time = ft.Dropdown(
            label=I18n.get("settings_update_time"),
            width=_DROPDOWN_WIDTH,
            value=doubao_time,
            options=self._build_time_options(),
            on_change=self.on_doubao_time_change,
            disabled=not doubao_enabled,
        )

        self.doubao_status = ft.Text(
            self._get_schedule_status_text(doubao_enabled),
            size=_FONT_SIZE_SMALL,
            color=AppColors.SUCCESS if doubao_enabled else AppColors.TEXT_HINT,
        )

        self._build_content()

    def _build_time_options(self):
        """构建时间选项列表"""
        return [
            ft.dropdown.Option("15:30", I18n.get("settings_opt_1530")),
            ft.dropdown.Option("16:00", I18n.get("settings_opt_1600")),
            ft.dropdown.Option("16:30", I18n.get("settings_opt_1630")),
            ft.dropdown.Option("17:00", I18n.get("settings_opt_1700")),
            ft.dropdown.Option("18:00", I18n.get("settings_opt_1800")),
            ft.dropdown.Option("20:00", I18n.get("settings_opt_2000")),
        ]

    def _build_content(self):
        """构建 UI 内容"""
        self.txt_title_main = ft.Text(
            I18n.get("settings_auto_update"),
            size=_FONT_SIZE_TITLE,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )
        self.txt_desc_main = ft.Text(
            I18n.get("settings_auto_desc"),
            size=_FONT_SIZE_BODY,
            color=AppColors.TEXT_SECONDARY,
        )

        self.row_schedule = SettingRow(
            icon=ft.Icons.SCHEDULE,
            title=I18n.get("settings_auto_update"),
            subtitle=I18n.get("settings_auto_desc"),
            control=self.schedule_enabled,
            icon_color=AppColors.PRIMARY,
            title_key="settings_auto_update",
            subtitle_key="settings_auto_desc",
        )

        self.row_time = SettingRow(
            icon=ft.Icons.ACCESS_TIME,
            title=I18n.get("settings_update_time"),
            subtitle=I18n.get("settings_trading_days"),
            control=self.schedule_time,
            icon_color=AppColors.ACCENT,
            title_key="settings_update_time",
            subtitle_key="settings_trading_days",
        )

        self.card_main = DashboardCard(
            content=ft.Column(
                [
                    self.row_schedule,
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    self.row_time,
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.INFO_OUTLINE,
                                size=_ICON_SIZE_SMALL,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            self.schedule_status,
                        ],
                    ),
                ],
            ),
        )

        self.row_doubao_schedule = SettingRow(
            icon=ft.Icons.AUTO_AWESOME,
            title=I18n.get("settings_doubao_update"),
            subtitle=I18n.get(
                "settings_doubao_desc",
            ),
            control=self.doubao_enabled,
            icon_color=AppColors.PRIMARY,
            title_key="settings_doubao_update",
            subtitle_key="settings_doubao_desc",
        )

        self.row_doubao_time = SettingRow(
            icon=ft.Icons.ACCESS_TIME,
            title=I18n.get("settings_update_time"),
            subtitle=I18n.get("settings_saturdays"),
            control=self.doubao_time,
            icon_color=AppColors.ACCENT,
            title_key="settings_update_time",
            subtitle_key="settings_saturdays",
        )

        self.card_doubao = DashboardCard(
            content=ft.Column(
                [
                    self.row_doubao_schedule,
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    self.row_doubao_time,
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    ft.Row(
                        [
                            ft.Icon(
                                ft.Icons.INFO_OUTLINE,
                                size=_ICON_SIZE_SMALL,
                                color=AppColors.TEXT_SECONDARY,
                            ),
                            self.doubao_status,
                        ],
                    ),
                ],
            ),
        )

        self.txt_hint_bg = ft.Text(
            I18n.get("settings_hint_bg_run"),
            size=_FONT_SIZE_HINT,
            color=AppColors.TEXT_HINT,
        )

        self.inner_container = ft.Container(
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    self.txt_title_main,
                    self.txt_desc_main,
                    ft.Container(height=_SPACING_SMALL),
                    self.card_main,
                    self.card_doubao,
                    self.txt_hint_bg,
                ],
                spacing=_SPACING_DEFAULT,
            ),
            **AppStyles.card(),
        )
        self.content = self.inner_container

    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors."""
        # Input fields
        self.schedule_time.bgcolor = AppColors.INPUT_BG
        self.schedule_time.color = AppColors.INPUT_TEXT
        self.schedule_time.border_color = AppColors.INPUT_BORDER

        # Status color (custom)
        enabled = self.schedule_enabled.value
        self.schedule_status.color = AppColors.SUCCESS if enabled else ft.Colors.ON_SURFACE_VARIANT

        self.doubao_time.bgcolor = AppColors.INPUT_BG
        self.doubao_time.color = AppColors.INPUT_TEXT
        self.doubao_time.border_color = AppColors.INPUT_BORDER
        doubao_enabled = self.doubao_enabled.value
        self.doubao_status.color = AppColors.SUCCESS if doubao_enabled else ft.Colors.ON_SURFACE_VARIANT

        if self.page:
            self.update()

    def did_mount(self):
        """组件挂载后订阅语言变更"""
        if getattr(self, "_mounted", False):
            return
        self._mounted = True
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[AutomationTab] Subscribed to locale changes")

    def will_unmount(self):
        """组件卸载前取消订阅"""
        self._mounted = False
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[AutomationTab] Unsubscribed from locale changes")

    def _on_locale_change(self, new_locale: str = None):  # type: ignore[assignment]
        """语言变更回调

        Note: 此回调可能在非主线程触发，使用 _safe_update 确保线程安全
        """
        try:
            # 更新静态文本
            self.schedule_enabled.label = I18n.get("settings_auto_update")
            self.schedule_time.label = I18n.get("settings_update_time")
            self.schedule_time.options = self._build_time_options()
            self.schedule_status.value = self._get_schedule_status_text(
                self.schedule_enabled.value,
            )

            self.doubao_enabled.label = I18n.get(
                "settings_doubao_update",
            )
            self.doubao_time.label = I18n.get("settings_update_time")
            self.doubao_time.options = self._build_time_options()
            self.doubao_status.value = self._get_schedule_status_text(
                self.doubao_enabled.value,
            )

            for row in [self.row_schedule, self.row_time, self.row_doubao_schedule, self.row_doubao_time]:
                row.update_locale()

            # 重建整个内容以确保所有文本更新
            self._build_content()
            self._safe_update()
        except Exception as e:
            logger.warning(f"[AutomationTab] Failed to update locale: {e}")

    def _safe_update(self):
        """线程安全的 UI 更新，处理页面未附加的情况"""
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug(f"[AutomationTab] UI update skipped: {exc}")

    def _get_schedule_status_text(self, enabled):
        return I18n.get("settings_status_auto_on") if enabled else I18n.get("settings_status_auto_off")

    def on_schedule_toggle(self, e):
        """处理自动更新开关切换"""
        enabled = self.schedule_enabled.value
        ConfigHandler.save_config({"auto_update_enabled": enabled})
        self.schedule_status.value = self._get_schedule_status_text(enabled)
        self.schedule_status.color = AppColors.SUCCESS if enabled else ft.Colors.ON_SURFACE_VARIANT
        self.schedule_time.disabled = not enabled
        self.update()
        if self.show_snack:
            self.show_snack(
                I18n.get("settings_snack_auto_on") if enabled else I18n.get("settings_snack_auto_off"),
            )

    def on_schedule_time_change(self, e):
        """处理更新时间变更"""
        selected_time = self.schedule_time.value
        ConfigHandler.save_config({"auto_update_time": selected_time})
        self.update()
        if self.show_snack:
            self.show_snack(
                I18n.get("settings_snack_time_set").format(time=selected_time),
            )

    def on_doubao_toggle(self, e):
        enabled = self.doubao_enabled.value
        ConfigHandler.set_doubao_schedule_enabled(enabled)
        self.doubao_status.value = self._get_schedule_status_text(enabled)
        self.doubao_status.color = AppColors.SUCCESS if enabled else ft.Colors.ON_SURFACE_VARIANT
        self.doubao_time.disabled = not enabled
        self.update()
        if self.show_snack:
            self.show_snack(
                I18n.get("settings_snack_auto_on") if enabled else I18n.get("settings_snack_auto_off"),
            )

    def on_doubao_time_change(self, e):
        selected_time = self.doubao_time.value
        ConfigHandler.set_doubao_schedule_time(selected_time)  # type: ignore[untyped]
        self.update()
        if self.show_snack:
            self.show_snack(
                I18n.get("settings_snack_time_set").format(time=selected_time),
            )


class NotificationsTab(ft.Container):
    """通知设置标签页"""

    def __init__(self, show_snack_callback, page_ref):
        super().__init__()
        self.show_snack = show_snack_callback
        # 使用弱引用避免闭包持有强引用导致的问题
        self._page_ref = weakref.ref(page_ref) if page_ref else None
        self._locale_subscription_id = None

        enable_news = ConfigHandler.get_config("enable_news_alerts", True)
        news_interval = ConfigHandler.get_config("news_poll_interval", 60)

        self.news_alerts_enabled = ft.Switch(
            label=I18n.get("settings_news_alerts"),
            value=enable_news,
            on_change=self.on_news_toggle,
        )

        self.news_interval = ft.Dropdown(
            label=I18n.get("settings_news_interval"),
            width=AppStyles.CONTROL_WIDTH_MD,
            value=str(news_interval),
            options=self._build_interval_options(),
            on_change=self.on_interval_change,
            disabled=not enable_news,
        )

        self._build_content()

    def _build_interval_options(self):
        return [
            ft.dropdown.Option("30", I18n.get("settings_news_interval_30s")),
            ft.dropdown.Option("60", I18n.get("settings_news_interval_60s")),
            ft.dropdown.Option("300", I18n.get("settings_news_interval_5m")),
            ft.dropdown.Option("900", I18n.get("settings_news_interval_15m")),
        ]

    def _build_content(self):
        """构建 UI 内容"""
        self.txt_notify_title = ft.Text(
            I18n.get("settings_notify_title"),
            size=_FONT_SIZE_TITLE,
            weight=ft.FontWeight.BOLD,
            color=AppColors.TEXT_PRIMARY,
        )

        self.row_alerts = SettingRow(
            icon=ft.Icons.NOTIFICATIONS_ACTIVE,
            title=I18n.get("settings_news_alerts"),
            subtitle=I18n.get("settings_notify_desc"),
            control=self.news_alerts_enabled,
            icon_color=AppColors.WARNING,
            title_key="settings_news_alerts",
            subtitle_key="settings_notify_desc",
        )

        self.row_interval = SettingRow(
            icon=ft.Icons.TIMER,
            title=I18n.get("settings_news_interval"),
            subtitle=I18n.get("settings_news_interval_desc"),
            control=self.news_interval,
            icon_color=AppColors.INFO,
            title_key="settings_news_interval",
            subtitle_key="settings_news_interval_desc",
        )

        self.card_notify = DashboardCard(
            content=ft.Column(
                [
                    self.row_alerts,
                    ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                    self.row_interval,
                ],
            ),
        )

        self.txt_notify_desc = ft.Text(
            I18n.get("settings_notify_desc"),
            size=_FONT_SIZE_BODY,
            color=AppColors.TEXT_SECONDARY,
        )

        self.inner_container = ft.Container(
            content=ft.Column(
                scroll=ft.ScrollMode.AUTO,
                controls=[
                    self.txt_notify_title,
                    ft.Container(height=_SPACING_SMALL),
                    self.card_notify,
                    self.txt_notify_desc,
                ],
                spacing=_SPACING_DEFAULT,
            ),
            **AppStyles.card(),
        )
        self.content = self.inner_container

    def update_theme(self):
        """Update styles on theme change — only Layer 2 custom colors."""
        # Input fields
        self.news_interval.bgcolor = AppColors.INPUT_BG
        self.news_interval.color = AppColors.INPUT_TEXT
        self.news_interval.border_color = AppColors.INPUT_BORDER

        if self.page:
            self.update()

    def did_mount(self):
        """组件挂载后订阅语言变更"""
        if getattr(self, "_mounted2", False):
            return
        self._mounted2 = True
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)
        logger.debug("[NotificationsTab] Subscribed to locale changes")

    def will_unmount(self):
        """组件卸载前取消订阅"""
        self._mounted2 = False
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
            logger.debug("[NotificationsTab] Unsubscribed from locale changes")

    def _on_locale_change(self, new_locale: str = None):  # type: ignore[assignment]
        """语言变更回调

        Note: 此回调可能在非主线程触发，使用 _safe_update 确保线程安全
        """
        try:
            self.news_alerts_enabled.label = I18n.get("settings_news_alerts")
            self.news_interval.label = I18n.get("settings_news_interval")
            self.news_interval.options = self._build_interval_options()
            for row in [self.row_alerts, self.row_interval]:
                row.update_locale()
            self._build_content()
            self._safe_update()
        except Exception as e:
            logger.warning(f"[NotificationsTab] Failed to update locale: {e}")

    def _safe_update(self):
        """线程安全的 UI 更新，处理页面未附加的情况"""
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug(f"[NewsTab] UI update skipped: {exc}")

    def on_news_toggle(self, e):
        """处理新闻推送开关切换"""
        enabled = self.news_alerts_enabled.value
        ConfigHandler.save_config({"enable_news_alerts": enabled})

        # Update visibility -> disabled state for UI consistency
        self.news_interval.disabled = not enabled
        self.update()

        if enabled:
            if self.show_snack:
                self.show_snack(I18n.get("settings_snack_news_on"))
        elif self.show_snack:
            self.show_snack(I18n.get("settings_snack_news_off"))

    def on_interval_change(self, e):
        """处理拉取间隔变更"""
        try:
            val = int(self.news_interval.value)  # type: ignore[untyped]
            ConfigHandler.save_config({"news_poll_interval": val})
            if self.show_snack:
                self.show_snack(
                    I18n.get("settings_snack_interval_set").format(interval=val),
                )
        except ValueError:
            pass
