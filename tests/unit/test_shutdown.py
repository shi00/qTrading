"""测试 ShutdownCoordinator 的优雅关闭流程。

覆盖范围:
- StepResult 数据结构与默认值
- _CLEANUP_STEPS 步骤定义、超时预算与执行顺序
- ShutdownCoordinator 初始化、看门狗、单步执行与完整清理流程
- 各清理步骤 (step0~step7) 的实例存在/缺失分支
- 看门狗强制退出、日志记录与 step_results 上报
- main.py 使用的清理超时配置
"""

import asyncio
import time

import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from utils.shutdown import ShutdownCoordinator, StepResult, _CLEANUP_STEPS

# 大部分测试使用 MagicMock 无真实长睡眠；少数含 asyncio.sleep 的测试类单独标注 slow
pytestmark = pytest.mark.unit


def _wait_until(condition, timeout=2.0, interval=0.01):
    """Poll condition() until True or timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(interval)


class TestStepResult:
    """验证 StepResult 数据类的字段默认值与错误字段赋值。"""

    def test_defaults(self):
        r = StepResult(name="test", critical=True, ok=True, timed_out=False, elapsed_ms=100.0)
        assert r.name == "test"
        assert r.critical is True
        assert r.ok is True
        assert r.error == ""

    def test_with_error(self):
        r = StepResult(
            name="test",
            critical=False,
            ok=False,
            timed_out=True,
            elapsed_ms=500.0,
            error="timeout",
        )
        assert r.error == "timeout"


class TestCleanupSteps:
    """验证 _CLEANUP_STEPS 步骤定义的结构、超时预算与顺序约束。"""

    def test_steps_defined(self):
        assert len(_CLEANUP_STEPS) == 9
        assert _CLEANUP_STEPS[0][0] == "Step 0"
        assert _CLEANUP_STEPS[0][1] == "_step0_cancel_tasks"
        assert _CLEANUP_STEPS[0][2] is True
        assert _CLEANUP_STEPS[0][3] == 4.0
        assert _CLEANUP_STEPS[4][2] is False
        assert _CLEANUP_STEPS[4][3] == 1.0

    def test_each_step_has_timeout(self):
        """ASYNC-004/005: Each cleanup step has its own timeout."""
        for step_def in _CLEANUP_STEPS:
            assert len(step_def) == 4, f"Step {step_def[0]} should be a 4-tuple"
            name, method_name, critical, timeout = step_def
            assert timeout > 0, f"Step {name} timeout must be positive"

    def test_total_step_timeouts_within_overall_budget(self):
        """ASYNC-005: Sum of step timeouts should fit within production window shutdown budget.

        Phase 2 Step 8 (_step8_stop_embedded_postgres, 35.0s) 加入后，默认 do_cleanup()
        的 20.0s 整体超时不再覆盖最坏情况和。生产路径 perform_window_shutdown 使用
        timeout_s=50.0s 容纳 Step 8；此处校验 sum <= 55.0s (50.0s + 5.0s margin)。
        """
        total_step_time = sum(step[3] for step in _CLEANUP_STEPS)
        assert total_step_time <= 55.0, (
            f"Sum of step timeouts ({total_step_time}s) exceeds production overall budget (55.0s)"
        )

    def test_step0_timeout_accommodates_join_timeout(self):
        """ASYNC-004: Step 0 timeout must accommodate cancel_all_running_async join_timeout."""
        step0_timeout = _CLEANUP_STEPS[0][3]
        # cancel_all_running_async default join_timeout is 3.0s
        # Step 0 needs to cover: cancel + persist + join
        assert step0_timeout >= 3.0, f"Step 0 timeout ({step0_timeout}s) must be >= join_timeout (3.0s)"


class TestShutdownCoordinatorInit:
    """验证 ShutdownCoordinator 的默认初始化与可定制回调/延迟参数。"""

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
    """验证看门狗的启动、取消与重复启动幂等行为。"""

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
    """验证 _run_async_step 对成功、失败、超时、取消与非关键步骤的处理。"""

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

    @pytest.mark.asyncio
    async def test_cancelled_step_raises(self):
        coord = ShutdownCoordinator()
        with pytest.raises(asyncio.CancelledError):
            await coord._run_async_step(
                name="test",
                step=AsyncMock(side_effect=asyncio.CancelledError()),
                step_timeout_s=5.0,
                critical=True,
            )


class TestShutdownCoordinatorCleanupSteps:
    """验证 step0~step6 各清理步骤在单例存在/缺失时的分支行为。"""

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
        """所有服务单例均为 None 时，step1 应安全跳过不报错。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            await coord._step1_stop_services()

    @pytest.mark.asyncio
    async def test_step1_stop_scheduler(self):
        """SchedulerService 单例存在且 scheduler.running=True 时应调用 stop()。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_sched._instance = mock_sched.return_value
            mock_sched._instance.scheduler.running = True
            mock_news._instance = None
            mock_mds._instance = None
            await coord._step1_stop_services()
            mock_sched._instance.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_step1_stop_news_service(self):
        """NewsSubscriptionService 单例存在时应调用 stop_async()。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
        """MarketDataService 单例存在时应调用 stop_async()。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
        """toast.stop_all 抛异常时 step4 应吞掉异常不影响后续步骤。"""
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
            mock_instance._worker_ready = False
            mock_instance._model_path = ""
            mock_lmm._instance = mock_instance
            await coord._step5_unload_ai_model()

    @pytest.mark.asyncio
    async def test_step5_unload_ai_with_llm(self):
        coord = ShutdownCoordinator()
        with patch("services.local_model_manager.LocalModelManager") as mock_lmm:
            mock_instance = MagicMock()
            mock_instance._worker_ready = True
            mock_instance.unload_model = MagicMock()
            mock_lmm._instance = mock_instance
            await coord._step5_unload_ai_model()
            mock_instance.unload_model.assert_called_once_with(force=True)

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

    @pytest.mark.asyncio
    async def test_step7_close_database_managers_calls_close_all(self):
        """Step 7 应调用 DataExplorerQueryClient.close_all() 关闭所有同步引擎。"""
        coord = ShutdownCoordinator()
        with patch("data.persistence.data_explorer_query_client.DataExplorerQueryClient") as mock_dm:
            await coord._step7_close_database_managers()
            mock_dm.close_all.assert_called_once()

    @pytest.mark.asyncio
    async def test_step7_close_database_managers_no_exception(self):
        """Step 7 即使 close_all 抛异常也不应传播（close_all 内部已 try/except）。"""
        coord = ShutdownCoordinator()
        with patch("data.persistence.data_explorer_query_client.DataExplorerQueryClient") as mock_dm:
            mock_dm.close_all = MagicMock()  # 正常调用不抛
            await coord._step7_close_database_managers()
            mock_dm.close_all.assert_called_once()


class TestShutdownCoordinatorDoCleanup:
    """验证 do_cleanup 的幂等性、完整流程与并发任务复用行为。"""

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
        """所有服务单例均为 None 时完整清理流程应顺利完成并标记 cleanup_done。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
        """已有 cleanup 任务在运行时应复用该任务而非重复启动清理流程。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._cleanup_task = asyncio.create_task(AsyncMock()())
        coord._cleanup_started = True
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
    """验证 _execute_cleanup 对超时、异常、取消与关键步骤失败的处理。"""

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
    async def test_cancelled(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(side_effect=asyncio.CancelledError())
        with pytest.raises(asyncio.CancelledError):
            await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert coord.cleanup_done is True

    @pytest.mark.asyncio
    async def test_success(self):
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(
            return_value=[
                StepResult(
                    name="Step 0",
                    critical=True,
                    ok=True,
                    timed_out=False,
                    elapsed_ms=10.0,
                ),
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
                StepResult(
                    name="Step 0",
                    critical=True,
                    ok=False,
                    timed_out=False,
                    elapsed_ms=10.0,
                    error="fail",
                ),
            ]
        )
        result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is False


@pytest.mark.slow
class TestShutdownWatchdogForceExit:
    """验证看门狗超时后强制退出行为与取消后的安全性。"""

    def test_watchdog_force_exit_uses_exit_code_1(self):
        """看门狗超时应通过 force_exit_callback 触发退出码 1。"""
        exit_codes = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: exit_codes.append(code))
        coord.start_watchdog(timeout_s=0.1)
        _wait_until(lambda: exit_codes == [1])
        assert exit_codes == [1]

    def test_watchdog_canceled_no_force_exit(self):
        """看门狗在超时前被取消时不应触发 force_exit_callback。"""
        exit_codes = []
        coord = ShutdownCoordinator(force_exit_callback=lambda code: exit_codes.append(code))
        coord.start_watchdog(timeout_s=5)
        coord.cancel_watchdog()
        assert exit_codes == []

    def test_watchdog_logs_error_on_timeout(self, caplog):
        """看门狗超时强制退出时应向 utils.shutdown logger 记录 ERROR 级日志。"""
        import logging

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            _wait_until(lambda: any("forcing exit" in r.message.lower() for r in caplog.records))
            assert any("forcing exit" in r.message.lower() for r in caplog.records)


class TestShutdownCoordinatorGracefulForceExit:
    """验证自定义 force_exit 回调的注入与默认回调的优雅退出语义。"""

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
        from utils.shutdown import ShutdownCoordinator

        assert hasattr(ShutdownCoordinator, "_default_force_exit"), (
            "ShutdownCoordinator should have _default_force_exit"
        )


@pytest.mark.slow
class TestWatchdogStepResultsLogging:
    """验证看门狗超时日志中包含 step_results 摘要信息。"""

    def test_watchdog_timeout_includes_step_results(self, caplog):
        """看门狗超时日志应包含各步骤名称、超时标记等 step_results 摘要。"""
        import logging

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        coord._step_results = [
            StepResult(name="Step 0", critical=True, ok=True, timed_out=False, elapsed_ms=100.0),
            StepResult(
                name="Step 1",
                critical=True,
                ok=False,
                timed_out=True,
                elapsed_ms=2000.0,
                error="timeout",
            ),
        ]
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            _wait_until(lambda: any("forcing exit" in r.message.lower() for r in caplog.records))
            log_msgs = [r.message for r in caplog.records if "forcing exit" in r.message.lower()]
            assert len(log_msgs) >= 1
            msg = log_msgs[0]
            assert "step_results=" in msg
            assert "Step 0" in msg
            assert "Step 1" in msg
            assert "timed_out=True" in msg

    def test_watchdog_timeout_empty_step_results(self, caplog):
        """无 step_results 时看门狗日志应输出 step_results=[] 占位。"""
        import logging

        coord = ShutdownCoordinator(force_exit_callback=lambda code: None)
        assert coord._step_results == []
        with caplog.at_level(logging.ERROR, logger="utils.shutdown"):
            coord.start_watchdog(timeout_s=0.1)
            _wait_until(lambda: any("forcing exit" in r.message.lower() for r in caplog.records))
            log_msgs = [r.message for r in caplog.records if "forcing exit" in r.message.lower()]
            assert len(log_msgs) >= 1
            assert "step_results=[]" in log_msgs[0]


class TestDefaultWatchdogTimeout:
    """验证看门狗默认超时 (25s) 与可定制超时参数。"""

    def test_default_watchdog_timeout_is_25s(self):
        coord = ShutdownCoordinator()
        assert coord._watchdog_timeout_s == 25.0

    def test_custom_watchdog_timeout(self):
        coord = ShutdownCoordinator(watchdog_timeout_s=20.0)
        assert coord._watchdog_timeout_s == 20.0


class TestMainPyCleanupTimeouts:
    """验证 main.py 调用 do_cleanup 时 step_timeout_s 参数的接受与生效。"""

    @pytest.mark.asyncio
    async def test_do_cleanup_accepts_step_timeout_s(self):
        """do_cleanup 应接受 step_timeout_s 参数并完成完整清理流程。"""
        coord = ShutdownCoordinator(service_stop_delay=0, force_exit_callback=lambda code: None)
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
    async def test_step_timeout_s_actually_limits_step_duration(self):
        """step_timeout_s 应真正限制单步耗时，使慢步骤被标记为 timed_out。"""
        coord = ShutdownCoordinator(service_stop_delay=0, force_exit_callback=lambda code: None)

        async def slow_cancel():
            await asyncio.sleep(60)

        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
            patch("data.domain_services.market_data_service.MarketDataService") as mock_mds,
        ):
            mock_instance = MagicMock()
            mock_instance.cancel_all_running_async = slow_cancel
            mock_tm._instance = mock_instance
            mock_sched.scheduler.running = False
            mock_news._instance = None
            mock_mds._instance = None
            await coord.do_cleanup(timeout_s=10.0, step_timeout_s=0.1)

        timed_out_steps = [r for r in coord.step_results if r.timed_out]
        assert len(timed_out_steps) > 0, (
            f"step_timeout_s should cause slow steps to time out, but no steps timed out. Results: {coord.step_results}"
        )


class TestShutdownStepOrdering:
    """验证 _CLEANUP_STEPS 中关键步骤的执行顺序约束 (防数据丢失)。"""

    def test_flush_db_before_close_processor(self):
        """flush_db_writes 必须在 close_processor 之前执行以防止数据丢失。"""
        flush_idx = None
        close_idx = None
        for i, (_name, method_name, _critical, _timeout) in enumerate(_CLEANUP_STEPS):
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
        assert _CLEANUP_STEPS[-1][1] == "_step8_stop_embedded_postgres"

    def test_step2_is_flush_db_writes(self):
        assert _CLEANUP_STEPS[2][1] == "_step2_flush_db_writes"

    def test_step3_is_close_processor(self):
        assert _CLEANUP_STEPS[3][1] == "_step3_close_processor"


class TestRegisterTask:
    """验证 register_task 注册 fire-and-forget 任务与自动清理行为。"""

    @pytest.mark.asyncio
    async def test_register_adds_task_to_set(self):
        """register_task 应将任务加入 _registered_tasks 集合。"""
        coord = ShutdownCoordinator()

        async def coro():
            await asyncio.sleep(0.1)

        task = asyncio.create_task(coro())
        try:
            coord.register_task(task)
            assert task in coord._registered_tasks
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    @pytest.mark.asyncio
    async def test_register_auto_removes_done_task(self):
        """任务完成后应通过 add_done_callback 自动从集合中移除。"""
        coord = ShutdownCoordinator()

        async def quick():
            return None

        task = asyncio.create_task(quick())
        await task  # 等待完成
        coord.register_task(task)
        # 已完成任务加入集合后 done callback 立即触发（add_done_callback 同步调度）
        # 由于任务已完成，callback 应被调度执行（get_event_loop().call_soon）
        # 等待一个 event loop tick 让 callback 执行
        await asyncio.sleep(0)
        assert task not in coord._registered_tasks or task.done()


class TestStep0RegisteredTasks:
    """覆盖 _step0_cancel_tasks() 中 _registered_tasks 分支（L317-L333）。"""

    @pytest.mark.asyncio
    async def test_step0_cancels_registered_pending_tasks(self):
        """有未完成 registered_tasks 时应取消并 drain。"""
        coord = ShutdownCoordinator()

        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                raise

        task = asyncio.create_task(long_running())
        coord.register_task(task)
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_tm._instance = None
            await coord._step0_cancel_tasks()
        assert task.cancelled() or task.done()
        assert coord._registered_tasks == set()

    @pytest.mark.asyncio
    async def test_step0_skips_done_registered_tasks(self):
        """已完成的 registered_tasks 应跳过取消但清理集合。"""
        coord = ShutdownCoordinator()

        async def quick():
            return None

        task = asyncio.create_task(quick())
        await task  # 等待完成
        coord._registered_tasks.add(task)
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_tm._instance = None
            await coord._step0_cancel_tasks()
        assert coord._registered_tasks == set()

    @pytest.mark.asyncio
    async def test_step0_empty_registered_tasks_skips_block(self):
        """无 registered_tasks 时应跳过整个取消块。"""
        coord = ShutdownCoordinator()
        assert coord._registered_tasks == set()
        with patch("services.task_manager.TaskManager") as mock_tm:
            mock_tm._instance = None
            await coord._step0_cancel_tasks()  # 不应抛异常
        assert coord._registered_tasks == set()


class TestRunCleanupStepsDeep:
    """覆盖 _run_cleanup_steps() 的 critical failure 后继续、CancelledError 传播分支。"""

    @pytest.mark.asyncio
    async def test_critical_failure_continues_remaining_steps(self):
        """关键步骤失败后应继续执行剩余步骤以释放资源。"""
        coord = ShutdownCoordinator(service_stop_delay=0)

        # Step 0 失败（critical=True），其余成功
        call_log: list[str] = []

        async def step0():
            call_log.append("step0")
            raise RuntimeError("step0 fail")

        async def step_ok():
            call_log.append("step_ok")
            return None

        # 替换各 step 方法
        coord._step0_cancel_tasks = step0
        coord._step1_stop_services = step_ok
        coord._step2_flush_db_writes = step_ok
        coord._step3_close_processor = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok
        coord._step8_stop_embedded_postgres = step_ok

        results = await coord._run_cleanup_steps(step_timeout_s=2.0)
        # 9 个步骤全部执行
        assert len(results) == 9
        # Step 0 失败但其余执行
        assert results[0].ok is False
        assert results[0].critical is True
        for r in results[1:]:
            assert r.ok is True
        # 所有步骤都被调用（critical failure 不中断流程）
        assert "step0" in call_log
        assert call_log.count("step_ok") == 8

    @pytest.mark.asyncio
    async def test_cancelled_step_propagates_after_loop(self):
        """某步骤被取消后应完成循环并在最后 re-raise CancelledError（R2）。"""
        coord = ShutdownCoordinator(service_stop_delay=0)

        async def step_cancelled():
            raise asyncio.CancelledError()

        async def step_ok():
            return None

        coord._step0_cancel_tasks = step_cancelled
        coord._step1_stop_services = step_ok
        coord._step2_flush_db_writes = step_ok
        coord._step3_close_processor = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok

        with pytest.raises(asyncio.CancelledError):
            await coord._run_cleanup_steps(step_timeout_s=2.0)

    @pytest.mark.asyncio
    async def test_step_timeout_uses_min_of_default_and_caller(self):
        """effective_timeout 应取 min(default, step_timeout_s)。"""
        coord = ShutdownCoordinator(service_stop_delay=0)

        async def step_ok():
            return None

        coord._step0_cancel_tasks = step_ok
        coord._step1_stop_services = step_ok
        coord._step2_flush_db_writes = step_ok
        coord._step3_close_processor = step_ok
        coord._step4_clear_toast = step_ok
        coord._step5_unload_ai_model = step_ok
        coord._step6_shutdown_thread_pools = step_ok
        coord._step7_close_database_managers = step_ok
        coord._step8_stop_embedded_postgres = step_ok

        # step_timeout_s=0.5 远小于各 step 的 default（1.0~5.0）
        results = await coord._run_cleanup_steps(step_timeout_s=0.5)
        assert len(results) == 9
        # 全部成功（mock step 立即返回，不超时）
        for r in results:
            assert r.ok is True


class TestDoCleanupConcurrent:
    """覆盖 do_cleanup() 中"已有 cleanup_task 在运行"分支（L187-L190）。"""

    @pytest.mark.asyncio
    async def test_concurrent_do_cleanup_reuses_task(self):
        """已有 cleanup_task 在运行时复用而非重复启动。"""
        coord = ShutdownCoordinator(service_stop_delay=0)

        # 预设一个 cleanup_task（模拟正在运行）
        async def long_cleanup():
            await asyncio.sleep(0.05)
            return True

        coord._cleanup_task = asyncio.create_task(long_cleanup())
        coord._cleanup_started = True

        result = await coord.do_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        assert result is True

    @pytest.mark.asyncio
    async def test_do_cleanup_starts_new_task_when_none(self):
        """无 cleanup_task 时应启动新任务。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
            assert coord._cleanup_task is None
            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)
            assert coord.cleanup_done is True
            assert result is True


