"""Unit tests for StartupController — zero Flet mocks, pure business logic."""

import asyncio

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

    @pytest.mark.asyncio
    async def test_init_services_set_onboarding_complete_failure_continues_to_ready(self, controller):
        """app-004 举一反三: set_onboarding_complete(True) 失败时仍 transition 到 READY.

        服务已初始化成功，仅 onboarding_complete 标志未持久化，不阻塞启动。
        """
        ctrl, transitions = controller
        with (
            patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init,
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            mock_tpm.return_value.run_async = AsyncMock(side_effect=RuntimeError("disk full"))
            await ctrl._init_services()

        assert ctrl.state == StartupState.READY
        assert transitions[-1][0] == StartupState.READY

    @pytest.mark.asyncio
    async def test_init_services_passes_services_initialized_and_sets_after_success(self, controller):
        """review01-A9: 首次初始化传 services_initialized=False，成功后再调用传 True。"""
        ctrl, _ = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}

            await ctrl._init_services()  # 首次
            first_kwargs = mock_init.call_args.kwargs
            assert first_kwargs["services_initialized"] is False
            assert ctrl._services_initialized is True

            mock_init.reset_mock()
            await ctrl._init_services()  # 同 session 二次调用
            second_kwargs = mock_init.call_args.kwargs
            assert second_kwargs["services_initialized"] is True

    @pytest.mark.asyncio
    async def test_init_services_failure_does_not_set_services_initialized(self, controller):
        """review01-A9: 初始化失败不置位 per-session 状态，允许重试。"""
        ctrl, _ = controller
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {
                "success": False,
                "error": "db_init_failed",
                "detail": "connection refused",
            }
            await ctrl._init_services()

        assert ctrl.state == StartupState.INIT_FAILED
        assert ctrl._services_initialized is False

    @pytest.mark.asyncio
    async def test_init_services_two_controllers_are_isolated(self, controller, state_recorder):
        """review01-A9: 两个 controller 实例（per-session）状态相互隔离。"""
        transitions, recorder = state_recorder
        ctrl1, _ = controller
        ctrl2 = StartupController(
            cache_manager=AsyncMock(),
            on_state_change=recorder,
            on_show_toast=MagicMock(),
            on_exit=MagicMock(),
        )

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl1._init_services()  # ctrl1 初始化成功
            mock_init.reset_mock()

            # ctrl2 首次初始化仍应传 False（per-session 隔离，不受 ctrl1 影响）
            await ctrl2._init_services()
            assert mock_init.call_args.kwargs["services_initialized"] is False
            assert ctrl1._services_initialized is True
            assert ctrl2._services_initialized is True


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

    @pytest.mark.asyncio
    async def test_retry_reentry_guard_ignores_duplicate(self, controller):
        """M-UI-04 (M-APP-01 复核裁定扩展): Retry 防重入.

        首次调用成功后 state 已 transition 到 READY；重渲染 patch 下发前的
        双击窗口内第二次调用应被忽略，不再触发 initialize_services
        （避免并发重入击穿 services_initialized 幂等保护）。
        """
        ctrl, transitions = controller
        ctrl._state = StartupState.INIT_FAILED

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.retry()
            # 模拟双击窗口内的第二次点击（state 已非 INIT_FAILED）
            await ctrl.retry()

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY
        assert [s for s, _ in transitions].count(StartupState.LOADING) == 1

    @pytest.mark.asyncio
    async def test_retry_concurrent_double_click_inits_once(self, controller):
        """并发双击（gather 模拟）：第二次调用到达时 state 已 LOADING，被守卫忽略.

        精确复现竞态窗口：首个 retry 经 _init_services 同步段 transition 到
        LOADING 后在 initialize_services 处挂起，第二个 retry 启动时守卫拦截。
        """
        ctrl, _ = controller
        ctrl._state = StartupState.INIT_FAILED

        async def slow_init(*args, **kwargs):
            await asyncio.sleep(0)  # 模拟 initialize_services 挂起，制造重入窗口
            return {"success": True, "error": None, "detail": None}

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.side_effect = slow_init
            await asyncio.gather(ctrl.retry(), ctrl.retry())

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY

    @pytest.mark.asyncio
    async def test_retry_ignored_when_not_in_init_failed_state(self, controller):
        """非 INIT_FAILED 状态（如 READY/LOADING）下调用直接忽略."""
        ctrl, _ = controller
        ctrl._state = StartupState.READY

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            await ctrl.retry()

        mock_init.assert_not_awaited()
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
    async def test_reconfigure_resets_services_initialized(self, controller):
        """review01-A9: reconfigure 重置 per-session 初始化状态，允许重新初始化。"""
        ctrl, _ = controller
        ctrl._state = StartupState.INIT_FAILED  # Reconfigure 按钮仅在 INIT_FAILED 渲染
        ctrl._services_initialized = True  # 模拟已初始化

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            await ctrl.reconfigure()

        assert ctrl._services_initialized is False

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

    @pytest.mark.asyncio
    async def test_reconfigure_set_onboarding_complete_failure_transitions_to_init_failed(self):
        """app-004: set_onboarding_complete(False) 失败时 transition 到 INIT_FAILED，避免停留 LOADING."""
        transitions = []
        cache_manager = AsyncMock()
        controller = StartupController(
            cache_manager=cache_manager,
            on_state_change=lambda s, ctx: transitions.append((s, ctx)),
        )
        controller._state = StartupState.INIT_FAILED

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock(side_effect=OSError("disk full"))
            await controller.reconfigure()

        # 状态机应 transition 到 INIT_FAILED，而非停留 LOADING
        assert controller.state == StartupState.INIT_FAILED
        assert transitions[-1][0] == StartupState.INIT_FAILED
        assert transitions[-1][1].error == "reconfigure_failed"
        assert transitions[-1][1].detail is not None
        # cache_manager.close 仍应被调用
        cache_manager.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reconfigure_reentry_guard_ignores_duplicate(self, controller):
        """M-UI-04 (M-APP-01 复核裁定扩展): Reconfigure 防重入.

        首次调用同步段已 transition 到 LOADING；双击窗口内第二次调用应被
        忽略，避免重复 close DB / 重置 onboarding 标志。
        """
        ctrl, transitions = controller
        ctrl._state = StartupState.INIT_FAILED

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            await ctrl.reconfigure()
            # 模拟双击窗口内的第二次点击（state 已非 INIT_FAILED）
            await ctrl.reconfigure()

        ctrl._cache_manager.close.assert_awaited_once()
        mock_tpm.return_value.run_async.assert_awaited_once()
        assert ctrl.state == StartupState.NEED_ONBOARDING
        assert [s for s, _ in transitions].count(StartupState.LOADING) == 1

    @pytest.mark.asyncio
    async def test_reconfigure_concurrent_double_click_closes_once(self, controller):
        """并发双击（gather 模拟）：第二次调用到达时 state 已 LOADING，被守卫忽略.

        精确复现竞态窗口：首个 reconfigure 同步段 transition 到 LOADING 后在
        close 处挂起，第二个 reconfigure 启动时守卫拦截。
        """
        ctrl, _ = controller
        ctrl._state = StartupState.INIT_FAILED

        async def slow_close():
            await asyncio.sleep(0)  # 模拟 close 挂起，制造重入窗口
            return None

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            ctrl._cache_manager.close.side_effect = slow_close
            await asyncio.gather(ctrl.reconfigure(), ctrl.reconfigure())

        ctrl._cache_manager.close.assert_awaited_once()
        mock_tpm.return_value.run_async.assert_awaited_once()
        assert ctrl.state == StartupState.NEED_ONBOARDING

    @pytest.mark.asyncio
    async def test_reconfigure_ignored_when_not_in_init_failed_state(self, controller):
        """非 INIT_FAILED 状态（如 READY/NEED_ONBOARDING）下调用直接忽略."""
        ctrl, _ = controller
        ctrl._state = StartupState.READY

        with (
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
            patch("utils.config_handler.ConfigHandler"),
        ):
            mock_tpm.return_value.run_async = AsyncMock()
            await ctrl.reconfigure()

        ctrl._cache_manager.close.assert_not_awaited()
        assert ctrl.state == StartupState.READY


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

    @pytest.mark.asyncio
    async def test_onboarding_complete_reentry_guard_ignores_duplicate(self, controller):
        """M-UI-04 (UI-15 移交深验): complete 步重复点击防重入.

        首次调用后 state 已 transition 到 LOADING（_init_services 同步段）；
        重渲染 patch 下发前的双击窗口内第二次调用应被忽略，不再触发
        initialize_services（避免并发重入击穿 services_initialized 幂等保护）。
        """
        ctrl, transitions = controller
        ctrl._state = StartupState.NEED_ONBOARDING

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl.onboarding_complete()
            # 模拟双击窗口内的第二次点击（state 已是 LOADING）
            await ctrl.onboarding_complete()

        mock_init.assert_awaited_once()
        assert ctrl.state == StartupState.READY
        assert [s for s, _ in transitions].count(StartupState.LOADING) == 1

    @pytest.mark.asyncio
    async def test_onboarding_complete_ignored_when_not_in_onboarding_state(self, controller):
        """非 NEED_ONBOARDING 状态（如 READY/LOADING）下调用直接忽略."""
        ctrl, _ = controller
        ctrl._state = StartupState.READY

        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            await ctrl.onboarding_complete()

        mock_init.assert_not_awaited()
        assert ctrl.state == StartupState.READY


