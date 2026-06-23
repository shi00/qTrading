"""Unit tests for StartupController — zero Flet mocks, pure business logic."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.startup_controller import StartupController, StartupState


@pytest.fixture
def state_recorder():
    """Records all state transitions."""
    transitions = []

    def recorder(state, context):
        transitions.append((state, context))

    return transitions, recorder


@pytest.fixture
def controller(state_recorder):
    """Creates a controller with mocked dependencies."""
    transitions, recorder = state_recorder
    cache_manager = AsyncMock()
    return StartupController(
        cache_manager=cache_manager,
        on_state_change=recorder,
        on_show_toast=MagicMock(),
        on_exit=MagicMock(),
    ), transitions


@pytest.fixture(autouse=True)
def _mock_thread_pool():
    """Mock ThreadPoolManager.run_async to avoid real thread pool operations."""
    with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
        mock_tpm.return_value.run_async = AsyncMock()
        yield mock_tpm


class TestStartupStart:
    @pytest.mark.asyncio
    async def test_start_needs_onboarding(self, controller):
        ctrl, transitions = controller
        with patch("app.startup_controller.check_onboarding_needed", return_value=True):
            await ctrl.start("db", "token", "key", False)

        assert ctrl.state == StartupState.NEED_ONBOARDING
        assert transitions[-1][0] == StartupState.NEED_ONBOARDING

    @pytest.mark.asyncio
    async def test_start_skips_onboarding_and_inits(self, controller):
        ctrl, transitions = controller
        with (
            patch("app.startup_controller.check_onboarding_needed", return_value=False),
            patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init,
        ):
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.start("db", "token", "key", True)

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY

    @pytest.mark.asyncio
    async def test_start_with_empty_db_url_triggers_onboarding(self, controller):
        ctrl, _ = controller
        await ctrl.start("", "token", "key", True)
        assert ctrl.state == StartupState.NEED_ONBOARDING

    @pytest.mark.asyncio
    async def test_start_with_empty_token_triggers_onboarding(self, controller):
        ctrl, _ = controller
        await ctrl.start("db_url", "", "key", True)
        assert ctrl.state == StartupState.NEED_ONBOARDING

    @pytest.mark.asyncio
    async def test_start_with_onboarding_not_complete_triggers_onboarding(self, controller):
        ctrl, _ = controller
        await ctrl.start("db_url", "token", "key", False)
        assert ctrl.state == StartupState.NEED_ONBOARDING


class TestStartupInitServices:
    @pytest.mark.asyncio
    async def test_init_services_success(self, controller):
        ctrl, transitions = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl._init_services()

        assert ctrl.state == StartupState.READY
        assert transitions[-1][0] == StartupState.READY

    @pytest.mark.asyncio
    async def test_init_services_db_upgrade_needed(self, controller):
        ctrl, transitions = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {
                "success": False,
                "error": "db_upgrade_needed",
                "detail": "rev mismatch",
                "current_rev": "abc",
                "head_rev": "def",
            }
            await ctrl._init_services()

        assert ctrl.state == StartupState.NEED_UPGRADE
        assert ctrl.context.detail == "rev mismatch"
        assert ctrl.context.current_rev == "abc"

    @pytest.mark.asyncio
    async def test_init_services_db_init_failed(self, controller):
        ctrl, transitions = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {
                "success": False,
                "error": "db_init_failed",
                "detail": "connection refused",
            }
            await ctrl._init_services()

        assert ctrl.state == StartupState.INIT_FAILED
        assert ctrl.context.error == "db_init_failed"
        assert ctrl.context.detail == "connection refused"

    @pytest.mark.asyncio
    async def test_init_services_db_engine_missing(self, controller):
        ctrl, _ = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {
                "success": False,
                "error": "db_engine_missing",
                "detail": None,
            }
            await ctrl._init_services()

        assert ctrl.state == StartupState.INIT_FAILED
        assert ctrl.context.error == "db_engine_missing"

    @pytest.mark.asyncio
    async def test_init_services_task_manager_init_failed(self, controller):
        ctrl, _ = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {
                "success": False,
                "error": "task_manager_init_failed",
                "detail": "lock error",
            }
            await ctrl._init_services()

        assert ctrl.state == StartupState.INIT_FAILED
        assert ctrl.context.error == "task_manager_init_failed"

    @pytest.mark.asyncio
    async def test_init_services_exception(self, controller):
        ctrl, transitions = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = RuntimeError("unexpected error")
            await ctrl._init_services()

        assert ctrl.state == StartupState.INIT_FAILED
        assert ctrl.context.error == "init_exception"
        assert ctrl.context.detail is not None
        assert "unexpected error" in ctrl.context.detail


class TestStartupRetry:
    @pytest.mark.asyncio
    async def test_retry_re_calls_init_services(self, controller):
        ctrl, _ = controller
        ctrl._state = StartupState.INIT_FAILED

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.retry()

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY


class TestStartupReconfigure:
    @pytest.mark.asyncio
    async def test_reconfigure_closes_db_and_resets_onboarding(self, controller):
        ctrl, transitions = controller
        ctrl._state = StartupState.INIT_FAILED

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            await ctrl.reconfigure()

        ctrl._cache_manager.close.assert_awaited_once()
        mock_tpm.return_value.run_async.assert_awaited_once()
        assert ctrl.state == StartupState.NEED_ONBOARDING
        assert transitions[-1][0] == StartupState.NEED_ONBOARDING

    @pytest.mark.asyncio
    async def test_reconfigure_full_flow(self):
        """Verify reconfigure: close DB -> reset onboarding -> NEED_ONBOARDING."""
        transitions = []
        cache_manager = AsyncMock()
        controller = StartupController(
            cache_manager=cache_manager,
            on_state_change=lambda s, ctx: transitions.append(s),
        )
        controller._state = StartupState.INIT_FAILED

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            await controller.reconfigure()

        assert StartupState.LOADING in transitions
        assert transitions[-1] == StartupState.NEED_ONBOARDING
        cache_manager.close.assert_awaited_once()
        mock_tpm.return_value.run_async.assert_awaited_once()


class TestStartupSkip:
    def test_skip_shows_warning_and_enters_ready(self, controller, _mock_thread_pool):
        ctrl, transitions = controller
        ctrl._state = StartupState.INIT_FAILED

        ctrl.skip()

        assert ctrl.state == StartupState.READY
        ctrl._on_show_toast.assert_called_once_with("warning_skip_db", "warning")
        _mock_thread_pool.return_value.run_async.assert_not_called()


class TestStartupUpgrade:
    @pytest.mark.asyncio
    async def test_upgrade_success(self, controller):
        ctrl, transitions = controller
        ctrl._state = StartupState.NEED_UPGRADE

        await ctrl.upgrade()

        ctrl._cache_manager.init_db.assert_awaited_once_with(force=True, auto_migrate=True)
        assert ctrl.state == StartupState.UPGRADE_SUCCESS

    @pytest.mark.asyncio
    async def test_upgrade_failure(self, controller):
        ctrl, transitions = controller
        ctrl._state = StartupState.NEED_UPGRADE
        ctrl._cache_manager.init_db.side_effect = RuntimeError("migration error")

        await ctrl.upgrade()

        assert ctrl.state == StartupState.UPGRADE_FAILED
        assert ctrl.context.error == "db_upgrade_failed"
        assert ctrl.context.detail is not None
        assert "migration error" in ctrl.context.detail

    @pytest.mark.asyncio
    async def test_proceed_after_upgrade_success_reinits(self, controller):
        ctrl, _ = controller
        ctrl._state = StartupState.UPGRADE_SUCCESS

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.proceed_after_upgrade_success()

        assert ctrl.state == StartupState.READY

    @pytest.mark.asyncio
    async def test_upgrade_retry_calls_upgrade_again(self, controller):
        ctrl, _ = controller
        ctrl._state = StartupState.UPGRADE_FAILED

        await ctrl.upgrade_retry()

        ctrl._cache_manager.init_db.assert_awaited_once_with(force=True, auto_migrate=True)
        assert ctrl.state == StartupState.UPGRADE_SUCCESS

    def test_upgrade_exit_calls_on_exit(self, controller):
        ctrl, _ = controller
        ctrl._state = StartupState.UPGRADE_FAILED

        ctrl.upgrade_exit()

        ctrl._on_exit.assert_called_once()

    @pytest.mark.asyncio
    async def test_upgrade_success_then_proceed(self):
        """Upgrade success -> UPGRADE_SUCCESS -> proceed -> READY."""
        transitions = []
        cache_manager = AsyncMock()
        controller = StartupController(
            cache_manager=cache_manager,
            on_state_change=lambda s, ctx: transitions.append(s),
        )
        controller._state = StartupState.NEED_UPGRADE

        await controller.upgrade()
        assert controller.state == StartupState.UPGRADE_SUCCESS

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await controller.proceed_after_upgrade_success()

        assert controller.state == StartupState.READY
        assert StartupState.UPGRADE_IN_PROGRESS in transitions
        assert StartupState.UPGRADE_SUCCESS in transitions
        assert StartupState.LOADING in transitions
        assert transitions[-1] == StartupState.READY

    @pytest.mark.asyncio
    async def test_upgrade_fail_then_retry_then_success(self):
        """Upgrade fails -> UPGRADE_FAILED -> retry -> UPGRADE_SUCCESS."""
        transitions = []
        cache_manager = AsyncMock()
        controller = StartupController(
            cache_manager=cache_manager,
            on_state_change=lambda s, ctx: transitions.append(s),
        )
        controller._state = StartupState.NEED_UPGRADE

        cache_manager.init_db.side_effect = RuntimeError("migration conflict")
        await controller.upgrade()
        assert controller.state == StartupState.UPGRADE_FAILED
        assert controller.context.detail is not None
        assert "migration conflict" in controller.context.detail

        cache_manager.init_db.side_effect = None
        await controller.upgrade_retry()
        assert controller.state == StartupState.UPGRADE_SUCCESS

    def test_upgrade_fail_then_exit(self):
        """Upgrade fails -> user clicks Exit -> on_exit called."""
        on_exit = MagicMock()
        cache_manager = AsyncMock()
        controller = StartupController(
            cache_manager=cache_manager,
            on_state_change=lambda s, ctx: None,
            on_exit=on_exit,
        )
        controller._state = StartupState.UPGRADE_FAILED

        controller.upgrade_exit()
        on_exit.assert_called_once()


class TestStartupOnboardingComplete:
    @pytest.mark.asyncio
    async def test_onboarding_complete_inits_services(self, controller):
        ctrl, _ = controller
        ctrl._state = StartupState.NEED_ONBOARDING

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.onboarding_complete()

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY
