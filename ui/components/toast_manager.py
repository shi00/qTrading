import asyncio
import logging
import threading

import flet as ft

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
            width=320,
            bgcolor=ft.Colors.TRANSPARENT,
        )

        if self.page:
            self.page.overlay.append(self.container)
            self.page.update()

    def _register_task(self, task: asyncio.Task) -> None:
        """Register a task for lifecycle tracking with auto-cleanup (thread-safe)."""
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

        # Determine colors and icon
        color_map = {
            "success": (ft.Colors.GREEN, ft.Icons.CHECK_CIRCLE, ft.Colors.GREEN_50),
            "error": (ft.Colors.RED, ft.Icons.ERROR, ft.Colors.RED_50),
            "warning": (ft.Colors.ORANGE, ft.Icons.WARNING, ft.Colors.ORANGE_50),
            "info": (ft.Colors.BLUE, ft.Icons.INFO, ft.Colors.BLUE_50),
        }
        color, icon, bg_color = color_map.get(type, color_map["info"])

        toast_card = ToastCard(
            message=message,
            icon=icon,
            color=color,
            bg_color=bg_color,
            duration=duration,
            on_dismiss=self._remove_toast
        )

        with self._lock:
            self.toasts_stack.controls.append(toast_card)

            # Limit max toasts (remove oldest)
            while len(self.toasts_stack.controls) > 5:
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
        for task in tasks_snapshot:
            if not task.done():
                task.cancel()

        # Wait for all cancellations to complete
        if tasks_snapshot:
            await asyncio.gather(*tasks_snapshot, return_exceptions=True)

        # Clear UI
        with self._lock:
            for control in self.toasts_stack.controls:
                if isinstance(control, ToastCard):
                    control.cancel_timer()
            self.toasts_stack.controls.clear()


class ToastCard(ft.Container):
    """Individual toast notification card with animation and timer."""

    def __init__(self, message, icon, color, bg_color, duration, on_dismiss):
        super().__init__()
        self.duration = duration
        self.on_dismiss = on_dismiss
        self.is_hovered = False
        self.remaining = duration
        self._is_cancelled = False

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
        self.offset = ft.transform.Offset(1.1, 0)  # Start off-screen right
        self.animate_offset = ft.animation.Animation(300, ft.AnimationCurve.EASE_OUT_CUBIC)
        self.animate_opacity = ft.animation.Animation(300, ft.AnimationCurve.EASE_IN)
        self.opacity = 0

        self.on_hover = self._on_hover

    def did_mount(self):
        # Trigger enter animation
        self.offset = ft.transform.Offset(0, 0)
        self.opacity = 1
        self.update()

    def cancel_timer(self):
        self._is_cancelled = True

    async def start_timer(self):
        try:
            # Wait for enter animation
            await asyncio.sleep(0.3)

            while self.remaining > 0:
                if self._is_cancelled:
                    return
                if not self.page:
                    return  # Page closed
                if not self.is_hovered:
                    self.remaining -= 0.1
                await asyncio.sleep(0.1)

            if not self._is_cancelled:
                await self.dismiss()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Task destroyed or other shutdown error
            pass

    def _on_hover(self, e):
        self.is_hovered = e.data == "true"

    async def _handle_dismiss_click(self, e):
        await self.dismiss()

    async def dismiss(self):
        """Animate out and notify manager to remove this toast."""
        if not self.page:
            return  # Already detached from page

        self.opacity = 0
        self.offset = ft.transform.Offset(1.1, 0)  # Slide out right
        self.update()
        await asyncio.sleep(0.3)
        if self.on_dismiss:
            self.on_dismiss(self)