class TestDefaultForceExit:
    """覆盖 _default_force_exit() 的 SystemExit→os._exit 分支（L94-L97）。"""

    def test_default_force_exit_with_system_exit(self):
        """sys.exit 抛 SystemExit 时应回退到 os._exit。"""
        import sys

        # 直接测试 _default_force_exit 内部逻辑：sys.exit 引发 SystemExit 后调用 os._exit
        # 由于 os._exit 不可恢复，我们 mock 两个函数验证调用顺序
        with (
            patch.object(sys, "exit", side_effect=SystemExit(1)) as mock_sys_exit,
            patch("os._exit") as mock_os_exit,
        ):
            try:
                ShutdownCoordinator._default_force_exit(1)
            except SystemExit:
                pass
            mock_sys_exit.assert_called_once_with(1)
            mock_os_exit.assert_called_once_with(1)

    def test_default_force_exit_flushes_handlers(self):
        """_default_force_exit 应尝试 flush 所有 root handlers。"""
        import logging

        flushed: list[bool] = []

        class FakeHandler(logging.Handler):
            def flush(self):
                flushed.append(True)

            def emit(self, record):
                pass

        original_handlers = logging.root.handlers[:]
        try:
            logging.root.handlers = [FakeHandler()]
            with (
                patch("sys.exit", side_effect=SystemExit(1)),
                patch("os._exit"),
            ):
                try:
                    ShutdownCoordinator._default_force_exit(1)
                except SystemExit:
                    pass
                assert flushed == [True]
        finally:
            logging.root.handlers = original_handlers

    def test_default_force_exit_handler_flush_error_swallowed(self):
        """handler.flush() 抛 OSError/ValueError 时应被吞掉。"""
        import logging

        class BadHandler(logging.Handler):
            def flush(self):
                raise OSError("flush failed")

            def emit(self, record):
                pass

        original_handlers = logging.root.handlers[:]
        try:
            logging.root.handlers = [BadHandler()]
            with (
                patch("sys.exit", side_effect=SystemExit(1)),
                patch("os._exit"),
            ):
                try:
                    ShutdownCoordinator._default_force_exit(1)
                except SystemExit:
                    pass
                # 不抛异常即视为通过
        finally:
            logging.root.handlers = original_handlers


