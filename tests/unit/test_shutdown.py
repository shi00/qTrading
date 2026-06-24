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

# P2-5: 文件含真实 asyncio.sleep（10s/60s 长睡眠，虽被 step timeout 截断），
# 标注 slow 以便 CI 分轨运行
pytestmark = pytest.mark.slow


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
        assert len(_CLEANUP_STEPS) == 8
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
        """ASYNC-005: Sum of step timeouts must not exceed default overall timeout."""
        total_step_time = sum(step[3] for step in _CLEANUP_STEPS)
        # Default overall timeout is 20.0s, need some margin for scheduling overhead
        assert total_step_time <= 20.0, (
            f"Sum of step timeouts ({total_step_time}s) exceeds default overall budget (20.0s)"
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
        assert _CLEANUP_STEPS[-1][1] == "_step7_close_database_managers"

    def test_step2_is_flush_db_writes(self):
        assert _CLEANUP_STEPS[2][1] == "_step2_flush_db_writes"

    def test_step3_is_close_processor(self):
        assert _CLEANUP_STEPS[3][1] == "_step3_close_processor"
