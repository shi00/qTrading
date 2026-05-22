"""ui/components/toast_manager.py 单元测试"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from ui.components.toast_manager import ToastCard, ToastManager


class TestToastManagerInit:
    def test_init_with_page(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)

            assert manager.page == mock_page
            assert manager.container in mock_page.overlay
            assert manager._is_stopping is False

    def test_init_without_page(self):
        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(None)

            assert manager.page is None
            assert manager._is_stopping is False


class TestToastManagerShow:
    def test_show_without_page(self):
        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(None)
            manager.show("test message")

            assert len(manager.toasts_stack.controls) == 0

    def test_show_when_stopping(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)
            manager._is_stopping = True
            manager.show("test message")

            assert len(manager.toasts_stack.controls) == 0

    def test_show_adds_toast(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.SUCCESS = "green"
            mock_colors.ERROR = "red"
            mock_colors.WARNING = "yellow"
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)
            manager.show("test message", toast_type="success")

            assert len(manager.toasts_stack.controls) == 1

    def test_show_different_types(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.SUCCESS = "green"
            mock_colors.ERROR = "red"
            mock_colors.WARNING = "yellow"
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)

            manager.show("success", toast_type="success")
            manager.show("error", toast_type="error")
            manager.show("warning", toast_type="warning")
            manager.show("info", toast_type="info")

            assert len(manager.toasts_stack.controls) == 4

    def test_show_limits_max_toasts(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)

            for i in range(10):
                manager.show(f"message {i}")

            assert len(manager.toasts_stack.controls) == ToastManager.MAX_TOAST_COUNT

    def test_show_removes_oldest_when_exceeds_max(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)

            manager.show("first message")
            first_toast = manager.toasts_stack.controls[0]

            for i in range(ToastManager.MAX_TOAST_COUNT):
                manager.show(f"message {i}")

            assert first_toast not in manager.toasts_stack.controls

    def test_show_update_exception_handled(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)

            mock_stack = MagicMock()
            mock_stack.controls = []
            mock_stack.update.side_effect = Exception("update error")
            manager.toasts_stack = mock_stack

            manager.show("test message")

            assert len(mock_stack.controls) == 1


class TestToastManagerRemoveToast:
    def test_remove_toast(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)
            manager.show("test message")

            toast = manager.toasts_stack.controls[0]
            manager._remove_toast(toast)

            assert toast not in manager.toasts_stack.controls

    def test_remove_toast_not_in_stack(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)

            mock_toast = MagicMock()
            manager._remove_toast(mock_toast)

    def test_remove_toast_update_exception(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)
            manager.show("test message")

            toast = manager.toasts_stack.controls[0]

            mock_stack = MagicMock()
            mock_stack.controls = [toast]
            mock_stack.update.side_effect = Exception("update error")
            manager.toasts_stack = mock_stack

            manager._remove_toast(toast)


class TestToastManagerRegisterTask:
    def test_register_task_none(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)
            manager._register_task(None)

            assert len(manager._active_tasks) == 0

    def test_register_task_invalid_type(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)
            manager._register_task("not a task")

            assert len(manager._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_register_task_valid(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)

            async def dummy_coro():
                pass

            task = asyncio.create_task(dummy_coro())
            manager._register_task(task)

            assert task in manager._active_tasks

            await task

            assert task not in manager._active_tasks


class TestToastManagerStopAll:
    @pytest.mark.asyncio
    async def test_stop_all_no_tasks(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)
            await manager.stop_all()

            assert manager._is_stopping is True

    @pytest.mark.asyncio
    async def test_stop_all_with_tasks(self):
        mock_page = MagicMock()
        mock_page.overlay = []

        with (
            patch("ui.components.toast_manager.AppColors"),
        ):
            manager = ToastManager(mock_page)

            async def long_running():
                try:
                    await asyncio.sleep(100)
                except asyncio.CancelledError:
                    pass

            task = asyncio.create_task(long_running())
            manager._active_tasks.add(task)

            await manager.stop_all()

            assert task.done()
            assert manager._is_stopping is True

    @pytest.mark.asyncio
    async def test_stop_all_clears_toasts(self):
        mock_page = MagicMock()
        mock_page.overlay = []
        mock_page.run_task = MagicMock(return_value=MagicMock())

        with (
            patch("ui.components.toast_manager.AppColors") as mock_colors,
        ):
            mock_colors.INFO = "blue"

            manager = ToastManager(mock_page)
            manager.show("test message")

            await manager.stop_all()

            assert len(manager.toasts_stack.controls) == 0


class TestToastCardInit:
    def test_init_short_text(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="short message",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            assert card.message == "short message"
            assert card.duration == 10
            assert card.is_long_text is False
            assert card.expand_btn is None

    def test_init_long_text(self):
        long_message = "x" * 100

        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message=long_message,
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            assert card.is_long_text is True
            assert card.expand_btn is not None


class TestToastCardToggleExpand:
    def test_toggle_expand(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
            patch("ui.components.toast_manager.ft.Icons"),
        ):
            card = ToastCard(
                message="x" * 100,
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()

            card._toggle_expand(MagicMock())
            assert card.is_expanded is True
            assert card.text_control.max_lines in (None, "", 0)

            card._toggle_expand(MagicMock())
            assert card.is_expanded is False
            assert card.text_control.max_lines == ToastCard.COLLAPSED_MAX_LINES


class TestToastCardCancelTimer:
    def test_cancel_timer(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            card.cancel_timer()
            assert card._is_cancelled is True


class TestToastCardStartTimer:
    @pytest.mark.asyncio
    async def test_start_timer_cancelled(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()

            card.cancel_timer()
            await card.start_timer()

            assert card.remaining == 10

    @pytest.mark.asyncio
    async def test_start_timer_no_page(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            await card.start_timer()

    @pytest.mark.asyncio
    async def test_start_timer_counts_down(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=1,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()

            await card.start_timer()

            assert card.remaining <= 0

    @pytest.mark.asyncio
    async def test_start_timer_hovered_pauses(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=5,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()
            card.is_hovered = True

            async def run_timer():
                await asyncio.sleep(0.5)
                card.cancel_timer()

            task = asyncio.create_task(run_timer())
            await card.start_timer()

            assert card.remaining == 5

    @pytest.mark.asyncio
    async def test_start_timer_expanded_pauses(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=5,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()
            card.is_expanded = True

            async def run_timer():
                await asyncio.sleep(0.5)
                card.cancel_timer()

            task = asyncio.create_task(run_timer())
            await card.start_timer()

            assert card.remaining == 5

    @pytest.mark.asyncio
    async def test_start_timer_cancelled_error(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()

            task = asyncio.create_task(card.start_timer())
            await asyncio.sleep(0.1)
            task.cancel()

            await asyncio.gather(task, return_exceptions=True)


class TestToastCardOnHover:
    def test_on_hover_true(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            card._on_hover(MagicMock(data="true"))
            assert card.is_hovered is True

    def test_on_hover_false(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            card._on_hover(MagicMock(data="false"))
            assert card.is_hovered is False


class TestToastCardDismiss:
    @pytest.mark.asyncio
    async def test_dismiss_without_page(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )

            await card.dismiss()

    @pytest.mark.asyncio
    async def test_dismiss_with_page(self):
        mock_on_dismiss = MagicMock()

        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=mock_on_dismiss,
            )
            card.page = MagicMock()

            await card.dismiss()

            mock_on_dismiss.assert_called_once_with(card)


class TestToastCardHandleDismissClick:
    @pytest.mark.asyncio
    async def test_handle_dismiss_click(self):
        mock_on_dismiss = MagicMock()

        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=mock_on_dismiss,
            )
            card.page = MagicMock()

            await card._handle_dismiss_click(MagicMock())

            mock_on_dismiss.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_dismiss_click_already_dismissing(self):
        mock_on_dismiss = MagicMock()

        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=mock_on_dismiss,
            )
            card._is_dismissing = True

            await card._handle_dismiss_click(MagicMock())

            mock_on_dismiss.assert_not_called()


class TestToastCardDidMount:
    def test_did_mount(self):
        with (
            patch("ui.components.toast_manager.I18n.get", return_value="test"),
            patch("ui.components.toast_manager.AppColors"),
        ):
            card = ToastCard(
                message="test",
                icon="icon",
                color="blue",
                duration=10,
                on_dismiss=MagicMock(),
            )
            card.page = MagicMock()

            card.did_mount()

            assert card.opacity == 1
