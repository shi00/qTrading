import asyncio
import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.startup_controller as startup_ctrl
import main as app_main
import ui.views.onboarding_wizard as onboarding_wizard_mod
import utils.shutdown as shutdown_mod

pytestmark = pytest.mark.integration

AsyncEventHandler = Callable[[Any], Awaitable[None]]
SyncClickHandler = Callable[[Any], None]


class _FakeTextButton:
    def __init__(self, label: str, on_click: SyncClickHandler | None = None):
        self.label = label
        self.on_click = on_click


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
        self.padding = 0
        self.toast = None
        self.controls = []
        self.current_dialog = None
        self.updated_count = 0

    def add(self, control):
        self.controls.append(control)

    def update(self):
        self.updated_count += 1

    def run_task(self, coro, *args):
        # V1: main.py 直接调用 page.run_task(_perform_window_shutdown) 调度协程。
        # 测试需要协程真正执行以验证 coordinator 调用，故用 loop.create_task 调度。
        try:
            loop = asyncio.get_event_loop()
            if asyncio.iscoroutine(coro):
                loop.create_task(coro)
            elif asyncio.iscoroutinefunction(coro):
                loop.create_task(coro(*args))
        except RuntimeError:
            pass

    def show_dialog(self, dialog):
        self.current_dialog = dialog
        dialog.open = True
        self.update()

    def pop_dialog(self):
        if self.current_dialog is not None:
            self.current_dialog.open = False
            self.current_dialog = None
        self.update()


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


