"""main.py 补充测试 - 覆盖数据库升级、初始化失败、onboarding等场景"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import main as app_main
import utils.shutdown as shutdown_mod


AsyncEventHandler = Callable[[Any], Awaitable[None]]
SyncClickHandler = Callable[[Any], None]


class _FakeTextButton:
    def __init__(self, label: str, on_click: SyncClickHandler | None = None):
        self.label = label
        self.on_click = on_click


class _FakeElevatedButton:
    def __init__(self, label: str, on_click=None, icon=None):
        self.label = label
        self.on_click = on_click
        self.icon = icon


class _DummyWindow:
    def __init__(self):
        self.prevent_close = True
        self.on_event: AsyncEventHandler | None = None
        self.min_width = 0
        self.min_height = 0
        self.width = 0
        self.height = 0
        self.destroy_called = 0

    def destroy(self):
        self.destroy_called += 1

    def center(self):
        return None


class _DummyPage:
    def __init__(self):
        self.window = _DummyWindow()
        self.on_disconnect: AsyncEventHandler | None = None
        self.on_error: Callable[[Any], None] | None = None
        self.title = ""
        self.window_icon = ""
        self.padding = 0
        self.toast = None
        self.controls = []
        self.current_dialog = None
        self.updated_count = 0
        self.run_task_calls = []

    def add(self, control):
        self.controls.append(control)

    def update(self):
        self.updated_count += 1

    def open(self, dialog):
        self.current_dialog = dialog
        dialog.open = True
        self.update()

    def close(self, dialog):
        if self.current_dialog is dialog:
            self.current_dialog = None
        dialog.open = False
        self.update()

    def clean(self):
        self.controls = []

    def run_task(self, coro, *args):
        self.run_task_calls.append((coro, args))


class _FakeCoordinator:
    last = None
    cleanup_result = True

    def __init__(self, _page, **_kwargs):
        self.cleanup_done = False
        self.start_watchdog_calls = 0
        self.cancel_watchdog_calls = 0
        self.do_cleanup_calls = 0
        self.step_results = []
        _FakeCoordinator.last = self

    def start_watchdog(self, _timeout=None):
        self.start_watchdog_calls += 1

    def cancel_watchdog(self):
        self.cancel_watchdog_calls += 1

    async def do_cleanup(self, **_kwargs):
        self.do_cleanup_calls += 1
        return self.cleanup_result

    def _force_exit(self, code):
        import os

        os._exit(code)


class _FakeAlertDialog:
    def __init__(self, **kwargs):
        self.modal = kwargs.get("modal")
        self.title = kwargs.get("title")
        self.content = kwargs.get("content")
        self.actions = kwargs.get("actions", [])
        self.actions_alignment = kwargs.get("actions_alignment")
        self.open = False


class _FakeProgressBar:
    def __init__(self, width=300):
        self.width = width


class _FakeColumn:
    def __init__(self, controls=None, spacing=10, horizontal_alignment=None):
        self.controls = controls or []
        self.spacing = spacing
        self.horizontal_alignment = horizontal_alignment


class _FakeRow:
    def __init__(self, controls=None, alignment=None, spacing=20, vertical_alignment=None):
        self.controls = controls or []
        self.alignment = alignment
        self.spacing = spacing
        self.vertical_alignment = vertical_alignment


class _FakeContainer:
    def __init__(self, content=None, expand=None, alignment=None, padding=None):
        self.content = content
        self.expand = expand
        self.alignment = alignment
        self.padding = padding


class _FakeIcon:
    def __init__(self, name, color=None, size=48):
        self.name = name
        self.color = color
        self.size = size


class _LoggerSpy:
    def __init__(self):
        self.messages: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []
        self.debugs: list[str] = []

    def info(self, msg, *args, **kwargs):
        self.messages.append(msg % args if args else msg)

    def debug(self, msg, *args, **kwargs):
        self.debugs.append(msg % args if args else msg)

    def warning(self, msg, *args, **kwargs):
        self.warnings.append(msg % args if args else msg)

    def error(self, msg, *args, **kwargs):
        self.errors.append(msg % args if args else msg)


@pytest.fixture(autouse=True)
def _reset_fake_coordinator():
    _FakeCoordinator.last = None
    _FakeCoordinator.cleanup_result = True
    yield
    _FakeCoordinator.last = None
    _FakeCoordinator.cleanup_result = True


def _prepare_main(monkeypatch, *, cleanup_result=True, exit_spy=None):
    _FakeCoordinator.cleanup_result = cleanup_result
    monkeypatch.setattr(app_main, "setup_logging", lambda: None)
    monkeypatch.setattr(app_main, "apply_page_theme", lambda _page: None)
    monkeypatch.setattr(app_main, "ToastManager", lambda _page: MagicMock())
    monkeypatch.setattr(app_main, "OnboardingWizard", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(app_main.ft, "Container", _FakeContainer)
    monkeypatch.setattr(app_main.ft, "AlertDialog", _FakeAlertDialog)
    monkeypatch.setattr(app_main.ft, "Text", lambda value, **_kwargs: value)
    monkeypatch.setattr(app_main.ft, "ProgressBar", _FakeProgressBar)
    monkeypatch.setattr(app_main.ft, "Column", _FakeColumn)
    monkeypatch.setattr(app_main.ft, "Row", _FakeRow)
    monkeypatch.setattr(app_main.ft, "Icon", _FakeIcon)
    monkeypatch.setattr(
        app_main.ft,
        "TextButton",
        lambda label, on_click=None, **_kwargs: _FakeTextButton(label=label, on_click=on_click),
    )
    monkeypatch.setattr(
        app_main.ft,
        "ElevatedButton",
        lambda label, on_click=None, icon=None, **_kwargs: _FakeElevatedButton(
            label=label, on_click=on_click, icon=icon
        ),
    )
    monkeypatch.setattr(
        app_main.ft, "MainAxisAlignment", SimpleNamespace(END="end", CENTER="center", SPACE_BETWEEN="space_between")
    )
    monkeypatch.setattr(app_main.ft, "CrossAxisAlignment", SimpleNamespace(CENTER="center"))
    monkeypatch.setattr(app_main.ft, "FontWeight", SimpleNamespace(BOLD="bold"))
    monkeypatch.setattr(app_main.ft, "WindowEventType", SimpleNamespace(CLOSE="close"))
    monkeypatch.setattr(app_main.ft, "alignment", SimpleNamespace(center="center"))
    monkeypatch.setattr(app_main, "CacheManager", lambda: MagicMock())
    monkeypatch.setattr(app_main.ProxyManager, "apply_smart_proxy_policy", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "ensure_defaults", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_db_url", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_token", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_llm_config", lambda: {"api_key": None})
    monkeypatch.setattr(app_main.ConfigHandler, "is_onboarding_complete", lambda: False)
    monkeypatch.setattr(app_main.I18n, "initialize", lambda: None)
    monkeypatch.setattr(app_main.I18n, "get", lambda key, default=None: default or key)
    monkeypatch.setattr(shutdown_mod, "ShutdownCoordinator", _FakeCoordinator)
    if exit_spy is None:
        monkeypatch.setattr(
            os, "_exit", lambda _code: (_ for _ in ()).throw(AssertionError("os._exit should not be called"))
        )
    else:
        monkeypatch.setattr(os, "_exit", exit_spy)


class TestMainDbUpgradeNeeded:
    @pytest.mark.asyncio
    async def test_db_upgrade_needed_shows_dialog(self, monkeypatch):
        _prepare_main(monkeypatch)
        mock_cm = MagicMock()
        monkeypatch.setattr(app_main, "CacheManager", lambda: mock_cm)

        with (
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("main.check_onboarding_needed", return_value=False),
        ):
            mock_init.return_value = {"success": False, "error": "db_upgrade_needed"}

            page = _DummyPage()
            await app_main.main(page)

            assert page.current_dialog is not None
            assert page.current_dialog.open is True
            dialog = page.current_dialog
            assert "升级" in str(dialog.title) or "upgrade" in str(dialog.title).lower()

    @pytest.mark.asyncio
    async def test_db_upgrade_success_flow(self, monkeypatch):
        _prepare_main(monkeypatch)
        mock_cm = MagicMock()
        mock_cm.engine = MagicMock()
        monkeypatch.setattr(app_main, "CacheManager", lambda: mock_cm)

        with (
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("main.check_onboarding_needed", return_value=False),
            patch("data.persistence.db_migrator.DatabaseMigrator.init_db", new_callable=AsyncMock) as mock_migrate,
        ):
            mock_init.return_value = {"success": False, "error": "db_upgrade_needed"}
            mock_migrate.return_value = None

            page = _DummyPage()
            await app_main.main(page)

            upgrade_dialog = page.current_dialog
            assert upgrade_dialog is not None

            upgrade_btn = upgrade_dialog.actions[0]
            assert upgrade_btn.on_click is not None


class TestMainDbInitFailed:
    @pytest.mark.asyncio
    async def test_db_init_failed_shows_error_page(self, monkeypatch):
        _prepare_main(monkeypatch)
        mock_cm = MagicMock()
        monkeypatch.setattr(app_main, "CacheManager", lambda: mock_cm)

        with (
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("main.check_onboarding_needed", return_value=False),
        ):
            mock_init.return_value = {"success": False, "error": "db_init_failed", "detail": "connection refused"}

            page = _DummyPage()
            await app_main.main(page)

            assert len(page.controls) > 0
            container = page.controls[0]
            assert isinstance(container, _FakeContainer)
            assert container.expand is True

    @pytest.mark.asyncio
    async def test_db_init_failed_retry_button(self, monkeypatch):
        _prepare_main(monkeypatch)
        mock_cm = MagicMock()
        monkeypatch.setattr(app_main, "CacheManager", lambda: mock_cm)

        init_call_count = 0

        async def mock_init_services(*args, **kwargs):
            nonlocal init_call_count
            init_call_count += 1
            if init_call_count == 1:
                return {"success": False, "error": "db_init_failed", "detail": "error"}
            return {"success": True}

        with (
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("main.check_onboarding_needed", return_value=False),
            patch("ui.app_layout.AppLayout") as mock_layout,
        ):
            mock_init.side_effect = mock_init_services
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance

            page = _DummyPage()
            await app_main.main(page)

            assert len(page.controls) > 0
            container = page.controls[0]
            col = container.content
            row = col.controls[-1]
            retry_btn = row.controls[0]
            assert retry_btn.on_click is not None

    @pytest.mark.asyncio
    async def test_db_init_failed_skip_button(self, monkeypatch):
        _prepare_main(monkeypatch)
        mock_cm = MagicMock()
        monkeypatch.setattr(app_main, "CacheManager", lambda: mock_cm)

        with (
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("main.check_onboarding_needed", return_value=False),
            patch("ui.app_layout.AppLayout") as mock_layout,
        ):
            mock_init.return_value = {"success": False, "error": "db_init_failed", "detail": "error"}
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance

            page = _DummyPage()
            await app_main.main(page)

            container = page.controls[0]
            col = container.content
            row = col.controls[-1]
            skip_btn = row.controls[1]
            assert skip_btn.on_click is not None

            skip_btn.on_click(MagicMock())
            mock_layout_instance.show.assert_called_once()


class TestMainOnboardingFlow:
    @pytest.mark.asyncio
    async def test_onboarding_needed_shows_wizard(self, monkeypatch):
        _prepare_main(monkeypatch)

        with patch("main.check_onboarding_needed", return_value=True):
            page = _DummyPage()
            await app_main.main(page)

            assert len(page.controls) > 0
            container = page.controls[0]
            assert isinstance(container, _FakeContainer)
            assert container.padding == 40

    @pytest.mark.asyncio
    async def test_onboarding_complete_calls_init_services(self, monkeypatch):
        _prepare_main(monkeypatch)

        with (
            patch("main.check_onboarding_needed", return_value=False),
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("main.NewsSubscriptionService") as mock_ns,
        ):
            mock_init.return_value = {"success": True}
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance
            mock_ns_instance = MagicMock()
            mock_ns.return_value = mock_ns_instance

            page = _DummyPage()
            await app_main.main(page)

            mock_init.assert_awaited_once()
            mock_layout_instance.show.assert_called_once()


class TestMainServicesSuccess:
    @pytest.mark.asyncio
    async def test_services_success_starts_app_layout(self, monkeypatch):
        _prepare_main(monkeypatch)

        with (
            patch("main.check_onboarding_needed", return_value=False),
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("main.NewsSubscriptionService") as mock_ns,
        ):
            mock_init.return_value = {"success": True}
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance
            mock_ns_instance = MagicMock()
            mock_ns.return_value = mock_ns_instance

            page = _DummyPage()
            await app_main.main(page)

            mock_layout_instance.show.assert_called_once()
            mock_ns_instance.add_listener.assert_called_once()


class TestMainWindowDestroyError:
    @pytest.mark.asyncio
    async def test_window_destroy_error_logged_and_ignored(self, monkeypatch):
        _prepare_main(monkeypatch, cleanup_result=True)
        logger_spy = _LoggerSpy()
        monkeypatch.setattr(app_main, "logger", logger_spy)

        class _WindowWithDestroyError:
            def __init__(self):
                self.prevent_close = True
                self.on_event = None
                self.min_width = 0
                self.min_height = 0
                self.width = 0
                self.height = 0

            def destroy(self):
                raise RuntimeError("window already destroyed")

            def center(self):
                return None

        class _PageWithDestroyError(_DummyPage):
            def __init__(self):
                super().__init__()
                self.window = _WindowWithDestroyError()

            def run_task(self, coro, *args):
                import asyncio

                try:
                    loop = asyncio.get_event_loop()
                    if asyncio.iscoroutine(coro):
                        loop.create_task(coro)
                    elif asyncio.iscoroutinefunction(coro):
                        loop.create_task(coro(*args))
                except RuntimeError:
                    pass

        page = _PageWithDestroyError()
        await app_main.main(page)

        assert page.window.on_event is not None
        on_event = cast(AsyncEventHandler, page.window.on_event)
        await on_event(SimpleNamespace(type="close"))

        dialog = page.current_dialog
        assert dialog is not None
        confirm_btn = cast(_FakeTextButton, dialog.actions[1])
        assert confirm_btn.on_click is not None
        confirm_btn.on_click(MagicMock())
        await asyncio.sleep(0.1)

        assert any("destroy ignored" in msg.lower() or "Window destroy" in msg for msg in logger_spy.debugs)


class TestMainScheduleAsync:
    @pytest.mark.asyncio
    async def test_schedule_async_with_run_task(self, monkeypatch):
        _prepare_main(monkeypatch)

        class _PageWithRunTask(_DummyPage):
            def __init__(self):
                super().__init__()
                self._run_task_called = False

            def run_task(self, coro):
                self._run_task_called = True

        with patch("main.check_onboarding_needed", return_value=False):
            page = _PageWithRunTask()
            await app_main.main(page)

            assert page.window.on_event is not None


class TestMainShowHideDialog:
    @pytest.mark.asyncio
    async def test_show_dialog_sets_active_dialog(self, monkeypatch):
        _prepare_main(monkeypatch)

        page = _DummyPage()
        await app_main.main(page)

        assert page.window.on_event is not None
        on_event = cast(AsyncEventHandler, page.window.on_event)
        await on_event(SimpleNamespace(type="close"))

        assert page.current_dialog is not None
        assert page.current_dialog.open is True

    @pytest.mark.asyncio
    async def test_hide_dialog_clears_active_dialog(self, monkeypatch):
        _prepare_main(monkeypatch)

        page = _DummyPage()
        await app_main.main(page)

        on_event = cast(AsyncEventHandler, page.window.on_event)
        await on_event(SimpleNamespace(type="close"))

        assert page.current_dialog is not None

        cancel_btn = cast(_FakeTextButton, page.current_dialog.actions[0])
        assert cancel_btn.on_click is not None
        cancel_btn.on_click(MagicMock())
        await asyncio.sleep(0)

        assert page.current_dialog is None


class TestMainPageDialogMatchesCloseConfirm:
    @pytest.mark.asyncio
    async def test_page_dialog_matches_close_confirm_returns_true(self, monkeypatch):
        _prepare_main(monkeypatch)

        page = _DummyPage()
        await app_main.main(page)

        on_event = cast(AsyncEventHandler, page.window.on_event)
        await on_event(SimpleNamespace(type="close"))

        dialog = page.current_dialog
        assert dialog is not None
        assert dialog.open is True


class TestMainConfigHandlerCalls:
    @pytest.mark.asyncio
    async def test_config_handler_methods_called(self, monkeypatch):
        _prepare_main(monkeypatch)

        calls = []

        def track_get_db_url():
            calls.append("get_db_url")
            return "test_url"

        def track_get_token():
            calls.append("get_token")
            return "test_token"

        def track_get_llm_config():
            calls.append("get_llm_config")
            return {"api_key": "test_key"}

        def track_is_onboarding_complete():
            calls.append("is_onboarding_complete")
            return True

        monkeypatch.setattr(app_main.ConfigHandler, "get_db_url", track_get_db_url)
        monkeypatch.setattr(app_main.ConfigHandler, "get_token", track_get_token)
        monkeypatch.setattr(app_main.ConfigHandler, "get_llm_config", track_get_llm_config)
        monkeypatch.setattr(app_main.ConfigHandler, "is_onboarding_complete", track_is_onboarding_complete)

        with (
            patch("main.check_onboarding_needed", return_value=False),
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("main.NewsSubscriptionService") as mock_ns,
        ):
            mock_init.return_value = {"success": True}
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance
            mock_ns_instance = MagicMock()
            mock_ns.return_value = mock_ns_instance

            page = _DummyPage()
            await app_main.main(page)

            assert "get_db_url" in calls
            assert "get_token" in calls
            assert "get_llm_config" in calls
            assert "is_onboarding_complete" in calls


class TestMainMaskSensitive:
    @pytest.mark.asyncio
    async def test_mask_sensitive_called_in_debug_log(self, monkeypatch):
        _prepare_main(monkeypatch)
        logger_spy = _LoggerSpy()
        monkeypatch.setattr(app_main, "logger", logger_spy)

        with (
            patch("main.check_onboarding_needed", return_value=False),
            patch("main.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("main.NewsSubscriptionService") as mock_ns,
        ):
            mock_init.return_value = {"success": True}
            mock_layout_instance = MagicMock()
            mock_layout.return_value = mock_layout_instance
            mock_ns_instance = MagicMock()
            mock_ns.return_value = mock_ns_instance

            page = _DummyPage()
            await app_main.main(page)

            assert len(logger_spy.debugs) > 0
            assert any("DB_URL" in d for d in logger_spy.debugs)


class TestMainOnError:
    @pytest.mark.asyncio
    async def test_on_error_handler_logs_error(self, monkeypatch):
        _prepare_main(monkeypatch)
        logger_spy = _LoggerSpy()
        monkeypatch.setattr(app_main, "logger", logger_spy)

        with patch("main.check_onboarding_needed", return_value=False):
            page = _DummyPage()
            await app_main.main(page)

            assert page.on_error is not None
            test_error = RuntimeError("test error")
            page.on_error(test_error)

            assert any("Unhandled UI Exception" in msg for msg in logger_spy.errors)


class TestMainDisconnectCleanupDone:
    @pytest.mark.asyncio
    async def test_disconnect_when_cleanup_done_skips_force_exit(self, monkeypatch):
        _prepare_main(monkeypatch, cleanup_result=True)

        class _CoordinatorWithCleanupDone(_FakeCoordinator):
            def __init__(self, page, **kwargs):
                super().__init__(page, **kwargs)
                self.cleanup_done = True

        monkeypatch.setattr(shutdown_mod, "ShutdownCoordinator", _CoordinatorWithCleanupDone)

        with patch("main.check_onboarding_needed", return_value=False):
            page = _DummyPage()
            await app_main.main(page)

            assert page.on_disconnect is not None
            on_disconnect = cast(AsyncEventHandler, page.on_disconnect)
            await on_disconnect(MagicMock())

            coordinator = _CoordinatorWithCleanupDone.last
            assert coordinator is not None
            assert coordinator.do_cleanup_calls == 1


class TestMainWindowCloseShowDialogSkipped:
    @pytest.mark.asyncio
    async def test_show_dialog_skipped_when_already_visible(self, monkeypatch):
        _prepare_main(monkeypatch)
        logger_spy = _LoggerSpy()
        monkeypatch.setattr(app_main, "logger", logger_spy)

        with patch("main.check_onboarding_needed", return_value=False):
            page = _DummyPage()
            await app_main.main(page)

            assert page.window.on_event is not None
            on_event = cast(AsyncEventHandler, page.window.on_event)

            await on_event(SimpleNamespace(type="close"))
            assert page.current_dialog is not None

            await on_event(SimpleNamespace(type="close"))

            assert any("Skip showing close confirm dialog" in msg for msg in logger_spy.messages)


class TestMainHideCloseConfirmDialog:
    @pytest.mark.asyncio
    async def test_hide_close_confirm_dialog_when_none(self, monkeypatch):
        _prepare_main(monkeypatch)

        with patch("main.check_onboarding_needed", return_value=False):
            page = _DummyPage()
            await app_main.main(page)

            assert page.window.on_event is not None
            on_event = cast(AsyncEventHandler, page.window.on_event)
            await on_event(SimpleNamespace(type="close"))

            dialog = page.current_dialog
            assert dialog is not None

            cancel_btn = cast(_FakeTextButton, dialog.actions[0])
            assert cancel_btn.on_click is not None
            cancel_btn.on_click(MagicMock())
            await asyncio.sleep(0)

            assert page.current_dialog is None
