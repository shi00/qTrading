"""main.py 测试 - 覆盖窗口/对话框/disconnect管理等场景"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.startup_controller as startup_ctrl
import main as app_main
import ui.views.onboarding_wizard as onboarding_wizard_mod
import utils.shutdown as shutdown_mod

pytestmark = [pytest.mark.integration, pytest.mark.no_db]

AsyncEventHandler = Callable[[Any], Awaitable[None]]
SyncClickHandler = Callable[[Any], None]


class _FakeTextButton:
    def __init__(self, label: str, on_click: SyncClickHandler | None = None):
        self.label = label
        self.content = label
        self.on_click = on_click


class _FakeAlertDialog:
    def __init__(self, **kwargs):
        self.modal = kwargs.get("modal")
        self.title = kwargs.get("title")
        self.content = kwargs.get("content")
        self.actions = kwargs.get("actions", [])
        self.actions_alignment = kwargs.get("actions_alignment")
        self.open = False


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
        self.run_task_calls = []

    def add(self, control):
        self.controls.append(control)

    def render(self, component, /, *args, **kwargs):
        """Mock page.render (V1 声明式 API) — 仅记录调用, 不实际渲染。"""
        self.controls.append(component)

    def update(self):
        self.updated_count += 1

    def clean(self):
        self.controls = []

    def run_task(self, coro, *args):
        self.run_task_calls.append((coro, args))

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
    # G.1 重写后 main.py 用声明式 @ft.component StartupView 替代 OnboardingWizard;
    # 测试目标是 main.py 的窗口/对话框/disconnect 行为, 不验证 StartupView 渲染,
    # mock 为 MagicMock 避免 renderer 上下文依赖
    monkeypatch.setattr(app_main, "StartupView", lambda *_args, **_kwargs: MagicMock())
    # CloseConfirmDialog 同为 @ft.component, mock 为返回 _FakeAlertDialog 的工厂,
    # 保留 actions[0/1].on_click 回调绑定以验证 cancel/confirm 按钮行为
    monkeypatch.setattr(
        app_main,
        "CloseConfirmDialog",
        lambda on_cancel, on_confirm: _FakeAlertDialog(
            actions=[
                _FakeTextButton("Cancel", on_click=on_cancel),
                _FakeTextButton("Confirm", on_click=on_confirm),
            ],
        ),
    )
    monkeypatch.setattr(shutdown_mod, "ShutdownCoordinator", _FakeCoordinator)
    _tpm_mock = MagicMock()
    _tpm_mock.return_value.run_async = AsyncMock()
    monkeypatch.setattr("utils.thread_pool.ThreadPoolManager", _tpm_mock)
    if exit_spy is None:
        monkeypatch.setattr(
            os,
            "_exit",
            lambda _code: (_ for _ in ()).throw(AssertionError("os._exit should not be called")),
        )
    else:
        monkeypatch.setattr(os, "_exit", exit_spy)


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
        confirm_btn = dialog.actions[1]
        assert confirm_btn.on_click is not None
        confirm_btn.on_click(MagicMock())
        await asyncio.sleep(0.1)

        assert any("destroy ignored" in msg.lower() or "Window destroy" in msg for msg in logger_spy.debugs)


class TestMainRunTask:
    @pytest.mark.asyncio
    async def test_run_task_direct_call(self, monkeypatch):
        _prepare_main(monkeypatch)

        class _PageWithRunTask(_DummyPage):
            def __init__(self):
                super().__init__()
                self._run_task_called = False

            def run_task(self, coro):
                self._run_task_called = True

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

        cancel_btn = page.current_dialog.actions[0]
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
        monkeypatch.setattr(
            app_main.ConfigHandler,
            "is_onboarding_complete",
            track_is_onboarding_complete,
        )

        with (
            patch("app.startup_controller.check_onboarding_needed", return_value=False),
            patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_ns,
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
            patch("app.startup_controller.check_onboarding_needed", return_value=False),
            patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("ui.app_layout.AppLayout") as mock_layout,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_ns,
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

        with (
            patch("app.startup_controller.check_onboarding_needed", return_value=False),
            patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init,
        ):
            mock_init.return_value = {"success": False, "error": "db_init_failed"}
            page = _DummyPage()
            await app_main.main(page)

            assert page.window.on_event is not None
            on_event = cast(AsyncEventHandler, page.window.on_event)

            await on_event(SimpleNamespace(type="close"))
            assert page.current_dialog is not None

            await on_event(SimpleNamespace(type="close"))

            assert any("Skip showing close confirm dialog" in msg for msg in logger_spy.debugs)


class TestMainHideCloseConfirmDialog:
    @pytest.mark.asyncio
    async def test_hide_close_confirm_dialog_when_none(self, monkeypatch):
        _prepare_main(monkeypatch)

        page = _DummyPage()
        await app_main.main(page)

        assert page.window.on_event is not None
        on_event = cast(AsyncEventHandler, page.window.on_event)
        await on_event(SimpleNamespace(type="close"))

        dialog = page.current_dialog
        assert dialog is not None

        cancel_btn = dialog.actions[0]
        assert cancel_btn.on_click is not None
        cancel_btn.on_click(MagicMock())
        await asyncio.sleep(0)

        assert page.current_dialog is None


@pytest.mark.asyncio
async def test_min_window_size_and_web_skip(monkeypatch):
    """窗口最小尺寸 1280x720 与默认 1280x800；Web 模式跳过窗口尺寸设置。

    响应式布局修复方案 v4.3 Task 2：窗口最小尺寸提升。
    _DummyWindow 初始 min_width/min_height/width/height 均为 0。
    """
    # --- 桌面模式：设置最小尺寸 1280x720 与默认尺寸 1280x800 ---
    _prepare_main(monkeypatch)
    desktop_page = _DummyPage()
    await app_main.main(desktop_page)

    assert desktop_page.window.min_width == 1280
    assert desktop_page.window.min_height == 720
    # width=0 (falsy) → 触发默认窗口逻辑，置为 1280x800
    assert desktop_page.window.width == 1280
    assert desktop_page.window.height == 800

    # --- Web 模式：跳过窗口尺寸设置，保持 _DummyWindow 初始值 0 ---
    monkeypatch.setenv("FLET_FORCE_WEB_SERVER", "true")
    web_page = _DummyPage()
    await app_main.main(web_page)

    assert web_page.window.min_width == 0
    assert web_page.window.min_height == 0
    assert web_page.window.width == 0
    assert web_page.window.height == 0
