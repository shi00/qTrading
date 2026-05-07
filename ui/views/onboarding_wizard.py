"""
Onboarding Wizard with Enhanced Navigation

Steps (8 total):
0. Welcome - Configuration overview
1. Database Configuration (Required)
2. Token Configuration (Required)
3. Cloud AI Configuration (Required)
4. Local Model Configuration (Optional)
5. Data Sync (Optional)
6. Schedule Setup (Optional)
7. Complete

Features:
- Fixed navigation bar at bottom
- Step-by-step validation with gradual persistence
- Back navigation with re-validation
- Component reuse (LLMConfigPanel, LocalModelConfigPanel)
"""

import asyncio
import logging
from dataclasses import dataclass

import flet as ft

from data.data_processor import DataProcessor
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

DEFAULT_SYNC_YEARS = 3
DEFAULT_SYNC_DAYS = DEFAULT_SYNC_YEARS * 365


@dataclass
class StepConfig:
    id: str
    name: str
    show_prev: bool
    show_next: bool
    next_text_key: str
    next_icon: str
    show_skip: bool = False
    skip_text_key: str = ""
    required: bool = False
    validate_before_next: bool = False


STEP_CONFIGS = [
    StepConfig(
        id="welcome",
        name="wizard_step_welcome",
        show_prev=False,
        show_next=True,
        next_text_key="wizard_btn_start",
        next_icon=ft.Icons.ARROW_FORWARD,
        required=False,
        validate_before_next=False,
    ),
    StepConfig(
        id="database",
        name="wizard_step_database",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon=ft.Icons.ARROW_FORWARD,
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="token",
        name="wizard_step_token",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon=ft.Icons.ARROW_FORWARD,
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="cloud_ai",
        name="wizard_step_cloud_ai",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon=ft.Icons.ARROW_FORWARD,
        required=True,
        validate_before_next=True,
    ),
    StepConfig(
        id="local_model",
        name="wizard_step_local_model",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_verify_next",
        next_icon=ft.Icons.ARROW_FORWARD,
        show_skip=True,
        skip_text_key="wizard_btn_skip",
        required=False,
        validate_before_next=True,
    ),
    StepConfig(
        id="data_sync",
        name="wizard_step_data_sync",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_next",
        next_icon=ft.Icons.ARROW_FORWARD,
        required=False,
        validate_before_next=False,
    ),
    StepConfig(
        id="schedule",
        name="wizard_step_schedule",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_finish",
        next_icon=ft.Icons.CHECK_CIRCLE,
        required=False,
        validate_before_next=True,
    ),
    StepConfig(
        id="complete",
        name="wizard_step_complete",
        show_prev=True,
        show_next=True,
        next_text_key="wizard_btn_start",
        next_icon=ft.Icons.ROCKET_LAUNCH,
        required=False,
        validate_before_next=False,
    ),
]


