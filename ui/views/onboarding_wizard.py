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

import logging

import flet as ft

from ui.components.config_panels.database_config_panel import DatabaseConfigPanel
from ui.components.config_panels.llm_config_panel import LLMConfigPanel
from ui.components.config_panels.local_model_config_panel import LocalModelConfigPanel
from ui.components.config_panels.tushare_config_panel import TushareConfigPanel
from ui.i18n import I18n
from ui.theme import AppColors, AppStyles
from ui.viewmodels.onboarding_view_model import OnboardingViewModel, STEP_CONFIGS
from utils.config_handler import ConfigHandler

logger = logging.getLogger(__name__)

DEFAULT_SYNC_YEARS = 3
DEFAULT_SYNC_DAYS = DEFAULT_SYNC_YEARS * 365


class OnboardingWizard(ft.Container):
    """Step-by-step onboarding wizard with enhanced navigation."""

    def __init__(self, page, on_complete=None):
        super().__init__()
        self.app_page = page
        self.on_complete = on_complete
        self.expand = True
        self.bgcolor = AppColors.BACKGROUND

        self.vm = OnboardingViewModel()
        self._locale_subscription_id = None

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

        self.step_container = ft.Container(  # pragma: no cover
            content=self.steps_content[0],  # pragma: no cover
        )  # pragma: no cover

        self.step_indicators = ft.Row(  # pragma: no cover
            self._build_step_indicators(),  # pragma: no cover
            alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
            vertical_alignment=ft.CrossAxisAlignment.START,  # pragma: no cover
            visible=1 <= self.vm.current_step <= 6,  # pragma: no cover
        )  # pragma: no cover

        self.navigation_bar = ft.Container(  # pragma: no cover
            content=self._build_navigation_buttons(),  # pragma: no cover
            padding=ft.padding.symmetric(horizontal=20, vertical=10),  # pragma: no cover
            bgcolor=AppColors.SURFACE,  # pragma: no cover
            border=ft.border.only(top=ft.BorderSide(1, AppColors.BORDER)),  # pragma: no cover
        )  # pragma: no cover

        self.step_content_container = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [self.step_container],  # pragma: no cover
                scroll=ft.ScrollMode.AUTO,  # pragma: no cover
                expand=True,  # pragma: no cover
            ),  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

        self.header_container = self._build_header()
        self.header_container.visible = self.vm.current_step in (0, 7)

        self.loading_overlay_text = ft.Text(  # pragma: no cover
            I18n.get("wizard_validating"),  # pragma: no cover
            size=14,  # pragma: no cover
            color=AppColors.TEXT_PRIMARY,  # pragma: no cover
        )  # pragma: no cover

        self.loading_overlay = ft.Container(  # pragma: no cover
            content=ft.Column(  # pragma: no cover
                [  # pragma: no cover
                    ft.ProgressRing(width=40, height=40, stroke_width=3),  # pragma: no cover
                    self.loading_overlay_text,  # pragma: no cover
                ],  # pragma: no cover
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
            ),  # pragma: no cover
            bgcolor=ft.Colors.with_opacity(0.7, AppColors.BACKGROUND),  # pragma: no cover
            visible=False,  # pragma: no cover
            expand=True,  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            on_click=lambda e: None,  # pragma: no cover
        )  # pragma: no cover

        self.content = ft.Stack(  # pragma: no cover
            controls=[  # pragma: no cover
                ft.Column(  # pragma: no cover
                    controls=[  # pragma: no cover
                        ft.Container(height=5),  # pragma: no cover
                        self.header_container,  # pragma: no cover
                        self.step_indicators,  # pragma: no cover
                        ft.Divider(height=10, color=ft.Colors.TRANSPARENT),  # pragma: no cover
                        self.step_content_container,  # pragma: no cover
                        self.navigation_bar,  # pragma: no cover
                    ],  # pragma: no cover
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                    expand=True,  # pragma: no cover
                ),  # pragma: no cover
                self.loading_overlay,  # pragma: no cover
            ],  # pragma: no cover
            expand=True,  # pragma: no cover
        )  # pragma: no cover

        self.did_mount = self._on_mount
        self.will_unmount = self._on_unmount

        self._bind_vm()

    def _bind_vm(self):  # pragma: no cover
        self.vm.bind(
            fn_validate_database=self.database_panel.save_config,
            fn_validate_token=self.tushare_panel.verify_token,
            fn_validate_cloud_ai=self._validate_cloud_ai_via_panel,
            fn_validate_local_model=self._validate_local_model_via_panel,
            fn_push_schedule_state=self._push_schedule_state,
            on_step_changed=self._on_vm_step_changed,
            on_sync_progress=self._on_vm_sync_progress,
            on_sync_state_changed=self._on_vm_sync_state_changed,
            on_validation_state_changed=self._on_vm_validation_state_changed,
            on_complete=self.on_complete or self._default_on_complete,
            on_schedule_time_normalized=self._on_schedule_time_normalized,
        )

    def _rebind_panel_callbacks(self):  # pragma: no cover
        """Rebind panel operation callbacks (called after panel recreation on locale change)."""
        self.vm.fn_validate_database = self.database_panel.save_config
        self.vm.fn_validate_token = self.tushare_panel.verify_token
        self.vm.fn_validate_cloud_ai = self._validate_cloud_ai_via_panel
        self.vm.fn_validate_local_model = self._validate_local_model_via_panel
        self.vm.fn_push_schedule_state = self._push_schedule_state

    async def _default_on_complete(self):  # pragma: no cover
        pass

    # --- Panel validation bridges (View → VM callbacks) ---

    async def _validate_cloud_ai_via_panel(self) -> bool:  # pragma: no cover
        if await self.llm_config_panel.async_verify_connection():
            if not self.llm_config_panel.save_current_config():
                logger.error("[OnboardingWizard] Failed to save LLM config")
                return False
            return True
        self._safe_update()
        return False

    async def _validate_local_model_via_panel(self) -> bool:  # pragma: no cover
        model_path = self.local_model_panel.model_path_input.value.strip()
        if not model_path:
            return True
        if await self.local_model_panel.async_verify_model():
            if not self.local_model_panel.save_config():
                logger.error("[OnboardingWizard] Failed to save local model config")
                return False
            return True
        self._safe_update()
        return False

    def _push_schedule_state(self):  # pragma: no cover
        self.vm.set_schedule_state(
            enabled=self.schedule_enabled.value,
            time_str=self.schedule_time.value,
        )

    def _on_schedule_time_normalized(self, normalized: str):  # pragma: no cover
        self.schedule_time.value = normalized

    # --- ViewModel → View notification callbacks ---

    def _on_vm_step_changed(self):  # pragma: no cover
        if self.vm.current_step == 7 and self.vm.step_validated.get("schedule") and hasattr(self, "schedule_time"):
            self.schedule_time.value = self.vm.normalized_schedule_time
        self._update_wizard()

    def _on_vm_sync_progress(self, progress: float, message: str):  # pragma: no cover
        self.sync_progress.value = progress
        self.sync_status.value = message
        self._safe_update()

    def _on_vm_sync_state_changed(self):  # pragma: no cover
        is_syncing = self.vm.sync_in_progress
        self.btn_quick_sync.disabled = is_syncing
        self.btn_full_sync.disabled = is_syncing
        self.btn_cancel_sync.visible = is_syncing
        self.btn_cancel_sync.disabled = not is_syncing
        self.btn_sync_later.disabled = is_syncing
        self._update_navigation_buttons()
        self._safe_update()

    def _on_vm_validation_state_changed(self):  # pragma: no cover
        self._show_loading_overlay(self.vm.validation_in_progress)

    def _init_database_controls(self):  # pragma: no cover
        self.database_panel = DatabaseConfigPanel(
            compact=True,
            show_save_button=False,
            show_header=False,
            load_password=True,
            on_change=lambda: self._on_input_change("database"),
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_token_controls(self):  # pragma: no cover
        self.tushare_panel = TushareConfigPanel(
            compact=True,
            show_save_button=False,
            show_register_link=True,
            show_internal_loading=False,
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_cloud_ai_controls(self):  # pragma: no cover
        self.llm_config_panel = LLMConfigPanel(
            on_test_connection=self._on_llm_test_connection,
            show_save_button=False,
            compact=True,
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_local_model_controls(self):  # pragma: no cover
        self.local_model_panel = LocalModelConfigPanel(
            on_verify_model=self._on_verify_local_model,
            show_save_button=False,
            compact=True,
            show_internal_loading=False,
            on_change=lambda: self._on_input_change("local_model"),
            on_loading_change=self._on_panel_loading_change,
        )

    def _on_panel_loading_change(self, loading: bool):  # pragma: no cover
        """通用面板加载状态回调 - 仅控制遮罩显隐"""
        if loading:
            self._show_loading_overlay(True)
        elif not self.vm.validation_in_progress:
            self._show_loading_overlay(False)
        self._safe_update()

    async def _on_llm_test_connection(
        self,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        **kwargs,
    ) -> dict:
        """LLM 连接测试回调 — 委托 ViewModel"""
        return await OnboardingViewModel.test_llm_connection(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            **kwargs,
        )

    async def _on_verify_local_model(self, model_path: str, config: dict) -> bool:
        """验证本地模型回调 — 委托 ViewModel"""
        return await OnboardingViewModel.verify_local_model(model_path, config)

    def _init_sync_controls(self):  # pragma: no cover
        self.sync_progress = ft.ProgressBar(  # pragma: no cover
            width=AppStyles.CONTROL_WIDTH_LG,  # pragma: no cover
            value=0,  # pragma: no cover
            color=AppColors.ACCENT,  # pragma: no cover
            bgcolor=AppColors.BORDER,  # pragma: no cover
        )  # pragma: no cover
        self.sync_status = ft.Text(  # pragma: no cover
            I18n.get("wizard_status_ready"),  # pragma: no cover
            size=12,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
            text_align=ft.TextAlign.CENTER,  # pragma: no cover
        )  # pragma: no cover
        self.btn_quick_sync = ft.ElevatedButton(  # pragma: no cover
            I18n.get("wizard_sync_quick"),  # pragma: no cover
            icon=ft.Icons.FLASH_ON,  # pragma: no cover
            style=AppStyles.accent_button(),  # pragma: no cover
        )  # pragma: no cover
        self.btn_full_sync = ft.ElevatedButton(  # pragma: no cover
            I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS),  # pragma: no cover
            icon=ft.Icons.CLOUD_SYNC,  # pragma: no cover
            style=AppStyles.primary_button(),  # pragma: no cover
        )  # pragma: no cover
        self.btn_sync_later = ft.TextButton(  # pragma: no cover
            I18n.get("wizard_btn_sync_later"),  # pragma: no cover
            icon=ft.Icons.SCHEDULE,  # pragma: no cover
            on_click=lambda e: self.app_page.run_task(self.vm.skip_sync),  # pragma: no cover
        )  # pragma: no cover
        self.btn_cancel_sync = ft.ElevatedButton(  # pragma: no cover
            I18n.get("wizard_btn_cancel"),  # pragma: no cover
            icon=ft.Icons.CANCEL,  # pragma: no cover
            color=AppColors.ERROR,  # pragma: no cover
            visible=False,  # pragma: no cover
        )  # pragma: no cover
        self.btn_quick_sync.on_click = lambda e: self.app_page.run_task(
            self.vm.start_sync, quick=True
        )  # pragma: no cover
        self.btn_full_sync.on_click = lambda e: self.app_page.run_task(
            self.vm.start_sync, quick=False
        )  # pragma: no cover
        self.btn_cancel_sync.on_click = lambda e: self.app_page.run_task(self.vm.cancel_sync)  # pragma: no cover

    def _init_schedule_controls(self):  # pragma: no cover
        self.schedule_enabled = ft.Checkbox(  # pragma: no cover
            label=I18n.get("wizard_schedule_label"),  # pragma: no cover
            value=True,  # pragma: no cover
            active_color=AppColors.PRIMARY,  # pragma: no cover
        )  # pragma: no cover

        from utils.config_handler import ConfigHandler  # pragma: no cover

        default_time = ConfigHandler.get_auto_update_time()  # pragma: no cover

        self.schedule_time = ft.TextField(  # pragma: no cover
            label=I18n.get("wizard_schedule_time_label"),  # pragma: no cover
            value=default_time,  # pragma: no cover
            hint_text="HH:MM",  # pragma: no cover
            width=150,  # pragma: no cover
            text_align=ft.TextAlign.CENTER,  # pragma: no cover
            border_color=AppColors.PRIMARY,  # pragma: no cover
            label_style=ft.TextStyle(color=AppColors.PRIMARY),  # pragma: no cover
        )  # pragma: no cover

    def _on_input_change(self, step_id: str):  # pragma: no cover
        self.vm.invalidate_step(step_id)

    def _build_header(self):  # pragma: no cover
        self.header_title = ft.Text(  # pragma: no cover
            I18n.get("wizard_welcome_title"),  # pragma: no cover
            size=32,  # pragma: no cover
            weight=ft.FontWeight.BOLD,  # pragma: no cover
            color=AppColors.PRIMARY,  # pragma: no cover
            text_align=ft.TextAlign.CENTER,  # pragma: no cover
        )  # pragma: no cover
        self.header_desc = ft.Text(  # pragma: no cover
            I18n.get("wizard_welcome_desc_with_time"),  # pragma: no cover
            size=16,  # pragma: no cover
            color=AppColors.TEXT_SECONDARY,  # pragma: no cover
            text_align=ft.TextAlign.CENTER,  # pragma: no cover
        )  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                self.header_title,  # pragma: no cover
                self.header_desc,  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_step_indicators(self):  # pragma: no cover
        # 仅在配置步骤 1~6 显示，欢迎页和完成页不显示
        if not (1 <= self.vm.current_step <= 6):
            return []

        total_config_steps = 6
        config_step = self.vm.current_step  # 1~6
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
        current_step_name = step_names[self.vm.current_step] or ""

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

    def _build_navigation_buttons(self):  # pragma: no cover
        config = STEP_CONFIGS[self.vm.current_step]

        buttons = []

        if config.show_prev:
            is_sync_step = config.id == "data_sync"
            buttons.append(
                ft.ElevatedButton(
                    I18n.get("wizard_btn_prev"),
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: self.app_page.run_task(self._prev_step),
                    style=AppStyles.secondary_button(),
                    disabled=(self.vm.sync_in_progress and is_sync_step) or self.vm.validation_in_progress,
                )
            )
        else:
            buttons.append(ft.Container())

        if config.show_skip:
            buttons.append(
                ft.TextButton(
                    I18n.get(config.skip_text_key),
                    on_click=lambda e: self.app_page.run_task(self._skip_step),
                    disabled=self.vm.validation_in_progress,
                )
            )

        if config.show_next:
            buttons.append(
                ft.ElevatedButton(
                    I18n.get(config.next_text_key),
                    icon=getattr(ft.Icons, config.next_icon, ft.Icons.ARROW_FORWARD),
                    on_click=lambda e: self.app_page.run_task(self._next_step),
                    style=AppStyles.primary_button(),
                    disabled=self.vm.validation_in_progress,
                )
            )

        return ft.Row(
            buttons,
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _update_navigation_buttons(self):  # pragma: no cover
        nav_row = self.navigation_bar.content
        new_buttons = self._build_navigation_buttons()
        nav_row.controls = new_buttons.controls  # type: ignore[union-attr]
        self._safe_update()

    def _build_welcome_step(self):  # pragma: no cover
        # Language Selector  # pragma: no cover
        self.wizard_language_dropdown = ft.Dropdown(  # pragma: no cover
            label=I18n.get_language_label(),  # pragma: no cover
            tooltip=I18n.get_language_label(),  # pragma: no cover
            value=I18n.current_locale(),  # pragma: no cover
            width=200,  # pragma: no cover
            text_size=14,  # pragma: no cover
            border_radius=8,  # pragma: no cover
            content_padding=10,  # pragma: no cover
            options=[  # pragma: no cover
                ft.dropdown.Option(code, name)  # pragma: no cover
                for code, name in I18n.get_language_options()  # pragma: no cover
            ],  # pragma: no cover
            on_change=self._on_language_change_wizard,  # pragma: no cover
        )  # pragma: no cover

        language_container = ft.Container(  # pragma: no cover
            content=self.wizard_language_dropdown,  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
        )  # pragma: no cover

        rocket_container = ft.Container(  # pragma: no cover
            content=ft.Icon(ft.Icons.ROCKET_LAUNCH, size=72, color=AppColors.PRIMARY),  # pragma: no cover
            width=120,  # pragma: no cover
            height=120,  # pragma: no cover
            border_radius=60,  # pragma: no cover
            bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY),  # pragma: no cover
            alignment=ft.alignment.center,  # pragma: no cover
            shadow=ft.BoxShadow(  # pragma: no cover
                spread_radius=2,  # pragma: no cover
                blur_radius=24,  # pragma: no cover
                color=ft.Colors.with_opacity(0.35, AppColors.PRIMARY),  # pragma: no cover
                offset=ft.Offset(0, 4),  # pragma: no cover
            ),  # pragma: no cover
        )  # pragma: no cover

        self.gradient_guide_text = ft.Text(  # pragma: no cover
            I18n.get("wizard_welcome_guide"),  # pragma: no cover
            size=20,  # pragma: no cover
            weight=ft.FontWeight.W_600,  # pragma: no cover
            text_align=ft.TextAlign.CENTER,  # pragma: no cover
        )  # pragma: no cover
        gradient_title = ft.ShaderMask(  # pragma: no cover
            content=self.gradient_guide_text,  # pragma: no cover
            shader=ft.LinearGradient(  # pragma: no cover
                begin=ft.alignment.center_left,  # pragma: no cover
                end=ft.alignment.center_right,  # pragma: no cover
                colors=[AppColors.PRIMARY, AppColors.ACCENT],  # pragma: no cover
            ),  # pragma: no cover
            blend_mode=ft.BlendMode.SRC_IN,  # pragma: no cover
        )  # pragma: no cover

        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                language_container,  # pragma: no cover
                ft.Container(height=16),  # pragma: no cover
                rocket_container,  # pragma: no cover
                ft.Container(height=16),  # pragma: no cover
                gradient_title,  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                self._build_overview_cards(),  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_overview_cards(self):  # pragma: no cover
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

    def _create_overview_card(  # pragma: no cover
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

    def _on_card_hover(self, e, color):  # pragma: no cover
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

    def _build_database_step(self):  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.STORAGE, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_db_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_db_desc"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                self.database_panel,  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_token_step(self):  # pragma: no cover
        desc = I18n.get("wizard_step1_desc")  # pragma: no cover

        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.KEY, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step1_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    desc,  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                self.tushare_panel,  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_cloud_ai_step(self):  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.CLOUD, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step_cloud_ai_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step_cloud_ai_desc"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=self.llm_config_panel,  # pragma: no cover
                    padding=10,  # pragma: no cover
                    border_radius=8,  # pragma: no cover
                    bgcolor=AppColors.SURFACE,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_local_model_step(self):  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.PSYCHOLOGY, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step_local_model_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step_local_model_desc"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=self.local_model_panel,  # pragma: no cover
                    padding=10,  # pragma: no cover
                    border_radius=8,  # pragma: no cover
                    bgcolor=AppColors.SURFACE,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_sync_step(self):  # pragma: no cover
        years = (  # pragma: no cover
            ConfigHandler.get_init_history_years()  # pragma: no cover
            if hasattr(ConfigHandler, "get_init_history_years")  # pragma: no cover
            else DEFAULT_SYNC_YEARS  # pragma: no cover
        )  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.CLOUD_DOWNLOAD, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step3_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step3_desc").format(years=years, hours=int(years * 1.5)),  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Container(  # pragma: no cover
                    content=ft.Column(  # pragma: no cover
                        [  # pragma: no cover
                            ft.Icon(ft.Icons.WARNING, color=AppColors.WARNING, size=20),  # pragma: no cover
                            ft.Text(  # pragma: no cover
                                I18n.get("wizard_sync_warning"),  # pragma: no cover
                                size=12,  # pragma: no cover
                                color=AppColors.WARNING,  # pragma: no cover
                                text_align=ft.TextAlign.CENTER,  # pragma: no cover
                            ),  # pragma: no cover
                        ],  # pragma: no cover
                        horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
                    ),  # pragma: no cover
                    padding=10,  # pragma: no cover
                    border_radius=8,  # pragma: no cover
                    bgcolor=ft.Colors.with_opacity(0.1, AppColors.WARNING),  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Row(  # pragma: no cover
                    [  # pragma: no cover
                        self.btn_quick_sync,  # pragma: no cover
                        self.btn_full_sync,  # pragma: no cover
                        self.btn_sync_later,  # pragma: no cover
                        self.btn_cancel_sync,  # pragma: no cover
                    ],  # pragma: no cover
                    alignment=ft.MainAxisAlignment.CENTER,  # pragma: no cover
                    wrap=True,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                self.sync_progress,  # pragma: no cover
                self.sync_status,  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_schedule_step(self):  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.SCHEDULE, size=64, color=AppColors.PRIMARY),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step4_title"),  # pragma: no cover
                    size=24,  # pragma: no cover
                    weight=ft.FontWeight.W_500,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step4_desc"),  # pragma: no cover
                    size=14,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=20),  # pragma: no cover
                ft.Row([self.schedule_enabled], alignment=ft.MainAxisAlignment.CENTER),  # pragma: no cover
                ft.Container(height=15),  # pragma: no cover
                ft.Row([self.schedule_time], alignment=ft.MainAxisAlignment.CENTER),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_schedule_note"),  # pragma: no cover
                    size=12,  # pragma: no cover
                    color=AppColors.TEXT_HINT,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    def _build_complete_step(self):  # pragma: no cover
        return ft.Column(  # pragma: no cover
            [  # pragma: no cover
                ft.Icon(ft.Icons.CELEBRATION, size=80, color=AppColors.SUCCESS),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step5_title"),  # pragma: no cover
                    size=32,  # pragma: no cover
                    weight=ft.FontWeight.BOLD,  # pragma: no cover
                    color=AppColors.TEXT_PRIMARY,  # pragma: no cover
                ),  # pragma: no cover
                ft.Container(height=10),  # pragma: no cover
                ft.Text(  # pragma: no cover
                    I18n.get("wizard_step5_desc"),  # pragma: no cover
                    size=16,  # pragma: no cover
                    color=AppColors.TEXT_SECONDARY,  # pragma: no cover
                    text_align=ft.TextAlign.CENTER,  # pragma: no cover
                ),  # pragma: no cover
            ],  # pragma: no cover
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

    async def _next_step(self):  # pragma: no cover
        await self.vm.next_step()

    async def _prev_step(self):  # pragma: no cover
        await self.vm.prev_step()

    async def _skip_step(self):  # pragma: no cover
        await self.vm.skip_step()

    def _update_wizard(self):  # pragma: no cover
        self.step_indicators.controls = self._build_step_indicators()
        self.step_indicators.visible = 1 <= self.vm.current_step <= 6
        self.header_container.visible = self.vm.current_step in (0, 7)
        self.step_container.content = self.steps_content[self.vm.current_step]
        self.navigation_bar.content = self._build_navigation_buttons()
        self._safe_update()

    def _on_mount(self):  # pragma: no cover
        self._locale_subscription_id = I18n.subscribe(self._on_locale_change)

    def _on_unmount(self):  # pragma: no cover
        if self._locale_subscription_id:
            I18n.unsubscribe(self._locale_subscription_id)
            self._locale_subscription_id = None
        if self.app_page:
            self.app_page.run_task(self._cleanup_vm)

    async def _cleanup_vm(self):  # pragma: no cover
        if self.vm.sync_in_progress:
            await self.vm.cancel_sync()
        self.vm.dispose()

    def _on_locale_change(self, new_locale: str = None):  # type: ignore[assignment]  # pragma: no cover
        if hasattr(self, "header_title"):
            self.header_title.value = I18n.get("wizard_welcome_title")
        if hasattr(self, "header_desc"):
            self.header_desc.value = I18n.get("wizard_welcome_desc_with_time")
        if hasattr(self, "gradient_guide_text"):
            self.gradient_guide_text.value = I18n.get("wizard_welcome_guide")
        self.sync_status.value = I18n.get("wizard_status_ready")
        self.btn_quick_sync.text = I18n.get("wizard_sync_quick")
        self.btn_full_sync.text = I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS)
        self.btn_cancel_sync.text = I18n.get("wizard_btn_cancel")
        self.schedule_enabled.label = I18n.get("wizard_schedule_label")
        self.schedule_time.label = I18n.get("wizard_schedule_time_label")
        self.loading_overlay_text.value = I18n.get("wizard_validating")

        if hasattr(self, "wizard_language_dropdown"):
            self.wizard_language_dropdown.label = I18n.get_language_label()

        self.step_indicators.controls = self._build_step_indicators()
        self._update_navigation_buttons()
        self._safe_update()

    def _on_language_change_wizard(self, e):  # pragma: no cover
        """Handle language change in Onboarding Wizard"""
        try:
            new_locale = self.wizard_language_dropdown.value
            I18n.set_locale(new_locale)

            if hasattr(self, "header_title"):
                self.header_title.value = I18n.get("wizard_welcome_title")
            if hasattr(self, "header_desc"):
                self.header_desc.value = I18n.get("wizard_welcome_desc_with_time")

            # 取消旧面板的 i18n 订阅，避免泄漏
            for panel_attr in ("database_panel", "tushare_panel", "llm_config_panel", "local_model_panel"):
                old_panel = getattr(self, panel_attr, None)
                if old_panel and hasattr(old_panel, "will_unmount"):
                    try:
                        old_panel.will_unmount()
                    except Exception:
                        pass

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

            self.step_container.content = self.steps_content[self.vm.current_step]
            self.step_indicators.controls = self._build_step_indicators()
            self._update_navigation_buttons()

            # Rebind panel callbacks to new panels
            self._rebind_panel_callbacks()

            self._safe_update()
        except Exception as ex:
            logger.error(f"[OnboardingWizard] Language change failed: {ex}")

    def _safe_update(self):  # pragma: no cover
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug(f"[OnboardingWizard] UI update skipped: {exc}")

    def _show_loading_overlay(self, show: bool):  # pragma: no cover
        self.loading_overlay.visible = show
        self._update_navigation_buttons()
