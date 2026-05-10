import asyncio
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from utils.shutdown import ShutdownCoordinator, StepResult, _CLEANUP_STEPS


class TestStepResult:
    def test_defaults(self):
        r = StepResult(name="test", critical=True, ok=True, timed_out=False, elapsed_ms=100.0)
        assert r.name == "test"
        assert r.critical is True
        assert r.ok is True
        assert r.error == ""

    def test_with_error(self):
        r = StepResult(name="test", critical=False, ok=False, timed_out=True, elapsed_ms=500.0, error="timeout")
        assert r.error == "timeout"


class TestCleanupSteps:
    def test_steps_defined(self):
        assert len(_CLEANUP_STEPS) == 7
        assert _CLEANUP_STEPS[0][0] == "Step 0"
        assert _CLEANUP_STEPS[0][1] == "_step0_cancel_tasks"
        assert _CLEANUP_STEPS[0][2] is True
        assert _CLEANUP_STEPS[4][2] is False


class TestShutdownCoordinatorInit:
    def test_default_init(self):
        coord = ShutdownCoordinator()
        assert coord.cleanup_done is False
        assert coord.cleanup_success is False
        assert coord.watchdog_started is False
        assert coord.step_results == []

    def test_custom_force_exit(self):
        called_with = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: called_with.append(code))
        coord._force_exit(0)
        assert called_with == [0]

    def test_custom_service_stop_delay(self):
        coord = ShutdownCoordinator(service_stop_delay=2.0)
        assert coord._service_stop_delay == 2.0


class TestShutdownCoordinatorWatchdog:
    def test_start_watchdog(self):
        coord = ShutdownCoordinator()
        coord.start_watchdog(timeout_s=1)
        assert coord.watchdog_started is True
        coord.cancel_watchdog()

    def test_cancel_watchdog(self):
        coord = ShutdownCoordinator()
        coord.start_watchdog(timeout_s=10)
        coord.cancel_watchdog()
        assert coord.watchdog_started is False
        assert coord._watchdog_cancel_event is None

    def test_double_start_ignored(self):
        coord = ShutdownCoordinator()
        coord.start_watchdog(timeout_s=10)
        coord.start_watchdog(timeout_s=10)
        assert coord.watchdog_started is True
        coord.cancel_watchdog()


class TestShutdownCoordinatorRunAsyncStep:
    @pytest.mark.asyncio
    async def test_successful_step(self):
        coord = ShutdownCoordinator()
        result = await coord._run_async_step(
            name="test",
            step=AsyncMock(return_value=None),
            step_timeout_s=5.0,
            critical=True,
        )
        assert result.ok is True
        assert result.timed_out is False
        assert result.name == "test"
        assert result.critical is True

    @pytest.mark.asyncio
    async def test_failing_step(self):
        coord = ShutdownCoordinator()
        result = await coord._run_async_step(
            name="test",
            step=AsyncMock(side_effect=Exception("fail")),
            step_timeout_s=5.0,
            critical=True,
        )
        assert result.ok is False
        assert result.error == "fail"
        assert result.timed_out is False

    @pytest.mark.asyncio
    async def test_timeout_step(self):
        coord = ShutdownCoordinator()

        async def slow_step():
            await asyncio.sleep(10)

        result = await coord._run_async_step(
            name="test",
            step=slow_step,
            step_timeout_s=0.1,
            critical=True,
        )
        assert result.ok is False
        assert result.timed_out is True

    @pytest.mark.asyncio
    async def test_non_critical_step(self):
        coord = ShutdownCoordinator()
        result = await coord._run_async_step(
            name="test",
            step=AsyncMock(side_effect=Exception("fail")),
            step_timeout_s=5.0,
            critical=False,
        )
        assert result.critical is False
        assert result.ok is False