class OnboardingWizard(ft.Container):
    """Step-by-step onboarding wizard with enhanced navigation."""

    def __init__(self, page, on_complete=None):
        super().__init__()
        self.app_page = page
        self.on_complete = on_complete
        self.current_step = 0
        self.expand = True
        self.bgcolor = AppColors.BACKGROUND

        self.step_validated: dict[str, bool] = {}
        self._locale_subscription_id = None
        self.sync_in_progress = False
        self._data_processor = None
        self._validation_in_progress = False

        self._init_database_controls()
        self._init_token_controls()
        self._init_cloud_ai_controls()
        self._init_local_model_controls()
        self._init_sync_controls()
        self._init_schedule_controls()

        self.steps_content = [
            self._build_welcome_step(),
            self._build_database_step(),
            self._build_token_step(),
            self._build_cloud_ai_step(),
            self._build_local_model_step(),
            self._build_sync_step(),
            self._build_schedule_step(),
            self._build_complete_step(),
        ]

        self.step_container = ft.Container(
            content=self.steps_content[0],
        )

        self.step_indicators = ft.Row(
            self._build_step_indicators(),
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.START,
            visible=1 <= self.current_step <= 6,
        )

        self.navigation_bar = ft.Container(
            content=self._build_navigation_buttons(),
            padding=ft.padding.symmetric(horizontal=20, vertical=10),
            bgcolor=AppColors.SURFACE,
            border=ft.border.only(top=ft.BorderSide(1, AppColors.BORDER)),
        )

        self.step_content_container = ft.Container(
            content=ft.Column(
                [self.step_container],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
            expand=True,
        )

        self.header_container = self._build_header()
        self.header_container.visible = self.current_step in (0, 7)

        self.loading_overlay_text = ft.Text(
            I18n.get("wizard_validating"),
            size=14,
            color=AppColors.TEXT_PRIMARY,
        )

        self.loading_overlay = ft.Container(
            content=ft.Column(
                [
                    ft.ProgressRing(width=40, height=40, stroke_width=3),
                    self.loading_overlay_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=ft.Colors.with_opacity(0.7, AppColors.BACKGROUND),
            visible=False,
            expand=True,
            alignment=ft.alignment.center,
            on_click=lambda e: None,
        )

        self.content = ft.Stack(
            controls=[
                ft.Column(
                    controls=[
                        ft.Container(height=5),
                        self.header_container,
                        self.step_indicators,
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),
                        self.step_content_container,
                        self.navigation_bar,
                    ],
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    expand=True,
                ),
                self.loading_overlay,
            ],
            expand=True,
        )

        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

    def _init_database_controls(self):
        from ui.components.config_panels.database_config_panel import (
            DatabaseConfigPanel,
        )

        self.database_panel = DatabaseConfigPanel(
            compact=True,
            show_save_button=False,
            show_header=False,
            load_password=True,
            on_change=lambda: self._on_input_change("database"),
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_token_controls(self):
        from ui.components.config_panels.tushare_config_panel import TushareConfigPanel

        self.tushare_panel = TushareConfigPanel(
            compact=True,
            show_save_button=False,
            show_register_link=True,
            show_internal_loading=False,
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_cloud_ai_controls(self):
        from ui.components.config_panels.llm_config_panel import LLMConfigPanel

        self.llm_config_panel = LLMConfigPanel(
            show_save_button=False,
            compact=True,
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_local_model_controls(self):
        from ui.components.config_panels.local_model_config_panel import (
            LocalModelConfigPanel,
        )

        self.local_model_panel = LocalModelConfigPanel(
            show_save_button=False,
            compact=True,
            show_internal_loading=False,
            on_change=lambda: self._on_input_change("local_model"),
            on_loading_change=self._on_panel_loading_change,
        )

    def _on_panel_loading_change(self, loading: bool):
        """通用面板加载状态回调 - 仅控制遮罩显隐"""
        self._show_loading_overlay(loading)
        self._safe_update()

    def _init_sync_controls(self):
        self.sync_progress = ft.ProgressBar(
            width=AppStyles.CONTROL_WIDTH_LG,
            value=0,
            color=AppColors.ACCENT,
            bgcolor=AppColors.BORDER,
        )
        self.sync_status = ft.Text(
            I18n.get("wizard_status_ready"),
            size=12,
            color=AppColors.TEXT_SECONDARY,
            text_align=ft.TextAlign.CENTER,
        )
        self.btn_quick_sync = ft.ElevatedButton(
            I18n.get("wizard_sync_quick"),
            icon=ft.Icons.FLASH_ON,
            style=AppStyles.accent_button(),
        )
        self.btn_full_sync = ft.ElevatedButton(
            I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS),
            icon=ft.Icons.CLOUD_SYNC,
            style=AppStyles.primary_button(),
        )
        self.btn_sync_later = ft.TextButton(
            I18n.get("wizard_btn_sync_later"),
            icon=ft.Icons.SCHEDULE,
            on_click=lambda e: self.app_page.run_task(self._skip_sync),
        )
        self.btn_cancel_sync = ft.ElevatedButton(
            I18n.get("wizard_btn_cancel"),
            icon=ft.Icons.CANCEL,
            color=AppColors.ERROR,
            visible=False,
        )
        self.btn_quick_sync.on_click = lambda e: self.app_page.run_task(self._start_sync, quick=True)
        self.btn_full_sync.on_click = lambda e: self.app_page.run_task(self._start_sync, quick=False)
        self.btn_cancel_sync.on_click = lambda e: self.app_page.run_task(self._cancel_sync)

    def _init_schedule_controls(self):
        self.schedule_enabled = ft.Checkbox(
            label=I18n.get("wizard_schedule_label"),
            value=True,
            active_color=AppColors.PRIMARY,
        )

        from utils.config_handler import ConfigHandler

        default_time = ConfigHandler.get_auto_update_time()

        self.schedule_time = ft.TextField(
            label=I18n.get("wizard_schedule_time_label"),
            value=default_time,
            hint_text="HH:MM",
            width=150,
            text_align=ft.TextAlign.CENTER,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
        )

    def _on_input_change(self, step_id: str):
        self.step_validated[step_id] = False

    def _build_header(self):
        return ft.Column(
            [
                ft.Text(
                    I18n.get("wizard_welcome_title"),
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.PRIMARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Text(
                    I18n.get("wizard_welcome_desc_with_time"),
                    size=16,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_step_indicators(self):
        # 仅在配置步骤 1~6 显示，欢迎页和完成页不显示
        if not (1 <= self.current_step <= 6):
            return []

        total_config_steps = 6
        config_step = self.current_step  # 1~6
        progress_percent = config_step / total_config_steps

        step_names = [
            None,  # 0: 欢迎(不显示)
            I18n.get("wizard_step_database"),
            I18n.get("wizard_step_label_token"),
            I18n.get("wizard_step_label_ai"),
            I18n.get("wizard_step_local_model"),
            I18n.get("wizard_step_label_sync"),
            I18n.get("wizard_step_label_schedule"),
            None,  # 7: 完成(不显示)
        ]
        current_step_name = step_names[self.current_step] or ""

        step_text = ft.Text(
            f"{current_step_name}  ({config_step}/{total_config_steps})",
            size=14,
            weight=ft.FontWeight.W_600,
            color=AppColors.TEXT_PRIMARY,
        )

        progress_bar = ft.Container(
            content=ft.Stack(
                [
                    ft.Container(
                        width=200,
                        height=4,
                        bgcolor=AppColors.BORDER,
                        border_radius=2,
                    ),
                    ft.Container(
                        width=200 * progress_percent,
                        height=4,
                        bgcolor=AppColors.PRIMARY,
                        border_radius=2,
                    ),
                ],
            ),
            padding=ft.padding.only(top=8),
        )

        return [
            ft.Column(
                [step_text, progress_bar],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=0,
            )
        ]

    def _build_navigation_buttons(self):
        config = STEP_CONFIGS[self.current_step]

        buttons = []

        if config.show_prev:
            is_sync_step = config.id == "data_sync"
            buttons.append(
                ft.ElevatedButton(
                    I18n.get("wizard_btn_prev"),
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: self.app_page.run_task(self._prev_step),
                    style=AppStyles.secondary_button(),
                    disabled=(self.sync_in_progress and is_sync_step) or self._validation_in_progress,
                )
            )
        else:
            buttons.append(ft.Container())

        if config.show_skip:
            buttons.append(
                ft.TextButton(
                    I18n.get(config.skip_text_key),
                    on_click=lambda e: self.app_page.run_task(self._skip_step),
                    disabled=self._validation_in_progress,
                )
            )

        if config.show_next:
            buttons.append(
                ft.ElevatedButton(
                    I18n.get(config.next_text_key),
                    icon=config.next_icon,
                    on_click=lambda e: self.app_page.run_task(self._next_step),
                    style=AppStyles.primary_button(),
                    disabled=self._validation_in_progress,
                )
            )

        return ft.Row(
            buttons,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _update_navigation_buttons(self):
        nav_row = self.navigation_bar.content
        new_buttons = self._build_navigation_buttons()
        nav_row.controls = new_buttons.controls
        self._safe_update()

    def _build_welcome_step(self):
        rocket_container = ft.Container(
            content=ft.Icon(ft.Icons.ROCKET_LAUNCH, size=72, color=AppColors.PRIMARY),
            width=120,
            height=120,
            border_radius=60,
            bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY),
            alignment=ft.alignment.center,
            shadow=ft.BoxShadow(
                spread_radius=2,
                blur_radius=24,
                color=ft.Colors.with_opacity(0.35, AppColors.PRIMARY),
                offset=ft.Offset(0, 4),
            ),
        )

        gradient_title = ft.ShaderMask(
            content=ft.Text(
                I18n.get("wizard_welcome_guide"),
                size=20,
                weight=ft.FontWeight.W_600,
                text_align=ft.TextAlign.CENTER,
            ),
            shader=ft.LinearGradient(
                begin=ft.alignment.center_left,
                end=ft.alignment.center_right,
                colors=[AppColors.PRIMARY, AppColors.ACCENT],
            ),
            blend_mode=ft.BlendMode.SRC_IN,
        )

        return ft.Column(
            [
                ft.Container(height=20),
                rocket_container,
                ft.Container(height=16),
                gradient_title,
                ft.Container(height=20),
                self._build_overview_cards(),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_overview_cards(self):
        return ft.ResponsiveRow(
            [
                self._create_overview_card(
                    icon=ft.Icons.STORAGE,
                    color=AppColors.PRIMARY,
                    title_key="wizard_overview_db_title",
                    desc_key="wizard_overview_db_desc",
                    default_title="数据库配置",
                    default_desc="配置 PostgreSQL 连接",
                    required=True,
                    gradient_index=0,
                ),
                self._create_overview_card(
                    icon=ft.Icons.KEY,
                    color=AppColors.PRIMARY,
                    title_key="wizard_overview_token_title",
                    desc_key="wizard_overview_token_desc",
                    default_title="Token 配置",
                    default_desc="设置 Tushare API 密钥",
                    required=True,
                    gradient_index=1,
                ),
                self._create_overview_card(
                    icon=ft.Icons.CLOUD,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_cloud_ai_title",
                    desc_key="wizard_overview_cloud_ai_desc",
                    default_title="云端 AI",
                    default_desc="配置 LLM API",
                    required=True,
                    gradient_index=2,
                ),
                self._create_overview_card(
                    icon=ft.Icons.PSYCHOLOGY,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_local_model_title",
                    desc_key="wizard_overview_local_model_desc",
                    default_title="本地模型",
                    default_desc="GGUF 模型配置",
                    required=False,
                    gradient_index=3,
                ),
                self._create_overview_card(
                    icon=ft.Icons.CLOUD_SYNC,
                    color=AppColors.PRIMARY,
                    title_key="wizard_overview_sync_title",
                    desc_key="wizard_overview_sync_desc",
                    default_title="数据同步",
                    default_desc="同步历史行情数据",
                    required=False,
                    gradient_index=4,
                ),
                self._create_overview_card(
                    icon=ft.Icons.SCHEDULE,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_schedule_title",
                    desc_key="wizard_overview_schedule_desc",
                    default_title="定时任务",
                    default_desc="自动更新计划",
                    required=False,
                    gradient_index=5,
                ),
            ],
            spacing=20,
            run_spacing=20,
        )

    def _create_overview_card(
        self,
        icon,
        color,
        title_key,
        desc_key,
        default_title,
        default_desc,
        required=False,
        gradient_index=0,
    ):
        required_text = I18n.get("wizard_required")
        optional_text = I18n.get("wizard_optional")

        if required:
            badge_bgcolor = ft.Colors.with_opacity(0.9, color)
            badge_text_color = AppColors.TEXT_ON_PRIMARY
            dot_color = AppColors.TEXT_ON_PRIMARY
        else:
            badge_bgcolor = ft.Colors.with_opacity(0.6, AppColors.TEXT_SECONDARY)
            badge_text_color = AppColors.TEXT_ON_PRIMARY
            dot_color = AppColors.TEXT_ON_PRIMARY

        badge = ft.Container(
            content=ft.Row(
                [
                    ft.Container(
                        width=5,
                        height=5,
                        border_radius=3,
                        bgcolor=dot_color,
                    ),
                    ft.Text(
                        required_text if required else optional_text,
                        size=9,
                        weight=ft.FontWeight.W_600,
                        color=badge_text_color,
                    ),
                ],
                spacing=4,
                alignment=ft.MainAxisAlignment.CENTER,
            ),
            bgcolor=badge_bgcolor,
            border_radius=6,
            padding=ft.padding.symmetric(horizontal=6, vertical=2),
        )

        icon_with_badge = ft.Stack(
            [
                ft.Container(
                    content=ft.Icon(icon, size=32, color=color),
                    width=52,
                    height=52,
                    border_radius=14,
                    bgcolor=ft.Colors.with_opacity(0.12, color),
                    alignment=ft.alignment.center,
                ),
                ft.Container(
                    content=badge,
                    top=-4,
                    right=-4,
                ),
            ],
            width=64,
            height=60,
            clip_behavior=ft.ClipBehavior.NONE,
        )

        card_content = ft.Container(
            padding=20,
            border_radius=16,
            bgcolor=ft.Colors.with_opacity(0.7, AppColors.SURFACE),
            border=ft.border.all(1, ft.Colors.with_opacity(0.15, AppColors.PRIMARY)),
            shadow=ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            ),
            content=ft.Column(
                [
                    icon_with_badge,
                    ft.Container(height=16),
                    ft.Text(
                        I18n.get(title_key),
                        size=16,
                        weight=ft.FontWeight.W_700,
                        color=AppColors.TEXT_PRIMARY,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Container(height=6),
                    ft.Text(
                        I18n.get(desc_key),
                        size=13,
                        color=ft.Colors.with_opacity(0.85, AppColors.TEXT_SECONDARY),
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=0,
            ),
            on_hover=lambda e: self._on_card_hover(e, color),
            animate=ft.Animation(300, ft.AnimationCurve.EASE_IN_OUT),
        )

        return ft.Container(
            col={"sm": 6, "md": 4, "lg": 4},
            content=card_content,
        )

    def _on_card_hover(self, e, color):
        if e.data == "true":
            e.control.border = ft.border.all(1.5, ft.Colors.with_opacity(0.5, color))
            e.control.shadow = ft.BoxShadow(
                spread_radius=1,
                blur_radius=12,
                color=ft.Colors.with_opacity(0.2, color),
                offset=ft.Offset(0, 3),
            )
        else:
            e.control.border = ft.border.all(1, ft.Colors.with_opacity(0.15, AppColors.PRIMARY))
            e.control.shadow = ft.BoxShadow(
                spread_radius=0,
                blur_radius=8,
                color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
                offset=ft.Offset(0, 2),
            )
        e.control.update()

    def _build_database_step(self):
        return ft.Column(
            [
                ft.Icon(ft.Icons.STORAGE, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_db_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_db_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                self.database_panel,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_token_step(self):
        desc = I18n.get("wizard_step1_desc")

        return ft.Column(
            [
                ft.Icon(ft.Icons.KEY, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step1_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    desc,
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                self.tushare_panel,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_cloud_ai_step(self):
        return ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step_cloud_ai_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step_cloud_ai_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=self.llm_config_panel,
                    padding=10,
                    border_radius=8,
                    bgcolor=AppColors.SURFACE,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_local_model_step(self):
        return ft.Column(
            [
                ft.Icon(ft.Icons.PSYCHOLOGY, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step_local_model_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step_local_model_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Container(
                    content=self.local_model_panel,
                    padding=10,
                    border_radius=8,
                    bgcolor=AppColors.SURFACE,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_sync_step(self):
        years = (
            ConfigHandler.get_init_history_years()
            if hasattr(ConfigHandler, "get_init_history_years")
            else DEFAULT_SYNC_YEARS
        )
        return ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD_DOWNLOAD, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step3_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step3_desc").format(years=years, hours=int(years * 1.5)),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=10),
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Icon(ft.Icons.WARNING, color=AppColors.WARNING, size=20),
                            ft.Text(
                                I18n.get("wizard_sync_warning"),
                                size=12,
                                color=AppColors.WARNING,
                                text_align=ft.TextAlign.CENTER,
                            ),
                        ],
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    padding=10,
                    border_radius=8,
                    bgcolor=ft.Colors.with_opacity(0.1, AppColors.WARNING),
                ),
                ft.Container(height=20),
                ft.Row(
                    [
                        self.btn_quick_sync,
                        self.btn_full_sync,
                        self.btn_sync_later,
                        self.btn_cancel_sync,
                    ],
                    alignment=ft.MainAxisAlignment.CENTER,
                    wrap=True,
                ),
                ft.Container(height=20),
                self.sync_progress,
                self.sync_status,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_schedule_step(self):
        return ft.Column(
            [
                ft.Icon(ft.Icons.SCHEDULE, size=64, color=AppColors.PRIMARY),
                ft.Text(
                    I18n.get("wizard_step4_title"),
                    size=24,
                    weight=ft.FontWeight.W_500,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step4_desc"),
                    size=14,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=20),
                ft.Row([self.schedule_enabled], alignment=ft.MainAxisAlignment.CENTER),
                ft.Container(height=15),
                ft.Row([self.schedule_time], alignment=ft.MainAxisAlignment.CENTER),
                ft.Text(
                    I18n.get("wizard_schedule_note"),
                    size=12,
                    color=AppColors.TEXT_HINT,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_complete_step(self):
        return ft.Column(
            [
                ft.Icon(ft.Icons.CELEBRATION, size=80, color=AppColors.SUCCESS),
                ft.Text(
                    I18n.get("wizard_step5_title"),
                    size=32,
                    weight=ft.FontWeight.BOLD,
                    color=AppColors.TEXT_PRIMARY,
                ),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step5_desc"),
                    size=16,
                    color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _validate_and_save_database(self) -> bool:
        self._show_loading_overlay(True)
        self._safe_update()

        try:
            result = await self.database_panel.test_connection()
            if result:
                config = self.database_panel.get_config()

                from data.persistence.db_config_service import DatabaseConfigService

                success, msg = await DatabaseConfigService.ensure_tables_exist(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )

                if not success:
                    self.database_panel.status_text.value = f"✗ {msg}"
                    self.database_panel.status_text.color = AppColors.ERROR
                    self.database_panel._safe_update()
                    return False

                ConfigHandler.save_db_config(
                    host=config["host"],
                    port=config["port"],
                    user=config["user"],
                    password=config["password"],
                    database=config["database"],
                )
            return result
        finally:
            self._show_loading_overlay(False)
            self._safe_update()

    async def _validate_and_save_token(self) -> bool:
        return await self.tushare_panel.verify_token()

    async def _validate_and_save_cloud_ai(self) -> bool:
        if await self.llm_config_panel.async_verify_connection():
            self.llm_config_panel.save_current_config()
            return True

        self._safe_update()
        return False

    async def _validate_and_save_local_model(self) -> bool:
        model_path = self.local_model_panel.model_path_input.value.strip()

        if not model_path:
            return True

        if await self.local_model_panel.async_verify_model():
            self.local_model_panel.save_config()
            return True

        self._safe_update()
        return False

    async def _validate_and_save_schedule(self) -> bool:
        enabled = self.schedule_enabled.value
        time_str = self.schedule_time.value.strip()

        import re

        if not re.match(r"^\d{1,2}:\d{2}$", time_str):
            time_str = "16:30"
        else:
            try:
                hours, minutes = map(int, time_str.split(":"))
                if not (0 <= hours <= 23 and 0 <= minutes <= 59):
                    time_str = "16:30"
            except ValueError:
                time_str = "16:30"

        self.schedule_time.value = time_str
        ConfigHandler.save_config(
            {
                "auto_update_enabled": enabled,
                "auto_update_time": time_str,
            }
        )
        return True

    async def _validate_and_persist_current_step(self) -> bool:
        config = STEP_CONFIGS[self.current_step]

        if self.step_validated.get(config.id, False):
            return True

        validators = {
            "database": self._validate_and_save_database,
            "token": self._validate_and_save_token,
            "cloud_ai": self._validate_and_save_cloud_ai,
            "local_model": self._validate_and_save_local_model,
            "schedule": self._validate_and_save_schedule,
        }

        validator = validators.get(config.id)
        if validator:
            result = await validator()
            if result:
                self.step_validated[config.id] = True
            return result

        return True

    async def _next_step(self):
        config = STEP_CONFIGS[self.current_step]

        if config.validate_before_next:  # noqa: SIM102
            if not await self._validate_and_persist_current_step():
                return

        if config.id == "complete":
            if self.on_complete:
                await self.on_complete()
            return

        if self.current_step < len(STEP_CONFIGS) - 1:
            self.current_step += 1
            self._update_wizard()

    async def _prev_step(self):
        config = STEP_CONFIGS[self.current_step]
        if config.validate_before_next:
            self.step_validated[config.id] = False

        if self.current_step > 0:
            self.current_step -= 1
            self._update_wizard()

    async def _skip_step(self):
        if self.current_step < len(STEP_CONFIGS) - 1:
            self.current_step += 1
            self._update_wizard()

    def _update_wizard(self):
        self.step_indicators.controls = self._build_step_indicators()
        self.step_indicators.visible = 1 <= self.current_step <= 6
        self.header_container.visible = self.current_step in (0, 7)
        self.step_container.content = self.steps_content[self.current_step]
        self.navigation_bar.content = self._build_navigation_buttons()
        self._safe_update()

    @property
    def data_processor(self):
        if self._data_processor is None:
            self._data_processor = DataProcessor()
        return self._data_processor

    async def _start_sync(self, quick=False):
        self.sync_in_progress = True
        self.btn_quick_sync.disabled = True
        self.btn_full_sync.disabled = True
        self.btn_cancel_sync.visible = True
        self.btn_cancel_sync.disabled = False
        self.btn_sync_later.disabled = True
        self._update_navigation_buttons()

        self.sync_status.value = I18n.get("wizard_status_init")
        self.sync_progress.value = 0
        self._safe_update()

        try:

            def progress_callback(current, total, message):
                self.sync_progress.value = current / 100
                self.sync_status.value = message
                self._safe_update()

            result = await self.data_processor.initialize_system(
                progress_callback=progress_callback,
                quick=quick,
            )

            if result:
                self.sync_status.value = I18n.get("wizard_status_done")
                self.sync_progress.value = 1
                self.btn_cancel_sync.visible = False
                self.btn_sync_later.disabled = False

                await asyncio.sleep(1)
                await self._next_step()
            else:
                self.sync_status.value = I18n.get("wizard_status_cancelled")
                self.btn_quick_sync.disabled = False
                self.btn_full_sync.disabled = False
                self.btn_sync_later.disabled = False

        except Exception as e:
            from utils.error_classifier import classify_error, get_error_message

            error_info = classify_error(e, context="general")
            self.sync_status.value = get_error_message(error_info)
            self.sync_progress.value = 0
            self.btn_quick_sync.disabled = False
            self.btn_full_sync.disabled = False
            self.btn_sync_later.disabled = False
            self.btn_cancel_sync.visible = False
        finally:
            self.sync_in_progress = False
            self._update_navigation_buttons()
            self._safe_update()

    async def _skip_sync(self):
        self.sync_status.value = I18n.get("wizard_status_skip")
        self._safe_update()
        await self._next_step()

    async def _cancel_sync(self):
        try:
            if self._data_processor:
                await self._data_processor.stop()
            self.sync_status.value = I18n.get("wizard_status_cancelled")
            self.btn_quick_sync.disabled = False
            self.btn_full_sync.disabled = False
            self.btn_sync_later.disabled = False
            self.btn_cancel_sync.visible = False
        except Exception as e:
            logger.warning(f"Failed to cancel sync: {e}")
        finally:
            self.sync_in_progress = False
            self._update_navigation_buttons()
            self._safe_update()

    def _on_mount(self):
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)

    def _on_unmount(self):
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None

    def _on_locale_change(self, new_locale: str = None):
        self.sync_status.value = I18n.get("wizard_status_ready")
        self.btn_quick_sync.text = I18n.get("wizard_sync_quick")
        self.btn_full_sync.text = I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS)
        self.btn_cancel_sync.text = I18n.get("wizard_btn_cancel")
        self.schedule_enabled.label = I18n.get("wizard_schedule_label")
        self.schedule_time.label = I18n.get("wizard_schedule_time_label")
        self.loading_overlay_text.value = I18n.get("wizard_validating")

        self.step_indicators.controls = self._build_step_indicators()
        self._update_navigation_buttons()
        self._safe_update()

    def _safe_update(self):
        try:
            if self.page:
                self.update()
        except Exception:
            pass

    def _show_loading_overlay(self, show: bool):
        self._validation_in_progress = show
        self.loading_overlay.visible = show
        self._update_navigation_buttons()
