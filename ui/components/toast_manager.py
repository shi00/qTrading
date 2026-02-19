import asyncio
import logging
import threading

import flet as ft
from ui.theme import AppColors
from ui.i18n import I18n

logger = logging.getLogger(__name__)


class ToastManager:
    """
    Manages floating toast notifications with centralized task lifecycle management.

    Uses a TaskGroup pattern for reliable shutdown:
    - All timer tasks tracked in a central set
    - Automatic cleanup via done_callback
    - Clean cancellation without race conditions

    Thread Safety:
    - All _active_tasks operations protected by _lock
    - Safe to call show() from any thread
    """
    MAX_TOAST_COUNT = 5

    def __init__(self, page: ft.Page):
        self.page = page
        self._lock = threading.Lock()
        self._active_tasks: set[asyncio.Task] = set()
        self._is_stopping = False

        self.toasts_stack = ft.Column(
            spacing=10,
            alignment=ft.MainAxisAlignment.END,
            horizontal_alignment=ft.CrossAxisAlignment.END,
        )
        self.container = ft.Container(
            content=self.toasts_stack,
            right=20,
            bottom=20,
            width=360,
            bgcolor=ft.Colors.TRANSPARENT,
        )

        if self.page:
            self.page.overlay.append(self.container)
            self.page.update()

    def _register_task(self, task: asyncio.Task) -> None:
        """Register a task for lifecycle tracking with auto-cleanup (thread-safe)."""
        if not task or not isinstance(task, (asyncio.Task, asyncio.Future)):
            return

        with self._lock:
            self._active_tasks.add(task)

        def on_done(t: asyncio.Task) -> None:
            with self._lock:
                self._active_tasks.discard(t)

        task.add_done_callback(on_done)

    def show(self, message: str, type: str = "info", duration: int = 10) -> None:
        """
        Show a toast notification.

        Args:
            message: Text to display
            type: 'info', 'success', 'error', 'warning'
            duration: Seconds before auto-dismiss
        """
        if not self.page or self._is_stopping:
            return

        # Determine colors and icon (Layer 2 custom colors)
        color_map = {
            "success": (AppColors.SUCCESS, ft.Icons.CHECK_CIRCLE),
            "error": (AppColors.ERROR, ft.Icons.ERROR),
            "warning": (AppColors.WARNING, ft.Icons.WARNING),
            "info": (AppColors.INFO, ft.Icons.INFO),
        }
        color, icon = color_map.get(type, color_map["info"])

        toast_card = ToastCard(
            message=message,
            icon=icon,
            color=color,
            duration=duration,
            on_dismiss=self._remove_toast
        )

        with self._lock:
            self.toasts_stack.controls.append(toast_card)

            # Limit max toasts (remove oldest)
            while len(self.toasts_stack.controls) > self.MAX_TOAST_COUNT:
                removed = self.toasts_stack.controls.pop(0)
                if isinstance(removed, ToastCard):
                    removed.cancel_timer()

            try:
                self.toasts_stack.update()
                self.container.update()
            except Exception as e:
                logger.warning(f"Toast update failed: {e}")

        # Start timer with centralized task tracking
        task = self.page.run_task(self._run_toast_lifecycle, toast_card)
        self._register_task(task)

    async def _run_toast_lifecycle(self, toast_card: "ToastCard") -> None:
        """
        Wrapper for toast lifecycle with proper exception handling.
        Ensures CancelledError is handled cleanly during shutdown.
        """
        try:
            await toast_card.start_timer()
        except asyncio.CancelledError:
            pass  # Normal cancellation during shutdown

    def _remove_toast(self, toast: "ToastCard") -> None:
        """Remove a toast from the stack."""
        with self._lock:
            if toast in self.toasts_stack.controls:
                self.toasts_stack.controls.remove(toast)
                toast.cancel_timer()
                try:
                    self.toasts_stack.update()
                    self.container.update()
                except Exception:
                    pass

    async def stop_all(self) -> None:
        """
        Graceful shutdown: cancel all active toasts and wait for cleanup.

        This method is idempotent and safe to call multiple times.
        Will not leave any pending tasks that could cause "Task destroyed but pending" errors.
        """
        self._is_stopping = True

        # Take snapshot under lock
        with self._lock:
            tasks_snapshot = list(self._active_tasks)

        # Cancel all active tasks
        valid_tasks = [t for t in tasks_snapshot if isinstance(t, (asyncio.Task, asyncio.Future))]
        for task in valid_tasks:
            if not task.done():
                task.cancel()

        # Wait for all cancellations to complete
        if valid_tasks:
            await asyncio.gather(*valid_tasks, return_exceptions=True)

        # Clear UI
        with self._lock:
            for control in self.toasts_stack.controls:
                if isinstance(control, ToastCard):
                    control.cancel_timer()
            self.toasts_stack.controls.clear()