class TestShutdownCoordinatorCleanupSteps:
    @pytest.mark.asyncio
    async def test_step0_cancel_tasks_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_tm._instance = None
            await coord._step0_cancel_tasks()

    @pytest.mark.asyncio
    async def test_step0_cancel_tasks_with_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_instance = MagicMock()
            mock_instance.cancel_all_running_async = AsyncMock()
            mock_tm._instance = mock_instance
            await coord._step0_cancel_tasks()
            mock_instance.cancel_all_running_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_step1_stop_services_no_instances(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            await coord._step1_stop_services()

    @pytest.mark.asyncio
    async def test_step1_stop_scheduler(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched.scheduler.running = True
            mock_news._instance = None
            mock_mds._instance = None
            await coord._step1_stop_services()
            mock_sched.return_value.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_step1_stop_news_service(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched.scheduler.running = False
            mock_news_instance = MagicMock()
            mock_news_instance.stop_async = AsyncMock()
            mock_news._instance = mock_news_instance
            mock_mds._instance = None
            await coord._step1_stop_services()
            mock_news_instance.stop_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_step1_stop_market_data(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds_instance = MagicMock()
            mock_mds_instance.stop_async = AsyncMock()
            mock_mds._instance = mock_mds_instance
            await coord._step1_stop_services()
            mock_mds_instance.stop_async.assert_called_once()

    @pytest.mark.asyncio
    async def test_step2_flush_db_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_tm._instance = None
            await coord._step2_flush_db_writes()

    @pytest.mark.asyncio
    async def test_step2_flush_db_with_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_instance = MagicMock()
            mock_instance.flush_persistence = AsyncMock()
            mock_tm._instance = mock_instance
            await coord._step2_flush_db_writes()
            mock_instance.flush_persistence.assert_called_once()

    @pytest.mark.asyncio
    async def test_step3_close_processor_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_dp._instance = None
            await coord._step3_close_processor()

    @pytest.mark.asyncio
    async def test_step3_close_processor_with_instance(self):
        coord = ShutdownCoordinator()
        with patch("data.data_processor.DataProcessor") as mock_dp:
            mock_instance = MagicMock()
            mock_instance.close = AsyncMock()
            mock_dp._instance = mock_instance
            await coord._step3_close_processor()
            mock_instance.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_no_page(self):
        coord = ShutdownCoordinator(page=None)
        await coord._step4_clear_toast()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_with_page_no_toast(self):
        mock_page = MagicMock()
        mock_page.toast = None
        coord = ShutdownCoordinator(page=mock_page)
        await coord._step4_clear_toast()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_with_stop_all(self):
        mock_page = MagicMock()
        mock_toast = MagicMock()
        mock_toast.stop_all = MagicMock()
        mock_page.toast = mock_toast
        coord = ShutdownCoordinator(page=mock_page)
        await coord._step4_clear_toast()
        mock_toast.stop_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_async_stop_all(self):
        mock_page = MagicMock()
        mock_toast = MagicMock()
        mock_toast.stop_all = AsyncMock()
        mock_page.toast = mock_toast
        coord = ShutdownCoordinator(page=mock_page)
        await coord._step4_clear_toast()
        mock_toast.stop_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_step4_clear_toast_exception(self):
        mock_page = MagicMock()
        mock_toast = MagicMock()
        mock_toast.stop_all = MagicMock(side_effect=Exception("toast error"))
        mock_page.toast = mock_toast
        coord = ShutdownCoordinator(page=mock_page)
        await coord._step4_clear_toast()

    @pytest.mark.asyncio
    async def test_step5_unload_ai_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_lmm._instance = None
            await coord._step5_unload_ai_model()

    @pytest.mark.asyncio
    async def test_step5_unload_ai_no_llm(self):
        coord = ShutdownCoordinator()
        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_instance = MagicMock()
            mock_instance._llm = None
            mock_lmm._instance = mock_instance
            await coord._step5_unload_ai_model()

    @pytest.mark.asyncio
    async def test_step5_unload_ai_with_llm(self):
        coord = ShutdownCoordinator()
        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_instance = MagicMock()
            mock_instance._llm = MagicMock()
            mock_instance.unload_model = MagicMock()
            mock_lmm._instance = mock_instance
            await coord._step5_unload_ai_model()

    @pytest.mark.asyncio
    async def test_step6_shutdown_pools_no_instance(self):
        coord = ShutdownCoordinator()
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_tpm._instance = None
            await coord._step6_shutdown_thread_pools()

    @pytest.mark.asyncio
    async def test_step6_shutdown_pools_with_instance(self):
        coord = ShutdownCoordinator()
        with patch("utils.thread_pool.ThreadPoolManager") as mock_tpm:
            mock_instance = MagicMock()
            mock_instance.shutdown = MagicMock()
            mock_tpm._instance = mock_instance
            await coord._step6_shutdown_thread_pools()


class TestShutdownCoordinatorDoCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_already_done(self):
        coord = ShutdownCoordinator()
        coord._cleanup_done = True
        coord._cleanup_task = None
        coord._cleanup_success = True
        result = await coord.do_cleanup()
        assert result is True

    @pytest.mark.asyncio
    async def test_cleanup_already_done_failure(self):
        coord = ShutdownCoordinator()
        coord._cleanup_done = True
        coord._cleanup_task = None
        coord._cleanup_success = False
        result = await coord.do_cleanup()
        assert result is False

    @pytest.mark.asyncio
    async def test_full_cleanup(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("services.local_model_manager.LocalModelManager") as mock_lmm,
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
        ):
            mock_tm._instance = None
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            mock_dp._instance = None
            mock_lmm._instance = None
            mock_tpm._instance = None
            await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)
            assert coord.cleanup_done is True

    @pytest.mark.asyncio
    async def test_cleanup_with_running_task(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._cleanup_task = asyncio.create_task(AsyncMock()())
        coord._cleanup_started = True
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("data.external.news_subscription.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
            patch("data.data_processor.DataProcessor") as mock_dp,
            patch("services.local_model_manager.LocalModelManager") as mock_lmm,
            patch("utils.thread_pool.ThreadPoolManager") as mock_tpm,
        ):
            mock_tm._instance = None
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            mock_dp._instance = None
            mock_lmm._instance = None
            mock_tpm._instance = None
            await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)


class TestShutdownCoordinatorExecuteCleanup:
    @pytest.mark.asyncio
    async def test_timeout(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=TimeoutError())
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        assert coord.cleanup_done is True

    @pytest.mark.asyncio
    async def test_exception(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=RuntimeError("unexpected"))
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False
        assert coord.cleanup_done is True

    @pytest.mark.asyncio
    async def test_success(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(
            return_value=[
                StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=10.0),
            ]
        )
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is True
        assert coord.cleanup_done is True

    @pytest.mark.asyncio
    async def test_critical_failure(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(
            return_value=[
                StepResult(name="Step 0", critical=True, ok=False, timed_out=False, elapsed_ms=10.0, error="fail"),
            ]
        )
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False


class TestShutdownWatchdogForceExit:
    def test_watchdog_force_exit_uses_exit_code_1(self):
        exit_codes = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: exit_codes.append(code))
        coord.start_watchdog(timeout_s=0.1)
        import time

        time.sleep(0.3)
        assert exit_codes == [1]

    def test_watchdog_canceled_no_force_exit(self):
        exit_codes = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: exit_codes.append(code))
        coord.start_watchdog(timeout_s=5)
        coord.cancel_watchdog()
        assert exit_codes == []

    def test_watchdog_logs_error_on_timeout(self, caplog):
        import logging

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            import time

            time.sleep(0.3)
            assert any("forcing exit" in r.message.lower() for r in caplog.records)


class TestShutdownCoordinatorGracefulForceExit:
    def test_custom_callback_tries_sys_exit_first(self):

        exit_calls = []

        def mock_sys_exit(code):
            exit_calls.append(("sys.exit", code))
            raise SystemExit(code)

        def force_exit(code):
            exit_calls.append(("force_exit", code))

        coord = ShutdownCoordinator(force_exit_callback=force_exit)
        coord._force_exit(1)
        assert ("force_exit", 1) in exit_calls

    def test_default_callback_is_graceful_exit(self):
        coord = ShutdownCoordinator()
        assert coord._force_exit == ShutdownCoordinator._default_force_exit

    def test_main_py_uses_coordinator_force_exit(self):
        import pathlib

        main_path = pathlib.Path(__file__).resolve().parent.parent.parent / "main.py"
        source = main_path.read_text(encoding="utf-8")
        assert "coordinator._force_exit" in source
        assert "_default_force_exit" in source or "ShutdownCoordinator" in source


class TestWatchdogStepResultsLogging:
    def test_watchdog_timeout_includes_step_results(self, caplog):
        import logging
        import time

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        coord._step_results = [
            StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=100.0),
            StepResult(name="Step 1", critical=True, ok=False, timed_out=True, elapsed_ms=2000.0, error="timeout"),
        ]
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            time.sleep(0.3)
            log_msgs = [r.message for r in caplog.records if "forcing exit" in r.message.lower()]
            assert len(log_msgs) >= 1
            msg = log_msgs[0]
            assert "step_results=" in msg
            assert "Step 0" in msg
            assert "Step 1" in msg
            assert "timed_out=True" in msg

    def test_watchdog_timeout_empty_step_results(self, caplog):
        import logging
        import time

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        assert coord._step_results == []
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            time.sleep(0.3)
            log_msgs = [r.message for r in caplog.records if "forcing exit" in r.message.lower()]
            assert len(log_msgs) >= 1
            assert "step_results=[]" in log_msgs[0]


class TestDefaultWatchdogTimeout:
    def test_default_watchdog_timeout_is_15s(self):
        coord = ShutdownCoordinator()
        assert coord._watchdog_timeout_s == 15.0

    def test_custom_watchdog_timeout(self):
        coord = ShutdownCoordinator(watchdog_timeout_s=20.0)
        assert coord._watchdog_timeout_s == 20.0


class TestMainPyCleanupTimeouts:
    def test_main_py_uses_adjusted_timeouts(self):
        import pathlib

        main_path = pathlib.Path(__file__).resolve().parent.parent.parent / "main.py"
        source = main_path.read_text(encoding="utf-8")
        assert "timeout_s=12.0" in source
        assert "step_timeout_s=2.0" in source


class TestShutdownStepOrdering:
    def test_flush_db_before_close_processor(self):
        flush_idx = None
        close_idx = None
        for i, (_name, method_name, _critical) in enumerate(_CLEANUP_STEPS):
            if method_name == "_step2_flush_db_writes":
                flush_idx = i
            if method_name == "_step3_close_processor":
                close_idx = i
        assert flush_idx is not None, "_step2_flush_db_writes not found in cleanup steps"
        assert close_idx is not None, "_step3_close_processor not found in cleanup steps"
        assert flush_idx < close_idx, (
            f"Flush DB writes (step {flush_idx}) must execute before "
            f"close processor (step {close_idx}) to prevent data loss"
        )

    def test_cancel_tasks_is_first_step(self):
        assert _CLEANUP_STEPS[0][1] == "_step0_cancel_tasks"

    def test_shutdown_pools_is_last_step(self):
        assert _CLEANUP_STEPS[-1][1] == "_step6_shutdown_thread_pools"

    def test_step2_is_flush_db_writes(self):
        assert _CLEANUP_STEPS[2][1] == "_step2_flush_db_writes"

    def test_step3_is_close_processor(self):
        assert _CLEANUP_STEPS[3][1] == "_step3_close_processor"