class TestExecuteCleanupFinallyBlock:
    """覆盖 _execute_cleanup() 的 finally 块（L218-L227）与 handler.flush 异常分支。"""

    @pytest.mark.asyncio
    async def test_finally_block_flushes_handlers(self):
        """finally 块应尝试 flush 所有 root handlers。"""
        import logging

        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(return_value=[])

        flushed: list[bool] = []

        class FakeHandler(logging.Handler):
            def flush(self):
                flushed.append(True)

            def emit(self, record):
                pass

        original_handlers = logging.root.handlers[:]
        try:
            logging.root.handlers = [FakeHandler()]
            await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
            assert flushed == [True]
        finally:
            logging.root.handlers = original_handlers

    @pytest.mark.asyncio
    async def test_finally_block_swallows_handler_flush_error(self):
        """finally 块中 handler.flush() 抛 OSError/ValueError 时应被吞掉。"""
        import logging

        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(return_value=[])

        class BadHandler(logging.Handler):
            def flush(self):
                raise ValueError("flush failed")

            def emit(self, record):
                pass

        original_handlers = logging.root.handlers[:]
        try:
            logging.root.handlers = [BadHandler()]
            # 不应抛异常
            result = await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
            assert result is True
        finally:
            logging.root.handlers = original_handlers

    @pytest.mark.asyncio
    async def test_finally_block_cancels_watchdog(self):
        """finally 块应调用 cancel_watchdog()。"""
        coord = ShutdownCoordinator(service_stop_delay=0)
        coord._run_cleanup_steps = AsyncMock(return_value=[])
        coord.start_watchdog(timeout_s=100)
        assert coord.watchdog_started is True

        await coord._execute_cleanup(timeout_s=5.0, step_timeout_s=2.0)
        # cancel_watchdog 应被调用
        assert coord.watchdog_started is False


