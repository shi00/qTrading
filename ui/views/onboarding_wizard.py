import flet as ft
from utils.config_handler import ConfigHandler
from data.tushare_client import TushareClient
from data.data_processor import DataProcessor
from ui.i18n import I18n

import asyncio
import traceback
import logging

logger = logging.getLogger(__name__)

class OnboardingWizard(ft.Container):
    """
    Step-by-step onboarding wizard for first-time users.
    Steps:
    1. Welcome & Token Configuration
    2. AI Configuration
    3. Initial Data Sync
    4. Set up Daily Scheduled Task (optional)
    5. Complete
    """
    
    def __init__(self, page, on_complete=None):
        super().__init__()
        self.app_page = page
        self.on_complete = on_complete  # Callback when wizard completes
        self.current_step = 0
        self.expand = True
        
        # Add background color to match theme
        from ui.theme import AppColors
        self.bgcolor = AppColors.BACKGROUND
        
        # Step 1: Token input
        self.token_input = ft.TextField(
            label=I18n.get("wizard_token_label"),
            password=True,
            can_reveal_password=True,
            width=400,
            hint_text=I18n.get("wizard_token_hint"),
            on_submit=self._verify_token,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY)
        )
        self.token_status = ft.Text("", size=12, color=AppColors.TEXT_SECONDARY)
        
        # Step 2: AI Config
        self.ai_api_key_input = ft.TextField(
            label=I18n.get("wizard_ai_key_label"),
            password=True,
            can_reveal_password=True,
            width=400,
            hint_text="sk-...",
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY)
        )
        self.ai_base_url_input = ft.TextField(
            label=I18n.get("settings_ai_base_url_label"),
            value="https://api.deepseek.com",
            width=400,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY)
        )
        self.ai_model_dropdown = ft.Dropdown(
            label=I18n.get("wizard_ai_model_label"),
            value="deepseek-chat",
            width=200,
            border_color=AppColors.PRIMARY,
            label_style=ft.TextStyle(color=AppColors.PRIMARY),
            options=[
                ft.dropdown.Option("deepseek-chat", "DeepSeek-V3 (deepseek-chat)"),
                ft.dropdown.Option("deepseek-reasoner", "DeepSeek-R1 (deepseek-reasoner)"),
                ft.dropdown.Option("moonshot-v1-8k", "Moonshot Kimi"),
                ft.dropdown.Option("qwen2.5-max", "Alibaba Qwen"),
                ft.dropdown.Option("gpt-4o", "OpenAI GPT-4o"),
            ]
        )
        
        from utils.config_handler import DEFAULT_AI_PROMPT
        self.ai_prompt_input = ft.TextField(
             label=I18n.get("wizard_ai_prompt_label"),
             value=DEFAULT_AI_PROMPT,
             multiline=True,
             min_lines=3,
             max_lines=8,
             text_size=12,
             border_color=AppColors.PRIMARY,
             label_style=ft.TextStyle(color=AppColors.PRIMARY),
             hint_text=I18n.get("wizard_ai_prompt_hint")
        )
        
        self.ai_status = ft.Text("", size=12, color=AppColors.TEXT_SECONDARY)

        # Step 3: Sync progress
        self.sync_progress = ft.ProgressBar(width=400, value=0, color=AppColors.ACCENT, bgcolor=AppColors.BORDER)
        self.sync_status = ft.Text(I18n.get("wizard_status_ready"), size=12, color=AppColors.TEXT_SECONDARY)
        
        # Step 4: Schedule options
        self.schedule_enabled = ft.Checkbox(
            label=I18n.get("wizard_schedule_label"),
            value=True,
            active_color=AppColors.PRIMARY,
        )
        
        # Build wizard UI
        self.steps_content = [
            self._build_step1(),
            self._build_step_ai(),
            self._build_step2(),
            self._build_step3(),
            self._build_step4(),
        ]
        
        self.step_container = ft.Container(
            content=self.steps_content[0],
            expand=True,
        )
        
        # Progress indicator
        self.step_indicators = ft.Row(
            self._build_step_indicators(),
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        
        self.content = ft.Column(
            scroll=ft.ScrollMode.AUTO,
            controls=[
                ft.Container(height=40), # More top spacing
                ft.Text(I18n.get("wizard_welcome_title"), size=32, weight=ft.FontWeight.BOLD, 
                       color=AppColors.PRIMARY, text_align=ft.TextAlign.CENTER),
                ft.Text(I18n.get("wizard_welcome_desc"), size=16, color=AppColors.TEXT_SECONDARY,
                       text_align=ft.TextAlign.CENTER),
                ft.Container(height=30),
                self.step_indicators,
                ft.Divider(height=40, color=ft.Colors.TRANSPARENT),
                self.step_container,
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            expand=True,
        )

    def _build_step_indicators(self):
        """Build the list of controls for step indicators including arrows"""
        from ui.theme import AppColors
        labels = [
            I18n.get("wizard_step_label_token"), 
            I18n.get("wizard_step_label_ai"), 
            I18n.get("wizard_step_label_sync"), 
            I18n.get("wizard_step_label_schedule"), 
            I18n.get("wizard_step_label_done")
        ]
        controls = []
        for i in range(5):
            # Add step indicator
            controls.append(self._create_step_indicator(i, labels))
            
            # Add arrow connector if not the last step
            if i < 4:
                is_completed = i < self.current_step
                color = AppColors.SUCCESS if is_completed else AppColors.BORDER
                controls.append(
                    ft.Container(
                        content=ft.Icon(ft.Icons.ARROW_RIGHT_ALT, color=color, size=24),
                        padding=ft.padding.symmetric(horizontal=10),
                        offset=ft.transform.Offset(0, -0.2) 
                    )
                )
        return controls

    def _create_step_indicator(self, index, labels):
        """Create step indicator dot with step number"""
        from ui.theme import AppColors

        is_active = index == self.current_step
        is_completed = index < self.current_step
        
        if is_completed:
            color = AppColors.SUCCESS
            icon = ft.Icons.CHECK_CIRCLE
        elif is_active:
            color = AppColors.PRIMARY
            icon = ft.Icons.RADIO_BUTTON_CHECKED
        else:
            color = AppColors.BORDER 
            icon = ft.Icons.RADIO_BUTTON_UNCHECKED
        
        # Text color logic
        text_color = color if (is_active or is_completed) else AppColors.TEXT_HINT
        
        font_weight = ft.FontWeight.BOLD if is_active else ft.FontWeight.NORMAL
        
        return ft.Column(
            [
                ft.Text(I18n.get("wizard_step_prefix").format(index=index + 1), size=12, color=text_color, weight=font_weight),
                ft.Icon(icon, color=color, size=32), 
                ft.Text(labels[index], size=13, color=text_color, weight=font_weight),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=4,
            width=90,
        )

    def _update_indicators(self):
        """Update step indicators"""
        self.step_indicators.controls = self._build_step_indicators()
        self.step_container.content = self.steps_content[self.current_step]
        self.update()

    def _build_step1(self):
        """Step 1: Token Configuration"""
        from ui.theme import AppColors, AppStyles
        return ft.Column(
            [
                ft.Icon(ft.Icons.KEY, size=64, color=AppColors.PRIMARY),
                ft.Text(I18n.get("wizard_step1_title"), size=24, weight=ft.FontWeight.W_500, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step1_desc"),
                    size=14, color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=30),
                self.token_input,
                self.token_status,
                ft.Container(height=30),
                ft.Row([
                    ft.ElevatedButton(
                        I18n.get("wizard_btn_verify_next"), 
                        icon=ft.Icons.ARROW_FORWARD, 
                        on_click=self._verify_token,
                        style=AppStyles.primary_button()
                    ),
                ], alignment=ft.MainAxisAlignment.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _handle_quick_sync(self, e):
        await self._start_sync(quick=True)

    def _build_step_ai(self):
        """Step 2: AI Configuration"""
        from ui.theme import AppColors, AppStyles
        return ft.Column(
            [
                ft.Icon(ft.Icons.SMART_TOY, size=64, color=AppColors.PRIMARY),
                ft.Text(I18n.get("wizard_step2_title"), size=24, weight=ft.FontWeight.W_500, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step2_desc"),
                    size=14, color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=30),
                self.ai_base_url_input,
                self.ai_api_key_input,
                self.ai_model_dropdown,
                ft.Container(height=10),
                ft.ExpansionTile(
                    title=ft.Text(I18n.get("wizard_ai_advanced"), size=14, color=AppColors.PRIMARY),
                    subtitle=ft.Text(I18n.get("wizard_ai_advanced_subtitle"), size=12, color=AppColors.TEXT_SECONDARY),
                    controls=[self.ai_prompt_input],
                    collapsed_text_color=AppColors.TEXT_SECONDARY,
                    text_color=AppColors.PRIMARY,
                ),
                self.ai_status,
                ft.Container(height=30),
                ft.Row([
                    ft.ElevatedButton(
                        I18n.get("wizard_btn_verify_next"), 
                        icon=ft.Icons.ARROW_FORWARD, 
                        on_click=self._verify_ai_config,
                        style=AppStyles.primary_button()
                    ),
                    ft.TextButton(
                        I18n.get("wizard_btn_skip"), 
                        on_click=self._skip_ai_config,
                        style=ft.ButtonStyle(color=AppColors.TEXT_HINT)
                    ),
                ], alignment=ft.MainAxisAlignment.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )



    def _build_step2(self):
        """Step 3: Data Sync"""
        from ui.theme import AppColors, AppStyles
        self.btn_quick_sync = ft.ElevatedButton(
            I18n.get("wizard_sync_quick"), 
            icon=ft.Icons.FLASH_ON,
            on_click=self._handle_quick_sync,
            style=AppStyles.accent_button() # Use accent color for quick action
        )
        self.btn_full_sync = ft.ElevatedButton(
            I18n.get("wizard_sync_full"), 
            icon=ft.Icons.CLOUD_SYNC,
            on_click=self._handle_full_sync,
            style=AppStyles.primary_button()
        )
        self.btn_cancel_sync = ft.ElevatedButton(
            I18n.get("wizard_btn_cancel"), 
            icon=ft.Icons.CANCEL,
            color=AppColors.ERROR,
            visible=False,
            on_click=self._handle_cancel_sync
        )
        
        return ft.Column(
            [
                ft.Icon(ft.Icons.CLOUD_DOWNLOAD, size=64, color=AppColors.PRIMARY),
                ft.Text(I18n.get("wizard_step3_title"), size=24, weight=ft.FontWeight.W_500, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step3_desc"),
                    size=14, color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=30),
                ft.Row(
                    [self.btn_quick_sync, self.btn_full_sync, self.btn_cancel_sync],
                    alignment=ft.MainAxisAlignment.CENTER,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(height=30),
                self.sync_progress,
                self.sync_status,
                ft.Container(height=10),
                ft.TextButton(I18n.get("wizard_btn_skip_step"), on_click=self._skip_sync, style=ft.ButtonStyle(color=AppColors.TEXT_HINT)),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_step3(self):
        """Step 4: Scheduled Task"""
        from ui.theme import AppColors, AppStyles
        return ft.Column(
            [
                ft.Icon(ft.Icons.SCHEDULE, size=64, color=AppColors.PRIMARY),
                ft.Text(I18n.get("wizard_step4_title"), size=24, weight=ft.FontWeight.W_500, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step4_desc"),
                    size=14, color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=30),
                ft.Row(
                    [self.schedule_enabled],
                    alignment=ft.MainAxisAlignment.CENTER
                ),
                ft.Text(I18n.get("wizard_schedule_note"), size=12, color=AppColors.TEXT_HINT),
                ft.Container(height=30),
                ft.Row([
                    ft.ElevatedButton(
                        I18n.get("wizard_btn_finish"), 
                        icon=ft.Icons.CHECK, 
                        on_click=self._finish_setup,
                        style=AppStyles.primary_button()
                    ),
                ], alignment=ft.MainAxisAlignment.CENTER),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_step4(self):
        """Step 5: Complete"""
        from ui.theme import AppColors, AppStyles
        return ft.Column(
            [
                ft.Icon(ft.Icons.CELEBRATION, size=80, color=AppColors.SUCCESS),
                ft.Text(I18n.get("wizard_step5_title"), size=32, weight=ft.FontWeight.BOLD, color=AppColors.TEXT_PRIMARY),
                ft.Container(height=10),
                ft.Text(
                    I18n.get("wizard_step5_desc"),
                    size=16, color=AppColors.TEXT_SECONDARY,
                    text_align=ft.TextAlign.CENTER,
                ),
                ft.Container(height=40),
                ft.ElevatedButton(
                    I18n.get("wizard_btn_start"), 
                    icon=ft.Icons.ROCKET_LAUNCH,
                    style=ft.ButtonStyle(bgcolor=AppColors.SUCCESS, color=AppColors.TEXT_ON_PRIMARY, icon_color=AppColors.TEXT_ON_PRIMARY, padding=20),
                    on_click=self._complete_wizard,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )

    async def _verify_token(self, e):
        """Verify token and proceed to next step"""
        from ui.theme import AppColors
        token = self.token_input.value.strip()
        if not token:
            self.token_status.value = I18n.get("wizard_err_token_empty")
            self.token_status.color = AppColors.ERROR
            self.update()
            return
        
        self.token_status.value = I18n.get("wizard_verifying")
        self.token_status.color = AppColors.WARNING
        self.update()
        
        try:
            # Use TushareClient for verification (ensures proxy/config compatibility)
            from data.tushare_client import TushareClient
            
            # Temporary client for this token
            client = TushareClient(token=token)
            
            # Verify by fetching calendar (lightweight)
            # TushareClient expects YYYYMMDD string dates
            dates = client.get_trade_dates(start_date='20250101', end_date='20250101')
            
            # Save token if successful
            ConfigHandler.save_config({"ts_token": token, "onboarding_complete": False})
            
            self.token_status.value = I18n.get("wizard_msg_token_success")
            self.token_status.color = AppColors.SUCCESS
            self.update()
            
            # Move to next step
            await self._next_step()
            
        except Exception as ex:
            self.token_status.value = I18n.get("wizard_err_verify_failed").format(error=str(ex)[:40])
            self.token_status.color = AppColors.ERROR
            self.update()

    async def _next_step(self):
        """Move to next step"""
        self.current_step += 1
        self._update_indicators()

    async def _start_sync(self, quick=False):
        """Start data sync"""
        # Disable sync buttons, show cancel button
        self.btn_quick_sync.disabled = True
        self.btn_full_sync.disabled = True
        self.btn_cancel_sync.visible = True
        self.btn_cancel_sync.disabled = False
        
        self.sync_status.value = I18n.get("wizard_status_init")
        self.sync_status.color = ft.Colors.BLUE
        self.sync_progress.value = None  # Indeterminate
        self.update()
        
        # Initialize cancel event
        self.cancel_event = asyncio.Event()
        self.cancel_event.clear()
        
        try:
            processor = DataProcessor()
            await processor.init_data()
            
            if quick:
                # Quick sync
                self.sync_status.value = I18n.get("wizard_status_today")
                self.update()
                await processor.sync_daily_market_snapshot()
                self.sync_status.value = I18n.get("wizard_msg_today_done")
                self.sync_status.color = ft.Colors.GREEN
            else:
                # Full sync
                self.sync_status.value = I18n.get("wizard_status_stock_list")
                self.update()
                await processor.sync_stock_basic()
                
                if self.cancel_event.is_set():
                    raise asyncio.CancelledError("User cancelled")

                self.sync_status.value = I18n.get("wizard_status_history")
                self.sync_progress.value = 0
                self.update()
                
                days = 750

    # ... (skipping context)

                self.sync_status.value = I18n.get("wizard_status_history")
                self.sync_progress.value = 0
                self.update()
                
                days = 750
                _last_update_ts = [0]
                def update_progress(current, total, msg):
                    import time
                    now = time.time()
                    if current == total or (now - _last_update_ts[0] > 0.1):
                        self.sync_progress.value = current / total
                        self.sync_status.value = f"{msg} ({int(current/total*100)}%)"
                        self._safe_update()
                        _last_update_ts[0] = now
                
                # Ensure DB is initialized before sync
                await processor.init_data()
                
                # Pass cancel_event to processor
                await processor.sync_historical_data(
                    days=days, 
                    progress_callback=update_progress,
                    cancel_event=self.cancel_event
                )
                
                if self.cancel_event.is_set():
                     self.sync_status.value = I18n.get("wizard_msg_sync_cancelled")
                     self.sync_status.color = ft.Colors.RED
                     self.sync_progress.value = 0
                else:
                    self.sync_status.value = I18n.get("wizard_msg_history_done")
                    self.sync_status.color = ft.Colors.GREEN
                    self.sync_progress.value = 1

            self.update()
            
            if not self.cancel_event.is_set():
                await asyncio.sleep(1)
                await self._next_step()
                
        except asyncio.CancelledError:
            logger.info("Sync task was cancelled by user")
            self.sync_status.value = I18n.get("wizard_msg_sync_cancelled")
            self.sync_status.color = ft.Colors.ORANGE
        except Exception as ex:
            logger.error(f"Sync error: {traceback.format_exc()}")
            self.sync_status.value = I18n.get("wizard_msg_sync_failed").format(error=str(ex)[:40])
            self.sync_status.color = ft.Colors.RED
            self.sync_progress.value = 0
            self.update()
            
        finally:
            # Always re-enable buttons and hide cancel
            self.btn_quick_sync.disabled = False
            self.btn_full_sync.disabled = False
            self.btn_cancel_sync.visible = False
            self.update()

    async def _skip_sync(self, e):
        """Skip sync step"""
        await self._next_step()

    async def _finish_setup(self, e):
        """Finish setup and save preferences"""
        # Save schedule preference
        config = {
            "onboarding_complete": True,
            "auto_update_enabled": self.schedule_enabled.value,
            "auto_update_time": "16:30",
        }
        ConfigHandler.save_config(config)
        
        # Move to completion step
        await self._next_step()

    async def _complete_wizard(self, e):
        """Complete wizard and call callback"""
        if self.on_complete:
            self.on_complete()

    async def _verify_ai_config(self, e):
        from ui.theme import AppColors
        api_key = self.ai_api_key_input.value.strip()
        base_url = self.ai_base_url_input.value.strip()
        model = self.ai_model_dropdown.value
        
        if not api_key:
             self.ai_status.value = I18n.get("wizard_err_ai_key")
             self.ai_status.color = AppColors.ERROR
             self.update()
             return

        # Save temporarily
        ConfigHandler.save_ai_config(api_key, base_url, model)
        ConfigHandler.save_ai_system_prompt(self.ai_prompt_input.value)
        
        self.ai_status.value = I18n.get("wizard_ai_connecting")
        self.ai_status.color = AppColors.WARNING
        self.update()
        
        try:
            from data.ai_client import AIClient
            client = AIClient()
            await client.reload_config()
            success = await client.verify_connection()
            
            if success:
                self.ai_status.value = I18n.get("wizard_ai_success")
                self.ai_status.color = AppColors.SUCCESS
                self.update()
                await asyncio.sleep(0.5)
                await self._next_step()
            else:
                 self.ai_status.value = I18n.get("wizard_ai_failed")
                 self.ai_status.color = AppColors.ERROR
                 self.update()
                 
        except Exception as ex:
            self.ai_status.value = I18n.get("wizard_ai_error").format(error=str(ex)[:30])
            self.ai_status.color = AppColors.ERROR
            self.update()

    async def _skip_ai_config(self, e):
        """Skip AI config"""
        await self._next_step()

    async def _handle_full_sync(self, e):
        await self._start_sync(quick=False)

    async def _handle_cancel_sync(self, e):
        """Cancel the running sync task"""
        from ui.theme import AppColors
        if hasattr(self, 'cancel_event'):
            self.cancel_event.set()
            self.sync_status.value = I18n.get("wizard_status_cancelling") # Keep simplified status directly or use key? "正在取消..." -> wizard_msg_sync_cancelled (close enough or need new key?)
            # Actually I should use I18n for "Cancelling..."
            # Let's use hardcoded "Cancelling..." for now as I missed this key or stick to English "Cancelling..."
            # I'll use "Cancelling..." -> "正在取消..."
            # I will use I18n.get("wizard_msg_sync_cancelled") but that says "Cancelled", not "Cancelling".
            # I'll just leave "正在取消..." hardcoded? No, I should use key.
            # I'll add a quick key if I can or just use "Cancelling..." in English?
            # I'll reuse "wizard_msg_sync_cancelled" for now as it conveys the idea, or just "..."
            # Wait, I can't add key now easily without interrupting flow.
            # I'll leave "正在取消..." as hardcoded since it's transient state.
            # Or better: I'll Replace with: I18n.get("common_cancel") + "..." ? -> "Cancel..." / "取消..."
            
            self.sync_status.value = I18n.get("common_cancelling")
            self.sync_status.color = AppColors.ERROR
            self.btn_cancel_sync.disabled = True
            self.update()
