import flet as ft
import asyncio
import time
import threading
from ui.theme import AppColors

class ToastManager:
    """
    Manages floating toast notifications properly stacked.
    Proposal A Implementation.
    """
    def __init__(self, page: ft.Page):
        self.page = page
        self.lock = threading.Lock()
        self.toasts_stack = ft.Column(
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        )
        # Container for the stack, positioned absolute
        self.container = ft.Container(
            content=self.toasts_stack,
            right=20,
            bottom=20,
            width=320,
            # No height, grows upwards
        )
        
        # Add to page overlay
        if self.page:
            self.page.overlay.append(self.container)
            self.page.update()

    def show(self, message, type="info", duration=10):
        """
        Show a toast.
        type: 'info', 'success', 'error', 'warning'
        """
        if not self.page:
            return

        # Determine colors and icon
        if type == "success":
            color = ft.Colors.GREEN
            icon = ft.Icons.CHECK_CIRCLE
            bg_color = ft.Colors.GREEN_50
        elif type == "error":
            color = ft.Colors.RED
            icon = ft.Icons.ERROR
            bg_color = ft.Colors.RED_50
        elif type == "warning":
            color = ft.Colors.ORANGE
            icon = ft.Icons.WARNING
            bg_color = ft.Colors.ORANGE_50
        else: # info
            color = ft.Colors.BLUE
            icon = ft.Icons.INFO
            bg_color = ft.Colors.BLUE_50

        # Create Toast Control
        toast_card = ToastCard(
            message=message,
            icon=icon,
            color=color,
            bg_color=bg_color,
            duration=duration,
            on_dismiss=self._remove_toast
        )
        
        with self.lock:
            # Add to stack
            # self.toasts_stack.controls.insert(0, toast_card) 
            self.toasts_stack.controls.append(toast_card)
            
            # Limit max toasts (e.g. 5)
            if len(self.toasts_stack.controls) > 5:
                 removed = self.toasts_stack.controls.pop(0) # Remove oldest (top)
                 
            # Update the stack control directly to register the new child
            try:
                self.toasts_stack.update()
                self.container.update() 
            except Exception as e:
                print(f"Toast update failed: {e}")
        
        # Start timer for this toast
        # Use page.run_task to be thread-safe from sync handlers
        self.page.run_task(toast_card.start_timer)

    def _remove_toast(self, toast):
        with self.lock:
            if toast in self.toasts_stack.controls:
                self.toasts_stack.controls.remove(toast)
                try:
                    self.toasts_stack.update()
                    self.container.update()
                except Exception:
                    pass

class ToastCard(ft.Container):
    def __init__(self, message, icon, color, bg_color, duration, on_dismiss):
        super().__init__()
        self.duration = duration
        self.on_dismiss = on_dismiss
        self.is_hovered = False
        self.remaining = duration
        self.start_time = time.time()
        
        # UI
        self.content = ft.Row([
            ft.Icon(icon, color=color, size=24),
            ft.Column([
                ft.Text(message, size=14, color=ft.Colors.BLACK87, width=230, no_wrap=False),
            ], spacing=2, alignment=ft.MainAxisAlignment.CENTER),
            ft.IconButton(
                ft.Icons.CLOSE, 
                icon_size=16, 
                icon_color=ft.Colors.GREY,
                on_click=self._handle_dismiss_click
            )
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.START)
        
        self.padding = 12
        self.bgcolor = ft.Colors.WHITE
        self.border_left = ft.BorderSide(4, color)
        self.border_radius = 8
        self.shadow = ft.BoxShadow(
            spread_radius=1,
            blur_radius=10,
            color=ft.Colors.with_opacity(0.1, ft.Colors.BLACK),
            offset=ft.Offset(0, 4)
        )
        # Animation
        self.offset = ft.transform.Offset(1.1, 0) # Start off-screen right
        self.animate_offset = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC)
        self.animate_opacity = ft.animation.Animation(300, ft.AnimationCurve.EASE_IN)
        self.opacity = 0
        
        self.on_hover = self._on_hover
        
    def did_mount(self):
        # Trigger enter animation
        self.offset = ft.transform.Offset(0, 0)
        self.opacity = 1
        self.update()
        
    async def start_timer(self):
        # Wait for enter animation
        await asyncio.sleep(0.3)
        
        while self.remaining > 0:
            if not self.is_hovered:
                self.remaining -= 0.1
            await asyncio.sleep(0.1)
            
        await self.dismiss()

    def _on_hover(self, e):
        self.is_hovered = e.data == "true"

    async def _handle_dismiss_click(self, e):
        await self.dismiss()

    async def dismiss(self):
        if not self.page:
             # Already removed from page (e.g. by limit)
             if self.on_dismiss:
                 # Still notify manager to cleanup if needed, 
                 # though typically manager did the removal if it was due to limit.
                 # If it was manual close, we are here.
                 # Just ensure we don't error.
                 pass
             return

        self.opacity = 0
        self.offset = ft.transform.Offset(1.1, 0) # Slide out right
        self.update()
        await asyncio.sleep(0.3)
        if self.on_dismiss:
            self.on_dismiss(self)