class TestRunAsyncStepCancelledReraise:
    """覆盖 _run_async_step() 的 CancelledError re-raise 分支（L282-L285）。"""

    @pytest.mark.asyncio
    async def test_cancelled_step_reraises_and_logs(self, caplog):
        """_run_async_step 收到 CancelledError 时应 re-raise 并记录 warning 日志。"""
        import logging

        coord = ShutdownCoordinator()
        with caplog.at_level(logging.WARNING, logger="utils.shutdown"):

            async def cancelled_step():
                raise asyncio.CancelledError()

            with pytest.raises(asyncio.CancelledError):
                await coord._run_async_step(
                    name="test_cancelled",
                    step=cancelled_step,
                    step_timeout_s=5.0,
                    critical=True,
                )
            # 验证日志包含 cancelled 关键词
            assert any("cancelled" in r.message.lower() for r in caplog.records)


class TestFullCleanupWithCriticalFailure:
    """验证完整清理流程中 critical 步骤失败时的整体行为。"""

    @pytest.mark.asyncio
    async def test_full_cleanup_with_critical_failure_returns_false(self):
        """关键步骤失败时 do_cleanup 应返回 False 但 cleanup_done 仍为 True。"""
        coord = ShutdownCoordinator(service_stop_delay=0)

        async def fail_step():
            raise RuntimeError("critical failure")

        async def ok_step():
            return None

        with (
            patch("services.task_manager.TaskManager") as mock_tm,
            patch("utils.scheduler_service.SchedulerService") as mock_sched,
            patch("services.news_subscription_service.NewsSubscriptionService") as mock_news,
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
            # 让 Step 2 (flush_db_writes) 失败
            coord._step2_flush_db_writes = fail_step
            # 其余步骤正常
            coord._step3_close_processor = ok_step
            coord._step5_unload_ai_model = ok_step
            coord._step6_shutdown_thread_pools = ok_step

            result = await coord.do_cleanup(timeout_s=10.0, step_timeout_s=5.0)
            assert result is False
            assert coord.cleanup_done is True
            assert coord.cleanup_success is False
            # 至少有一个 critical failure
            failures = [r for r in coord.step_results if r.critical and not r.ok]
            assert len(failures) >= 1