class TestStartupEmbeddedPgScenario:
    """UX 改进 spec §启动侧方案 A：embedded_pg_scenario 传递与跨状态保留."""

    def test_default_scenario_is_none(self, controller):
        ctrl, _ = controller
        assert ctrl.context.embedded_pg_scenario is None

    def test_scenario_injected_to_initial_context(self):
        from app.bootstrap import EmbeddedPgStartupScenario

        ctrl = StartupController(
            cache_manager=AsyncMock(),
            on_state_change=lambda s, ctx: None,
            embedded_pg_scenario=EmbeddedPgStartupScenario.FIRST_RUN,
        )
        assert ctrl.context.embedded_pg_scenario == EmbeddedPgStartupScenario.FIRST_RUN

    def test_scenario_preserved_across_transition(self):
        """_transition 不重置 embedded_pg_scenario（未显式传入时保留旧值）."""
        from app.bootstrap import EmbeddedPgStartupScenario

        ctrl = StartupController(
            cache_manager=AsyncMock(),
            on_state_change=lambda s, ctx: None,
            embedded_pg_scenario=EmbeddedPgStartupScenario.NORMAL,
        )
        ctrl._transition(StartupState.NEED_UPGRADE, error="db_upgrade_needed", detail="rev mismatch")
        assert ctrl.context.embedded_pg_scenario == EmbeddedPgStartupScenario.NORMAL
        assert ctrl.context.error == "db_upgrade_needed"

    def test_scenario_explicit_override_in_transition(self):
        """_transition 显式传入 embedded_pg_scenario 时覆盖旧值（用于测试或重置）."""
        from app.bootstrap import EmbeddedPgStartupScenario

        ctrl = StartupController(
            cache_manager=AsyncMock(),
            on_state_change=lambda s, ctx: None,
            embedded_pg_scenario=EmbeddedPgStartupScenario.NORMAL,
        )
        ctrl._transition(
            StartupState.INIT_FAILED,
            error="db_init_failed",
            embedded_pg_scenario=EmbeddedPgStartupScenario.FIRST_RUN,
        )
        assert ctrl.context.embedded_pg_scenario == EmbeddedPgStartupScenario.FIRST_RUN

    @pytest.mark.asyncio
    async def test_scenario_preserved_through_init_services_flow(self):
        """端到端：scenario 经 __init__ 注入后，_init_services 多次 _transition 仍保留."""
        from app.bootstrap import EmbeddedPgStartupScenario

        ctrl = StartupController(
            cache_manager=AsyncMock(),
            on_state_change=lambda s, ctx: None,
            embedded_pg_scenario=EmbeddedPgStartupScenario.FIRST_RUN,
        )
        with patch("app.startup_controller.initialize_services", new_callable=AsyncMock) as mock_init:
            mock_init.return_value = {"success": True, "error": None, "detail": None}
            await ctrl._init_services()

        assert ctrl.state == StartupState.READY
        assert ctrl.context.embedded_pg_scenario == EmbeddedPgStartupScenario.FIRST_RUN