class ToastCard(ft.Container):
    """
    Individual toast notification card with animation and timer.
    Uses semantic tokens for standard colors — auto-updates with theme.
    """
    LONG_TEXT_THRESHOLD = 80
    COLLAPSED_MAX_LINES = 3

    def __init__(self, message, icon, color, duration, on_dismiss):
        super().__init__()
        self.message = message
        self.duration = duration
        self.on_dismiss = on_dismiss
        self.is_hovered = False
        self.is_expanded = False # State for expansion
        self.remaining = duration
        self._is_cancelled = False

        # Threshold for "Long Text"
        self.is_long_text = len(message) > self.LONG_TEXT_THRESHOLD
        
        # Text Component
        self.text_control = ft.Text(
            message, 
            size=14, 
            color=ft.Colors.ON_SURFACE, 
            width=270, 
            max_lines=self.COLLAPSED_MAX_LINES,
            overflow=ft.TextOverflow.ELLIPSIS, 
            tooltip=I18n.get("toast_expand_hint") if self.is_long_text else None
        )

        # Expand Button (only if long text)
        self.expand_btn = None
        if self.is_long_text:
            self.expand_btn = ft.IconButton(
                icon=ft.Icons.KEYBOARD_ARROW_DOWN,
                icon_size=16,
                icon_color=ft.Colors.PRIMARY,
                tooltip=I18n.get("common_expand"),
                on_click=self._toggle_expand,
                style=ft.ButtonStyle(padding=0)
            )

        # Layout Construction
        self.content_col = ft.Column([
            self.text_control,
        ], spacing=2, alignment=ft.MainAxisAlignment.CENTER)

        if self.expand_btn:
            self.content_col.controls.append(
                ft.Row([ft.Container(expand=True), self.expand_btn], alignment=ft.MainAxisAlignment.END, height=20)
            )

        self.content = ft.Row([
            ft.Icon(icon, color=color, size=24),
            self.content_col,
            ft.IconButton(
                ft.Icons.CLOSE,
                icon_size=16,
                icon_color=ft.Colors.ON_SURFACE_VARIANT,
                on_click=self._handle_dismiss_click
            )
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN, vertical_alignment=ft.CrossAxisAlignment.START)

        self.padding = 12
        self.bgcolor = ft.Colors.SURFACE
        self.border_left = ft.BorderSide(4, color)
        self.border_radius = 8
        self.shadow = ft.BoxShadow(
            spread_radius=1,
            blur_radius=10,
            color=ft.Colors.with_opacity(0.1, ft.Colors.SHADOW),
            offset=ft.Offset(0, 4)
        )
        # Animation
        self.offset = ft.Offset(1.1, 0)
        self.animate_offset = ft.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC)
        self.animate_opacity = ft.Animation(300, ft.AnimationCurve.EASE_IN)
        self.opacity = 0
        self.on_hover = self._on_hover

    def _toggle_expand(self, e):
        self.is_expanded = not self.is_expanded
        
        if self.is_expanded:
            self.text_control.max_lines = None # Show all
            self.expand_btn.icon = ft.Icons.KEYBOARD_ARROW_UP
            self.expand_btn.tooltip = I18n.get("common_collapse")
            # Timer logic handled in start_timer loop
        else:
            self.text_control.max_lines = self.COLLAPSED_MAX_LINES
            self.expand_btn.icon = ft.Icons.KEYBOARD_ARROW_DOWN
            self.expand_btn.tooltip = I18n.get("common_expand")
            
        self.update()

    def did_mount(self):
        self.offset = ft.Offset(0, 0)
        self.opacity = 1
        self.update()

    def cancel_timer(self):
        self._is_cancelled = True

    async def start_timer(self):
        try:
            await asyncio.sleep(0.3)

            while self.remaining > 0:
                if self._is_cancelled:
                    return
                if not self.page:
                    return
                
                # Logic: Don't countdown if hovered OR EXPANDED
                if not self.is_hovered and not self.is_expanded:
                    self.remaining -= 0.1
                
                await asyncio.sleep(0.1)

            if not self._is_cancelled:
                await self.dismiss()
        except asyncio.CancelledError:
            pass
        except Exception:
            pass

    def _on_hover(self, e):
        self.is_hovered = e.data == "true"

    async def _handle_dismiss_click(self, e):
        await self.dismiss()

    async def dismiss(self):
        if not self.page:
            return
        self.opacity = 0
        self.offset = ft.Offset(1.1, 0)
        self.update()
        await asyncio.sleep(0.3)
        if self.on_dismiss:
            self.on_dismiss(self)