class _LoggerSpy:
    def __init__(self):
        self.messages: list[str] = []

    def info(self, msg, *args):
        self.messages.append(msg % args if args else msg)

    def debug(self, msg, *args):
        self.messages.append(msg % args if args else msg)


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
    # WindowEventType must be a string "close" so SimpleNamespace(type="close") matches
    monkeypatch.setattr(app_main.ft, "WindowEventType", SimpleNamespace(CLOSE="close"))
    monkeypatch.setattr(app_main, "CacheManager", lambda: MagicMock())
    monkeypatch.setattr(app_main.ProxyManager, "apply_smart_proxy_policy", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "ensure_defaults", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_db_url", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_token", lambda: None)
    monkeypatch.setattr(app_main.ConfigHandler, "get_llm_config", lambda: {"api_key": None})
    monkeypatch.setattr(app_main.ConfigHandler, "is_onboarding_complete", lambda: False)
    monkeypatch.setattr(app_main.I18n, "initialize", lambda *args, **kwargs: None)
    monkeypatch.setattr(app_main.I18n, "get", lambda key, default=None: default or key)
    # Mock startup flow to avoid real DB operations: stop at NEED_ONBOARDING by default
    monkeypatch.setattr(startup_ctrl, "check_onboarding_needed", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(onboarding_wizard_mod, "OnboardingWizard", lambda *_args, **_kwargs: MagicMock())
    monkeypatch.setattr(shutdown_mod, "ShutdownCoordinator", _FakeCoordinator)
    if exit_spy is None:
        monkeypatch.setattr(
            os,
            "_exit",
            lambda _code: (_ for _ in ()).throw(AssertionError("os._exit should not be called")),
        )
    else:
        monkeypatch.setattr(os, "_exit", exit_spy)


@pytest.mark.asyncio
async def test_disconnect_success_cancels_watchdog(monkeypatch):
    _prepare_main(monkeypatch)
    page = _DummyPage()

    await app_main.main(page)

    assert page.on_disconnect is not None
    on_disconnect = cast(AsyncEventHandler, page.on_disconnect)
    await on_disconnect(MagicMock())

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert coordinator.start_watchdog_calls == 1
    assert coordinator.do_cleanup_calls == 1
    assert coordinator.cancel_watchdog_calls == 1


@pytest.mark.asyncio
async def test_window_close_success_cancels_watchdog(monkeypatch):
    _prepare_main(monkeypatch)
    page = _DummyPage()

    await app_main.main(page)

    assert page.window.on_event is not None
    on_event = cast(AsyncEventHandler, page.window.on_event)
    await on_event(SimpleNamespace(type="close"))
    assert page.current_dialog is not None
    assert page.current_dialog.open is True

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert coordinator.start_watchdog_calls == 0
    assert coordinator.do_cleanup_calls == 0

    confirm_btn = cast(_FakeTextButton, page.current_dialog.actions[1])
    assert confirm_btn.on_click is not None
    confirm_btn.on_click(MagicMock())
    await asyncio.sleep(0)

    assert coordinator.start_watchdog_calls == 1
    assert coordinator.do_cleanup_calls == 1
    assert coordinator.cancel_watchdog_calls == 1
    assert page.window.destroy_called == 1


@pytest.mark.asyncio
async def test_window_close_cancel_does_not_shutdown(monkeypatch):
    _prepare_main(monkeypatch)
    page = _DummyPage()

    await app_main.main(page)
    assert page.window.on_event is not None
    on_event = cast(AsyncEventHandler, page.window.on_event)
    await on_event(SimpleNamespace(type="close"))
    assert page.current_dialog is not None
    assert page.current_dialog.open is True

    cancel_btn = cast(_FakeTextButton, page.current_dialog.actions[0])
    assert cancel_btn.on_click is not None
    cancel_btn.on_click(MagicMock())
    await asyncio.sleep(0)

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert coordinator.start_watchdog_calls == 0
    assert coordinator.do_cleanup_calls == 0
    assert coordinator.cancel_watchdog_calls == 0
    assert page.window.destroy_called == 0
    assert page.current_dialog is None


@pytest.mark.asyncio
async def test_window_close_failure_forces_exit(monkeypatch):
    exit_calls = []
    real_sleep = asyncio.sleep
    _prepare_main(monkeypatch, cleanup_result=False, exit_spy=lambda code: exit_calls.append(code))
    monkeypatch.setattr(app_main.asyncio, "sleep", AsyncMock(return_value=None))
    page = _DummyPage()

    await app_main.main(page)
    assert page.window.on_event is not None
    on_event = cast(AsyncEventHandler, page.window.on_event)
    await on_event(SimpleNamespace(type="close"))
    assert page.current_dialog is not None
    confirm_btn = cast(_FakeTextButton, page.current_dialog.actions[1])
    assert confirm_btn.on_click is not None
    confirm_btn.on_click(MagicMock())
    # Use the real sleep to yield control; app_main.asyncio.sleep is mocked.
    await real_sleep(0)
    await real_sleep(0)

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert coordinator.start_watchdog_calls == 1
    assert coordinator.do_cleanup_calls == 1
    assert coordinator.cancel_watchdog_calls == 0
    assert exit_calls == [1]


@pytest.mark.asyncio
async def test_disconnect_failure_forces_exit(monkeypatch):
    exit_calls = []
    _prepare_main(monkeypatch, cleanup_result=False, exit_spy=lambda code: exit_calls.append(code))
    monkeypatch.setattr(app_main.asyncio, "sleep", AsyncMock(return_value=None))
    page = _DummyPage()

    await app_main.main(page)
    assert page.on_disconnect is not None
    on_disconnect = cast(AsyncEventHandler, page.on_disconnect)
    await on_disconnect(MagicMock())

    coordinator = _FakeCoordinator.last
    assert coordinator is not None
    assert coordinator.start_watchdog_calls == 1
    assert coordinator.do_cleanup_calls == 1
    assert coordinator.cancel_watchdog_calls == 0
    assert exit_calls == [1]


@pytest.mark.asyncio
async def test_window_close_during_shutdown_does_not_reopen_dialog(monkeypatch):
    _prepare_main(monkeypatch, cleanup_result=True)
    started = asyncio.Event()
    release = asyncio.Event()

    async def _blocking_cleanup(self, **_kwargs):
        self.do_cleanup_calls += 1
        started.set()
        await release.wait()
        return True

    monkeypatch.setattr(_FakeCoordinator, "do_cleanup", _blocking_cleanup, raising=False)
    page = _DummyPage()

    await app_main.main(page)
    assert page.window.on_event is not None
    on_event = cast(AsyncEventHandler, page.window.on_event)
    await on_event(SimpleNamespace(type="close"))
    assert page.current_dialog is not None
    confirm_btn = cast(_FakeTextButton, page.current_dialog.actions[1])
    assert confirm_btn.on_click is not None
    confirm_btn.on_click(MagicMock())
    await started.wait()

    await on_event(SimpleNamespace(type="close"))
    assert page.current_dialog is None

    release.set()
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_window_close_logs_dialog_state_transitions(monkeypatch):
    _prepare_main(monkeypatch)
    page = _DummyPage()
    logger_spy = _LoggerSpy()
    monkeypatch.setattr(app_main, "logger", logger_spy)

    ui_logger_spy = MagicMock()
    monkeypatch.setattr(app_main.UILogger, "log_action", ui_logger_spy)

    await app_main.main(page)
    assert page.window.on_event is not None
    on_event = cast(AsyncEventHandler, page.window.on_event)
    await on_event(SimpleNamespace(type="close"))

    # Internal state logs are DEBUG, not INFO
    assert any("Window event received." in msg for msg in logger_spy.messages)
    assert any("Request to show close confirm dialog." in msg for msg in logger_spy.messages)
    # User action goes through UILogger.log_action, not main logger
    ui_logger_spy.assert_called_with("MainWindow", action="close_request")
