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
from ui.i18n import I18n, refresh_dropdown_options
from ui.theme import AppColors, AppStyles
from ui.viewmodels import Message
from ui.viewmodels.database_config_panel_view_model import DatabaseConfigPanelViewModel
from ui.viewmodels.llm_config_panel_view_model import LLMConfigPanelViewModel
from ui.viewmodels.local_model_config_panel_view_model import LocalModelConfigPanelViewModel
from ui.viewmodels.onboarding_view_model import OnboardingViewModel, STEP_CONFIGS
from ui.viewmodels.tushare_config_panel_view_model import TushareConfigPanelViewModel
from utils.config_handler import ConfigHandler
from utils.log_decorators import UILogger
from utils.sanitizers import DataSanitizer
from utils.thread_pool import TaskType, ThreadPoolManager

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
        self._panel_loading: bool = False

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
            padding=ft.Padding.symmetric(horizontal=20, vertical=10),  # pragma: no cover
            bgcolor=AppColors.SURFACE,  # pragma: no cover
            border=ft.Border.only(top=ft.BorderSide(1, AppColors.BORDER)),  # pragma: no cover
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
            alignment=ft.Alignment.CENTER,  # pragma: no cover
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

        # NOTE(lazy): _prev_state 用于 state-driven diff 通知,Phase 4 View 声明式重写时移除。
        # ceiling: Phase 4 View 重写完成. upgrade: Phase 4 Task 4.x OnboardingWizard 声明式重写.
        self._prev_state = self.vm.state

        self._bind_vm()

    def _bind_vm(self):  # pragma: no cover
        self.vm.bind(
            fn_validate_database=self.database_vm.save_config,
            fn_validate_token=self.tushare_vm.verify_token,
            fn_validate_cloud_ai=self._validate_cloud_ai_via_panel,
            fn_validate_local_model=self._validate_local_model_via_panel,
            fn_push_schedule_state=self._push_schedule_state,
            on_complete=self.on_complete or self._default_on_complete,
        )
        # NOTE(lazy): on_* 通知回调已移除,改用 subscribe(state) diff 派发。
        # Phase 4 View 声明式重写时,此 subscribe + _on_vm_* handler 全部移除。
        # ceiling: Phase 4 View 重写完成. upgrade: Phase 4 Task 4.x OnboardingWizard 声明式重写.
        self.vm.subscribe(self._on_vm_state_changed)

    def _on_vm_state_changed(self, state) -> None:  # pragma: no cover
        """Single state-driven handler replacing 5 separate on_* callbacks.

        根据 state diff 派发到对应的 _on_vm_* UI 更新方法。
        NOTE(lazy): 混合态过渡实现,Phase 4 View 声明式重写时移除。
        ceiling: Phase 4 View 重写完成. upgrade: Phase 4 Task 4.x OnboardingWizard 声明式重写.
        """
        prev = self._prev_state
        if prev is None or prev.current_step != state.current_step:
            self._on_vm_step_changed()
        if prev is None or prev.sync_in_progress != state.sync_in_progress:
            self._on_vm_sync_state_changed()
        if (
            prev is None
            or prev.sync_progress != state.sync_progress
            or prev.sync_progress_message != state.sync_progress_message
        ):
            self._on_vm_sync_progress(state.sync_progress, state.sync_progress_message)
        if prev is None or prev.validation_in_progress != state.validation_in_progress:
            self._on_vm_validation_state_changed()
        if prev is None or prev.normalized_schedule_time != state.normalized_schedule_time:
            self._on_schedule_time_normalized(state.normalized_schedule_time)
        self._prev_state = state

    def _rebind_panel_callbacks(self):  # pragma: no cover
        """Rebind panel operation callbacks (called after panel recreation on locale change)."""
        self.vm.fn_validate_database = self.database_vm.save_config
        self.vm.fn_validate_token = self.tushare_vm.verify_token
        self.vm.fn_validate_cloud_ai = self._validate_cloud_ai_via_panel
        self.vm.fn_validate_local_model = self._validate_local_model_via_panel
        self.vm.fn_push_schedule_state = self._push_schedule_state

    async def _default_on_complete(self):  # pragma: no cover
        pass

    # --- Panel validation bridges (View → VM callbacks) ---

    async def _validate_cloud_ai_via_panel(self) -> bool:  # pragma: no cover
        if await self.llm_vm.verify_connection():
            if not await self.llm_vm.save_config():
                logger.error("[OnboardingWizard] Failed to save LLM config")
                self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)
                return False
            return True
        self._safe_update()
        return False

    async def _validate_local_model_via_panel(self) -> bool:  # pragma: no cover
        model_path = self.local_model_vm.state.model_path.strip()
        if not model_path:
            return True
        if await self.local_model_vm.verify_model():
            if not await self.local_model_vm.save_config():
                logger.error("[OnboardingWizard] Failed to save local model config")
                self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)
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
        self._update_wizard()

    def _on_vm_sync_progress(self, progress: float, message: Message | None):  # pragma: no cover
        self.sync_progress.value = progress
        self.sync_status.value = I18n.get(message.key, **message.params) if message else ""
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
        if self.vm.validation_in_progress:
            self._show_loading_overlay(True)
        elif not self._panel_loading:
            self._show_loading_overlay(False)

    def _init_database_controls(self):  # pragma: no cover
        # NOTE(lazy): VM 由消费方实例化（声明式 DatabaseConfigPanel 接收 vm 参数，经 use_viewmodel(vm=vm) 消费）。
        # ceiling: Phase 4 OnboardingWizard 声明式重写. upgrade: Task 4.x OnboardingWizard 声明式重写.
        self.database_vm = DatabaseConfigPanelViewModel(
            load_password=True,
            on_change=lambda: self._on_input_change("database"),
            on_loading_change=self._on_panel_loading_change,
        )

    def _init_token_controls(self):  # pragma: no cover
        # NOTE(lazy): tushare_panel 改为声明式组件,VM 由消费方实例化。
        # ceiling: Phase 4 OnboardingWizard 声明式重写. upgrade: Task 4.x OnboardingWizard 声明式重写.
        self.tushare_vm = TushareConfigPanelViewModel(
            on_loading_change=self._on_panel_loading_change,
            show_internal_loading=False,
        )
        self.tushare_panel = TushareConfigPanel(
            vm=self.tushare_vm,
            compact=True,
            show_save_button=False,
            show_register_link=True,
        )

    def _init_cloud_ai_controls(self):  # pragma: no cover
        # NOTE(lazy): VM 由消费方实例化（声明式 LLMConfigPanel 接收 vm 参数，经 use_viewmodel(vm=vm) 消费）。
        # ceiling: Phase 4 OnboardingWizard 声明式重写. upgrade: Task 4.x OnboardingWizard 声明式重写.
        self.llm_vm = LLMConfigPanelViewModel(
            on_test_connection=self._on_llm_test_connection,
            on_loading_change=self._on_panel_loading_change,
        )
        self.llm_config_panel = LLMConfigPanel(
            vm=self.llm_vm,
            compact=True,
            show_save_button=False,
        )

    def _init_local_model_controls(self):  # pragma: no cover
        # NOTE(lazy): VM 由消费方实例化（声明式 LocalModelConfigPanel 接收 vm 参数，经 use_viewmodel(vm=vm) 消费）。
        # ceiling: Phase 4 OnboardingWizard 声明式重写. upgrade: Task 4.x OnboardingWizard 声明式重写.
        self.local_model_vm = LocalModelConfigPanelViewModel(
            on_verify_model=self._on_verify_local_model,
            on_change=lambda: self._on_input_change("local_model"),
            on_loading_change=self._on_panel_loading_change,
            show_internal_loading=False,
        )
        self.local_model_panel = LocalModelConfigPanel(
            vm=self.local_model_vm,
            show_save_button=False,
            compact=True,
            show_internal_loading=False,
        )

    def _on_panel_loading_change(self, loading: bool):  # pragma: no cover
        """通用面板加载状态回调 - 仅控制遮罩显隐"""
        self._panel_loading = loading
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
        self.btn_quick_sync = ft.Button(  # pragma: no cover
            I18n.get("wizard_sync_quick"),  # pragma: no cover
            icon=ft.Icons.FLASH_ON,  # pragma: no cover
            style=AppStyles.accent_button(),  # pragma: no cover
        )  # pragma: no cover
        self.btn_full_sync = ft.Button(  # pragma: no cover
            I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS),  # pragma: no cover
            icon=ft.Icons.CLOUD_SYNC,  # pragma: no cover
            style=AppStyles.primary_button(),  # pragma: no cover
        )  # pragma: no cover
        self.btn_sync_later = ft.TextButton(  # pragma: no cover
            I18n.get("wizard_btn_sync_later"),  # pragma: no cover
            icon=ft.Icons.SCHEDULE,  # pragma: no cover
            on_click=lambda e: self.app_page.run_task(self.vm.skip_sync),  # pragma: no cover
        )  # pragma: no cover
        self.btn_cancel_sync = ft.Button(  # pragma: no cover
            I18n.get("wizard_btn_cancel"),  # pragma: no cover
            icon=ft.Icons.CANCEL,  # pragma: no cover
            color=AppColors.ERROR,  # pragma: no cover
            visible=False,  # pragma: no cover
        )  # pragma: no cover
        self.btn_quick_sync.on_click = lambda e: self.app_page.run_task(self._on_quick_sync)  # pragma: no cover
        self.btn_full_sync.on_click = lambda e: self.app_page.run_task(self._on_full_sync)  # pragma: no cover
        self.btn_cancel_sync.on_click = lambda e: self.app_page.run_task(
            self._on_cancel_sync_wizard
        )  # pragma: no cover

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
            padding=ft.Padding.only(top=8),
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
                ft.Button(
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
                ft.Button(
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
            on_select=self._on_language_change_wizard,  # pragma: no cover
        )  # pragma: no cover

        language_container = ft.Container(  # pragma: no cover
            content=self.wizard_language_dropdown,  # pragma: no cover
            alignment=ft.Alignment.CENTER,  # pragma: no cover
        )  # pragma: no cover

        rocket_container = ft.Container(  # pragma: no cover
            content=ft.Icon(ft.Icons.ROCKET_LAUNCH, size=72, color=AppColors.PRIMARY),  # pragma: no cover
            width=120,  # pragma: no cover
            height=120,  # pragma: no cover
            border_radius=60,  # pragma: no cover
            bgcolor=ft.Colors.with_opacity(0.1, AppColors.PRIMARY),  # pragma: no cover
            alignment=ft.Alignment.CENTER,  # pragma: no cover
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
                begin=ft.Alignment.CENTER_LEFT,  # pragma: no cover
                end=ft.Alignment.CENTER_RIGHT,  # pragma: no cover
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
                    required=True,
                    gradient_index=0,
                ),
                self._create_overview_card(
                    icon=ft.Icons.KEY,
                    color=AppColors.PRIMARY,
                    title_key="wizard_overview_token_title",
                    desc_key="wizard_overview_token_desc",
                    required=True,
                    gradient_index=1,
                ),
                self._create_overview_card(
                    icon=ft.Icons.CLOUD,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_cloud_ai_title",
                    desc_key="wizard_overview_cloud_ai_desc",
                    required=True,
                    gradient_index=2,
                ),
                self._create_overview_card(
                    icon=ft.Icons.PSYCHOLOGY,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_local_model_title",
                    desc_key="wizard_overview_local_model_desc",
                    required=False,
                    gradient_index=3,
                ),
                self._create_overview_card(
                    icon=ft.Icons.CLOUD_SYNC,
                    color=AppColors.PRIMARY,
                    title_key="wizard_overview_sync_title",
                    desc_key="wizard_overview_sync_desc",
                    required=False,
                    gradient_index=4,
                ),
                self._create_overview_card(
                    icon=ft.Icons.SCHEDULE,
                    color=AppColors.ACCENT,
                    title_key="wizard_overview_schedule_title",
                    desc_key="wizard_overview_schedule_desc",
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
            padding=ft.Padding.symmetric(horizontal=6, vertical=2),
        )

        icon_with_badge = ft.Stack(
            [
                ft.Container(
                    content=ft.Icon(icon, size=32, color=color),
                    width=52,
                    height=52,
                    border_radius=14,
                    bgcolor=ft.Colors.with_opacity(0.12, color),
                    alignment=ft.Alignment.CENTER,
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
            border=ft.Border.all(1, ft.Colors.with_opacity(0.15, AppColors.PRIMARY)),
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
            e.control.border = ft.Border.all(1.5, ft.Colors.with_opacity(0.5, color))
            e.control.shadow = ft.BoxShadow(
                spread_radius=1,
                blur_radius=12,
                color=ft.Colors.with_opacity(0.2, color),
                offset=ft.Offset(0, 3),
            )
        else:
            e.control.border = ft.Border.all(1, ft.Colors.with_opacity(0.15, AppColors.PRIMARY))
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
                DatabaseConfigPanel(  # pragma: no cover
                    vm=self.database_vm,  # pragma: no cover
                    compact=True,  # pragma: no cover
                    show_save_button=False,  # pragma: no cover
                    show_header=False,  # pragma: no cover
                ),  # pragma: no cover
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
        UILogger.log_action("OnboardingWizard", "Click", f"next_step={self.vm.current_step}")
        await self.vm.next_step()

    async def _prev_step(self):  # pragma: no cover
        UILogger.log_action("OnboardingWizard", "Click", f"prev_step={self.vm.current_step}")
        await self.vm.prev_step()

    async def _skip_step(self):  # pragma: no cover
        UILogger.log_action("OnboardingWizard", "Click", f"skip_step={self.vm.current_step}")
        await self.vm.skip_step()

    async def _on_quick_sync(self):  # pragma: no cover
        UILogger.log_action("OnboardingWizard", "Click", "btn_quick_sync")
        await self.vm.start_sync(quick=True)

    async def _on_full_sync(self):  # pragma: no cover
        UILogger.log_action("OnboardingWizard", "Click", "btn_full_sync")
        await self.vm.start_sync(quick=False)

    async def _on_cancel_sync_wizard(self):  # pragma: no cover
        UILogger.log_action("OnboardingWizard", "Click", "btn_cancel_sync")
        await self.vm.cancel_sync()

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
        # Fire-and-forget: dispose() is synchronous and fast; Flet guarantees
        # the page is still alive during will_unmount, so run_task is safe here.
        if self.app_page:
            self.app_page.run_task(self._cleanup_vm)

    async def _cleanup_vm(self):  # pragma: no cover
        if self.vm.sync_in_progress:
            await self.vm.cancel_sync()
        # 清理未提交的验证状态
        from services.local_model_manager import LocalModelManager

        LocalModelManager.cancel_verification_if_active()
        self.database_vm.dispose()
        self.tushare_vm.dispose()
        self.llm_vm.dispose()
        self.local_model_vm.dispose()
        self.vm.dispose()

    def _on_locale_change(self):
        try:
            if hasattr(self, "header_title"):
                self.header_title.value = I18n.get("wizard_welcome_title")
            if hasattr(self, "header_desc"):
                self.header_desc.value = I18n.get("wizard_welcome_desc_with_time")
            if hasattr(self, "gradient_guide_text"):
                self.gradient_guide_text.value = I18n.get("wizard_welcome_guide")
            self.sync_status.value = I18n.get("wizard_status_ready")
            self.btn_quick_sync.content = I18n.get("wizard_sync_quick")
            self.btn_full_sync.content = I18n.get("wizard_sync_full").format(years=DEFAULT_SYNC_YEARS)
            self.btn_cancel_sync.content = I18n.get("wizard_btn_cancel")
            if hasattr(self, "btn_sync_later"):
                self.btn_sync_later.content = I18n.get("wizard_btn_sync_later")
            self.schedule_enabled.label = I18n.get("wizard_schedule_label")
            self.schedule_time.label = I18n.get("wizard_schedule_time_label")
            self.loading_overlay_text.value = I18n.get("wizard_validating")

            if hasattr(self, "wizard_language_dropdown"):
                self.wizard_language_dropdown.label = I18n.get_language_label()
                self.wizard_language_dropdown.tooltip = I18n.get_language_label()
                refresh_dropdown_options(
                    self.wizard_language_dropdown,
                    [ft.dropdown.Option(code, name) for code, name in I18n.get_language_options()],
                )

            # 重建步骤内容（含子面板），保证外部触发的语言切换也能完整刷新
            self._rebuild_steps_after_locale_change()
            self._safe_update()
        except Exception as e:
            logger.warning("[OnboardingWizard] _on_locale_change failed: %s", e, exc_info=True)

    def _rebuild_steps_after_locale_change(self):
        """语言切换后刷新 steps_content。

        不重建子面板实例：构造函数会触发 keyring IO（如 DatabaseConfigPanel._load_config），
        违反 §5.8 纯 UI 规范。所有子面板（DatabaseConfigPanel / TushareConfigPanel /
        LLMConfigPanel / LocalModelConfigPanel）均已重写为声明式组件，通过
        ``ft.use_state(I18n.get_observable_state)`` 自动重渲染，无需级联调用
        ``_on_locale_change``。schedule 控件不重建，用户输入自然保留。
        """
        # 重建 steps_content 以更新步骤标题/描述等纯 UI 文本（子面板实例保持不变）
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

    def _on_language_change_wizard(self, e):  # pragma: no cover
        """Handle language change in Onboarding Wizard"""
        if self.app_page:
            self.app_page.run_task(self._do_language_change_wizard_async)

    async def _do_language_change_wizard_async(self):  # pragma: no cover
        try:
            new_locale = self.wizard_language_dropdown.value
            success = await ThreadPoolManager().run_async(TaskType.IO, ConfigHandler.set_locale, new_locale)
            if not success:
                self.wizard_language_dropdown.value = I18n.current_locale()
                logger.warning("[OnboardingWizard] Failed to persist locale: %s", new_locale)
                self._safe_update()
                return
            # I18n.set_locale 会自动触发 _on_locale_change → _rebuild_steps_after_locale_change
            I18n.set_locale(new_locale)

            if self.app_page and getattr(self.app_page, "locale_configuration", None):
                try:
                    normalized = I18n.current_locale()
                    parts = normalized.split("_")
                    lang = parts[0]
                    country = parts[1] if len(parts) > 1 else None
                    self.app_page.locale_configuration.current_locale = ft.Locale(lang, country)
                    self.app_page.update()
                except Exception as ex:
                    logger.debug("[OnboardingWizard] Failed to update page locale configuration: %s", ex, exc_info=True)
        except Exception as ex:
            logger.error("[OnboardingWizard] Language change failed: %s", DataSanitizer.sanitize_error(ex))
            self._show_snack(I18n.get("sys_snack_save_err"), AppColors.ERROR)

    def _show_snack(self, msg: str, color: str):  # pragma: no cover
        if self.app_page:
            snack = ft.SnackBar(ft.Text(msg), bgcolor=color)
            self.app_page.show_dialog(snack)

    def _safe_update(self):  # pragma: no cover
        try:
            if self.page:
                self.update()
        except Exception as exc:
            logger.debug("[OnboardingWizard] UI update skipped: %s", exc, exc_info=True)

    def _show_loading_overlay(self, show: bool):  # pragma: no cover
        self.loading_overlay.visible = show
        self._update_navigation_buttons()

    def handle_resize(self, width: float = 0, height: float = 0) -> None:
        """窗口 resize 通知。当前布局自适应，无需响应式调整。"""
        # No responsive adjustment needed
